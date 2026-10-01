/**
 * @jest-environment jsdom
 *
 * BenchPanel is the hub body shared by every shell.  It is handed a model and
 * must render it without fetching or re-deriving anything.
 */
import React from 'react';
import { fireEvent, render, screen, within } from '@testing-library/react';
import '@testing-library/jest-dom';
import { BenchPanel, BenchPanelModel } from '../BenchPanel';
import { mk } from '../benchTestUtils';

function model(over: Partial<BenchPanelModel> = {}): BenchPanelModel {
    return {
        ready: true, error: null,
        items: [
            mk('skill:Concise', { project: 'always', def: 'off' }),   // pinned on hand, 10t
            mk('mcp:jira', { provenance: 'user' }),                   // default always, 10t
            mk('mcp:shell', { tier: 'environment' }),                 // environment, 10t
        ],
        dropped: [],
        weight: { always: 20, ondemand: 0, environment: 10 },
        deltas: { conversation: 0, project: 1, user: 0 },
        lenses: ['conversation', 'project', 'user'],
        lens: 'project',
        setLens: jest.fn(),
        place: jest.fn(async () => {}),
        ...over,
    };
}

describe('BenchPanel', () => {
    const realFetch = (global as any).fetch;
    afterEach(() => { (global as any).fetch = realFetch; });

    it('makes no network call, even when interacted with', () => {
        const f = jest.fn();
        (global as any).fetch = f;
        const m = model();
        render(<BenchPanel bench={m} />);
        fireEvent.click(screen.getByTestId('lens-user'));
        fireEvent.click(screen.getByText('inspect ▸'));
        expect(screen.getByTestId('bench-board')).toBeInTheDocument();   // positive: it rendered
        expect(f).not.toHaveBeenCalled();
    });

    it('lens strip: offered lenses, delta only where pinned, switch reports up', () => {
        const m = model();
        render(<BenchPanel bench={m} />);
        expect(screen.getByTestId('lens-project')).toHaveAttribute('aria-selected', 'true');
        expect(screen.getByTestId('lens-delta-project')).toHaveTextContent('+1');
        expect(screen.queryByTestId('lens-delta-user')).toBeNull();
        fireEvent.click(screen.getByTestId('lens-conversation'));
        expect(m.setLens).toHaveBeenCalledWith('conversation');
    });

    it('omits the conversation lens when the server does not offer it', () => {
        render(<BenchPanel bench={model({ lenses: ['project', 'user'] })} />);
        expect(screen.getByTestId('lens-project')).toBeInTheDocument();
        expect(screen.queryByTestId('lens-conversation')).toBeNull();
    });

    it('environment is below the line, never a lane; detail on inspect', () => {
        render(<BenchPanel bench={model()} />);
        const board = screen.getByTestId('bench-board');
        expect(within(board).queryByTestId('card-mcp:shell')).toBeNull();
        expect(within(board).getByTestId('card-mcp:jira')).toBeInTheDocument();
        const row = screen.getByTestId('environment-row');
        expect(row).toHaveTextContent('shell');
        expect(screen.queryByTestId('environment-detail')).toBeNull();
        fireEvent.click(within(row).getByText('inspect ▸'));
        expect(screen.getByTestId('environment-detail')).toHaveTextContent('shell');
    });

    it('gauge segments are the on-hand placeable items; environment is the base', () => {
        render(<BenchPanel bench={model()} />);
        expect(screen.getByTestId('weight-seg-skill:Concise')).toBeInTheDocument();
        expect(screen.getByTestId('weight-seg-mcp:jira')).toBeInTheDocument();
        expect(screen.queryByTestId('weight-seg-mcp:shell')).toBeNull();
        expect(screen.getByTestId('weight-env')).toBeInTheDocument();
        expect(screen.getByTestId('weight-gauge')).toHaveTextContent('20 on hand');
    });

    it('names placements for things not installed here, and only those', () => {
        render(<BenchPanel bench={model({ dropped: [
            { key: 'skill:Gone', layer: 'project', reason: 'unknown_key' },
            { key: 'mcp:jira', layer: 'user', reason: 'mcp_ondemand_unsupported' },
        ] })} />);
        const note = screen.getByTestId('bench-not-installed');
        expect(note).toHaveTextContent('Gone');
        expect(note).not.toHaveTextContent('jira');
    });

    it('loading state renders no board', () => {
        render(<BenchPanel bench={model({ ready: false })} />);
        expect(screen.getByTestId('bench-loading')).toBeInTheDocument();
        expect(screen.queryByTestId('bench-board')).toBeNull();
    });

    it('selecting a card opens the drawer beside the board; another card swaps it; ✕ closes', async () => {
        const detail = async (key: string) => ({
            key, kind: (key.startsWith('mcp:') ? 'mcp' : 'skill') as 'mcp' | 'skill',
            name: key.slice(key.indexOf(':') + 1), description: `about ${key}`,
        });
        render(<BenchPanel bench={model({ detail })} />);
        expect(screen.queryByTestId('bench-detail')).toBeNull();
        fireEvent.click(screen.getByRole('button', { name: 'Details for Concise' }));
        expect(await screen.findByText('about skill:Concise')).toBeInTheDocument();
        expect(screen.getByTestId('card-skill:Concise')).toHaveAttribute('data-selected', 'true');
        fireEvent.click(screen.getByRole('button', { name: 'Details for jira' }));
        expect(await screen.findByText('about mcp:jira')).toBeInTheDocument();
        expect(screen.getByTestId('card-skill:Concise')).toHaveAttribute('data-selected', 'false');
        expect(screen.getAllByTestId('bench-detail')).toHaveLength(1);
        fireEvent.click(screen.getByTestId('detail-close'));
        expect(screen.queryByTestId('bench-detail')).toBeNull();
    });

    it('a flash renders the same delta chip on gauge, card and drawer; a partial server is marked for good', async () => {
        const jira = { ...mk('mcp:jira', { provenance: 'user' }), tokens: 1200,
                       health: { tool_count: 2, enabled_count: 1, withheld_tokens: 800 } };
        const detail = async () => ({ key: 'mcp:jira', kind: 'mcp' as const, name: 'jira', description: '', tools: ['a', 'b'],
            tool_details: [{ name: 'a', description: '', tokens: 1200, enabled: true }, { name: 'b', description: '', tokens: 800, enabled: false }] });
        const m = model({ items: [jira], weight: { always: 1200, ondemand: 0, environment: 0 }, detail,
                          flash: { byKey: { 'mcp:jira': -800 }, always: -800, at: 0 } });
        render(<BenchPanel bench={m} />);
        expect(screen.getByTestId('weight-delta')).toHaveTextContent('−800t');
        expect(screen.getByTestId('weight-withheld-stub')).toBeInTheDocument();
        expect(screen.getByTestId('delta-mcp:jira')).toHaveTextContent('−800t');
        expect(screen.getByTestId('partial-mcp:jira')).toHaveTextContent('1/2');
        expect(screen.getByTestId('lane-tokens-always')).toHaveTextContent('1.2K');
        fireEvent.click(screen.getByRole('button', { name: 'Details for jira' }));
        const summary = await screen.findByTestId('detail-tools-summary');
        expect(summary).toHaveTextContent('1 of 2 enabled · 1,200t on hand');
        expect(summary).toHaveTextContent('800t withheld');
        expect(screen.getByTestId('detail-delta')).toHaveTextContent('−800t');
        // No flash: chips and stub are gone, the partial mark stays.
        const { container: quiet } = render(<BenchPanel bench={model({ items: [jira], flash: null })} />);
        expect(within(quiet).queryByTestId('weight-withheld-stub')).toBeNull();
        expect(within(quiet).queryByTestId('delta-mcp:jira')).toBeNull();
        expect(within(quiet).getByTestId('partial-mcp:jira')).toHaveTextContent('1/2');
    });
});
