/**
 * Reattaches the viewed conversation to a chat turn the server is still
 * running (or finished while nobody was watching).
 *
 * Fires when the viewed conversation changes and on first load.  Renders
 * nothing.  Lives beside FeedbackRecoveryWatcher for the same reason: it
 * needs conversation state and the send pipeline, and must not re-render
 * the composer on every conversation mutation.
 *
 * Sequence per candidate conversation:
 *   1. GET /api/chat/turn/{id} — 404 means nothing to do (the common case).
 *   2. Probe other tabs in this browser; an owner replies with
 *      'streaming-state', which ChatContext turns into a streaming mark.
 *   3. decideReattach() with the fresh local facts.
 *   4. send({ reattach: true }) — the ordinary pipeline, sourced from
 *      GET /api/chat/turn/{id}/stream instead of POST /api/chat.
 *
 * See design/consent-runtime.md §Chat turn relay.
 */
import React, { useEffect, useRef } from 'react';
import { useActiveChat } from '../context/ActiveChatContext';
import { useConversationList } from '../context/ConversationListContext';
import { useSendPayload } from '../hooks/useSendPayload';
import { projectSync } from '../utils/projectSync';
import {
  decideReattach,
  fetchChatTurnStatus,
  probeOtherTabs,
} from '../utils/chatTurnReattach';

export const ChatTurnReattachWatcher: React.FC = () => {
  const { currentConversationId, currentMessages, streamingConversations } = useActiveChat();
  const { hasLoadedConversations } = useConversationList();
  const { send } = useSendPayload();

  // Read at decision time, not captured at effect time: the probe wait
  // is exactly when another tab's reply may change these.
  const streamingRef = useRef(streamingConversations);
  streamingRef.current = streamingConversations;
  const messagesRef = useRef(currentMessages);
  messagesRef.current = currentMessages;

  // Turn ids this tab has already reattached to.  A turn id is minted per
  // server-side turn, so this is exact: the same conversation can be
  // reattached again later for a NEW turn.
  const reattachedRef = useRef<Set<string>>(new Set());
  // Serialises concurrent checks for the same conversation (rapid switching).
  const inFlightRef = useRef<Set<string>>(new Set());

  useEffect(() => {
    const conversationId = currentConversationId;
    if (!conversationId || !hasLoadedConversations) return;
    if (inFlightRef.current.has(conversationId)) return;
    inFlightRef.current.add(conversationId);
    let stale = false;

    (async () => {
      try {
        const status = await fetchChatTurnStatus(conversationId);
        if (stale || !status) return;
        // Cheap early exit before the probe wait.
        if (streamingRef.current.has(conversationId)) return;

        await probeOtherTabs((t, p) => projectSync.post(t, p), conversationId);
        if (stale) return;

        const decision = decideReattach(
          status, messagesRef.current,
          streamingRef.current.has(conversationId), reattachedRef.current,
        );
        if (!decision.reattach) {
          console.log(`📡 REATTACH: skip ${conversationId.slice(0, 8)} — ${decision.reason}`);
          return;
        }
        reattachedRef.current.add(decision.turnId);
        console.log(`📡 REATTACH: resuming turn ${decision.turnId.slice(0, 8)} for ${conversationId.slice(0, 8)}`);
        await send({
          conversationId,
          question: decision.question,
          messages: messagesRef.current.filter(m => !m.muted),
          reattach: true,
          includeReasoning: true,
        });
      } catch (e) {
        console.warn('📡 REATTACH: failed', e);
      } finally {
        inFlightRef.current.delete(conversationId);
      }
    })();

    return () => { stale = true; };
  }, [currentConversationId, hasLoadedConversations, send]);

  return null;
};
