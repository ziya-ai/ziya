/**
 * Task Run API client.
 * Paths match app/api/task_runs.py and the launch path in
 * app/api/task_cards.py.
 */

import type { Artifact } from '../types/task_card';
import type { TaskBinding } from '../types/task_binding';
import type {
  TaskRun, IterationsQuery, IterationsResponse, CalleeContext,
} from '../types/task_run';

/**
 * Per-request project-root header.  Mirrors the convention used by
 * chatApi / FolderContext / api/index.ts: every endpoint that may
 * spawn server-side work reading or writing files MUST send this so
 * ProjectContextMiddleware can set the request-scoped ContextVar.
 * Without it, server-side code falls through to ``os.getcwd()``.
 */
function projectHeaders(): Record<string, string> {
  const path = (window as any).__ZIYA_CURRENT_PROJECT_PATH__;
  return path ? { 'X-Project-Root': path } : {};
}

const runsBase = (projectId: string) =>
  `/api/v1/projects/${encodeURIComponent(projectId)}/task-runs`;

const cardsBase = (projectId: string) =>
  `/api/v1/projects/${encodeURIComponent(projectId)}/task-cards`;

export async function launchTaskCard(
  projectId: string, cardId: string,
  opts?: { source_conversation_id?: string },
): Promise<TaskRun> {
  const res = await fetch(
    `${cardsBase(projectId)}/${encodeURIComponent(cardId)}/launch`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...projectHeaders() },
      body: JSON.stringify({
        source_conversation_id: opts?.source_conversation_id ?? null,
        parameter_overrides: {},
      }),
    },
  );
  if (!res.ok) throw new Error(`launchTaskCard ${cardId} failed: ${res.status}`);
  return res.json();
}

export async function listTaskRuns(
  projectId: string, opts?: { cardId?: string },
): Promise<TaskRun[]> {
  const url = new URL(runsBase(projectId), window.location.origin);
  if (opts?.cardId) url.searchParams.set('card_id', opts.cardId);
  const res = await fetch(url.toString(), { headers: projectHeaders() });
  if (!res.ok) throw new Error(`listTaskRuns failed: ${res.status}`);
  return res.json();
}

/** Per-conversation run-status counts for the whole project. */
export interface RunStatusIndex {
  /** conversation id -> { status: lineage count } */
  conversations: Record<string, Record<string, number>>;
  /** True while something can still change on its own. */
  live: boolean;
  /** When the server's memo was last rebuilt; for debugging staleness. */
  built_at: number;
}

/**
 * Project-wide run status, for conversations that are NOT open.
 *
 * Distinct from listTaskRuns: that returns whole run records (block
 * states, iteration summaries, artifacts) which are large and encrypted at
 * rest, so polling it to learn status strings costs work proportional to
 * total run history.  This returns only the counts the sidebar renders.
 *
 * Returns an empty index on 404 so a sidebar still renders against a
 * server that predates this route.
 */
export async function getRunStatusIndex(
  projectId: string,
): Promise<RunStatusIndex> {
  const res = await fetch(`${runsBase(projectId)}/status-index`, {
    headers: projectHeaders(),
  });
  if (res.status === 404) {
    return { conversations: {}, live: false, built_at: 0 };
  }
  if (!res.ok) throw new Error(`getRunStatusIndex failed: ${res.status}`);
  return res.json();
}

/**
 * Runs that invoked this card as a Call target and are still live or held.
 *
 * Distinct from listTaskRuns({cardId}), which filters on the run's OWNER.
 * A Call runs inline in the caller's run, so a card used only as a callee
 * (CL1 inside CL0) owns no runs and looked idle even while it was the card
 * holding a study.  This asks the other question: who is running me?
 *
 * Callers MUST gate per-block markup on `held_in_callee`.  A hold in a
 * sibling callee is returned as context — this card did take part in a
 * held run — but drawing it on this card's blocks would point at a card
 * that is fine.
 */
export async function getCalleeContext(
  projectId: string, cardId: string,
): Promise<CalleeContext[]> {
  const res = await fetch(
    `${runsBase(projectId)}/callee-context/${encodeURIComponent(cardId)}`,
    { headers: projectHeaders() },
  );
  // A deck page must render with or without this: it is supplementary
  // context, not the card itself.  An older server has no such route.
  if (res.status === 404) return [];
  if (!res.ok) throw new Error(`getCalleeContext failed: ${res.status}`);
  return res.json();
}

