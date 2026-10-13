/**
 * @jest-environment jsdom
 *
 * The MCP Servers and Registry panels lost their toolbar buttons (6b); the
 * Bench must reach them.  Callbacks arrive through BenchActionsProvider.
 */
import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom';
import { BenchActionsProvider } from '../BenchActions';
import { CapabilityDetail } from '../CapabilityDetail';
import { BenchPanel } from '../BenchPanel';
import { mk } from '../benchTestUtils';

const detail = async (key: string) => ({ key, kind: 'mcp' as const, name: 'jira', description: '', tools: ['mcp_a'], connected: true });
const panelModel = () => ({
    ready: true, error: null, items: [mk('mcp:jira', { provenance: 'user' })], dropped: [],
    weight: { always: 10, ondemand: 0, environment: 0 }, deltas: { conversation: 0, project: 0, user: 0 },
    lenses: ['project', 'user'] as const, lens: 'project' as const, setLens: () => {}, place: async () => {},
});

it('an MCP detail drawer opens the status panel focused on that server', async () => {
    const openMcpServers = jest.fn();
    render(<BenchActionsProvider actions={{ openMcpServers }}>
        <CapabilityDetail item={mk('mcp:jira', { provenance: 'user' })} detail={detail} onClose={() => {}} />
    </BenchActionsProvider>);
    await waitFor(() => expect(screen.getByTestId('detail-tools')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('mcp-server-details'));
    expect(openMcpServers).toHaveBeenCalledWith('jira');
});

it('the board itself reaches the MCP Servers panel, unfocused, so Reload Config is reachable with no card', () => {
    // A server just added to mcp_config.json has no card yet; the detail
    // drawer's per-server link cannot be the only route to Reload Config.
    const openMcpServers = jest.fn();
    const { unmount } = render(<BenchActionsProvider actions={{ openMcpServers }}>
        <BenchPanel bench={{ ...panelModel(), items: [] } as any} /></BenchActionsProvider>);
    fireEvent.click(screen.getByTestId('bench-mcp-servers'));
    expect(openMcpServers).toHaveBeenCalledTimes(1);
    expect(openMcpServers.mock.calls[0][0]).toBeUndefined();
    unmount();
    render(<BenchPanel bench={panelModel() as any} />);
    expect(screen.queryByTestId('bench-mcp-servers')).toBeNull();
});

it('find more opens the registry; without actions neither control renders', () => {
    const openRegistry = jest.fn();
    const { unmount } = render(<BenchActionsProvider actions={{ openRegistry }}>
        <BenchPanel bench={panelModel() as any} /></BenchActionsProvider>);
    fireEvent.click(screen.getByTestId('bench-find-more'));
    expect(openRegistry).toHaveBeenCalledTimes(1);
    unmount();
    render(<BenchPanel bench={panelModel() as any} />);
    expect(screen.queryByTestId('bench-find-more')).toBeNull();
    render(<CapabilityDetail item={mk('mcp:jira', { provenance: 'user' })} detail={detail} onClose={() => {}} />);
    expect(screen.queryByTestId('mcp-server-details')).toBeNull();
});
