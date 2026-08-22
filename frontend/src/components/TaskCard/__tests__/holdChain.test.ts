/**
 * holdChain derivation tests.
 *
 * The requirement being pinned: a hold must be legible from ANYWHERE on
 * the chain — you should be able to tell, from any single block, whether
 * you are the fault, holding because of a child, or blocked by an
 * ancestor.  These tests exist because the naive implementation (mark the
 * one held block and leave everything else 'none') satisfies the type
 * signature while completely failing that requirement, and nothing else
 * in the suite would catch it.
 */

import {
  deriveHoldChain, positionOf, holdLabel, describeBreadth, describeGate,
} from '../holdChain';
import type { Block } from '../../../types/task_card';
import type { HeldFaults, TaskRun } from '../../../types/task_run';

function blk(id: string, body: Block[] = [], type = 'group'): Block {
  return {
    block_type: type as Block['block_type'],
    id,
    name: id,
    body,
  } as Block;
}

/**
 * CL0 -> CL1 -> fan-out shape, mirroring the real stack:
 *
 *   root
 *     phase1 (group)
 *       state1
 *       fanout (repeat)
 *         auditor      <- the fault lands here
 *     phase2 (group)
 *       merge
 */
function tree(): Block {
  return blk('root', [
    blk('phase1', [
      blk('state1', [], 'state'),
      blk('fanout', [blk('auditor', [], 'task')], 'repeat'),
    ]),
    blk('phase2', [blk('merge', [], 'task')]),
  ]);
}

function heldRun(over: Partial<TaskRun> = {}): TaskRun {
  return {
    status: 'held',
    held_reason: 'authentication_error',
    held_at_block_id: 'auditor',
    ...over,
  } as TaskRun;
}

const FLEET: HeldFaults = {
  fault_count: 18,
  fanout_width: 20,
  primary_kind: 'authentication_error',
  kinds: { authentication_error: 18 },
  call_path: ['CL0', 'CL1', 'audit-mcp-security'],
  fleet_wide: true,
  block_ids: ['auditor'],
};

describe('deriveHoldChain — inert cases', () => {
  it('reports nothing for a running run', () => {
    const chain = deriveHoldChain({ status: 'running' } as TaskRun, tree());
    expect(chain.isHeld).toBe(false);
    expect(positionOf(chain, 'auditor')).toBe('none');
  });

  it('reports nothing for a failed run — a failure is not a hold', () => {
    const chain = deriveHoldChain(
      { status: 'failed', held_at_block_id: 'auditor' } as TaskRun, tree(),
    );
    expect(chain.isHeld).toBe(false);
  });

  it('still reports the hold when the block id is unknown', () => {
    // Suppressing a hold because its location is unknown is how a hold
    // becomes invisible — the opposite of the requirement.
    const chain = deriveHoldChain(
      heldRun({ held_at_block_id: null }), tree(),
    );
    expect(chain.isHeld).toBe(true);
    expect(chain.kind).toBe('authentication_error');
    expect(chain.positions.size).toBe(0);
  });

  it('still reports the hold when the block is not in this tree', () => {
    // Held block inside an unexpanded Call target.
    const chain = deriveHoldChain(
      heldRun({ held_at_block_id: 'not-in-tree' }), tree(),
    );
    expect(chain.isHeld).toBe(true);
    expect(chain.pathToHold).toEqual([]);
  });
});

describe('deriveHoldChain — position of every node', () => {
  it('marks the faulting block local', () => {
    const chain = deriveHoldChain(heldRun(), tree());
    expect(positionOf(chain, 'auditor')).toBe('local');
  });

  it('marks every ancestor as holding-on-a-descendant', () => {
    const chain = deriveHoldChain(heldRun(), tree());
    expect(positionOf(chain, 'fanout')).toBe('descendant');
    expect(positionOf(chain, 'phase1')).toBe('descendant');
    expect(positionOf(chain, 'root')).toBe('descendant');
  });

  it('marks siblings under a holding ancestor as blocked', () => {
    // This is the case the naive implementation gets wrong: from
    // 'state1' or 'merge' the user must still see that the chain is held.
    const chain = deriveHoldChain(heldRun(), tree());
    expect(positionOf(chain, 'state1')).toBe('ancestor');
    expect(positionOf(chain, 'phase2')).toBe('ancestor');
    expect(positionOf(chain, 'merge')).toBe('ancestor');
  });

  it('leaves no block on the tree unlabelled', () => {
    const chain = deriveHoldChain(heldRun(), tree());
    for (const id of [
      'root', 'phase1', 'state1', 'fanout', 'auditor', 'phase2', 'merge',
    ]) {
      expect(positionOf(chain, id)).not.toBe('none');
    }
  });

  it('records the root-first path to the hold', () => {
    const chain = deriveHoldChain(heldRun(), tree());
    expect(chain.pathToHold).toEqual(['root', 'phase1', 'fanout', 'auditor']);
  });

  it('marks descendants of the held block as blocked', () => {
    const t = blk('root', [
      blk('holder', [blk('childA', [blk('grandchild')])]),
    ]);
    const chain = deriveHoldChain(heldRun({ held_at_block_id: 'holder' }), t);
    expect(positionOf(chain, 'holder')).toBe('local');
    expect(positionOf(chain, 'childA')).toBe('ancestor');
    expect(positionOf(chain, 'grandchild')).toBe('ancestor');
  });
});

