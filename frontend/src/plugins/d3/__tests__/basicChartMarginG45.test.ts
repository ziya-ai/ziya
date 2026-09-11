/**
 * @jest-environment jsdom
 */
/**
 * G-45 / D-050 regression test: basic-chart margin gutter clamp.
 *
 * Defect (basic-chart-w2-15): a caller-supplied `margin:{top:0,right:0,bottom:0,
 * left:0}` was honoured verbatim. With no left/bottom gutter the y-axis tick
 * labels render at negative x (left of the SVG origin) and the x-axis labels at
 * y=height render below the bottom edge, so every axis label is clipped OUTSIDE
 * the viewport. The fix clamps the effective margin UP to the known-good
 * defaultMargin on each side — a strict no-op for the default path and for any
 * caller supplying margins already >= default, and a bump only for a
 * too-small / degenerate margin.
 *
 * Direction (fail-without-the-fix) is pinned two ways:
 *  - the pure helper `effectiveMargin` did not exist before this change (import
 *    would be undefined -> TypeError), and
 *  - behaviourally, the x-axis <g> is translated to `translate(0,${height})`;
 *    with the clamp height = 400 - 20 - 30 = 350, whereas the unpatched code
 *    left margin {0,0,0,0} so height = 400 and the transform was
 *    `translate(0,400)` (bottom edge) with the plot group at `translate(0,0)`
 *    (labels off the left edge).
 *
 * D-050 is a STRUCTURAL defect (theme-independent geometry), but the render
 * assertions are still exercised in BOTH themes for parity.
 */

import { basicChartPlugin, effectiveMargin } from '../basicChart';

const DEFAULT = { top: 20, right: 20, bottom: 30, left: 40 };

describe('effectiveMargin — gutter clamp (D-050)', () => {
    it('bumps a degenerate {0,0,0,0} margin up to the default gutter (was honoured verbatim -> labels clipped)', () => {
        expect(effectiveMargin({ top: 0, right: 0, bottom: 0, left: 0 })).toEqual(DEFAULT);
    });

    it('is a no-op when no margin is supplied (default path unchanged)', () => {
        expect(effectiveMargin(undefined)).toEqual(DEFAULT);
    });

    it('never REDUCES a caller margin already larger than the default', () => {
        expect(effectiveMargin({ top: 50, right: 60, bottom: 70, left: 80 }))
            .toEqual({ top: 50, right: 60, bottom: 70, left: 80 });
    });

    it('clamps each side independently (small left/bottom raised, large top/right kept)', () => {
        expect(effectiveMargin({ top: 100, right: 100, bottom: 2, left: 3 }))
            .toEqual({ top: 100, right: 100, bottom: 30, left: 40 });
    });
});

// ── render-level: recording mock (evaluates function-valued attrs) ───────────

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

describe('basicChart render — degenerate margin is clamped so axes stay in the viewport (D-050), both themes', () => {
    // Mirrors basic-chart-w2-15: a line chart, no width/height (600x400 default),
    // all margins 0. Post-fix: plot group at translate(40,20), x-axis at
    // translate(0,350). Pre-fix: plot group at translate(0,0), x-axis at
    // translate(0,400) (labels shoved off the left/bottom edges).
    const spec = {
        type: 'line',
        data: [{ label: 'a', value: 1 }, { label: 'b', value: 2 }, { label: 'c', value: 3 }],
        margin: { top: 0, right: 0, bottom: 0, left: 0 },
    };
    it.each([false, true])('x-axis is translated to the clamped plot height (isDarkMode=%p)', (dark) => {
        const r = makeRecorder();
        basicChartPlugin.render(document.createElement('div'), r.d3, spec, dark);
        const transforms = valuesFor(r.records, 'transform');
        // plot group is offset by the clamped left/top gutter (was translate(0,0)).
        expect(transforms).toContain('translate(40,20)');
        // x-axis sits at the clamped plot height 350 (was 400, the very bottom edge).
        expect(transforms).toContain('translate(0,350)');
        expect(transforms).not.toContain('translate(0,400)');
        expect(transforms).not.toContain('translate(0,0)');
    });
});
