/**
 * Deriving a callee's OWN hold chain from its caller's run.
 *
 * The scenario: CL0 calls CL1..CL6.  A Call runs inline, so exactly ONE
 * run record exists and CL0 owns it.  Opening CL1 in the deck showed
 * nothing at all, because a run lookup by card id filters on the run's
 * owner — yet CL1 is the card actually holding the study.
 *
 * These tests pin the claim that makes the fix cheap: the callee tree
 * recorded in the caller's `call_snapshots` carries the callee's OWN block
 * ids, so `held_at_block_id` from the caller's run resolves directly
 * against the callee's own card and `deriveHoldChain` can be reused
 * verbatim — no new derivation, no per-callee run records.
 *
 * They also pin the guard that stops the worst failure mode: a hold in a
 * SIBLING callee must not be drawn on this card's blocks.  Pointing a user
 * at a card that is fine is worse than showing nothing.
 */

import type { Block } from '../../../types/task_card';
import type { CalleeContext, HeldFaults } from '../../../types/task_run';
import {
  deriveHoldChain, positionOf, holdLabel, describeBreadth, describeGate,
} from '../holdChain';

/** CL1's own tree, as recorded in CL0's call_snapshots. */
function cl1Root(): Block {
  return {
    id: 'cl1-root', block_type: 'group', name: 'CL1', body: [
      { id: 'cl1-recon', block_type: 'task', name: 'Recon', body: [] },
      {
        id: 'cl1-fanout', block_type: 'repeat', name: 'Auditors', body: [
          { id: 'cl1-auditor', block_type: 'task', name: 'Audit', body: [] },
        ],
      },
      { id: 'cl1-merge', block_type: 'task', name: 'Merge', body: [] },
    ],
  } as unknown as Block;
}

const FAULTS: HeldFaults = {
  fault_count: 18,
  fanout_width: 20,
  primary_kind: 'authentication_error',
  kinds: { authentication_error: 18 },
  call_path: ['CL0', 'CL1: Ziya Ground Truth', 'audit-mcp-security'],
  fleet_wide: true,
  block_ids: ['cl1-auditor'],
};

function ctx(overrides: Partial<CalleeContext> = {}): CalleeContext {
  return {
    run_id: 'run-cl0',
    run_status: 'held',
    caller_card_id: 'card-CL0',
    call_block_id: 'cl0-call1',
    callee_target: 'CL1',
    callee_root: cl1Root(),
    held_at_block_id: 'cl1-fanout',
    held_in_callee: true,
    held_reason: 'authentication_error',
    held_faults: FAULTS,
    held_gate_reason: 'authentication_error is a session-level fault',
    updated_at: 100,
    ...overrides,
  };
}

/**
 * The adapter a deck page needs.  Deliberately a plain function here
 * rather than in holdChain.ts: it proves the existing derivation is
 * reusable as-is, and if this type-checks the production wiring is a
 * one-liner with no new module.
 *
 * Gates on held_in_callee — that is the whole point.
 */
function chainForCallee(c: CalleeContext | null | undefined) {
  if (!c || !c.held_in_callee) {
    return deriveHoldChain(null, null);
  }
  return deriveHoldChain(
    {
      status: 'held',
      held_reason: c.held_reason,
      held_at_block_id: c.held_at_block_id,
      held_faults: c.held_faults,
      held_gate_reason: c.held_gate_reason,
    },
    c.callee_root,
  );
}