describe('deriveHoldChain — robustness', () => {
  it('terminates on a cyclic tree instead of hanging', () => {
    const a = blk('a');
    const b = blk('b', [a]);
    (a as { body: Block[] }).body = [b]; // cycle
    const root = blk('root', [a]);
    const chain = deriveHoldChain(heldRun({ held_at_block_id: 'b' }), root);
    expect(chain.isHeld).toBe(true);
    expect(positionOf(chain, 'b')).toBe('local');
  });

  it('tolerates a null tree', () => {
    const chain = deriveHoldChain(heldRun(), null);
    expect(chain.isHeld).toBe(true);
    expect(chain.positions.size).toBe(0);
  });
});

describe('describeBreadth — fleet-wide vs isolated', () => {
  it('reports a fleet-wide collapse with its denominator', () => {
    expect(describeBreadth(FLEET)).toBe('18 of 20 subagents — fleet-wide');
  });

  it('does not claim fleet-wide for one throttled sibling', () => {
    const one: HeldFaults = {
      ...FLEET,
      fault_count: 1,
      kinds: { throttling_error: 1 },
      primary_kind: 'throttling_error',
      fleet_wide: false,
    };
    expect(describeBreadth(one)).toBe('1 of 20 subagents');
  });

  it('renders nothing when no aggregate was supplied', () => {
    // Rather than "0 of 0", which reads as a measurement.
    expect(describeBreadth(null)).toBeNull();
    expect(describeBreadth(undefined)).toBeNull();
  });

  it('omits the denominator outside a fan-out', () => {
    expect(describeBreadth({
      ...FLEET, fault_count: 1, fanout_width: 0, fleet_wide: true,
    })).toBeNull();
  });
});

describe('holdLabel — the reader learns whose problem it is', () => {
  it('distinguishes local from descendant from ancestor', () => {
    const chain = deriveHoldChain(heldRun({ held_faults: FLEET }), tree());
    const local = holdLabel(chain, 'local')!;
    const desc = holdLabel(chain, 'descendant')!;
    const anc = holdLabel(chain, 'ancestor')!;
    expect(local).toContain('Held here');
    expect(desc).toContain('below');
    expect(anc).toContain('earlier step');
    expect(new Set([local, desc, anc]).size).toBe(3);
  });

  it('carries the breadth into the local and descendant labels', () => {
    const chain = deriveHoldChain(heldRun({ held_faults: FLEET }), tree());
    expect(holdLabel(chain, 'local')).toContain('18 of 20');
    expect(holdLabel(chain, 'descendant')).toContain('18 of 20');
  });

  it('returns nothing for an unrelated block', () => {
    const chain = deriveHoldChain(heldRun(), tree());
    expect(holdLabel(chain, 'none')).toBeNull();
  });
});

describe('describeGate — what the hold is gated on', () => {
  it('prefers the backend gate reason', () => {
    const chain = deriveHoldChain(
      heldRun({ held_gate_reason: 'authentication_error is session-level' }),
      tree(),
    );
    expect(describeGate(chain)).toBe('authentication_error is session-level');
  });

  it('gives an actionable remedy per kind when no gate fired', () => {
    const auth = deriveHoldChain(heldRun(), tree());
    expect(describeGate(auth)).toContain('Credentials');
    const thr = deriveHoldChain(
      heldRun({ held_reason: 'throttling_error' }), tree(),
    );
    expect(describeGate(thr)).toContain('throttling');
    // The two remedies must differ — both are infra kinds but they call
    // for opposite responses.
    expect(describeGate(auth)).not.toBe(describeGate(thr));
  });

  it('handles an unknown kind without crashing', () => {
    const chain = deriveHoldChain(
      heldRun({ held_reason: 'some_new_kind' }), tree(),
    );
    expect(describeGate(chain)).toContain('some new kind');
  });
});
