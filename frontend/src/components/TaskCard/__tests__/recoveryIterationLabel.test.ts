/**
 * The recovery banner must name the ITERATION a mid-loop resume lands on.
 *
 * Context: a serial loop held 22 iterations in is now resumed mid-loop by
 * the block-level endpoint (app/api/task_runs.py, via
 * resume_targets.serial_replay_prefix).  The banner that triggers that
 * resume, however, could only ever say "Execution resumes at <loop name>"
 * — the loop is one block, and RecoveryTarget carried a block id and
 * nothing else.  So the control that preserves 22 iterations describes
 * itself identically to one that would re-run them, which is precisely the
 * ambiguity that made the original defect hard to notice.
 *
 * ``bankedIterationPrefix`` answers "where will this land", and the
 * banner renders it.  It is a PREDICTION of a server-side decision, so
 * these tests pin it against the server's rule rather than against
 * whatever the frontend finds convenient:
 *
 *   - a prefix, not an index set (a serial loop's {{previous}} binds its
 *     immediate predecessor, so a gap cannot be skipped over);
 *   - stopping at the first index that is not a RETAINED PASS, for the
 *     two distinct reasons the server has — a failure is the work being
 *     redone, and a dropped artifact would replay as empty;
 *   - null rather than 0 for a parallel loop, which the server resumes by
 *     index set and for which an ordinal is meaningless.
 *
 * Two divergences from the server are known and deliberate, and are the
 * reason the banner's wording must not become a promise:
 *
 *   1. The server also consults ``resume_iteration_artifacts`` (indices
 *      inherited from an attempt further back).  That field is not on the
 *      wire, so a chained resume whose seeded summaries were lost can be
 *      resumed LATER than predicted — the banner under-states, which is
 *      the safe direction.
 *   2. The server truncates the prefix at the first artifact missing from
 *      disk.  A record/disk disagreement is an anomaly, but it means the
 *      run can start EARLIER than predicted.
 */

import type {
  IterationSummary, TaskRun, TaskRunBlockState,
} from '../../../types/task_run';
import type { Block } from '../../../types/task_card';

/**
 * ``bankedIterationPrefix``, resolved lazily.
 *
 * Deliberately not a static import: before the fix lands the export does
 * not exist, and a static import fails the whole module at compile time —
 * which would hide whether the wiring assertions below fail on the DEFECT
 * or merely on a missing symbol.
 */
const prefix = (run: unknown, block: unknown): number | null =>
  // eslint-disable-next-line @typescript-eslint/no-var-requires
  require('../recoveryTarget').bankedIterationPrefix(run, block);

// ── fixtures ───────────────────────────────────────────────────────

const LOOP = 'b-campaign';

const iter = (
  index: number, over: Partial<IterationSummary> = {},
): IterationSummary => ({
  index, status: 'passed', duration_ms: 1000, tokens: 100,
  has_artifact: true, ...over,
});

const loopBlock = (over: Partial<Block> = {}): Block => ({
  block_type: 'repeat', id: LOOP, name: 'Serial campaign',
  repeat_mode: 'count', repeat_count: 25, repeat_propagate: 'last',
  ...over,
} as Block);

const state = (summaries: IterationSummary[]): TaskRunBlockState => ({
  block_id: LOOP, block_type: 'repeat', status: 'held',
  completed_at: null, iteration_summaries: summaries,
} as TaskRunBlockState);

const mkRun = (summaries: IterationSummary[]): TaskRun => ({
  id: 'r1', card_id: 'c1', status: 'held', held_at_block_id: LOOP,
  cancel_requested: false, pause_requested: false,
  block_states: { [LOOP]: state(summaries) },
  total_tokens: 0, total_tool_calls: 0, created_at: 0, updated_at: 0,
} as TaskRun);

/** 0..n-1 all passed and retained. */
const contiguous = (n: number) =>
  Array.from({ length: n }, (_, i) => iter(i));

// ── the reported case ──────────────────────────────────────────────

