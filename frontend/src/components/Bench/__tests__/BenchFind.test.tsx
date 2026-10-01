/**
 * @jest-environment jsdom
 *
 * The Find view is a renderer over ``find``/``install`` (design/capabilities-hub.md,
 * slice 8b).  It makes no network call; the home panels and the per-source
 * columns are exactly what the model returned; browse is a query, never a shelf.
 */
import React from 'react';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import '@testing-library/jest-dom';
import { BenchActionsProvider } from '../BenchActions';
import { BenchFind, DEBOUNCE_MS } from '../BenchFind';
import { BenchPanel } from '../BenchPanel';
import type { FindHit, FindHome, FindResults } from '../../../hooks/useBench';
import { mk } from '../benchTestUtils';

const hit = (source: string, id: string, over: Partial<FindHit> = {}): FindHit =>
    ({ source, id, name: id, description: `${id} does things`, installed: false, missing_requires: [], ...over });

const HOME: FindHome = {
    needs: [{ key: 'skill:beta', name: 'beta', missing: ['mcp:slack'] }],
    updates: [{ key: 'skill:alpha', name: 'alpha', source: 'local', id: 'alpha',
                installed_version: 'aaaa1111bbbb2222', current_version: 'cccc3333dddd4444' }],
    pairs_with: [hit('local', 'gamma', { requires: ['mcp:jira'] })],
};
const RESULTS: FindResults = { q: 'jira', per_source: [
    { source: 'local', name: 'Local', kind: 'folder', total: 1, hits: [hit('local', 'jira-notes')] },
    { source: 'aim', name: 'Amazon · AIM', kind: 'aim', total: 1451,
      hits: [hit('aim', 'SetA', { support_level: 'Recommended', matched: ['jira-search', 'jira-write'], installed: true }),
             hit('aim', 'SetB', { missing_requires: ['mcp:jira-mcp'] })] },
    { source: 'mcp-registry', name: 'MCP registry', kind: 'mcp', total: 3,
      hits: [hit('mcp-registry', 'svc-1', { name: 'jira-mcp', matched: ['search_issues'] })] },
    { source: 'dead', name: 'Dead', kind: 'git', total: null, hits: [], error: 'down' },
] };

function setup(over: { install?: jest.Mock; actions?: { openRegistry?: () => void } } = {}) {
    const find = jest.fn(async (q: string) => (q ? RESULTS : HOME));
    const ui = <BenchFind model={{ find, install: over.install, lens: 'project' }} />;
    render(over.actions ? <BenchActionsProvider actions={over.actions}>{ui}</BenchActionsProvider> : ui);
    return { find };
}
const type = async (q: string) => {
    fireEvent.change(screen.getByTestId('find-input'), { target: { value: q } });
    return screen.findByTestId('find-columns', {}, { timeout: DEBOUNCE_MS + 1000 });
};

let fetchSpy: jest.Mock;
beforeEach(() => { fetchSpy = jest.fn(); (global as any).fetch = fetchSpy; });

describe('home: only what this bench implies', () => {
    it('renders needs / updates / pairs-with from find("") with zero network', async () => {
        const { find } = setup();
        expect(await screen.findByTestId('find-home')).toBeInTheDocument();
        expect(find).toHaveBeenCalledWith('', 8);
        expect(screen.getByTestId('find-needs')).toHaveTextContent('beta');
        expect(screen.getByTestId('find-needs')).toHaveTextContent('mcp:slack');
        expect(screen.getByTestId('find-updates')).toHaveTextContent('alpha');
        expect(screen.getByTestId('find-pairs')).toHaveTextContent('gamma');
        expect(screen.queryByTestId('find-columns')).toBeNull();
        expect(fetchSpy).not.toHaveBeenCalled();
    });
    it('an empty home says so rather than listing a catalog', async () => {
        const find = jest.fn(async () => ({ needs: [], updates: [], pairs_with: [] }));
        render(<BenchFind model={{ find, lens: 'user' }} />);
        expect(await screen.findByTestId('find-home-empty')).toBeInTheDocument();
        expect(find).toHaveBeenCalledTimes(1);
    });
    it('a missing server in "needs" becomes the query', async () => {
        const { find } = setup();
        await screen.findByTestId('find-home');
        fireEvent.click(within(screen.getByTestId('find-needs')).getByText('mcp:slack'));
        expect(screen.getByTestId('find-input')).toHaveValue('slack');
        await waitFor(() => expect(find).toHaveBeenCalledWith('slack', 8), { timeout: DEBOUNCE_MS + 1000 });
    });
    it('update calls install with a null placement (the pin is kept by name)', async () => {
        const install = jest.fn(async () => ({}));
        setup({ install });
        await screen.findByTestId('find-updates');
        await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Update alpha' })); });
        expect(install).toHaveBeenCalledWith('local', 'alpha', null);
        expect(screen.getByRole('status')).toHaveTextContent('alpha updated');
    });
});

