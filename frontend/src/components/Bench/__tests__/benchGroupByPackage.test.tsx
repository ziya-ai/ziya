/**
 * @jest-environment jsdom
 *
 * "By package" grouping: the pure tree, the board rendering it, a package
 * moved as one (menu and drag, with the On hand confirmation), and the
 * panel's selector that turns it on.
 */
import React from 'react';
import { act, fireEvent, render, screen, within } from '@testing-library/react';
import '@testing-library/jest-dom';
import { BenchBoard, BenchBoardModel } from '../BenchBoard';
import { BenchPanel, BenchPanelModel, GROUP_PREF_KEY } from '../BenchPanel';
import { GROUP_DRAG_MIME } from '../PackageLane';
import { mk, mockDataTransfer } from '../benchTestUtils';
import {
    BUILTIN_GROUP, UNGROUPED, buildPackageTree, countText, hasPackages, nodeItems, otherPackageCount,
} from '../packageTree';
import type { BenchItemView } from '../../../hooks/useBench';

const g = (key: string, groups: string[][], opts: Parameters<typeof mk>[1] = {}): BenchItemView =>
    ({ ...mk(key, { provenance: 'user', ...opts }), groups });

// All default to Within reach except fetch (an MCP server at the kind default).
const items = () => [
    g('skill:shared', [['Big', 'Set'], ['Small', 'Set']]),
    g('skill:own', [['Big']]),
    g('mcp:quip', [['Registry']], { def: 'ondemand' }),
    mk('mcp:fetch', { provenance: 'user' }),
    mk('skill:Concise', { provenance: 'builtin' }),
];

describe('packageTree', () => {
    it('files a shared item under every package and component that ships it', () => {
        const tree = buildPackageTree(items());
        const big = tree.find(n => n.label === 'Big')!;
        const small = tree.find(n => n.label === 'Small')!;
        expect(big.items.map(i => i.key)).toEqual(['skill:own']);
        expect(big.children.map(c => c.path)).toEqual(['Big › Set']);
        expect(big.children[0].items.map(i => i.key)).toEqual(['skill:shared']);
        expect(small.children[0].items.map(i => i.key)).toEqual(['skill:shared']);
    });
    it('orders packages first, then built-ins, then the unattributed', () => {
        expect(buildPackageTree(items()).map(n => n.label))
            .toEqual(['Big', 'Registry', 'Small', BUILTIN_GROUP, UNGROUPED]);
    });
    it('counts each item under a node once and names what it holds', () => {
        const big = buildPackageTree(items()).find(n => n.label === 'Big')!;
        expect(nodeItems(big).map(i => i.key).sort()).toEqual(['skill:own', 'skill:shared']);
        expect(countText(nodeItems(big))).toBe('2 skills');
        expect(countText([items()[2]])).toBe('1 server');
    });
    it('reports other packages, and whether anything is packaged at all', () => {
        const [shared, own] = items();
        expect(otherPackageCount(shared, 'Big')).toBe(1);
        expect(otherPackageCount(own, 'Big')).toBe(0);
        expect(hasPackages(items())).toBe(true);
        expect(hasPackages([mk('mcp:fetch'), mk('skill:Concise')])).toBe(false);
    });
});

function board(groupBy?: 'none' | 'package', place = jest.fn(async () => {})) {
    const model: BenchBoardModel = { items: items(), place };
    render(<BenchBoard bench={model} groupBy={groupBy} />);
    return place;
}
const toggle = (id: string) => fireEvent.click(screen.getByTestId(`group-toggle-${id}`));
const chooseMove = async (id: string, to: string) => {
    await act(async () => { fireEvent.change(screen.getByTestId(`group-move-${id}`), { target: { value: to } }); });
};

describe('board, flat (default)', () => {
    it('shows cards and no package rows', () => {
        board();
        expect(screen.getByTestId('card-skill:own')).toBeInTheDocument();
        expect(screen.queryByTestId('group-ondemand|Big')).toBeNull();
    });
});

