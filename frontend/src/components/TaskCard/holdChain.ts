/**
 * holdChain — where a block sits relative to an infrastructure hold.
 *
 * A hold is not a property of one block.  When a fan-out collapses on a
 * dead credential the run stops, but the *reason* lives on the run record
 * while the *location* lives in held_at_block_id, and neither says
 * anything about the blocks around it.  Looking at any single node
 * therefore tells you nothing about whether you are upstream of the
 * fault, downstream of it, or the fault itself — which is exactly the
 * "click into every subagent to find out" problem.
 *
 * This module derives, for every block in the tree, one of four
 * positions, so the same fact is legible from wherever the user is
 * looking:
 *
 *   'local'      — this block is where the fault was raised.
 *   'descendant' — a block BELOW this one is held; this container is
 *                  holding because its child could not finish.
 *   'ancestor'   — a block ABOVE this one is held, so this block was
 *                  never admitted (or was cancelled by the gate).  The
 *                  hold is not about this block at all.
 *   'none'       — unrelated to the hold.
 *
 * Deliberately pure and free of React so it can be unit-tested against a
 * plain tree, and so the tile, the inspector and the run map read the
 * SAME derivation rather than each re-implementing the walk with
 * slightly different edge cases.
 *
 * Why not read a block status of 'held'?  Because the backend's
 * BlockStatus has no such value: a held run marks its faulting block
 * 'failed', identical to a genuine failure of the work.  The run
 * record's held_at_block_id is the only authority on which block it
 * was, so the derivation is anchored there, not on per-block status.
 */

import type { Block } from '../../types/task_card';
import type { CalleeContext, HeldFaults, TaskRun } from '../../types/task_run';

export type HoldPosition = 'local' | 'descendant' | 'ancestor' | 'none';

export interface HoldChain {
  /** True when the run actually held; everything else is inert if false. */
  isHeld: boolean;
  /** Fault kind, e.g. 'authentication_error'. */
  kind: string | null;
  /** Block the fault was raised at, from the run record. */
  heldBlockId: string | null;
  /** Aggregate breadth, when the backend supplied it. */
  faults: HeldFaults | null;
  /** Prose reason the fleet gate fired; null when no gate fired. */
  gateReason: string | null;
  /** Position per block id.  Blocks absent from the map are 'none'. */
  positions: Map<string, HoldPosition>;
  /**
   * Ids from the root down to (and including) the held block — the
   * breadcrumb, in tree terms rather than Call-target names.
   */
  pathToHold: string[];
}

const EMPTY: HoldChain = {
  isHeld: false,
  kind: null,
  heldBlockId: null,
  faults: null,
  gateReason: null,
  positions: new Map(),
  pathToHold: [],
};

/**
 * Locate a block id, returning the ancestor chain root-first and
 * inclusive of the target.  Empty when not found.
 *
 * Iterative rather than recursive: an expanded Call target can nest to
 * MAX_CALL_DEPTH, and a malformed tree containing a cycle would blow the
 * stack.  ``seen`` makes a cycle terminate instead of hanging the tile.
 */
function findPath(root: Block | null | undefined, targetId: string): string[] {
  if (!root || !targetId) return [];
  const seen = new Set<Block>();
  const stack: Array<{ node: Block; path: string[] }> = [
    { node: root, path: [] },
  ];
  while (stack.length) {
    const { node, path } = stack.pop()!;
    if (seen.has(node)) continue;
    seen.add(node);
    const here = node.id ? [...path, node.id] : path;
    if (node.id && node.id === targetId) return here;
    for (const child of node.body || []) {
      stack.push({ node: child, path: here });
    }
  }
  return [];
}

/** Every block id at or below ``root``. */
function collectSubtree(root: Block | null | undefined): string[] {
  if (!root) return [];
  const out: string[] = [];
  const seen = new Set<Block>();
  const stack: Block[] = [root];
  while (stack.length) {
    const node = stack.pop()!;
    if (seen.has(node)) continue;
    seen.add(node);
    if (node.id) out.push(node.id);
    for (const child of node.body || []) stack.push(child);
  }
  return out;
}

/** Find a node by id, iteratively. */
function findNode(
  root: Block | null | undefined, targetId: string,
): Block | null {
  if (!root || !targetId) return null;
  const seen = new Set<Block>();
  const stack: Block[] = [root];
  while (stack.length) {
    const node = stack.pop()!;
    if (seen.has(node)) continue;
    seen.add(node);
    if (node.id === targetId) return node;
    for (const child of node.body || []) stack.push(child);
  }
  return null;
}

/**
 * Derive the hold chain for a run against its block tree.
 *
 * Returns an inert result for any run that is not held, so callers can
 * call this unconditionally and branch on ``isHeld``.
 */
