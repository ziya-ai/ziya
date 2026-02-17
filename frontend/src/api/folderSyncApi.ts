/**
 * Folder Sync API - syncs ConversationFolder to server-side ChatGroup storage.
 *
 * Server stores groups at /api/v1/projects/{projectId}/chat-groups.
 * Frontend stores folders in IndexedDB.
 * This module syncs between the two, same pattern as conversationSyncApi.
 */

import { ConversationFolder } from '../utils/types';

const BASE = '/api/v1/projects';

function projectHeaders(): Record<string, string> {
  const path = (window as any).__ZIYA_CURRENT_PROJECT_PATH__;
  return path ? { 'X-Project-Root': path } : {};
}

/**
 * Fetch all groups/folders from server for a project.
 */
export async function listServerFolders(projectId: string): Promise<ConversationFolder[]> {
  const res = await fetch(`${BASE}/${projectId}/chat-groups`, {
    headers: projectHeaders(),
  });
  if (!res.ok) {
    console.warn('Failed to list folders from server:', res.status);
    return [];
  }
  const groups = await res.json();

  // Map server ChatGroup shape → frontend ConversationFolder shape
  return groups.map((g: any) => ({
    id: g.id,
    name: g.name,
    projectId: g.projectId,
    parentId: g.parentId ?? null,
    useGlobalContext: g.useGlobalContext ?? true,
    useGlobalModel: g.useGlobalModel ?? true,
    createdAt: g.createdAt,
    updatedAt: g.updatedAt || g.createdAt,
    isGlobal: g.isGlobal,
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
  const res = await fetch(`${BASE}/${projectId}/chat-groups/bulk-sync`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...projectHeaders() },
    body: JSON.stringify({ groups: folders }),
  });
  if (!res.ok) {
    console.warn('Folder bulk sync failed:', res.status);
    return { created: 0, updated: 0, skipped: 0, errors: [{ id: 'bulk', error: `HTTP ${res.status}` }] };
  }
  return res.json();
}

/**
 * Delete a folder on the server.
 */
export async function deleteServerFolder(projectId: string, folderId: string): Promise<boolean> {
  const res = await fetch(`${BASE}/${projectId}/chat-groups/${folderId}`, {
    method: 'DELETE',
    headers: projectHeaders(),
  });
  return res.ok;
}
