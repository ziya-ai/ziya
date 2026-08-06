/**
 * Project types
 */
import type { TaskScope } from './task_card';

export interface WritePolicy {
  /** "none" = safe paths + patterns only, "new_files" = also create new files anywhere, "all_files" = write any project file */
  direct_write_mode?: 'none' | 'new_files' | 'all_files';
  safe_write_paths?: string[];
  allowed_write_patterns?: string[];
  allowed_interpreters?: string[];
  always_blocked?: string[];
}

export interface ContextManagementSettings {
  /** Automatically add files referenced in diffs to the active context */
  auto_add_diff_files?: boolean;
  /** Per-file token cap for auto-added files (0 = no limit). Default 35000. */
  auto_add_token_limit?: number;
}

export interface ProjectSettings {
  defaultContextIds: string[];
  defaultSkillIds: string[];
  writePolicy?: WritePolicy;
  contextManagement?: ContextManagementSettings;
  // Which template seeded these settings. Provenance only — templates are
  // apply-once, so this is never resolved when reading settings. Use it to
  // explain WHY a default skill is on, not to compute what is on.
  templateId?: string;
  // Saved project-wide model pin (alias string).  The outermost model
  // scope: conversations/folders with no more-specific pin inherit it.
  // Absent = follow the server default.
  modelPreference?: string | null;
  // Deck-level (project-wide) Task Card permissions baseline.  Merged
  // additively with each card's own scope and every ancestor block's
  // scope (see app/models/task_card.py::merge_scopes and
  // app/agents/block_executor.py) — this is the outermost layer.
  taskScope?: TaskScope | null;
}

export interface Project {
  id: string;
  name: string;
  path: string;
  createdAt: number;
  lastAccessedAt: number;
  settings: ProjectSettings;
}

export interface ProjectCreate {
  path?: string;
  name?: string;
  /**
   * Explicit template choice.  Omit to let the server autodetect from the
   * directory's build markers, then fall back to the user's default.
   */
  templateId?: string;
}

export interface ProjectUpdate {
  name?: string;
  path?: string;
  settings?: ProjectSettings;
}

export interface ProjectListItem {
  id: string;
  name: string;
  path: string;
  lastAccessedAt: number;
  isCurrentWorkingDirectory: boolean;
  conversationCount: number;
}

export interface StartupInfo {
  /** Absolute path the server was started in (or --root/--directory). */
  root: string;
  /** True when --root/--directory was passed explicitly on the command line. */
  explicit: boolean;
  hasAnyProjects: boolean;
  /** Project already rooted at `root`, if one exists (null otherwise). */
  rootProject: Project | null;
}