describe('a callee resolves its own portion of the blocking tree', () => {
  it('marks the held block in the callee\'s own frame', () => {
    const chain = chainForCallee(ctx());
    expect(chain.isHeld).toBe(true);
    expect(positionOf(chain, 'cl1-fanout')).toBe('local');
  });

  it('marks the callee\'s own containers as holding', () => {
    const chain = chainForCallee(ctx());
    expect(positionOf(chain, 'cl1-root')).toBe('descendant');
  });

  it('marks the callee\'s later stages as blocked', () => {
    const chain = chainForCallee(ctx());
    // cl1-merge never ran: an upstream fault stopped the run first.
    expect(positionOf(chain, 'cl1-merge')).toBe('ancestor');
  });

  it('marks the fan-out\'s body as cut off by the fault above it', () => {
    const chain = chainForCallee(ctx());
    expect(positionOf(chain, 'cl1-auditor')).toBe('ancestor');
  });

  it('leaves no block in the callee\'s tree unlabelled', () => {
    const chain = chainForCallee(ctx());
    const all = ['cl1-root', 'cl1-recon', 'cl1-fanout', 'cl1-auditor', 'cl1-merge'];
    const unlabelled = all.filter(id => positionOf(chain, id) === 'none');
    expect(unlabelled).toEqual([]);
  });

  it('resolves a hold on a NESTED callee block too', () => {
    const chain = chainForCallee(ctx({ held_at_block_id: 'cl1-auditor' }));
    expect(positionOf(chain, 'cl1-auditor')).toBe('local');
    expect(positionOf(chain, 'cl1-fanout')).toBe('descendant');
    expect(positionOf(chain, 'cl1-root')).toBe('descendant');
  });
});

describe('a sibling callee\'s hold is never drawn on this card', () => {
  it('produces an inert chain when held_in_callee is false', () => {
    // The hold is in CL2.  CL1's page must not mark any of CL1's blocks.
    const chain = chainForCallee(ctx({
      held_in_callee: false, held_at_block_id: 'cl2-roster',
    }));
    expect(chain.isHeld).toBe(false);
    expect(positionOf(chain, 'cl1-fanout')).toBe('none');
    expect(positionOf(chain, 'cl1-root')).toBe('none');
  });

  it('produces an inert chain with no context at all', () => {
    const chain = chainForCallee(null);
    expect(chain.isHeld).toBe(false);
    expect(chain.positions.size).toBe(0);
  });

  it('is inert for a callee in a merely-running study', () => {
    const chain = chainForCallee(ctx({
      run_status: 'running', held_in_callee: false,
      held_at_block_id: null, held_reason: null, held_faults: null,
    }));
    expect(chain.isHeld).toBe(false);
  });
});

describe('breadth and remedy carry into the callee\'s view', () => {
  it('reports the fleet-wide breadth, not just the local fault', () => {
    const chain = chainForCallee(ctx());
    expect(describeBreadth(chain.faults)).toBe('18 of 20 subagents — fleet-wide');
  });

  it('prefers the backend gate reason over the generic remedy', () => {
    const chain = chainForCallee(ctx());
    expect(describeGate(chain)).toContain('session-level fault');
  });

  it('falls back to a per-kind remedy with no gate reason', () => {
    const chain = chainForCallee(ctx({ held_gate_reason: null }));
    expect(describeGate(chain)).toContain('Credentials expired');
  });

  it('labels the held row with kind and breadth', () => {
    const chain = chainForCallee(ctx());
    const label = holdLabel(chain, positionOf(chain, 'cl1-fanout'));
    expect(label).toContain('Held here');
    expect(label).toContain('authentication error');
    expect(label).toContain('18 of 20');
  });

  it('tells a blocked row the hold is not about it', () => {
    const chain = chainForCallee(ctx());
    const label = holdLabel(chain, positionOf(chain, 'cl1-merge'));
    expect(label).toContain('Blocked');
    expect(label).toContain('earlier step');
  });
});

describe('degraded records still surface the hold', () => {
  it('reports a hold whose block is unknown rather than hiding it', () => {
    const chain = chainForCallee(ctx({ held_at_block_id: null }));
    // held_in_callee is true but no block was recorded: the hold is real
    // and must still be visible, just without per-block positions.
    expect(chain.isHeld).toBe(true);
    expect(chain.positions.size).toBe(0);
  });

  it('reports a hold with no recorded callee tree', () => {
    const chain = chainForCallee(ctx({ callee_root: null }));
    expect(chain.isHeld).toBe(true);
    expect(chain.positions.size).toBe(0);
  });

  it('survives a held block absent from the recorded tree', () => {
    const chain = chainForCallee(ctx({ held_at_block_id: 'not-in-tree' }));
    expect(chain.isHeld).toBe(true);
    expect(positionOf(chain, 'cl1-fanout')).toBe('none');
  });
});
