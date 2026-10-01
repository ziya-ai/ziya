/**
 * @jest-environment jsdom
 *
 * PIN: "Save" (edit in place, no resubmit) applies the edit locally at once
 * and then WAITS for the server PATCH, telling the user when it did not land.
 *
 * Before: `handleSave` fired `patchMessage` with `void` and returned.  A
 * failed PATCH (server restart, timeout, 5xx) was swallowed — the browser
 * showed the new text, the server kept the old one, and the old text came
 * back in any other browser, in `chat_read`, in exports, and after a full
 * fetch.  `handleSubmit` already awaits its `rewindMessages` and logs a
 * failure; the in-place path had no such check.
 *
 * A 404 (chat not yet on the server) is still silent: there is nothing to
 * repair and bulk-sync will carry the message up with its edited content.
 */
jest.mock('../../services/modelCapabilitiesService', () => ({
    modelCapabilitiesService: {
        invalidateCache: () => {},
        getCapabilities: async () => ({ supports_vision: false }),
    },
}));
jest.mock('../../hooks/useSendPayload', () => ({ useSendPayload: () => ({ send: async () => '' }) }));

const mockPatchMessage = jest.fn();
jest.mock('../../api/conversationSyncApi', () => ({
    messageLocator: (msgs: any[], i: number) => ({ messageId: msgs[i]?.id }),
    ownerProjectId: () => 'p1',
    rewindMessages: async () => ({ ok: true }),
    patchMessage: (...args: any[]) => mockPatchMessage(...args),
}));
jest.mock('../../context/ConversationListContext', () => ({
    useConversationList: () => ({ setConversations: () => {}, conversations: [] }),
}));
jest.mock('../../context/FolderContext', () => ({
    useFolderContext: () => ({ checkedKeys: [] }),
}));
jest.mock('../../context/ProjectContext', () => ({
    useProject: () => ({ currentProject: { id: 'p1', name: 'p1', path: '/p1' } }),
}));

const mockReplaceMessages = jest.fn();
const mockActiveChat: any = {
    currentMessages: [],
    currentConversationId: 'conv-1',
    editingMessageIndex: 0,
    streamingConversations: new Set(),
    addMessageToConversation: () => {},
    setIsStreaming: () => {},
    addStreamingConversation: () => {},
    setStreamedContentMap: () => {},
    setEditingMessageIndex: (i: number | null) => { mockActiveChat.editingMessageIndex = i; },
    removeStreamingConversation: () => {},
    replaceMessages: (...args: any[]) => mockReplaceMessages(...args),
};
jest.mock('../../context/ActiveChatContext', () => ({
    useActiveChat: () => mockActiveChat,
}));

import React from 'react';
import { render, fireEvent, act, screen } from '@testing-library/react';
import { message } from 'antd';
import { EditSection } from '../EditSection';

(global as any).ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
if (!window.matchMedia) {
    (window as any).matchMedia = (q: string) => ({
        matches: false, media: q, onchange: null,
        addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {}, dispatchEvent() { return false; },
    });
}

const msgs = () => [
    { id: 'm1', role: 'human', content: 'original question' },
    { id: 'm2', role: 'assistant', content: 'reply' },
];

/** Resolve-later PATCH so the test can observe ordering. */
function deferredPatch() {
    let resolve!: (v: any) => void;
    const promise = new Promise<any>(r => { resolve = r; });
    mockPatchMessage.mockReturnValueOnce(promise);
    return { resolve };
}

async function editAndSave() {
    const { container } = render(<EditSection index={0} />);
    await act(async () => {});
    fireEvent.change(container.querySelector('textarea')!, { target: { value: 'edited text' } });
    fireEvent.click(screen.getByText('Save'));
    return container;
}

describe('EditSection Save: local edit is immediate, server failure is reported', () => {
    let warn: jest.SpyInstance;
    beforeEach(() => {
        mockActiveChat.currentMessages = msgs();
        mockActiveChat.editingMessageIndex = 0;
        mockPatchMessage.mockReset();
        mockReplaceMessages.mockReset();
        warn = jest.spyOn(message, 'warning').mockImplementation((() => {}) as any);
    });
    afterEach(() => warn.mockRestore());

    it('applies the edit locally BEFORE the PATCH resolves (no wait on the network)', async () => {
        const { resolve } = deferredPatch();
        await editAndSave();
        expect(mockReplaceMessages).toHaveBeenCalledTimes(1);
        expect(mockReplaceMessages.mock.calls[0][0]).toBe('conv-1');
        const updater = mockReplaceMessages.mock.calls[0][1];
        expect(updater(msgs())[0].content).toBe('edited text');
        expect(mockPatchMessage).toHaveBeenCalledWith('p1', 'conv-1', { messageId: 'm1' },
            { content: 'edited text', images: null });
        await act(async () => { resolve({ ok: true }); });
        expect(warn).not.toHaveBeenCalled();
    });

    it('warns the user when the server PATCH fails (the previously swallowed case)', async () => {
        mockPatchMessage.mockResolvedValueOnce({ ok: false, status: 500 });
        await editAndSave();
        await act(async () => {});
        expect(warn).toHaveBeenCalledTimes(1);
        expect(String(warn.mock.calls[0][0])).toMatch(/server/i);
        // The local edit is NOT rolled back: the user's text stays on screen.
        expect(mockReplaceMessages).toHaveBeenCalledTimes(1);
    });

    it('stays silent on 404 (chat not yet synced; bulk-sync will carry the edit)', async () => {
        mockPatchMessage.mockResolvedValueOnce({ ok: false, status: 404 });
        await editAndSave();
        await act(async () => {});
        expect(warn).not.toHaveBeenCalled();
    });

    it('does not warn twice or apply twice for one save', async () => {
        mockPatchMessage.mockResolvedValueOnce({ ok: false, status: 0 });
        await editAndSave();
        await act(async () => {});
        expect(mockPatchMessage).toHaveBeenCalledTimes(1);
        expect(warn).toHaveBeenCalledTimes(1);
    });
});