describe('board, by package', () => {
    it('puts each package row in the lane its items occupy, collapsed', () => {
        board('package');
        const reach = within(screen.getByTestId('lane-ondemand'));
        expect(reach.getByTestId('group-ondemand|Big')).toHaveTextContent('Big · 2 skills');
        expect(reach.getByTestId('group-ondemand|Registry')).toHaveTextContent('1 server');
        expect(within(screen.getByTestId('lane-always')).getByTestId(`group-always|${UNGROUPED}`)).toBeInTheDocument();
        expect(screen.queryByTestId('card-skill:own')).toBeNull();
    });
    it('opens a row to its cards and nested component rows', () => {
        board('package');
        toggle('ondemand|Big');
        expect(screen.getByTestId('card-skill:own')).toBeInTheDocument();
        expect(screen.queryByTestId('card-skill:shared')).toBeNull();          // one level down
        toggle('ondemand|Big › Set');
        expect(screen.getByTestId('card-skill:shared')).toBeInTheDocument();
        expect(screen.getByTestId('also-in-ondemand|Big › Set|skill:shared')).toHaveTextContent('also in 1 other package');
        toggle('ondemand|Big');
        expect(screen.queryByTestId('card-skill:own')).toBeNull();             // collapses again
    });
    it('shows a shared item under both packages', () => {
        board('package');
        ['ondemand|Big', 'ondemand|Big › Set', 'ondemand|Small', 'ondemand|Small › Set'].forEach(toggle);
        expect(screen.getAllByTestId('card-skill:shared')).toHaveLength(2);
    });
    it('the move menu places every item under the row', async () => {
        const place = board('package');
        await chooseMove('ondemand|Big', 'off');
        expect(place.mock.calls.map(c => [c[0], c[1]]).sort())
            .toEqual([['skill:own', 'off'], ['skill:shared', 'off']]);
        expect(screen.queryByTestId('group-confirm')).toBeNull();
    });
    it('asks before putting a whole package on hand, and Cancel places nothing', async () => {
        const place = board('package');
        await chooseMove('ondemand|Big', 'always');
        expect(screen.getByTestId('group-confirm')).toHaveTextContent('Put all 2 items from Big on hand?');
        expect(place).not.toHaveBeenCalled();
        fireEvent.click(screen.getByTestId('group-confirm-no'));
        expect(screen.queryByTestId('group-confirm')).toBeNull();
        expect(place).not.toHaveBeenCalled();
    });
    it('confirming places them all on hand', async () => {
        const place = board('package');
        await chooseMove('ondemand|Big', 'always');
        await act(async () => { fireEvent.click(screen.getByTestId('group-confirm-yes')); });
        expect(place.mock.calls.map(c => [c[0], c[1]]).sort())
            .toEqual([['skill:own', 'always'], ['skill:shared', 'always']]);
        expect(screen.queryByTestId('group-confirm')).toBeNull();
    });
    it('a single-item row goes on hand without asking', async () => {
        const place = board('package');
        await chooseMove('ondemand|Registry', 'always');
        expect(place).toHaveBeenCalledWith('mcp:quip', 'always');
        expect(screen.queryByTestId('group-confirm')).toBeNull();
    });
    it('dragging a row onto a lane moves the package', async () => {
        const place = board('package');
        const dataTransfer = mockDataTransfer();
        fireEvent.dragStart(screen.getByTestId('group-row-ondemand|Big'), { dataTransfer });
        expect(JSON.parse(dataTransfer.getData(GROUP_DRAG_MIME)).label).toBe('Big');
        await act(async () => { fireEvent.drop(screen.getByTestId('lane-off'), { dataTransfer }); });
        expect(place.mock.calls.map(c => [c[0], c[1]]).sort())
            .toEqual([['skill:own', 'off'], ['skill:shared', 'off']]);
    });
});

function panelModel(over: Partial<BenchPanelModel> = {}): BenchPanelModel {
    return {
        ready: true, error: null, items: items(), dropped: [],
        weight: { always: 10, ondemand: 40, environment: 0 },
        deltas: { conversation: 0, project: 0, user: 0 },
        lenses: ['project', 'user'], lens: 'project',
        setLens: jest.fn(), place: jest.fn(async () => {}),
        ...over,
    };
}

describe('panel selector', () => {
    beforeEach(() => localStorage.clear());

    it('is not offered when nothing came from a package', () => {
        render(<BenchPanel bench={panelModel({ items: [mk('mcp:fetch', { provenance: 'user' })] })} />);
        expect(screen.getByTestId('bench-board')).toBeInTheDocument();
        expect(screen.queryByTestId('groupby-package')).toBeNull();
    });
    it('starts flat, regroups on click and remembers the choice', () => {
        render(<BenchPanel bench={panelModel()} />);
        expect(screen.getByTestId('groupby-none')).toHaveAttribute('aria-selected', 'true');
        expect(screen.queryByTestId('group-ondemand|Big')).toBeNull();
        fireEvent.click(screen.getByTestId('groupby-package'));
        expect(screen.getByTestId('group-ondemand|Big')).toBeInTheDocument();
        expect(localStorage.getItem(GROUP_PREF_KEY)).toBe('package');
    });
    it('opens grouped when that was the remembered choice', () => {
        localStorage.setItem(GROUP_PREF_KEY, 'package');
        render(<BenchPanel bench={panelModel()} />);
        expect(screen.getByTestId('groupby-package')).toHaveAttribute('aria-selected', 'true');
        expect(screen.getByTestId('group-ondemand|Big')).toBeInTheDocument();
    });
});
