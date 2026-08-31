/**
 * @jest-environment jsdom
 *
 * G-51 regression tests for the network engine (networkDiagram.ts).
 *
 * Every assertion is written so it FAILS against the pre-fix source and passes
 * only with the change; where a pure helper lets us reproduce the old behaviour
 * the direction is asserted explicitly.
 *
 *  D-200 (structural): node labels sat at a fixed dy with NO halo/plate, so at
 *        high node count they were lost under neighbouring circles and the edge
 *        fan. Fix: a canvas-coloured halo painted UNDER the glyph fill
 *        (stroke=effectiveBg + paint-order:stroke). Asserted in BOTH themes
 *        (the halo colour flips with the surface).
 *
 *  D-201 (structural): the label font-size was applied verbatim with no floor,
 *        so a large viewBox downscaled it below legibility (w2-07) and a tiny
 *        nominal size rendered sub-pixel (w2-14: fontSize 4 @ 600px). Fix:
 *        effectiveNetworkFontSize clamps to an on-screen floor accounting for
 *        the responsive downscale.
 *
 *  D-202 (structural): group rects came from a hardcoded
 *        `d.id==='modem_board' ? 180/350 : 680/200` ternary that ignored
 *        `members`, so every other group superimposed into ONE box at x=680.
 *        Fix: computeGroupRect derives each rect from its own members' positions.
 *
 *  D-204 (theme): linkOpacity was applied as a raw stroke-opacity over a pale
 *        base with no floor, so a low caller opacity ghosted the whole topology
 *        below 3:1 in BOTH themes. Fix: resolveNetworkColors reconciles the
 *        stroke AND raises the opacity until the composite clears 3:1.
 */
import {
    effectiveNetworkFontSize,
    computeGroupRect,
    resolveNetworkColors,
    networkDiagramPlugin,
    NETWORK_MIN_EFFECTIVE_FONT_PX,
    NETWORK_LIGHT_BG,
    NETWORK_DARK_BG,
} from '../networkDiagram';
import { contrastRatio, compositeOver } from '../chartTheme';

// ── recording d3 mock (evaluates function-valued attrs over bound data) ──────
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
    const d3: any = { select: () => selection([]) }; // no forceSimulation -> nodes must carry x/y
    return { d3, records };
}
const valuesFor = (records: Array<{ key: string; value: any }>, key: string) =>
    records.filter(r => r.key === key).map(r => r.value);

// ── D-201: effective font-size floor ─────────────────────────────────────────
describe('D-201 — label font-size clamped to an on-screen legibility floor', () => {
    it('a tiny nominal size on a non-downscaled canvas is raised to the floor (w2-14: fontSize 4 @ 600x400)', () => {
        // DIRECTION: unpatched code applied `style.fontSize || 12` = 4 verbatim.
        expect(4).toBeLessThan(NETWORK_MIN_EFFECTIVE_FONT_PX);
        expect(effectiveNetworkFontSize(4, 600, 400)).toBe(NETWORK_MIN_EFFECTIVE_FONT_PX);
    });

    it('a large viewBox boosts the nominal size to survive the downscale (w2-07: 12px @ 3000px-wide)', () => {
        const boosted = effectiveNetworkFontSize(12, 3000, 400);
        expect(boosted).toBeGreaterThan(12);                 // was applied verbatim (invisible after downscale)
        // downscale ~= 700/3000 = 0.233 -> floor/downscale ~= 34
        expect(boosted).toBeGreaterThanOrEqual(30);
    });

    it('a comfortable size on a normal canvas is left unchanged', () => {
        expect(effectiveNetworkFontSize(14, 600, 400)).toBe(14);
    });

    it('render applies the clamped size, not the raw 4 (BOTH themes)', () => {
        const spec = {
            type: 'network',
            nodes: [{ id: 'a', x: 100, y: 100 }, { id: 'b', x: 300, y: 100 }],
            links: [{ source: 'a', target: 'b' }],
            width: 600, height: 400, style: { fontSize: 4 },
        };
        for (const dark of [false, true]) {
            const r = makeRecorder();
            networkDiagramPlugin.render(document.createElement('div'), r.d3, spec, dark);
            const fs = valuesFor(r.records, 'font-size');
            expect(fs.length).toBeGreaterThan(0);
            fs.forEach(v => expect(Number(v)).toBeGreaterThanOrEqual(NETWORK_MIN_EFFECTIVE_FONT_PX));
            expect(fs).not.toContain(4);                     // the raw sub-legible size never reaches the DOM
        }
    });
});

