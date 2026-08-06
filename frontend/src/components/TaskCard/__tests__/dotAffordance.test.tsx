/**
 * Tests for iteration-dot affordance (B10) and focused-block provenance
 * rendering (B9).
 *
 * B10: the dot open-rule was ``status === 'failed' && hasArtifact``, but
 * ``has_artifact`` is true for EVERY failure *and* for passes under the
 * retention cap (see block_executor._record_iteration).  So a loop whose
 * iterations all passed rendered dots backed by real, fetchable output —
 * every one of them inert.  Worse, the only difference from the
 * genuinely-empty kind was the ``disabled`` attribute, invisible on an
 * 8px circle, so clicking across a row did nothing and read as broken
 * rather than as absent data.
 *
 * B9: the render half of blockOrigin — a replayed stage must SAY it came
 * from an earlier attempt rather than being labelled "skipped" beside
 * its own output.
 */

import React from 'react';
import * as fs from 'fs';
import * as path from 'path';
import { render, screen } from '@testing-library/react';
import { TaskRunMap } from '../TaskRunMap';
import { BlockDetailPanel } from '../BlockDetailPanel';
import type { Block, TaskCard, Artifact } from '../../../types/task_card';
import type {
  TaskRun, TaskRunBlockState, IterationSummary,
} from '../../../types/task_run';
import type { LiveTaskState } from '../../../hooks/useTaskRunStream';

// MarkdownRenderer drags in the whole markdown/katex/mermaid stack and
// none of it is under test here.
jest.mock('../../MarkdownRenderer', () => ({
  MarkdownRenderer: ({ markdown }: { markdown: string }) => (
    <div data-testid="md">{markdown}</div>
  ),
}));

// ─────────────────────────── fixtures ───────────────────────────

const EMPTY_LIVE: LiveTaskState = {
  text: {}, toolCalls: [], events: [], iterations: [],
  variables: {}, blockStatuses: {},
};

const task = (id: string, name: string): Block => ({
  block_type: 'task', id, name, body: [],
});

/** A repeat loop wrapping one task, so the map renders a dot strip. */
function loopCard(): TaskCard {
  return {
    id: 'card-1', name: 'Card', description: '',
    root: {
      block_type: 'repeat', id: 'loop', name: 'Fuzz loop',
      repeat_mode: 'count', repeat_count: 3,
      body: [task('inner', 'Inner task')],
    },
    tags: [], is_template: false, source: 'user',
    created_at: 0, updated_at: 0, run_count: 0,
  } as TaskCard;
}

function iterSummary(
  index: number, status: IterationSummary['status'], hasArtifact: boolean,
): IterationSummary {
  return {
    index, status, duration_ms: 100, tokens: 0, has_artifact: hasArtifact,
  };
}

function runWithIterations(summaries: IterationSummary[]): TaskRun {
  return {
    id: 'run-1', card_id: 'card-1', status: 'done',
    block_states: {
      loop: {
        block_id: 'loop', block_type: 'repeat', status: 'done',
        iteration_summaries: summaries,
      } as TaskRunBlockState,
      inner: {
        block_id: 'inner', block_type: 'task', status: 'done',
        iteration_summaries: [],
      } as TaskRunBlockState,
    },
    cancel_requested: false, pause_requested: false,
    total_tokens: 0, total_tool_calls: 0, created_at: 0, updated_at: 0,
  } as TaskRun;
}

function renderMap(summaries: IterationSummary[]) {
  return render(
    <TaskRunMap
      projectId="p1" card={loopCard()} run={runWithIterations(summaries)}
      live={EMPTY_LIVE} focusedId={null} focusedIndex={null}
      onFocus={() => {}}
    />,
  );
}

/** The dot buttons, in render order. */
function dots(container: HTMLElement): HTMLButtonElement[] {
  return Array.from(
    container.querySelectorAll('button.tc-map__dot'),
  ) as HTMLButtonElement[];
}

// ── B10: clickability follows retention, not status ──────────────

describe('iteration dot clickability', () => {
  it('enables a PASSED dot whose artifact was retained', () => {
    // The headline bug: passes under the retention cap DO have fetchable
    // artifacts, and every one of them used to be dead to the touch.
    const { container } = renderMap([iterSummary(0, 'passed', true)]);
    expect(dots(container)[0].disabled).toBe(false);
  });

  it('still enables a FAILED dot with a retained artifact', () => {
    const { container } = renderMap([iterSummary(0, 'failed', true)]);
    expect(dots(container)[0].disabled).toBe(false);
  });

  it('disables a dot whose artifact was NOT retained', () => {
    // Beyond the pass-retention cap only the summary is kept, so the
    // fetch would 404 — offering a click that always errors is worse
    // than withholding it.
    const { container } = renderMap([iterSummary(0, 'passed', false)]);
    expect(dots(container)[0].disabled).toBe(true);
  });

  it('mixes enabled and disabled by retention across a loop', () => {
    const { container } = renderMap([
      iterSummary(0, 'passed', true),
      iterSummary(1, 'passed', false),
      iterSummary(2, 'failed', true),
    ]);
    expect(dots(container).map(d => d.disabled)).toEqual([false, true, false]);
  });
});

