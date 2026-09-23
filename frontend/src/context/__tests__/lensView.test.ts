import { viewAtLens } from '../LensContext';

const base = { key: 'skill:a', kind: 'skill', tier: 'placeable', name: 'a', provenance: 'user', tokens: 0, health: {}, default: 'off' } as const;

describe('viewAtLens: ghost vs pin is derived client-side from all layers', () => {
    it('pinned at the viewed lens', () => {
        const v = viewAtLens({ ...base, placements: { conversation: null, project: 'always', user: null }, effective: 'always', origin: 'project' } as any, 'project');
        expect(v).toMatchObject({ state: 'always', ghost: false, pinnedHigher: false });
    });
    it('inherited from a lower layer renders as a ghost of the effective state', () => {
        const v = viewAtLens({ ...base, placements: { conversation: null, project: null, user: 'always' }, effective: 'always', origin: 'user' } as any, 'project');
        expect(v).toMatchObject({ state: 'always', ghost: true, pinnedHigher: false });
    });
    it('a higher layer overriding this lens is flagged', () => {
        const v = viewAtLens({ ...base, placements: { conversation: 'off', project: 'always', user: null }, effective: 'off', origin: 'conversation' } as any, 'project');
        expect(v).toMatchObject({ state: 'always', ghost: false, pinnedHigher: true });
    });
    it('environment is fixed regardless of lens', () => {
        const v = viewAtLens({ ...base, kind: 'mcp', tier: 'environment', placements: { conversation: null, project: null, user: null }, effective: 'always', origin: 'environment' } as any, 'user');
        expect(v).toMatchObject({ state: 'always', ghost: false, origin: 'environment' });
    });
});
