/**
 * "n/m" progress for for_each loops — the dot strip's trailing count.
 *
 * The count after a loop row's iteration dots was the completed total
 * alone, so a for_each fan-out gave no sense of remaining scope.  The
 * roster is resolved at run time, so the executor persists it as
 * ``planned_iterations`` on the loop's block state; with it present the
 * count renders "n/m", and without it (count/until loops, older runs)
 * the bare total renders exactly as before.
 */

import React from 'react';
import { render } from '@testing-library/react';
import { TaskRunMap } from '../TaskRunMap';
import { dotCountLabel } from '../runMapModel';
import type { TaskCard } from '../../../types/task_card';
import type {
  TaskRun, TaskRunBlockState, IterationSummary,
} from '../../../types/task_run';
import type { LiveTaskState } from '../../../hooks/useTaskRunStream';

const EMPTY_LIVE: LiveTaskState = {
  text: {}, toolCalls: [], events: [], iterations: [],
  variables: {}, blockStatuses: {},
};

function forEachCard(): TaskCard {
  return {
    id: 'card-1', name: 'Card', description: '',
    root: {
      block_type: 'repeat', id: 'loop', name: 'Fan-out',
      repeat_mode: 'for_each',
      repeat_for_each_source: '["a","b","c"]',
      body: [{ block_type: 'task', id: 'inner', name: 'Inner', body: [] }],
    },
    tags: [], is_template: false, source: 'user',
    created_at: 0, updated_at: 0, run_count: 0,
  } as TaskCard;
}

function iter(index: number): IterationSummary {
  return {
    index, status: 'passed', duration_ms: 10, tokens: 0,
    has_artifact: false,
  };
}

function runWith(
  summaries: IterationSummary[], planned?: number | null,
): TaskRun {
  return {
    id: 'run-1', card_id: 'card-1', status: 'running',
    block_states: {
      loop: {
        block_id: 'loop', block_type: 'repeat', status: 'running',
        iteration_summaries: summaries,
        planned_iterations: planned,
      } as TaskRunBlockState,
    },
    cancel_requested: false, pause_requested: false,
    total_tokens: 0, total_tool_calls: 0, created_at: 0, updated_at: 0,
  } as TaskRun;
}

function countText(container: HTMLElement): string | null {
  const counts = container.querySelectorAll('.tc-map__dot-count');
  // No overflow in these fixtures, so exactly one count span exists —
  // guard it so a second claimant would fail loudly rather than let
  // the assertion below pick the wrong one.
  expect(counts.length).toBe(1);
  return counts[0].textContent;
}

describe('dotCountLabel', () => {
  it('renders n/m when the roster size is known', () => {
    expect(dotCountLabel(3, 12)).toBe('3/12');
  });

  it('renders 0/m before any iteration lands', () => {
    expect(dotCountLabel(0, 12)).toBe('0/12');
  });

  it('falls back to the bare total without a roster size', () => {
    expect(dotCountLabel(3)).toBe('3');
    expect(dotCountLabel(3, null)).toBe('3');
    expect(dotCountLabel(3, undefined)).toBe('3');
  });

  it('treats a non-positive planned value as unknown', () => {
    // A zero-iteration for_each returns before persisting anything, so
    // 0 here is a defect elsewhere — render it as absent, not "3/0".
    expect(dotCountLabel(3, 0)).toBe('3');
  });
});

describe('run map dot-count rendering', () => {
  it('shows n/m on a for_each loop with a persisted roster size', () => {
    const { container } = render(
      <TaskRunMap
        projectId="p1" card={forEachCard()}
        run={runWith([iter(0), iter(1), iter(2)], 12)}
        live={EMPTY_LIVE} focusedId={null} focusedIndex={null}
        onFocus={() => {}}
      />,
    );
    expect(countText(container)).toBe('3/12');
  });

  it('keeps the bare total when no roster size was recorded', () => {
    // Older runs and non-for_each loops carry no planned_iterations;
    // their rendering must be byte-identical to before the change.
    const { container } = render(
      <TaskRunMap
        projectId="p1" card={forEachCard()}
        run={runWith([iter(0), iter(1), iter(2)])}
        live={EMPTY_LIVE} focusedId={null} focusedIndex={null}
        onFocus={() => {}}
      />,
    );
    expect(countText(container)).toBe('3');
  });
});
