/**
 * @jest-environment jsdom
 */
/**
 * G-03 regression tests for basicChart theme-blindness and unvalidated colours.
 *
 * Covers:
 *   D-011  theme-blind axis/label/marker colours (was '#666', '#fff', black axes)
 *   D-012  caller colours passed to fill with no validation / contrast guard
 *   D-007  band x-axis labels: no rotation / thinning / truncation / reserved margin
 *
 * Direction (fail-without-the-fix) is asserted explicitly: every render-level
 * check compares against the exact constant the UNPATCHED plugin emitted
 * ('#666' label, '#fff' stroke, verbatim caller fill), and every theme check
 * asserts BOTH themes — the broken one is now correct AND the other still is.
 */

import {
    basicChartPlugin,
} from '../basicChart';
import {
    resolveChartColors,
    contrastRatio,
    ensureReadableFill,
    classifyColor,
    planBandLabels,
    truncateLabel,
    CHART_LIGHT_BG,
    CHART_DARK_BG,
} from '../chartTheme';

// ── pure helpers: contrast in BOTH themes ────────────────────────────────────

describe('resolveChartColors — theme-resolved defaults clear the WCAG floor on their own surface', () => {
    it('light defaults are readable on the light surface (text >= 4.5, and would fail on dark)', () => {
        const c = resolveChartColors(false);
        expect(c.bg).toBe(CHART_LIGHT_BG);
        expect(contrastRatio(c.label, CHART_LIGHT_BG)).toBeGreaterThanOrEqual(4.5);
        expect(contrastRatio(c.axis, CHART_LIGHT_BG)).toBeGreaterThanOrEqual(4.5);
        // The paired proof: the light label would be illegible on dark — which is
        // exactly why the value is resolved per-theme rather than hardcoded once.
        expect(contrastRatio(c.label, CHART_DARK_BG)).toBeLessThan(3);
    });

    it('dark defaults are readable on the dark surface (text >= 4.5, and would fail on light)', () => {
        const c = resolveChartColors(true);
        expect(c.bg).toBe(CHART_DARK_BG);
        expect(contrastRatio(c.label, CHART_DARK_BG)).toBeGreaterThanOrEqual(4.5);
        expect(contrastRatio(c.axis, CHART_DARK_BG)).toBeGreaterThanOrEqual(4.5);
        expect(contrastRatio(c.label, CHART_LIGHT_BG)).toBeLessThan(3);
    });

    it('the marker halo stroke equals the surface in each theme (separates overlapping markers)', () => {
        // The stroke is deliberately the surface colour, so overlapping markers
        // show a gap of background between them. It flips with the theme (light
        // markers on a light page would otherwise carry a black outline in dark).
        expect(resolveChartColors(false).markerStroke).toBe(CHART_LIGHT_BG);
        expect(resolveChartColors(true).markerStroke).toBe(CHART_DARK_BG);
        expect(resolveChartColors(false).markerStroke).not.toBe(resolveChartColors(true).markerStroke);
    });

    it('the steelblue series fallback clears the 3:1 graphical floor on BOTH surfaces', () => {
        // steelblue = #4682b4
        expect(contrastRatio('#4682b4', CHART_LIGHT_BG)).toBeGreaterThanOrEqual(3);
        expect(contrastRatio('#4682b4', CHART_DARK_BG)).toBeGreaterThanOrEqual(3);
    });

    it('a LEGIBLE caller style.* override wins over the theme default (kept verbatim)', () => {
        // Overrides that clear the 4.5 text floor on the pinned surface pass
        // through unchanged (#dddddd/#bbbbbb on near-black #0a0a0a both >= 4.5).
        const c = resolveChartColors(true, { labelColor: '#dddddd', axisColor: '#bbbbbb', background: '#0a0a0a' });
        expect(c.label).toBe('#dddddd');
        expect(c.axis).toBe('#bbbbbb');
        expect(c.bg).toBe('#0a0a0a');
        expect(contrastRatio(c.label, '#0a0a0a')).toBeGreaterThanOrEqual(4.5);
        expect(contrastRatio(c.axis, '#0a0a0a')).toBeGreaterThanOrEqual(4.5);
    });

    it('reconciles a caller axisColor that fails the text floor on the surface — kept in the theme where legible (D-159)', () => {
        // authored light-tuned navy #0b5394: 7.84:1 on white (kept), 2.13:1 on
        // the dark panel (was applied verbatim -> illegible; now nudged >= 4.5).
        const dark = resolveChartColors(true, { axisColor: '#0b5394' });
        expect(dark.axis).not.toBe('#0b5394');
        expect(contrastRatio(dark.axis, CHART_DARK_BG)).toBeGreaterThanOrEqual(4.5);

        const light = resolveChartColors(false, { axisColor: '#0b5394' });
        expect(light.axis).toBe('#0b5394');
        expect(contrastRatio(light.axis, CHART_LIGHT_BG)).toBeGreaterThanOrEqual(4.5);
    });

    it('derives axis/label defaults from a caller-pinned background luminance, not the theme flag (D-006), both directions', () => {
        // light panel pinned UNDER DARK theme: defaults must be dark-on-light, not
        // the dark theme's pale #cfcfcf/#e0e0e0 (1.43/1.21 on #f5f5f5 = invisible).
        const lightPanelDarkTheme = resolveChartColors(true, { background: '#f5f5f5' });
        expect(lightPanelDarkTheme.bg).toBe('#f5f5f5');
        expect(lightPanelDarkTheme.axis).not.toBe('#cfcfcf');
        expect(contrastRatio(lightPanelDarkTheme.axis, '#f5f5f5')).toBeGreaterThanOrEqual(4.5);
        expect(contrastRatio(lightPanelDarkTheme.label, '#f5f5f5')).toBeGreaterThanOrEqual(4.5);

        // dark panel pinned UNDER LIGHT theme: mirror — defaults go pale-on-dark.
        const darkPanelLightTheme = resolveChartColors(false, { background: '#101010' });
        expect(contrastRatio(darkPanelLightTheme.axis, '#101010')).toBeGreaterThanOrEqual(4.5);
        expect(contrastRatio(darkPanelLightTheme.label, '#101010')).toBeGreaterThanOrEqual(4.5);
        // the marker halo tracks the effective surface, not the theme flag.
        expect(darkPanelLightTheme.markerStroke).toBe('#101010');
    });
});

