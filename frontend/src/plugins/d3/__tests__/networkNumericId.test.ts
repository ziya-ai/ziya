/**
 * Regression test for Issue 47 (network renderer): TOTAL DETECTION FAILURE for
 * numeric node `id` / numeric link `source`/`target`.
 *
 * ANOMALY: an adversarial network spec whose nodes carried numeric ids
 * (`5`, `1000000000000000`) and whose links had numeric endpoints
 * (`{source:5,target:"5"}`, `{source:1e15,target:1e15}`) did NOT render —
 * render_diagram timed out after 30s with zero output, console spinning in a
 * "No plugin found for spec: network" retry loop. A control spec with all
 * string ids rendered fine.
 *
 * ROOT CAUSE: `isNetworkDiagramSpec` (used as `networkDiagramPlugin.canHandle`)
 * gated detection with `nodes.every(n => typeof n.id === 'string')` AND
 * `links.every(l => typeof l.source === 'string' && typeof l.target ===
 * 'string')`. A single numeric id made `.every(...)` return false -> canHandle
 * false -> registry finds no plugin -> D3Renderer retries to the 30s timeout.
 *
 * FIX: exported pure `isValidNetworkId` (non-empty string OR finite number),
 * used in both the node-id and link-endpoint checks. Numeric ids are valid JSON,
 * d3-force resolves them, and `sanitizeNetworkGraph`'s Set-based dangling filter
 * keeps `5 !== "5"` distinct. The guard stays STRICT: NaN/Infinity/object/null/
 * array/boolean/empty-string ids are still rejected.
 *
 * This test IMPORTS THE REAL MODULE. It is non-vacuous: against the pre-fix code
 * `isValidNetworkId` does not exist (import fails) and `canHandle` returned false
 * for every numeric-id spec below (the assertions expecting `true` would fail).
 */
import { isValidNetworkId, networkDiagramPlugin } from '../networkDiagram';

const netSpec = (nodes: any[], links: any[]) => ({ type: 'network', nodes, links });

describe('Issue 47 — isValidNetworkId (pure helper)', () => {
    it('accepts non-empty strings', () => {
        expect(isValidNetworkId('hub')).toBe(true);
        expect(isValidNetworkId('5')).toBe(true);
        expect(isValidNetworkId('a')).toBe(true);
    });

    it('accepts finite numbers (including 0, negatives, astronomical magnitudes)', () => {
        expect(isValidNetworkId(5)).toBe(true);
        expect(isValidNetworkId(0)).toBe(true);
        expect(isValidNetworkId(-7)).toBe(true);
        expect(isValidNetworkId(1000000000000000)).toBe(true);
        expect(isValidNetworkId(1e15)).toBe(true);
    });

    // GUARD DIRECTION — must still reject everything the old predicate did
    // (empty string) plus the non-finite/non-primitive shapes, so widening the
    // predicate did not turn it into a catch-all.
    it('rejects empty string', () => {
        expect(isValidNetworkId('')).toBe(false);
    });

    it('rejects NaN / Infinity / -Infinity', () => {
        expect(isValidNetworkId(NaN)).toBe(false);
        expect(isValidNetworkId(Infinity)).toBe(false);
        expect(isValidNetworkId(-Infinity)).toBe(false);
    });

    it('rejects objects, arrays, null, undefined, booleans', () => {
        expect(isValidNetworkId({})).toBe(false);
        expect(isValidNetworkId({ id: 5 })).toBe(false);
        expect(isValidNetworkId([1, 2, 3])).toBe(false);
        expect(isValidNetworkId(null)).toBe(false);
        expect(isValidNetworkId(undefined)).toBe(false);
        expect(isValidNetworkId(true)).toBe(false);
        expect(isValidNetworkId(false)).toBe(false);
    });
});

describe('Issue 47 — networkDiagramPlugin.canHandle detection', () => {
    it('ACCEPTS the exact adversarial numeric-id spec that timed out (regression)', () => {
        const spec = netSpec(
            [{ id: 1000000000000000 }, { id: 5 }, { id: '5' }, { id: 'hub' }],
            [
                { source: 5, target: '5' },
                { source: 1000000000000000, target: 1000000000000000 },
                { source: 'hub', target: '5' },
            ]
        );
        expect(networkDiagramPlugin.canHandle(spec)).toBe(true);
    });

    it('ACCEPTS an all-numeric graph', () => {
        const spec = netSpec(
            [{ id: 1 }, { id: 2 }, { id: 3 }],
            [{ source: 1, target: 2 }, { source: 2, target: 3 }]
        );
        expect(networkDiagramPlugin.canHandle(spec)).toBe(true);
    });

    it('still ACCEPTS the all-string control graph (no regression)', () => {
        const spec = netSpec(
            [{ id: 'a' }, { id: 'b' }, { id: 'c' }],
            [{ source: 'a', target: 'b' }, { source: 'b', target: 'c' }]
        );
        expect(networkDiagramPlugin.canHandle(spec)).toBe(true);
    });

    // GUARD: malformed ids must STILL be declined at detection (not a catch-all).
    it('REJECTS a node whose id is an object', () => {
        const spec = netSpec(
            [{ id: { nested: 1 } }, { id: 'b' }],
            [{ source: 'b', target: 'b' }]
        );
        expect(networkDiagramPlugin.canHandle(spec)).toBe(false);
    });

    it('REJECTS a node whose id is NaN', () => {
        const spec = netSpec([{ id: NaN }, { id: 'b' }], []);
        expect(networkDiagramPlugin.canHandle(spec)).toBe(false);
    });

    it('REJECTS a link whose endpoint is null / an array', () => {
        const spec1 = netSpec([{ id: 'a' }, { id: 'b' }], [{ source: null, target: 'b' }]);
        const spec2 = netSpec([{ id: 'a' }, { id: 'b' }], [{ source: 'a', target: [1] }]);
        expect(networkDiagramPlugin.canHandle(spec1)).toBe(false);
        expect(networkDiagramPlugin.canHandle(spec2)).toBe(false);
    });

    it('REJECTS non-network specs (type gate preserved)', () => {
        expect(networkDiagramPlugin.canHandle({ type: 'mermaid', nodes: [{ id: 1 }], links: [] })).toBe(false);
        expect(networkDiagramPlugin.canHandle({ nodes: [{ id: 1 }], links: [] })).toBe(false);
    });
});
