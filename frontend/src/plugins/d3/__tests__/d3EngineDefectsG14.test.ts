/**
 * @jest-environment jsdom
 *
 * d3-engine defect coverage (iteration 14).
 *
 * The d3 renderer-family routes several spec shapes through basicChart and the
 * force-directed plugin. Most d3-engine defects in the backlog were repaired as
 * a side-effect of the basic-chart (iter13) and force-directed (iter10) plugin
 * rewrites and are already covered by the basicChart and forceDirected suites. This
 * file closes the one behavioural assertion those suites did not make:
 *
 *   D-087 wrong-dialect-field-names-no-alias (d3-w4-08):
 *     An EXPLICIT `type:'bar'` spec whose rows use a foreign dialect
 *     ({name, y} — a Highcharts/Vega habit) passes canHandle (which keys on
 *     spec.type) and then, in the unpatched engine, `d3.max(data, d=>d.value)`
 *     is undefined -> y-domain [0, undefined] -> every bar height NaN and ZERO
 *     visible bars (a silent empty chart, not an error). The existing D-013
 *     render test only exercises the TYPELESS recovery path; this asserts the
 *     explicit-type path too, because `aliasBandRow` runs for every band chart
 *     regardless of whether `type` was supplied.
 *
 * Direction: against the unpatched basicChart (no `aliasBandRow`), the bar
 * `height` values are computed from `d.value` (undefined) and are NaN, so the
 * `Number.isFinite` assertion below fails; with the alias they are finite.
 */
import { basicChartPlugin } from '../basicChart';

// Recording mock d3 (mirrors basicChartG07): evaluates function-valued attrs
// against the bound data so we can inspect the values written to bars.
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

describe('D-087 — explicit type:"bar" with wrong-dialect {name,y} rows renders real bars', () => {
    const barNameY = {
        type: 'bar',
        data: [{ name: 'A', y: 5 }, { name: 'B', y: 8 }, { name: 'C', y: 3 }],
        width: 600, height: 400,
    };

    it.each([false, true])('bar heights are finite (not NaN) in both themes (isDarkMode=%p)', (dark) => {
        const r = makeRecorder();
        expect(() => basicChartPlugin.render(document.createElement('div'), r.d3, barNameY, dark)).not.toThrow();
        // `height` records include the outer <svg> height constant plus one per
        // bar; every one must be finite. Unpatched (`d.value` undefined) the three
        // bar heights are `height - y(undefined)` = NaN -> this assertion fails.
        const heights = valuesFor(r.records, 'height');
        expect(heights.length).toBeGreaterThanOrEqual(3);
        expect(heights.every((h) => Number.isFinite(h))).toBe(true);
        expect(heights.filter((h) => h > 0).length).toBeGreaterThanOrEqual(3);
    });

    it('bars fall back to the visible series colour (rows reached the draw path)', () => {
        const r = makeRecorder();
        basicChartPlugin.render(document.createElement('div'), r.d3, barNameY, false);
        expect(valuesFor(r.records, 'fill')).toContain('steelblue');
    });
});