describe('ensureReadableFill — a sub-floor palette swatch is nudged per-theme (D-009)', () => {
    it('a pale tableau swatch below 3:1 is nudged on the failing theme and kept on the passing one', () => {
        // tableau yellow-green #bcbd22: 2.01:1 on white (fails), 8.29:1 on dark (ok)
        const light = ensureReadableFill('#bcbd22', CHART_LIGHT_BG, 'steelblue');
        expect(light).not.toBe('#bcbd22');
        expect(contrastRatio(light, CHART_LIGHT_BG)).toBeGreaterThanOrEqual(3);
        expect(ensureReadableFill('#bcbd22', CHART_DARK_BG, 'steelblue')).toBe('#bcbd22');

        // mirror: brown #8c564b passes light (5.92) and fails dark (2.82)
        expect(ensureReadableFill('#8c564b', CHART_LIGHT_BG, 'steelblue')).toBe('#8c564b');
        const brownDark = ensureReadableFill('#8c564b', CHART_DARK_BG, 'steelblue');
        expect(brownDark).not.toBe('#8c564b');
        expect(contrastRatio(brownDark, CHART_DARK_BG)).toBeGreaterThanOrEqual(3);
    });
});

// ── D-012 colour validation + contrast clamp ─────────────────────────────────

describe('classifyColor — reject the forms that used to erase geometry or fall to black', () => {
    it.each(['transparent', 'none', '', '   ', 'rgba(0,0,0,0)', 'rgba(12, 34, 56, 0)'])(
        'treats %p as absent (null)', (v) => {
            expect(classifyColor(v)).toBeNull();
        });

    it.each(['var(--chart-1)', '$blue-500', 'theme.accent', 'token.text.primary', 'primary color'])(
        'treats unresolvable token %p as absent (null)', (v) => {
            expect(classifyColor(v)).toBeNull();
        });

    it('parses hex and opaque rgb()/rgba()', () => {
        expect(classifyColor('#abc')).toEqual({ hex: '#abc' });
        expect(classifyColor('#aabbcc')).toEqual({ hex: '#aabbcc' });
        expect(classifyColor('rgb(70,130,180)')).toEqual({ hex: '#4682b4' });
        expect(classifyColor('rgba(70,130,180,0.5)')).toEqual({ hex: '#4682b4' });
    });

    it('passes a bare CSS keyword through as named', () => {
        expect(classifyColor('steelblue')).toEqual({ named: 'steelblue' });
        expect(classifyColor('red')).toEqual({ named: 'red' });
    });
});

