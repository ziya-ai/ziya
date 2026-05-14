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

/**
 * Window event dispatched by any component that creates or modifies a
 * task binding.  Listeners should refresh their binding data.
 */
export const TASK_BINDING_EVENT = 'task-binding-created';

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

  const bindingsByAnchor = useMemo(() => {
    const map = new Map<string, TaskBinding[]>();
    for (const b of bindings) {
      const key = b.anchor_message_id ?? '__no_anchor__';
      const arr = map.get(key) ?? [];
      arr.push(b);
      map.set(key, arr);
    }
    return map;
  }, [bindings]);

  return { bindingsByAnchor, loading, refresh };
}