// ── D-200: label halo in the canvas colour ───────────────────────────────────
describe('D-200 — node labels get a canvas-coloured halo (paint-order:stroke)', () => {
    const spec = {
        type: 'network',
        nodes: [
            { id: 'a', label: 'Alpha', x: 100, y: 100, size: 14 },
            { id: 'b', label: 'Beta', x: 130, y: 108, size: 14 }, // near neighbour: overlap risk
        ],
        links: [{ source: 'a', target: 'b' }],
        width: 600, height: 400,
    };

    it.each([[false, NETWORK_LIGHT_BG], [true, NETWORK_DARK_BG]])(
        'label stroke = the effective canvas and paint-order=stroke (isDarkMode=%p)',
        (dark: any, bg: any) => {
            const r = makeRecorder();
            networkDiagramPlugin.render(document.createElement('div'), r.d3, spec, dark);
            // paint-order is unique to the label <text> (never set pre-fix).
            const paintOrder = valuesFor(r.records, 'paint-order');
            expect(paintOrder.length).toBeGreaterThan(0);
            paintOrder.forEach(v => expect(v).toBe('stroke'));
            // the halo stroke equals the theme surface, so it separates the glyph
            // from an overlapping circle/edge in BOTH themes.
            const strokes = valuesFor(r.records, 'stroke');
            expect(strokes).toContain(bg);
            // a non-trivial halo width was set on the label
            expect(valuesFor(r.records, 'stroke-width').some(v => Number(v) >= 2)).toBe(true);
        });
});

// ── D-202: group rects derived from member positions ─────────────────────────
describe('D-202 — group rects frame their own members (no hardcoded ternary)', () => {
    it('computeGroupRect bounds the members and DIFFERS per group (old ternary gave both x=680)', () => {
        const nodeById = new Map<string, any>([
            ['n1', { id: 'n1', x: 100, y: 100, size: 10 }],
            ['n2', { id: 'n2', x: 160, y: 140, size: 10 }],
            ['n3', { id: 'n3', x: 480, y: 300, size: 10 }],
            ['n4', { id: 'n4', x: 520, y: 260, size: 10 }],
        ]);
        const gA = computeGroupRect(['n1', 'n2'], nodeById)!;
        const gB = computeGroupRect(['n3', 'n4'], nodeById)!;
        expect(gA).not.toBeNull();
        expect(gB).not.toBeNull();
        // bounds actually enclose the members (x-r-pad .. x+r+pad)
        expect(gA.x).toBeLessThanOrEqual(100 - 10);
        expect(gA.x + gA.width).toBeGreaterThanOrEqual(160 + 10);
        // the two groups occupy DIFFERENT x positions (the defect: identical 680)
        expect(Math.abs(gA.x - gB.x)).toBeGreaterThan(100);
    });

    it('returns null when no member resolves to a positioned node (caller skips it)', () => {
        const nodeById = new Map<string, any>([['n1', { id: 'n1', x: 10, y: 10 }]]);
        expect(computeGroupRect(['ghost'], nodeById)).toBeNull();
        expect(computeGroupRect([], nodeById)).toBeNull();
        // a member with no finite position does not count
        const m = new Map<string, any>([['x', { id: 'x' }]]);
        expect(computeGroupRect(['x'], m)).toBeNull();
    });

    it('render draws one dashed rect per resolvable group at distinct positions (BOTH themes)', () => {
        const spec = {
            type: 'network',
            nodes: [
                { id: 'n1', x: 100, y: 100, size: 10 },
                { id: 'n2', x: 160, y: 140, size: 10 },
                { id: 'n3', x: 480, y: 300, size: 10 },
                { id: 'n4', x: 520, y: 260, size: 10 },
            ],
            links: [{ source: 'n1', target: 'n3' }],
            groups: [
                { id: 'left', label: 'Left board', members: ['n1', 'n2'] },
                { id: 'right', label: 'Right board', members: ['n3', 'n4'] },
            ],
            width: 640, height: 400,
        };
        for (const dark of [false, true]) {
            const r = makeRecorder();
            networkDiagramPlugin.render(document.createElement('div'), r.d3, spec, dark);
            // The two group rects are drawn at DISTINCT x positions derived from
            // their members (pre-fix: identical x=680 for every non-modem group).
            // `x` is a per-datum function attr set by the board rects + captions.
            const xs = valuesFor(r.records, 'x').filter(v => typeof v === 'number');
            expect(new Set(xs).size).toBeGreaterThanOrEqual(2);
            // both group captions rendered (were superimposed into one blob pre-fix)
            const texts = valuesFor(r.records, 'text').map(String);
            expect(texts).toContain('Left board');
            expect(texts).toContain('Right board');
        }
    });
});

