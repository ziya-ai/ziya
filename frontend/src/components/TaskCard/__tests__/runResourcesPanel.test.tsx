/**
 * @jest-environment jsdom
 *
 * RunResourcesPanel: the resource log that stays with a run.
 *
 * Seams pinned here:
 *  - the panel fetches /task-runs/{id}/resources only once opened, and
 *    re-fetches when the run turns terminal while open;
 *  - every figure the backend joins (wall, model time, turns, tool calls,
 *    tokens, cost, CPU, disk, per-block rows) reaches the DOM;
 *  - an estimated cost renders italic, an unpriced one as "unpriced";
 *  - the Info tab's deep link (defaultOpen) fetches on mount.
 */
import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom';
import { RunResourcesPanel } from '../RunResourcesPanel';
import type { RunResources } from '../../../api/usageApi';

const payload: RunResources = {
  run_id: 'run-1', card_name: 'Release sweep', status: 'done', attempt: 1,
  wall_ms: 8_040_000, model_time_ms: 3_480_000, cpu_ms: 31_200, cpu_shared: false,
  tool_calls: 212, turns: 41,
  dims: { input: 1_100_000, cache_read: 980_000, output: 52_000, reasoning: 18_000 },
  effective_cost: 8.04, list_cost: 11.9, has_estimates: false, estimate_records: 0,
  tiers: [{ tier: 'flex', records: 37 }, { tier: 'standard', records: 4 }],
  blocks: [
    { block_id: 't1', name: 'P1 Plan commits', block_type: 'task', status: 'done',
      wall_ms: 250_000, tool_calls: 18, cpu_ms: 1200, model: 'opus5.5', files_written: [],
      iterations: 0, turns: 3, dims: { input: 96_000, output: 6000 },
      effective_cost: 0.91, has_estimates: false },
    { block_id: 'loop', name: 'P2 Review diffs', block_type: 'repeat', status: 'done',
      wall_ms: 6_060_000, tool_calls: null, cpu_ms: null, model: null, files_written: [],
      iterations: 20, turns: 24, dims: { input: 820_000, output: 31_000 },
      effective_cost: 4.88, has_estimates: true },
    { block_id: 't3', name: 'P3 Commit', block_type: 'task', status: 'held',
      wall_ms: 14_940_000, tool_calls: 6, cpu_ms: 40, model: 'opus5.5', files_written: [],
      iterations: 0, turns: 2, dims: {}, effective_cost: null, has_estimates: false },
  ],
  files_written: [{ path: 'plan.md', bytes: 2048, exists: true },
                  { path: 'tmp.json', bytes: 0, exists: false }],
  files_bytes: 2048,
  disk: { record_bytes: 140_000, scratch_bytes: 30_000, scratch_path: '/p/.ziya/task-runs/run-1' },
};

function mockFetch(data: RunResources = payload) {
  const calls: string[] = [];
  global.fetch = jest.fn(async (url: string) => {
    calls.push(url);
    return { ok: true, json: async () => data } as Response;
  }) as unknown as typeof fetch;
  return calls;
}

const realFetch = global.fetch;
afterEach(() => { global.fetch = realFetch; });

describe('RunResourcesPanel', () => {
  it('does not fetch until opened, then hits the run resources route', async () => {
    const calls = mockFetch();
    render(<RunResourcesPanel projectId="p 1" runId="run/1" status="done" />);
    expect(calls).toHaveLength(0);
    fireEvent.click(screen.getByText(/Resources/));
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0]).toBe('/api/v1/projects/p%201/task-runs/run%2F1/resources');
  });

  it('renders every joined figure once loaded', async () => {
    mockFetch();
    render(<RunResourcesPanel projectId="p" runId="run-1" status="done" defaultOpen />);
    await screen.findByText('2h 14m');                       // wall clock
    expect(screen.getByText('58m 00s')).toBeInTheDocument();  // model time
    expect(screen.getByText('41')).toBeInTheDocument();       // turns
    expect(screen.getByText('212')).toBeInTheDocument();      // tool calls
    expect(screen.getAllByText('$8.04').length).toBeGreaterThan(0);
    expect(screen.getByText(/980k cache read/)).toBeInTheDocument();
    expect(screen.getByText(/18k reasoning/)).toBeInTheDocument();
    expect(screen.getByText(/37 flex \/ 4 standard/)).toBeInTheDocument();
    expect(screen.getByText(/list \$11\.90/)).toBeInTheDocument();
    expect(screen.getByText(/tool CPU: 31s/)).toBeInTheDocument();
    expect(screen.getByText(/run record 136\.7 KB/)).toBeInTheDocument();
    expect(screen.getByText(/scratchpad 29\.3 KB/)).toBeInTheDocument();
    // per-block rows: names, loop multiplicity, held badge, container cost italic
    expect(screen.getByText(/P1 Plan commits/)).toBeInTheDocument();
    expect(screen.getByText('×20')).toBeInTheDocument();
    expect(screen.getByText('held')).toHaveClass('tc-res__badge--held');
    const estimated = screen.getByText('$4.88');
    expect(estimated.tagName).toBe('I');
    // a block with turns but no rate shows unpriced, not $0
    expect(screen.getByText('unpriced')).toBeInTheDocument();
    // files: the deleted one is named as such
    fireEvent.click(screen.getByText(/files written \(2\)/));
    expect(screen.getByText('tmp.json')).toBeInTheDocument();
    expect(screen.getByText(/no longer present/)).toBeInTheDocument();
  });

  it('flags shared CPU under parallelism', async () => {
    mockFetch({ ...payload, cpu_shared: true });
    render(<RunResourcesPanel projectId="p" runId="run-1" status="done" defaultOpen />);
    await screen.findByText(/parallel tasks share the process/);
  });

  it('re-fetches when an open panel sees the run turn terminal', async () => {
    const calls = mockFetch({ ...payload, status: 'running', wall_ms: 1000 });
    const { rerender } = render(
      <RunResourcesPanel projectId="p" runId="run-1" status="running" defaultOpen />);
    await waitFor(() => expect(calls).toHaveLength(1));
    rerender(<RunResourcesPanel projectId="p" runId="run-1" status="done" defaultOpen />);
    await waitFor(() => expect(calls).toHaveLength(2));
    // ...but not on a status change that stays live.
    rerender(<RunResourcesPanel projectId="p" runId="run-1" status="partial" defaultOpen />);
    await new Promise(r => setTimeout(r, 20));
    expect(calls).toHaveLength(2);
  });

  it('shows the error, not a blank panel, when the route fails', async () => {
    global.fetch = jest.fn(async () =>
      ({ ok: false, status: 500, json: async () => ({ detail: 'run resources failed' }) } as Response),
    ) as unknown as typeof fetch;
    render(<RunResourcesPanel projectId="p" runId="run-1" status="done" defaultOpen />);
    await screen.findByText(/resources unavailable \(run resources failed\)/);
  });
});
