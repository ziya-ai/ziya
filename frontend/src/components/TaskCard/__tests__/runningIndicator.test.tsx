/**
 * Tests for the "stage is running" indicator in TaskRunMap.
 *
 * The original signal was one 9%-alpha row tint plus an icon animated
 * DOWN to opacity 0.35, and it failed under four independent
 * conditions, each of which these tests pin:
 *
 *   1. light theme      — no `--running` override existed in the
 *                         prefers-color-scheme block, so a 9% blue sat
 *                         on an almost-white panel
 *   2. :hover           — `.tc-map__row:hover` (0,2,0) is declared
 *                         AFTER `.tc-map__row--running` (0,1,0), so
 *                         pointing at a running row erased its tint
 *   3. reduced motion   — no prefers-reduced-motion block at all, so
 *                         users with motion suppressed lost the blink
 *                         and got nothing in its place
 *   4. monochrome /
 *      colour blindness — `running` was the only state with no text
 *                         label, while `skipped` had one
 *
 * Split into two halves deliberately.  The render tests assert on
 * behaviour a user can perceive (the word "running" appears on the
 * running row and nowhere else).  The CSS tests assert on the
 * stylesheet, following the convention set by
 * components/__tests__/copyPasteWhitespace.test.ts — the visual cues
 * are unreachable from jsdom, which does not apply stylesheets or
 * compute cascade, so a source assertion is the only way to hold them.
 */

import React from 'react';
import * as fs from 'fs';
import * as path from 'path';
import { render, screen } from '@testing-library/react';
import { TaskRunMap } from '../TaskRunMap';
import type { Block, TaskCard } from '../../../types/task_card';
import type { TaskRun, TaskRunBlockState } from '../../../types/task_run';
import type { LiveTaskState } from '../../../hooks/useTaskRunStream';

// ─────────────────────────────── fixtures ───────────────────────────────

const task = (id: string, name: string): Block => ({
  block_type: 'task', id, name, body: [],
});

const container = (
  type: Block['block_type'], id: string, name: string, body: Block[],
): Block => ({ block_type: type, id, name, body });

function card(root: Block): TaskCard {
  return {
    id: 'card-1', name: 'Card', description: '', root,
    tags: [], is_template: false, source: 'user',
    created_at: 0, updated_at: 0, run_count: 0,
  };
}

function blockState(
  id: string, status: string, iters: TaskRunBlockState['iteration_summaries'] = [],
): TaskRunBlockState {
  return {
    block_id: id, block_type: 'task', status: status as any,
    iteration_summaries: iters,
  };
}

function run(
  status: TaskRun['status'],
  block_states: TaskRun['block_states'] = {},
): TaskRun {
  return {
    id: 'run-1', card_id: 'card-1', status,
    cancel_requested: false, pause_requested: false,
    block_states, total_tokens: 0, total_tool_calls: 0,
    created_at: 0, updated_at: 0,
  };
}

const EMPTY_LIVE: LiveTaskState = {
  text: {}, toolCalls: [], events: [], iterations: [],
  variables: {}, blockStatuses: {},
};

function renderMap(
  root: Block, r: TaskRun, live: LiveTaskState = EMPTY_LIVE,
) {
  return render(
    <TaskRunMap
      projectId="p1" card={card(root)} run={r} live={live}
      focusedId={null} focusedIndex={null} onFocus={() => {}}
    />,
  );
}

// A two-row tree; the map renders nothing at rows.length <= 1.
const TWO_TASKS = container('group', 'g', 'G', [
  task('t1', 'First step'),
  task('t2', 'Second step'),
]);

// ───────────────────────── perceivable indicator ─────────────────────────