describe('bankedIterationPrefix names where a mid-loop resume lands', () => {
  it('reports 22 for a loop held 22 iterations in', () => {
    // The reported scenario, and the assertion the whole change exists
    // for.  A null here means the banner has nothing to name and falls
    // back to "resumes at <loop>" — the ambiguous wording.
    expect(prefix(mkRun(contiguous(22)), loopBlock())).toBe(22);
  });

  it('is the count of banked iterations, i.e. the next index to run', () => {
    // Indices are 0-based and displayed raw as #N throughout the tile
    // (BlockDetailPanel's "re-run #N", the dot titles), so the value
    // rendered must be the index that will EXECUTE, not the last one
    // banked.  An off-by-one here is a banner that points at work it is
    // about to discard.
    const p = prefix(mkRun(contiguous(3)), loopBlock());
    expect(p).toBe(3);
  });

  it('works for an until loop, not only a repeat', () => {
    // Both push a binding frame and both honour resume_from_iteration in
    // the executor, so excluding until would refuse a shape the server
    // supports.
    expect(prefix(
      mkRun(contiguous(4)),
      loopBlock({ block_type: 'until', until_condition: 'green' } as any),
    )).toBe(4);
  });

  it('counts a replayed prefix carried from an earlier attempt', () => {
    // A chained resume's inherited iterations are seeded onto the run as
    // replayed=true summaries.  progressCounts deliberately EXCLUDES
    // those from progress aggregates, so reusing that figure here would
    // under-report the preserved work of exactly the runs most likely to
    // be resumed again.  The prefix is about what can be replayed, not
    // about what this attempt earned.
    const run = mkRun([
      ...contiguous(3).map(s => ({ ...s, replayed: true })),
      iter(3), iter(4),
    ]);
    expect(prefix(run, loopBlock())).toBe(5);
  });
});

// ── the stopping rule ──────────────────────────────────────────────

describe('the prefix stops where the server stops', () => {
  it('stops at the first failed iteration', () => {
    // The failure is the work being redone, so it must be re-run — and
    // everything after it is unreachable, even a later pass.
    const run = mkRun([
      iter(0), iter(1), iter(2), iter(3, { status: 'failed' }), iter(4),
    ]);
    expect(prefix(run, loopBlock())).toBe(3);
  });

  it('stops at a pass whose artifact was not retained', () => {
    // Past the 50-pass retention cap only a summary survives, so
    // replaying it would bind an empty {{previous}} — the failure this
    // check exists to avoid, and one that would look like a card defect.
    const run = mkRun([
      iter(0), iter(1), iter(2, { has_artifact: false }), iter(3),
    ]);
    expect(prefix(run, loopBlock())).toBe(2);
  });

  it('stops at a gap in the record', () => {
    // A serial loop's iterations are dependent, so a missing index
    // cannot be skipped the way a parallel fan-out's can.
    const run = mkRun([iter(0), iter(1), iter(3), iter(4)]);
    expect(prefix(run, loopBlock())).toBe(2);
  });

  it('treats an absent has_artifact as retained', () => {
    // Parity with the server, which reads has_artifact with a default of
    // True.  Records written before the field existed would otherwise
    // predict a prefix of 0 and silently lose the label.
    const run = mkRun([
      { index: 0, status: 'passed', duration_ms: 1, tokens: 1 } as any,
      { index: 1, status: 'passed', duration_ms: 1, tokens: 1 } as any,
    ]);
    expect(prefix(run, loopBlock())).toBe(2);
  });

  it('is unaffected by iteration_summaries arriving out of order', () => {
    // Summaries are appended as iterations SEAL, which under a resume is
    // not index order.  Anchoring on position in the array rather than on
    // the index field would make the prediction depend on completion
    // timing.
    const run = mkRun([iter(2), iter(0), iter(1)]);
    expect(prefix(run, loopBlock())).toBe(3);
  });
});

// ── the refusals: null, not 0 ──────────────────────────────────────

