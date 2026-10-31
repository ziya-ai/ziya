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

import HandoffNotice, {
    resolveHandoffForward, forwardLabelTitle, FORWARD_LABEL_MAX, isSupersededHandoffNotice,
} from '../HandoffNotice';

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

describe('HandoffNotice — forked kind', () => {
    // A fork leaves the same local notice on its source, kind 'forked', at
    // the fork point.  DIRECTION: against the unpatched component the row
    // renders ⇢, a "Continue in … ⇢" button under the handoff testid, and
    // borrows the record's handedOffTo when no targetId is given.
    const forkedMsg = { ...drafted, content: 'Forked here into “Fork: ISL BFD flap”.',
        handoffNotice: { kind: 'forked' as const, targetId: 'fk' } };

    test('shows the fork glyph and an Open link to the fork by title', () => {
        const onNavigate = jest.fn();
        render(<HandoffNotice message={forkedMsg} conversationId="src"
            conversations={[{ id: 'src', title: 'ISL BFD flap' }, { id: 'fk', title: 'Fork: ISL BFD flap' }]}
            onNavigate={onNavigate} />);
        expect(screen.getByTestId('handoff-notice').getAttribute('data-kind')).toBe('forked');
        expect(screen.getByTestId('fork-glyph')).toBeInTheDocument();
        expect(screen.queryByText('⇢')).toBeNull();
        expect(screen.queryByTestId('handoff-notice-forward')).toBeNull();
        const fwd = screen.getByTestId('fork-notice-forward');
        expect(fwd).toHaveTextContent('Open “Fork: ISL BFD flap”');
        fireEvent.click(fwd);
        expect(onNavigate).toHaveBeenCalledWith('fk');
    });

    test('never offers Review handoff and never borrows handedOffTo', () => {
        const onNavigate = jest.fn();
        const noTarget = { ...forkedMsg, handoffNotice: { kind: 'forked' as const } };
        render(<HandoffNotice message={noTarget} conversationId="src"
            conversations={[{ id: 'src', handedOffTo: 'cont' }, { id: 'cont', title: 'C' }]}
            onNavigate={onNavigate} />);
        expect(screen.queryByText('Review handoff')).toBeNull();
        expect(screen.queryByTestId('fork-notice-forward')).toBeNull();
        expect(screen.queryByTestId('handoff-notice-forward')).toBeNull();
        expect(resolveHandoffForward(noTarget, 'src', [{ id: 'src', handedOffTo: 'cont' }])).toBeNull();
    });
});

describe('HandoffNotice — long titles', () => {
    // A fork of a long prompt ("Fork: Fork: we need to create a task that…")
    // put the whole title in an unwrappable button, which squeezed the
    // notice text into a one-word-per-line column.  DIRECTION: unpatched, the
    // button text is the full title and the text span has no minWidth.
    const long = 'Fork: Fork: we need to create a task that quickly scans through the last 12 hours of contact time and pulls out loss events';
    const msg = { ...drafted, content: `Forked here into “${long}”.`,
        handoffNotice: { kind: 'forked' as const, targetId: 'fk' } };

    test('truncates the button label, keeps the full title in the tooltip and the text', () => {
        render(<HandoffNotice message={msg} conversationId="src"
            conversations={[{ id: 'fk', title: long }]} onNavigate={jest.fn()} />);
        const fwd = screen.getByTestId('fork-notice-forward');
        expect(fwd.textContent!.length).toBeLessThan(long.length);
        expect(fwd).toHaveTextContent('…');
        expect(fwd).toHaveTextContent(long.slice(0, 20));
        expect(fwd.getAttribute('title')).toBe(long);
        expect(fwd.style.flexShrink).toBe('0');
        const text = screen.getByTestId('handoff-notice-text');
        expect(text).toHaveTextContent(long);
        // React serialises a numeric 0 without a unit; jsdom may report either.
        expect(text.style.minWidth).toMatch(/^0(px)?$/);
    });

    test('forwardLabelTitle leaves short titles alone and caps long ones', () => {
        expect(forwardLabelTitle('ISL BFD flap')).toBe('ISL BFD flap');
        const cut = forwardLabelTitle(long);
        expect(cut.length).toBeLessThanOrEqual(FORWARD_LABEL_MAX);
        expect(cut.endsWith('…')).toBe(true);
    });
});

describe('isSupersededHandoffNotice', () => {
    // After a commit the source showed two rows, "The model drafted a handoff
    // document" and "Handed off to …", each with the same Continue link.
    // DIRECTION: the helper does not exist on unpatched code (import fails).
    const handedOff = { ...drafted, id: 'n2', content: 'Handed off to “X”.',
        handoffNotice: { kind: 'handed_off' as const, targetId: 'cont' } };
    const human = { id: 'h', role: 'human' as const, content: 'hi' };

    test('drafted notice is superseded once a handed_off notice exists', () => {
        expect(isSupersededHandoffNotice(drafted, [human, drafted, handedOff] as any)).toBe(true);
    });

    test('drafted notice stays when there is no handed_off notice (pre-commit or legacy)', () => {
        expect(isSupersededHandoffNotice(drafted, [human, drafted] as any)).toBe(false);
    });

    test('the handed_off notice itself, fork notices and ordinary messages are never hidden', () => {
        const forked = { ...drafted, id: 'f', handoffNotice: { kind: 'forked' as const, targetId: 'fk' } };
        const all = [human, drafted, handedOff, forked] as any;
        expect(isSupersededHandoffNotice(handedOff as any, all)).toBe(false);
        expect(isSupersededHandoffNotice(forked as any, all)).toBe(false);
        expect(isSupersededHandoffNotice(human as any, all)).toBe(false);
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
