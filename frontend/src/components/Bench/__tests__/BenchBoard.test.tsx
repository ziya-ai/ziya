/**
 * @jest-environment jsdom
 *
 * The board is a renderer of the useBench view-model (design/capabilities-hub.md,
 * "Architecture rule").  These tests hand it a model and assert: it makes no
 * network call, every card shows exactly the lane/ghost/pin it was given, and
 * every gesture reaches ``place`` with the payload laneDnd defines.
 */
import React from 'react';
import * as fs from 'fs';
import * as path from 'path';
import { act, fireEvent, render, screen, within } from '@testing-library/react';
import '@testing-library/jest-dom';
import { BenchBoard, BenchBoardModel } from '../BenchBoard';
import { DRAG_MIME } from '../laneDnd';
import { mk, mockDataTransfer } from '../benchTestUtils';

const items = () => [
    mk('skill:Concise', { project: 'always', def: 'off', provenance: 'builtin' }),   // pinned here
    mk('skill:Code Review', { provenance: 'project' }),                             // ghost, ondemand (not builtin: those fold)
    mk('skill:Mine', { conversation: 'off', project: 'always', def: 'off', provenance: 'user' }), // pinned, overridden
    mk('mcp:jira', { provenance: 'user' }),                                         // ghost, always
    mk('mcp:shell', { tier: 'environment' }),                                       // not a lane item
];

function setup(place = jest.fn(async () => {})) {
    const model: BenchBoardModel = { items: items(), place };
    render(<BenchBoard bench={model} />);
    return { model, place };
}

let fetchSpy: jest.Mock;
beforeEach(() => { fetchSpy = jest.fn(); (global as any).fetch = fetchSpy; });

