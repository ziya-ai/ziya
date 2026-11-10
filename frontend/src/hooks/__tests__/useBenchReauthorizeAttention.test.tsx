/**
 * @jest-environment jsdom
 *
 * Re-authorizing a quarantined server from the Bench drawer must tell the
 * rest of the UI that MCP status changed.  useHeaderAttention (which colours
 * the Bench's "MCP servers" button orange for a quarantine) and
 * TokenCountDisplay re-fetch /api/mcp/status only on `mcpStatusChanged`;
 * without it the button stays orange after the quarantine is lifted.
 * Fails without the MCP_STATUS_CHANGED_EVENT dispatch in useBench.reauthorize.
 */
import React from 'react';
import { act, render, waitFor } from '@testing-library/react';

jest.mock('../../apis/benchApi', () => ({
    fetchBench: jest.fn(),
    reauthorizeBenchServer: jest.fn(),
}));
import { fetchBench, reauthorizeBenchServer } from '../../apis/benchApi';
import { useBench, MCP_STATUS_CHANGED_EVENT, BENCH_CHANGED_EVENT } from '../useBench';

const view = {
    items: [], dropped: [], lenses: ['project', 'user'],
    deltas: { conversation: 0, project: 0, user: 0 },
    weight: { always: 0, ondemand: 0, environment: 0 }, conversation_id: null,
};

let hook: ReturnType<typeof useBench> | null = null;
const Host = () => { hook = useBench({ projectId: 'P' }); return null; };

afterEach(() => { hook = null; jest.clearAllMocks(); });

async function mountAndReauthorize(result: any, force = false) {
    (fetchBench as jest.Mock).mockResolvedValue(view);
    (reauthorizeBenchServer as jest.Mock).mockResolvedValue({ result, detail: {}, view });
    const mcpChanged = jest.fn();
    const benchChanged = jest.fn();
    window.addEventListener(MCP_STATUS_CHANGED_EVENT, mcpChanged);
    window.addEventListener(BENCH_CHANGED_EVENT, benchChanged);
    render(<Host />);
    await waitFor(() => expect(hook).not.toBeNull());
    await act(async () => { await hook!.reauthorize('pippin', force); });
    window.removeEventListener(MCP_STATUS_CHANGED_EVENT, mcpChanged);
    window.removeEventListener(BENCH_CHANGED_EVENT, benchChanged);
    return { mcpChanged, benchChanged };
}

it('a successful re-authorization announces mcpStatusChanged so header attention re-polls', async () => {
    const { mcpChanged, benchChanged } = await mountAndReauthorize({ success: true });
    expect(reauthorizeBenchServer).toHaveBeenCalledWith('P', 'pippin', false, undefined);
    expect(benchChanged).toHaveBeenCalledTimes(1);   // path ran: the bench announcement still fires
    expect(mcpChanged).toHaveBeenCalledTimes(1);
});

it('a refusal (quarantine still in place) does not announce an MCP status change', async () => {
    const { mcpChanged, benchChanged } = await mountAndReauthorize(
        { success: false, can_force: true, blocking_tools: { t: ['x'] } });
    expect(benchChanged).toHaveBeenCalledTimes(1);   // positive: reauthorize completed
    expect(mcpChanged).not.toHaveBeenCalled();
});

it('useHeaderAttention listens for the same event name the bench now fires', () => {
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const fs = require('fs'); const path = require('path');
    const src = fs.readFileSync(path.join(__dirname, '..', 'useHeaderAttention.ts'), 'utf8');
    expect(src).toContain(`addEventListener('${MCP_STATUS_CHANGED_EVENT}'`);
});