describe('ensureReadableFill — unusable colour falls back; low-contrast hex is nudged', () => {
    it('transparent / token -> theme fallback (was passed through verbatim and vanished)', () => {
        expect(ensureReadableFill('transparent', CHART_LIGHT_BG, 'steelblue')).toBe('steelblue');
        expect(ensureReadableFill('var(--x)', CHART_DARK_BG, 'steelblue')).toBe('steelblue');
        expect(ensureReadableFill('rgba(0,0,0,0)', CHART_DARK_BG, 'steelblue')).toBe('steelblue');
    });

    it('near-surface fill is nudged until it clears 3:1 — on BOTH themes', () => {
        const light = ensureReadableFill('#fafafa', CHART_LIGHT_BG, 'steelblue');
        expect(light).not.toBe('#fafafa');
        expect(contrastRatio(light, CHART_LIGHT_BG)).toBeGreaterThanOrEqual(3);

        const dark = ensureReadableFill('#202020', CHART_DARK_BG, 'steelblue');
        expect(dark).not.toBe('#202020');
        expect(contrastRatio(dark, CHART_DARK_BG)).toBeGreaterThanOrEqual(3);
    });

    it('a colour that already clears the floor is returned unchanged', () => {
        expect(ensureReadableFill('#4682b4', CHART_LIGHT_BG, 'orange')).toBe('#4682b4');
    });
});

// ── D-007 band-label fitting ─────────────────────────────────────────────────

describe('planBandLabels — rotate / thin / truncate dense or long categories', () => {
    it('leaves few short labels upright with no truncation', () => {
        const plan = planBandLabels(['A', 'B', 'C'], 540, 11, 30);
        expect(plan.rotate).toBe(false);
        expect(plan.keepEvery).toBe(1);
        expect(plan.maxChars).toBe(Infinity);
        expect(plan.reservedBottom).toBe(30);
    });

    it('rotates and reserves extra bottom margin for long labels', () => {
        const labels = Array.from({ length: 30 }, (_, i) => `Category-${i}-longname`);
        const plan = planBandLabels(labels, 540, 11, 30);
        expect(plan.rotate).toBe(true);
        expect(plan.reservedBottom).toBeGreaterThan(30);
        expect(plan.maxChars).toBeLessThanOrEqual(16);
    });

    it('thins ticks (keepEvery > 1) when a single char will not fit per slot', () => {
        const labels = Array.from({ length: 120 }, (_, i) => `L${i}`);
        const plan = planBandLabels(labels, 540, 11, 30);
        expect(plan.keepEvery).toBeGreaterThan(1);
    });

    it('truncateLabel adds an ellipsis only past the cap', () => {
        expect(truncateLabel('short', 16)).toBe('short');
        expect(truncateLabel('a-very-long-category-name', 8)).toBe('a-very-\u2026');
        expect(truncateLabel('x', Infinity)).toBe('x');
    });
});

