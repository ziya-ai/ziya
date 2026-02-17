/**
 * Project types
 */

export interface WritePolicy {
  safe_write_paths?: string[];
  allowed_write_patterns?: string[];
  allowed_interpreters?: string[];
  always_blocked?: string[];
}

export interface ProjectSettings {
  defaultContextIds: string[];
  defaultSkillIds: string[];
  writePolicy?: WritePolicy;
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
  settings?: ProjectSettings;
}

export interface ProjectListItem {
  id: string;
  name: string;
  path: string;
  lastAccessedAt: number;
  isCurrentWorkingDirectory: boolean;
}
