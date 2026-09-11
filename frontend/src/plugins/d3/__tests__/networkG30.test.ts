/**
 * @jest-environment jsdom
 *
 * G-30 / D-033 — consolidated render-level regression guard for the network
 * force-layout / clamp / label-sizing / group-rect class.
 *
 * D-033 bundles seven triage clusters against networkDiagram.ts:
 *   - viewport-clamp-edge-pileup-coincident-nodes   (w2-01/04/06/09/10/12/14/15)
 *   - tall-canvas-clipped-by-fixed-400px-container  (w1-11, w1-13, w2-08)
 *   - label-overlap-at-high-node-count              (w2-03, w2-11)
 *   - long-label-clipped-and-overlapping            (w2-05)
 *   - font-size-below-legible-floor                 (w2-07, w2-14)
 *   - group-rects-collapse-to-one-hardcoded-position(w2-13)
 *   - hardcoded-group-color-lowcontrast:dark        (w1-11, w2-13)
 *
 * Every one of those mechanisms was ALREADY repaired in source under prior
 * commits and is regression-covered at the HELPER level in sibling suites:
 *   D-197 grid-layout + charge-scaling + forceCollide (networkG19),
 *   Issue-31 clampNodePositionsToViewport (networkViewportClamp),
 *   D-052 dynamic container height (networkG52),
 *   D-199 label truncation (networkG28),
 *   D-200 label halo / D-201 font floor / D-202 member-derived group rects /
 *   D-245 group colour per theme (networkG51).
 *
 * So G-30 required NO new source change (the D-019/D-032 precedent). This suite
 * is the CONSOLIDATION guard tied to the group id: it exercises the shipped
 * mechanisms END-TO-END on synthetic specs that mirror the exact shapes of the
 * failing D-033 spec bodies (verified against .ziya/gfx-sweep/specs/network):
 *   w2-01  200 nodes / 600x400 / no coords  -> grid, distinct + in-viewport
 *   w2-07   90 nodes / 3000x200 / fontSize 12 -> font floor survives downscale
 *   w2-13   60 nodes / 30 groups / 950x400   -> 30 DISTINCT group rects
 *   w1-11    5 nodes / 950x600 / 2 groups     -> tall canvas not cropped
 *
 * Where a pure helper can reproduce the PRE-FIX behaviour the direction is
 * asserted explicitly (a coincident-node pileup that clamp alone cannot break
 * vs. the grid that does), so the guard certifies the fix, not the bug.
 */
import {
    networkDiagramPlugin,
    computeGridLayout,
    clampNodePositionsToViewport,
    computeGroupRect,
    effectiveNetworkFontSize,
    NETWORK_FORCE_LAYOUT_MAX_NODES,
    NETWORK_MIN_EFFECTIVE_FONT_PX,
} from '../networkDiagram';

// ── recording d3 mock (evaluates function-valued attrs over bound data). No
//    forceSimulation is provided, so a >MAX un-anchored graph MUST take the
//    deterministic grid path (the fix), and a small graph relies on authored
//    x/y — mirroring networkG51's harness. ─────────────────────────────────
function makeRecorder() {
    const records: Array<{ key: string; value: any }> = [];
    function selection(data: any[], bound = false): any {
        const self: any = {};
        const rec = (key: string, val: any) => {
            if (typeof val === 'function') {
                const rows = bound ? data : (data.length ? data : [undefined]);
                rows.forEach((d, i) => records.push({ key, value: val(d, i) }));
            } else {
                records.push({ key, value: val });
            }
        };
        self.append = () => selection(data, bound);
        self.select = () => selection(data, bound);
        self.selectAll = () => selection([], false);
        self.data = (arr: any[]) => selection(Array.isArray(arr) ? arr : [], true);
        self.datum = (d: any) => selection([d], true);
        self.enter = () => self;
        self.exit = () => self;
        self.merge = () => self;
        self.join = () => selection(data, bound);
        self.filter = (fn?: any) => selection(typeof fn === 'function' ? data.filter(fn) : data, bound);
        self.each = () => self;
        self.call = () => self;
        self.remove = () => self;
        self.attr = (k: string, v: any) => { rec(k, v); return self; };
        self.style = (k: string, v: any) => { rec('style:' + k, v); return self; };
        self.text = (v: any) => { rec('text', v); return self; };
        return self;
    }
    const d3: any = { select: () => selection([]) };
    return { d3, records };
}
const valuesFor = (records: Array<{ key: string; value: any }>, key: string) =>
    records.filter(r => r.key === key).map(r => r.value);

