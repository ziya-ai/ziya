/**
 * Tests for FeedbackRecoveryWatcher — the presence-independent recovery of
 * mid-stream feedback that a turn ended without consuming.
 *
 * What this replaces, and why the replacement is not cosmetic:
 *
 * The recovery used to live in SendChatContainer, gated on the stranded
 * feedback belonging to the VIEWED conversation and on that conversation not
 * streaming. So a turn that stranded feedback while the user was elsewhere was
 * not recovered when it ended — it was recovered whenever the user next
 * navigated back, firing an unexpected turn on arrival. It also built its
 * history from the viewed conversation's messages, so it could not have sent
 * into another conversation even if it had tried.
 *
 * The intended behaviour: feedback stays on the delivery path to the
 * conversation it was typed into, regardless of what is on screen. These tests
 * assert that by keeping the viewed conversation DIFFERENT from the one whose
 * turn ends in the load-bearing cases.
 *
 * This renders the real component through mocked context modules, so the seam
 * under test is the actual one: streamingConversations transition → retention
 * store take → send() with that conversation's own history.
 */

import React from 'react';
import { render, act } from '@testing-library/react';

// ── context / hook stubs ──────────────────────────────────────────────────
// Mutable so each test can position the viewed conversation independently of
// the conversation whose turn is ending.
const activeChat = {
    streamingConversations: new Set<string>(),
    currentConversationId: 'conv-viewed',
    addMessageToConversation: jest.fn(),
    addStreamingConversation: jest.fn(),
    removeStreamingConversation: jest.fn(),
};
const convList = { conversations: [] as any[] };
const send = jest.fn().mockResolvedValue('ok');

jest.mock('../../context/ActiveChatContext', () => ({
    useActiveChat: () => activeChat,
}));
jest.mock('../../context/ConversationListContext', () => ({
    useConversationList: () => convList,
}));
jest.mock('../../hooks/useSendPayload', () => ({
    useSendPayload: () => ({ send }),
}));

import { FeedbackRecoveryWatcher } from '../FeedbackRecoveryWatcher';
import {
    recordSentFeedback,
    retireDeliveredFeedback,
    hasRetainedFeedback,
    FEEDBACK_RESUBMITTED_EVENT,
    __resetFeedbackRetentionForTests,
} from '../../utils/feedbackRetention';

/** Render the watcher with `streaming` in flight, then end those turns. */
function mountWithStreaming(streaming: string[]) {
    activeChat.streamingConversations = new Set(streaming);
    return render(<FeedbackRecoveryWatcher />);
}

/** Drive a streamingConversations transition through a re-render. */
async function setStreaming(
    rerender: (ui: React.ReactElement) => void,
    streaming: string[],
) {
    activeChat.streamingConversations = new Set(streaming);
    await act(async () => {
        rerender(<FeedbackRecoveryWatcher />);
    });
}

beforeEach(() => {
    __resetFeedbackRetentionForTests();
    send.mockClear();
    activeChat.addMessageToConversation.mockClear();
    activeChat.addStreamingConversation.mockClear();
    activeChat.removeStreamingConversation.mockClear();
    activeChat.currentConversationId = 'conv-viewed';
    convList.conversations = [
        {
            id: 'conv-away',
            messages: [
                { role: 'human', content: 'original question' },
                { role: 'assistant', content: 'the response' },
            ],
        },
        {
            id: 'conv-viewed',
            messages: [{ role: 'human', content: 'unrelated thread' }],
        },
    ];
});

describe('recovery for a conversation the user is NOT looking at', () => {
    it('resubmits when that conversation\'s turn ends (the reported gap)', async () => {
        recordSentFeedback('conv-away', 'use the other index');
        const { rerender } = mountWithStreaming(['conv-away']);
        await setStreaming(rerender, []);

        expect(send).toHaveBeenCalledTimes(1);
        expect(send.mock.calls[0][0]).toMatchObject({
            conversationId: 'conv-away',
            question: 'use the other index',
        });
    });

    it('does not wait for the user to navigate back', async () => {
        recordSentFeedback('conv-away', 'note');
        const { rerender } = mountWithStreaming(['conv-away']);
        // Viewed conversation never changes to conv-away.
        await setStreaming(rerender, []);
        expect(activeChat.currentConversationId).toBe('conv-viewed');
        expect(send).toHaveBeenCalledTimes(1);
    });

    it('builds history from that conversation, not the viewed one', async () => {
        recordSentFeedback('conv-away', 'follow-up');
        const { rerender } = mountWithStreaming(['conv-away']);
        await setStreaming(rerender, []);

        const messages = send.mock.calls[0][0].messages;
        expect(messages.map((m: any) => m.content)).toEqual([
            'original question',
            'the response',
            'follow-up',
        ]);
    });

    it('marks the send as not streaming to the current conversation', async () => {
        recordSentFeedback('conv-away', 'note');
        const { rerender } = mountWithStreaming(['conv-away']);
        await setStreaming(rerender, []);
        expect(send.mock.calls[0][0].isStreamingToCurrentConversation).toBe(false);
    });

    it('adds the message and the streaming marker to that conversation', async () => {
        recordSentFeedback('conv-away', 'note');
        const { rerender } = mountWithStreaming(['conv-away']);
        await setStreaming(rerender, []);
        expect(activeChat.addMessageToConversation).toHaveBeenCalledWith(
            expect.objectContaining({ role: 'human', content: 'note' }),
            'conv-away',
        );
        expect(activeChat.addStreamingConversation).toHaveBeenCalledWith('conv-away');
    });
});

