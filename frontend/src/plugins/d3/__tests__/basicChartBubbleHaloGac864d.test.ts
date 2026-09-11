/**
 * @jest-environment jsdom
 */
/**
 * G-ac864d regression tests for basicChart.ts / chartTheme.ts.
 *
 * Covers:
 *   D-007  bubble-label contrast collapses on a bubble FILL at density. The
 *          label (placed above its own marker) lands on a NEIGHBOURING bubble at
 *          high count; colors.label is guaranteed only against the page bg, so
 *          over a steelblue fill it dropped to ~1.1-1.9:1 (no label cleared the
 *          floor in dark). Fix: a surface-coloured halo painted UNDER the glyph
 *          (paint-order:stroke), so the glyph reads over whatever it overlaps.
 *   D-008  a 10-colour categorical palette recycled 15x over 150 thin bars: the
 *          weakest swatch (which INVERTS by theme) drops below the 3:1 graphical
 *          floor. Fix already in place (D-012): every bar fill routes through
 *          ensureReadableFill, which clears the floor per-theme; locked here.
 *   D-011  caller colour passed to fill unvalidated: transparent / zero-alpha /
 *          design tokens erased or blackened bars. Fix in place (D-012); locked
 *          here against the exact w4-12/13/14 inputs, in BOTH themes.
 *
 * Direction (fail-without-the-fix) is asserted explicitly for D-007: the
 * unpatched plugin emitted NO paint-order on the bubble label, so the halo
 * assertions fail before the change and pass after. Every theme-sensitive check
 * asserts BOTH themes.
 */

import { basicChartPlugin } from '../basicChart';
import {
    resolveChartColors,
    contrastRatio,
    ensureReadableFill,
    CHART_LIGHT_BG,
    CHART_DARK_BG,
} from '../chartTheme';

// ── recording selection mock (mirrors basicChartTheme.test.ts) ───────────────

function makeRecorder() {
    const records: Array<{ key: string; value: any }> = [];

    function selection(data: any[]): any {
        const self: any = {};
        const rec = (key: string, val: any) => {
            if (typeof val === 'function') {
                const rows = data.length ? data : [undefined];
                rows.forEach((d, i) => records.push({ key, value: val(d, i) }));
            } else {
                records.push({ key, value: val });
            }
        };
        self.append = () => selection(data);
        self.select = () => selection(data);
        self.selectAll = () => selection([]);
        self.data = (arr: any[]) => selection(Array.isArray(arr) ? arr : []);
        self.datum = (d: any) => selection([d]);
        self.join = () => selection(data);
        self.filter = (fn?: any) => selection(typeof fn === 'function' ? data.filter(fn) : data);
        self.each = () => self;
        self.call = () => self;
        self.remove = () => self;
        self.merge = () => self;
        self.enter = () => self;
        self.exit = () => self;
        self.attr = (k: string, v: any) => { rec(k, v); return self; };
        self.style = (k: string, v: any) => { rec('style:' + k, v); return self; };
        self.text = (v: any) => { rec('text', v); return self; };
        return self;
    }

    const scaleBand: any = () => {
        const s: any = () => 0;
        s.domain = () => s; s.range = () => s; s.padding = () => s; s.bandwidth = () => 10;
        return s;
    };
    const scaleLinear: any = () => {
        const s: any = (v: number) => v; s.domain = () => s; s.range = () => s; return s;
    };
    const scaleSqrt: any = () => {
        const s: any = (v: number) => Math.sqrt(v); s.domain = () => s; s.range = () => s; return s;
    };
    const line: any = () => { const g: any = () => ''; g.x = () => g; g.y = () => g; return g; };

    const d3: any = {
        select: () => selection([]),
        scaleBand, scaleLinear, scaleSqrt, line,
        extent: (arr: any[], fn: any) => { const v = arr.map(fn); return [Math.min(...v), Math.max(...v)]; },
        max: (arr: any[], fn: any) => Math.max(...arr.map(fn)),
        axisBottom: () => () => selection([]),
        axisLeft: () => () => selection([]),
    };
    return { d3, records };
}

