import { dropIntent, moveOptions } from '../laneDnd';
import { mk } from '../benchTestUtils';

describe('dropIntent', () => {
    it('drop on a new lane pins it at the lens', () => {
        expect(dropIntent(mk('skill:a'), 'always')).toEqual({ kind: 'place', key: 'skill:a', toState: 'always' });
    });
    it('drop on its current lane is a no-op (ghost or pinned)', () => {
        expect(dropIntent(mk('skill:a'), 'ondemand')).toEqual({ kind: 'noop' });
        expect(dropIntent(mk('skill:a', { project: 'always' }), 'always')).toEqual({ kind: 'noop' });
    });
    it('a pinned item dropped on its own ghost (the inherited lane) clears', () => {
        const pinned = mk('skill:a', { project: 'always' });           // inherits default ondemand
        expect(pinned.atLens.inherited).toBe('ondemand');
        expect(dropIntent(pinned, 'ondemand')).toEqual({ kind: 'place', key: 'skill:a', toState: null });
    });
    it('the inherited lane is what THIS lens would inherit, not the effective state', () => {
        // conversation pins off; project lens has no pin; user pins always.
        const x = mk('skill:a', { conversation: 'off', user: 'always', project: 'ondemand' });
        expect(x.atLens.inherited).toBe('always');
        expect(dropIntent(x, 'always')).toEqual({ kind: 'place', key: 'skill:a', toState: null });
    });
    it('mcp ondemand and environment are refused', () => {
        expect(dropIntent(mk('mcp:jira'), 'ondemand')).toEqual({ kind: 'rejected', reason: 'mcp_ondemand_unsupported' });
        expect(dropIntent(mk('mcp:shell', { tier: 'environment' }), 'off'))
            .toEqual({ kind: 'rejected', reason: 'environment_not_placeable' });
    });
});

describe('moveOptions (keyboard / right-click parity with drag)', () => {
    it('every non-noop lane is offered; mcp ondemand is disabled "coming soon"', () => {
        const opts = moveOptions(mk('mcp:jira'));
        expect(opts.map(o => o.label)).toEqual(['Within reach (coming soon)', 'Move to Shelved']);
        expect(opts[0].disabled).toBe(true);
    });
    it('a pin equal to the inherited value still offers a clear', () => {
        const redundant = mk('skill:a', { project: 'ondemand' });  // default is ondemand too
        const clear = moveOptions(redundant).find(o => o.label === 'Clear placement');
        expect(clear?.intent).toEqual({ kind: 'place', key: 'skill:a', toState: null });
    });
    it('an inherited (ghost) item offers no clear', () => {
        expect(moveOptions(mk('skill:a')).some(o => o.label === 'Clear placement')).toBe(false);
    });
});
