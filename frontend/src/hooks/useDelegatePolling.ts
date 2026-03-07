/**
 * useDelegatePolling — T28: Live delegate status updates.
 *
 * When active TaskPlan folders exist, polls the lightweight
 * /delegate-status endpoint every 3s. When delegate statuses
 * change (crystal formed, failed, etc.), updates conversation
 * state and broadcasts to other tabs via BroadcastChannel.
 *
 * Stops polling when all TaskPlans are terminal (completed/cancelled)
 * or when no TaskPlan folders exist.
 */

import { useEffect, useRef, useCallback } from 'react';
import type { Conversation, ConversationFolder } from '../utils/types';
import { projectSync } from '../utils/projectSync';
import * as syncApi from '../api/conversationSyncApi';

const POLL_INTERVAL_MS = 3_000;

// After this many consecutive 404s from delegate-status, treat the plan as
// gone and mark the folder completed so we stop polling it.
const MAX_CONSECUTIVE_404 = 3;

interface DelegateStatusEntry {
  status: string;
  has_crystal: boolean;
}

interface DelegateStatusResponse {
  plan_id: string;
  name: string;
  status: string;
  delegates: Record<string, DelegateStatusEntry>;
  running_count: number;
  crystal_count: number;
  total_delegates: number;
}

function getActiveTaskPlanFolders(folders: ConversationFolder[]): ConversationFolder[] {
  return folders.filter(
    f => f.taskPlan && f.taskPlan.status !== 'completed' && f.taskPlan.status !== 'cancelled'
  );
}

export function useDelegatePolling(
  projectId: string | undefined,
  folders: ConversationFolder[],
  setConversations: React.Dispatch<React.SetStateAction<Conversation[]>>,
  setFolders: React.Dispatch<React.SetStateAction<ConversationFolder[]>>,
): void {
  // Track previous delegate statuses to detect changes.
  // Keyed by plan_id → delegate_id → status string.
  const prevStatusRef = useRef<Record<string, Record<string, string>>>({});

  // Snapshot folders into a ref so the interval callback doesn't
  // create a new closure (and new interval) every time folders change.
  const foldersRef = useRef(folders);
  foldersRef.current = folders;
  
  // Track consecutive 404 responses per folder id for GC.
  const notFoundCountRef = useRef<Record<string, number>>({});

  const projectIdRef = useRef(projectId);
  projectIdRef.current = projectId;

  const poll = useCallback(async () => {
    const pid = projectIdRef.current;
    if (!pid) return;

    const activeFolders = getActiveTaskPlanFolders(foldersRef.current);
    if (activeFolders.length === 0) return;

    for (const folder of activeFolders) {
      try {
        const res = await fetch(
          `/api/v1/projects/${pid}/groups/${folder.id}/delegate-status`
        );
        if (!res.ok) {
          if (res.status === 404) {
            const count = (notFoundCountRef.current[folder.id] || 0) + 1;
            notFoundCountRef.current[folder.id] = count;
            if (count >= MAX_CONSECUTIVE_404) {
              // Server no longer knows about this plan — GC the folder locally.
              console.warn(
                `Delegate plan for folder ${folder.id} returned 404 ${count}x — marking completed (GC).`
              );
              setFolders(prev => prev.map(f =>
                f.id === folder.id && f.taskPlan
                  ? { ...f, taskPlan: { ...f.taskPlan, status: 'completed' } }
                  : f
              ));
              delete notFoundCountRef.current[folder.id];
            }
          }
          continue;
        }
        // Successful response — reset the 404 counter for this folder.
        notFoundCountRef.current[folder.id] = 0;
        const data: DelegateStatusResponse = await res.json();

        // Diff against previous snapshot
        const prevStatuses = prevStatusRef.current[data.plan_id] || {};
        let changed = false;
        for (const [did, info] of Object.entries(data.delegates)) {
          if (prevStatuses[did] !== info.status) {
            changed = true;
            break;
          }
        }
        if (!changed && prevStatuses._planStatus === data.status) continue;

        // Store new snapshot
        prevStatusRef.current[data.plan_id] = {
          ...Object.fromEntries(
            Object.entries(data.delegates).map(([did, info]) => [did, info.status])
          ),
          _planStatus: data.status,
        };

        // Merge delegate status changes into conversations
        setConversations(prev => prev.map(c => {
          if (!c.delegateMeta || c.delegateMeta.plan_id !== data.plan_id) return c;
          const did = c.delegateMeta.delegate_id;
          if (!did || !data.delegates[did]) return c;
          const newStatus = data.delegates[did].status;
          if (c.delegateMeta.status === newStatus) return c;
          const updated: any = { ...c, delegateMeta: { ...c.delegateMeta, status: newStatus as any } };
          // Surface interrupted/failed delegates so the user notices they need action
          if (newStatus === 'interrupted' || newStatus === 'failed') {
            updated.hasUnreadResponse = true;
          }
          return updated;
        }));

        // Refresh source conversation whenever a new crystal arrives,
        // so the user sees per-delegate progress messages in the source chat.
        const anyNewCrystal = Object.values(data.delegates).some(
          (info: any) => info.status === 'crystal'
        );
        const sourceId = folder.taskPlan?.source_conversation_id;
        if (anyNewCrystal && sourceId && pid) {
          try {
            const freshChat = await syncApi.getChat(pid, sourceId);
            if (freshChat?.messages) {
              setConversations(prev => prev.map(c => {
                if (c.id !== sourceId) return c;
                if (freshChat.messages.length <= c.messages.length) return c;
                return { ...c, messages: freshChat.messages, hasUnreadResponse: true, _version: Date.now() };
              }));
            }
          } catch { /* retry next poll cycle */ }
        }

        // Update folder taskPlan status and do final refresh when plan completes
        if (data.status === 'completed' || data.status === 'cancelled') {
          setFolders(prev => prev.map(f =>
            f.id === folder.id && f.taskPlan
              ? { ...f, taskPlan: { ...f.taskPlan, status: data.status } }
              : f
          ));

          // Refresh the source conversation to pick up the completion message
          // that DelegateManager._post_completion_to_source() wrote server-side.
          const sourceId = folder.taskPlan?.source_conversation_id;
          if (sourceId && pid) {
            try {
              const freshChat = await syncApi.getChat(pid, sourceId);
              if (freshChat && freshChat.messages) {
                setConversations(prev => prev.map(c => {
                  if (c.id !== sourceId) return c;
                  // Only update if server has newer messages
                  if (freshChat.messages.length <= c.messages.length) return c;
                  return {
                    ...c,
                    messages: freshChat.messages,
                    hasUnreadResponse: true,
                    _version: Date.now(),
                  };
                }));
              }
            } catch (err) {
              console.warn('Failed to refresh source conversation after plan completion:', err);
            }
          }
        }

        // Broadcast so other tabs re-render their sidebars
        projectSync.post('delegate-status-changed', {
          planId: data.plan_id,
          delegates: Object.fromEntries(
            Object.entries(data.delegates).map(([did, info]) => [did, info.status])
          ),
        });
      } catch {
        // Network error — skip this cycle, retry next poll
      }
    }
  }, [setConversations, setFolders]);

  useEffect(() => {
    const id = setInterval(poll, POLL_INTERVAL_MS);
    // Run immediately on mount so first update doesn't wait 3s
    poll();
    return () => clearInterval(id);
  }, [poll]);
}