// ── viewport-clamp-edge-pileup — large un-anchored graph (w2-01) ─────────────
describe('D-033 large un-anchored graph gets a distinct, in-viewport cell per node (grid, not a clamped pileup)', () => {
    const W = 600, H = 400, N = 200; // w2-01 exact shape

    it('DIRECTION: the clamp ALONE (the pre-D197 fallback) cannot separate coincident ejected nodes', () => {
        // Old path: force ejects, clamp pins to edges -> a perimeter band of
        // stacked circles. Model the degenerate limit: every node coincident at
        // the centre. clampNodePositionsToViewport leaves them coincident.
        const coincident = Array.from({ length: N }, () => ({ x: W / 2, y: H / 2 }));
        clampNodePositionsToViewport(coincident, W, H);
        const distinct = new Set(coincident.map(n => `${n.x},${n.y}`));
        expect(distinct.size).toBe(1); // the bug: all 200 on one point
    });

    it('the render takes the grid path (N exceeds the force-layout ceiling) and every node is distinct + in-viewport', () => {
        expect(N).toBeGreaterThan(NETWORK_FORCE_LAYOUT_MAX_NODES);
        const spec = {
            type: 'network',
            nodes: Array.from({ length: N }, (_, i) => ({ id: `n${i}` })), // no x/y
            links: Array.from({ length: 220 }, (_, i) => ({ source: `n${i % N}`, target: `n${(i + 1) % N}` })),
            width: W, height: H, style: {},
        };
        for (const dark of [false, true]) {
            const r = makeRecorder();
            networkDiagramPlugin.render(document.createElement('div'), r.d3, spec, dark);
            const transforms = valuesFor(r.records, 'transform')
                .filter((t: any) => typeof t === 'string' && t.startsWith('translate('));
            expect(transforms.length).toBe(N);
            const seen = new Set<string>();
            for (const t of transforms) {
                const m = /translate\(([-0-9.]+),([-0-9.]+)\)/.exec(t)!;
                const x = Number(m[1]), y = Number(m[2]);
                expect(x).toBeGreaterThanOrEqual(0);
                expect(x).toBeLessThanOrEqual(W);
                expect(y).toBeGreaterThanOrEqual(0);
                expect(y).toBeLessThanOrEqual(H);
                seen.add(t);
            }
            // no two nodes coincide (the anti-pileup invariant)
            expect(seen.size).toBe(N);
        }
    });

    it('computeGridLayout fills the interior, not just a perimeter band', () => {
        const nodes = Array.from({ length: N }, () => ({ x: 0, y: 0 }));
        computeGridLayout(nodes, W, H);
        const interior = nodes.filter(n =>
            n.x > W * 0.2 && n.x < W * 0.8 && n.y > H * 0.2 && n.y < H * 0.8);
        expect(interior.length).toBeGreaterThan(0);
    });
});

