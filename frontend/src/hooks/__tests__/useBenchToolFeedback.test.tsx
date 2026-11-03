/**
 * @jest-environment jsdom
 *
 * Unticking a tool must move every counter in the same frame: setTool takes
 * the re-costed view from the PUT response, announces to sibling instances,
 * and records a transient per-key / total delta that clears itself.
 */
import React from 'react';
import { act, render, waitFor } from '@testing-library/react';
import { FLASH_MS, BENCH_CHANGED_EVENT, useBench } from '../useBench';

const view = (tokens: number, enabledCount: number) => ({
    items: [{ key: 'mcp:jira', kind: 'mcp', tier: 'placeable', name: 'jira', provenance: 'user', tokens,
              health: { tool_count: 2, enabled_count: enabledCount, withheld_tokens: 2000 - tokens },
              placements: { conversation: null, project: null, user: null },
              effective: 'always', origin: 'default', default: 'always' }],
    dropped: [], lenses: ['project', 'user'], deltas: { conversation: 0, project: 0, user: 0 },
    weight: { always: tokens, ondemand: 0, environment: 0 }, conversation_id: null,
});
const detail = (b: boolean) => ({ key: 'mcp:jira', kind: 'mcp', name: 'jira', description: '', tools: ['a', 'b'],
    tool_permissions: { a: true, b: b } });

let hook: ReturnType<typeof useBench> | null = null;
const Host = () => { hook = useBench({ projectId: 'PT' }); return null; };

it('setTool re-costs the view, flashes the deltas, announces, and the flash clears', async () => {
    jest.useFakeTimers();
    const fetchMock = jest.fn(async (url: string, init?: RequestInit) => {
        if (init?.method === 'PUT') return { ok: true, status: 200, json: async () => ({ detail: detail(false), view: view(1200, 1) }) } as any;
        return { ok: true, status: 200, json: async () => view(2000, 2) } as any;
    });
    (global as any).fetch = fetchMock;
    const announced = jest.fn();
    window.addEventListener(BENCH_CHANGED_EVENT, announced);
    render(<Host />);
    await waitFor(() => expect(hook!.ready).toBe(true));
    expect(hook!.items[0].tokens).toBe(2000);
    expect(hook!.flash).toBeNull();

    await act(async () => { await hook!.setTool('jira', 'b', false); });
    const put = fetchMock.mock.calls.find(c => c[1]?.method === 'PUT')!;
    expect(put[0]).toBe('/api/v1/projects/PT/bench/tool');
    expect(JSON.parse(put[1]!.body as string)).toEqual({ server: 'jira', tool: 'b', enabled: false });
    // every counter's source moved in one update
    expect(hook!.items[0].tokens).toBe(1200);
    expect(hook!.items[0].health.enabled_count).toBe(1);
    expect(hook!.weight.always).toBe(1200);
    expect(hook!.flash).toMatchObject({ byKey: { 'mcp:jira': -800 }, always: -800 });
    expect(announced).toHaveBeenCalledTimes(1);
    // the drawer's cached detail is the returned one, no refetch
    await expect(hook!.detail('mcp:jira')).resolves.toEqual(detail(false));
    expect(fetchMock.mock.calls.filter(c => String(c[0]).includes('/detail'))).toHaveLength(0);

    act(() => { jest.advanceTimersByTime(FLASH_MS + 10); });
    expect(hook!.flash).toBeNull();
    jest.useRealTimers();
});
