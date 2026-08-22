/**
 * The shared run-status vocabulary, and the counting it drives.
 *
 * These tests exist because the previous design carried ONE bit per
 * conversation ("a task is running") and then a second bit bolted beside
 * it ("a task is held").  That does not generalize: there are eight run
 * statuses, several of them terminal-but-interesting, and a conversation
 * can hold more than one run.  The interesting assertions here are the
 * ones that pin decisions a re-implementation would plausibly get wrong:
 * the two colours for 'running', violet-not-red for 'held', counting
 * lineages rather than bindings, and attention-first ordering.
 */

import type { TaskBinding } from '../../../types/task_binding';
import {
  RUN_STATUS_FILL, RUN_STATUS_FG, RUN_STATUS_ANIMATES,
  RUN_STATUS_LABEL, RUN_STATUS_HINT, RUN_STATUS_ORDER,
  statusClusters, showCount, clustersFromCounts,
} from '../runStatusVocabulary';

const ALL_STATUSES = [
  'queued', 'running', 'paused', 'done',
  'partial', 'failed', 'cancelled', 'held',
] as const;

function b(over: Partial<TaskBinding> & { id: string }): TaskBinding {
  return {
    chat_id: 'c1',
    card_id: 'card1',
    created_at: 1,
    ...over,
  } as TaskBinding;
}

