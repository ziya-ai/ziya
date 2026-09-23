/**
 * @jest-environment jsdom
 *
 * HandoffBanner — the in-conversation half of the handoff trail
 * (design/conversation-handoff.md).  Asserts on what the user sees:
 *
 *  - an ordinary conversation renders nothing;
 *  - a SOURCE (handedOffTo) shows the "continued in" notice with the child's
 *    title as a navigable link, and says it is still usable (not locked);
 *  - a CONTINUATION (lineageKind=handoff + handoff doc) shows the card, links
 *    back to the source, and reveals the document on demand;
 *  - saving an edit calls the API and reports the new document upward so
 *    the record updates without a refetch.
 */
import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom';

jest.mock('../../context/ThemeContext', () => ({
    useTheme: () => ({ isDarkMode: false }),
}));
jest.mock('../../api/handoffApi', () => ({
    editInheritedHandoff: jest.fn(),
}));

import HandoffBanner from '../HandoffBanner';
import * as handoffApi from '../../api/handoffApi';

const convs = [
    { id: 'src', title: 'ISL BFD flap', handedOffTo: 'cont' },
    {
        id: 'cont', title: 'ISL BFD flap (2)', lineageKind: 'handoff', branchedFrom: 'src',
        handoff: { document: '## Objective\nroot-cause the flap', sourceMessageCount: 62 },
    },
    { id: 'plain', title: 'Unrelated' },
];

describe('HandoffBanner', () => {
    test('plain conversation renders nothing', () => {
        const { container } = render(
            <HandoffBanner conversation={convs[2]} conversations={convs} onNavigate={() => {}} />);
        expect(container).toBeEmptyDOMElement();
    });

    test('source shows the forward link and is described as still usable', () => {
        const nav = jest.fn();
        render(<HandoffBanner conversation={convs[0]} conversations={convs} onNavigate={nav} />);
        const banner = screen.getByTestId('handoff-source-banner');
        expect(banner).toHaveTextContent('continued in');
        expect(banner).toHaveTextContent('still works');
        fireEvent.click(screen.getByText('ISL BFD flap (2)'));
        expect(nav).toHaveBeenCalledWith('cont');
        expect(screen.queryByTestId('handoff-card')).toBeNull();
    });

    test('continuation shows the card, links back, reveals the document', () => {
        const nav = jest.fn();
        render(<HandoffBanner conversation={convs[1]} conversations={convs} onNavigate={nav} />);
        const card = screen.getByTestId('handoff-card');
        expect(card).toHaveTextContent('62 messages there');
        fireEvent.click(screen.getByText('ISL BFD flap'));
        expect(nav).toHaveBeenCalledWith('src');
        expect(screen.queryByText(/root-cause the flap/)).toBeNull();
        fireEvent.click(screen.getByText('Show handoff'));
        expect(screen.getByText(/root-cause the flap/)).toBeInTheDocument();
        expect(screen.queryByTestId('handoff-source-banner')).toBeNull();
    });

    test('saving an edit hits the API and reports upward', async () => {
        (handoffApi.editInheritedHandoff as jest.Mock).mockResolvedValue({
            ok: true, handoff: { document: 'v2', editedAt: 1 },
        });
        const saved = jest.fn();
        render(<HandoffBanner conversation={convs[1]} conversations={convs} onNavigate={() => {}}
            onDocumentSaved={saved} />);
        fireEvent.click(screen.getByText('Show handoff'));
        fireEvent.click(screen.getByText('Edit'));
        fireEvent.change(screen.getByRole('textbox'), { target: { value: 'v2' } });
        fireEvent.click(screen.getByText('Save'));
        await waitFor(() => expect(handoffApi.editInheritedHandoff).toHaveBeenCalledWith('cont', 'v2'));
        await waitFor(() => expect(saved).toHaveBeenCalledWith('cont', { document: 'v2', editedAt: 1 }));
    });
});
