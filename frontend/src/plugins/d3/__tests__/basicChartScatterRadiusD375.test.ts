/**
 * @jest-environment jsdom
 */
/**
 * D-375 regression (d3-w1-05): a plain continuous x/y scatter authored with a
 * CONSTANT `size` (every row size:10) was routed through the bubble radius
 * scale. `rScale = scaleSqrt().domain([0, maxSize]).range([minRadius,maxRadius])`
 * maps a degenerate (all-equal) size domain so every point lands on `maxSize`
 * and draws at `maxRadius` (~r=40): all markers inflate to the same large blob
 * and adjacent points physically overlap, so a scatter cannot read as discrete
 * points.
 *
 * Fix: `sizeVaries(data)` — a size dimension counts only when at least two
 * DISTINCT finite sizes exist. A uniform-size dataset is treated as sizeless, so
 * `radiusRange(false, …)` returns the fixed small radius (5) and `radiusOf`
 * returns it directly (ignoring rScale). A genuine bubble chart with graduated
 * sizes still varies, so its area/count-aware radii are unchanged.
 *
 * Direction (fail-without-the-fix) is asserted: `sizeVaries` did not exist
 * before, and for the uniform scatter the pre-fix `radiusOf` returned
 * `rScale(size)` (NOT the fixed 5), so the render assertion fails before the
 * change and passes after. Structural defect — identical in light and dark, but
 * both themes are exercised to be safe.
 */

import { basicChartPlugin, sizeVaries, radiusRange } from '../basicChart';

// ── recording d3 stub (mirrors basicChartBubbleHaloGac864d.test.ts) ───────────

function makeRecorder() {
    const records: Array<{ key: string; value: any }> = [];

    function selection(data: any[]): any {
        const self: any = {};
        const rec = (key: string, val: any) => {
            if (typeof val === 'function') {
                // Real d3 calls the accessor once per bound datum; an EMPTY
                // selection (e.g. the label branch of a label-less scatter)
                // calls it zero times. Do not fabricate an undefined row.
                data.forEach((d, i) => records.push({ key, value: val(d, i) }));
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
    // sqrt stub (range-blind) — enough to prove the uniform-scatter path no
    // longer routes radii through rScale: sqrt(10) !== the fixed 5.
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

// ── sizeVaries pure helper ────────────────────────────────────────────────────

describe('D-375 — sizeVaries: a size dimension counts only when it actually varies', () => {
    it('is FALSE for a uniform-size scatter (all rows size:10)', () => {
        const rows = [
            { x: 1.2, y: 8.4, size: 10 }, { x: 2.8, y: 15.1, size: 10 },
            { x: 4.1, y: 11.7, size: 10 }, { x: 5.6, y: 22.3, size: 10 },
        ];
        expect(sizeVaries(rows)).toBe(false);
    });

    it('is FALSE for sizeless rows; a LONE sized datum stays a bubble (D-010)', () => {
        expect(sizeVaries([{ x: 1, y: 2 }, { x: 3, y: 4 }])).toBe(false);
        expect(sizeVaries([])).toBe(false);
        // A single sized point cannot overlap and the author gave a size — it
        // must keep its bubble radius (the D-010 single high/large bubble).
        expect(sizeVaries([{ x: 1, y: 2, size: 9 }])).toBe(true);
    });

    it('is TRUE for a genuine bubble chart with graduated sizes', () => {
        const rows = [
            { x: 0, y: 0, size: 5 }, { x: 7, y: 23, size: 12 }, { x: 14, y: 46, size: 19 },
        ];
        expect(sizeVaries(rows)).toBe(true);
    });

    it('a degenerate size domain routes through the fixed small radius, a varying one does not', () => {
        // radiusRange(hasSize=false) is the fixed small dot; hasSize=true is the
        // area/count-aware bubble range. sizeVaries is what selects between them.
        const fixed = radiusRange(false, 560, 330, 7);
        expect(fixed.min).toBe(fixed.max);            // a single dot size
        expect(fixed.max).toBeLessThanOrEqual(6);
        const bubble = radiusRange(true, 560, 330, 7);
        expect(bubble.max).toBeGreaterThan(fixed.max); // graduated markers grow
    });
});

// ── render: uniform-size scatter draws discrete fixed-radius dots ─────────────

describe('D-375 — a uniform-size scatter renders as fixed small dots, not size-mapped blobs (both themes)', () => {
    // d3-w1-05: continuous x/y scatter, every row size:10.
    const scatterSpec = {
        type: 'scatter',
        width: 620,
        height: 380,
        data: [
            { x: 1.2, y: 8.4, size: 10 }, { x: 2.8, y: 15.1, size: 10 },
            { x: 4.1, y: 11.7, size: 10 }, { x: 5.6, y: 22.3, size: 10 },
            { x: 7.0, y: 19.8, size: 10 }, { x: 8.4, y: 28.6, size: 10 },
            { x: 9.9, y: 25.0, size: 10 },
        ],
    };

    // The plot area after the default margins (matches basicChart's math).
    const plotW = 620 - 40 - 20;
    const plotH = 380 - 20 - 30;
    const expectedR = radiusRange(false, plotW, plotH, 7).min; // fixed dot = 5

    it('every marker radius is the fixed small dot, NOT the size-scaled radius (fails without the fix)', () => {
        for (const dark of [false, true]) {
            const r = makeRecorder();
            basicChartPlugin.render(document.createElement('div'), r.d3, scatterSpec, dark);
            const radii = valuesFor(r.records, 'r');
            expect(radii.length).toBe(scatterSpec.data.length);
            // Fixed small dot. Before the fix radiusOf returned rScale(size)
            // (= sqrt(10) ≈ 3.16 under the stub, ~40 in production) — never 5.
            for (const rr of radii) {
                expect(rr).toBe(expectedR);
            }
            expect(expectedR).toBeLessThanOrEqual(6);
        }
    });
});

// ── control: a real bubble chart still uses the size scale (no regression) ────

describe('D-375 — a graduated bubble chart still maps radii through the size scale', () => {
    const bubbleSpec = {
        type: 'bubble',
        width: 620,
        height: 380,
        data: [
            { x: 0, y: 0, size: 5 }, { x: 7, y: 23, size: 20 }, { x: 14, y: 46, size: 54 },
        ],
    };

    it('bubble radii are size-derived (vary across differently-sized markers)', () => {
        const r = makeRecorder();
        basicChartPlugin.render(document.createElement('div'), r.d3, bubbleSpec, false);
        const radii = valuesFor(r.records, 'r');
        expect(radii.length).toBe(bubbleSpec.data.length);
        // The stub rScale is sqrt(size), so the three distinct sizes yield
        // distinct radii — proving the bubble path is unchanged by the fix.
        const distinct = new Set(radii.map((v: number) => Math.round(v * 1000)));
        expect(distinct.size).toBeGreaterThan(1);
    });
});