describe('every status is covered, or a row renders a hole', () => {
  it.each(ALL_STATUSES)('%s has a fill colour', (s) => {
    expect(RUN_STATUS_FILL[s]).toMatch(/^#[0-9a-f]{6}$/i);
  });

  it.each(ALL_STATUSES)('%s has a foreground colour', (s) => {
    expect(RUN_STATUS_FG[s]).toMatch(/^#[0-9a-f]{6}$/i);
  });

  it.each(ALL_STATUSES)('%s declares whether it animates', (s) => {
    expect(typeof RUN_STATUS_ANIMATES[s]).toBe('boolean');
  });

  it.each(ALL_STATUSES)('%s has a label and a hint', (s) => {
    expect(RUN_STATUS_LABEL[s]).toBeTruthy();
    expect(RUN_STATUS_HINT[s]).toBeTruthy();
  });

  it('orders every status exactly once', () => {
    expect([...RUN_STATUS_ORDER].sort()).toEqual([...ALL_STATUSES].sort());
    expect(new Set(RUN_STATUS_ORDER).size).toBe(RUN_STATUS_ORDER.length);
  });
});

describe('the two decisions a copied colour map loses', () => {
  it('gives running a LIGHTER foreground than its fill', () => {
    // #1f6feb is tuned as a filled Tag background; as a foreground glyph
    // on a dark surface it drops to ~2.5:1.  A copy that takes the fill
    // value for an icon produces a barely-legible gear.
    expect(RUN_STATUS_FILL.running).toBe('#1f6feb');
    expect(RUN_STATUS_FG.running).toBe('#58a6ff');
    expect(RUN_STATUS_FG.running).not.toBe(RUN_STATUS_FILL.running);
  });

  it('leaves every other status foreground equal to its fill', () => {
    // Only running needed the correction; diverging elsewhere would mean
    // two palettes to keep in sync for no reason.
    for (const s of ALL_STATUSES) {
      if (s === 'running') continue;
      expect(RUN_STATUS_FG[s]).toBe(RUN_STATUS_FILL[s]);
    }
  });

  it('colours held like paused, not like failed', () => {
    // Both mean "stopped, not broken".  Reaching for red here
    // re-introduces the misreading held was added to prevent.
    expect(RUN_STATUS_FG.held).toBe(RUN_STATUS_FG.paused);
    expect(RUN_STATUS_FG.held).not.toBe(RUN_STATUS_FG.failed);
  });

  it('does not colour held green either', () => {
    expect(RUN_STATUS_FG.held).not.toBe(RUN_STATUS_FG.done);
  });
});

describe('animation asserts progress, so only live states animate', () => {
  it('spins for queued and running', () => {
    expect(RUN_STATUS_ANIMATES.queued).toBe(true);
    expect(RUN_STATUS_ANIMATES.running).toBe(true);
  });

  it('is static for paused and held', () => {
    // A spinning indicator is how a user decides to keep waiting rather
    // than intervene, so animating a stopped run is actively misleading.
    expect(RUN_STATUS_ANIMATES.paused).toBe(false);
    expect(RUN_STATUS_ANIMATES.held).toBe(false);
  });

  it('is static for every terminal state', () => {
    for (const s of ['done', 'partial', 'failed', 'cancelled'] as const) {
      expect(RUN_STATUS_ANIMATES[s]).toBe(false);
    }
  });
});

describe('ordering puts problems where they cannot be clipped', () => {
  it('ranks held and failed ahead of done', () => {
    const i = (s: string) => RUN_STATUS_ORDER.indexOf(s as never);
    expect(i('held')).toBeLessThan(i('done'));
    expect(i('failed')).toBeLessThan(i('done'));
  });

  it('ranks every needs-attention state ahead of every benign one', () => {
    const i = (s: string) => RUN_STATUS_ORDER.indexOf(s as never);
    const attention = ['held', 'failed', 'partial', 'cancelled'];
    const benign = ['running', 'paused', 'queued', 'done'];
    for (const a of attention) {
      for (const x of benign) {
        expect(i(a)).toBeLessThan(i(x));
      }
    }
  });
});

describe('clusters count work, not records', () => {
  it('returns nothing for no bindings', () => {
    expect(statusClusters([])).toEqual([]);
    expect(statusClusters(null)).toEqual([]);
    expect(statusClusters(undefined)).toEqual([]);
  });

  it('ignores a staged binding, which has no run', () => {
    expect(statusClusters([b({ id: 'b1' })])).toEqual([]);
  });

  it('counts one gear for one run', () => {
    const out = statusClusters([
      b({ id: 'b1', run_id: 'r1', run_status: 'done' }),
    ]);
    expect(out).toHaveLength(1);
    expect(out[0].status).toBe('done');
    expect(out[0].count).toBe(1);
  });

  it('counts a retry lineage ONCE, not once per attempt', () => {
    // Three bindings, one logical piece of work.  Reporting "3 failed"
    // for a card that failed once inflates the apparent damage — the
    // direction of error that matters.
    const out = statusClusters([
      b({ id: 'b1', run_id: 'r1', root_run_id: 'r1', attempt: 1, run_status: 'failed' }),
      b({ id: 'b2', run_id: 'r2', root_run_id: 'r1', attempt: 2, run_status: 'failed' }),
      b({ id: 'b3', run_id: 'r3', root_run_id: 'r1', attempt: 3, run_status: 'held' }),
    ]);
    expect(out).toHaveLength(1);
    expect(out[0].status).toBe('held');
    expect(out[0].count).toBe(1);
  });

  it('separates distinct cards into distinct clusters', () => {
    const out = statusClusters([
      b({ id: 'b1', run_id: 'r1', root_run_id: 'r1', run_status: 'done' }),
      b({ id: 'b2', run_id: 'r2', root_run_id: 'r2', run_status: 'held' }),
      b({ id: 'b3', run_id: 'r3', root_run_id: 'r3', run_status: 'done' }),
    ]);
    const byStatus = Object.fromEntries(out.map(c => [c.status, c.count]));
    expect(byStatus).toEqual({ held: 1, done: 2 });
  });

  it('emits clusters in attention-first order', () => {
    const out = statusClusters([
      b({ id: 'b1', run_id: 'r1', root_run_id: 'r1', run_status: 'done' }),
      b({ id: 'b2', run_id: 'r2', root_run_id: 'r2', run_status: 'running' }),
      b({ id: 'b3', run_id: 'r3', root_run_id: 'r3', run_status: 'held' }),
    ]);
    expect(out.map(c => c.status)).toEqual(['held', 'running', 'done']);
  });

  it('carries colour, animation and hint onto each cluster', () => {
    const out = statusClusters([
      b({ id: 'b1', run_id: 'r1', root_run_id: 'r1', run_status: 'held' }),
    ]);
    expect(out[0].color).toBe(RUN_STATUS_FG.held);
    expect(out[0].animate).toBe(false);
    expect(out[0].hint).toMatch(/infrastructure/i);
  });

  it('skips an unrecognised status rather than throwing', () => {
    // A newer server can send a status this build has never heard of.  A
    // missing gear is recoverable; a thrown render loses the sidebar.
    const out = statusClusters([
      b({ id: 'b1', run_id: 'r1', root_run_id: 'r1', run_status: 'quantum' as never }),
      b({ id: 'b2', run_id: 'r2', root_run_id: 'r2', run_status: 'done' }),
    ]);
    expect(out.map(c => c.status)).toEqual(['done']);
  });

  it('ignores a binding whose run has no status yet', () => {
    const out = statusClusters([b({ id: 'b1', run_id: 'r1' })]);
    expect(out).toEqual([]);
  });
});

describe('the count is shown only when it means something', () => {
  it('hides "1"', () => {
    expect(showCount({ count: 1 } as never)).toBe(false);
  });

  it('shows 2 and above', () => {
    expect(showCount({ count: 2 } as never)).toBe(true);
    expect(showCount({ count: 17 } as never)).toBe(true);
  });
});


describe('clustersFromCounts (project-wide index path)', () => {
  it('builds clusters from pre-counted statuses', () => {
    const cs = clustersFromCounts({ done: 2, held: 1 });
    expect(cs.map(c => [c.status, c.count])).toEqual([['held', 1], ['done', 2]]);
  });

  it('orders needs-attention first, same as the bindings path', () => {
    // The property that matters most: a conversation's gears must look
    // identical whether or not it happens to be the open chat.  If these
    // two paths ordered differently, switching conversations would
    // reshuffle the row for no reason the user could see.
    const cs = clustersFromCounts({ done: 1, failed: 1, running: 1, held: 1 });
    expect(cs.map(c => c.status)).toEqual(['held', 'failed', 'running', 'done']);
  });

  it('takes colour and animation from the shared maps', () => {
    const cs = clustersFromCounts({ running: 1, held: 1 });
    const running = cs.find(c => c.status === 'running')!;
    const held = cs.find(c => c.status === 'held')!;
    // Foreground, not fill: the sidebar gear is drawn on a surface.
    expect(running.color).toBe(RUN_STATUS_FG.running);
    expect(running.color).not.toBe(RUN_STATUS_FILL.running);
    expect(running.animate).toBe(true);
    expect(held.animate).toBe(false);
  });

  it('skips zero and unknown statuses', () => {
    expect(clustersFromCounts({ done: 0 })).toEqual([]);
    expect(clustersFromCounts({ teleported: 3 } as any)).toEqual([]);
  });

  it('is empty for null or empty input', () => {
    expect(clustersFromCounts(null)).toEqual([]);
    expect(clustersFromCounts({})).toEqual([]);
  });

  it('agrees with the bindings path for the same logical runs', () => {
    // Cross-check: two bindings done + one held, versus the counts the
    // server-side index would produce for the same runs.
    const bindings = [
      { id: 'b1', chat_id: 'c', card_id: 'k', run_id: 'r1',
        run_status: 'done', root_run_id: 'r1', attempt: 1, created_at: 1 },
      { id: 'b2', chat_id: 'c', card_id: 'k', run_id: 'r2',
        run_status: 'done', root_run_id: 'r2', attempt: 1, created_at: 2 },
      { id: 'b3', chat_id: 'c', card_id: 'k', run_id: 'r3',
        run_status: 'held', root_run_id: 'r3', attempt: 1, created_at: 3 },
    ] as unknown as TaskBinding[];
    const fromBindings = statusClusters(bindings)
      .map(c => [c.status, c.count, c.color, c.animate]);
    const fromCounts = clustersFromCounts({ done: 2, held: 1 })
      .map(c => [c.status, c.count, c.color, c.animate]);
    expect(fromCounts).toEqual(fromBindings);
  });
});
