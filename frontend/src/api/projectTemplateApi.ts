/**
 * Project template API client.
 *
 * Every call here is best-effort from the caller's point of view: templates
 * are a convenience layer over project creation, so a failure must degrade
 * to "no template" rather than blocking the user from making a project.
 * `detectTemplateSafe` encodes that explicitly; the raw calls throw and let
 * callers that DO want the error (the settings surfaces) see it.
 */
import { api } from './index';
import type {
  DetectTemplateResponse, ProjectTemplate, SnapshotTemplateRequest,
  TemplateListResponse,
} from '../types/projectTemplate';

/** The template used when nothing is detected and no default is set. */
export const GENERAL_TEMPLATE_ID = 'general';

export async function listTemplates(): Promise<TemplateListResponse> {
  return api.get<TemplateListResponse>('/projects/templates');
}

export async function detectTemplate(path: string): Promise<DetectTemplateResponse> {
  return api.get<DetectTemplateResponse>(
    `/projects/templates/detect?path=${encodeURIComponent(path)}`,
  );
}

/**
 * Detection that never throws.
 *
 * The create dialog calls this on every keystroke of the path field, where
 * a transient failure (or a path the user is still halfway through typing)
 * must not surface an error toast.  Falls back to the same answer the
 * server would give for an unrecognised directory.
 */
export async function detectTemplateSafe(
  path: string,
): Promise<DetectTemplateResponse> {
  if (!path || !path.trim()) {
    return { templateId: GENERAL_TEMPLATE_ID, marker: null, detected: false };
  }
  try {
    return await detectTemplate(path.trim());
  } catch {
    return { templateId: GENERAL_TEMPLATE_ID, marker: null, detected: false };
  }
}

/** Set (or, with null, clear) the default template for new projects. */
export async function setDefaultTemplate(
  templateId: string | null,
): Promise<TemplateListResponse> {
  return api.put<TemplateListResponse>('/projects/templates/default', { templateId });
}

export async function deleteTemplate(templateId: string): Promise<void> {
  await api.delete(`/projects/templates/${encodeURIComponent(templateId)}`);
}

/** Snapshot an existing project's settings as a reusable template. */
export async function saveProjectAsTemplate(
  projectId: string, data: SnapshotTemplateRequest,
): Promise<ProjectTemplate> {
  return api.post<ProjectTemplate>(
    `/projects/${projectId}/save-as-template`, data,
  );
}

/**
 * Human-readable provenance for the create dialog's one line of UI.
 *
 * Says WHY a template was chosen, because a silent detection that changes
 * a project's default skills should be visible rather than spooky.
 */
export function describeTemplateChoice(
  detection: DetectTemplateResponse | null,
  defaultTemplateId: string | null,
): string {
  if (detection?.detected && detection.marker) {
    return `detected from ${detection.marker}`;
  }
  if (defaultTemplateId) return 'your default';
  return 'no template';
}

/**
 * Derive a template id from a human-typed name.
 *
 * The id is a storage key, not a display string: it must survive being a
 * JSON object key and a URL path segment, so the snapshot dialog derives it
 * rather than asking the user for a second field they would have to
 * understand the constraints of.  Collisions are the server's business —
 * it rejects a duplicate or built-in id with a 400.
 */
export function slugifyTemplateId(name: string): string {
  return (name || '')
    .trim()
    .toLowerCase()
    // Any run of non-alphanumerics becomes a single underscore, so
    // "Deno + Fresh (v2)" and "Deno  Fresh  v2" converge on one id
    // instead of producing two entries that look identical in the list.
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '');
}

/**
 * True when a name yields an id the server will accept at all.
 *
 * A name of only punctuation slugs to the empty string, which would POST an
 * id of "" and fail server-side validation — worth catching in the dialog
 * so the user sees why rather than getting a bare 400.
 */
export function isUsableTemplateName(name: string): boolean {
  return slugifyTemplateId(name).length > 0;
}
