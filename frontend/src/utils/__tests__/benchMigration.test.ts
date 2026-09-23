import {
    convertActiveSkillIds, placementForLegacySkill, projectLayerIsEmpty,
    runBenchMigration, clearLegacySkillIds,
} from '../benchMigration';
import type { BenchView, BenchViewItem } from '../../apis/benchApi';

const disc = { id: 'd1', name: 'Code Review', visibility: 'model_discoverable' };
const pick = { id: 'p1', name: 'Concise', visibility: 'user_selectable' };
const skills = [disc, pick];

const item = (name: string, project: BenchViewItem['placements']['project']): BenchViewItem => ({
    key: `skill:${name}`, kind: 'skill', tier: 'placeable', name, provenance: 'builtin', tokens: 1, health: {},
    placements: { conversation: null, project, user: null }, effective: 'off', origin: 'default', default: 'off',
});
const view = (...items: BenchViewItem[]): BenchView => ({
    items, dropped: [], lenses: ['project', 'user'], deltas: { conversation: 0, project: 0, user: 0 },
    weight: { always: 0, ondemand: 0, environment: 0 }, conversation_id: null,
});

describe('converter: the two meanings of the legacy list', () => {
    it('discoverable in list => off, selectable in list => always, absent => null', () => {
        expect(placementForLegacySkill(disc, true)).toBe('off');
        expect(placementForLegacySkill(pick, true)).toBe('always');
        expect(placementForLegacySkill(pick, false)).toBeNull();
        expect(convertActiveSkillIds(['d1', 'p1', 'stale'], skills)).toEqual([
            { key: 'skill:Code Review', state: 'off' },
            { key: 'skill:Concise', state: 'always' },
        ]);
    });
});

function deps(initial: BenchView, failOn?: string) {
    const placed: string[] = [];
    const cleared: string[] = [];
    return {
        placed, cleared,
        fetchBench: jest.fn(async () => initial),
        place: jest.fn(async (_p: string, key: string) => {
            if (key === failOn) throw new Error('boom');
            placed.push(key);
        }),
        clearLegacy: jest.fn((pid: string) => { cleared.push(pid); }),
    };
}

describe('runBenchMigration', () => {
    it('PUTs every conversion then clears the legacy key', async () => {
        const d = deps(view(item('Code Review', null), item('Concise', null)));
        const out = await runBenchMigration('P', ['d1', 'p1'], skills, d);
        expect(out.status).toBe('migrated');
        expect(d.placed).toEqual(['skill:Code Review', 'skill:Concise']);
        expect(d.cleared).toEqual(['P']);
    });
    it('empty-layer guard: another browser already migrated -> no PUT, legacy retired', async () => {
        const d = deps(view(item('Concise', 'always')));
        expect(projectLayerIsEmpty(view(item('Concise', 'always')))).toBe(false);
        const out = await runBenchMigration('P', ['d1'], skills, d);
        expect(out).toEqual({ status: 'skipped', reason: 'project_layer_not_empty' });
        expect(d.place).not.toHaveBeenCalled();
        expect(d.cleared).toEqual(['P']);
    });
    it('a failed PUT keeps the legacy key so the migration can retry', async () => {
        const d = deps(view(item('Code Review', null), item('Concise', null)), 'skill:Concise');
        const out = await runBenchMigration('P', ['d1', 'p1'], skills, d);
        expect(out.status).toBe('failed');
        expect(d.placed).toEqual(['skill:Code Review']);
        expect(d.cleared).toEqual([]);
    });
    it('nothing to migrate leaves everything alone', async () => {
        const d = deps(view(item('Concise', null)));
        expect((await runBenchMigration('P', [], skills, d)).status).toBe('skipped');
        expect(d.cleared).toEqual([]);
    });
});

describe('clearLegacySkillIds', () => {
    it('drops only skillIds; contexts survive', () => {
        localStorage.setItem('ZIYA_LENS_P', JSON.stringify({ contextIds: ['c'], skillIds: ['d1'] }));
        clearLegacySkillIds('P');
        expect(JSON.parse(localStorage.getItem('ZIYA_LENS_P')!)).toEqual({ contextIds: ['c'], skillIds: [] });
    });
});
