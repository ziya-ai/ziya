/**
 * Replayed iteration dots — a resumed loop showing its preserved prefix.
 *
 * The defect: the dot strip is built from ``iteration_summaries``, and a
 * mid-loop resume recorded only the iterations it EXECUTED.  So a resume
 * at 3 of 5 drew two dots and a count of 2 — indistinguishable from a
 * fresh two-iteration run, and reading as though the three banked
 * iterations had been discarded.
 *
 * The backend now seeds the prefix with ``replayed: true``.  These pin
 * both directions of that flag: the dots must SHOW (dimmed, keeping
 * pass/fail colour), and every progress aggregate must EXCLUDE them.
 */

import React from 'react';
import { render } from '@testing-library/react';
import { TaskRunMap } from '../TaskRunMap';
import { buildDots } from '../runMapModel';
import { progressCounts } from '../partialOutcome';
import type { Block, TaskCard } from '../../../types/task_card';
import type {
  TaskRun, TaskRunBlockState, IterationSummary,
} from '../../../types/task_run';
import type { LiveTaskState } from '../../../hooks/useTaskRunStream';

const EMPTY_LIVE: LiveTaskState = {
  text: {}, toolCalls: [], events: [], iterations: [],
  variables: {}, blockStatuses: {},
};

function loopCard(): TaskCard {
  return {
    id: 'card-1', name: 'Card', description: '',
    root: {
      block_type: 'repeat', id: 'loop', name: 'Fuzz loop',
      repeat_mode: 'count', repeat_count: 5,
      body: [{ block_type: 'task', id: 'inner', name: 'Inner', body: [] }],
    },
    tags: [], is_template: false, source: 'user',
    created_at: 0, updated_at: 0, run_count: 0,
  } as TaskCard;
}

function iter(
  index: number,
  status: IterationSummary['status'] = 'passed',
  replayed = false,
  hasArtifact = true,
): IterationSummary {
  return {
    index, status, duration_ms: 10, tokens: 0,
    has_artifact: hasArtifact, replayed,
  };
}

function runWith(summaries: IterationSummary[]): TaskRun {
  return {
    id: 'run-2', card_id: 'card-1', status: 'done',
    block_states: {
      loop: {
        block_id: 'loop', block_type: 'repeat', status: 'done',
        iteration_summaries: summaries,
      } as TaskRunBlockState,
    },
    cancel_requested: false, pause_requested: false,
    total_tokens: 0, total_tool_calls: 0, created_at: 0, updated_at: 0,
  } as TaskRun;
}

/** A run resumed at index 3: three replayed, two executed. */
const RESUMED_AT_3: IterationSummary[] = [
  iter(0, 'passed', true), iter(1, 'failed', true), iter(2, 'passed', true),
  iter(3, 'passed'), iter(4, 'passed'),
];

function dots(container: HTMLElement): HTMLButtonElement[] {
  return Array.from(
    container.querySelectorAll('button.tc-map__dot'),
  ) as HTMLButtonElement[];
}

describe('buildDots replayed flag', () => {
  it('carries replayed through to the dot model', () => {
    const m = buildDots(RESUMED_AT_3, false);
    expect(m.dots.map(d => d.replayed))
      .toEqual([true, true, true, false, false]);
  });

  it('counts the whole loop, not just what executed', () => {
    // The headline symptom: total was 2 for this run.
    expect(buildDots(RESUMED_AT_3, false).total).toBe(5);
  });

  it('defaults replayed to false when the field is absent', () => {
    // Runs written before the field exists must read as executed.
    const legacy = [{
      index: 0, status: 'passed', duration_ms: 1, tokens: 0,
      has_artifact: true,
    }] as IterationSummary[];
    expect(buildDots(legacy, false).dots[0].replayed).toBe(false);
  });
});

describe('replayed dot rendering', () => {
  it('marks the preserved prefix and leaves the executed tail plain', () => {
    const { container } = render(
      <TaskRunMap
        projectId="p1" card={loopCard()} run={runWith(RESUMED_AT_3)}
        live={EMPTY_LIVE} focusedId={null} focusedIndex={null}
        onFocus={() => {}}
      />,
    );
    const cls = dots(container).map(d => d.className);
    expect(cls.slice(0, 3).every(c => c.includes('tc-map__dot--replayed')))
      .toBe(true);
    expect(cls.slice(3).some(c => c.includes('tc-map__dot--replayed')))
      .toBe(false);
  });

  it('renders one dot per loop iteration, not per executed iteration', () => {
    const { container } = render(
      <TaskRunMap
        projectId="p1" card={loopCard()} run={runWith(RESUMED_AT_3)}
        live={EMPTY_LIVE} focusedId={null} focusedIndex={null}
        onFocus={() => {}}
      />,
    );
    expect(dots(container).length).toBe(5);
  });

  it('keeps the failure colour on a preserved failure', () => {
    // Greying to neutral would erase the fact that the preserved
    // iteration failed — the thing the user is resuming past.
    const { container } = render(
      <TaskRunMap
        projectId="p1" card={loopCard()} run={runWith(RESUMED_AT_3)}
        live={EMPTY_LIVE} focusedId={null} focusedIndex={null}
        onFocus={() => {}}
      />,
    );
    expect(dots(container)[1].className).toContain('tc-map__dot--failed');
  });

  it('says a replayed dot came from an earlier attempt', () => {
    const { container } = render(
      <TaskRunMap
        projectId="p1" card={loopCard()} run={runWith(RESUMED_AT_3)}
        live={EMPTY_LIVE} focusedId={null} focusedIndex={null}
        onFocus={() => {}}
      />,
    );
    expect(dots(container)[0].getAttribute('title'))
      .toMatch(/replayed from an earlier attempt/i);
  });

  it('keeps a replayed dot with a retained artifact clickable', () => {
    // The artifact is copied onto the resumed run at launch, so the
    // preserved output is genuinely reachable from this run.
    const { container } = render(
      <TaskRunMap
        projectId="p1" card={loopCard()} run={runWith(RESUMED_AT_3)}
        live={EMPTY_LIVE} focusedId={null} focusedIndex={null}
        onFocus={() => {}}
      />,
    );
    expect(dots(container)[0].disabled).toBe(false);
  });
});

describe('replayed iterations are excluded from progress', () => {
  it('counts only executed iterations', () => {
    const p = progressCounts(runWith(RESUMED_AT_3));
    expect(p.passedIterations).toBe(2);
    expect(p.failedIterations).toBe(0);
  });

  it('leaves an ordinary run\'s counts unchanged', () => {
    const p = progressCounts(runWith([iter(0), iter(1, 'failed')]));
    expect(p.passedIterations).toBe(1);
    expect(p.failedIterations).toBe(1);
  });
});
