/**
 * @jest-environment jsdom
 *
 * HandoffNotice — the local transcript row a handoff leaves on the SOURCE.
 * Previously it only ever offered "Review handoff", so after committing, the
 * last item of a handed-off segment said the model had drafted a document
 * and nothing about where the track went.  Asserts on what the user sees:
 *
 *  - before a continuation exists: "Review handoff" opens the drawer;
 *  - a 'drafted' notice on a source whose record has handedOffTo links
 *    forward to the child by title;
 *  - a 'handed_off' notice links forward from its own targetId even before
 *    the record's handedOffTo has synced, falling back to a generic label
 *    when the child title is unknown.
 */
import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import '@testing-library/jest-dom';

// `mock`-prefixed: jest.mock factories are hoisted above this line, and
// babel-jest only lets them reference identifiers with that prefix.
const mockDispatchHandoffOpen = jest.fn();
jest.mock('../../utils/handoffOpen', () => ({
    dispatchHandoffOpen: (...a: any[]) => mockDispatchHandoffOpen(...a),
}));

import HandoffNotice, { resolveHandoffForward } from '../HandoffNotice';

const drafted = {
    id: 'n1', role: 'system' as const, muted: true,
    content: 'The model drafted a handoff document for this conversation.',
    handoffNotice: { kind: 'drafted' as const },
};

beforeEach(() => jest.clearAllMocks());

describe('HandoffNotice', () => {
    test('not yet handed off: offers Review handoff, no forward link', () => {
        const onNavigate = jest.fn();
        render(<HandoffNotice message={drafted} conversationId="src"
            conversations={[{ id: 'src', title: 'ISL BFD flap' }]} onNavigate={onNavigate} />);
        expect(screen.getByText(drafted.content)).toBeInTheDocument();
        expect(screen.queryByTestId('handoff-notice-forward')).toBeNull();
        fireEvent.click(screen.getByText('Review handoff'));
        expect(mockDispatchHandoffOpen).toHaveBeenCalledWith('src');
        expect(onNavigate).not.toHaveBeenCalled();
    });

    test('drafted notice on a handed-off source links forward to the child by title', () => {
        const onNavigate = jest.fn();
        render(<HandoffNotice message={drafted} conversationId="src"
            conversations={[
                { id: 'src', title: 'ISL BFD flap', handedOffTo: 'cont' },
                { id: 'cont', title: 'ISL BFD flap (2)' },
            ]} onNavigate={onNavigate} />);
        const fwd = screen.getByTestId('handoff-notice-forward');
        expect(fwd).toHaveTextContent('ISL BFD flap (2)');
        expect(screen.queryByText('Review handoff')).toBeNull();
        fireEvent.click(fwd);
        expect(onNavigate).toHaveBeenCalledWith('cont');
        expect(mockDispatchHandoffOpen).not.toHaveBeenCalled();
    });

    test('handed_off notice uses its own targetId before the record has synced', () => {
        const onNavigate = jest.fn();
        const msg = { ...drafted, content: 'Handed off to “X”.',
            handoffNotice: { kind: 'handed_off' as const, targetId: 'cont' } };
        // Source record has no handedOffTo yet and the child is not in the list.
        render(<HandoffNotice message={msg} conversationId="src"
            conversations={[{ id: 'src', title: 'ISL BFD flap' }]} onNavigate={onNavigate} />);
        const fwd = screen.getByTestId('handoff-notice-forward');
        expect(fwd).toHaveTextContent('the next segment');
        fireEvent.click(fwd);
        expect(onNavigate).toHaveBeenCalledWith('cont');
    });
});

describe('resolveHandoffForward', () => {
    test('notice targetId wins over the record; record fills in for a drafted notice', () => {
        const convs = [{ id: 'src', handedOffTo: 'b' }, { id: 'a', title: 'A' }, { id: 'b', title: 'B' }];
        expect(resolveHandoffForward(
            { ...drafted, handoffNotice: { kind: 'handed_off', targetId: 'a' } }, 'src', convs))
            .toEqual({ id: 'a', title: 'A' });
        expect(resolveHandoffForward(drafted, 'src', convs)).toEqual({ id: 'b', title: 'B' });
        expect(resolveHandoffForward(drafted, 'src', [{ id: 'src' }])).toBeNull();
    });
});
