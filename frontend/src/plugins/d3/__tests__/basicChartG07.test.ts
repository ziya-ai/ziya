/**
 * @jest-environment jsdom
 */
/**
 * G-07 regression tests for basicChart structural / recovery defects.
 *
 * Covers:
 *   D-009  sizeless scatter routed through the bubble branch got r=40 blobs;
 *          the [4,40] range was absolute (count/area-blind) and domain padding
 *          ignored the radius so extremes spilled past the axes.
 *   D-010  bubble labels placed at `y - r - 4` with no top headroom / clamp were
 *          shaved at the SVG top edge for the highest/largest bubble.
 *   D-013  canHandle keyed only on spec.type and render read spec.data/label/
 *          value with no unwrap or field aliasing, so wrapped ({data:{values}})
 *          / typeless / wrong-dialect (name/y) specs matched no plugin or NaN-ed
 *          out to an empty chart.
 *
 * Direction (fail-without-the-fix) is asserted explicitly: the recovery helpers
 * did not exist before, the typeless canHandle branch returned false, the
 * sizeless radius was not fixed-small, and the bubble label y was unclamped.
 */

import {
    basicChartPlugin,
    unwrapChartData,
    aliasBandRow,
    radiusRange,
} from '../basicChart';

// ── D-013: unwrap + alias pure helpers ───────────────────────────────────────

describe('unwrapChartData — nested data payloads are unwrapped (D-013)', () => {
    it('returns a bare array unchanged', () => {
        const a = [{ label: 'A', value: 1 }];
        expect(unwrapChartData(a)).toBe(a);
    });
    it.each([
        ['values', { values: [{ label: 'A', value: 1 }] }],
        ['data', { data: [{ label: 'A', value: 1 }] }],
        ['rows', { rows: [{ label: 'A', value: 1 }] }],
        ['items', { items: [{ label: 'A', value: 1 }] }],
    ])('unwraps a {%s:[...]} wrapper', (_k, wrapped) => {
        expect(unwrapChartData(wrapped)).toEqual([{ label: 'A', value: 1 }]);
    });
    it('returns [] for non-array / non-wrapping input', () => {
        expect(unwrapChartData(undefined)).toEqual([]);
        expect(unwrapChartData(42)).toEqual([]);
        expect(unwrapChartData({ nope: 1 })).toEqual([]);
    });
});

describe('aliasBandRow — foreign field spellings map to {label,value} (D-013)', () => {
    it('maps name/y', () => {
        expect(aliasBandRow({ name: 'A', y: 5 })).toMatchObject({ label: 'A', value: 5 });
    });
    it('maps category/count and coerces a numeric string', () => {
        expect(aliasBandRow({ category: 'B', count: '8' })).toMatchObject({ label: 'B', value: 8 });
    });
    it('leaves canonical {label,value} untouched and preserves color', () => {
        expect(aliasBandRow({ label: 'C', value: 3, color: '#abc' })).toMatchObject({ label: 'C', value: 3, color: '#abc' });
    });
});

// ── D-013: canHandle recovery gate ───────────────────────────────────────────

describe('basicChartPlugin.canHandle — typeless recovery (D-013)', () => {
    it('still accepts the known explicit types', () => {
        for (const type of ['bar', 'line', 'scatter', 'bubble']) {
            expect(basicChartPlugin.canHandle({ type, data: [] })).toBe(true);
        }
    });

    it('recovers a TYPELESS {data:[{label,value}]} spec (was false -> unclaimable -> 30s hang)', () => {
        expect(basicChartPlugin.canHandle({ data: [{ label: 'A', value: 1 }] })).toBe(true);
    });

    it('recovers a typeless wrong-dialect {data:[{name,y}]} and a wrapped {data:{values}} spec', () => {
        expect(basicChartPlugin.canHandle({ data: [{ name: 'A', y: 1 }] })).toBe(true);
        expect(basicChartPlugin.canHandle({ data: { values: [{ label: 'A', value: 1 }] } })).toBe(true);
    });

    it('does NOT hijack another engine: a foreign discriminator key declines even with label/value rows', () => {
        // basic-chart is the highest-priority plugin (tried first); it must not
        // steal a vega-lite / force / chord spec that happens to carry rows.
        expect(basicChartPlugin.canHandle({ nodes: [], data: [{ label: 'A', value: 1 }] })).toBe(false);
        expect(basicChartPlugin.canHandle({ mark: 'bar', data: [{ label: 'A', value: 1 }] })).toBe(false);
        expect(basicChartPlugin.canHandle({ $schema: 'x', data: [{ label: 'A', value: 1 }] })).toBe(false);
    });

    it('declines a typeless spec whose rows are not category/value pairs', () => {
        expect(basicChartPlugin.canHandle({ data: [{ foo: 1, bar: 2 }] })).toBe(false);
        expect(basicChartPlugin.canHandle({ data: [] })).toBe(false);
        expect(basicChartPlugin.canHandle({})).toBe(false);
    });
});

