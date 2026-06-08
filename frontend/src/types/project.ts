/**
 * Project types
 */

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
}

export interface ProjectSettings {
  defaultContextIds: string[];
  defaultSkillIds: string[];
  writePolicy?: WritePolicy;
  contextManagement?: ContextManagementSettings;
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
