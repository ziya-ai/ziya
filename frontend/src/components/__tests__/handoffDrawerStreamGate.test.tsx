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

const streaming = new Set<string>();
const activeChat: any = {
    loadConversation: jest.fn(),
    currentConversationId: 'c1',
    get isStreaming() { return streaming.size > 0; },   // ChatContext's definition
    streamingConversations: streaming,
    addMessageToConversation: jest.fn(),
    currentMessages: [],
};
jest.mock('../../context/ActiveChatContext', () => ({ useActiveChat: () => activeChat }));
jest.mock('../../context/ConversationListContext', () => ({
    useConversationList: () => ({ conversations: [{ id: 'c1', projectId: 'P' }], setConversations: jest.fn() }),
}));
jest.mock('../../context/ThemeContext', () => ({ useTheme: () => ({ isDarkMode: false }) }));
jest.mock('../../hooks/useHandoffDraftRequest', () => ({ useHandoffDraftRequest: () => jest.fn() }));
jest.mock('../../api/handoffApi', () => ({
    getHandoffState: jest.fn(async () => ({ title: 'T', messageCount: 4, draft: null, additionalFiles: [] })),
}));
jest.mock('../../utils/handoffDraftModel', () => ({
    DRAFT_TIER_LABELS: {}, DRAFT_TIER_ORDER: [],
    fetchModelTiers: jest.fn(async () => ({ tiers: [] })),
    readDraftTier: () => 'medium', writeDraftTier: jest.fn(),
}));

import HandoffDrawer from '../HandoffDrawer';

const button = async () => (await screen.findByText('Ask the model to draft it')).closest('button')!;

beforeEach(() => streaming.clear());

it('another conversation streaming does not block the draft request', async () => {
    streaming.add('other-conversation');
    render(<HandoffDrawer conversationId="c1" onClose={() => {}} />);
    expect(await button()).toBeEnabled();
});

it('this conversation streaming does block it', async () => {
    streaming.add('c1');
    render(<HandoffDrawer conversationId="c1" onClose={() => {}} />);
    expect(await button()).toBeDisabled();
});

it('idle: enabled', async () => {
    render(<HandoffDrawer conversationId="c1" onClose={() => {}} />);
    expect(await button()).toBeEnabled();
});
