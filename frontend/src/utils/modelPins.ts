/**
 * modelPins — model pinning across conversation / folder / project
 * scopes, in two persistence layers.
 *
 * A "pin" routes chat requests to a specific model without touching the
 * server's global model (/api/set-model).  There are two layers:
 *
 *   • TAB pins  — the in-memory maps in this module.  Per-browser-tab,
 *     non-persisted, dropped on reload.  A temporary local override.
 *   • SAVED pins — a ``modelPreference`` string stored on the
 *     conversation / folder / project RECORD itself (synced to the
 *     server, survives restarts, shared across tabs).  This module does
 *     NOT hold saved pins; the caller passes them into resolveModelPin
 *     so this store stays pure and unit-testable.
 *
 * Resolution precedence (most specific wins):
 *
 *   conversation → folder → project → server default
 *
 * Within a level, a TAB pin overrides a SAVED pin (a temporary in-tab
 * override of the durable choice), and the resolved result flags which
 * layer won via ``persistent``.
 *
 * The store dispatches MODEL_PIN_CHANGED_EVENT on every mutation so UI
 * surfaces (the model display in FolderTree, etc.) can re-render.
 */

export type ModelPinScope = 'conversation' | 'folder' | 'project';

export interface ResolvedModelPin {
  model: string;
  scope: ModelPinScope;
  /** True when the winning pin is a saved record pref; false for a tab pin. */
  persistent: boolean;
}

/** Saved (record-level) model prefs, passed in by the caller. */
export interface PersistedModelPrefs {
  conversation?: string | null;
  folder?: string | null;
  project?: string | null;
}

export const MODEL_PIN_CHANGED_EVENT = 'modelPinChanged';

const conversationPins = new Map<string, string>();
const folderPins = new Map<string, string>();
const projectPins = new Map<string, string>();

function notify(): void {
  // Guard for non-browser (test) environments without a window.
  if (typeof window !== 'undefined' && typeof window.dispatchEvent === 'function') {
    window.dispatchEvent(new CustomEvent(MODEL_PIN_CHANGED_EVENT));
  }
}

export function setConversationModelPin(
  conversationId: string | null | undefined, model: string | null,
): void {
  if (!conversationId) return;
  if (model) conversationPins.set(conversationId, model);
  else conversationPins.delete(conversationId);
  notify();
}

export function getConversationModelPin(conversationId: string | null | undefined): string | null {
  if (!conversationId) return null;
  return conversationPins.get(conversationId) ?? null;
}

export function setFolderModelPin(
  folderId: string | null | undefined, model: string | null,
): void {
  if (!folderId) return;
  if (model) folderPins.set(folderId, model);
  else folderPins.delete(folderId);
  notify();
}

export function getFolderModelPin(folderId: string | null | undefined): string | null {
  if (!folderId) return null;
  return folderPins.get(folderId) ?? null;
}

export function setProjectModelPin(
  projectId: string | null | undefined, model: string | null,
): void {
  if (!projectId) return;
  if (model) projectPins.set(projectId, model);
  else projectPins.delete(projectId);
  notify();
}

export function getProjectModelPin(projectId: string | null | undefined): string | null {
  if (!projectId) return null;
  return projectPins.get(projectId) ?? null;
}

export interface ResolveModelPinArgs {
  conversationId?: string | null;
  folderId?: string | null;
  projectId?: string | null;
  /** Saved record prefs (modelPreference) for each level, if any. */
  persisted?: PersistedModelPrefs;
}

/**
 * Resolve the effective pin for a send.  Walks conversation → folder →
 * project; at each level a TAB pin wins over the SAVED pref, and the
 * first level with any pin wins overall.  null = no pin (server default).
 */
export function resolveModelPin(
  args: ResolveModelPinArgs,
): ResolvedModelPin | null {
  const { conversationId, folderId, projectId, persisted = {} } = args;
  const levels: Array<{ scope: ModelPinScope; tab: string | null; saved: string | null }> = [
    { scope: 'conversation', tab: getConversationModelPin(conversationId), saved: persisted.conversation ?? null },
    { scope: 'folder',       tab: getFolderModelPin(folderId),             saved: persisted.folder ?? null },
    { scope: 'project',      tab: getProjectModelPin(projectId),           saved: persisted.project ?? null },
  ];
  for (const { scope, tab, saved } of levels) {
    if (tab) return { model: tab, scope, persistent: false };
    if (saved) return { model: saved, scope, persistent: true };
  }
  return null;
}

/**
 * Clear all TAB pins covering the given context.  Used when the user
 * applies scope "Server default" (this context should follow the global
 * model again).  Saved prefs are cleared separately by the caller, which
 * owns the record-update path.
 */
export function clearContextModelPins(
  conversationId?: string | null,
  folderId?: string | null,
  projectId?: string | null,
): void {
  let changed = false;
  if (conversationId && conversationPins.delete(conversationId)) changed = true;
  if (folderId && folderPins.delete(folderId)) changed = true;
  if (projectId && projectPins.delete(projectId)) changed = true;
  if (changed) notify();
}

/** Test helper — reset all pin state. */
export function clearAllModelPins(): void {
  conversationPins.clear();
  folderPins.clear();
  projectPins.clear();
  notify();
}