// ── D-009: radius range ──────────────────────────────────────────────────────

describe('radiusRange — sizeless scatter is small dots, bubbles are area/count aware (D-009)', () => {
    it('sizeless scatter -> a FIXED small radius (was every point r=40 via range([4,40]))', () => {
        const rr = radiusRange(false, 540, 350, 10);
        expect(rr.min).toBe(5);
        expect(rr.max).toBe(5);              // NOT 40
        expect(rr.max).toBeLessThan(40);
    });

    it('bubble max radius shrinks as the point count grows for a fixed plot (count-aware)', () => {
        const few = radiusRange(true, 540, 350, 4).max;
        const many = radiusRange(true, 540, 350, 400).max;
        expect(many).toBeLessThan(few);
        expect(few).toBeLessThanOrEqual(40); // still clamped to the sane ceiling
        expect(many).toBeGreaterThanOrEqual(6);
    });
});

// ── render-level: recording mock (evaluates function-valued attrs) ───────────

function makeRecorder() {
    const records: Array<{ key: string; value: any }> = [];
    // `bound` marks a selection produced by `.data()/.datum()` — a data-bound
    // selection. Real d3 never invokes a function-valued attr on an EMPTY bound
    // selection, so the recorder must not synthesise an `undefined` datum for it
    // (that would crash `d => x(d.x)` on a label-less scatter). Non-bound
    // selections (a plain append/select) get a single `[undefined]` eval so their
    // constant/function attrs are still recorded.
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
        self.join = () => selection(data, bound);
        self.filter = (fn?: any) => selection(typeof fn === 'function' ? data.filter(fn) : data, bound);
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
    const scaleLinear: any = () => { const s: any = (v: number) => v; s.domain = () => s; s.range = () => s; return s; };
    const scaleSqrt: any = () => { const s: any = (v: number) => Math.sqrt(v); s.domain = () => s; s.range = () => s; return s; };
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

// ── D-009 render: sizeless scatter uses fixed small radius, not big blobs ────

describe('basicChart render — sizeless scatter radius (D-009), both themes', () => {
    const scatterSpec = {
        type: 'scatter',
        data: [{ x: 1, y: 2 }, { x: 3, y: 4 }, { x: 5, y: 6 }],   // no `size` field
        width: 600, height: 400,
    };
    it.each([false, true])('every point gets the fixed r=5 (isDarkMode=%p)', (dark) => {
        const r = makeRecorder();
        basicChartPlugin.render(document.createElement('div'), r.d3, scatterSpec, dark);
        const radii = valuesFor(r.records, 'r');
        expect(radii.length).toBe(3);
        expect(radii.every(v => v === 5)).toBe(true);
    });
});

// ── D-010 render: bubble label y is clamped inside the SVG top edge ──────────

describe('basicChart render — bubble label top clamp (D-010), both themes', () => {
    // A single high, large bubble: unpatched code emits y = y(d.y)-r-4 = -54
    // (shaved off the top). With the fix the top margin reserves headroom and the
    // baseline is clamped to fontSize-margin.top = 11-57 = -46.
    const spec = {
        type: 'bubble',
        data: [{ x: 5, y: 0, size: 2500, label: 'top' }],
        width: 600, height: 400,
    };
    it.each([false, true])('label baseline is clamped, never the unclamped -54 (isDarkMode=%p)', (dark) => {
        const r = makeRecorder();
        basicChartPlugin.render(document.createElement('div'), r.d3, spec, dark);
        const ys = valuesFor(r.records, 'y');
        expect(ys).toContain(-46);     // clamped baseline (fontSize - reserved top margin)
        expect(ys).not.toContain(-54); // the unpatched, clipped placement
        expect(Math.min(...ys)).toBeGreaterThanOrEqual(-46);
    });
});

// ── D-013 render: typeless aliased spec actually renders bars ────────────────

describe('basicChart render — typeless aliased spec renders (D-013), both themes', () => {
    const aliased = {
        // no `type`, wrong-dialect rows, one wrapped level — the recovery path
        data: { values: [{ name: 'A', y: 5 }, { name: 'B', y: 8 }] },
        width: 600, height: 400,
    };
    it.each([false, true])('does not throw and draws visible bars (isDarkMode=%p)', (dark) => {
        const r = makeRecorder();
        expect(() => basicChartPlugin.render(document.createElement('div'), r.d3, aliased, dark)).not.toThrow();
        // bars fall back to the visible series colour (no per-row color supplied),
        // proving the aliased rows reached the bar-drawing path with real values.
        expect(valuesFor(r.records, 'fill')).toContain('steelblue');
    });
});
