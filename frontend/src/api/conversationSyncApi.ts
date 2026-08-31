/**
 * Conversation Sync API - bridges frontend conversations to server-side storage.
 * 
 * Server stores chats at /api/v1/projects/{projectId}/chats.
 * Frontend stores conversations in IndexedDB.
 * This module syncs between the two.
 */

import {
  timedFetchJson,
  timedFetchOk,
  SyncHttpError,
  LIST_TIMEOUT_MS,
  SINGLE_TIMEOUT_MS,
  BULK_TIMEOUT_MS,
  MUTATE_TIMEOUT_MS,
} from './timedFetch';

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
  // Derived open-work counts from the server summary path (always present, 0+).
  openBeadCount?: number;
  openWorkItemCount?: number;
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

export interface SearchChatsOpts {
  allProjects?: boolean;
  caseSensitive?: boolean;
  maxSnippetLength?: number;
  sort?: 'relevance' | 'newest' | 'oldest';
}

/**
 * Server-side conversation search. Scans chat files on the server one at a
 * time and returns SearchResult-shaped objects, avoiding loading every
 * conversation's full message bodies into the browser just to substring-scan.
 * allProjects=false searches strictly the given project.
 * sort selects ordering: weighted relevance score, or last activity asc/desc.
 * Returns null on transport failure so callers can fall back to a local scan.
 */
export async function searchChats(
  projectId: string,
  query: string,
  opts: SearchChatsOpts = {}
): Promise<any[] | null> {
  const params = new URLSearchParams({
    q: query,
    all_projects: String(!!opts.allProjects),
    case_sensitive: String(!!opts.caseSensitive),
    max_snippet_length: String(opts.maxSnippetLength ?? 150),
    sort: opts.sort ?? 'relevance',
  });
  try {
    const url = `${BASE}/${projectId}/chats/search?${params.toString()}`;
    return await timedFetchJson<any[]>(url, { headers: projectHeaders() },
      LIST_TIMEOUT_MS, 'searchChats');
  } catch (e) {
    // Unchanged contract: every failure (status, transport, deadline)
    // degrades to the local scan.
    console.debug('Server search failed, will fall back to local:', e);
    return null;
  }
}

export async function listChats(projectId: string, includeMessages = false): Promise<ServerChat[]> {
  try {
    return await timedFetchJson<ServerChat[]>(
      `${BASE}/${projectId}/chats?include_messages=${includeMessages}`,
      { headers: projectHeaders() },
      LIST_TIMEOUT_MS,
      'listChats',
    );
  } catch (e) {
    if (e instanceof SyncHttpError) {
      console.debug('Failed to list chats from server:', e.status);
      return [];
    }
    // Transport failure or our own deadline: THROW rather than return [].
    // An empty array is not a safe stand-in for "we could not ask" — the
    // caller's deletion pass treats any previously-seen conversation absent
    // from this list as deleted elsewhere, so a silent [] would stage the
    // whole project for local removal.  Throwing routes to syncWithServer's
    // catch, which abandons the cycle and still runs its finally.
    throw e;
  }
}

export async function getChat(projectId: string, chatId: string): Promise<ServerChat | null> {
  try {
    return await timedFetchJson<ServerChat>(
      `${BASE}/${projectId}/chats/${chatId}`,
      { headers: projectHeaders() },
      SINGLE_TIMEOUT_MS,
      'getChat',
    );
  } catch (e) {
    // null means "the server has no such chat".  A deadline breach is not
    // that: the post-sync rehydrate would read it as authoritative absence
    // for a conversation the user is currently looking at.
    if (e instanceof SyncHttpError) return null;
    throw e;
  }
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

  try {
    return await timedFetchJson<BulkSyncResult>(
      `${BASE}/${projectId}/chats/bulk-sync`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...projectHeaders() },
        body: JSON.stringify({ chats }),
      },
      BULK_TIMEOUT_MS,
      'bulkSync',
    );
  } catch (e) {
    if (e instanceof SyncHttpError) {
      console.error('Bulk sync failed:', e.status);
      return { created: 0, updated: 0, skipped: 0, errors: [{ id: 'bulk', error: `HTTP ${e.status}` }] };
    }
    throw e;
  }
}

/**
 * Delete a chat from server-side storage.
 * Returns true if deleted (or already gone), false on unexpected error.
 */
export async function deleteChat(projectId: string, chatId: string): Promise<boolean> {
  const res = await timedFetchOk(
    `${BASE}/${projectId}/chats/${chatId}`,
    { method: 'DELETE', headers: projectHeaders() },
    MUTATE_TIMEOUT_MS,
    'deleteChat',
  );
  // 404 is fine — already deleted by another instance
  return res.ok || res.status === 404;
}

/**
 * Convert a frontend Conversation to a ServerChat for syncing.
 */
export function conversationToServerChat(conv: any, projectId: string): ServerChat {
  // folderId is the frontend's single source of truth for folder
  // membership; groupId is only the server's storage name for the same
  // concept.  A conversation object can carry a STALE groupId left over
  // from an earlier server read — folder moves patch folderId only and
  // never update groupId.  Spreading ...conv would then push the stale
  // groupId alongside the new folderId; the server prefers the explicit
  // incoming groupId (chats.py bulk-sync guard) and read-back resolves
  // `groupId || folderId`, so the conversation snaps back to its old
  // folder.  Force groupId to mirror folderId here so both sides stay
  // consistent and any pre-existing divergence self-heals on next push.
  //
  // The absent-vs-null distinction is load-bearing: an explicit move to
  // ROOT sets folderId===null (present), which must win over a stale
  // groupId — a `??` chain would wrongly fall through to groupId and
  // snap the conversation back.  Only fall back to groupId when folderId
  // is genuinely absent (undefined), e.g. a server-hydrated record that
  // carries only groupId and hasn't been mapped to folderId yet.
  const resolvedFolderId =
    conv.folderId !== undefined ? conv.folderId : (conv.groupId ?? null);
  // Preserve the TRUE owner of a chat rather than re-stamping it with the
  // project currently being viewed.  Global chats from other projects are
  // surfaced into this project's sidebar and hydrated into IndexedDB carrying
  // their real owner's projectId (see syncMerge).  Forcing `projectId` to the
  // viewing project here made the next bulk-sync clone the chat into the
  // viewed project's dir and re-stamp it as local — producing cross-project
  // duplicates with divergent groupId/isGlobal (a chat that "reappeared under
  // the wrong global group" / "went missing" in its home project).  Only fall
  // back to the viewing project for a genuinely new local chat that has no
  // owner yet.
  const ownerProjectId = conv.projectId || projectId;
  return {
    ...conv,
    projectId: ownerProjectId,
    folderId: resolvedFolderId,
    groupId: resolvedFolderId,
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
    return await timedFetchJson<ServerChat>(
      `${BASE}/${encodeURIComponent(projectId)}/chats/${encodeURIComponent(chatId)}/global`,
      { method: 'POST', headers, body: JSON.stringify({ isGlobal }) },
      MUTATE_TIMEOUT_MS,
      'setChatGlobal',
    );
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
    return await timedFetchJson<{ chats: ServerChat[]; missing: string[] }>(
      `${BASE}/${encodeURIComponent(projectId)}/chats/bulk-get`,
      { method: 'POST', headers, body: JSON.stringify({ ids }) },
      BULK_TIMEOUT_MS,
      'bulkGetChats',
    );
  } catch (e) {
    console.warn('📡 bulkGetChats failed:', e);
    return null;
  }
}
