/**
 * Conversation Sync API - bridges frontend conversations to server-side storage.
 * 
 * Server stores chats at /api/v1/projects/{projectId}/chats.
 * Frontend stores conversations in IndexedDB.
 * This module syncs between the two.
 */

export interface ServerChat {
  id: string;
  title: string;
  groupId?: string | null;
  contextIds?: string[];
  skillIds?: string[];
  additionalFiles?: string[];
  additionalPrompt?: string | null;
  messages: any[];
  createdAt: number;
  isGlobal?: boolean;
  lastActiveAt: number;
  // Frontend-preserved fields
  projectId?: string;
  isActive?: boolean;
  folderId?: string | null;
  hasUnreadResponse?: boolean;
  displayMode?: string;
  lastAccessedAt?: number | null;
  [key: string]: any;  // Extra fields preserved by server
}

export interface BulkSyncResult {
  created: number;
  updated: number;
  skipped: number;
  errors: Array<{ id: string; error: string }>;
}

const BASE = '/api/v1/projects';

/**
 * Get project-scoping header for server-side request isolation.
 */
function projectHeaders(): Record<string, string> {
  const path = (window as any).__ZIYA_CURRENT_PROJECT_PATH__;
  return path ? { 'X-Project-Root': path } : {};
}

export async function listChats(projectId: string, includeMessages = false): Promise<ServerChat[]> {
  const res = await fetch(`${BASE}/${projectId}/chats?include_messages=${includeMessages}`, {
    headers: projectHeaders(),
  });
  if (!res.ok) {
    console.debug('Failed to list chats from server:', res.status);
    return [];
  }
  return res.json();
}

export async function getChat(projectId: string, chatId: string): Promise<ServerChat | null> {
  const res = await fetch(`${BASE}/${projectId}/chats/${chatId}`, {
    headers: projectHeaders(),
  });
  if (!res.ok) return null;
  return res.json();
}

export async function bulkSync(projectId: string, chats: ServerChat[]): Promise<BulkSyncResult> {
  const res = await fetch(`${BASE}/${projectId}/chats/bulk-sync`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...projectHeaders() },
    body: JSON.stringify({ chats }),
  });
  if (!res.ok) {
    console.error('Bulk sync failed:', res.status);
    return { created: 0, updated: 0, skipped: 0, errors: [{ id: 'bulk', error: `HTTP ${res.status}` }] };
  }
  return res.json();
}

/**
 * Delete a chat from server-side storage.
 * Returns true if deleted (or already gone), false on unexpected error.
 */
export async function deleteChat(projectId: string, chatId: string): Promise<boolean> {
  const res = await fetch(`${BASE}/${projectId}/chats/${chatId}`, {
    method: 'DELETE',
    headers: projectHeaders(),
  });
  // 404 is fine — already deleted by another instance
  return res.ok || res.status === 404;
}

/**
 * Convert a frontend Conversation to a ServerChat for syncing.
 */
export function conversationToServerChat(conv: any, projectId: string): ServerChat {
  return {
    ...conv,
    projectId,
    lastActiveAt: conv.lastAccessedAt || conv.lastActiveAt || Date.now(),
    createdAt: conv.createdAt || conv.lastAccessedAt || Date.now(),
    messages: (conv.messages || []).map((m: any) => ({
      ...m,
      id: m.id || `msg-${Date.now()}-${Math.random().toString(36).slice(2)}`,
      timestamp: m._timestamp || m.timestamp || Date.now(),
    })),
  };
}
