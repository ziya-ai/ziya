/**
 * TypeScript types for delegate orchestration.
 *
 * Mirrors: app/models/delegate.py
 * Used by: MUIChatHistory (sidebar status), future delegate API
 */

export interface FileChange {
  path: string;
  action: string;  // 'created' | 'modified' | 'deleted'
  line_delta: string;
}

export interface MemoryCrystal {
  delegate_id: string;
  task: string;
  summary: string;
  files_changed: FileChange[];
  decisions: string[];
  exports: Record<string, string>;
  tool_stats: Record<string, number>;
  original_tokens: number;
  crystal_tokens: number;
  created_at: number;
  retroactive_review?: string | null;
}

export interface DelegateSpec {
  delegate_id: string;
  conversation_id?: string | null;
  name: string;
  emoji: string;
  scope: string;
  files: string[];
  dependencies: string[];
  skill_id?: string | null;
  color: string;
}

export type DelegateStatus =
  | 'proposed' | 'ready' | 'running'
  | 'compacting' | 'crystal' | 'failed'
  | 'interrupted' | 'blocked';

export interface DelegateMeta {
  role: 'orchestrator' | 'delegate';
  plan_id: string;
  delegate_id?: string | null;
  delegate_spec?: DelegateSpec | null;
  status: DelegateStatus;
  crystal?: MemoryCrystal | null;
  context_id?: string | null;
  skill_id?: string | null;
}

export interface TaskPlan {
  name: string;
  description: string;
  orchestrator_id?: string | null;
  source_conversation_id?: string | null;
  parent_plan_id?: string | null;
  parent_delegate_id?: string | null;
  delegate_specs: DelegateSpec[];
  crystals: MemoryCrystal[];
  status: string;  // 'planning' | 'running' | 'completed' | 'completed_partial' | 'cancelled'
  task_graph?: Record<string, any> | null;
  task_list?: SwarmTask[];
  created_at: number;
  completed_at?: number | null;
}

export interface SwarmTask {
  task_id: string;
  title: string;
  status: string;  // 'open' | 'claimed' | 'done' | 'blocked'
  claimed_by?: string | null;
  added_by: string;
  summary?: string | null;
  created_at: number;
  completed_at?: number | null;
  tags: string[];
}

/** Helper to check if a DelegateMeta represents a completed delegate */
export function isDelegateCrystal(meta: DelegateMeta | undefined | null): boolean {
  return meta?.role === 'delegate' && meta?.status === 'crystal';
}
