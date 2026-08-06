/**
 * Task Run types — mirrors app/models/task_run.py.
 *
 * A TaskRun is one execution of a TaskCard's block tree.  Its
 * iteration_summaries carry lightweight per-iteration records; full
 * artifacts are loaded on demand via the /iterations/{block_id}/{index}
 * endpoint (see design/task-cards.md §Queryable runs).
 */

import type { Artifact, Block } from './task_card';

/**
 * ``partial`` = stopped after real progress (≥1 block done AND work
 * left unfinished).  Derived server-side at the terminal write, so the
 * executor still only ever reports failed/cancelled.  Rendered amber
 * with an explicit "N of M stages" figure and a side-effect warning,
 * because a partial run may have changed the workspace.
 */
export type RunStatus =
  | 'queued' | 'running' | 'paused'
  // 'held' — stopped by an infrastructure fault (expired credentials,
  // lost endpoint, exhausted throttling retries) rather than by the
  // card's own work.  Terminal for the run object (the executor has
  // unwound) but continuable: the run records held_at_block_id, and
  // resume-from-block replays completed blocks rather than re-running
  // them.  Kept distinct from 'failed' because the two ask for
  // different responses — fix the infrastructure, not the card.
  | 'done' | 'partial' | 'failed' | 'cancelled' | 'held';

/** How a run came to exist; drives the attempt-rail badges. */
export type ResumeKind = 'initial' | 'retry_from' | 'continue_from' | 'rerun';

/**
 * Per-block lifecycle status — RunStatus plus 'skipped' (a sibling
 * that never ran because of on_failure="stop").  Drives the run map.
 */
export type BlockStatus = RunStatus | 'skipped';
export type IterationStatus = 'passed' | 'failed' | 'cancelled';

/**
 * The two keys of a permissions-snapshot block scope the UI actually
 * reads, for answering "could this block have changed my workspace?".
 *
 * Deliberately NOT a full mirror of app/utils/permissions_snapshot.py:
 * that schema is documented as opaque and free to evolve without a
 * migration, so typing all of it here would create a second definition
 * to keep in sync for no benefit.  Unknown keys stay unmodelled.
 */
export interface PermissionScopeSummary {
  block_name?: string;
  block_type?: string;
  shell_commands?: string[];
  paths?: Array<{ path?: string; write?: boolean }>;
  /** fnmatch globs granted to a file-task callee, which has no `paths`
   * at all — an independent write signal, not a kind of path. */
  write_patterns?: string[];
  /** Set when this block entered the run through a Call block. */
  via_call?: { call_block_id?: string; target?: string; kind?: string };
}

/** A Call block's target as resolved at run time. */
export interface CallSnapshot {
  target?: string;
  kind?: string;
  key?: string;
  /** The callee's block tree — the run map splices this in beneath the
   * call row, since it is in neither the card nor `card_snapshot`. */
  root?: Block;
}

export interface PermissionsSnapshot {
  schema_version?: number;
  project_root?: string | null;
  block_scopes?: Record<string, PermissionScopeSummary>;
}

export interface IterationSummary {
  index: number;
  status: IterationStatus;
  signature?: string | null;
  duration_ms: number;
  tokens: number;
  has_artifact: boolean;
}

/**
 * One entry in a run's progress trail — mirrors app/models/task_run.py.
 *
 * ``source === 'model'`` marks a model-authored ``<progress note=.../>``
 * tag, which is semantically richer than a tool-derived line and is
 * therefore shown with emphasis rather than being averaged in.
 */
export interface ProgressNote {
  note: string;
  at: number;
  source?: string | null;
}

export interface TaskRunBlockState {
  block_id: string;
  block_type: string;
  status: BlockStatus;
  started_at?: number | null;
  completed_at?: number | null;
  artifact?: Artifact | null;
  error?: string | null;
  iteration_summaries: IterationSummary[];
}

