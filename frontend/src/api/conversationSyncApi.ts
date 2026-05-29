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
  // Defense-in-depth: never push shells.  Shells have messages stripped
  // to first+last (or blanked content) for sidebar memory reasons.
  // Pushing them to the server truncates the authoritative record —
  // this caused the April-2026 chat-history loss.
  const _filtered: ServerChat[] = [];
  let _dropped = 0;
  for (const c of chats) {
    const anyC = c as any;
    if (anyC?._isShell) { _dropped++; continue; }
    if (typeof anyC?._fullMessageCount === 'number'
      && Array.isArray(anyC.messages)
      && anyC.messages.length < anyC._fullMessageCount) {
      _dropped++;
      continue;
    }
    _filtered.push(c);
  }
  if (_dropped > 0) {
    console.warn(`⚠️ bulkSync: dropped ${_dropped} shell/partial chats to protect server records`);
  }
  chats = _filtered;
  if (chats.length === 0) return { created: 0, updated: 0, skipped: 0, errors: [] };

  // Chunk large payloads to avoid 413 Request Entity Too Large.
  // With 500+ conversations carrying full message bodies, a single
  // POST can easily exceed the server's 20MB request limit.
  const CHUNK_SIZE = 50;
  if (chats.length > CHUNK_SIZE) {
    const aggregate: BulkSyncResult = { created: 0, updated: 0, skipped: 0, errors: [] };
    for (let i = 0; i < chats.length; i += CHUNK_SIZE) {
      const chunk = chats.slice(i, i + CHUNK_SIZE);
      const result = await bulkSync(projectId, chunk);
      aggregate.created += result.created;
      aggregate.updated += result.updated;
      aggregate.skipped += result.skipped;
      aggregate.errors.push(...result.errors);
    }
    return aggregate;
  }

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

/**
 * Atomically set a chat's isGlobal flag on the server.
 *
 * Server is the single source of truth for the global flag.  Frontend
 * should call this rather than mutating the flag locally and waiting
 * for the bulk-sync debounce to round-trip — the dedicated endpoint
 * gives immediate, durable, race-free semantics.  The next periodic
 * sync mirrors the on-disk state into IDB.
 *
 * Returns the updated chat on success, null on failure.
 */
export async function setChatGlobal(
  projectId: string,
  chatId: string,
  isGlobal: boolean
): Promise<ServerChat | null> {
  try {
    const headers: Record<string, string> = { 'Content-Type': 'application/json', ...projectHeaders() };
    const res = await fetch(
      `${BASE}/${encodeURIComponent(projectId)}/chats/${encodeURIComponent(chatId)}/global`,
      { method: 'POST', headers, body: JSON.stringify({ isGlobal }) }
    );
    if (!res.ok) {
      console.warn(`📡 setChatGlobal: ${res.status} ${res.statusText}`);
      return null;
    }
    return await res.json();
  } catch (e) {
    console.warn('📡 setChatGlobal failed:', e);
    return null;
  }
}

/**
 * Fetch many chats in a single request.
 *
 * Per-request /chats/{id} fetches under high parallelism are an order
 * of magnitude slower than isolated fetches due to server-side lock
 * contention.  This endpoint bundles N reads into one call, paying
 * the per-request overhead once.
 *
 * Returns {chats, missing} on success, null on network failure.
 * Caller is responsible for chunking large id lists if needed.
 */
export async function bulkGetChats(
  projectId: string,
  ids: string[]
): Promise<{ chats: ServerChat[]; missing: string[] } | null> {
  if (ids.length === 0) return { chats: [], missing: [] };
  try {
    const headers: Record<string, string> = { 'Content-Type': 'application/json', ...projectHeaders() };
    const res = await fetch(
      `${BASE}/${encodeURIComponent(projectId)}/chats/bulk-get`,
      { method: 'POST', headers, body: JSON.stringify({ ids }) }
    );
    if (!res.ok) {
      console.warn(`📡 bulkGetChats: ${res.status} ${res.statusText}`);
      return null;
    }
    return await res.json();
  } catch (e) {
    console.warn('📡 bulkGetChats failed:', e);
    return null;
  }
}
