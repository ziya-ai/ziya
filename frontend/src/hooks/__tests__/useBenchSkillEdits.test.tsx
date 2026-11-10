/**
 * @jest-environment jsdom
 *
 * useBench owns skill creation / edit / delete for the Bench (components
 * fetch nothing).  Each write goes to the skills REST API by Skill.id, then:
 * the stale card-back detail is dropped, the view is re-fetched, sibling
 * bench instances are told (BENCH_CHANGED_EVENT), and ProjectContext is told
 * the skill LIST changed (SKILLS_CHANGED_EVENT) -- the chip bar resolves
 * bench items to skills by name from that list, so a skill created here and
 * placed on hand would otherwise have no chip until a reload.
 */
import React from 'react';
import { act, render, waitFor } from '@testing-library/react';
import * as fs from 'fs';
import * as path from 'path';

jest.mock('../../apis/benchApi', () => ({
    fetchBench: jest.fn(), placeBench: jest.fn(), fetchBenchDetail: jest.fn(),
}));
jest.mock('../../api/skillApi', () => ({
    createSkill: jest.fn(), updateSkill: jest.fn(), deleteSkill: jest.fn(),
}));
import { fetchBench, fetchBenchDetail } from '../../apis/benchApi';
import * as skillApi from '../../api/skillApi';
import { BENCH_CHANGED_EVENT, useBench } from '../useBench';
import { SKILLS_CHANGED_EVENT } from '../../utils/skillsChanged';

const view = (names: string[]) => ({
    items: names.map(n => ({ key: `skill:${n}`, kind: 'skill', tier: 'placeable', name: n, provenance: 'custom', tokens: 1,
        health: {}, placements: { conversation: null, project: null, user: null }, effective: 'off', origin: 'default', default: 'off' })),
    dropped: [], lenses: ['project', 'user'], deltas: { conversation: 0, project: 0, user: 0 },
    weight: { always: 0, ondemand: 0, environment: 0 }, conversation_id: null,
});

let hook: ReturnType<typeof useBench> | null = null;
const Host = () => { hook = useBench({ projectId: 'P' }); return null; };

beforeEach(() => { jest.clearAllMocks(); });

it('createSkill posts, re-fetches, and announces to both the bench and the skill list', async () => {
    (fetchBench as jest.Mock).mockResolvedValue(view(['a']));
    (skillApi.createSkill as jest.Mock).mockResolvedValue({ id: 'c-9', name: 'b' });
    const bench = jest.fn(); const skills = jest.fn();
    window.addEventListener(BENCH_CHANGED_EVENT, bench);
    window.addEventListener(SKILLS_CHANGED_EVENT, skills);
    render(<Host />);
    await waitFor(() => expect(hook!.ready).toBe(true));
    (fetchBench as jest.Mock).mockResolvedValue(view(['a', 'b']));
    let out: any;
    await act(async () => { out = await hook!.createSkill!({ name: 'b', description: 'd', prompt: 'p' }); });
    expect(out.id).toBe('c-9');
    expect(skillApi.createSkill).toHaveBeenCalledWith('P', { name: 'b', description: 'd', prompt: 'p' });
    expect(hook!.items.map(i => i.name)).toEqual(['a', 'b']);
    expect(bench).toHaveBeenCalledTimes(1);
    expect(skills).toHaveBeenCalledTimes(1);
    expect((skills.mock.calls[0][0] as CustomEvent).detail).toEqual({ projectId: 'P' });
});

it('updateSkill drops the cached detail so the drawer re-reads the body', async () => {
    (fetchBench as jest.Mock).mockResolvedValue(view(['a']));
    (fetchBenchDetail as jest.Mock).mockResolvedValue({ key: 'skill:a', kind: 'skill', name: 'a', description: '', prompt: 'old' });
    (skillApi.updateSkill as jest.Mock).mockResolvedValue({});
    render(<Host />);
    await waitFor(() => expect(hook!.ready).toBe(true));
    await hook!.detail('skill:a'); await hook!.detail('skill:a');
    expect(fetchBenchDetail).toHaveBeenCalledTimes(1);               // cached
    await act(async () => { await hook!.updateSkill!('c-1', 'skill:a', { prompt: 'new' }); });
    expect(skillApi.updateSkill).toHaveBeenCalledWith('P', 'c-1', { prompt: 'new' });
    await hook!.detail('skill:a');
    expect(fetchBenchDetail).toHaveBeenCalledTimes(2);               // invalidated
});

it('deleteSkill removes through the API and the card leaves the view', async () => {
    (fetchBench as jest.Mock).mockResolvedValue(view(['a', 'b']));
    (skillApi.deleteSkill as jest.Mock).mockResolvedValue(undefined);
    render(<Host />);
    await waitFor(() => expect(hook!.items).toHaveLength(2));
    (fetchBench as jest.Mock).mockResolvedValue(view(['a']));
    await act(async () => { await hook!.deleteSkill!('c-2', 'skill:b'); });
    expect(skillApi.deleteSkill).toHaveBeenCalledWith('P', 'c-2');
    expect(hook!.items.map(i => i.name)).toEqual(['a']);
});

it('ProjectContext reloads its skill list on SKILLS_CHANGED_EVENT (the seam the chip bar needs)', () => {
    const src = fs.readFileSync(path.join(__dirname, '..', '..', 'context', 'ProjectContext.tsx'), 'utf8');
    expect(src).toMatch(/SKILLS_CHANGED_EVENT/);
    expect(src).toMatch(/addEventListener\(SKILLS_CHANGED_EVENT/);
});