export interface TaskRun {
  id: string;
  card_id: string;
  source_conversation_id?: string | null;
  status: RunStatus;
  /** Fault kind, e.g. 'authentication_error'.  Set only when status==='held'. */
  held_reason?: string | null;
  /** Block the run stopped at; the natural resume target for a held run. */
  held_at_block_id?: string | null;
  started_at?: number | null;
  completed_at?: number | null;
  error?: string | null;
  cancel_requested: boolean;
  /** Soft-pause flag; executor holds at the next boundary when set. */
  pause_requested: boolean;
  /**
   * Unspent step-debug credits.  The step endpoint grants these and
   * leaves ``pause_requested`` set, so the executor crosses exactly
   * this many boundaries and then holds again.
   *
   * Optional only so callers constructing partial TaskRun fixtures
   * don't have to name it; the server always sends it (the Pydantic
   * field defaults to 0, so even a run record written before the field
   * existed materializes it on load).
   */
  step_budget?: number;
  /**
   * Launch-time variable overrides, persisted so the run is
   * reproducible from its own record and so a resume can carry them
   * forward instead of falling back to authored baselines.
   */
  parameter_overrides?: Record<string, unknown>;
  artifact?: Artifact | null;
  block_states: Record<string, TaskRunBlockState>;
  total_tokens: number;
  total_tool_calls: number;
  /** Heartbeat: wall-clock seconds of most recent executor activity. */
  last_activity_at?: number | null;
  /** Short server-derived line describing the latest activity. */
  progress_note?: string | null;
  /**
   * Bounded trail of progress notes, oldest first.  ``progress_note``
   * above is a single slot destroyed on every update, so a finished run
   * could report what it was doing LAST but never what it had been
   * doing — the whole-run view carried no progress narrative at all.
   * Absent on runs written before the field existed.
   */
  progress_notes?: ProgressNote[] | null;
  /**
   * Effective permissions captured at launch.  The tile intersects this
   * with which blocks actually ran to warn that a partial run may have
   * changed the workspace — the first question a user asks about one.
   * Absent on runs whose capture failed (it is non-fatal at launch).
   */
  permissions_snapshot?: PermissionsSnapshot | null;
  /**
   * Snapshot of the card definition (name/description/root) captured at
   * launch, so later edits to the card don't retroactively rewrite what
   * this run is shown to have executed.  Absent on runs created before
   * snapshotting was added — fall back to the live card in that case.
   */
  card_snapshot?: { name: string; description: string; root: Block } | null;
  /**
   * Resolved Call targets, keyed by the CALL block's id.  A call names
   * its target and resolves it server-side at run time, so without this
   * the map can only draw the call row and the callee's blocks — which
   * do stream status and persist in block_states — have no tree to sit in.
   */
  call_snapshots?: Record<string, CallSnapshot>;
  /**
   * Attempt lineage.  ``root_run_id`` is the lineage key shared by every
   * attempt, so a chain is one filter rather than a pointer walk.  The
   * tile collapses a lineage to a single threaded tile showing the
   * newest attempt, with the rest on an attempt rail — which is what
   * makes "was prior state preserved?" answerable by looking.
   *
   * All optional: runs written before lineage tracking have none, and
   * are treated as ``attempt: 1, resume_kind: 'initial'``.
   */
  root_run_id?: string | null;
  parent_run_id?: string | null;
  attempt?: number;
  resume_kind?: ResumeKind | null;
  resumed_from_block_id?: string | null;
  created_at: number;
  updated_at: number;
}

export interface IterationsQuery {
  block_id?: string;
  status?: IterationStatus;
  signature?: string;
  limit?: number;
  offset?: number;
  include_artifact?: boolean;
}

export interface IterationsResponse {
  total: number;
  limit: number;
  offset: number;
  items: Array<{
    block_id: string;
    summary: IterationSummary;
    artifact?: Artifact | null;
  }>;
}
