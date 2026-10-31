/**
 * Post-launch sync for DelegateLaunchButton.
 *
 * After launching a TaskPlan, the sidebar needs the chats the launch created.
 * The launch response already names them (orchestrator_id plus one id per
 * delegate in conversation_ids), so only those are fetched.  Listing the
 * project with include_messages=true for this pulls every message of every
 * chat in the project into the tab: hundreds of MB and tens of seconds on a
 * large project.
 */
import * as syncApi from '../api/conversationSyncApi';

export interface LaunchDelegatesResult {
  orchestrator_id?: string | null;
  conversation_ids?: Record<string, string> | null;
}

/** Every chat id the launch created: the orchestrator plus each delegate. */
export function launchedChatIds(result: LaunchDelegatesResult): string[] {
  const ids = [result.orchestrator_id, ...Object.values(result.conversation_ids || {})];
  return Array.from(new Set(ids.filter((id): id is string => typeof id === 'string' && id.length > 0)));
}

/**
 * Fetch the launched chats that are not already local, shaped for IndexedDB
 * and React state.  A failed fetch yields [] so the periodic server sync can
 * pick them up instead.
 */
export async function fetchLaunchedChats(
  projectId: string,
  result: LaunchDelegatesResult,
  existingIds: Set<string>,
) {
  const wanted = launchedChatIds(result).filter(id => !existingIds.has(id));
  if (wanted.length === 0) return [];
  const resp = await syncApi.bulkGetChats(projectId, wanted);
  if (!resp) return [];
  return resp.chats.map(sc => ({
    id: sc.id,
    title: sc.title || 'Untitled',
    messages: sc.messages || [],
    // Preserve the server's authoritative timestamp.  Falling back to
    // Date.now() set lastAccessedAt > server lastActiveAt at insert time,
    // which made every empty delegate chat look "newer locally" on the next
    // sync cycle.
    lastAccessedAt: sc.lastActiveAt || sc.createdAt || 0,
    isActive: true,
    projectId,
    folderId: sc.groupId || sc.folderId || null,
    delegateMeta: sc.delegateMeta || null,
    hasUnreadResponse: false,
  }));
}
