/**
 * @jest-environment jsdom
 *
 * PIN: an in-progress message edit survives a background conversation
 * refresh.
 *
 * Observed defect (live session, 2026-09-30): editing the first message of a
 * conversation, the textarea reset to the stored content every ~30s, so no
 * edit could be completed.  ChatContext's `syncWithServer` runs on a 30s
 * interval and rewrites the active conversation's message array (a new array
 * identity, usually identical content).  EditSection's draft-initialisation
 * effect listed `currentMessages` in its dependencies, so every tick re-ran
 * it and clobbered `editedMessage` with `currentMessages[index].content`.
 *
 * The draft must be seeded when an edit session STARTS (isEditing flips on,
 * or the target index / conversation changes), never merely because the
 * message array was replaced.
 */
jest.mock('../../services/modelCapabilitiesService', () => ({
    modelCapabilitiesService: {
        invalidateCache: () => {},
        getCapabilities: async () => ({ supports_vision: false }),
    },
}));
jest.mock('../../hooks/useSendPayload', () => ({ useSendPayload: () => ({ send: async () => '' }) }));
jest.mock('../../api/conversationSyncApi', () => ({
    messageLocator: () => ({}),
    ownerProjectId: () => 'p1',
    rewindMessages: async () => {},
    patchMessage: async () => {},
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

// Mutable stand-in for ActiveChatContext so the test can swap the message
// array (as the sync tick does) without remounting the component.
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
    replaceMessages: () => {},
};
jest.mock('../../context/ActiveChatContext', () => ({
    useActiveChat: () => mockActiveChat,
}));

import React from 'react';
import { render, fireEvent, act } from '@testing-library/react';
import { EditSection } from '../EditSection';

(global as any).ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
if (!window.matchMedia) {
    (window as any).matchMedia = (q: string) => ({
        matches: false, media: q, onchange: null,
        addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {}, dispatchEvent() { return false; },
    });
}

const msgs = (content: string) => [
    { id: 'm1', role: 'human', content },
    { id: 'm2', role: 'assistant', content: 'reply' },
];

const textarea = (container: HTMLElement) =>
    container.querySelector('textarea') as HTMLTextAreaElement;

describe('EditSection draft survives a background message-array refresh', () => {
    beforeEach(() => {
        mockActiveChat.currentMessages = msgs('original question');
        mockActiveChat.currentConversationId = 'conv-1';
        mockActiveChat.editingMessageIndex = 0;
    });

    it('seeds the textarea from the stored message when editing starts (positive control)', async () => {
        const { container } = render(<EditSection index={0} />);
        await act(async () => {});
        expect(textarea(container).value).toBe('original question');
    });

    it('keeps the typed draft when currentMessages is replaced by an identical array (the 30s sync tick)', async () => {
        const { container, rerender } = render(<EditSection index={0} />);
        await act(async () => {});
        fireEvent.change(textarea(container), { target: { value: 'my half-typed edit' } });
        expect(textarea(container).value).toBe('my half-typed edit');

        // What syncWithServer's setConversations(prev => prev.map(...)) does:
        // same content, new array identity.
        mockActiveChat.currentMessages = msgs('original question');
        rerender(<EditSection index={0} />);
        await act(async () => {});
        expect(textarea(container).value).toBe('my half-typed edit');

        // And when the server copy differs (an earlier edit landed there),
        // the draft in progress still wins.
        mockActiveChat.currentMessages = msgs('server-side older text');
        rerender(<EditSection index={0} />);
        await act(async () => {});
        expect(textarea(container).value).toBe('my half-typed edit');
    });

    it('re-seeds when the edit session restarts (cancel, then edit again)', async () => {
        const { container, rerender } = render(<EditSection index={0} />);
        await act(async () => {});
        fireEvent.change(textarea(container), { target: { value: 'abandoned draft' } });

        mockActiveChat.editingMessageIndex = null;
        rerender(<EditSection index={0} />);
        expect(textarea(container)).toBeNull();

        mockActiveChat.currentMessages = msgs('fresh stored content');
        mockActiveChat.editingMessageIndex = 0;
        rerender(<EditSection index={0} />);
        await act(async () => {});
        expect(textarea(container).value).toBe('fresh stored content');
    });

    it('re-seeds when the conversation changes while an editor is mounted', async () => {
        const { container, rerender } = render(<EditSection index={0} />);
        await act(async () => {});
        fireEvent.change(textarea(container), { target: { value: 'draft for conv-1' } });

        mockActiveChat.currentConversationId = 'conv-2';
        mockActiveChat.currentMessages = msgs('conv-2 first message');
        rerender(<EditSection index={0} />);
        await act(async () => {});
        expect(textarea(container).value).toBe('conv-2 first message');
    });
});
