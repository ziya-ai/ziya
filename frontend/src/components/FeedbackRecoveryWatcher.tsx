/**
 * Recovers mid-stream feedback that a turn ended without consuming.
 *
 * Renders nothing. It exists as a component rather than a hook inside the
 * composer for two reasons:
 *
 *  1. Presence-independence. Feedback belongs to the conversation it was typed
 *     into, and the backend's queue already treats it that way. The recovery
 *     must therefore fire when THAT conversation's turn ends, whatever is on
 *     screen. The composer can only see the viewed conversation, so a recovery
 *     living there fired on navigating BACK to a conversation that had already
 *     stranded feedback — an unexpected turn on arrival, possibly much later.
 *  2. Subscription isolation. Resubmitting into a non-viewed conversation
 *     needs that conversation's own message history, which means subscribing
 *     to the conversation list. Doing that from App or the composer would
 *     re-render an expensive tree on every conversation mutation; a null
 *     component absorbs those renders.
 */
import React, { useCallback, useEffect, useRef } from 'react';
import { useActiveChat } from '../context/ActiveChatContext';
import { useConversationList } from '../context/ConversationListContext';
import { useSendPayload } from '../hooks/useSendPayload';
import {
  takeRetainedFeedback,
  FEEDBACK_RESUBMITTED_EVENT,
  FeedbackResubmittedDetail,
} from '../utils/feedbackRetention';

export const FeedbackRecoveryWatcher: React.FC = () => {
  const {
    streamingConversations,
    currentConversationId,
    addMessageToConversation,
    addStreamingConversation,
    removeStreamingConversation,
  } = useActiveChat();
  const { conversations } = useConversationList();
  const { send } = useSendPayload();

  // Read at recovery time, not captured at render time: the turn may end many
  // renders after the feedback was sent.
  const conversationsRef = useRef(conversations);
  conversationsRef.current = conversations;
  // Used ONLY to decide whether the recovered stream should render inline.
  // It must never gate whether the recovery happens.
  const viewedRef = useRef(currentConversationId);
  viewedRef.current = currentConversationId;

  const previousStreamingRef = useRef<Set<string>>(new Set());

  const resubmit = useCallback(async (conversationId: string, texts: string[]) => {
    const conv = conversationsRef.current.find(c => c.id === conversationId);
    if (!conv) {
      // Deleted or switched out of the project while the turn was running.
      // The text is already claimed, so it is dropped rather than sent into a
      // conversation whose history we cannot reconstruct.
      console.warn(
        '📝 FEEDBACK: conversation gone before recovery could run; dropping',
        conversationId, texts,
      );
      return;
    }
    const text = texts.join('\n\n');
    const userMessage = {
      role: 'human' as const,
      content: text,
      _timestamp: Date.now(),
    };
    // Snapshot BEFORE staging the new message, then append it explicitly —
    // the assistant's response for the finished turn is already committed by
    // the time streamingConversations drops the id, so it is included here.
    const history = (conv.messages ?? []).filter((m: any) => !m.muted);
    addMessageToConversation(userMessage, conversationId);
    addStreamingConversation(conversationId);
    document.dispatchEvent(new CustomEvent<FeedbackResubmittedDetail>(
      FEEDBACK_RESUBMITTED_EVENT,
      { detail: { conversationId, texts } },
    ));
    try {
      await send({
        messages: [...history, userMessage],
        question: text,
        conversationId,
        includeReasoning: true,
        isStreamingToCurrentConversation: conversationId === viewedRef.current,
      });
    } catch (error) {
      console.error('📝 FEEDBACK: recovery send failed for', conversationId, error);
      removeStreamingConversation(conversationId);
    }
  }, [addMessageToConversation, addStreamingConversation, removeStreamingConversation, send]);

  useEffect(() => {
    const previous = previousStreamingRef.current;
    previousStreamingRef.current = new Set(streamingConversations);
    for (const conversationId of previous) {
      if (streamingConversations.has(conversationId)) continue;
      const texts = takeRetainedFeedback(conversationId);
      if (texts.length === 0) continue;
      console.warn(
        '📝 FEEDBACK: turn ended without consuming feedback — resubmitting as a new turn:',
        conversationId, texts,
      );
      void resubmit(conversationId, texts);
    }
  }, [streamingConversations, resubmit]);

  return null;
};

export default FeedbackRecoveryWatcher;
