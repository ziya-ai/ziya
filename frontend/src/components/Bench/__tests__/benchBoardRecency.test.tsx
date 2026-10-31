/**
 * @jest-environment jsdom
 *
 * Seam: the board hands each lane to foldLane WITH its lane, so the shelved
 * and within-reach lanes render most-recently-used first while on hand keeps
 * its order.  Asserted on the rendered card order, not on foldLane.
 */
import React from 'react';
import { render, screen, within } from '@testing-library/react';
import '@testing-library/jest-dom';
import { BenchBoard, BenchBoardModel } from '../BenchBoard';
import { mk } from '../benchTestUtils';

const used = (item: ReturnType<typeof mk>, at: number | null) => ({ ...item, last_used: at });

const items = () => [
    // shelved: server order is oldest-first, so the server order alone fails
    used(mk('skill:offOld', { def: 'off', provenance: 'user' }), 1_000),
    used(mk('skill:offNever', { def: 'off', provenance: 'user' }), null),
    used(mk('skill:offNew', { def: 'off', provenance: 'user' }), 9_000),
    // within reach
    used(mk('skill:reachOld', { provenance: 'user' }), 2_000),
    used(mk('skill:reachNew', { provenance: 'user' }), 8_000),
    // on hand: pinned stays ahead of a more recent default-placed one
    used(mk('skill:handRecent', { def: 'always', provenance: 'user' }), 9_999),
    used(mk('skill:handPinned', { project: 'always', provenance: 'user' }), null),
];

const order = (lane: string) =>
    within(screen.getByTestId(`lane-${lane}`)).queryAllByTestId(/^card-/).map(c => c.getAttribute('data-testid'));

beforeEach(() => { (global as any).fetch = jest.fn(); });

it('shelved and within reach render by most recent use; on hand is unchanged', () => {
    const model: BenchBoardModel = { items: items(), place: jest.fn(async () => {}) };
    render(<BenchBoard bench={model} />);
    expect(order('off')).toEqual(['card-skill:offNew', 'card-skill:offOld', 'card-skill:offNever']);
    expect(order('ondemand')).toEqual(['card-skill:reachNew', 'card-skill:reachOld']);
    expect(order('always')).toEqual(['card-skill:handPinned', 'card-skill:handRecent']);
});