export async function getTaskRun(
  projectId: string, runId: string,
): Promise<TaskRun> {
  const res = await fetch(`${runsBase(projectId)}/${encodeURIComponent(runId)}`, { headers: projectHeaders() });
  if (!res.ok) throw new Error(`getTaskRun ${runId} failed: ${res.status}`);
  return res.json();
}

export async function cancelTaskRun(
  projectId: string, runId: string,
): Promise<TaskRun> {
  const res = await fetch(
    `${runsBase(projectId)}/${encodeURIComponent(runId)}/cancel`,
    { method: 'POST', headers: projectHeaders() },
  );
  if (!res.ok) throw new Error(`cancelTaskRun ${runId} failed: ${res.status}`);
  return res.json();
}

export async function pauseTaskRun(
  projectId: string, runId: string,
): Promise<TaskRun> {
  const res = await fetch(
    `${runsBase(projectId)}/${encodeURIComponent(runId)}/pause`,
    { method: 'POST', headers: projectHeaders() },
  );
  if (!res.ok) throw new Error(`pauseTaskRun ${runId} failed: ${res.status}`);
  return res.json();
}

export async function resumeTaskRun(
  projectId: string, runId: string,
): Promise<TaskRun> {
  const res = await fetch(
    `${runsBase(projectId)}/${encodeURIComponent(runId)}/resume`,
    { method: 'POST', headers: projectHeaders() },
  );
  if (!res.ok) throw new Error(`resumeTaskRun ${runId} failed: ${res.status}`);
  return res.json();
}

/**
 * Advance a held run by ``count`` block boundaries, then hold again.
 *
 * Differs from resume in that ``pause_requested`` stays set, so the
 * run does not run to completion.  Credits accumulate, so calling this
 * repeatedly queues that many boundary crossings.  Granularity is a
 * block: stepping past a Task runs that entire Task including all its
 * LLM iterations and tool calls.
 *
 * A step on a run that is merely *running* (not yet paused) is
 * meaningful rather than a no-op — the server sets the pause flag too,
 * so the run advances to its next boundary and holds there.
 */
export async function stepTaskRun(
  projectId: string, runId: string, count = 1,
): Promise<TaskRun> {
  const url = new URL(
    `${runsBase(projectId)}/${encodeURIComponent(runId)}/step`,
    window.location.origin,
  );
  url.searchParams.set('count', String(count));
  const res = await fetch(url.toString(), {
    method: 'POST', headers: projectHeaders(),
  });
  if (!res.ok) throw new Error(`stepTaskRun ${runId} failed: ${res.status}`);
  return res.json();
}

/** Server response for a resume-from-block request. */
export interface ResumeFromResult {
  /** The NEW run.  The source run is left untouched as a record. */
  run: TaskRun;
  /**
   * Binding created for the new run, so a tile renders for it.  Null
   * when the source run had no chat to bind to — the run still executes,
   * it just has no anchor in any conversation.
   */
  binding: TaskBinding | null;
}

/**
 * ``retry`` re-executes the named block; ``continue`` accepts its
 * recorded outcome and starts at the block after it.  Both replay every
 * earlier block from record, so prior deck state is preserved either
 * way — the only difference is whether the named block runs again.
 */
export type ResumeMode = 'retry' | 'continue';

/**
 * Re-run a finished run from ``blockId`` onward, replaying the earlier
 * blocks' recorded artifacts instead of re-executing them.
 *
 * Creates a NEW run; the source run stays an immutable record.  The
 * server resolves the target — a block inside a loop body normalizes up
 * to the outermost enclosing loop, because only structural blocks have
 * persisted per-block state — so the resolved id may differ from
 * ``blockId``.  Compare ``result.run`` against what you asked for if
 * that matters to the caller.
 *
 * Errors: 404 unknown run or block not in the run's snapshot; 409 the
 * source run is still running/paused; 422 either the run predates
 * card_snapshot, or ``continue`` was asked for on the last block (there
 * is nothing to continue to, and launching anyway would execute
 * nothing).
 */
export async function resumeRunFromBlock(
  projectId: string, runId: string, blockId: string,
  mode: ResumeMode = 'retry',
): Promise<ResumeFromResult> {
  const res = await fetch(
    `${runsBase(projectId)}/${encodeURIComponent(runId)}` +
    `/resume-from/${encodeURIComponent(blockId)}` +
    `?mode=${encodeURIComponent(mode)}`,
    { method: 'POST', headers: projectHeaders() },
  );
  if (!res.ok) {
    // The status codes carry distinct, actionable meanings here, so
    // surface the server's own detail rather than a bare code.
    const detail = await res.json().then(b => b?.detail).catch(() => null);
    throw new Error(
      `resumeRunFromBlock ${runId} failed: ${res.status}` +
      (detail ? ` — ${detail}` : ''),
    );
  }
  return res.json();
}

