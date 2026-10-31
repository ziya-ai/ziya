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
  backgroundReattachCandidates,
  decideReattach,
  fetchChatTurnStatus,
  fetchChatTurnStatuses,
  probeOtherTabs,
} from '../utils/chatTurnReattach';

export const ChatTurnReattachWatcher: React.FC = () => {
  const { currentConversationId, currentMessages, streamingConversations } = useActiveChat();
  const { hasLoadedConversations, conversations } = useConversationList();
  const { send } = useSendPayload();

  // Read at decision time, not captured at effect time: the probe wait
  // is exactly when another tab's reply may change these.
  const streamingRef = useRef(streamingConversations);
  streamingRef.current = streamingConversations;
  const messagesRef = useRef(currentMessages);
  messagesRef.current = currentMessages;
  const conversationsRef = useRef(conversations);
  conversationsRef.current = conversations;
  const currentIdRef = useRef(currentConversationId);
  currentIdRef.current = currentConversationId;

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

  // Reload sweep, once per page load: resubscribe to turns in conversations
  // this tab is NOT showing.  The effect above only covers the viewed one,
  // so before this a turn running elsewhere (typically parked in a
  // job_wait / shadow_wait) had no subscriber after a reload, hit the
  // relay's disconnect grace timer (60 s) and was cancelled.
  const sweptRef = useRef(false);
  useEffect(() => {
    if (!hasLoadedConversations || sweptRef.current) return;
    sweptRef.current = true;
    (async () => {
      const statuses = await fetchChatTurnStatuses();
      const candidates = backgroundReattachCandidates(
        statuses, currentIdRef.current, conversationsRef.current);
      if (candidates.length === 0) return;
      // One probe window for all of them, not 250 ms each.
      for (const c of candidates) {
        projectSync.post('streaming-probe', { conversationId: c.status.conversation_id });
      }
      await new Promise(resolve => setTimeout(resolve, 250));
      for (const c of candidates) {
        const id = c.status.conversation_id;
        // The user may have opened it during the probe; that path owns it now.
        if (id === currentIdRef.current || inFlightRef.current.has(id)) continue;
        const decision = decideReattach(
          c.status, c.messages, streamingRef.current.has(id), reattachedRef.current);
        if (!decision.reattach) {
          console.log(`📡 REATTACH: sweep skip ${id.slice(0, 8)} — ${decision.reason}`);
          continue;
        }
        reattachedRef.current.add(decision.turnId);
        inFlightRef.current.add(id);
        console.log(`📡 REATTACH: sweep resuming turn ${decision.turnId.slice(0, 8)} for ${id.slice(0, 8)}`);
        // Not awaited: each background stream runs concurrently.
        send({
          conversationId: id,
          question: decision.question,
          messages: c.messages.filter(m => !m.muted),
          reattach: true,
          includeReasoning: true,
          isStreamingToCurrentConversation: false,
        })
          .catch(e => console.warn('📡 REATTACH: sweep failed', e))
          .finally(() => inFlightRef.current.delete(id));
      }
    })().catch(e => console.warn('📡 REATTACH: sweep failed', e));
  }, [hasLoadedConversations, send]);

  return null;
};
