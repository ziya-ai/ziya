/**
 * useResolvedModelPin — resolves the effective model pin for a
 * conversation across all scopes and both persistence layers.
 *
 * Centralises record-reading so callers (useSendPayload at send time,
 * the sidebar display chip) don't each have to wire up ChatContext +
 * ProjectContext.  It layers the tab-ephemeral pin store
 * (frontend/src/utils/modelPins.ts) over the SAVED ``modelPreference``
 * fields on the conversation / folder / project records.
 *
 * Resolution precedence (most specific wins): conversation → folder →
 * project → server default.  Within a level, a tab pin overrides a
 * saved pref.  Returns null when nothing is pinned.
 *
 * Re-resolves on MODEL_PIN_CHANGED_EVENT (tab-store mutations) and when
 * the underlying records or active conversation change.
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useChatContext } from '../context/ChatContext';
import { useProject } from '../context/ProjectContext';
import {
  resolveModelPin, MODEL_PIN_CHANGED_EVENT,
  type ResolvedModelPin, type PersistedModelPrefs,
} from '../utils/modelPins';

export interface ResolvedModelPinResult {
  /** The effective pin, or null for server default. */
  pin: ResolvedModelPin | null;
  /** The active conversation's folder id (null when not in a folder). */
  folderId: string | null;
  /** Re-read helper for callers resolving for a specific conversation. */
  resolveFor: (conversationId: string | null | undefined) => ResolvedModelPin | null;
}

export function useResolvedModelPin(): ResolvedModelPinResult {
  const { conversations, folders, currentConversationId } = useChatContext();
  const { currentProject } = useProject();

  // Bump on tab-store mutations so the memo below re-resolves.
  const [pinTick, setPinTick] = useState(0);
  useEffect(() => {
    const onChanged = () => setPinTick(t => t + 1);
    window.addEventListener(MODEL_PIN_CHANGED_EVENT, onChanged);
    return () => window.removeEventListener(MODEL_PIN_CHANGED_EVENT, onChanged);
  }, []);

  const resolveFor = useCallback(
    (conversationId: string | null | undefined): ResolvedModelPin | null => {
      const conv = conversations.find(c => c.id === conversationId) || null;
      const folderId = conv?.folderId ?? null;
      const folder = folderId ? (folders.find(f => f.id === folderId) || null) : null;
      // SAVED prefs live on the records as ``modelPreference`` (added in
      // the persistence slice; undefined until then — resolves as no-op).
      const persisted: PersistedModelPrefs = {
        conversation: (conv as any)?.modelPreference ?? null,
        folder: (folder as any)?.modelPreference ?? null,
        project: (currentProject?.settings as any)?.modelPreference ?? null,
      };
      return resolveModelPin({
        conversationId: conversationId ?? null,
        folderId,
        projectId: currentProject?.id ?? null,
        persisted,
      });
      // eslint-disable-next-line react-hooks/exhaustive-deps
    },
    [conversations, folders, currentProject?.id, currentProject?.settings, pinTick],
  );

  const activeFolderId = useMemo(() => {
    const conv = conversations.find(c => c.id === currentConversationId);
    return conv?.folderId ?? null;
  }, [conversations, currentConversationId]);

  const pin = useMemo(
    () => resolveFor(currentConversationId),
    [resolveFor, currentConversationId],
  );

  return { pin, folderId: activeFolderId, resolveFor };
}