describe('the running stage is named in text, not only coloured', () => {
  it('labels the running block with the word "running"', () => {
    renderMap(TWO_TASKS, run('running', {
      t1: blockState('t1', 'done'),
      t2: blockState('t2', 'running'),
    }));
    expect(screen.getByText('running')).toBeInTheDocument();
  });

  it('does not label any row when nothing is running', () => {
    renderMap(TWO_TASKS, run('done', {
      t1: blockState('t1', 'done'),
      t2: blockState('t2', 'done'),
    }));
    expect(screen.queryByText('running')).toBeNull();
  });

  it('labels exactly one row when one block is running', () => {
    renderMap(TWO_TASKS, run('running', {
      t1: blockState('t1', 'done'),
      t2: blockState('t2', 'running'),
    }));
    expect(screen.queryAllByText('running')).toHaveLength(1);
  });

  it('labels every concurrently-running row (parallel fan-out)', () => {
    const root = container('parallel', 'p', 'P', [
      task('a', 'Branch A'), task('b', 'Branch B'),
    ]);
    renderMap(root, run('running', {
      p: blockState('p', 'running'),
      a: blockState('a', 'running'),
      b: blockState('b', 'running'),
    }));
    // Parent + both branches — none is a loop, so all three get chips.
    expect(screen.queryAllByText('running')).toHaveLength(3);
  });

  it('derives the label from live block_status, not just the snapshot', () => {
    // The live WS status is the fresher source; a row whose persisted
    // state still reads 'queued' must still be labelled once the
    // block_status event has arrived.
    renderMap(
      TWO_TASKS,
      run('running', { t2: blockState('t2', 'queued') }),
      { ...EMPTY_LIVE, blockStatuses: { t2: 'running' } },
    );
    expect(screen.getByText('running')).toBeInTheDocument();
  });

  it('drops the label once the run goes terminal with a stale running block', () => {
    // resolveBlockStatus's terminal backstop degrades a stale 'running'
    // to the run's own status; the chip must follow it rather than
    // leaving a finished run showing an active stage.
    renderMap(TWO_TASKS, run('done', {
      t1: blockState('t1', 'done'),
      t2: blockState('t2', 'running'),
    }));
    expect(screen.queryByText('running')).toBeNull();
  });

  it('keeps the existing "skipped" label working', () => {
    renderMap(TWO_TASKS, run('failed', {
      t1: blockState('t1', 'failed'),
      t2: blockState('t2', 'skipped'),
    }));
    expect(screen.getByText('skipped')).toBeInTheDocument();
  });
});

describe('the chip yields the row edge to the iteration dot strip', () => {
  // Both .tc-map__tag and .tc-map__dots take margin-left: auto, so
  // rendering both puts two elements in one slot.  The strip already
  // shows a live iteration, making the chip redundant there.
  it('omits the chip on a running loop that has iterations', () => {
    const root = container('group', 'g', 'G', [
      container('repeat', 'r', 'Loop', [task('t', 'Body')]),
      task('after', 'After'),
    ]);
    const { container: el } = renderMap(root, run('running', {
      r: blockState('r', 'running', [
        { index: 0, status: 'passed', duration_ms: 10, tokens: 5, has_artifact: false },
      ]),
    }));
    expect(screen.queryByText('running')).toBeNull();
    // The strip is what carries the state on this row instead.
    expect(el.querySelector('.tc-map__dots')).not.toBeNull();
    expect(el.querySelector('.tc-map__dot--running')).not.toBeNull();
  });

  it('omits the chip on a running loop with no completed iterations yet', () => {
    // buildDots still emits a running dot at total === 0, so the strip
    // exists and the chip must still stand down.
    const root = container('group', 'g', 'G', [
      container('repeat', 'r', 'Loop', [task('t', 'Body')]),
      task('after', 'After'),
    ]);
    const { container: el } = renderMap(root, run('running', {
      r: blockState('r', 'running', []),
    }));
    expect(screen.queryByText('running')).toBeNull();
    expect(el.querySelector('.tc-map__dot--running')).not.toBeNull();
  });

  it('does not chip a queued loop, which has no strip either', () => {
    // Not running, so no chip — guards against the inverse bug of
    // chipping every loop row unconditionally.
    const root = container('group', 'g', 'G', [
      container('repeat', 'r', 'Loop', [task('t', 'Body')]),
      task('after', 'After'),
    ]);
    const { container: el } = renderMap(root, run('queued', {}));
    expect(screen.queryByText('running')).toBeNull();
    expect(el.querySelector('.tc-map__dots')).toBeNull();
  });
});

// ───────────────────────────── stylesheet ─────────────────────────────

const CSS = fs.readFileSync(
  path.resolve(__dirname, '../task-card-inline-tile.css'), 'utf-8',
);

/** Body of the first flat rule block whose selector matches `re`. */
function ruleBody(re: RegExp): string {
  const m = CSS.match(new RegExp(re.source + '\\s*\\{([^}]*)\\}'));
  return m ? m[1] : '';
}

/**
 * Body of the first NESTED at-rule matching `re` (@keyframes, @media),
 * brace-matched.  `ruleBody`'s `[^}]*` stops at the first inner `}`,
 * which for a keyframe is the end of its first stop — so a fade hidden
 * in a later stop would go unseen and the assertion would pass
 * vacuously.
 */
function atRuleBody(re: RegExp): string {
  const open = CSS.search(new RegExp(re.source + '\\s*\\{'));
  if (open < 0) return '';
  const start = CSS.indexOf('{', open);
  let depth = 0;
  for (let i = start; i < CSS.length; i++) {
    if (CSS[i] === '{') depth++;
    else if (CSS[i] === '}' && --depth === 0) return CSS.slice(start + 1, i);
  }
  return '';
}