// ── D-204: low-opacity link stroke clamped to the graphical floor ─────────────
describe('D-204 — a low caller linkOpacity no longer ghosts the topology', () => {
    it('DIRECTION: at opacity 0.35 even a pure-black stroke composites below 3:1 on light', () => {
        // so the stroke colour alone cannot rescue it — opacity must be raised.
        expect(contrastRatio(compositeOver('#000000', NETWORK_LIGHT_BG, 0.35), NETWORK_LIGHT_BG)).toBeLessThan(3);
        // and the pale default at 0.35 was a ghost hairline (w1-09).
        expect(contrastRatio(compositeOver('#999999', NETWORK_LIGHT_BG, 0.35), NETWORK_LIGHT_BG)).toBeLessThan(3);
    });

    it('resolveNetworkColors raises opacity on light so the composite clears 3:1', () => {
        const light = resolveNetworkColors(false, { linkOpacity: 0.35 });
        expect(light.linkOpacity).toBeGreaterThan(0.35);     // opacity escalated (only lever left)
        expect(contrastRatio(compositeOver(light.linkColor, NETWORK_LIGHT_BG, light.linkOpacity), NETWORK_LIGHT_BG))
            .toBeGreaterThanOrEqual(3);
    });

    it('the composite clears 3:1 on dark too (resolved via stroke and/or opacity)', () => {
        const dark = resolveNetworkColors(true, { linkOpacity: 0.35 });
        expect(contrastRatio(compositeOver(dark.linkColor, NETWORK_DARK_BG, dark.linkOpacity), NETWORK_DARK_BG))
            .toBeGreaterThanOrEqual(3);
    });

    it('a comfortable default opacity is NOT altered (no regression on the healthy path)', () => {
        const light = resolveNetworkColors(false, {});
        const dark = resolveNetworkColors(true, {});
        expect(light.linkOpacity).toBe(0.9);
        expect(dark.linkOpacity).toBe(0.9);
    });
});

// ── D-245: group rect stroke + caption theme-resolved (was hardcoded #666) ────
describe('D-245 — group rect stroke/label colour resolves per theme (not #666)', () => {
    const spec = {
        type: 'network',
        nodes: [
            { id: 'n1', x: 100, y: 100, size: 10 },
            { id: 'n2', x: 160, y: 140, size: 10 },
        ],
        links: [],
        groups: [{ id: 'g', label: 'Board', members: ['n1', 'n2'] }],
        width: 640, height: 400,
    };

    it('DIRECTION: the old hardcoded #666 fails both floors on dark (2.87:1)', () => {
        // stroke floor 3:1, text floor 4.5:1 — #666 clears neither on #1f1f1f.
        expect(contrastRatio('#666666', NETWORK_DARK_BG)).toBeLessThan(3);
    });

    it.each([false, true])('group stroke+caption use the theme label colour, never #666 (isDarkMode=%p)', (dark) => {
        const r = makeRecorder();
        networkDiagramPlugin.render(document.createElement('div'), r.d3, spec, dark);
        const expected = resolveNetworkColors(dark, {}).labelColor;
        const strokes = valuesFor(r.records, 'stroke').map(String);
        const fills = valuesFor(r.records, 'fill').map(String);
        // The dashed board rect stroke and the caption fill are the theme label
        // colour (pre-fix: literal '#666' on both).
        expect(strokes).toContain(expected);
        expect(fills).toContain(expected);
        expect(strokes).not.toContain('#666');
        expect(strokes).not.toContain('#666666');
        // and that colour clears the floors on the active canvas in BOTH themes.
        const bg = dark ? NETWORK_DARK_BG : NETWORK_LIGHT_BG;
        expect(contrastRatio(expected, bg)).toBeGreaterThanOrEqual(4.5);
    });
});
