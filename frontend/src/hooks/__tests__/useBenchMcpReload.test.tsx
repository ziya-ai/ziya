/**
 * @jest-environment jsdom
 *
 * The MCP Servers panel's Reload Config re-reads mcp_config.json and
 * reconnects, then fires `mcpStatusChanged`.  A server added to the config
 * has no card until the bench refetches, so useBench must listen for that
 * event.  Fails without the MCP_STATUS_CHANGED_EVENT listener in useBench.
 */
import React from 'react';
import { act, render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom';

jest.mock('../../apis/benchApi', () => ({ fetchBench: jest.fn() }));
import { fetchBench } from '../../apis/benchApi';
import { useBench, MCP_STATUS_CHANGED_EVENT } from '../useBench';

const view = (servers: string[]) => ({
    items: servers.map(name => ({
        key: `mcp:${name}`, kind: 'mcp', tier: 'placeable', name, provenance: 'user', tokens: 1,
        health: {}, placements: { conversation: null, project: null, user: null },
        effective: 'ondemand', origin: 'default', default: 'ondemand',
    })),
    dropped: [], lenses: ['project', 'user'],
    deltas: { conversation: 0, project: 0, user: 0 },
    weight: { always: 0, ondemand: 0, environment: 0 }, conversation_id: null,
});

const Reader = () => {
    const b = useBench({ projectId: 'P' });
    return <div data-testid="names">{b.items.map(i => i.name).join(',') || '-'}</div>;
};

it('a server that appears after Reload Config gets a card without reopening the bench', async () => {
    (fetchBench as jest.Mock).mockResolvedValue(view(['jira']));
    render(<Reader />);
    await waitFor(() => expect(screen.getByTestId('names')).toHaveTextContent('jira'));
    const before = (fetchBench as jest.Mock).mock.calls.length;

    (fetchBench as jest.Mock).mockResolvedValue(view(['jira', 'confluence']));
    // What MCPStatusModal.reinitializeMCP dispatches after POST /api/mcp/initialize.
    act(() => { window.dispatchEvent(new Event(MCP_STATUS_CHANGED_EVENT)); });

    await waitFor(() => expect(screen.getByTestId('names')).toHaveTextContent('jira,confluence'));
    expect((fetchBench as jest.Mock).mock.calls.length).toBe(before + 1);
    expect(MCP_STATUS_CHANGED_EVENT).toBe('mcpStatusChanged');   // the name the modal actually fires
});
