/**
 * Task Run types — mirrors app/models/task_run.py.
 *
 * A TaskRun is one execution of a TaskCard's block tree.  Its
 * iteration_summaries carry lightweight per-iteration records; full
 * artifacts are loaded on demand via the /iterations/{block_id}/{index}
 * endpoint (see design/task-cards.md §Queryable runs).
 */

import type { Artifact } from './task_card';

export type RunStatus = 'queued' | 'running' | 'done' | 'failed' | 'cancelled';
export type IterationStatus = 'passed' | 'failed' | 'cancelled';

export interface IterationSummary {
  index: number;
  status: IterationStatus;
  signature?: string | null;
  duration_ms: number;
  tokens: number;
  has_artifact: boolean;
}

export interface TaskRunBlockState {
  block_id: string;
  block_type: string;
  status: RunStatus;
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
  started_at?: number | null;
  completed_at?: number | null;
  error?: string | null;
  cancel_requested: boolean;
  artifact?: Artifact | null;
  block_states: Record<string, TaskRunBlockState>;
  total_tokens: number;
  total_tool_calls: number;
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
