/**
 * Folder Sync API - syncs ConversationFolder to server-side ChatGroup storage.
 *
 * Server stores groups at /api/v1/projects/{projectId}/chat-groups.
 * Frontend stores folders in IndexedDB.
 * This module syncs between the two, same pattern as conversationSyncApi.
 */

import { ConversationFolder } from '../utils/types';
import {
  timedFetchJson,
  timedFetchOk,
  SyncHttpError,
  LIST_TIMEOUT_MS,
  BULK_TIMEOUT_MS,
  MUTATE_TIMEOUT_MS,
} from './timedFetch';

const BASE = '/api/v1/projects';

function projectHeaders(): Record<string, string> {
  const path = (window as any).__ZIYA_CURRENT_PROJECT_PATH__;
  return path ? { 'X-Project-Root': path } : {};
}

/**
 * Fetch all groups/folders from server for a project.
 */
export async function listServerFolders(projectId: string): Promise<ConversationFolder[]> {
  // Awaited inside syncWithServer, so an unbounded hang here wedges the
  // whole sync cycle exactly as a hung listChats does.
  let groups: any[];
  try {
    groups = await timedFetchJson<any[]>(
      `${BASE}/${projectId}/chat-groups`,
      { headers: projectHeaders() },
      LIST_TIMEOUT_MS,
      'listServerFolders',
    );
  } catch (e) {
    if (e instanceof SyncHttpError) {
      console.warn('Failed to list folders from server:', e.status);
      return [];
    }
    throw e;
  }

  // Map server ChatGroup shape → frontend ConversationFolder shape
  return groups.map((g: any) => ({
    id: g.id,
    name: g.name,
    projectId: g.projectId,
    parentId: g.parentId ?? null,
    useGlobalContext: g.useGlobalContext ?? true,
    useGlobalModel: g.useGlobalModel ?? true,
    modelPreference: g.modelPreference ?? null,
    createdAt: g.createdAt,
    updatedAt: g.updatedAt || g.createdAt,
    isGlobal: g.isGlobal,
    taskPlan: g.taskPlan ?? null,
  }));
}

/**
 * Bulk-sync folders to server. Server uses version comparison
 * to decide whether to accept each one.
 */
export async function bulkSyncFolders(
  projectId: string,
  folders: ConversationFolder[]
): Promise<{ created: number; updated: number; skipped: number; errors: any[] }> {
  try {
    return await timedFetchJson<{ created: number; updated: number; skipped: number; errors: any[] }>(
      `${BASE}/${projectId}/chat-groups/bulk-sync`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...projectHeaders() },
        body: JSON.stringify({ groups: folders }),
      },
      BULK_TIMEOUT_MS,
      'bulkSyncFolders',
    );
  } catch (e) {
    if (e instanceof SyncHttpError) {
      console.warn('Folder bulk sync failed:', e.status);
      return { created: 0, updated: 0, skipped: 0, errors: [{ id: 'bulk', error: `HTTP ${e.status}` }] };
    }
    throw e;
  }
}

/**
 * Delete a folder on the server.
 */
export async function deleteServerFolder(projectId: string, folderId: string): Promise<boolean> {
  const res = await timedFetchOk(
    `${BASE}/${projectId}/chat-groups/${folderId}`,
    { method: 'DELETE', headers: projectHeaders() },
    MUTATE_TIMEOUT_MS,
    'deleteServerFolder',
  );
  return res.ok;
}

/**
 * Atomically set a folder's isGlobal flag on the server.
 *
 * Server is the single source of truth for the global flag.  Frontend
 * should call this rather than mutating the flag locally and waiting
 * for the bulk-sync debounce to round-trip — the dedicated endpoint
 * gives immediate, durable, race-free semantics.
 *
 * Returns the updated folder on success, null on failure.
 */
export async function setFolderGlobal(
  projectId: string,
  folderId: string,
  isGlobal: boolean
): Promise<ConversationFolder | null> {
  try {
    const g: any = await timedFetchJson<any>(
      `${BASE}/${encodeURIComponent(projectId)}/chat-groups/${encodeURIComponent(folderId)}/global`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...projectHeaders() },
        body: JSON.stringify({ isGlobal }),
      },
      MUTATE_TIMEOUT_MS,
      'setFolderGlobal',
    );
    return {
      id: g.id,
      name: g.name,
      projectId: g.projectId,
      parentId: g.parentId ?? null,
      useGlobalContext: g.useGlobalContext ?? true,
      useGlobalModel: g.useGlobalModel ?? true,
      modelPreference: g.modelPreference ?? null,
      createdAt: g.createdAt,
      updatedAt: g.updatedAt || g.createdAt,
      isGlobal: g.isGlobal,
      taskPlan: g.taskPlan ?? null,
    };
  } catch (e) {
    console.warn('📡 setFolderGlobal failed:', e);
    return null;
  }
}
