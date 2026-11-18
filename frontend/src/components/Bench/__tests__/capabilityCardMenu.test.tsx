/**
 * @jest-environment jsdom
 *
 * The ⋯ move menu on a Bench card (reported 2026-11-12).
 *
 * The menu was an absolutely positioned child of the card.  Three things
 * followed from that:
 *   - an inherited card is drawn at opacity 0.85, which makes the card a
 *     stacking context: the menu's z-index stopped at the card, the cards
 *     below painted over it (their own background is transparent, so the
 *     menu looked see-through) and took its clicks, so "Move to Within
 *     reach" did nothing while drag still worked;
 *   - a folded lane is overflow:hidden, which clipped the menu of the last
 *     cards in it;
 *   - nothing closed the menu except choosing an item or pressing ⋯ again.
 *
 * The menu is now rendered into document.body with fixed positioning and
 * closes on an outside press, Escape, scroll, resize and drag start.
 *
 * jsdom does not paint, so the stacking itself cannot be asserted; what is
 * asserted is the structure that removes it (the menu is not inside the
 * card) and every close path.  DIRECTION: the portal and outside-press
 * tests fail against the in-card menu.
 */
import React from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom';
import { CapabilityCard } from '../CapabilityCard';
import { mk } from '../benchTestUtils';

// On hand, inherited from the user layer: the reported case (a ghost card).
const inherited = () => mk('skill:Code Review', { user: 'always' });

function setup(items = [inherited()]) {
    const onIntent = jest.fn();
    render(
        <div data-testid="outside">
            {items.map(i => <CapabilityCard key={i.key} item={i} onIntent={onIntent} />)}
        </div>,
    );
    return { onIntent };
}
const open = (name = 'Code Review') =>
    fireEvent.click(screen.getByRole('button', { name: `Move ${name}` }));

describe('⋯ menu placement', () => {
    it('the fixture is the reported case: an inherited card at reduced opacity', () => {
        setup();
        const card = screen.getByTestId('card-skill:Code Review');
        expect(card).toHaveAttribute('data-ghost', 'true');
        expect(card).toHaveAttribute('data-state', 'always');
        expect(Number(card.style.opacity)).toBeLessThan(1);
    });

    it('renders outside the card, fixed, above the modal layer', () => {
        setup();
        open();
        const menu = screen.getByRole('menu');
        expect(screen.getByTestId('card-skill:Code Review')).not.toContainElement(menu);
        expect(menu.parentElement).toBe(document.body);
        expect(menu.style.position).toBe('fixed');
        expect(Number(menu.style.zIndex)).toBeGreaterThan(1000);
    });

    it('a choice from the inherited On hand card reports the move and closes', () => {
        const { onIntent } = setup();
        open();
        fireEvent.click(screen.getByRole('menuitem', { name: 'Move to Within reach' }));
        expect(onIntent).toHaveBeenCalledWith({ kind: 'place', key: 'skill:Code Review', toState: 'ondemand' });
        expect(screen.queryByRole('menu')).toBeNull();
    });
});

describe('⋯ menu closes when it is no longer the thing in use', () => {
    it('a press anywhere else closes it; a press inside does not', () => {
        setup();
        open();
        fireEvent.mouseDown(screen.getByRole('menu'));
        expect(screen.getByRole('menu')).toBeInTheDocument();
        fireEvent.mouseDown(screen.getByTestId('outside'));
        expect(screen.queryByRole('menu')).toBeNull();
    });

    it('opening another card\'s menu leaves exactly one open', () => {
        setup([inherited(), mk('skill:Debug Mode', { user: 'always' })]);
        open();
        const second = screen.getByRole('button', { name: 'Move Debug Mode' });
        fireEvent.mouseDown(second);
        fireEvent.click(second);
        expect(screen.getAllByRole('menu')).toHaveLength(1);
    });

    it('the ⋯ button still toggles its own menu', () => {
        setup();
        const btn = screen.getByRole('button', { name: 'Move Code Review' });
        fireEvent.mouseDown(btn); fireEvent.click(btn);
        expect(screen.getByRole('menu')).toBeInTheDocument();
        fireEvent.mouseDown(btn); fireEvent.click(btn);
        expect(screen.queryByRole('menu')).toBeNull();
    });

    it('Escape closes it without focus being in the menu', () => {
        setup();
        open();
        fireEvent.keyDown(document.body, { key: 'Escape' });
        expect(screen.queryByRole('menu')).toBeNull();
    });

    it('scroll, resize and drag start close it', () => {
        setup();
        const card = screen.getByTestId('card-skill:Code Review');
        open();
        act(() => { fireEvent.scroll(screen.getByTestId('outside')); });
        expect(screen.queryByRole('menu')).toBeNull();
        open();
        act(() => { fireEvent(window, new Event('resize')); });
        expect(screen.queryByRole('menu')).toBeNull();
        open();
        fireEvent.dragStart(card, { dataTransfer: { setData: () => {}, effectAllowed: 'all' } });
        expect(screen.queryByRole('menu')).toBeNull();
    });

    it('unmounting the card removes its menu from the body', () => {
        const { unmount } = render(<CapabilityCard item={inherited()} onIntent={() => {}} />);
        open();
        unmount();
        expect(document.body.querySelector('[role="menu"]')).toBeNull();
    });
});