export function deriveHoldChain(
  run: Pick<
    TaskRun,
    'status' | 'held_reason' | 'held_at_block_id' | 'held_faults'
    | 'held_gate_reason'
  > | null | undefined,
  root: Block | null | undefined,
): HoldChain {
  if (!run || run.status !== 'held') return EMPTY;

  const heldBlockId = run.held_at_block_id || null;
  const base = {
    isHeld: true,
    kind: run.held_reason || null,
    heldBlockId,
    faults: run.held_faults || null,
    gateReason: run.held_gate_reason || null,
  };

  // A held run with no recorded block: the fault happened outside any
  // identified block (or predates the field).  Still report the hold —
  // suppressing it because the location is unknown is how a hold becomes
  // invisible — but with no per-block positions to assign.
  if (!heldBlockId || !root) {
    return { ...base, positions: new Map(), pathToHold: [] };
  }

  const pathToHold = findPath(root, heldBlockId);
  const positions = new Map<string, HoldPosition>();

  // Not found in this tree: the held block may live in a Call target not
  // yet expanded into the snapshot.  Report the hold without positions
  // rather than mislabelling every block 'none', which would read as
  // "no hold here".
  if (pathToHold.length === 0) {
    return { ...base, positions, pathToHold: [] };
  }

  // Ancestors of the held block are holding BECAUSE of it.
  for (const id of pathToHold.slice(0, -1)) {
    positions.set(id, 'descendant');
  }
  positions.set(heldBlockId, 'local');

  // Everything below the held block was cut off by it.
  const heldNode = findNode(root, heldBlockId);
  for (const id of collectSubtree(heldNode)) {
    if (id !== heldBlockId) positions.set(id, 'ancestor');
  }

  // Every remaining block is a sibling (or a sibling's descendant) of
  // something on the hold path.  Those under a 'descendant' container are
  // likewise blocked by an ancestor's hold: the container cannot
  // complete, so nothing beneath it will run.  Assigning them 'ancestor'
  // is what makes the fact visible from a leaf the user drilled into,
  // which is the entire point of this module.
  const onPath = new Set(pathToHold);
  for (const containerId of pathToHold.slice(0, -1)) {
    const container = findNode(root, containerId);
    for (const id of collectSubtree(container)) {
      if (!onPath.has(id) && !positions.has(id)) {
        positions.set(id, 'ancestor');
      }
    }
  }

  return { ...base, positions, pathToHold };
}

/**
 * The same derivation, from a CALLEE's point of view.
 *
 * A Call runs inline in the caller's run, so a held CL1 has no run record
 * of its own — the hold lives on CL0's run.  What makes this resolvable
 * without per-callee run records is that the recorded `callee_root` is the
 * callee's own `card.root` with its own block ids, so the caller's
 * `held_at_block_id` is directly meaningful against the callee's tree.
 * The full derivation is therefore reused verbatim rather than
 * reimplemented for this frame.
 *
 * Returns an INERT chain when `held_in_callee` is false.  That guard is
 * the whole safety property of this function: a hold in a sibling callee
 * (CL2) is legitimately reported as context for CL1 — CL1 did take part
 * in a held run — but drawing it on CL1's blocks would point the user at
 * a card that is fine, which is worse than showing nothing at all.
 */
export function deriveCalleeHoldChain(
  ctx: CalleeContext | null | undefined,
): HoldChain {
  if (!ctx || !ctx.held_in_callee) return EMPTY;
  return deriveHoldChain(
    {
      // The context only ever carries hold fields when the CALLER's run
      // is held, and held_in_callee narrows that to this card's subtree;
      // asserting the status here keeps deriveHoldChain's contract
      // ("inert unless held") intact without widening its signature to
      // accept a second, differently-shaped record.
      status: 'held',
      held_reason: ctx.held_reason,
      held_at_block_id: ctx.held_at_block_id,
      held_faults: ctx.held_faults,
      held_gate_reason: ctx.held_gate_reason,
    },
    ctx.callee_root,
  );
}

/** Position for one block; 'none' when unknown or not held. */
export function positionOf(chain: HoldChain, blockId: string): HoldPosition {
  if (!chain.isHeld || !blockId) return 'none';
  return chain.positions.get(blockId) || 'none';
}

/**
 * Breadth in words: what distinguishes "the credential died and took the
 * whole fleet" from "one subagent got throttled".  Null when the backend
 * sent no aggregate, so callers render nothing rather than "0 of 0".
 */
export function describeBreadth(
  faults: HeldFaults | null | undefined,
): string | null {
  if (!faults || !faults.fault_count) return null;
  const { fault_count: n, fanout_width: w, fleet_wide: fleet } = faults;
  if (w > 1) {
    return `${n} of ${w} ${fleet ? 'subagents — fleet-wide' : 'subagents'}`;
  }
  return n > 1 ? `${n} faults` : null;
}

/**
 * One-line label for a position.  Phrased so the reader learns whether
 * the hold is THEIR problem or someone else's without opening anything.
 */
export function holdLabel(
  chain: HoldChain, position: HoldPosition,
): string | null {
  if (!chain.isHeld || position === 'none') return null;
  const kind = chain.kind
    ? chain.kind.replace(/_/g, ' ') : 'infrastructure fault';
  const breadth = describeBreadth(chain.faults);
  switch (position) {
    case 'local':
      return `Held here — ${kind}${breadth ? ` (${breadth})` : ''}`;
    case 'descendant':
      return `Holding — a step below stopped on ${kind}`
        + `${breadth ? ` (${breadth})` : ''}`;
    case 'ancestor':
      return `Blocked — an earlier step is held on ${kind}`;
    default:
      return null;
  }
}

/**
 * What the hold is gated on, in the user's terms: the actionable
 * sentence.  Prefers the backend's gate reason (which knows WHY the fleet
 * was stopped) and falls back to a per-kind remedy.
 */
export function describeGate(chain: HoldChain): string | null {
  if (!chain.isHeld) return null;
  if (chain.gateReason) return chain.gateReason;
  if (!chain.kind) return null;
  switch (chain.kind) {
    case 'authentication_error':
      return 'Credentials expired or were rejected — refresh them, then resume.';
    case 'throttling_error':
      return 'Provider throttling outlasted the retry budget — resume when capacity returns.';
    case 'connection_error':
      return 'Lost connection to the provider endpoint — resume once reachable.';
    case 'transient_service_error':
      return 'Provider returned a transient service error — resume to retry.';
    default:
      return `Stopped on ${chain.kind.replace(/_/g, ' ')}.`;
  }
}
