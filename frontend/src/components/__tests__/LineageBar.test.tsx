/**
 * @jest-environment jsdom
 *
 * LineageBar — the breadcrumb at the top of a forked / branched conversation.
 *
 * Reported 2026-11-15: a fork of a fork of a long-titled conversation showed
 * the same title three times ("… (2)", "… (3)"), each wrapped over two rows
 * with the chevron on a row of its own, and the bar is sticky, so it held
 * about a third of the viewport and grew with every further fork.
 *
 * Asserts on what the user sees:
 *  - a trunk conversation renders nothing;
 *  - an ancestor whose title differs from the current one only by its
 *    "(N)" ordinal is shown as that ordinal, the full title stays reachable
 *    (tooltip) and the link still navigates;
 *  - the chain is one row, and past three segments the middle ancestors are
 *    folded behind a count that expands and collapses;
 *  - an ancestor with a different title is left alone.
 */
import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import '@testing-library/jest-dom';

jest.mock('../../context/ThemeContext', () => ({
    useTheme: () => ({ isDarkMode: false }),
}));

import LineageBar, { abbreviateLineageTitle } from '../LineageBar';

const LONG = 'Fork: find the conversation in slack from last night where someone claimed to have an xdp pipeline fix ready';

const forkChain = (n: number) =>
    Array.from({ length: n }, (_, i) => ({
        id: `c${i + 1}`,
        title: i === 0 ? LONG : `${LONG} (${i + 1})`,
        branchedFrom: i === 0 ? undefined : `c${i}`,
    }));

describe('abbreviateLineageTitle', () => {
    test('same title apart from the ordinal becomes the ordinal', () => {
        expect(abbreviateLineageTitle(`${LONG} (2)`, `${LONG} (3)`)).toBe('(2)');
        expect(abbreviateLineageTitle(LONG, `${LONG} (3)`)).toBe('(1)');
        expect(abbreviateLineageTitle(`${LONG} (2)`, LONG)).toBe('(2)');
    });

    test('a different title is returned unchanged', () => {
        expect(abbreviateLineageTitle('ISL BFD flap', `${LONG} (3)`)).toBe('ISL BFD flap');
        expect(abbreviateLineageTitle('Plan (v2) draft', 'Plan (v3) draft')).toBe('Plan (v2) draft');
        expect(abbreviateLineageTitle('', '')).toBe('');
    });
});

describe('LineageBar', () => {
    test('trunk conversation renders nothing', () => {
        const { container } = render(
            <LineageBar conversationId="c1" conversations={forkChain(1)} onNavigate={() => {}} />);
        expect(container).toBeEmptyDOMElement();
    });

    test('repeated titles are shown once; ancestors become their ordinal and still navigate', () => {
        const nav = jest.fn();
        render(<LineageBar conversationId="c3" conversations={forkChain(3)} onNavigate={nav} />);
        // positive control: the bar rendered, with the current title in full
        expect(screen.getByTestId('lineage-current')).toHaveTextContent(`${LONG} (3)`);
        // the long title is on screen exactly once
        expect(screen.getByTestId('lineage-bar').textContent!.split(LONG).length - 1).toBe(1);
        const root = screen.getByTestId('lineage-node-c1');
        const parent = screen.getByTestId('lineage-node-c2');
        expect(root).toHaveTextContent('(1)');
        expect(parent).toHaveTextContent('(2)');
        // the full name is still reachable
        expect(parent.getAttribute('title')).toContain(`${LONG} (2)`);
        fireEvent.click(parent);
        expect(nav).toHaveBeenCalledWith('c2');
        // three segments: nothing to fold
        expect(screen.queryByTestId('lineage-toggle')).toBeNull();
    });

    test('the chain is a single row and every segment clips instead of wrapping', () => {
        render(<LineageBar conversationId="c3" conversations={forkChain(3)} onNavigate={() => {}} />);
        expect(screen.getByTestId('lineage-chain')).toHaveStyle({ flexWrap: 'nowrap' });
        for (const id of ['lineage-current', 'lineage-node-c1', 'lineage-node-c2']) {
            expect(screen.getByTestId(id)).toHaveStyle({ whiteSpace: 'nowrap', textOverflow: 'ellipsis' });
        }
    });

    test('past three segments the middle ancestors fold behind a count, and unfold', () => {
        const nav = jest.fn();
        render(<LineageBar conversationId="c6" conversations={forkChain(6)} onNavigate={nav} />);
        // root, direct parent and current stay; c2..c4 are folded
        expect(screen.getByTestId('lineage-node-c1')).toBeInTheDocument();
        expect(screen.getByTestId('lineage-node-c5')).toHaveTextContent('(5)');
        expect(screen.getByTestId('lineage-current')).toHaveTextContent(`${LONG} (6)`);
        for (const id of ['c2', 'c3', 'c4']) {
            expect(screen.queryByTestId(`lineage-node-${id}`)).toBeNull();
        }
        const toggle = screen.getByTestId('lineage-toggle');
        expect(toggle).toHaveTextContent('3 more');

        fireEvent.click(toggle);
        for (const id of ['c1', 'c2', 'c3', 'c4', 'c5']) {
            expect(screen.getByTestId(`lineage-node-${id}`)).toBeInTheDocument();
        }
        fireEvent.click(screen.getByTestId('lineage-node-c3'));
        expect(nav).toHaveBeenCalledWith('c3');

        fireEvent.click(screen.getByTestId('lineage-toggle'));
        expect(screen.queryByTestId('lineage-node-c3')).toBeNull();
        expect(screen.getByTestId('lineage-chain')).toHaveStyle({ flexWrap: 'nowrap' });
    });

    test('an ancestor with a different title keeps its title', () => {
        const convs = [
            { id: 'a', title: 'ISL BFD flap' },
            { id: 'b', title: 'Hugo latency', branchedFrom: 'a', branchedFromLabel: 'fpga timing' },
        ];
        render(<LineageBar conversationId="b" conversations={convs} onNavigate={() => {}} />);
        expect(screen.getByTestId('lineage-node-a')).toHaveTextContent('ISL BFD flap');
        expect(screen.getByTestId('lineage-current')).toHaveTextContent('Hugo latency');
        // the seam note is unchanged by the abbreviation
        expect(screen.getByTestId('lineage-bar')).toHaveTextContent('fpga timing');
    });
});