describe('iteration dot visual affordance', () => {
  it('marks an openable dot with a class, not just the disabled attribute', () => {
    // ``disabled`` alone is invisible at 8px, which is why clicking
    // around "did nothing" and read as a broken UI.
    const { container } = renderMap([iterSummary(0, 'passed', true)]);
    expect(dots(container)[0].className).toMatch(/tc-map__dot--openable/);
  });

  it('does not mark an inert dot as openable', () => {
    const { container } = renderMap([iterSummary(0, 'passed', false)]);
    expect(dots(container)[0].className).not.toMatch(/tc-map__dot--openable/);
  });

  it('says in the tooltip whether output can be viewed', () => {
    const { container } = renderMap([
      iterSummary(0, 'passed', true),
      iterSummary(1, 'passed', false),
    ]);
    const [open, inert] = dots(container);
    expect(open.getAttribute('title')).toMatch(/click to view output/i);
    expect(inert.getAttribute('title')).toMatch(/not retained/i);
  });
});

describe('dot affordance styling', () => {
  const CSS = fs.readFileSync(
    path.resolve(__dirname, '../task-card-inline-tile.css'), 'utf-8',
  );

  it('gives openable dots a pointer cursor', () => {
    expect(CSS).toMatch(/\.tc-map__dot--openable\s*\{[^}]*cursor:\s*pointer/);
  });

  it('dims inert dots so the difference is visible', () => {
    expect(CSS).toMatch(/\.tc-map__dot:disabled\s*\{[^}]*opacity/);
  });

  it('no longer ties the hover ring to the failed status alone', () => {
    // The old rule was ``.tc-map__dot--failed:hover``, so a passed dot
    // could not indicate interactivity even once it became clickable.
    expect(CSS).not.toMatch(/\.tc-map__dot--failed:hover/);
  });

  it('rings on keyboard focus too, not only hover', () => {
    // A hover-only affordance is invisible to keyboard users.
    expect(CSS).toMatch(/\.tc-map__dot--openable:focus-visible/);
  });
});

// ── B9: provenance in the focused-block panel ────────────────────

const anArtifact: Artifact = {
  summary: 'result from the earlier attempt', decisions: [], outputs: [],
  tokens: 0, tool_calls: 0, duration_ms: 0, created_at: 0,
} as Artifact;

function renderPanel(
  status: string,
  blockState: Partial<TaskRunBlockState> | undefined,
  runOver: Partial<TaskRun> = {},
) {
  const run = {
    id: 'run-1', card_id: 'c1', status: 'done', block_states: {},
    cancel_requested: false, pause_requested: false,
    total_tokens: 0, total_tool_calls: 0, created_at: 0, updated_at: 0,
    ...runOver,
  } as TaskRun;
  return render(
    <BlockDetailPanel
      block={task('b1', 'Stage one')}
      status={status}
      run={run}
      blockState={blockState as TaskRunBlockState | undefined}
      iterationIndex={null}
      iterationArtifact={null}
      iterationLoading={false}
      iterationError={null}
    />,
  );
}

describe('focused-block provenance', () => {
  it('says a replayed stage came from an earlier attempt', () => {
    renderPanel(
      'skipped',
      { status: 'skipped', artifact: anArtifact, block_id: 'b1',
        block_type: 'task', iteration_summaries: [] },
      { resume_kind: 'retry_from', attempt: 2 },
    );
    expect(screen.getByText(/replayed from an earlier attempt/i))
      .toBeInTheDocument();
  });

  it('does not label a replayed stage "skipped"', () => {
    // "skipped" beside real output is self-contradictory and is what
    // made a resumed run's panel unreadable.
    const { container } = renderPanel(
      'skipped',
      { status: 'skipped', artifact: anArtifact, block_id: 'b1',
        block_type: 'task', iteration_summaries: [] },
      { resume_kind: 'retry_from', attempt: 2 },
    );
    const badge = container.querySelector('.tc-detail__status');
    expect(badge?.textContent).toBe('replayed');
  });

  it('shows the attempt ordinal for a stage that ran here', () => {
    renderPanel(
      'done',
      { status: 'done', artifact: anArtifact, block_id: 'b1',
        block_type: 'task', iteration_summaries: [] },
      { attempt: 3 },
    );
    expect(screen.getByText(/attempt 3/i)).toBeInTheDocument();
  });

  it('does not claim a replay for a genuine never-ran stage', () => {
    renderPanel(
      'skipped',
      { status: 'skipped', artifact: null, block_id: 'b1',
        block_type: 'task', iteration_summaries: [] },
      { resume_kind: 'retry_from', attempt: 2 },
    );
    expect(screen.queryByText(/replayed from an earlier attempt/i)).toBeNull();
  });

  it('shows when the stage finished', () => {
    const { container } = renderPanel(
      'done',
      { status: 'done', artifact: anArtifact, completed_at: 1_700_000_000,
        block_id: 'b1', block_type: 'task', iteration_summaries: [] },
    );
    const when = container.querySelector('.tc-detail__when');
    expect(when).not.toBeNull();
    expect(when!.textContent).toMatch(/\d/);
  });

  it('restates the origin on the output section itself', () => {
    // With a long Configuration block open the header scrolls out of
    // view, and the output would again look like this attempt's work.
    renderPanel(
      'skipped',
      { status: 'skipped', artifact: anArtifact, block_id: 'b1',
        block_type: 'task', iteration_summaries: [] },
      { resume_kind: 'continue_from', attempt: 2 },
    );
    expect(screen.getByText(/not re-run here/i)).toBeInTheDocument();
  });
});
