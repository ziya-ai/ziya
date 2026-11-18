/**
 * ChatTurnReattachWatcher — the seam between "decided to reattach" and the
 * rest of the UI knowing a turn is live in this tab.
 *
 * Two defects this pins:
 *
 * 1. A reattach called send({ reattach: true }) without marking the
 *    conversation as streaming.  The reattached tab showed text with no
 *    Stop button or streaming outline, did not answer other tabs' probes,
 *    and at turn end posted no 'streaming-ended' (removeStreamingConversation
 *    returns early for an id that was never in the set).  Tabs that had been
 *    mirroring the turn before the owner reloaded kept the streaming mark
 *    for the life of the page.
 *
 * 2. When the owning tab went away, mirroring tabs were never told and
 *    nobody took the turn over.  ChatContext now relays the owner's pagehide
 *    as CHAT_TURN_ORPHANED_EVENT; the watcher reclaims a turn that is still
 *    running.
 *
 * Renders the real component through mocked context modules, as
 * feedbackRecoveryWatcher.test.tsx does.
 */
import React from 'react';
import { render, act } from '@testing-library/react';

const mockActiveChat = {
    streamingConversations: new Set<string>(),
    currentConversationId: 'viewed',
    currentMessages: [] as any[],
    addStreamingConversation: jest.fn(),
    removeStreamingConversation: jest.fn(),
};
const mockConvList = { conversations: [] as any[], hasLoadedConversations: true };
const mockSend = jest.fn();

jest.mock('../../context/ActiveChatContext', () => ({
    useActiveChat: () => mockActiveChat,
}));
jest.mock('../../context/ConversationListContext', () => ({
    useConversationList: () => mockConvList,
}));
jest.mock('../../hooks/useSendPayload', () => ({
    useSendPayload: () => ({ send: mockSend }),
}));
jest.mock('../../utils/projectSync', () => ({
    projectSync: { post: jest.fn() },
}));
jest.mock('../../utils/chatTurnReattach', () => {
    const actual = jest.requireActual('../../utils/chatTurnReattach');
    return {
        ...actual,
        fetchChatTurnStatus: jest.fn(),
        fetchChatTurnStatuses: jest.fn(),
        probeOtherTabs: jest.fn(() => Promise.resolve()),
    };
});

import { ChatTurnReattachWatcher } from '../ChatTurnReattachWatcher';
import * as reattach from '../../utils/chatTurnReattach';

const fetchStatus = reattach.fetchChatTurnStatus as jest.Mock;
const fetchStatuses = reattach.fetchChatTurnStatuses as jest.Mock;

const human = (content = 'q') => ({ role: 'human', content, _timestamp: Date.now() });
const status = (id: string, over: Record<string, unknown> = {}) => ({
    conversation_id: id, turn_id: `turn-${id}`, active: true, held: false,
    cancelled: false, error: null, started_at: Date.now() / 1000,
    finished_at: null, subscribers: 0, buffered_frames: 3, dropped_frames: 0,
    ...over,
});

const wait = (ms: number) => act(async () => {
    await new Promise(resolve => setTimeout(resolve, ms));
});

async function mount() {
    let utils: ReturnType<typeof render>;
    await act(async () => { utils = render(<ChatTurnReattachWatcher />); });
    await wait(20);
    return utils!;
}

const ORPHAN_WAIT_MS = 500;   // base delay 300 ms; jitter pinned to 0 below

beforeEach(() => {
    jest.clearAllMocks();
    mockActiveChat.streamingConversations = new Set();
    mockActiveChat.currentConversationId = 'viewed';
    mockActiveChat.currentMessages = [human()];
    mockConvList.conversations = [];
    mockConvList.hasLoadedConversations = true;
    mockSend.mockReset().mockResolvedValue('ok');
    fetchStatus.mockReset().mockResolvedValue(null);
    fetchStatuses.mockReset().mockResolvedValue([]);
    jest.spyOn(Math, 'random').mockReturnValue(0);
});

afterEach(() => { (Math.random as jest.Mock).mockRestore?.(); });