describe('recovery for the viewed conversation', () => {
    it('still resubmits, and marks the stream as current', async () => {
        activeChat.currentConversationId = 'conv-away';
        recordSentFeedback('conv-away', 'note');
        const { rerender } = mountWithStreaming(['conv-away']);
        await setStreaming(rerender, []);
        expect(send).toHaveBeenCalledTimes(1);
        expect(send.mock.calls[0][0].isStreamingToCurrentConversation).toBe(true);
    });
});

describe('cases that must NOT resubmit', () => {
    it('sends nothing when no feedback was retained (negative control)', async () => {
        const { rerender } = mountWithStreaming(['conv-away']);
        await setStreaming(rerender, []);
        expect(send).not.toHaveBeenCalled();
    });

    it('sends nothing once the delivery ack has retired the text', async () => {
        recordSentFeedback('conv-away', 'delivered fine');
        retireDeliveredFeedback('conv-away', 'delivered fine');
        const { rerender } = mountWithStreaming(['conv-away']);
        await setStreaming(rerender, []);
        expect(send).not.toHaveBeenCalled();
    });

    it('sends nothing while the turn is still streaming', async () => {
        recordSentFeedback('conv-away', 'note');
        const { rerender } = mountWithStreaming(['conv-away']);
        await setStreaming(rerender, ['conv-away']);
        expect(send).not.toHaveBeenCalled();
        expect(hasRetainedFeedback('conv-away')).toBe(true);
    });

    it('does not resubmit another conversation\'s feedback when one ends', async () => {
        recordSentFeedback('conv-away', 'for away');
        const { rerender } = mountWithStreaming(['conv-away', 'conv-viewed']);
        await setStreaming(rerender, ['conv-away']);   // only conv-viewed ended
        expect(send).not.toHaveBeenCalled();
        expect(hasRetainedFeedback('conv-away')).toBe(true);
    });

    it('drops retained text when the conversation no longer exists', async () => {
        recordSentFeedback('conv-deleted', 'orphaned');
        const { rerender } = mountWithStreaming(['conv-deleted']);
        await setStreaming(rerender, []);
        expect(send).not.toHaveBeenCalled();
        expect(hasRetainedFeedback('conv-deleted')).toBe(false);
    });
});

describe('duplication guards', () => {
    it('resubmits once even if the same turn end is observed twice', async () => {
        recordSentFeedback('conv-away', 'only once');
        const { rerender } = mountWithStreaming(['conv-away']);
        await setStreaming(rerender, []);
        await setStreaming(rerender, []);
        expect(send).toHaveBeenCalledTimes(1);
    });

    it('joins several retained texts into a single turn', async () => {
        recordSentFeedback('conv-away', 'first point');
        recordSentFeedback('conv-away', 'second point');
        const { rerender } = mountWithStreaming(['conv-away']);
        await setStreaming(rerender, []);
        expect(send).toHaveBeenCalledTimes(1);
        expect(send.mock.calls[0][0].question).toBe('first point\n\nsecond point');
    });

    it('excludes muted messages from the recovered history', async () => {
        convList.conversations[0].messages = [
            { role: 'human', content: 'kept' },
            { role: 'assistant', content: 'muted detour', muted: true },
        ];
        recordSentFeedback('conv-away', 'note');
        const { rerender } = mountWithStreaming(['conv-away']);
        await setStreaming(rerender, []);
        expect(send.mock.calls[0][0].messages.map((m: any) => m.content))
            .toEqual(['kept', 'note']);
    });
});

describe('chip notification', () => {
    it('announces the resubmission with its conversation id', async () => {
        const seen: any[] = [];
        const handler = (e: Event) => seen.push((e as CustomEvent).detail);
        document.addEventListener(FEEDBACK_RESUBMITTED_EVENT, handler);
        try {
            recordSentFeedback('conv-away', 'note');
            const { rerender } = mountWithStreaming(['conv-away']);
            await setStreaming(rerender, []);
        } finally {
            document.removeEventListener(FEEDBACK_RESUBMITTED_EVENT, handler);
        }
        expect(seen).toEqual([{ conversationId: 'conv-away', texts: ['note'] }]);
    });
});

describe('send failure', () => {
    it('clears the streaming marker so the conversation is not stuck', async () => {
        send.mockRejectedValueOnce(new Error('network down'));
        recordSentFeedback('conv-away', 'note');
        const { rerender } = mountWithStreaming(['conv-away']);
        await setStreaming(rerender, []);
        expect(activeChat.removeStreamingConversation).toHaveBeenCalledWith('conv-away');
    });
});
