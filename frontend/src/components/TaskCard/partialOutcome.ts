/**
 * partialOutcome — pure derivations for the partial-run banner and the
 * attempt rail.
 *
 * Split out of TaskCardInlineTile because these answer the two
 * questions a user asks about a run that stopped partway, and both
 * answers must come from the durable record rather than from live
 * stream state that a reload would lose:
 *
 *   1. "How far did it get?"     -> stage counts from block_states
 *   2. "Did it change anything?" -> write/shell grants ∩ blocks that ran
 *
 * Mirrors app/utils/run_outcome.py.  Deliberately duplicated rather
 * than served from an endpoint: both sides need it (the server to
 * classify, the client to render) and the inputs are already on the run
 * record the tile has in hand, so a round trip would add latency for no
 * new information.
 */

import type { TaskRun, TaskRunBlockState } from '../../types/task_run';

/**
 * Wrapper blocks with no run-map row of their own — see
 * runMapModel.flattenBlocks, which renders groups chromeless.  Excluded
 * so an "N of M stages" figure matches the rows actually on screen; a
 * count that disagrees with what the user can see is worse than no
 * count at all.
 */
const INVISIBLE = ['group'];

/** Statuses meaning the block had its chance to touch the workspace. */
const RAN = ['done', 'failed', 'cancelled'];

export interface ProgressCounts {
  completed: number;
  total: number;
  failed: number;
  skipped: number;
  /**
   * Loop iterations that passed / failed, summed across every block.
   *
   * Load-bearing, not decoration.  ``_mark_block_status`` deliberately
   * does NOT persist a block's status while it is inside an active loop
   * iteration, so a Repeat/Until body's blocks keep the ``queued``
   * status they were seeded with and only the container reaches a
   * terminal one.  A five-iteration campaign that passed all five
   * therefore has ZERO structural blocks at ``done`` — the successes
   * exist only in ``iteration_summaries``.
   *
   * Counting stages alone produced the reported contradiction: the
   * banner said "0 of 5 stages completed" beside a dot strip reading
   * "5 passed".  Mirrors ``run_outcome.summarize_progress``, which has
   * always computed these but had no reader.
   */
  passedIterations: number;
  failedIterations: number;
}

export function progressCounts(run: TaskRun | null | undefined): ProgressCounts {
  const out: ProgressCounts = {
    completed: 0, total: 0, failed: 0, skipped: 0,
    passedIterations: 0, failedIterations: 0,
  };
  if (!run?.block_states) return out;
  for (const st of Object.values(run.block_states)) {
    // Iterations are counted for EVERY block, including invisible
    // wrappers: an iteration is work that happened regardless of
    // whether its owner has a row on screen.
    for (const s of st.iteration_summaries ?? []) {
      if (s.status === 'passed') out.passedIterations += 1;
      else if (s.status === 'failed') out.failedIterations += 1;
    }
    if (INVISIBLE.includes(st.block_type)) continue;
    out.total += 1;
    if (st.status === 'done') out.completed += 1;
    else if (st.status === 'failed') out.failed += 1;
    else if (st.status === 'skipped') out.skipped += 1;
  }
  return out;
}

/**
 * One phrase describing how far a run got, in the units it actually
 * made progress in.
 *
 * Reports iterations ALONGSIDE stages rather than instead of them,
 * because on a loop card the two answer different questions and the
 * stage figure alone is actively misleading: "0 of 5 stages" is
 * literally true of a run whose five iterations all passed, and reads
 * as "nothing happened".
 *
 * Returns '' when there is nothing to report, so callers can omit the
 * clause entirely rather than printing an empty count.
 */
export function progressPhrase(p: ProgressCounts): string {
  const parts: string[] = [];
  if (p.total > 0) {
    parts.push(`${p.completed} of ${p.total} stages completed`);
  }
  if (p.passedIterations > 0) {
    const n = p.passedIterations;
    parts.push(`${n} loop iteration${n === 1 ? '' : 's'} passed`);
  }
  return parts.join(', ');
}

/**
 * The block whose failure ended the run, if identifiable.
 *
 * Earliest by completion time: under on_failure="stop" the first
 * failure is the one that ended the run, and later ``failed`` entries
 * are containers propagating it upward.  Naming it in the banner spares
 * the user hunting the run map for a red row.
 */
