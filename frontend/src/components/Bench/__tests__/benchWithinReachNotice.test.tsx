/**
 * @jest-environment jsdom
 *
 * The within-reach notice: which servers it names, that Dismiss and
 * "keep on hand" are remembered per server, and that the panel shows it.
 */
import React from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom';
import { BenchPanel, BenchPanelModel } from '../BenchPanel';
import { REACH_NOTICE_KEY, WithinReachNotice, reachDefaults } from '../WithinReachNotice';
import { mk } from '../benchTestUtils';

const reg = (name: string) => mk(`mcp:${name}`, { def: 'ondemand', provenance: 'user' });
const items = () => [
    reg('quip'),
    reg('grafana'),
    mk('mcp:slack', { def: 'ondemand', user: 'always', provenance: 'user' }),   // placed by the user
    mk('mcp:fetch', { provenance: 'user' }),                                    // kind default: on hand
    mk('skill:notes', { provenance: 'user' }),                                  // a skill, on demand
];

beforeEach(() => localStorage.clear());

describe('reachDefaults', () => {
    it('names only unplaced MCP servers whose default is within reach', () => {
        expect(reachDefaults(items()).map(i => i.key)).toEqual(['mcp:quip', 'mcp:grafana']);
    });
});

describe('WithinReachNotice', () => {
    it('renders nothing when no server is within reach by default', () => {
        const { container } = render(
            <WithinReachNotice items={[mk('mcp:fetch', { provenance: 'user' })]} place={jest.fn()} />);
        expect(container).toBeEmptyDOMElement();
    });
    it('counts and names the servers', () => {
        render(<WithinReachNotice items={items()} place={jest.fn()} />);
        const notice = screen.getByTestId('reach-notice');
        expect(notice).toHaveTextContent('2 MCP servers sit within reach by default');
        expect(notice).toHaveTextContent('quip, grafana');
        expect(notice).not.toHaveTextContent('slack');
    });
    it('Dismiss hides it, places nothing and is remembered', () => {
        const place = jest.fn(async () => {});
        const { unmount } = render(<WithinReachNotice items={items()} place={place} />);
        fireEvent.click(screen.getByTestId('reach-dismiss'));
        expect(screen.queryByTestId('reach-notice')).toBeNull();
        expect(place).not.toHaveBeenCalled();
        expect(JSON.parse(localStorage.getItem(REACH_NOTICE_KEY)!).sort()).toEqual(['mcp:grafana', 'mcp:quip']);
        unmount();
        render(<WithinReachNotice items={items()} place={place} />);
        expect(screen.queryByTestId('reach-notice')).toBeNull();
    });
    it('a server that arrives after a dismissal is announced on its own', () => {
        localStorage.setItem(REACH_NOTICE_KEY, JSON.stringify(['mcp:quip', 'mcp:grafana']));
        render(<WithinReachNotice items={[...items(), reg('leolens')]} place={jest.fn()} />);
        const notice = screen.getByTestId('reach-notice');
        expect(notice).toHaveTextContent('1 MCP server sits within reach by default');
        expect(notice).toHaveTextContent('leolens');
        expect(notice).not.toHaveTextContent('quip');
    });
    it('"Keep all on hand" pins each at the user layer, then goes away', async () => {
        const place = jest.fn(async () => {});
        render(<WithinReachNotice items={items()} place={place} />);
        await act(async () => { fireEvent.click(screen.getByTestId('reach-keep')); });
        expect(place.mock.calls).toEqual([['mcp:quip', 'always', 'user'], ['mcp:grafana', 'always', 'user']]);
        expect(screen.queryByTestId('reach-notice')).toBeNull();
    });
    it('a server that could not be pinned stays listed', async () => {
        const place = jest.fn(async (key: string) => { if (key === 'mcp:grafana') throw new Error('nope'); });
        render(<WithinReachNotice items={items()} place={place} />);
        await act(async () => { fireEvent.click(screen.getByTestId('reach-keep')); });
        const notice = screen.getByTestId('reach-notice');
        expect(notice).toHaveTextContent('1 MCP server sits within reach by default');
        expect(notice).toHaveTextContent('grafana');
        expect(JSON.parse(localStorage.getItem(REACH_NOTICE_KEY)!)).toEqual(['mcp:quip']);
    });
});

function panelModel(over: Partial<BenchPanelModel> = {}): BenchPanelModel {
    return {
        ready: true, error: null, items: items(), dropped: [],
        weight: { always: 20, ondemand: 30, environment: 0 },
        deltas: { conversation: 0, project: 0, user: 1 },
        lenses: ['project', 'user'], lens: 'project',
        setLens: jest.fn(), place: jest.fn(async () => {}),
        ...over,
    };
}

describe('in the panel', () => {
    it('is shown above the board and reports through the panel model', async () => {
        const m = panelModel();
        render(<BenchPanel bench={m} />);
        expect(screen.getByTestId('bench-board')).toBeInTheDocument();
        expect(screen.getByTestId('reach-notice')).toBeInTheDocument();
        await act(async () => { fireEvent.click(screen.getByTestId('reach-keep')); });
        expect(m.place).toHaveBeenCalledWith('mcp:quip', 'always', 'user');
    });
    it('is absent when every server is at the on-hand default', () => {
        render(<BenchPanel bench={panelModel({ items: [mk('mcp:fetch', { provenance: 'user' })] })} />);
        expect(screen.getByTestId('bench-board')).toBeInTheDocument();
        expect(screen.queryByTestId('reach-notice')).toBeNull();
    });
});