describe('a reattach marks the conversation as streaming', () => {
    it('viewed conversation: marked before the reattach send', async () => {
        fetchStatus.mockResolvedValue(status('viewed'));
        await mount();
        expect(mockSend).toHaveBeenCalledTimes(1);
        expect(mockSend.mock.calls[0][0]).toMatchObject({ conversationId: 'viewed', reattach: true });
        expect(mockActiveChat.addStreamingConversation).toHaveBeenCalledWith('viewed');
        expect(mockActiveChat.addStreamingConversation.mock.invocationCallOrder[0])
            .toBeLessThan(mockSend.mock.invocationCallOrder[0]);
        expect(mockActiveChat.removeStreamingConversation).not.toHaveBeenCalled();
    });

    it('viewed conversation: a failed reattach releases the mark', async () => {
        fetchStatus.mockResolvedValue(status('viewed'));
        mockSend.mockRejectedValue(new Error('stream 404'));
        await mount();
        expect(mockActiveChat.addStreamingConversation).toHaveBeenCalledWith('viewed');
        expect(mockActiveChat.removeStreamingConversation).toHaveBeenCalledWith('viewed');
    });

    it('nothing to reattach: no mark, and none released', async () => {
        await mount();
        expect(fetchStatus).toHaveBeenCalledWith('viewed');     // the path ran
        expect(mockSend).not.toHaveBeenCalled();
        expect(mockActiveChat.addStreamingConversation).not.toHaveBeenCalled();
        expect(mockActiveChat.removeStreamingConversation).not.toHaveBeenCalled();
    });

    it('a mirrored conversation is neither reattached nor has its mark removed on a status failure', async () => {
        // The mark here belongs to another tab's stream.  Removing it would
        // broadcast a false streaming-ended.
        mockActiveChat.streamingConversations = new Set(['viewed']);
        fetchStatus.mockRejectedValue(new Error('network'));
        await mount();
        expect(fetchStatus).toHaveBeenCalled();
        expect(mockSend).not.toHaveBeenCalled();
        expect(mockActiveChat.removeStreamingConversation).not.toHaveBeenCalled();
    });

    it('reload sweep: a background turn is marked before its send', async () => {
        mockConvList.conversations = [{ id: 'bg', messages: [human()] }];
        fetchStatuses.mockResolvedValue([status('bg')]);
        await mount();
        await wait(350);                                        // the sweep's probe window
        expect(mockSend).toHaveBeenCalledTimes(1);
        expect(mockSend.mock.calls[0][0]).toMatchObject({
            conversationId: 'bg', reattach: true, isStreamingToCurrentConversation: false,
        });
        expect(mockActiveChat.addStreamingConversation).toHaveBeenCalledWith('bg');
        expect(mockActiveChat.addStreamingConversation.mock.invocationCallOrder[0])
            .toBeLessThan(mockSend.mock.invocationCallOrder[0]);
    });

    it('reload sweep: a failed send releases the mark', async () => {
        mockConvList.conversations = [{ id: 'bg', messages: [human()] }];
        fetchStatuses.mockResolvedValue([status('bg')]);
        mockSend.mockRejectedValue(new Error('boom'));
        await mount();
        await wait(350);
        expect(mockActiveChat.removeStreamingConversation).toHaveBeenCalledWith('bg');
    });
});

describe('a turn whose owning tab went away is reclaimed', () => {
    const orphan = (id: string) => act(async () => {
        window.dispatchEvent(new CustomEvent(
            (reattach as any).CHAT_TURN_ORPHANED_EVENT, { detail: { conversationId: id } }));
    });

    it('the event name is exported (ChatContext dispatches the same constant)', () => {
        expect(typeof (reattach as any).CHAT_TURN_ORPHANED_EVENT).toBe('string');
    });

    it('a still-running turn is marked and reattached', async () => {
        mockConvList.conversations = [{ id: 'bg', messages: [human('bg q')] }];
        await mount();
        fetchStatus.mockResolvedValue(status('bg'));
        await orphan('bg');
        expect(mockSend).not.toHaveBeenCalled();                    // not before the delay
        await wait(ORPHAN_WAIT_MS);
        expect(mockSend).toHaveBeenCalledTimes(1);
        expect(mockSend.mock.calls[0][0]).toMatchObject({
            conversationId: 'bg', reattach: true, question: 'bg q',
            isStreamingToCurrentConversation: false,
        });
        expect(mockActiveChat.addStreamingConversation).toHaveBeenCalledWith('bg');
        expect(mockActiveChat.addStreamingConversation.mock.invocationCallOrder[0])
            .toBeLessThan(mockSend.mock.invocationCallOrder[0]);
    });

    it('the viewed conversation is reclaimed with its live messages, inline', async () => {
        await mount();
        fetchStatus.mockResolvedValue(status('viewed'));
        mockSend.mockClear();
        mockActiveChat.addStreamingConversation.mockClear();
        // mount() already reattached 'viewed' once only if a status existed;
        // it did not (null), so this turn id is new to the watcher.
        await orphan('viewed');
        await wait(ORPHAN_WAIT_MS);
        expect(mockSend).toHaveBeenCalledTimes(1);
        expect(mockSend.mock.calls[0][0]).toMatchObject({
            conversationId: 'viewed', reattach: true, isStreamingToCurrentConversation: true,
        });
    });

    it('a finished turn is left alone', async () => {
        mockConvList.conversations = [{ id: 'bg', messages: [human()] }];
        await mount();
        fetchStatus.mockResolvedValue(status('bg', { active: false, finished_at: Date.now() / 1000 }));
        await orphan('bg');
        await wait(ORPHAN_WAIT_MS);
        expect(fetchStatus).toHaveBeenCalledWith('bg');         // the path ran
        expect(mockSend).not.toHaveBeenCalled();
        expect(mockActiveChat.addStreamingConversation).not.toHaveBeenCalled();
    });

    it('another tab that claimed it first wins', async () => {
        mockConvList.conversations = [{ id: 'bg', messages: [human()] }];
        const utils = await mount();
        fetchStatus.mockResolvedValue(status('bg'));
        await orphan('bg');
        // The other tab's streaming-state lands during the delay.
        mockActiveChat.streamingConversations = new Set(['bg']);
        await act(async () => { utils.rerender(<ChatTurnReattachWatcher />); });
        await wait(ORPHAN_WAIT_MS);
        expect(mockSend).not.toHaveBeenCalled();
        expect(mockActiveChat.addStreamingConversation).not.toHaveBeenCalled();
        expect(mockActiveChat.removeStreamingConversation).not.toHaveBeenCalled();
    });

    it('unmount cancels a pending reclaim', async () => {
        mockConvList.conversations = [{ id: 'bg', messages: [human()] }];
        const utils = await mount();
        fetchStatus.mockResolvedValue(status('bg'));
        await orphan('bg');
        utils.unmount();
        await wait(ORPHAN_WAIT_MS);
        expect(mockSend).not.toHaveBeenCalled();
    });
});
