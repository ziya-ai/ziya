import {
    isJointLinkCell,
    normalizeJointLink,
    normalizeJointCells,
} from '../jointShapeResolver';

// D-142 (G-78): link endpoint alias from/to (also src/dst, start/end) unmapped.
// joint-w4-10 emits connections as {from, to, label} instead of
// {source, target}. Pre-fix: normalizeJointLink left source/target undefined,
// so the link creator dereferenced linkSpec.source.id and dropped every edge
// (4 disconnected boxes, no error). isJointLinkCell also failed to classify an
// interleaved aliased edge as a link. These tests fail against unpatched code
// and pass with the alias mapping.
describe('D-142/G-78: endpoint alias from/to -> source/target', () => {
    it('normalizeJointLink maps from/to onto source/target (the w4-10 shape)', () => {
        const lk = { from: 'spider', to: 'indexer', label: 'html' };
        const out = normalizeJointLink(lk);
        // Direction check: the raw cell has no source/target at all.
        expect((lk as any).source).toBeUndefined();
        expect((lk as any).target).toBeUndefined();
        // After normalization the canonical keys resolve the endpoints.
        expect(out.source).toBe('spider');
        expect(out.target).toBe('indexer');
        expect(out.label).toBe('html');
    });

    it('maps src/dst and start/end synonyms too', () => {
        expect(normalizeJointLink({ src: 'a', dst: 'b' }).source).toBe('a');
        expect(normalizeJointLink({ src: 'a', dst: 'b' }).target).toBe('b');
        expect(normalizeJointLink({ start: 'a', end: 'b' }).source).toBe('a');
        expect(normalizeJointLink({ start: 'a', end: 'b' }).target).toBe('b');
    });

    it('does NOT override an explicit source/target with an alias', () => {
        const out = normalizeJointLink({ source: 'real', from: 'alias', target: 'realT', to: 'aliasT' });
        expect(out.source).toBe('real');
        expect(out.target).toBe('realT');
    });

    it('accepts object-form endpoints {id} under aliases', () => {
        const out = normalizeJointLink({ from: { id: 'a' }, to: { id: 'b' } });
        expect(out.source).toEqual({ id: 'a' });
        expect(out.target).toEqual({ id: 'b' });
    });

    it('isJointLinkCell classifies an aliased edge as a link (not an element)', () => {
        const cell = { from: 'a', to: 'b' };
        expect(isJointLinkCell(cell)).toBe(true);
        // A plain element with neither pair of endpoints is still an element.
        expect(isJointLinkCell({ id: 'box', label: 'X' })).toBe(false);
        // Only one endpoint present -> not a link (no spurious classification).
        expect(isJointLinkCell({ from: 'a' })).toBe(false);
    });

    it('normalizeJointCells recovers the w4-10 connections through rawLinks', () => {
        const elements = [
            { id: 'spider' }, { id: 'indexer' }, { id: 'ranker' }, { id: 'serp' },
        ];
        const connections = [
            { from: 'spider', to: 'indexer', label: 'html' },
            { from: 'indexer', to: 'ranker', label: 'postings' },
            { from: 'ranker', to: 'serp', label: 'top-10' },
        ];
        const { elements: els, connections: conns } = normalizeJointCells(elements, connections);
        expect(els.length).toBe(4);
        expect(conns.length).toBe(3);
        // Every connection now carries a downstream-dereferenceable endpoint.
        for (const c of conns) {
            expect(c.source).toBeDefined();
            expect(c.target).toBeDefined();
        }
        expect(conns[0].source).toBe('spider');
        expect(conns[0].target).toBe('indexer');
        expect(conns[2].target).toBe('serp');
    });

    it('splits an interleaved aliased edge out of a mixed cells array', () => {
        const { elements, connections } = normalizeJointCells([
            { id: 'a', type: 'standard.Rectangle' },
            { id: 'b', type: 'standard.Rectangle' },
            { id: 'e1', from: 'a', to: 'b', label: 'x' },
        ]);
        expect(elements.map(e => e.id).sort()).toEqual(['a', 'b']);
        expect(connections.length).toBe(1);
        expect(connections[0].source).toBe('a');
        expect(connections[0].target).toBe('b');
    });
});
