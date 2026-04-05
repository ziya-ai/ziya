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
  needs_attention: string[];
  task_list?: Array<{ task_id: string; title: string; status: string; claimed_by?: string; summary?: string }>;
}

function getActiveTaskPlanFolders(folders: ConversationFolder[]): ConversationFolder[] {
  const TERMINAL = new Set(['completed', 'completed_partial', 'cancelled']);
  return folders.filter(
    f => {
      if (!f.taskPlan) return false;
      if (TERMINAL.has(f.taskPlan.status)) return false;
      // Also skip plans where all delegates are in terminal states
      // (server may not have updated plan-level status yet)
      const specs = f.taskPlan.delegate_specs;
      if (specs && specs.length > 0) {
        const allTerminal = specs.every(
          (s: any) => ['crystal', 'failed', 'interrupted'].includes(s.status || '')
        );
        if (allTerminal) return false;
      }
      return true;
    }
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
  
  // Guard against re-entrant poll calls (previous poll still in-flight)
  const pollInFlightRef = useRef(false);

  // Derive a stable boolean for whether any active task plans exist.
  // Used as an effect dependency to start/stop the polling interval
  // without re-creating it on every unrelated folders change.
  const hasActivePlans = folders.some(
    f => f.taskPlan && !new Set(['completed', 'completed_partial', 'cancelled']).has(f.taskPlan.status)
  );

  // Snapshot folders into a ref so the interval callback doesn't
  // create a new closure (and new interval) every time folders change.
  const foldersRef = useRef(folders);
  foldersRef.current = folders;
  
  const projectIdRef = useRef(projectId);
  projectIdRef.current = projectId;

  const poll = useCallback(async () => {
    const pid = projectIdRef.current;
    if (!pid) return;
    
    // Skip polling when tab is hidden to avoid piling up fetch requests
    if (document.hidden) return;

    // Prevent re-entrant polls — if the previous cycle is still fetching
    // (e.g. slow getChat for large conversations), skip this tick entirely.
    if (pollInFlightRef.current) return;
    pollInFlightRef.current = true;

    try {
    const activeFolders = getActiveTaskPlanFolders(foldersRef.current);
    if (activeFolders.length === 0) return; // finally block still clears flag

    for (const folder of activeFolders) {
      try {
        const res = await fetch(
          `/api/v1/projects/${pid}/groups/${folder.id}/delegate-status`
        );
        if (!res.ok) {
          continue;
        }
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
        const newlyCrystalConvIds: string[] = [];
        setConversations(prev => {
          let anyChanged = false;
          const next = prev.map(c => {
          if (!c.delegateMeta || c.delegateMeta.plan_id !== data.plan_id) return c;
          const did = c.delegateMeta.delegate_id;
          if (!did || !data.delegates[did]) return c;
          const newStatus = data.delegates[did].status;
          if (c.delegateMeta.status === newStatus) return c;
          const updated: any = { ...c, delegateMeta: { ...c.delegateMeta, status: newStatus as any } };
          // Surface terminal delegates so the user sees they have results
          if (newStatus === 'crystal' || newStatus === 'interrupted' || newStatus === 'failed') {
            updated.hasUnreadResponse = true;
          }
          if (newStatus === 'crystal' && c.delegateMeta.status !== 'crystal') {
            newlyCrystalConvIds.push(c.id);
          }
          anyChanged = true;
          return updated;
          });
          // Return same reference when nothing changed — avoids cascading re-renders
          return anyChanged ? next : prev;
        });

        // Refresh source conversation only when a NEW crystal arrives
        // (not on every poll where crystals exist).
        const anyNewCrystal = newlyCrystalConvIds.length > 0 || (
          Object.values(data.delegates).some(
            (info: any) => info.status === 'crystal'
          ) && !prevStatuses._hadCrystals
        );
        const sourceId = folder.taskPlan?.source_conversation_id;
        if (newlyCrystalConvIds.length > 0 && sourceId && pid) {
          try {
            const freshChat = await syncApi.getChat(pid, sourceId);
            if (freshChat?.messages) {
              setConversations(prev => {
                const c = prev.find(c => c.id === sourceId);
                if (!c || freshChat.messages.length <= c.messages.length) return prev;
                return prev.map(c => {
                if (c.id !== sourceId) return c;
                return { ...c, messages: freshChat.messages, hasUnreadResponse: true, _version: Date.now() };
                });
              });
            }
          } catch { /* retry next poll cycle */ }
        }

        // Update folder taskPlan status and do final refresh when plan completes
        if (data.status === 'completed' || data.status === 'completed_partial' || data.status === 'cancelled') {
          setFolders(prev => prev.map(f =>
            f.id === folder.id && f.taskPlan
              ? { ...f, taskPlan: { ...f.taskPlan, status: data.status } }
              : f
          ));
          
          // Clean up previous status tracking for completed plans
          // to prevent stale data from accumulating
          delete prevStatusRef.current[data.plan_id];

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
    } finally {
      pollInFlightRef.current = false;
    }
  }, [setConversations, setFolders]);

  useEffect(() => {
    // Only start the polling interval when active TaskPlan folders exist.
    // This avoids a 3s timer firing hundreds of times during normal
    // (non-swarm) usage with hundreds of conversations.
    if (!hasActivePlans) return;

    const id = setInterval(poll, POLL_INTERVAL_MS);
    // Run immediately on mount so first update doesn't wait 3s
    poll();
    return () => clearInterval(id);
  }, [poll, hasActivePlans]);
}