const valuesFor = (records: Array<{ key: string; value: any }>, key: string) =>
    records.filter(r => r.key === key).map(r => r.value);

// ── D-007: bubble-label halo reads over an overlapped fill, both themes ───────

describe('D-007 — bubble label carries a surface halo so it reads over a bubble fill (both themes)', () => {
    // A dense-ish bubble spec; the label of one bubble sits over its neighbour.
    const bubbleSpec = {
        type: 'bubble',
        data: [
            { x: 2, y: 20, size: 40, label: 'alpha' },
            { x: 3, y: 22, size: 45, label: 'beta' },
            { x: 12, y: 85, size: 50, label: 'gamma' },
        ],
        width: 600, height: 400,
    };

    it('paints the halo UNDER the glyph via paint-order:stroke — both themes (fails without the fix)', () => {
        for (const dark of [false, true]) {
            const r = makeRecorder();
            basicChartPlugin.render(document.createElement('div'), r.d3, bubbleSpec, dark);
            // The unpatched plugin emitted NO paint-order on the label -> empty.
            expect(valuesFor(r.records, 'paint-order')).toContain('stroke');
            expect(valuesFor(r.records, 'stroke-linejoin')).toContain('round');
        }
    });

    it('the halo colour is the EFFECTIVE surface, and the glyph fill is the theme label colour', () => {
        const light = makeRecorder();
        basicChartPlugin.render(document.createElement('div'), light.d3, bubbleSpec, false);
        // label fill = light label colour; halo stroke = light surface.
        expect(valuesFor(light.records, 'fill')).toContain('#333333');
        expect(valuesFor(light.records, 'stroke')).toContain(CHART_LIGHT_BG);

        const dark = makeRecorder();
        basicChartPlugin.render(document.createElement('div'), dark.d3, bubbleSpec, true);
        expect(valuesFor(dark.records, 'fill')).toContain('#e0e0e0');
        expect(valuesFor(dark.records, 'stroke')).toContain(CHART_DARK_BG);
    });

    it('glyph-vs-halo contrast clears the 4.5 text floor in BOTH themes (independent of the overlapped fill)', () => {
        const lightC = resolveChartColors(false);
        const darkC = resolveChartColors(true);
        // The glyph sits on its own halo, whose colour is the surface; so the
        // relevant contrast is label vs bg, which is >= 4.5 by construction and
        // does NOT depend on the bubble underneath.
        expect(contrastRatio(lightC.label, lightC.bg)).toBeGreaterThanOrEqual(4.5);
        expect(contrastRatio(darkC.label, darkC.bg)).toBeGreaterThanOrEqual(4.5);
    });
});

// ── D-008: every recycled palette swatch clears the graphical floor per-theme ─

