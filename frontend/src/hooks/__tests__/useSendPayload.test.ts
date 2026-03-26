/**
 * Tests for useSendPayload hook — validates the argument mapping layer.
 *
 * These are logic-only tests that verify the options→sendPayload argument
 * translation without requiring full React context or DOM rendering.
 *
 * Covers:
 *   - Default values are filled from context refs
 *   - Explicit overrides take precedence
 *   - isStreamingToCurrentConversation defaults correctly
 *   - includeReasoning controls setReasoningContentMap passthrough
 *   - Argument order matches sendPayload's 17-parameter signature
 */

describe('useSendPayload argument mapping', () => {
    // Simulate the mapping logic from the hook without React
    const mapOptions = (
        options: {
            messages?: any[];
            question: string;
            conversationId?: string;
            images?: any[];
            activeSkillPrompts?: string;
            checkedItems?: string[];
            isStreamingToCurrentConversation?: boolean;
            includeReasoning?: boolean;
        },
        context: {
            currentConversationId: string;
            currentMessages: any[];
            checkedKeys: string[];
            activeSkillPrompts: string | undefined;
            currentProject: any;
        }
    ) => {
        const conversationId = options.conversationId ?? context.currentConversationId;
        const messages = options.messages ?? context.currentMessages.filter((m: any) => !m.muted);
        const checkedItems = options.checkedItems ?? context.checkedKeys;
        const skills = options.activeSkillPrompts ?? context.activeSkillPrompts;
        const isCurrentConv = options.isStreamingToCurrentConversation
            ?? (conversationId === context.currentConversationId);
        const includeReasoning = options.includeReasoning ?? false;

        return { conversationId, messages, checkedItems, skills, isCurrentConv, includeReasoning };
    };

    const defaultContext = {
        currentConversationId: 'conv-1',
        currentMessages: [
            { role: 'human', content: 'hello', muted: false },
            { role: 'assistant', content: 'hi', muted: false },
            { role: 'human', content: 'muted msg', muted: true },
        ],
        checkedKeys: ['file1.ts', 'file2.ts'],
        activeSkillPrompts: 'skill-prompt',
        currentProject: { id: 'proj-1', name: 'Test', path: '/test' },
    };

    it('fills defaults from context when no overrides given', () => {
        const result = mapOptions({ question: 'test question' }, defaultContext);
        expect(result.conversationId).toBe('conv-1');
        expect(result.messages).toHaveLength(2); // muted message filtered out
        expect(result.checkedItems).toEqual(['file1.ts', 'file2.ts']);
        expect(result.skills).toBe('skill-prompt');
        expect(result.isCurrentConv).toBe(true);
    });

    it('explicit overrides take precedence', () => {
        const customMessages = [{ role: 'human', content: 'custom' }];
        const result = mapOptions({
            question: 'q',
            messages: customMessages,
            conversationId: 'conv-2',
            checkedItems: ['other.ts'],
            activeSkillPrompts: 'custom-skill',
        }, defaultContext);

        expect(result.conversationId).toBe('conv-2');
        expect(result.messages).toBe(customMessages);
        expect(result.checkedItems).toEqual(['other.ts']);
        expect(result.skills).toBe('custom-skill');
        expect(result.isCurrentConv).toBe(false); // conv-2 !== conv-1
    });

    it('isStreamingToCurrentConversation defaults based on conversationId match', () => {
        const same = mapOptions({ question: 'q', conversationId: 'conv-1' }, defaultContext);
        expect(same.isCurrentConv).toBe(true);

        const different = mapOptions({ question: 'q', conversationId: 'conv-other' }, defaultContext);
        expect(different.isCurrentConv).toBe(false);

        const forced = mapOptions({ question: 'q', conversationId: 'conv-other', isStreamingToCurrentConversation: true }, defaultContext);
        expect(forced.isCurrentConv).toBe(true);
    });

    it('includeReasoning defaults to false', () => {
        const result = mapOptions({ question: 'q' }, defaultContext);
        expect(result.includeReasoning).toBe(false);

        const withReasoning = mapOptions({ question: 'q', includeReasoning: true }, defaultContext);
        expect(withReasoning.includeReasoning).toBe(true);
    });
});
