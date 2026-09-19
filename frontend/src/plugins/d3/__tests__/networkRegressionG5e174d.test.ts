/**
 * @jest-environment jsdom
 *
 * Consolidated regression canary for fix group G-5e174d
 * (file:frontend/src/plugins/d3/networkDiagram.ts).
 *
 * This group's six defects (D-179, D-180, D-181, D-183, D-184, D-189) have
 * each been verified and then re-appeared as REGRESSIONS four separate times.
 * The source fixes are present, but nothing guards the whole group as a unit,
 * so a partial revert of networkDiagram.ts (or a lost rebuild) silently drops
 * one or more of them. This single test asserts the CONTRACT of each fix
 * through its exported pure helper, in the direction that FAILS against the
 * pre-fix source and passes only with the fix in place.
 *
 *  D-189  strict-json-parse-no-tolerant-layer  -> lenientParseNetworkObject
 *  D-179  viewport-clamp-edge-pileup           -> computeGridLayout / NETWORK_FORCE_LAYOUT_MAX_NODES / clampNodePositionsToViewport
 *  D-183  font-size-below-legible-floor        -> effectiveNetworkFontSize
 *  D-184  group-rects-collapse-to-one-position -> computeGroupRect
 *  D-181  label-overlap-at-high-node-count     -> label halo (paint-order:stroke) via plugin render
 *  D-180  tall-canvas-clipped-by-400px         -> sizingConfig.needsDynamicHeight via plugin render
 */
import {
    lenientParseNetworkObject,
    computeGridLayout,
    clampNodePositionsToViewport,
    effectiveNetworkFontSize,
    computeGroupRect,
    NETWORK_FORCE_LAYOUT_MAX_NODES,
    NETWORK_MIN_EFFECTIVE_FONT_PX,
    NETWORK_REF_DISPLAY_WIDTH,
} from '../networkDiagram';

// ── D-189: every near-JSON slip a model emits must recover to a real object ──
describe('G-5e174d / D-189 lenient network spec recovery', () => {
    const cases: Array<[string, string]> = [
        ['trailing comma (w4-01)', '{ "nodes": [{"id":"a"},{"id":"b"},], "links": [] }'],
        ['unquoted keys (w4-02)', '{ nodes: [{id:"a"},{id:"b"}], links: [] }'],
        ["single-quoted (w4-03)", "{ 'nodes': [{'id':'a'},{'id':'b'}], 'links': [] }"],
        ['markdown fence (w4-04)', '```json\n{ "nodes": [{"id":"a"},{"id":"b"}], "links": [] }\n```'],
        ['smart quotes (w4-05)', '{ \u201cnodes\u201d: [{\u201cid\u201d:\u201ca\u201d}], \u201clinks\u201d: [] }'],
        ['semicolon separators (w4-06)', '{ "nodes": [{"id":"a"};{"id":"b"}]; "links": [] }'],
    ];
    it.each(cases)('recovers %s into an object with nodes', (_label, raw) => {
        const parsed = lenientParseNetworkObject(raw);
        expect(parsed).toBeTruthy();
        expect(typeof parsed).toBe('object');
        expect(Array.isArray(parsed.nodes)).toBe(true);
        expect(parsed.nodes.length).toBeGreaterThan(0);
        // pre-fix behaviour was strict JSON.parse -> throw -> spec unchanged (no nodes)
    });
});

