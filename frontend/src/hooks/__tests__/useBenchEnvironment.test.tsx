/**
 * @jest-environment jsdom
 *
 * setEnvironment PUTs /environment with {category, enabled}, replaces the
 * view with the response, and surfaces the server's warning.
 */
import React from 'react';
import { act, render, waitFor } from '@testing-library/react';
import { useBench } from '../useBench';

const view = (warning?: string) => ({
    items: [], dropped: [], lenses: ['project', 'user'], deltas: { conversation: 0, project: 0, user: 0 },
    weight: { always: 0, ondemand: 0, environment: 7 }, conversation_id: null, ...(warning ? { warning } : {}),
});

let hook: ReturnType<typeof useBench> | null = null;
const Host = () => { hook = useBench({ projectId: 'PE', conversationId: 'C1' }); return null; };

it('toggle goes out on the wire and the warning lands on the model', async () => {
    const fetchMock = jest.fn(async (url: string, init?: RequestInit) => {
        const body = init?.method === 'PUT' ? view('Skill discovery is off') : view();
        return { ok: true, status: 200, json: async () => body } as any;
    });
    (global as any).fetch = fetchMock;
    render(<Host />);
    await waitFor(() => expect(hook!.ready).toBe(true));
    expect(hook!.environmentWarning).toBeNull();

    await act(async () => { await hook!.setEnvironment('skills', false); });
    const put = fetchMock.mock.calls.find(c => c[1]?.method === 'PUT')!;
    expect(put[0]).toBe('/api/v1/projects/PE/bench/environment?conversation_id=C1');
    expect(JSON.parse(put[1]!.body as string)).toEqual({ category: 'skills', enabled: false });
    expect(hook!.environmentWarning).toBe('Skill discovery is off');

    // A later toggle without a warning clears it.
    fetchMock.mockImplementation(async () => ({ ok: true, status: 200, json: async () => view() }) as any);
    await act(async () => { await hook!.setEnvironment('skills', true); });
    expect(hook!.environmentWarning).toBeNull();
});
