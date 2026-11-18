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
  CHAT_TURN_ORPHANED_EVENT,
  ORPHAN_RECLAIM_DELAY_MS,
  ORPHAN_RECLAIM_JITTER_MS,
  backgroundReattachCandidates,
  decideReattach,
  fetchChatTurnStatus,
  fetchChatTurnStatuses,
  probeOtherTabs,
} from '../utils/chatTurnReattach';

export const ChatTurnReattachWatcher: React.FC = () => {
  const {
    currentConversationId, currentMessages, streamingConversations,
    addStreamingConversation, removeStreamingConversation,
  } = useActiveChat();
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
  // A reattached turn is live in this tab exactly as a sent one is, so it
  // carries the same streaming mark: Stop button, probe replies, relay to
  // other tabs, and the 'streaming-ended' that releases their mirrors
  // (removeStreamingConversation posts nothing for an id never marked).
  // Via a ref: removeStreamingConversation changes identity on every
  // conversation switch and must not re-run the effects below.
  const markRef = useRef({ add: addStreamingConversation, remove: removeStreamingConversation });
  markRef.current = { add: addStreamingConversation, remove: removeStreamingConversation };

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
    // Only a mark THIS path set is released on failure.  The conversation
    // may carry another tab's mirror mark, which is not ours to remove.
    let marked = false;

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
        markRef.current.add(conversationId);
        marked = true;
        await send({
          conversationId,
          question: decision.question,
          messages: messagesRef.current.filter(m => !m.muted),
          reattach: true,
          includeReasoning: true,
        });
      } catch (e) {
        console.warn('📡 REATTACH: failed', e);
        if (marked) markRef.current.remove(conversationId);
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
        markRef.current.add(id);
        // Not awaited: each background stream runs concurrently.
        send({
          conversationId: id,
          question: decision.question,
          messages: c.messages.filter(m => !m.muted),
          reattach: true,
          includeReasoning: true,
          isStreamingToCurrentConversation: false,
        })
          .catch(e => {
            console.warn('📡 REATTACH: sweep failed', e);
            markRef.current.remove(id);
          })
          .finally(() => inFlightRef.current.delete(id));
      }
    })().catch(e => console.warn('📡 REATTACH: sweep failed', e));
  }, [hasLoadedConversations, send]);

  // A tab this one was mirroring went away mid-turn.  ChatContext has
  // already released the mirror mark; take the turn over if the server is
  // still running it, whether or not the conversation is on screen.
  // Without this the turn ran on with no viewer and its answer reached
  // history only when some tab next opened the conversation.
  useEffect(() => {
    const timers = new Set<ReturnType<typeof setTimeout>>();

    const reclaim = async (id: string) => {
      // Another mirror claimed it during the delay, or the owner reloaded
      // and reattached: its 'streaming-state' has marked it here again.
      if (streamingRef.current.has(id) || inFlightRef.current.has(id)) return;
      inFlightRef.current.add(id);
      let marked = false;
      try {
        const status = await fetchChatTurnStatus(id);
        // Only a turn still running.  A finished one is left to the
        // per-conversation path, which runs when it is next opened.
        if (!status || !status.active) return;
        if (streamingRef.current.has(id)) return;
        const viewed = id === currentIdRef.current;
        const messages = viewed
          ? messagesRef.current
          : (conversationsRef.current.find(c => c.id === id)?.messages ?? []);
        const decision = decideReattach(status, messages, false, reattachedRef.current);
        if (!decision.reattach) {
          console.log(`📡 REATTACH: orphan skip ${id.slice(0, 8)} — ${decision.reason}`);
          return;
        }
        reattachedRef.current.add(decision.turnId);
        console.log(`📡 REATTACH: taking over orphaned turn ${decision.turnId.slice(0, 8)} for ${id.slice(0, 8)}`);
        markRef.current.add(id);
        marked = true;
        await send({
          conversationId: id,
          question: decision.question,
          messages: messages.filter(m => !m.muted),
          reattach: true,
          includeReasoning: true,
          isStreamingToCurrentConversation: viewed,
        });
      } catch (e) {
        console.warn('📡 REATTACH: orphan takeover failed', e);
        if (marked) markRef.current.remove(id);
      } finally {
        inFlightRef.current.delete(id);
      }
    };

    const onOrphaned = (e: Event) => {
      const id = (e as CustomEvent<{ conversationId?: string }>).detail?.conversationId;
      if (!id) return;
      const t = setTimeout(() => { timers.delete(t); void reclaim(id); },
        ORPHAN_RECLAIM_DELAY_MS + Math.random() * ORPHAN_RECLAIM_JITTER_MS);
      timers.add(t);
    };
    window.addEventListener(CHAT_TURN_ORPHANED_EVENT, onOrphaned);
    return () => {
      window.removeEventListener(CHAT_TURN_ORPHANED_EVENT, onOrphaned);
      timers.forEach(t => clearTimeout(t));
    };
  }, [send]);

  return null;
};
