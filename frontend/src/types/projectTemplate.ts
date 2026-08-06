/**
 * Project template types.
 *
 * A template is a SEED for ProjectSettings, applied once at project
 * creation.  It is deliberately NOT an inheritance layer: the resolved
 * values are stamped into the project record and every later settings read
 * stays literal.  `ProjectSettings.templateId` records which template did
 * the seeding, for provenance only.
 *
 * Mirrors app/utils/project_templates.py.
 */
import type { ProjectSettings } from './project';

/**
 * The subset of ProjectSettings a template may carry.
 *
 * Narrower than ProjectSettings on purpose — a template must not carry a
 * project's identity, and `defaultContextIds` is excluded from snapshots
 * because context ids are per-project record ids that would become
 * dangling references in any other project.
 */
export type TemplatableSettings = Partial<Pick<
  ProjectSettings,
  'defaultSkillIds' | 'writePolicy' | 'contextManagement' | 'taskScope'
>>;

export interface ProjectTemplate {
  id: string;
  name: string;
  description: string;
  /**
   * Filenames whose presence in a directory selects this template.
   * Empty means the template is never auto-detected and must be chosen
   * explicitly or set as the global default.
   */
  detectMarkers: string[];
  settings: TemplatableSettings;
  /** True for templates that ship with Ziya; these cannot be deleted. */
  isBuiltIn?: boolean;
}

/**
 * Templates and the default preference in one payload.
 *
 * Combined server-side because the create dialog needs both to render a
 * single line of UI, and two round trips would let it paint a stale
 * default.
 */
export interface TemplateListResponse {
  templates: ProjectTemplate[];
  defaultTemplateId: string | null;
}

/** Result of sniffing a directory, without creating anything. */
export interface DetectTemplateResponse {
  templateId: string;
  /**
   * The marker file that produced the match, so the UI can say
   * "detected from pyproject.toml" instead of asking the user to take the
   * detection on trust.  Null when nothing matched.
   */
  marker: string | null;
  detected: boolean;
}

export interface SnapshotTemplateRequest {
  id: string;
  name: string;
  description?: string;
  detectMarkers?: string[];
}