// ── render-level wiring: the plugin actually threads the theme + validator ───

/**
 * Recording selection mock that (unlike a bare chainable Proxy) EVALUATES
 * function-valued attr/style args against bound data, so per-datum fills are
 * observable — this is what lets us prove the caller-colour validator is wired
 * into the data-bound marks, not just the static labels.
 */
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
        self.filter = () => selection(data);
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

describe('basicChart render — theme is threaded (D-011) and caller colour validated (D-012)', () => {
    const barSpec = {
        type: 'bar',
        data: [
            { label: 'A', value: 5, color: 'transparent' },   // must NOT survive verbatim
            { label: 'B', value: 8, color: 'var(--brand)' },   // token -> fallback
            { label: 'C', value: 3 },                          // no colour -> fallback
        ],
        width: 600, height: 400,
    };

    const bubbleSpec = {
        type: 'bubble',
        data: [{ x: 2, y: 20, size: 15, label: 'alpha' }, { x: 12, y: 85, size: 50, label: 'gamma' }],
        width: 600, height: 400,
    };

    it('does not throw in either theme', () => {
        const c = document.createElement('div');
        expect(() => basicChartPlugin.render(c, makeRecorder().d3, barSpec, false)).not.toThrow();
        expect(() => basicChartPlugin.render(c, makeRecorder().d3, bubbleSpec, true)).not.toThrow();
    });

    it('bubble labels use the theme label colour, NOT the old hardcoded #666 — both themes', () => {
        const light = makeRecorder();
        basicChartPlugin.render(document.createElement('div'), light.d3, bubbleSpec, false);
        const lightFills = valuesFor(light.records, 'fill');
        expect(lightFills).toContain('#333333');   // light label
        expect(lightFills).not.toContain('#666');  // the unpatched constant

        const dark = makeRecorder();
        basicChartPlugin.render(document.createElement('div'), dark.d3, bubbleSpec, true);
        const darkFills = valuesFor(dark.records, 'fill');
        expect(darkFills).toContain('#e0e0e0');     // dark label
        expect(darkFills).not.toContain('#666');
        // parity: the dark run must not reuse the light label colour and vice-versa
        expect(darkFills).not.toContain('#333333');
    });

    it('marker stroke follows the theme surface, NOT the old hardcoded #fff — both themes', () => {
        const light = makeRecorder();
        basicChartPlugin.render(document.createElement('div'), light.d3, bubbleSpec, false);
        expect(valuesFor(light.records, 'stroke')).toContain('#ffffff');

        const dark = makeRecorder();
        basicChartPlugin.render(document.createElement('div'), dark.d3, bubbleSpec, true);
        const darkStrokes = valuesFor(dark.records, 'stroke');
        expect(darkStrokes).toContain('#1e1e1e');
        expect(darkStrokes).not.toContain('#fff');  // the unpatched constant
    });

    it('caller transparent / token / missing fills all resolve to a visible fallback, never passed through', () => {
        const r = makeRecorder();
        basicChartPlugin.render(document.createElement('div'), r.d3, barSpec, false);
        const fills = valuesFor(r.records, 'fill');
        // three bars, all unusable inputs -> all fall back to steelblue
        expect(fills.filter(v => v === 'steelblue').length).toBe(3);
        expect(fills).not.toContain('transparent');   // the old verbatim passthrough
        expect(fills).not.toContain('var(--brand)');
    });

    it('axis text is coloured for the theme (fixes invisible black axes in dark)', () => {
        const dark = makeRecorder();
        basicChartPlugin.render(document.createElement('div'), dark.d3, barSpec, true);
        expect(valuesFor(dark.records, 'style:fill')).toContain('#cfcfcf');
    });
});