describe('running-row cues do not depend on a single channel', () => {
  it('draws a structural accent bar on the running row', () => {
    // The cue that survives every failure mode: not a tint, not colour
    // alone, not animated.  inset rather than border-left so the 3px
    // does not shift content against the inline depth padding.
    const body = ruleBody(/\.tc-map__row--running/);
    expect(body).toMatch(/box-shadow\s*:\s*inset\s+3px\s+0\s+0/);
  });

  it('tints the running row well above the previous 0.09 alpha', () => {
    const body = ruleBody(/\.tc-map__row--running/);
    const alpha = body.match(/background\s*:\s*rgba\([^)]*,\s*([\d.]+)\s*\)/);
    expect(alpha).not.toBeNull();
    expect(parseFloat(alpha![1])).toBeGreaterThanOrEqual(0.15);
  });

  it('weights the running row label', () => {
    expect(CSS).toMatch(
      /\.tc-map__row--running\s+\.tc-map__label\s*\{[^}]*font-weight/,
    );
  });

  it('styles the running chip distinctly from the muted generic tag', () => {
    const body = ruleBody(/\.tc-map__tag--running/);
    // The generic .tc-map__tag is italic grey (used for "skipped"); the
    // running chip must not inherit that de-emphasis.
    expect(body).toMatch(/font-style\s*:\s*normal/);
    expect(body).toMatch(/font-weight\s*:\s*(700|bold)/);
  });
});

describe('animated cues never make the active stage the dimmest thing', () => {
  it('removes the fade-to-0.35 keyframe entirely', () => {
    // Regression guard: the old tc-map-blink is what made the running
    // glyph less visible than its neighbours half the time.
    expect(CSS).not.toContain('tc-map-blink');
  });

  it('pulses the running icon without reducing its opacity', () => {
    // atRuleBody, not ruleBody: a keyframe's stops are nested blocks,
    // so `[^}]*` would only see the first stop and a fade in a later
    // one would slip through.
    const body = atRuleBody(/@keyframes\s+tc-map-throb/);
    expect(body).not.toBe('');
    expect(body).not.toMatch(/opacity/);
    expect(body).toMatch(/transform\s*:\s*scale/);
  });

  it('pulses the running iteration dot without reducing its opacity', () => {
    const body = atRuleBody(/@keyframes\s+tc-map-ring/);
    expect(body).not.toBe('');
    expect(body).not.toMatch(/opacity/);
    expect(body).toMatch(/box-shadow/);
  });

  it('makes the running dot larger than a finished one', () => {
    const running = ruleBody(/\.tc-map__dot--running/);
    const base = ruleBody(/\.tc-map__dot(?![-\w])/);
    const rw = running.match(/width\s*:\s*(\d+)px/);
    const bw = base.match(/width\s*:\s*(\d+)px/);
    expect(rw).not.toBeNull();
    expect(bw).not.toBeNull();
    expect(parseInt(rw![1], 10)).toBeGreaterThan(parseInt(bw![1], 10));
  });
});

describe('the cascade cannot erase the running state', () => {
  it('declares a running :hover override AFTER the generic row :hover', () => {
    // `.tc-map__row:hover` is 0,2,0 and `.tc-map__row--running` is
    // 0,1,0, so without a later same-specificity rule, hovering a
    // running row replaced its tint with the neutral grey hover —
    // i.e. pointing at the row destroyed the indicator.
    const generic = CSS.indexOf('.tc-map__row:hover');
    const running = CSS.indexOf('.tc-map__row--running:hover');
    expect(generic).toBeGreaterThan(-1);
    expect(running).toBeGreaterThan(-1);
    expect(running).toBeGreaterThan(generic);
  });

  it('overrides the running row for the light theme', () => {
    // The map panel is rgba(0,0,0,0.03) in light mode, so the dark
    // theme's tint is nearly invisible there.
    const light = CSS.slice(CSS.lastIndexOf('@media (prefers-color-scheme: light)'));
    expect(light).toMatch(/\.tc-map__row--running\s*\{/);
    expect(light).toMatch(/\.tc-map__tag--running\s*\{/);
  });
});

describe('reduced motion leaves the static cues in charge', () => {
  it('declares a prefers-reduced-motion block', () => {
    expect(CSS).toMatch(/@media\s*\(prefers-reduced-motion:\s*reduce\)/);
  });

  it('suppresses every running animation in that block', () => {
    // Brace-matched so the assertion is scoped to the media query and
    // cannot be satisfied by an `animation: none` sitting outside it.
    const block = atRuleBody(/@media\s*\(prefers-reduced-motion:\s*reduce\)/);
    expect(block).not.toBe('');
    expect(block).toContain('.tc-map__icon--running');
    expect(block).toContain('.tc-map__dot--running');
    expect(block).toContain('.tc-tile--running');
    expect(block).toMatch(/animation\s*:\s*none/);
  });
});