export function firstFailedBlock(
  run: TaskRun | null | undefined,
): TaskRunBlockState | null {
  if (!run?.block_states) return null;
  const failed = Object.values(run.block_states)
    .filter(s => s.status === 'failed');
  if (failed.length === 0) return null;
  return failed.slice().sort(
    (a, b) => (a.completed_at ?? Infinity) - (b.completed_at ?? Infinity),
  )[0];
}

export interface SideEffect {
  blockId: string;
  blockName: string;
  status: string;
  hadWriteGrant: boolean;
  files: string[];
}

/**
 * Blocks that ran AND held a grant able to change the workspace.
 *
 * Reports CAPABILITY, not just declared files: a block holding a write
 * grant may have written files it never declared via emit_artifact, so
 * an empty ``files`` list is not evidence that nothing changed.  Saying
 * so plainly is the point — an over-confident "no changes" would be
 * worse than the flat red status this replaces.
 *
 * A failed block is included: it may have written before it crashed, and
 * excluding it would understate the hazard.  A queued block is not — it
 * never had the chance.
 */
export function sideEffects(run: TaskRun | null | undefined): SideEffect[] {
  const scopes = run?.permissions_snapshot?.block_scopes ?? {};
  const out: SideEffect[] = [];
  for (const [blockId, st] of Object.entries(run?.block_states ?? {})) {
    if (!RAN.includes(st.status)) continue;
    const scope = scopes[blockId] ?? {};
    const hadWriteGrant = Boolean(
      (scope.shell_commands?.length ?? 0) > 0
      // A file-task callee's grants are fnmatch globs with no `paths`
      // entries at all, so omitting this made the one shape that reaches
      // the workspace purely through a glob report no hazard.
      || (scope.write_patterns?.length ?? 0) > 0
      || (scope.paths ?? []).some(p => p?.write),
    );
    const files = (st.artifact?.outputs ?? [])
      .filter((p: any) => p?.part_type === 'file')
      .map((p: any) => String(p.file_uri ?? p.name ?? ''))
      .filter(Boolean);
    if (!hadWriteGrant && files.length === 0) continue;
    out.push({
      blockId,
      blockName: scope.block_name || st.block_type,
      status: st.status,
      hadWriteGrant,
      files,
    });
  }
  return out;
}

/**
 * One line summarising the workspace hazard, or null when there is
 * none to report.
 *
 * Distinguishes "wrote these files" from "could have written" because
 * the two warrant different amounts of alarm, and conflating them would
 * either cry wolf or under-warn.
 */
export function sideEffectSummary(
  run: TaskRun | null | undefined,
): string | null {
  const effects = sideEffects(run);
  if (effects.length === 0) return null;
  const fileCount = new Set(effects.flatMap(e => e.files)).size;
  const blocks = effects.length;
  const stagePlural = blocks === 1 ? 'stage' : 'stages';
  if (fileCount > 0) {
    return `${blocks} ${stagePlural} held write access and declared `
      + `${fileCount} changed file${fileCount === 1 ? '' : 's'}.`;
  }
  return `${blocks} ${stagePlural} held write or shell access, so files `
    + `may have changed without being declared.`;
}

/** Human label for how an attempt came to exist. */
export function resumeKindLabel(run: TaskRun): string {
  switch (run.resume_kind) {
    case 'retry_from': return 'manual retry';
    case 'continue_from': return 'manual continue';
    case 'rerun': return 'rerun';
    // Absent on pre-lineage records, which were all initial launches.
    default: return 'initial run';
  }
}

/**
 * One-line outcome for an attempt-rail row.
 *
 * Says both how far it got AND what became of it, because "partial" on
 * its own is exactly the ambiguity this change exists to remove.
 */
export function attemptSummary(run: TaskRun): string {
  const p = progressCounts(run);
  // Iteration progress is included for the same reason the banner
  // includes it: on a loop card the stage figure alone reads as "no
  // progress" for a run that completed every iteration.
  const stages = p.total > 0
    ? `${p.completed} of ${p.total} stages`
      + (p.passedIterations > 0 ? `, ${p.passedIterations} iterations` : '')
    : (p.passedIterations > 0 ? `${p.passedIterations} iterations` : '');
  if (run.status === 'partial') {
    return stages ? `partial — ${stages}` : 'partial';
  }
  if (run.status === 'done') {
    return stages ? `completed all ${p.total} stages` : 'completed';
  }
  return stages ? `${run.status} — ${stages}` : String(run.status);
}

