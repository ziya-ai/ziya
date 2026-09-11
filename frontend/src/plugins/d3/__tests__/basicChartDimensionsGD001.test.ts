/**
 * @jest-environment jsdom
 */
/**
 * D-001 regression test: explicit spec width/height must be honoured.
 *
 * Defect (basic-chart-w2-08..11): D3Renderer built the plugin argument as
 * `{ ...spec, width: width || 600, height: height || 400 }` — spreading the spec
 * and then UNCONDITIONALLY overwriting width/height with the component's own
 * (container-derived) props. A spec requesting 110x80 / 220x2400 / 3600x2600 had
 * those discarded, so every chart came out the same ~600x400 landscape.
 *
 * The fix is a pure merge helper, `resolvePluginDimensions`, wired into the one
 * plugin-render call site so an explicit numeric spec dimension wins and a spec
 * that omits dimensions is a strict no-op (falls back to the renderer props /
 * 600x400).
 *
 * Fail-without-the-fix is pinned two ways:
 *  - the helper did not exist before this change (import undefined -> TypeError);
 *  - behaviourally, the OLD code's result equals the fallback regardless of the
 *    spec, so the "spec dims win" assertions below could not hold.
 *
 * D-001 is STRUCTURAL (theme-independent geometry); the basic-chart render is
 * additionally exercised in BOTH themes for parity.
 */

import { resolvePluginDimensions } from '../../../utils/pluginDimensions';
import { basicChartPlugin } from '../basicChart';

describe('resolvePluginDimensions — explicit spec dims win over renderer props (D-001)', () => {
    it.each([
        [{ width: 110, height: 80 }, { width: 110, height: 80 }],
        [{ width: 220, height: 2400 }, { width: 220, height: 2400 }],
        [{ width: 3600, height: 2600 }, { width: 3600, height: 2600 }],
    ])('honours explicit %o (was clobbered to the 600x400 fallback)', (spec, expected) => {
        // Fallbacks are the renderer's container-derived props; the spec must win.
        expect(resolvePluginDimensions(spec, 600, 400)).toEqual(expected);
    });

    it('is a strict no-op when the spec omits dimensions (falls back to renderer props)', () => {
        expect(resolvePluginDimensions({ type: 'bar', data: [] }, 512, 768)).toEqual({ width: 512, height: 768 });
    });

    it('falls back to 600x400 when neither spec nor props supply a size', () => {
        expect(resolvePluginDimensions({}, undefined, undefined)).toEqual({ width: 600, height: 400 });
    });

    it('ignores a non-positive / non-numeric spec dimension and falls back', () => {
        expect(resolvePluginDimensions({ width: 0, height: -5 }, 600, 400)).toEqual({ width: 600, height: 400 });
        expect(resolvePluginDimensions({ width: '900', height: null }, 600, 400)).toEqual({ width: 600, height: 400 });
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

describe('basicChart render — an explicit portrait canvas is drawn at the requested size (D-001), both themes', () => {
    // A portrait request (default gutter {20,20,30,40}): SVG outer size is
    // width = 220, height = 2400 (was collapsed to the 600x400 landscape once
    // D3Renderer discarded spec.width/height before this fix reached the plugin).
    const spec = {
        type: 'bar',
        data: [{ label: 'a', value: 1 }, { label: 'b', value: 2 }],
        width: 220,
        height: 2400,
    };
    it.each([false, true])('SVG width/height reflect the requested canvas (isDarkMode=%p)', (dark) => {
        const r = makeRecorder();
        basicChartPlugin.render(document.createElement('div'), r.d3, spec, dark);
        // outer svg attrs = (width - l - r) + l + r  ==  requested width, and likewise height.
        expect(valuesFor(r.records, 'width')).toContain(220);
        expect(valuesFor(r.records, 'height')).toContain(2400);
        // never the discarded-dims landscape default.
        expect(valuesFor(r.records, 'width')).not.toContain(600);
    });
});