describe('hub makes no fetch and derives nothing', () => {
    it('renders from the model with zero network calls', () => {
        setup();
        expect(fetchSpy).not.toHaveBeenCalled();
    });
    it('every card shows exactly the state / ghost / pin it was handed', () => {
        const { model } = setup();
        for (const i of model.items.filter(x => x.tier !== 'environment')) {
            const card = screen.getByTestId(`card-${i.key}`);
            expect(card).toHaveAttribute('data-state', i.atLens.state);
            expect(card).toHaveAttribute('data-ghost', String(i.atLens.ghost));
            expect(card).toHaveAttribute('data-pinned-higher', String(i.atLens.pinnedHigher));
            expect(within(screen.getByTestId(`lane-${i.atLens.state}`)).getByTestId(`card-${i.key}`)).toBe(card);
        }
        expect(screen.getByTestId('card-skill:Mine')).toHaveTextContent('overridden by conversation');
    });
    it('environment items are never rendered as a lane card', () => {
        setup();
        expect(screen.queryByTestId('card-mcp:shell')).toBeNull();
        expect(screen.queryByText('shell')).toBeNull();
    });
    it('source never imports the API client or calls fetch', () => {
        for (const f of ['BenchBoard.tsx', 'CapabilityCard.tsx', 'laneDnd.ts']) {
            const src = fs.readFileSync(path.join(__dirname, '..', f), 'utf8');
            expect(src).not.toMatch(/apis\/benchApi['"]/);
            expect(src).not.toMatch(/\bfetch\(/);
            // types from useBench are fine; the hook itself is the shell's job
            expect(src).not.toMatch(/import\s*\{[^}]*\buseBench\b[^}]*\}/);
        }
    });
});

describe('drag and drop', () => {
    const drag = (key: string, lane: string) => {
        const dt = mockDataTransfer();
        fireEvent.dragStart(screen.getByTestId(`card-${key}`), { dataTransfer: dt });
        fireEvent.drop(screen.getByTestId(`lane-${lane}`), { dataTransfer: dt });
        return dt;
    };
    it('payload is the item key; drop on a new lane places toState', async () => {
        const { place } = setup();
        let dt: ReturnType<typeof mockDataTransfer>;
        await act(async () => { dt = drag('skill:Code Review', 'always'); });
        expect(dt!.getData(DRAG_MIME)).toBe('skill:Code Review');
        expect(place).toHaveBeenCalledWith('skill:Code Review', 'always');
    });
    it('a pinned item leaves a ghost outline in its inherited lane; dropping there clears', async () => {
        const { place } = setup();
        expect(within(screen.getByTestId('lane-off')).getByTestId('ghost-outline-skill:Concise')).toBeInTheDocument();
        await act(async () => { drag('skill:Concise', 'off'); });
        expect(place).toHaveBeenCalledWith('skill:Concise', null);
    });
    it('mcp into Within reach is refused without calling place, with a reason', async () => {
        const { place } = setup();
        await act(async () => { drag('mcp:jira', 'ondemand'); });
        expect(place).not.toHaveBeenCalled();
        expect(screen.getByRole('status')).toHaveTextContent('on-demand MCP servers are coming soon');
    });
    it('a 409 from place is shown, not swallowed', async () => {
        const err = Object.assign(new Error('x'), { reason: 'environment_not_placeable' });
        setup(jest.fn(async () => { throw err; }));
        await act(async () => { drag('skill:Code Review', 'off'); });
        expect(screen.getByRole('status')).toHaveTextContent('part of the environment');
    });
});

describe('keyboard and right-click reach the same moves', () => {
    it('the ⋯ button opens the menu and a choice calls place', async () => {
        const { place } = setup();
        fireEvent.click(screen.getByRole('button', { name: 'Move Code Review' }));
        await act(async () => { fireEvent.click(screen.getByRole('menuitem', { name: 'Move to Shelved' })); });
        expect(place).toHaveBeenCalledWith('skill:Code Review', 'off');
        expect(screen.queryByRole('menu')).toBeNull();
    });
    it('right-click opens the same menu; mcp Within reach is disabled', () => {
        setup();
        fireEvent.contextMenu(screen.getByTestId('card-mcp:jira'));
        expect(screen.getByRole('menuitem', { name: 'Within reach (coming soon)' })).toBeDisabled();
    });
    it('Escape closes the menu', () => {
        setup();
        fireEvent.click(screen.getByRole('button', { name: 'Move Code Review' }));
        fireEvent.keyDown(screen.getByRole('menu'), { key: 'Escape' });
        expect(screen.queryByRole('menu')).toBeNull();
    });
});

describe('built-in fold', () => {
    it('built-ins at default render as one row, not cards; expanding reveals them and a drop still resolves', async () => {
        const place = jest.fn(async () => {});
        render(<BenchBoard bench={{ items: [
            ...items(),
            mk('skill:Packet Diagrams'),      // builtin, default ondemand
            mk('skill:Music Notation'),
        ], place }} />);
        const lane = screen.getByTestId('lane-ondemand');
        expect(within(lane).queryByTestId('card-skill:Packet Diagrams')).toBeNull();
        expect(within(lane).getByTestId('card-skill:Code Review')).toBeInTheDocument();   // non-builtin ghost still a card
        const fold = within(lane).getByTestId('fold-builtin-ondemand');
        expect(fold).toHaveTextContent('2 built-in skills');
        expect(screen.queryByTestId('fold-ondemand')).toBeNull();                          // no space fold when unmeasured
        fireEvent.click(fold);
        expect(within(lane).getByTestId('card-skill:Packet Diagrams')).toBeInTheDocument();
        const dt = mockDataTransfer();
        fireEvent.dragStart(within(lane).getByTestId('card-skill:Packet Diagrams'), { dataTransfer: dt });
        await act(async () => { fireEvent.drop(screen.getByTestId('lane-always'), { dataTransfer: dt }); });
        expect(place).toHaveBeenCalledWith('skill:Packet Diagrams', 'always');
    });
});

describe('✦ pickup marker', () => {
    it('marks exactly the cards the hook says were loaded this turn', () => {
        render(<BenchBoard bench={{ items: items(), place: async () => {},
                                    isPickedUp: k => k === 'skill:Code Review' }} />);
        expect(screen.getByTestId('picked-up-skill:Code Review')).toBeInTheDocument();
        expect(screen.getByTestId('card-skill:Code Review')).toHaveAttribute('data-picked-up', 'true');
        expect(screen.queryByTestId('picked-up-skill:Concise')).toBeNull();
        expect(screen.getByTestId('card-skill:Concise')).toHaveAttribute('data-picked-up', 'false');
    });
});
