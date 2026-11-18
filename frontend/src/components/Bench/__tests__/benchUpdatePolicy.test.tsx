/**
 * @jest-environment jsdom
 *
 * Update policy in the drawer: a held server says what changed and can be
 * re-authorized once or re-authorized and set to follow updates; any MCP
 * server shows its policy and lets it be changed.  Renderers only; no network.
 */
import React from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom';
import { BenchPanel } from '../BenchPanel';
import { mk } from '../benchTestUtils';

const GATE = { state: 'quarantined', reason: 'Tool definitions changed since they were approved' };
const ASK = { policy: 'ask' as const, via: null, publisher: 'amazon-internal', lapsed: false, publisher_follows: false };
const CHANGES = { known: true, added: ['quip_export'], removed: [], changed: [{ name: 'quip_read', what: ['description'] }],
                  summary: '1 added, 1 changed' };
const base = { key: 'mcp:quip', kind: 'mcp' as const, name: 'quip', description: '', tools: ['quip_read'], connected: true };

const model = (items: any[], over: Record<string, unknown>) => ({
    ready: true, error: null, items, dropped: [], weight: { always: 0, ondemand: 0, environment: 0 },
    deltas: { conversation: 0, project: 0, user: 0 }, lenses: ['project', 'user'] as const, lens: 'project' as const,
    setLens: () => {}, place: async () => {}, ...over,
});
const open = async () => {
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Details for quip' })); });
};

it('a held server lists what changed; "re-authorize and follow" accepts it and sets the policy', async () => {
    const held = { ...mk('mcp:quip', { provenance: 'user' }), health: { connected: true, quarantined: true, gate: GATE } };
    const detail = jest.fn(async () => ({ ...base, gate: GATE, updates: { ...ASK, changes: CHANGES } }));
    const reauthorize = jest.fn(async () => ({ result: { success: true, message: 'quip re-authorized' },
                                               detail: { ...base, gate: null, updates: ASK }, view: {} as any }));
    const setUpdatePolicy = jest.fn(async () => ({ ...base, gate: null, updates: { ...ASK, policy: 'follow' as const, via: 'server' } }));
    render(<BenchPanel bench={model([held], { detail, reauthorize, setUpdatePolicy }) as any} />);
    await open();

    const changes = await screen.findByTestId('detail-gate-changes');
    expect(changes).toHaveTextContent('quip_export');
    expect(changes).toHaveTextContent('quip_read');
    expect(changes).toHaveTextContent('description');
    expect(screen.getByTestId('detail-reauthorize')).toBeInTheDocument();      // the one-off accept is still offered

    await act(async () => { fireEvent.click(screen.getByTestId('detail-reauthorize-follow')); });
    expect(reauthorize).toHaveBeenCalledWith('quip', false);
    expect(setUpdatePolicy).toHaveBeenCalledWith('quip', 'follow', 'server');
    expect(screen.queryByTestId('detail-gate')).toBeNull();
    expect(screen.getByTestId('update-policy-follow')).toBeChecked();
});

it('a refused re-authorization does not set the policy', async () => {
    const held = { ...mk('mcp:quip', { provenance: 'user' }), health: { connected: true, quarantined: true, gate: GATE } };
    const detail = jest.fn(async () => ({ ...base, gate: GATE, updates: { ...ASK, changes: CHANGES } }));
    const reauthorize = jest.fn(async () => ({
        result: { success: false, message: 'refused', can_force: true, blocking_tools: { quip_read: ['injection pattern'] } },
        detail: { ...base, gate: GATE, updates: { ...ASK, changes: CHANGES } }, view: {} as any }));
    const setUpdatePolicy = jest.fn();
    render(<BenchPanel bench={model([held], { detail, reauthorize, setUpdatePolicy }) as any} />);
    await open();
    await act(async () => { fireEvent.click(await screen.findByTestId('detail-reauthorize-follow')); });
    expect(reauthorize).toHaveBeenCalled();                                    // positive: the click reached the handler
    expect(setUpdatePolicy).not.toHaveBeenCalled();
    expect(screen.queryByTestId('detail-reauthorize-follow')).toBeNull();      // only the reviewed override remains
    expect(screen.getByTestId('detail-reauthorize-force')).toBeInTheDocument();
});

it('an unknown previous definition is stated, not shown as "no changes"', async () => {
    const held = { ...mk('mcp:quip', { provenance: 'user' }), health: { connected: true, quarantined: true, gate: GATE } };
    const unknown = { known: false, added: [], removed: [], changed: [], summary: 'previous definitions were not recorded' };
    const detail = jest.fn(async () => ({ ...base, gate: GATE, updates: { ...ASK, changes: unknown } }));
    render(<BenchPanel bench={model([held], { detail, reauthorize: jest.fn(), setUpdatePolicy: jest.fn() }) as any} />);
    await open();
    expect(await screen.findByTestId('detail-gate-changes')).toHaveTextContent(/not known/i);
});

it('a server that is not held shows its policy and changes it, per server and per publisher', async () => {
    const detail = jest.fn(async () => ({ ...base, gate: null, updates: ASK }));
    const setUpdatePolicy = jest.fn(async (_s: string, policy: string, scope?: string) => ({
        ...base, gate: null,
        updates: scope === 'publisher' ? { ...ASK, policy: 'follow' as const, via: 'publisher', publisher_follows: true }
                                       : { ...ASK, policy: policy as 'ask' | 'follow', via: 'server' } }));
    render(<BenchPanel bench={model([mk('mcp:quip', { provenance: 'user' })], { detail, setUpdatePolicy }) as any} />);
    await open();

    await screen.findByTestId('detail-updates');
    expect(screen.getByTestId('update-policy-ask')).toBeChecked();
    await act(async () => { fireEvent.click(screen.getByTestId('update-policy-follow')); });
    expect(setUpdatePolicy).toHaveBeenLastCalledWith('quip', 'follow', 'server');
    expect(screen.getByTestId('update-policy-follow')).toBeChecked();

    expect(screen.getByTestId('update-policy-publisher')).toHaveAccessibleName(/amazon-internal/);
    await act(async () => { fireEvent.click(screen.getByTestId('update-policy-publisher')); });
    expect(setUpdatePolicy).toHaveBeenLastCalledWith('quip', 'follow', 'publisher');
    expect(screen.getByTestId('update-policy-publisher')).toBeChecked();
});

it('a server with no registry provider offers no publisher grant; a builtin offers no policy at all', async () => {
    const detail = jest.fn(async () => ({ ...base, gate: null, updates: { ...ASK, publisher: null } }));
    const { unmount } = render(<BenchPanel bench={model([mk('mcp:quip', { provenance: 'user' })],
                                                         { detail, setUpdatePolicy: jest.fn() }) as any} />);
    await open();
    await screen.findByTestId('detail-updates');                               // positive: the section rendered
    expect(screen.queryByTestId('update-policy-publisher')).toBeNull();
    unmount();

    const builtin = jest.fn(async () => ({ ...base, gate: null, builtin: true, updates: { ...ASK, publisher: null } }));
    render(<BenchPanel bench={model([mk('mcp:quip', { provenance: 'user' })], { detail: builtin, setUpdatePolicy: jest.fn() }) as any} />);
    await open();
    await screen.findByText(/TOOLS/);                                          // positive: the detail loaded
    expect(screen.queryByTestId('detail-updates')).toBeNull();
});
