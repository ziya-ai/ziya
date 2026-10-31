/**
 * Client for the managed-job routes (app/api/jobs.py).
 *
 * The sidebar reads job SUMMARIES from the run-status index; this is the
 * open conversation's fuller view: the records with command and cwd, one
 * job's journal tail, and the one write — kill.
 */
import type { JobSummary } from './taskRunApi';

export interface JobRecord extends JobSummary {
  command?: string;
  cwd?: string;
  pid?: number;
  conversation_id?: string | null;
  owner?: Record<string, string>;
}

export interface JobDetail extends JobRecord {
  /** Last ``tail_lines`` output lines of the journal. */
  tail: string;
  declared_outputs: string[];
  outputs: string[];
  outputs_missing?: string[] | null;
  max_runtime_s?: number;
  keep_awake?: string | null;
  obs_id?: string | null;
}

function projectHeaders(): Record<string, string> {
  const path = (window as any).__ZIYA_CURRENT_PROJECT_PATH__;
  return path ? { 'X-Project-Root': path } : {};
}

const base = (projectId: string) =>
  `/api/v1/projects/${encodeURIComponent(projectId)}/jobs`;

/** Every job of one conversation, newest first.  Empty on a server that
 *  predates the route, so the rail degrades to its task chips. */
export async function listConversationJobs(
  projectId: string, conversationId: string,
): Promise<JobRecord[]> {
  const url = `${base(projectId)}?conversation_id=${encodeURIComponent(conversationId)}`;
  const res = await fetch(url, { headers: projectHeaders() });
  if (res.status === 404) return [];
  if (!res.ok) throw new Error(`listConversationJobs failed: ${res.status}`);
  return res.json();
}

export async function getJob(
  projectId: string, jobId: string, tailLines = 30,
): Promise<JobDetail> {
  const res = await fetch(`${base(projectId)}/${encodeURIComponent(jobId)}?tail_lines=${tailLines}`, {
    headers: projectHeaders(),
  });
  if (!res.ok) throw new Error(`getJob failed: ${res.status}`);
  return res.json();
}

/** TERM the job's process group, KILL after the grace period. */
export async function killJob(projectId: string, jobId: string): Promise<JobRecord & { killed: boolean }> {
  const res = await fetch(`${base(projectId)}/${encodeURIComponent(jobId)}/kill`, {
    method: 'POST', headers: projectHeaders(),
  });
  if (!res.ok) throw new Error(`killJob failed: ${res.status}`);
  return res.json();
}
