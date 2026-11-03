/**
 * @jest-environment jsdom
 *
 * The hub and ActiveContextBar mount separate useBench() instances.  A
 * placement through one must reach the other, or the chip bar lies until a
 * remount.  Fails without BENCH_CHANGED_EVENT.
 */
import React from 'react';
import { act, render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom';

jest.mock('../../apis/benchApi', () => ({ fetchBench: jest.fn(), placeBench: jest.fn() }));
import { fetchBench, placeBench } from '../../apis/benchApi';
import { useBench } from '../useBench';

const view = (state: 'always' | null) => ({
    items: [{ key: 'skill:A', kind: 'skill', tier: 'placeable', name: 'A', provenance: 'builtin', tokens: 1,
              health: {}, placements: { conversation: null, project: state, user: null },
              effective: state ?? 'ondemand', origin: state ? 'project' : 'default', default: 'ondemand' }],
    dropped: [], lenses: ['project', 'user'],
    deltas: { conversation: 0, project: state ? 1 : 0, user: 0 },
    weight: { always: 0, ondemand: 0, environment: 0 }, conversation_id: null,
});

let placer: ReturnType<typeof useBench> | null = null;
const Placer = () => { placer = useBench({ projectId: 'P' }); return null; };
const Reader = () => {
    const b = useBench({ projectId: 'P' });
    return <div data-testid="eff">{b.items[0]?.effective ?? '-'}</div>;
};
const Other = () => { useBench({ projectId: 'Q' }); return null; };

const calls = (pid: string) => (fetchBench as jest.Mock).mock.calls.filter(c => c[0] === pid).length;

it('a placement through one instance refreshes the others for the same project only', async () => {
    (fetchBench as jest.Mock).mockResolvedValue(view(null));
    render(<><Placer /><Reader /><Other /></>);
    await waitFor(() => expect(screen.getByTestId('eff')).toHaveTextContent('ondemand'));
    expect(calls('P')).toBe(2);
    expect(calls('Q')).toBe(1);

    (placeBench as jest.Mock).mockResolvedValue(view('always'));
    (fetchBench as jest.Mock).mockResolvedValue(view('always'));
    await act(async () => { await placer!.place('skill:A', 'always'); });

    await waitFor(() => expect(screen.getByTestId('eff')).toHaveTextContent('always'));
    expect(calls('P')).toBe(3);   // the reader refetched; the placer did not refetch itself
    expect(calls('Q')).toBe(1);   // another project is not disturbed
});
