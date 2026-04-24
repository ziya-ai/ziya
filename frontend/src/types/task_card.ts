/**
 * Task Card types — mirrors app/models/task_card.py.
 *
 * A TaskCard is a saveable, re-runnable tree of blocks.
 * See design/task-cards.md for the conceptual framing.
 */

// ── Scope ─────────────────────────────────────────────────

export interface TaskScope {
  files: string[];
  tools: string[];
  skills: string[];
}

// ── Artifacts (for runtime display, not editing) ──────────

export type ArtifactPartType = 'text' | 'file' | 'data';

export interface ArtifactPart {
  part_type: ArtifactPartType;
  text?: string | null;
  file_uri?: string | null;
  media_type?: string | null;
  data?: Record<string, unknown> | null;
}

export interface Artifact {
  summary: string;
  decisions: string[];
  outputs: ArtifactPart[];
  tokens: number;
  tool_calls: number;
  duration_ms: number;
  created_at: number;
}

// ── The recursive Block type ──────────────────────────────

export type BlockType = 'task' | 'repeat' | 'parallel';
export type RepeatMode = 'count' | 'until' | 'for_each';
export type PropagateMode = 'none' | 'last' | 'all';

export interface Block {
  block_type: BlockType;
  id: string;
  name: string;

  // Task-only
  instructions?: string | null;
  scope?: TaskScope | null;
  emoji?: string | null;

  // Repeat-only
  repeat_mode?: RepeatMode | null;
  repeat_count?: number | null;
  repeat_max?: number | null;
  repeat_parallel?: boolean;
  repeat_propagate?: PropagateMode;
  repeat_until?: string | null;
  repeat_for_each_source?: string | null;
  repeat_item_template?: string | null;

  // Body (Task ignores this)
  body: Block[];
}

// ── Task Card ─────────────────────────────────────────────

export interface TaskCard {
  id: string;
  name: string;
  description: string;
  root: Block;
  tags: string[];
  is_template: boolean;
  source: string;
  created_at: number;
  updated_at: number;
  last_run_at?: number | null;
  run_count: number;
}

export interface TaskCardCreate {
  name: string;
  description?: string;
  root: Block;
  tags?: string[];
  is_template?: boolean;
}

export interface TaskCardUpdate {
  name?: string;
  description?: string;
  root?: Block;
  tags?: string[];
  is_template?: boolean;
}
