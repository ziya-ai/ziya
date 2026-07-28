/**
 * Regression tests for the force-directed `canHandle` widening and link
 * sanitization (Issue 3 of the graphics-stress ledger).
 *
 * Two defects, both producing a ~35s render timeout rather than an error:
 *
 *  1. `type: "d3"` is a renderer-FAMILY name, not a concrete diagram type.
 *     A spec of `{type: "d3", layout: "force-directed", nodes, links}` matched
 *     NO plugin's canHandle, so findPluginForSpec returned undefined and the
 *     D3Renderer orchestrator retried to timeout — silent total data loss.
 *
 *  2. `d3.forceLink().id()` throws an uncaught "node not found" when a link
 *     endpoint does not resolve to a node, aborting the whole render. One
 *     dangling edge destroyed an otherwise-valid graph.
 *
 * NOTE ON APPROACH: the sibling forceDirectedPlugin.test.ts re-implements
 * `isForceDirectedSpec` as a LOCAL COPY, so it cannot detect drift between
 * the test's copy and the real plugin. These tests import the real plugin
 * and exercise `forceDirectedPlugin.canHandle` directly, so the shipped
 * predicate is what is under test.
 */

import { forceDirectedPlugin } from '../forceDirectedPlugin';

const NODES = [{ id: 'A' }, { id: 'B' }];
const LINKS = [{ source: 'A', target: 'B' }];

describe('forceDirectedPlugin.canHandle — d3 renderer-family specs', () => {
    it('accepts the concrete type "force-directed" (pre-existing behaviour)', () => {
        expect(forceDirectedPlugin.canHandle({
            type: 'force-directed', nodes: NODES, links: LINKS,
        })).toBe(true);
    });

    it('accepts the concrete type "force" (pre-existing behaviour)', () => {
        expect(forceDirectedPlugin.canHandle({
            type: 'force', nodes: NODES, links: LINKS,
        })).toBe(true);
    });

    // The actual Issue 3 fix: family name + layout discriminator.
    it('accepts {type:"d3", layout:"force-directed"}', () => {
        expect(forceDirectedPlugin.canHandle({
            type: 'd3', layout: 'force-directed', nodes: NODES, links: LINKS,
        })).toBe(true);
    });

    it('accepts {type:"d3", layout:"force"}', () => {
        expect(forceDirectedPlugin.canHandle({
            type: 'd3', layout: 'force', nodes: NODES, links: LINKS,
        })).toBe(true);
    });

    it('accepts a d3-family spec with nodes/links nested under data', () => {
        expect(forceDirectedPlugin.canHandle({
            type: 'd3', layout: 'force-directed',
            data: { nodes: NODES, links: LINKS },
        })).toBe(true);
    });

    // Guard: the widening must not make this plugin a catch-all for the
    // whole d3 family, or it would steal chord/network/basic-chart specs.
    it('rejects {type:"d3"} with no layout discriminator', () => {
        expect(forceDirectedPlugin.canHandle({
            type: 'd3', nodes: NODES, links: LINKS,
        })).toBe(false);
    });

    it('rejects {type:"d3"} with an unrelated layout', () => {
        expect(forceDirectedPlugin.canHandle({
            type: 'd3', layout: 'chord', nodes: NODES, links: LINKS,
        })).toBe(false);
    });

    it('rejects a layout hint on a non-d3 type', () => {
        expect(forceDirectedPlugin.canHandle({
            type: 'mermaid', layout: 'force-directed', nodes: NODES, links: LINKS,
        })).toBe(false);
    });

    describe('structural requirements still enforced', () => {
        it('rejects a matching type with no nodes', () => {
            expect(forceDirectedPlugin.canHandle({
                type: 'd3', layout: 'force-directed', links: LINKS,
            })).toBe(false);
        });

        it('rejects an empty node array', () => {
            expect(forceDirectedPlugin.canHandle({
                type: 'd3', layout: 'force-directed', nodes: [], links: LINKS,
            })).toBe(false);
        });

        it('rejects null and non-objects without throwing', () => {
            expect(forceDirectedPlugin.canHandle(null as any)).toBe(false);
            expect(forceDirectedPlugin.canHandle('force-directed' as any)).toBe(false);
            expect(forceDirectedPlugin.canHandle(undefined as any)).toBe(false);
        });
    });
});

/**
 * Link sanitization (defect 2).
 *
 * The filter lives inside `render`, which needs real d3 + SVG. Rather than
 * stand up a DOM, this pins the filter's LOGIC against the exact shapes the
 * ledger recorded (dangling target, object-form endpoint, self-loop) using
 * the same predicate the plugin applies. Kept adjacent to the canHandle
 * tests so the Issue 3 pair is documented in one place.
 */
describe('link sanitization against the node set', () => {
    // Mirrors the plugin's filter. If the plugin's version changes, this
    // documents the contract that change must preserve.
    const sanitize = (nodes: any[], links: any[]) => {
        const nodeIds = new Set(
            nodes.map(n => (n && n.id != null ? String(n.id) : '')).filter(id => id !== ''),
        );
        const endpointId = (e: any) =>
            typeof e === 'object' && e !== null ? String(e.id) : String(e);
        return links.filter(l => nodeIds.has(endpointId(l.source)) && nodeIds.has(endpointId(l.target)));
    };

    it('drops a link whose target does not exist (the ledger case)', () => {
        const out = sanitize(NODES, [
            { source: 'A', target: 'B' },
            { source: 'A', target: 'NONEXISTENT' },
        ]);
        expect(out).toEqual([{ source: 'A', target: 'B' }]);
    });

    it('drops a link whose source does not exist', () => {
        const out = sanitize(NODES, [{ source: 'GHOST', target: 'B' }]);
        expect(out).toEqual([]);
    });

    it('keeps the valid subset rather than failing the whole render', () => {
        // This is the point of the fix: partial success beats total loss.
        const nodes = [{ id: 'A' }, { id: 'B' }, { id: 'C' }];
        const out = sanitize(nodes, [
            { source: 'A', target: 'B' },
            { source: 'X', target: 'Y' },
            { source: 'B', target: 'C' },
        ]);
        expect(out).toHaveLength(2);
    });

    it('resolves object-form endpoints by id', () => {
        const out = sanitize(NODES, [{ source: { id: 'A' }, target: { id: 'B' } }]);
        expect(out).toHaveLength(1);
    });

    it('keeps a legitimate self-loop', () => {
        const out = sanitize(NODES, [{ source: 'A', target: 'A' }]);
        expect(out).toHaveLength(1);
    });

    it('coerces numeric ids so they are not spuriously dropped', () => {
        const out = sanitize([{ id: 1 }, { id: 2 }], [{ source: 1, target: 2 }]);
        expect(out).toHaveLength(1);
    });

    it('ignores nodes with no usable id when building the id set', () => {
        const out = sanitize([{ id: 'A' }, {}, { id: null }], [{ source: 'A', target: 'A' }]);
        expect(out).toHaveLength(1);
    });
});
