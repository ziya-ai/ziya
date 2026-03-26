/**
 * useSendPayload — centralises the 10 context-sourced values that every
 * sendPayload callsite repeats.
 *
 * Returns a single `send()` function with a clean interface.  Callers
 * provide only what varies per invocation (messages, question, images,
 * optional overrides).  The streaming infrastructure (maps, setters,
 * processing-state updater, project, checked files) is captured once
 * and stored in a ref so the returned function has a **stable identity**
 * that never changes — safe to use in dependency arrays without causing
 * re-renders.
 *
 * Fixes:
 * - Eliminates 15-argument sendPayload calls duplicated across 10 components
 * - Prevents argument-order bugs (RetrySection had args swapped)
 * - Reduces useCallback dependency arrays from 15+ items to 2–3
 */
import { useCallback, useRef } from 'react';
import { useActiveChat } from '../context/ActiveChatContext';
import { useProject } from '../context/ProjectContext';
import { useFolderContext } from '../context/FolderContext';
import { sendPayload } from '../apis/chatApi';
import { convertKeysToStrings } from '../utils/types';
import type { Message, ImageAttachment } from '../utils/types';

export interface SendPayloadOptions {
    /** Messages to send.  Defaults to currentMessages (non-muted). */
    messages?: Message[];
    /** The user's question / prompt text. */
    question: string;
    /** Target conversation ID.  Defaults to currentConversationId. */
    conversationId?: string;
    /** Image attachments for vision models. */
    images?: ImageAttachment[];
    /** Override active skill prompts (default: from ProjectContext). */
    activeSkillPrompts?: string;
    /** Override checked files (default: from FolderContext). */
    checkedItems?: string[];
    /**
     * Whether the target conversation is the one currently visible.
     * Controls whether streaming content is displayed inline.
     * Default: true when conversationId matches currentConversationId.
     */
    isStreamingToCurrentConversation?: boolean;
    /** Pass setReasoningContentMap for models that emit reasoning tokens. */
    includeReasoning?: boolean;
}

export interface SendPayloadHandle {
    /**
     * Send a payload to the model.  All streaming infrastructure values
     * (maps, setters, project, processing-state updater) are captured
     * from context and stay current via a ref.
     *
     * Returns the completed response string (from sendPayload).
     */
    send: (options: SendPayloadOptions) => Promise<string>;
}

/**
 * Hook that captures all streaming infrastructure from context and
 * returns a stable `send()` function.  The function identity never
 * changes, so it's safe in dependency arrays without causing
 * re-renders.
 */
export function useSendPayload(): SendPayloadHandle {
    const activeChat = useActiveChat();
    const { checkedKeys } = useFolderContext();
    const project = useProject();

    // Store everything in a ref so the returned `send` callback is
    // truly stable — its closure reads .current at call time.
    const ref = useRef({
        activeChat,
        checkedKeys,
        project,
    });
    ref.current = { activeChat, checkedKeys, project };

    const send = useCallback(async (options: SendPayloadOptions): Promise<string> => {
        const { activeChat: ac, checkedKeys: ck, project: pj } = ref.current;

        const conversationId = options.conversationId ?? ac.currentConversationId;
        const messages = options.messages ?? ac.currentMessages.filter(m => !m.muted);
        const checkedItems = options.checkedItems ?? convertKeysToStrings(ck || []);
        const skills = options.activeSkillPrompts ?? pj.activeSkillPrompts;
        const isCurrentConv = options.isStreamingToCurrentConversation
            ?? (conversationId === ac.currentConversationId);

        return sendPayload(
            messages,
            options.question,
            checkedItems,
            conversationId,
            skills || undefined,
            options.images,
            ac.streamedContentMap,
            ac.setStreamedContentMap,
            ac.setIsStreaming,
            ac.removeStreamingConversation,
            ac.addMessageToConversation,
            isCurrentConv,
            (state) => ac.updateProcessingState(conversationId, state),
            options.includeReasoning ? ac.setReasoningContentMap : undefined,
            undefined, // throttlingRecoveryDataRef
            pj.currentProject ?? null,
        );
    }, []); // stable — reads from ref.current

    return { send };
}
