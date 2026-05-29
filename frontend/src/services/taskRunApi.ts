/**
 * Task Run API client.
 * Paths match app/api/task_runs.py and the launch path in
 * app/api/task_cards.py.
 */

import type { Artifact } from '../types/task_card';
import type {
  TaskRun, IterationsQuery, IterationsResponse,
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