describe('query: per-source columns', () => {
    it('one column per source with hits, totals, more, errors -- and no fetch', async () => {
        const { find } = setup();
        await screen.findByTestId('find-home');
        await type('jira');
        expect(find).toHaveBeenLastCalledWith('jira', 8);
        expect(screen.getAllByTestId(/^find-col-/)).toHaveLength(4);
        const aim = screen.getByTestId('find-col-aim');
        expect(within(aim).getByTestId('hit-aim:SetA')).toHaveTextContent('jira-search, jira-write');
        expect(within(aim).getByTestId('hit-installed-SetA')).toBeInTheDocument();
        expect(within(aim).getByTestId('hit-support')).toHaveTextContent('Recommended');
        expect(within(aim).getByTestId('hit-needs-SetB')).toHaveTextContent('needs mcp:jira-mcp');
        expect(within(aim).getByTestId('find-more-aim')).toHaveTextContent('+1449 more');
        expect(screen.getByTestId('find-col-dead')).toHaveTextContent('unavailable: down');
        expect(fetchSpy).not.toHaveBeenCalled();
    });
    it('install buttons call install(source, id, state) and skip installed hits', async () => {
        const install = jest.fn(async () => ({}));
        setup({ install });
        await screen.findByTestId('find-home');
        await type('jira');
        expect(screen.queryByRole('button', { name: 'Install SetA on hand' })).toBeNull();     // already installed
        await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Install SetB within reach' })); });
        expect(install).toHaveBeenCalledWith('aim', 'SetB', 'ondemand');
        await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Install jira-notes on hand' })); });
        expect(install).toHaveBeenCalledWith('local', 'jira-notes', 'always');
        expect(screen.getByRole('status')).toHaveTextContent('jira-notes installed → on hand');
    });
    it('a 409 from install is phrased, not swallowed', async () => {
        const install = jest.fn(async () => { throw Object.assign(new Error('x'), { reason: 'mcp_ondemand_unsupported' }); });
        setup({ install });
        await screen.findByTestId('find-home');
        await type('jira');
        await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Install SetB within reach' })); });
        expect(screen.getByRole('status')).toHaveTextContent('coming soon');
    });
    it('MCP hits link to the registry and never offer a local install', async () => {
        const openRegistry = jest.fn();
        setup({ install: jest.fn(), actions: { openRegistry } });
        await screen.findByTestId('find-home');
        await type('jira');
        const mcp = screen.getByTestId('find-col-mcp-registry');
        expect(within(mcp).queryByRole('button', { name: /Install/ })).toBeNull();
        fireEvent.click(within(mcp).getByTestId('find-open-registry'));
        expect(openRegistry).toHaveBeenCalledTimes(1);
    });
    it('without registry actions the MCP column has no link', async () => {
        setup();
        await screen.findByTestId('find-home');
        await type('jira');
        expect(screen.queryByTestId('find-open-registry')).toBeNull();
    });
});

describe('BenchPanel arrange/find toggle', () => {
    const panel = (find?: jest.Mock) => ({
        ready: true, error: null, items: [mk('skill:Concise', { project: 'always', def: 'off' })], dropped: [],
        weight: { always: 10, ondemand: 0, environment: 0 }, deltas: { conversation: 0, project: 1, user: 0 },
        lenses: ['project', 'user'] as const, lens: 'project' as const, setLens: () => {}, place: async () => {}, find,
    });
    it('toggle appears only when the model can find; find hides the board, arrange restores it', async () => {
        const { unmount } = render(<BenchPanel bench={panel() as any} />);
        expect(screen.queryByTestId('mode-find')).toBeNull();
        unmount();
        const find = jest.fn(async () => ({ needs: [], updates: [], pairs_with: [] }));
        render(<BenchActionsProvider actions={{ openRegistry: () => {} }}><BenchPanel bench={panel(find) as any} /></BenchActionsProvider>);
        expect(screen.getByTestId('bench-board')).toBeInTheDocument();
        expect(screen.getByTestId('bench-find-more')).toBeInTheDocument();
        expect(find).not.toHaveBeenCalled();                          // arrange mode asks nothing
        fireEvent.click(screen.getByTestId('mode-find'));
        expect(await screen.findByTestId('bench-find')).toBeInTheDocument();
        expect(screen.queryByTestId('bench-board')).toBeNull();
        expect(screen.queryByTestId('bench-find-more')).toBeNull();   // the MCP column carries the registry link
        fireEvent.click(screen.getByTestId('mode-arrange'));
        expect(screen.getByTestId('bench-board')).toBeInTheDocument();
    });
});