// ── font-size-below-legible-floor — wide viewBox downscale (w2-07) ───────────
describe('D-033 label font survives the responsive downscale (w2-07: 12px @ 3000x200)', () => {
    it('effectiveNetworkFontSize boosts a size that a 3000px-wide viewBox would crush', () => {
        const boosted = effectiveNetworkFontSize(12, 3000, 200);
        expect(boosted).toBeGreaterThan(12); // pre-fix applied 12 verbatim -> ~2.8px on screen
        expect(boosted).toBeGreaterThanOrEqual(NETWORK_MIN_EFFECTIVE_FONT_PX);
    });

    it('render never emits a sub-legible font-size for the wide-canvas spec (BOTH themes)', () => {
        const spec = {
            type: 'network',
            nodes: [{ id: 'a', x: 100, y: 100 }, { id: 'b', x: 2900, y: 100 }],
            links: [{ source: 'a', target: 'b' }],
            width: 3000, height: 200, style: { fontSize: 12 },
        };
        for (const dark of [false, true]) {
            const r = makeRecorder();
            networkDiagramPlugin.render(document.createElement('div'), r.d3, spec, dark);
            const fs = valuesFor(r.records, 'font-size').map(Number).filter(n => Number.isFinite(n));
            expect(fs.length).toBeGreaterThan(0);
            fs.forEach(v => expect(v).toBeGreaterThan(12));
        }
    });
});

// ── group-rects-collapse — 30 declared groups (w2-13) ────────────────────────
describe('D-033 many groups get DISTINCT member-derived rects (not one hardcoded box)', () => {
    it('computeGroupRect returns a distinct rect per group whose members sit in different places', () => {
        // 30 groups, each with 2 members spread across the canvas (w2-13 shape).
        const nodeById = new Map<string, any>();
        const groups: Array<{ id: string; members: string[] }> = [];
        for (let g = 0; g < 30; g++) {
            const a = `g${g}a`, b = `g${g}b`;
            nodeById.set(a, { id: a, x: (g % 6) * 150 + 30, y: Math.floor(g / 6) * 70 + 30, size: 8 });
            nodeById.set(b, { id: b, x: (g % 6) * 150 + 80, y: Math.floor(g / 6) * 70 + 40, size: 8 });
            groups.push({ id: `grp${g}`, members: [a, b] });
        }
        const rects = groups.map(gr => computeGroupRect(gr.members, nodeById)).filter(Boolean) as any[];
        expect(rects.length).toBe(30);
        // DIRECTION: the old hardcoded ternary put every non-'modem_board' group
        // at the identical x=680. Here the x origins must be spread out.
        const xs = new Set(rects.map(r => Math.round(r.x)));
        expect(xs.size).toBeGreaterThan(1);
        // at least 6 distinct columns (the grid has 6)
        expect(xs.size).toBeGreaterThanOrEqual(6);
    });

    it('render draws one dashed group rect per resolvable group at distinct positions (BOTH themes)', () => {
        const nodes = [
            { id: 'a', x: 80, y: 80, size: 8 }, { id: 'b', x: 140, y: 90, size: 8 },
            { id: 'c', x: 700, y: 300, size: 8 }, { id: 'd', x: 760, y: 320, size: 8 },
        ];
        const spec = {
            type: 'network', nodes,
            links: [{ source: 'a', target: 'b' }, { source: 'c', target: 'd' }],
            groups: [{ id: 'left', members: ['a', 'b'] }, { id: 'right', members: ['c', 'd'] }],
            width: 950, height: 400, style: { fontSize: 9 },
        };
        for (const dark of [false, true]) {
            const r = makeRecorder();
            networkDiagramPlugin.render(document.createElement('div'), r.d3, spec, dark);
            // rect x origins recorded for the two group rects must differ
            const rectXs = valuesFor(r.records, 'x').filter(v => typeof v === 'number');
            const distinctX = new Set(rectXs.map(v => Math.round(v as number)));
            expect(distinctX.size).toBeGreaterThanOrEqual(2);
        }
    });
});

// ── tall-canvas / dynamic height (w1-11, w1-13, w2-08) ───────────────────────
describe('D-033 tall canvas is not cropped by a fixed 400px container (D-052)', () => {
    it('the plugin sizing lets the container grow to the SVG content', () => {
        expect(networkDiagramPlugin.sizingConfig?.needsDynamicHeight).toBe(true);
        const h = (networkDiagramPlugin.sizingConfig as any)?.containerStyles?.height;
        expect(h === undefined || h === 'auto').toBe(true); // never a pinned 400px
    });
});
