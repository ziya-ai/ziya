/**
 * Tests for collapseLineages — one tile per attempt lineage.
 *
 * A resume creates a new run AND a new binding, so a card retried twice
 * previously rendered three tiles side by side with nothing stating
 * their relationship.  That was the whole confusion: prior state WAS
 * preserved, but a second tile silently appearing said nothing about
 * it.  This collapses each lineage to its newest attempt; the rest are
 * reachable from that tile's attempt rail.
 *
 * The function is pure over the server-enriched binding list, which is
 * the point: the previous implementation issued one getTaskRun per
 * binding, and did so against the VIEWING project — which 404s for a
 * cross-project global chat, where the server resolved bindings from
 * the chat's owning project instead.  Reading the lineage fields the
 * list endpoint already stamps removes both the request burst and that
 * bug, and makes the decision unit-testable.
 */

import { collapseLineages } from '../lineageCollapse';
import type { TaskBinding } from '../../../types/task_binding';

/** A launched binding whose run carries lineage fields. */
function b(
  id: string,
  over: Partial<TaskBinding> = {},
): TaskBinding {
  return {
    id, chat_id: 'chat-1', card_id: 'card-1',
    run_id: `run-${id}`, anchor_message_id: 'msg-1', created_at: 0,
    ...over,
  } as TaskBinding;
}

/** Binding for attempt N of a lineage rooted at ``root``. */
function attempt(
  id: string, root: string, n: number, over: Partial<TaskBinding> = {},
): TaskBinding {
  return b(id, { root_run_id: root, attempt: n, ...over } as Partial<TaskBinding>);
}

describe('collapseLineages', () => {
  it('keeps a lone binding', () => {
    const only = attempt('b1', 'run-b1', 1);
    expect(collapseLineages([only])).toEqual(new Set());
  });

  it('drops every attempt but the newest', () => {
    const drop = collapseLineages([
      attempt('b1', 'r1', 1),
      attempt('b2', 'r1', 2),
      attempt('b3', 'r1', 3),
    ]);
    expect(drop).toEqual(new Set(['b1', 'b2']));
  });

  it('is order-independent — newest wins even when listed first', () => {
    // list_for_chat ordering is not guaranteed to be chronological, and
    // a max-by-attempt fold must not depend on it.
    const drop = collapseLineages([
      attempt('b3', 'r1', 3),
      attempt('b1', 'r1', 1),
      attempt('b2', 'r1', 2),
    ]);
    expect(drop).toEqual(new Set(['b1', 'b2']));
  });

  it('collapses each lineage independently', () => {
    const drop = collapseLineages([
      attempt('a1', 'rA', 1),
      attempt('a2', 'rA', 2),
      attempt('c1', 'rC', 1),
      attempt('c2', 'rC', 2),
      attempt('c3', 'rC', 3),
    ]);
    expect(drop).toEqual(new Set(['a1', 'c1', 'c2']));
  });

  it('never drops a staged binding', () => {
    // A staged binding has no run at all, so it is not part of any
    // lineage and the tile must still render its Run button.
    const staged = b('s1', { run_id: null });
    const drop = collapseLineages([
      staged, attempt('b1', 'r1', 1), attempt('b2', 'r1', 2),
    ]);
    expect(drop.has('s1')).toBe(false);
    expect(drop).toEqual(new Set(['b1']));
  });

  it('treats a pre-lineage record as its own single-attempt lineage', () => {
    // Runs written before lineage tracking have no root_run_id.  Keying
    // on the run id makes each its own lineage, so none is collapsed
    // away — an existing chat must not lose tiles on upgrade.
    const drop = collapseLineages([
      b('b1', { run_id: 'old-1' }),
      b('b2', { run_id: 'old-2' }),
    ]);
    expect(drop).toEqual(new Set());
  });

  it('does not merge a pre-lineage run into an unrelated lineage', () => {
    // The id-fallback must key on the RUN id, not the binding id, or two
    // unrelated legacy runs could collide.
    const drop = collapseLineages([
      b('b1', { run_id: 'r1' }),                 // legacy, keys on 'r1'
      attempt('b2', 'r1', 2, { run_id: 'r2' }),  // real attempt 2 of r1
    ]);
    // b1's run IS r1, the lineage root, so it is attempt 1 by implication
    // and loses to attempt 2.
    expect(drop).toEqual(new Set(['b1']));
  });

  it('defaults a missing attempt number to 1', () => {
    const drop = collapseLineages([
      b('b1', { root_run_id: 'r1' }),            // no attempt -> 1
      attempt('b2', 'r1', 2),
    ]);
    expect(drop).toEqual(new Set(['b1']));
  });

  it('keeps exactly one binding when attempts tie', () => {
    // Two records claiming the same attempt is a corrupt state, but it
    // must not render two tiles for one lineage — a tie resolves
    // deterministically rather than keeping both.
    const drop = collapseLineages([
      attempt('b1', 'r1', 2),
      attempt('b2', 'r1', 2),
    ]);
    expect(drop.size).toBe(1);
  });

  it('is empty for an empty list', () => {
    expect(collapseLineages([])).toEqual(new Set());
  });

  it('ignores anchors — a lineage collapses across anchor points', () => {
    // A resumed run reuses the source's anchor, but if that anchor were
    // ever missing the attempts must still collapse: grouping is by
    // lineage, not by position in the chat.
    const drop = collapseLineages([
      attempt('b1', 'r1', 1, { anchor_message_id: 'msg-1' }),
      attempt('b2', 'r1', 2, { anchor_message_id: null }),
    ]);
    expect(drop).toEqual(new Set(['b1']));
  });
});

describe('the hook reads the enriched list, not N round trips', () => {
  const fs = require('fs');
  const path = require('path');
  const src = fs.readFileSync(
    path.join(__dirname, '../../../hooks/useTaskBindings.ts'), 'utf8',
  );

  it('does not fetch a run per binding', () => {
    // The list endpoint already loads every run to stamp run_status, so
    // a per-binding getTaskRun is a redundant request burst.
    expect(src).not.toContain('getTaskRun');
  });

  it('delegates the decision to the pure helper', () => {
    expect(src).toContain('collapseLineages');
  });

  it('applies the collapse when grouping by anchor', () => {
    // Computing the set but not consulting it would silently restore
    // the multi-tile behaviour.
    expect(src).toMatch(/supersededIds\.has\(/);
  });
});

describe('server stamps the lineage fields the collapse needs', () => {
  const fs = require('fs');
  const path = require('path');
  const src = fs.readFileSync(
    path.join(__dirname, '../../../../../app/api/task_bindings.py'), 'utf8',
  );

  it('enriches bindings with root_run_id and attempt', () => {
    // Without these on the list response the collapse silently becomes a
    // no-op and every attempt renders its own tile again.
    expect(src).toContain('b.root_run_id');
    expect(src).toContain('b.attempt');
  });

  it('stamps them from the run it already loaded for run_status', () => {
    // Reusing that lookup is the entire reason this costs nothing; a
    // separate pass would reintroduce per-binding I/O server-side.
    expect(src).toMatch(/b\.run_status\s*=\s*run\.status/);
  });
});
