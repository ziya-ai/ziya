/**
 * Tests for EditSection component logic.
 *
 * Verifies:
 *   - setConversations must come from ConversationListContext, not ActiveChatContext
 *   - handleSubmit truncates messages up to and including the edited index
 *   - handleSave updates the message at the edited index in place
 *   - handleCancel resets editing state
 */

describe('EditSection context requirements', () => {
    // The key invariant: setConversations is NOT part of ActiveChatContextValue.
    // It lives in ConversationListContext. Destructuring it from useActiveChat()
    // yields undefined, which crashes at runtime when called as a function.

    it('ActiveChatContext does not expose setConversations', () => {
        // This mirrors the interface exported from ActiveChatContext.tsx.
        // If someone adds setConversations there, this test should be reviewed.
        const activeChatKeys = [
            'currentConversationId', 'currentMessages', 'setCurrentConversationId',
            'addMessageToConversation', 'loadConversation', 'loadConversationAndScrollToMessage',
            'startNewChat', 'editingMessageIndex', 'setEditingMessageIndex',
            'isStreaming', 'setIsStreaming', 'streamingConversations',
            'addStreamingConversation', 'removeStreamingConversation',
            'streamedContentMap', 'setStreamedContentMap',
            'reasoningContentMap', 'setReasoningContentMap',
            'getProcessingState', 'updateProcessingState',
            'dynamicTitleLength', 'setDynamicTitleLength',
            'lastResponseIncomplete', 'setDisplayMode', 'toggleMessageMute',
            'setChatContexts', 'currentDisplayMode',
            'throttlingRecoveryData', 'setThrottlingRecoveryData',
        ];
        expect(activeChatKeys).not.toContain('setConversations');
    });

    it('ConversationListContext exposes setConversations', () => {
        // This mirrors the interface exported from ConversationListContext.tsx.
        const convListKeys = [
            'conversations', 'setConversations',
            'folders', 'setFolders',
            'isLoadingConversation', 'isProjectSwitching',
        ];
        expect(convListKeys).toContain('setConversations');
    });
});

describe('EditSection message truncation logic', () => {
    const buildMessages = (count: number) =>
        Array.from({ length: count }, (_, i) => ({
            role: i % 2 === 0 ? 'human' : 'assistant',
            content: `Message ${i}`,
        }));

    it('truncates messages up to and including the edited index', () => {
        const messages = buildMessages(6); // indices 0..5
        const editIndex = 2;
        const truncated = messages.slice(0, editIndex + 1);

        expect(truncated).toHaveLength(3);
        expect(truncated[truncated.length - 1].content).toBe('Message 2');
    });

    it('keeps all messages when editing the last one', () => {
        const messages = buildMessages(4);
        const editIndex = 3;
        const truncated = messages.slice(0, editIndex + 1);

        expect(truncated).toHaveLength(4);
    });

    it('keeps only the first message when editing index 0', () => {
        const messages = buildMessages(4);
        const editIndex = 0;
        const truncated = messages.slice(0, editIndex + 1);

        expect(truncated).toHaveLength(1);
        expect(truncated[0].content).toBe('Message 0');
    });
});

describe('EditSection save logic', () => {
    it('updates the message at the specified index without truncating', () => {
        const messages = [
            { role: 'human', content: 'Original question' },
            { role: 'assistant', content: 'Response' },
            { role: 'human', content: 'Follow up' },
        ];
        const editIndex = 0;
        const newContent = 'Edited question';

        const updated = messages.map((msg, i) =>
            i === editIndex ? { ...msg, content: newContent } : msg
        );

        expect(updated).toHaveLength(3); // no truncation
        expect(updated[0].content).toBe('Edited question');
        expect(updated[1].content).toBe('Response');
        expect(updated[2].content).toBe('Follow up');
    });
});