/**
 * ``retry_iteration`` re-runs the named iteration; ``continue_iteration``
 * accepts its recorded result and runs the next one.  Iterations before
 * the resume point replay from record, so the first executed iteration
 * still receives its ``{{previous}}`` / ``{{all}}`` bindings.
 */
export type IterationResumeMode = 'retry_iteration' | 'continue_iteration';

/**
 * Resume a finished run from a point INSIDE a loop.
 *
 * Distinct endpoint rather than a mode on ``resumeRunFromBlock`` because
 * the resume point is an iteration INDEX, which has no block id to name:
 * a loop's iterations share one ``block_states`` entry and are recorded
 * only as ``iteration_summaries``.
 *
 * Errors worth surfacing verbatim — each names an actionable refusal:
 * 404 unknown run, or the block has no recorded state in this run; 409
 * the run is still live; 422 either the run predates ``card_snapshot``,
 * the block is not a loop, the loop is PARALLEL (its iterations do not
 * depend on each other, so there is no ordering to resume into), or the
 * predecessor's full artifact was dropped past the 50-pass retention cap
 * (so ``{{previous}}`` would replay empty).
 */
export async function resumeRunFromIteration(
  projectId: string, runId: string, blockId: string, index: number,
  mode: IterationResumeMode = 'retry_iteration',
): Promise<ResumeFromResult> {
  const res = await fetch(
    `${runsBase(projectId)}/${encodeURIComponent(runId)}` +
    `/resume-iteration/${encodeURIComponent(blockId)}/${index}` +
    `?mode=${encodeURIComponent(mode)}`,
    { method: 'POST', headers: projectHeaders() },
  );
  if (!res.ok) {
    const detail = await res.json().then(b => b?.detail).catch(() => null);
    throw new Error(
      `resumeRunFromIteration ${runId}#${index} failed: ${res.status}` +
      (detail ? ` — ${detail}` : ''),
    );
  }
  return res.json();
}

export async function deleteTaskRun(
  projectId: string, runId: string,
): Promise<void> {
  const res = await fetch(
    `${runsBase(projectId)}/${encodeURIComponent(runId)}`,
    { method: 'DELETE', headers: projectHeaders() },
  );
  if (!res.ok && res.status !== 404) {
    throw new Error(`deleteTaskRun ${runId} failed: ${res.status}`);
  }
}

/**
 * Every attempt in a run's lineage, oldest first.
 *
 * The tile collapses a lineage to one threaded tile showing the newest
 * attempt; this feeds the attempt rail that lists the rest.  Always
 * contains at least the requested run.
 */
export async function getRunLineage(
  projectId: string, runId: string,
): Promise<TaskRun[]> {
  const res = await fetch(
    `${runsBase(projectId)}/${encodeURIComponent(runId)}/lineage`,
    { headers: projectHeaders() },
  );
  if (!res.ok) {
    throw new Error(`getRunLineage ${runId} failed: ${res.status}`);
  }
  return res.json();
}

export async function listIterations(
  projectId: string, runId: string, q: IterationsQuery = {},
): Promise<IterationsResponse> {
  const url = new URL(
    `${runsBase(projectId)}/${encodeURIComponent(runId)}/iterations`,
    window.location.origin,
  );
  if (q.block_id) url.searchParams.set('block_id', q.block_id);
  if (q.status) url.searchParams.set('status', q.status);
  if (q.signature) url.searchParams.set('signature', q.signature);
  if (q.limit != null) url.searchParams.set('limit', String(q.limit));
  if (q.offset != null) url.searchParams.set('offset', String(q.offset));
  if (q.include_artifact) url.searchParams.set('include', 'artifact');
  const res = await fetch(url.toString(), { headers: projectHeaders() });
  if (!res.ok) throw new Error(`listIterations ${runId} failed: ${res.status}`);
  return res.json();
}

export async function getIterationArtifact(
  projectId: string, runId: string, blockId: string, index: number,
): Promise<Artifact> {
  const res = await fetch(
    `${runsBase(projectId)}/${encodeURIComponent(runId)}` +
    `/iterations/${encodeURIComponent(blockId)}/${index}`,
    { headers: projectHeaders() },
  );
  if (!res.ok) throw new Error(`getIterationArtifact failed: ${res.status}`);
  return res.json();
}
