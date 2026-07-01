/**
 * Task Card types — mirrors app/models/task_card.py.
 *
 * A TaskCard is a saveable, re-runnable tree of blocks.
 * See design/task-cards.md for the conceptual framing.
 */

// ── Scope ─────────────────────────────────────────────────

/**
 * One path-permission entry on a Task scope.
 *
 * - read:    advisory today; the model is told it may read this path.
 * - write:   gates file_write via task-scoped allowlist.
 * - context: file contents are preloaded into the system prompt.
 *            Only meaningful for files (is_dir=false); ignored for
 *            directory entries.
 */
export interface ScopeEntry {
  path: string;
  is_dir?: boolean;
  read?: boolean;
  write?: boolean;
  context?: boolean;
}

export interface TaskScope {
  paths: ScopeEntry[];
  cwd?: string | null;
  tools: string[];
  skills: string[];
  /**
   * Per-task shell command grants.  Each entry is either a literal
   * first-token match (e.g. "pytest" grants any pytest invocation)
   * or, with a "re:" prefix, a regex against the full command line.
   * Grant is additive: bypasses base shell allowlist and destructive
   * checks but never overrides ``always_blocked`` (sudo/vi/etc.) or
   * redirection blocking.  Empty/undefined = no extra grants.
   */
  shell_commands?: string[];
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
  // Optional error-identity hash for failure clustering.  Null on success.
  signature?: string | null;
  failed?: boolean;
}

// ── The recursive Block type ──────────────────────────────

// 'group' is a neutral run-once sequential container.  It carries no
// loop/trigger semantics and renders without visible chrome — it is the
// invisible card-root wrapper that lets a State precede a loop without
// entering the loop's scope.  Backend dispatches it to _execute_sequence.
export type BlockType = 'task' | 'repeat' | 'parallel' | 'until' | 'schedule' | 'state' | 'group';
export type RepeatMode = 'count' | 'until' | 'for_each';
export type PropagateMode = 'none' | 'last' | 'all';
export type UntilMode = 'model' | 'expression';
export type ScheduleMode = 'interval' | 'at' | 'daily_at' | 'cron';
export type IntervalUnit = 'minutes' | 'hours' | 'days';

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

  // Until-only
  until_mode?: UntilMode | null;
  until_condition?: string | null;
  until_max?: number | null;

  // Schedule-only (the "outer-outer" trigger decorator)
  schedule_mode?: ScheduleMode | null;
  schedule_interval_value?: number | null;
  schedule_interval_unit?: IntervalUnit | null;
  schedule_at_iso?: string | null;
  schedule_daily_at?: string | null;
  schedule_cron?: string | null;
  schedule_timezone?: string | null;
  schedule_enabled?: boolean;
  schedule_catch_up?: boolean;
  schedule_max_runs?: number | null;

  // State-only: read-only run-scoped variables (name -> literal).
  // Tasks read them via {{var.NAME}} templating; nothing writes back.
  // Placement is the reset policy — a State block inside a Repeat/Until
  // body re-applies its literals each iteration; at top level it sets
  // once per run.  See app/agents/block_executor.py::_execute_state.
  state_variables?: Record<string, unknown> | null;

  // State prose context — the PRIMARY, conversational form of a State
  // block.  Freeform English givens that flow into every in-scope
  // task's context automatically, no {{var}} templating required.
  // ``state_variables`` is the optional formal adjunct.  Same
  // placement-is-reset-policy.  See block_executor.py::_execute_state.
  state_context?: string | null;

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
