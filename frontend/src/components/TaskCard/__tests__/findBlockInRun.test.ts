/**
 * findBlockInRun — labels for blocks that live inside a called card.
 *
 * A Call is named, not inlined, so the callee's blocks exist in neither
 * the card nor ``card_snapshot`` — only in ``run.call_snapshots``.  Every
 * label derived via ``findBlockById(displayCard.root, …)`` therefore
 * returned null for a callee block and degraded to the raw id, which is
 * how the recovery banner came to read "↻ Retry b-cf96c4e2".
 *
 * That fallback was the visible tell for a much larger defect — the
 * resume request itself 404'd, because the server searched the same tree —
 * so these tests pin the resolution rather than the cosmetics: a callee
 * block must be findable, and the two id spaces must not be conflated.
 */

import type { Block } from '../../../types/task_card';
import { findBlockById, findBlockInRun } from '../runMapModel';

const task = (id: string, name: string): Block =>
  ({ id, block_type: 'task', name, instructions: 'go', body: [] } as Block);

const CALLEE: Block = {
  id: 'b-cl1-root', block_type: 'group', name: 'CL1',
  body: [
    task('b-recon', 'Stage 1: Recon'),
    {
      id: 'b-cf96c4e2', block_type: 'repeat',
      name: 'Stage 2: Parallel subsystem auditors',
      repeat_mode: 'count', repeat_count: 20, repeat_parallel: true,
      body: [task('b-auditor', 'Audit subsystem')],
    } as Block,
  ],
} as Block;

const CARD: Block = {
  id: 'root', block_type: 'group', name: 'CL0',
  body: [
    { id: 'b-params', block_type: 'state', name: 'Study parameters', body: [] } as Block,
    { id: 'call-p1', block_type: 'call', name: 'Phase 1 — Ziya ground truth',
      call_target: 'CL1', body: [] } as Block,
  ],
} as Block;

const SNAPS = {
  'call-p1': { key: 'card:cl1', target: 'CL1', root: CALLEE },
};

describe('findBlockInRun', () => {
  it('reproduces the raw-id fallback when only the card is searched', () => {
    expect(findBlockById(CARD, 'b-cf96c4e2')).toBeNull();
  });

  it('finds a block inside a recorded callee', () => {
    const b = findBlockInRun(CARD, SNAPS, 'b-cf96c4e2');
    expect(b?.name).toBe('Stage 2: Parallel subsystem auditors');
  });

  it('finds a nested block inside a callee', () => {
    expect(findBlockInRun(CARD, SNAPS, 'b-auditor')?.name)
      .toBe('Audit subsystem');
  });

  it("prefers the card's own tree", () => {
    // A Call block's id is a KEY of call_snapshots, never a node inside
    // one, so the two id spaces are disjoint — but assert the precedence
    // anyway so a future change that inlines callees cannot silently make
    // a caller block resolve to a callee's.
    expect(findBlockInRun(CARD, SNAPS, 'call-p1')?.name)
      .toBe('Phase 1 — Ziya ground truth');
  });

  it('returns null for an unknown id', () => {
    expect(findBlockInRun(CARD, SNAPS, 'ghost')).toBeNull();
  });

  it('degrades to the card alone when there are no snapshots', () => {
    expect(findBlockInRun(CARD, undefined, 'b-params')?.name)
      .toBe('Study parameters');
    expect(findBlockInRun(CARD, undefined, 'b-cf96c4e2')).toBeNull();
  });

  it('tolerates a snapshot with no recorded root', () => {
    const partial = { 'call-p1': { target: 'CL1' } } as any;
    expect(() => findBlockInRun(CARD, partial, 'b-recon')).not.toThrow();
    expect(findBlockInRun(CARD, partial, 'b-recon')).toBeNull();
  });
});