// ── D-179: large un-anchored graph gets a distinct-cell grid, never a pile-up ─
describe('G-5e174d / D-179 grid layout above the force threshold', () => {
    it('threshold constant is defined and finite', () => {
        expect(Number.isFinite(NETWORK_FORCE_LAYOUT_MAX_NODES)).toBe(true);
        expect(NETWORK_FORCE_LAYOUT_MAX_NODES).toBeGreaterThan(0);
    });

    it('assigns every node a distinct, in-bounds position (no coincidence)', () => {
        const n = NETWORK_FORCE_LAYOUT_MAX_NODES + 120; // 200 nodes
        const width = 800, height = 600;
        const nodes = Array.from({ length: n }, (_, i) => ({ id: `n${i}` }));
        computeGridLayout(nodes, width, height);
        clampNodePositionsToViewport(nodes, width, height);

        const seen = new Set<string>();
        for (const node of nodes) {
            expect(Number.isFinite(node.x)).toBe(true);
            expect(Number.isFinite(node.y)).toBe(true);
            expect(node.x).toBeGreaterThanOrEqual(0);
            expect(node.x).toBeLessThanOrEqual(width);
            expect(node.y).toBeGreaterThanOrEqual(0);
            expect(node.y).toBeLessThanOrEqual(height);
            seen.add(`${Math.round(node.x)},${Math.round(node.y)}`);
        }
        // distinct positions: pre-fix clamp stacked ejected nodes into a
        // handful of perimeter coordinates. A grid gives ~n distinct cells.
        expect(seen.size).toBeGreaterThan(n * 0.9);

        // and the interior is populated, not just a perimeter band
        const interior = nodes.filter(
            (nd: any) => nd.x > width * 0.2 && nd.x < width * 0.8 &&
                         nd.y > height * 0.2 && nd.y < height * 0.8,
        );
        expect(interior.length).toBeGreaterThan(0);
    });
});

// ── D-183: on-screen label size never drops below the legibility floor ───────
describe('G-5e174d / D-183 legible font floor under responsive downscale', () => {
    it('boosts a 12px label on a huge viewBox above the on-screen floor (w2-07)', () => {
        // 3000px viewBox downscaled onto a ~700px column: 12 * 700/3000 = 2.8px pre-fix
        const nominal = effectiveNetworkFontSize(12, 3000, 2000);
        const downscale = Math.min(1, NETWORK_REF_DISPLAY_WIDTH / 3000);
        expect(nominal * downscale).toBeGreaterThanOrEqual(NETWORK_MIN_EFFECTIVE_FONT_PX - 1e-6);
    });
    it('lifts a tiny nominal size on a normal canvas to the floor (w2-14)', () => {
        const nominal = effectiveNetworkFontSize(4, 600, 400);
        expect(nominal).toBeGreaterThanOrEqual(NETWORK_MIN_EFFECTIVE_FONT_PX);
    });
    it('leaves a comfortable label unchanged', () => {
        expect(effectiveNetworkFontSize(16, 700, 400)).toBe(16);
    });
});

// ── D-184: each group rect frames its OWN members, not one hardcoded box ─────
describe('G-5e174d / D-184 group rects derived from member positions', () => {
    it('produces distinct boxes for groups at different member positions', () => {
        const nodeById = new Map<string, any>([
            ['a', { id: 'a', x: 100, y: 100, size: 10 }],
            ['b', { id: 'b', x: 140, y: 120, size: 10 }],
            ['c', { id: 'c', x: 600, y: 400, size: 10 }],
            ['d', { id: 'd', x: 640, y: 440, size: 10 }],
        ]);
        const r1 = computeGroupRect(['a', 'b'], nodeById)!;
        const r2 = computeGroupRect(['c', 'd'], nodeById)!;
        expect(r1).not.toBeNull();
        expect(r2).not.toBeNull();
        // the two rects must not collapse onto the same coordinates
        expect(Math.abs(r1.x - r2.x)).toBeGreaterThan(50);
        expect(Math.abs(r1.y - r2.y)).toBeGreaterThan(50);
        // each rect must actually enclose its members
        expect(r1.x).toBeLessThanOrEqual(100 - 10);
        expect(r1.x + r1.width).toBeGreaterThanOrEqual(140 + 10);
    });
    it('returns null when no member resolves (skips a bogus box)', () => {
        expect(computeGroupRect(['zzz'], new Map())).toBeNull();
        expect(computeGroupRect([], new Map())).toBeNull();
    });
});

// NOTE: D-181 (label halo, paint-order:stroke) and D-180 (dynamic container
// height) live inside the plugin's render() body, which requires the full d3
// force runtime. They are already guarded in both themes by networkG51.test.ts
// and networkG52.test.ts respectively; this canary deliberately stays DOM-free
// and asserts the four root causes exposed as pure helpers.