/** True when the tile should show the amber partial banner. */
export function isPartial(run: TaskRun | null | undefined): boolean {
  return run?.status === 'partial';
}

/**
 * Where each stage's result came from, for a resumed attempt.
 *
 * This is the answer to "is prior state preserved? I think it is?" —
 * it IS preserved (the resume gate replays every completed block), and
 * this makes that answerable by looking rather than by reading source.
 *
 * ``replayed`` counts blocks this run did not execute: the gate marks
 * them ``skipped`` with the source run's artifact attached, which is
 * precisely "inherited from an earlier attempt".
 */
export interface ProvenanceCounts {
  replayed: number;
  executed: number;
  /** The block the user pointed at, if this run is a resume. */
  resumedFromBlockId: string | null;
  kind: TaskRun['resume_kind'] | null;
}

export function provenance(
  run: TaskRun | null | undefined,
): ProvenanceCounts | null {
  if (!run?.block_states) return null;
  // An initial launch has no provenance story to tell — everything ran
  // here — so returning null keeps the block off the common case.
  const kind = run.resume_kind ?? null;
  if (!kind || kind === 'initial') return null;
  let replayed = 0;
  let executed = 0;
  for (const st of Object.values(run.block_states)) {
    if (INVISIBLE.includes(st.block_type)) continue;
    if (st.status === 'skipped' && st.artifact) replayed += 1;
    else if (RAN.includes(st.status)) executed += 1;
  }
  return {
    replayed,
    executed,
    resumedFromBlockId: run.resumed_from_block_id ?? null,
    kind,
  };
}

/**
 * Where a single focused block's output CAME FROM, in time.
 *
 * The reported confusion — "I can click around a whole bunch and not be
 * able to tell what is from the past run or the current run" — has one
 * root cause: the detail panel shows STATE without WHEN.  On a resumed
 * attempt a replayed block carries the PRIOR attempt's artifact under
 * the status ``skipped``, which reads as self-contradictory ("skipped,
 * yet here is its output") and gives no hint the output predates this
 * run.
 *
 * ``replayed`` is inferred exactly as ``provenance`` does it — status
 * ``skipped`` WITH an artifact — because that is the shape
 * ``block_executor._skip_on_resume`` writes.  A skipped block with no
 * artifact is a genuine never-ran (on_failure=stop), not a replay.
 */
export interface BlockOrigin {
  /** True when this block's output was replayed from an earlier attempt. */
  replayed: boolean;
  /** Attempt ordinal of the run being viewed (1 for a first launch). */
  attempt: number;
  /** Epoch seconds the block finished, when recorded. */
  completedAt: number | null;
  /**
   * Status to DISPLAY.  Replayed blocks are relabelled: 'skipped' is
   * true of this run's executor but actively misleading to a reader,
   * who wants to know the stage has a result and where it came from.
   */
  displayStatus: string;
}

export function blockOrigin(
  run: TaskRun | null | undefined,
  state: TaskRunBlockState | null | undefined,
  liveStatus: string,
): BlockOrigin {
  const attempt = run?.attempt ?? 1;
  const replayed = !!state && state.status === 'skipped' && !!state.artifact;
  return {
    replayed,
    attempt,
    completedAt: state?.completed_at ?? null,
    displayStatus: replayed ? 'replayed' : liveStatus,
  };
}

/**
 * Human "when" for a block's recorded output.
 *
 * Absolute clock time, not a relative age: the whole point is telling a
 * replayed result apart from a fresh one, and "2 minutes ago" is the
 * same phrase for both when a resume lands quickly.
 */
export function formatCompletedAt(
  tsSeconds: number | null | undefined,
): string | null {
  if (tsSeconds == null) return null;
  try {
    const d = new Date(tsSeconds * 1000);
    if (Number.isNaN(d.getTime())) return null;
    return d.toLocaleTimeString();
  } catch {
    return null;
  }
}
