/**
 * @jest-environment jsdom
 *
 * The one-shot migration must mark its writes ``via: 'migration'`` on the
 * wire, or the hub cannot explain a lens "+n" the user never made.  Mocks
 * fetch (not benchApi) so the request body itself is asserted.
 */
import React from 'react';
import { render, waitFor } from '@testing-library/react';
import { useBench } from '../useBench';

const emptyView = {
    items: [{ key: 'skill:Tests', kind: 'skill', tier: 'placeable', name: 'Tests', provenance: 'builtin',
              tokens: 1, health: {}, placements: { conversation: null, project: null, user: null },
              effective: 'off', origin: 'default', default: 'off' }],
    dropped: [], lenses: ['project', 'user'], deltas: { conversation: 0, project: 0, user: 0 },
    weight: { always: 0, ondemand: 0, environment: 0 }, conversation_id: null,
};

const Host = () => {
    useBench({ projectId: 'PV', legacy: {
        skillIds: ['t1'], skillsLoadedForProject: true,
        skills: [{ id: 't1', name: 'Tests', visibility: 'user_selectable' } as any],
    } });
    return null;
};

it('migration PUTs carry via=migration', async () => {
    localStorage.clear();
    const fetchMock = jest.fn(async (_url: string, init?: RequestInit) =>
        ({ ok: true, status: 200, json: async () => emptyView }) as any);
    (global as any).fetch = fetchMock;
    render(<Host />);
    await waitFor(() => expect(fetchMock.mock.calls.some(c => c[1]?.method === 'PUT')).toBe(true));
    const puts = fetchMock.mock.calls.filter(c => c[1]?.method === 'PUT').map(c => JSON.parse(c[1]!.body as string));
    expect(puts).toHaveLength(1);
    expect(puts[0]).toMatchObject({ key: 'skill:Tests', layer: 'project', state: 'always', via: 'migration' });
});
