/**
 * @jest-environment jsdom
 *
 * HandoffDrawer's "Ask the model to draft it" must be gated on THIS
 * conversation streaming, not on any conversation in the tab.
 *
 * `useActiveChat().isStreaming` is `streamingConversations.size > 0`
 * (ChatContext): a background turn in another conversation disabled the
 * button for hours with "Wait for the current response to finish" while the
 * conversation being handed off was idle.  FAILS against the drawer that
 * read `isStreaming`.
 */
import React from 'react';
import { render, screen } from '@testing-library/react';
import '@testing-library/jest-dom';

const mockStreaming = new Set<string>();
const mockActiveChat: any = {
    loadConversation: jest.fn(),
    currentConversationId: 'c1',
    get isStreaming() { return mockStreaming.size > 0; },   // ChatContext's definition
    streamingConversations: mockStreaming,
    addMessageToConversation: jest.fn(),
    currentMessages: [],
};
jest.mock('../../context/ActiveChatContext', () => ({ useActiveChat: () => mockActiveChat }));
jest.mock('../../hooks/useSendPayload', () => ({ useSendPayload: () => ({ send: jest.fn() }) }));
// uuid is ESM-only; stub so the drawer's import resolves under jest.
jest.mock('uuid', () => ({ v4: () => 'test-uuid' }));
// antd's Drawer portal throws under jsdom ("contains ... not of type 'Node'");
// render it inline — the gate under test is the button, not the portal.
jest.mock('antd', () => {
    const actual = jest.requireActual('antd');
    const R = require('react');
    return {
        ...actual,
        Drawer: ({ open, title, footer, children }: any) =>
            open ? R.createElement('div', null, title, children, footer) : null,
    };
});
jest.mock('../../context/ConversationListContext', () => ({
    useConversationList: () => ({ conversations: [{ id: 'c1', projectId: 'P' }], setConversations: jest.fn() }),
}));
jest.mock('../../context/ThemeContext', () => ({ useTheme: () => ({ isDarkMode: false }) }));
jest.mock('../../hooks/useHandoffDraftRequest', () => ({ useHandoffDraftRequest: () => jest.fn() }));
jest.mock('../../api/handoffApi', () => ({
    // Plain functions, not jest.fn: CRA's jest config resets mock
    // implementations before each test, which left these returning undefined
    // and the drawer throwing on mount.
    getHandoffState: async () => ({ title: 'T', messageCount: 4, draft: null, additionalFiles: [] }),
}));
jest.mock('../../utils/handoffDraftModel', () => ({
    DRAFT_TIER_LABELS: {}, DRAFT_TIER_ORDER: [],
    fetchModelTiers: async () => ({ tiers: [] }),
    readDraftTier: () => 'medium', writeDraftTier: jest.fn(),
}));

import HandoffDrawer from '../HandoffDrawer';
import { applyDiffWaitEvent, clearDiffWait } from '../../utils/diffWaitStore';

const button = async () => (await screen.findByText('Ask the model to draft it')).closest('button')!;

beforeEach(() => mockStreaming.clear());

it('another conversation streaming does not block the draft request', async () => {
    mockStreaming.add('other-conversation');
    render(<HandoffDrawer conversationId="c1" onClose={() => {}} />);
    expect(await button()).toBeEnabled();
});

it('this conversation streaming does block it', async () => {
    mockStreaming.add('c1');
    render(<HandoffDrawer conversationId="c1" onClose={() => {}} />);
    expect(await button()).toBeDisabled();
});

// A turn held for its diffs to be applied is still in streamingConversations,
// with no timer on the wait, but no model call is running: the draft request
// is sent as a background turn beside it (app/utils/background_turn.py).
// FAILS against the drawer that gated on streamingConversations alone.
it('this conversation waiting for its diffs to be applied does not block it', async () => {
    mockStreaming.add('c1');
    applyDiffWaitEvent('c1', { state: 'waiting', total: 2, remaining: 2, remaining_paths: ['a.py', 'b.py'] });
    try {
        render(<HandoffDrawer conversationId="c1" onClose={() => {}} />);
        expect(await button()).toBeEnabled();
    } finally {
        clearDiffWait('c1');
    }
});

it('another conversation waiting on diffs does not unblock this one mid-response', async () => {
    mockStreaming.add('c1');
    applyDiffWaitEvent('other-conversation', { state: 'waiting', total: 1, remaining: 1, remaining_paths: ['a.py'] });
    try {
        render(<HandoffDrawer conversationId="c1" onClose={() => {}} />);
        expect(await button()).toBeDisabled();
    } finally {
        clearDiffWait('other-conversation');
    }
});

it('idle: enabled', async () => {
    render(<HandoffDrawer conversationId="c1" onClose={() => {}} />);
    expect(await button()).toBeEnabled();
});
