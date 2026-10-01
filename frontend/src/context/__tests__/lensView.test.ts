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
    it('a ghost under a higher pin shows what this lens inherits, not the effective state', () => {
        // conversation pins off; project lens has no pin; nothing below => default
        const v = viewAtLens({ ...base, default: 'ondemand', placements: { conversation: 'off', project: null, user: null }, effective: 'off', origin: 'conversation' } as any, 'project');
        expect(v).toMatchObject({ state: 'ondemand', inherited: 'ondemand', ghost: true, pinnedHigher: true });
    });
    it('inherited comes from the first set layer BELOW the lens', () => {
        const v = viewAtLens({ ...base, placements: { conversation: 'always', project: 'off', user: 'always' }, effective: 'always', origin: 'conversation' } as any, 'project');
        expect(v).toMatchObject({ state: 'off', inherited: 'always', ghost: false, pinnedHigher: true });
    });
    it('environment is fixed regardless of lens', () => {
        const v = viewAtLens({ ...base, kind: 'mcp', tier: 'environment', placements: { conversation: null, project: null, user: null }, effective: 'always', origin: 'environment' } as any, 'user');
        expect(v).toMatchObject({ state: 'always', ghost: false, origin: 'environment' });
    });
});
