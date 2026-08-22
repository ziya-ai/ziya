/**
 * useTaskBindings — fetches TaskBindings for the current chat and
 * builds a lookup map keyed by anchor_message_id.
 *
 * Returns:
 *   bindingsByAnchor: Map<string, TaskBinding[]>  (empty if no project/chat)
 *   loading: boolean
 *   refresh: () => void  (force re-fetch)
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import { useProject } from '../context/ProjectContext';
import type { TaskBinding } from '../types/task_binding';
import { listBindings } from '../services/taskBindingApi';
import { collapseLineages } from '../components/TaskCard/lineageCollapse';

/**
 * Window event dispatched by any component that creates or modifies a
 * task binding.  Listeners should refresh their binding data.
 */
export const TASK_BINDING_EVENT = 'task-binding-created';

// Dispatched by an inline card tile's "Edit card" backlink; App listens and
// opens the Task Cards deck focused on detail.cardId.  Same window-event
// pattern as TASK_BINDING_EVENT — the tile is deep under the chat tree and
// can't reach App's deck state directly.
export const TASK_CARD_OPEN_EVENT = 'task-card-open';

export function useTaskBindings(chatId: string | undefined) {
  const { currentProject } = useProject();
  const projectId = currentProject?.id ?? '';

  const [bindings, setBindings] = useState<TaskBinding[]>([]);
  const [loading, setLoading] = useState(false);
  const [version, setVersion] = useState(0);

  useEffect(() => {
    if (!projectId || !chatId) {
      setBindings([]);
      return;
    }
    let cancelled = false;
    setLoading(true);
    (async () => {
      try {
        const list = await listBindings(projectId, chatId);
        if (!cancelled) setBindings(list);
      } catch (e) {
        // Non-fatal: bindings are optional UX enhancement
        console.debug('useTaskBindings: fetch failed', e);
        if (!cancelled) setBindings([]);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [projectId, chatId, version]);

  // Listen for binding-change events from other parts of the app
  // (TaskCardsLibrary, TaskCardLaunchButton) so the chat view reflects
  // new bindings without requiring a reload or remount.
  useEffect(() => {
    const handler = () => setVersion(v => v + 1);
    window.addEventListener(TASK_BINDING_EVENT, handler);
    return () => window.removeEventListener(TASK_BINDING_EVENT, handler);
  }, []);

  const refresh = useCallback(() => setVersion(v => v + 1), []);

  // Listen for binding-creation events from TaskCardLaunchButton so the
  // tile appears without a reload.
  useEffect(() => {
    if (!chatId) return;
    const handler = () => setVersion(v => v + 1);
    window.addEventListener('task-binding-created', handler);
    return () => window.removeEventListener('task-binding-created', handler);
  }, [chatId]);

  /**
   * Collapse each attempt lineage to its newest attempt.
   *
   * A resume creates a new run AND a new binding, so a card retried
   * twice previously rendered three tiles side by side with nothing
   * stating their relationship — the confusion this whole change exists
   * to remove.  One tile per lineage, showing the newest attempt, with
   * the rest reachable from its attempt rail.
   *
   * Derived synchronously from ``root_run_id`` / ``attempt``, which the
   * list endpoint stamps from the run lookup it already performs for
   * ``run_status``.  The previous version fetched a run per binding,
   * which was both a request burst and wrong for a cross-project global
   * chat: it used the VIEWING project id, while the server resolves
   * bindings from the chat's OWNING project.  Being synchronous also
   * removes the first-paint window in which every attempt rendered.
   */
  const supersededIds = useMemo(() => collapseLineages(bindings), [bindings]);

  const bindingsByAnchor = useMemo(() => {
    const map = new Map<string, TaskBinding[]>();
    for (const b of bindings) {
      if (supersededIds.has(b.id)) continue;
      const key = b.anchor_message_id ?? '__no_anchor__';
      const arr = map.get(key) ?? [];
      arr.push(b);
      map.set(key, arr);
    }
    return map;
  }, [bindings, supersededIds]);

  // ``bindings`` is exposed flat as well as grouped: the conversation
  // list needs per-status COUNTS across the whole chat, which the
  // anchor-keyed map cannot answer without the caller flattening it back
  // out — and a caller that flattens the map has already lost the staged
  // bindings the map drops.  Grouping is a render concern; counting is
  // not.
  return { bindings, bindingsByAnchor, loading, refresh };
}