describe('null where there is no ordinal to name', () => {
  it('returns null for a parallel loop', () => {
    // The server resumes a fan-out by index SET, and its iterations
    // receive previous=None, so "resumes at #7" would assert an ordering
    // that does not exist.  Distinct from 0: the banner must fall back to
    // its loop-level wording, not claim a restart at the beginning.
    expect(prefix(
      mkRun(contiguous(19)), loopBlock({ repeat_parallel: true }),
    )).toBeNull();
  });

  it('returns null for a non-loop block', () => {
    const run = mkRun(contiguous(3));
    expect(prefix(run, {
      block_type: 'task', id: LOOP, name: 'A task',
      instructions: 'do it',
    } as Block)).toBeNull();
  });

  it('returns null when the first iteration itself failed', () => {
    // Prefix 0 means nothing is preserved, and "resumes at #0" would
    // dress a from-scratch loop re-run as a mid-loop resume.
    const run = mkRun([iter(0, { status: 'failed' }), iter(1)]);
    expect(prefix(run, loopBlock())).toBeNull();
  });

  it('returns null when the loop recorded no iterations', () => {
    expect(prefix(mkRun([]), loopBlock())).toBeNull();
  });

  it('returns null when the block has no state in this run', () => {
    const run = mkRun(contiguous(5));
    expect(prefix(run, loopBlock({ id: 'b-other' }))).toBeNull();
  });

  it('returns null for a missing run or block', () => {
    expect(prefix(null, loopBlock())).toBeNull();
    expect(prefix(undefined, loopBlock())).toBeNull();
    expect(prefix(mkRun(contiguous(3)), null)).toBeNull();
    expect(prefix(mkRun(contiguous(3)), undefined)).toBeNull();
  });
});

// ── the seam: computed, passed, and rendered ───────────────────────

/**
 * Static source assertions, following the convention of
 * recoveryBannerWiring.test.ts.  These carry the weight here: every unit
 * assertion above would pass in full while the value was computed and
 * never handed to the banner, or accepted by the banner and never
 * rendered — a defined-but-unused prop, which is the exact shape of the
 * defect this whole thread started from.
 */
describe('the predicted iteration reaches the screen', () => {
  const fs = require('fs');
  const path = require('path');
  const read = (f: string) =>
    fs.readFileSync(path.resolve(__dirname, '..', f), 'utf8');

  const TILE = read('TaskCardInlineTile.tsx');
  const BANNER = read('RunRecoveryBanner.tsx');

  it('recoveryTarget.ts exports the helper', () => {
    expect(read('recoveryTarget.ts')).toMatch(
      /export function bankedIterationPrefix/);
  });

  it('the tile computes it and passes it to the banner', () => {
    expect(TILE).toContain('bankedIterationPrefix');
    const at = TILE.indexOf('<RunRecoveryBanner');
    expect(at).toBeGreaterThan(-1);
    const el = TILE.slice(at, TILE.indexOf('/>', at) + 2);
    expect(el).toContain('resumeAtIteration');
  });

  it('the tile passes the resolved block, not the raw id', () => {
    // The helper needs the BLOCK to tell serial from parallel, and the
    // target may live in a callee tree — so it must be the block the
    // tile already resolved through findBlockInRun, or a hold inside a
    // called card silently loses its label.
    const at = TILE.indexOf('<RunRecoveryBanner');
    const before = TILE.slice(0, at);
    const call = before.lastIndexOf('bankedIterationPrefix');
    expect(call).toBeGreaterThan(-1);
    expect(before.slice(call, call + 60)).toMatch(/\brun\b/);
    // `b` is the findBlockInRun result the banner's own label already uses.
    expect(before).toMatch(/const b = displayCard/);
  });

  it('the banner declares the prop and renders the number', () => {
    expect(BANNER).toMatch(/resumeAtIteration\??:\s*number\s*\|\s*null/);
    // Rendered as #N to match the tile's existing 0-based convention.
    expect(BANNER).toMatch(/\{resumeAtIteration\}/);
  });

  it('the banner still resumes by BLOCK, not by iteration', () => {
    // The label is cosmetic; the request must stay on the block-level
    // route, which is where the serial prefix is derived server-side.
    // Calling the iteration endpoint from here would 422 on a parallel
    // loop and duplicate a decision the server already makes.
    expect(BANNER).toContain('onRetry(target.blockId)');
    expect(BANNER).not.toContain('resumeRunFromIteration');
  });
});
