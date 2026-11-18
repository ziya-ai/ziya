/**
 * @jest-environment jsdom
 *
 * "Not installed here": what a package needs that this bench lacks -- the
 * strip, its find action, the count on a package row, and the hop from the
 * strip into the Find view with the name filled in.
 */
import React from 'react';
import { act, fireEvent, render, screen, within } from '@testing-library/react';
import '@testing-library/jest-dom';
import { BenchPanel, BenchPanelModel, GROUP_PREF_KEY } from '../BenchPanel';
import { MissingCapability, NotInstalledHere, missingByPackage, missingCountByRoot } from '../NotInstalledHere';
import { mk } from '../benchTestUtils';
import type { BenchItemView } from '../../../hooks/useBench';

const missing = (): MissingCapability[] => [
    { kind: 'mcp', name: 'quick-mcp', groups: [['Noc']], installable: true },
    { kind: 'mcp', name: 'nova', groups: [['Noc']], installable: false },
    { kind: 'mcp', name: 'shared-mcp', groups: [['Noc'], ['Big']], installable: true },
];

describe('grouping', () => {
    it('lists each package with what it lacks; a shared server under both', () => {
        expect(missingByPackage(missing()).map(g => [g.pkg, g.items.map(i => i.name)])).toEqual([
            ['Big', ['shared-mcp']],
            ['Noc', ['quick-mcp', 'nova', 'shared-mcp']],
        ]);
        expect(missingCountByRoot(missing())).toEqual({ Big: 1, Noc: 3 });
    });
});

describe('NotInstalledHere', () => {
    it('renders nothing when nothing is missing', () => {
        const { container } = render(<NotInstalledHere missing={[]} onFind={jest.fn()} />);
        expect(container).toBeEmptyDOMElement();
    });
    it('names the package, the count and each server', () => {
        render(<NotInstalledHere missing={missing()} onFind={jest.fn()} />);
        const noc = screen.getByTestId('missing-pkg-Noc');
        expect(noc).toHaveTextContent('Noc needs 3 servers not installed here:');
        expect(screen.getByTestId('missing-pkg-Big')).toHaveTextContent('Big needs 1 server not installed here:');
        expect(within(noc).getByTestId('missing-item-Noc|nova')).toHaveTextContent('nova (set up by the package)');
    });
    it('offers find only for what a registry can supply', () => {
        const onFind = jest.fn();
        render(<NotInstalledHere missing={missing()} onFind={onFind} />);
        fireEvent.click(screen.getByTestId('missing-find-Noc|quick-mcp'));
        expect(onFind).toHaveBeenCalledWith('quick-mcp');
        expect(screen.queryByTestId('missing-find-Noc|nova')).toBeNull();
    });
    it('without a find handler the servers are still named, with no action', () => {
        render(<NotInstalledHere missing={missing()} />);
        expect(screen.getByTestId('missing-item-Noc|quick-mcp')).toHaveTextContent('quick-mcp');
        expect(screen.queryByTestId('missing-find-Noc|quick-mcp')).toBeNull();
    });
});

const packaged = (): BenchItemView[] => [
    { ...mk('skill:triage', { provenance: 'user' }), groups: [['Noc']] },
    mk('mcp:fetch', { provenance: 'user' }),
];

function panelModel(over: Partial<BenchPanelModel> = {}): BenchPanelModel {
    return {
        ready: true, error: null, items: packaged(), dropped: [],
        weight: { always: 10, ondemand: 10, environment: 0 },
        deltas: { conversation: 0, project: 0, user: 0 },
        lenses: ['project', 'user'], lens: 'project',
        setLens: jest.fn(), place: jest.fn(async () => {}),
        missing: missing(),
        ...over,
    };
}

describe('in the panel', () => {
    beforeEach(() => localStorage.clear());

    it('shows the strip, and not when nothing is missing', () => {
        const { unmount } = render(<BenchPanel bench={panelModel()} />);
        expect(screen.getByTestId('bench-missing')).toBeInTheDocument();
        unmount();
        render(<BenchPanel bench={panelModel({ missing: [] })} />);
        expect(screen.getByTestId('bench-board')).toBeInTheDocument();
        expect(screen.queryByTestId('bench-missing')).toBeNull();
    });
    it('has no find action when the bench cannot search', () => {
        render(<BenchPanel bench={panelModel()} />);
        expect(screen.getByTestId('missing-item-Noc|quick-mcp')).toBeInTheDocument();
        expect(screen.queryByTestId('missing-find-Noc|quick-mcp')).toBeNull();
    });
    it('find opens the Find view already searching for that server', async () => {
        const find = jest.fn(async () => ({ needs: [], updates: [], pairs_with: [] }));
        render(<BenchPanel bench={panelModel({ find })} />);
        await act(async () => { fireEvent.click(screen.getByTestId('missing-find-Noc|quick-mcp')); });
        expect(screen.getByTestId('mode-find')).toHaveAttribute('aria-selected', 'true');
        expect(screen.getByTestId('find-input')).toHaveValue('quick-mcp');
        expect(screen.queryByTestId('bench-missing')).toBeNull();          // the board's strip is gone in Find
    });
    it('choosing the find tab by hand starts with an empty query', async () => {
        const find = jest.fn(async () => ({ needs: [], updates: [], pairs_with: [] }));
        render(<BenchPanel bench={panelModel({ find })} />);
        await act(async () => { fireEvent.click(screen.getByTestId('missing-find-Noc|quick-mcp')); });
        await act(async () => { fireEvent.click(screen.getByTestId('mode-arrange')); });
        await act(async () => { fireEvent.click(screen.getByTestId('mode-find')); });
        expect(screen.getByTestId('find-input')).toHaveValue('');
    });
    // jsdom has no layout, so these pin the three style properties the fix
    // rests on.  The layout itself (a long strip squeezing the board row to
    // zero height, so the lanes painted under the strip) was reproduced and
    // checked fixed in Chromium.
    it('the strip is bounded and scrolls, so a long list cannot take the board\'s height', () => {
        render(<BenchPanel bench={panelModel()} />);
        const strip = screen.getByTestId('bench-missing');
        expect(strip.style.overflowY).toBe('auto');
        expect(parseInt(strip.style.maxHeight, 10)).toBeGreaterThan(0);
        expect(strip.style.flexShrink).toBe('0');
        expect(within(strip).getByTestId('missing-pkg-Noc')).toBeInTheDocument();   // still all listed
    });
    it('the board row keeps a floor and the panel scrolls instead of overlapping', () => {
        render(<BenchPanel bench={panelModel()} />);
        const row = screen.getByTestId('bench-board-row');
        expect(within(row).getByTestId('bench-board')).toBeInTheDocument();
        expect(parseInt(row.style.minHeight, 10)).toBeGreaterThan(0);
        expect(screen.getByTestId('bench-panel').style.overflowY).toBe('auto');
    });
    it('by package, the package row carries the count', () => {
        localStorage.setItem(GROUP_PREF_KEY, 'package');
        render(<BenchPanel bench={panelModel()} />);
        expect(screen.getByTestId('group-missing-ondemand|Noc')).toHaveTextContent('3 not installed');
        expect(screen.queryByTestId('group-missing-always|Not from a package')).toBeNull();
    });
});
