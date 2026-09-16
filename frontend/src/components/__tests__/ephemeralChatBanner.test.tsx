/**
 * EphemeralChatBanner — the in-window indicator that the ACTIVE conversation
 * is ephemeral (React-state only, never persisted).
 *
 * Before this banner the only cue was an italic "· ephemeral" suffix on the
 * row in the chat list, which is invisible with the sidebar collapsed.
 *
 * These tests mount the real ActiveChatProvider and ConversationListProvider
 * (both are pass-through slice providers) rather than mocking the hooks, so
 * the seam under test is the actual one: currentConversationId is resolved
 * against conversations[] and the isEphemeral flag on THAT record decides
 * whether the banner renders. A mocked hook returning `isEphemeral: true`
 * directly would pass even if the component looked at the wrong record.
 */
import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { ActiveChatProvider, ActiveChatContextValue } from '../../context/ActiveChatContext';
import { ConversationListProvider, ConversationListContextValue } from '../../context/ConversationListContext';
import { EphemeralChatBanner } from '../EphemeralChatBanner';
import type { Conversation } from '../../utils/types';

const conv = (id: string, extra: Partial<Conversation> = {}): Conversation => ({
    id,
    title: id,
    messages: [],
    lastAccessedAt: 1,
    isActive: true,
    ...extra,
} as Conversation);

function mount(
    conversations: Conversation[],
    currentConversationId: string,
    promote: jest.Mock = jest.fn().mockResolvedValue(undefined),
) {
    const active = {
        currentConversationId,
        promoteEphemeralToRetained: promote,
    } as unknown as ActiveChatContextValue;
    const list = { conversations } as unknown as ConversationListContextValue;
    const utils = render(
        <ConversationListProvider {...list}>
            <ActiveChatProvider {...active}>
                <EphemeralChatBanner />
            </ActiveChatProvider>
        </ConversationListProvider>,
    );
    return { ...utils, promote };
}

describe('EphemeralChatBanner', () => {
    test('renders for the active ephemeral conversation', () => {
        mount([conv('eph', { isEphemeral: true })], 'eph');
        const banner = screen.getByTestId('ephemeral-chat-banner');
        expect(banner).toHaveClass('ephemeral-chat-banner');
        expect(banner).toHaveTextContent(/ephemeral chat/i);
        expect(banner).toHaveTextContent(/not saved/i);
        expect(screen.getByRole('button', { name: /keep this chat/i })).toBeInTheDocument();
    });

    test('renders nothing for a persisted conversation', () => {
        mount([conv('normal')], 'normal');
        expect(screen.queryByTestId('ephemeral-chat-banner')).toBeNull();
    });

    test('keys off the ACTIVE record, not any ephemeral in the list', () => {
        // An ephemeral exists elsewhere in the list but the active chat is
        // persisted; the banner must stay hidden.
        mount([conv('eph', { isEphemeral: true }), conv('normal')], 'normal');
        expect(screen.queryByTestId('ephemeral-chat-banner')).toBeNull();
    });

    test('renders nothing when there is no active conversation', () => {
        mount([conv('eph', { isEphemeral: true })], '');
        expect(screen.queryByTestId('ephemeral-chat-banner')).toBeNull();
    });

    test('Keep this chat calls promoteEphemeralToRetained with the active id', async () => {
        const { promote } = mount([conv('eph', { isEphemeral: true })], 'eph');
        fireEvent.click(screen.getByRole('button', { name: /keep this chat/i }));
        await waitFor(() => expect(promote).toHaveBeenCalledTimes(1));
        expect(promote).toHaveBeenCalledWith('eph');
    });

    test('disappears once the record is promoted (isEphemeral cleared)', () => {
        const ephemeral = [conv('eph', { isEphemeral: true })];
        const { rerender } = mount(ephemeral, 'eph');
        expect(screen.getByTestId('ephemeral-chat-banner')).toBeInTheDocument();

        // Simulate what promoteEphemeralToRetained does to state: same id,
        // flag removed.
        const promoted = [conv('eph')];
        const active = { currentConversationId: 'eph', promoteEphemeralToRetained: jest.fn() } as unknown as ActiveChatContextValue;
        rerender(
            <ConversationListProvider {...({ conversations: promoted } as unknown as ConversationListContextValue)}>
                <ActiveChatProvider {...active}>
                    <EphemeralChatBanner />
                </ActiveChatProvider>
            </ConversationListProvider>,
        );
        expect(screen.queryByTestId('ephemeral-chat-banner')).toBeNull();
    });
});