describe('D-008 — the 150-bar recycled palette clears the 3:1 graphical floor on BOTH surfaces', () => {
    // The exact 10-colour tableau palette recycled 15x by basic-chart-w2-13.
    const PALETTE = [
        '#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd',
        '#8c564b', '#e377c2', '#7f7f7f', '#bcbd22', '#17becf',
    ];

    it('at least one swatch is BELOW 3:1 raw on each surface (proves the floor is real, and inverts by theme)', () => {
        const weakLight = PALETTE.some(c => contrastRatio(c, CHART_LIGHT_BG) < 3);
        const weakDark = PALETTE.some(c => contrastRatio(c, CHART_DARK_BG) < 3);
        expect(weakLight).toBe(true);   // e.g. #bcbd22 2.01:1 on white
        expect(weakDark).toBe(true);    // e.g. #8c564b 2.61:1 on dark
    });

    it('after ensureReadableFill every swatch clears 3:1 on the light surface', () => {
        for (const c of PALETTE) {
            const fixed = ensureReadableFill(c, CHART_LIGHT_BG, 'steelblue');
            expect(contrastRatio(fixed, CHART_LIGHT_BG)).toBeGreaterThanOrEqual(3);
        }
    });

    it('after ensureReadableFill every swatch clears 3:1 on the dark surface', () => {
        for (const c of PALETTE) {
            const fixed = ensureReadableFill(c, CHART_DARK_BG, 'steelblue');
            expect(contrastRatio(fixed, CHART_DARK_BG)).toBeGreaterThanOrEqual(3);
        }
    });

    it('the plugin routes every bar fill through the floor guard — no raw sub-floor swatch reaches the canvas', () => {
        const spec = {
            type: 'bar',
            data: PALETTE.map((color, i) => ({ label: String(i), value: 50 + i, color })),
            width: 600, height: 400,
        };
        for (const [dark, bg] of [[false, CHART_LIGHT_BG], [true, CHART_DARK_BG]] as const) {
            const r = makeRecorder();
            basicChartPlugin.render(document.createElement('div'), r.d3, spec, dark);
            const fills = valuesFor(r.records, 'fill').filter((v: any) => typeof v === 'string' && v.startsWith('#'));
            expect(fills.length).toBeGreaterThan(0);
            for (const f of fills) {
                expect(contrastRatio(f, bg)).toBeGreaterThanOrEqual(3);
            }
        }
    });
});

// ── D-011: unvalidated caller colours never erase / blacken a bar, both themes ─

describe('D-011 — caller colour is validated before it reaches fill (w4-12/13/14, both themes)', () => {
    const CASES: Record<string, string[]> = {
        'w4-12 shorthand hex': ['#f90', '#09c', '#333', '#eee'],
        'w4-13 rgba/space-rgb': ['rgba(70,130,180,1)', 'rgba(214,39,40,0.35)', 'rgba(0,0,0,0)', 'rgb(44 160 44)'],
        'w4-14 tokens/transparent': ['var(--ziya-accent)', '$primary', 'theme.colors.chart1', 'transparent'],
    };

    it.each(Object.entries(CASES))('%s: every bar fill clears 3:1 in BOTH themes (no dropped/blackened bar)', (_name, colors) => {
        for (const bg of [CHART_LIGHT_BG, CHART_DARK_BG]) {
            for (const c of colors) {
                const fill = ensureReadableFill(c, bg, 'steelblue');
                // Never a passthrough of an erasing/token value.
                expect(fill).not.toBe('transparent');
                expect(fill).not.toBe('rgba(0,0,0,0)');
                expect(fill.startsWith('var(')).toBe(false);
                expect(fill.startsWith('$')).toBe(false);
                // steelblue fallback (a named keyword) clears the floor; a hex is checked directly.
                const hex = fill === 'steelblue' ? '#4682b4' : fill;
                if (hex.startsWith('#')) {
                    expect(contrastRatio(hex, bg)).toBeGreaterThanOrEqual(3);
                }
            }
        }
    });

    it('render-level: transparent / zero-alpha / token bars all fall back to a visible steelblue', () => {
        const spec = {
            type: 'bar',
            data: [
                { label: 'A', value: 30, color: 'transparent' },
                { label: 'B', value: 55, color: 'rgba(0,0,0,0)' },
                { label: 'C', value: 18, color: 'var(--ziya-accent)' },
                { label: 'D', value: 44, color: 'theme.colors.chart1' },
            ],
            width: 600, height: 400,
        };
        const r = makeRecorder();
        basicChartPlugin.render(document.createElement('div'), r.d3, spec, false);
        const fills = valuesFor(r.records, 'fill');
        expect(fills.filter((v: any) => v === 'steelblue').length).toBe(4);
        expect(fills).not.toContain('transparent');
        expect(fills).not.toContain('rgba(0,0,0,0)');
    });
});
