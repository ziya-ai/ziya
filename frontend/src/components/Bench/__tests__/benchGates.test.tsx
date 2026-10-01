/**
 * @jest-environment jsdom
 *
 * Authorization gates on the bench: a quarantined server is marked on its
 * card, is not counted on hand, and is re-authorized from the drawer -- a
 * refusal shows the flagged tools and offers the override.  Renderers only;
 * no network.
 */
import React from 'react';
import { act, fireEvent, render, screen, within } from '@testing-library/react';
import '@testing-library/jest-dom';
import { BenchPanel } from '../BenchPanel';
import { CapabilityCard } from '../CapabilityCard';
import { mk } from '../benchTestUtils';

const GATE = { state: 'quarantined', reason: 'Tool definitions changed since they were approved' };
const held = () => ({ ...mk('mcp:pippin', { provenance: 'user' }), tokens: 900,
                      health: { connected: true, quarantined: true, gate: GATE, tool_count: 1, enabled_count: 1 } });

it('the card wears the gate, the clean one does not', () => {
    render(<><CapabilityCard item={held()} onIntent={() => {}} /><CapabilityCard item={mk('mcp:jira')} onIntent={() => {}} /></>);
    const card = screen.getByTestId('card-mcp:pippin');
    expect(card).toHaveAttribute('data-held', 'true');
    expect(within(card).getByTestId('gate-mcp:pippin')).toHaveTextContent('needs re-authorization');
    expect(card).toHaveAttribute('data-state', 'always');            // the placement is untouched
    expect(screen.getByTestId('card-mcp:jira')).toHaveAttribute('data-held', 'false');
    expect(screen.queryByTestId('gate-mcp:jira')).toBeNull();
});

it('a held item is not on hand in the gauge; re-authorizing from the drawer clears it, refusals offer the override', async () => {
    const detail = jest.fn(async () => ({ key: 'mcp:pippin', kind: 'mcp' as const, name: 'pippin', description: '',
                                          tools: ['mcp_scan'], connected: true, gate: GATE }));
    const cleared = { ...held(), health: { connected: true, gate: null } };
    const refusal = { success: false, message: 'Re-authorization refused', can_force: true,
                      blocking_tools: { mcp_scan: ['injection pattern'] }, advisory_tools: {} };
    const reauthorize = jest.fn(async (_s: string, force?: boolean) => force
        ? { result: { success: true, message: 'pippin re-authorized' }, detail: { key: 'mcp:pippin', kind: 'mcp' as const, name: 'pippin', description: '', gate: null },
            view: {} as any }
        : { result: refusal, detail: { key: 'mcp:pippin', kind: 'mcp' as const, name: 'pippin', description: '', gate: GATE }, view: {} as any });
    const model = (items: any[]) => ({
        ready: true, error: null, items, dropped: [], weight: { always: 0, ondemand: 0, environment: 0 },
        deltas: { conversation: 0, project: 0, user: 0 }, lenses: ['project', 'user'] as const, lens: 'project' as const,
        setLens: () => {}, place: async () => {}, detail, reauthorize,
    });
    const f = jest.fn(); (global as any).fetch = f;
    const { rerender } = render(<BenchPanel bench={model([held()]) as any} />);
    expect(screen.queryByTestId('weight-seg-mcp:pippin')).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'Details for pippin' }));
    const gate = await screen.findByTestId('detail-gate');
    expect(gate).toHaveTextContent('DEFINITIONS CHANGED');
    await act(async () => { fireEvent.click(screen.getByTestId('detail-reauthorize')); });
    expect(reauthorize).toHaveBeenCalledWith('pippin', false);
    expect(screen.getByTestId('detail-gate-refusal')).toHaveTextContent('mcp_scan');
    expect(screen.getByTestId('detail-gate-refusal')).toHaveTextContent('injection pattern');
    expect(screen.queryByTestId('detail-reauthorize')).toBeNull();                 // the plain button yields to the override
    await act(async () => { fireEvent.click(screen.getByTestId('detail-reauthorize-force')); });
    expect(reauthorize).toHaveBeenLastCalledWith('pippin', true);
    expect(screen.getByRole('status')).toHaveTextContent('pippin re-authorized');
    expect(screen.queryByTestId('detail-gate')).toBeNull();                        // the detail's gate is gone

    rerender(<BenchPanel bench={model([cleared]) as any} />);                        // the view the hook would set
    expect(screen.getByTestId('card-mcp:pippin')).toHaveAttribute('data-held', 'false');
    expect(screen.getByTestId('weight-seg-mcp:pippin')).toBeInTheDocument();
    expect(f).not.toHaveBeenCalled();
});

it('without a reauthorize callback the drawer states the gate but offers no button', async () => {
    const detail = async () => ({ key: 'mcp:pippin', kind: 'mcp' as const, name: 'pippin', description: '', gate: GATE });
    render(<BenchPanel bench={{ ready: true, error: null, items: [held()], dropped: [], weight: { always: 0, ondemand: 0, environment: 0 },
                                deltas: { conversation: 0, project: 0, user: 0 }, lenses: ['project'], lens: 'project',
                                setLens: () => {}, place: async () => {}, detail } as any} />);
    fireEvent.click(screen.getByRole('button', { name: 'Details for pippin' }));
    expect(await screen.findByTestId('detail-gate')).toBeInTheDocument();
    expect(screen.queryByTestId('detail-reauthorize')).toBeNull();
});
