/**
 * @jest-environment jsdom
 */
/**
 * D-373 (G-31d219 / d3-w2-05) regression test: y-axis tick count must scale
 * with plot height.
 *
 * Defect: basicChart emitted the y axis with `d3.axisLeft(y)` and NO `.ticks()`
 * argument, so d3 targeted ~10 ticks regardless of height. At a requested
 * 130x100 the ~50px plot still got ~8 numeric labels (0..70 by 10) at ~10px
 * font, stacked into an unreadable overprinted column. The fix ties the tick
 * COUNT to the available height via `heightAwareTickCount` and applies it with
 * `withHeightAwareTicks`.
 *
 * Fail-without-the-fix direction is pinned two ways:
 *  - `heightAwareTickCount` / `withHeightAwareTicks` did not exist before this
 *    change (the import is `undefined` -> the pure assertions throw), and
 *  - behaviourally, the render mock records the tick count handed to the y axis:
 *    the unpatched code called `axisLeft(y)` with no `.ticks`, so no count was
 *    recorded; post-fix a cramped 130x100 chart records a count of 2..3, far
 *    below the ~10 a generous 600x400 chart records.
 *
 * D-373 is a STRUCTURAL defect (theme-independent geometry); the render
 * assertions are exercised in BOTH themes for parity.
 */

import { basicChartPlugin, heightAwareTickCount, withHeightAwareTicks } from '../basicChart';

describe('heightAwareTickCount — tick count scales with plot height (D-373)', () => {
    it('clamps a cramped plot down to the floor of 2 (was ~10 -> stacked labels)', () => {
        // 130x100 spec -> height = 100 - margin.top(20) - margin.bottom(30) = 50.
        expect(heightAwareTickCount(50, 10)).toBe(2);
    });

    it('keeps ~10 ticks for a generous plot (previously-verified renders unchanged)', () => {
        // default 600x400 -> height ~350.
        expect(heightAwareTickCount(350, 10)).toBe(10);
    });

    it('never returns fewer than 2 even for a degenerate/zero height', () => {
        expect(heightAwareTickCount(0, 10)).toBe(2);
        expect(heightAwareTickCount(-40, 12)).toBe(2);
    });

    it('scales monotonically between the floor and cap', () => {
        const small = heightAwareTickCount(80, 10);
        const mid = heightAwareTickCount(180, 10);
        const big = heightAwareTickCount(350, 10);
        expect(small).toBeLessThan(mid);
        expect(mid).toBeLessThanOrEqual(big);
        expect(small).toBeGreaterThanOrEqual(2);
        expect(big).toBeLessThanOrEqual(10);
    });

    it('reserves more room per label for a larger font (fewer ticks)', () => {
        expect(heightAwareTickCount(120, 20)).toBeLessThanOrEqual(heightAwareTickCount(120, 10));
    });
});

describe('withHeightAwareTicks — applies the count, defensive on shims (D-373)', () => {
    it('calls .ticks() with the height-aware count when present', () => {
        let got: number | undefined;
        const axis: any = { ticks: (n: number) => { got = n; return axis; } };
        const out = withHeightAwareTicks(axis, 50, 10);
        expect(got).toBe(2);
        expect(out).toBe(axis);
    });

    it('returns the axis unchanged when it has no .ticks (test shim)', () => {
        const axis: any = () => 'g';
        expect(withHeightAwareTicks(axis, 50, 10)).toBe(axis);
    });
});

// ── render-level: record the tick count handed to the y axis, both themes ────

function makeRecorder() {
    const tickCounts: number[] = [];
    function selection(): any {
        const self: any = {};
        self.append = () => selection();
        self.select = () => selection();
        self.selectAll = () => selection();
        self.data = () => selection();
        self.datum = () => selection();
        self.join = () => selection();
        self.filter = () => selection();
        self.each = () => self;
        self.call = () => self;
        self.remove = () => self;
        self.merge = () => self;
        self.enter = () => self;
        self.exit = () => self;
        self.attr = () => self;
        self.style = () => self;
        self.text = () => self;
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
    const axisBottom: any = () => { const g: any = () => selection(); g.ticks = () => g; return g; };
    // axisLeft records the count it is asked for (the D-373 fix path).
    const axisLeft: any = () => {
        const g: any = () => selection();
        g.ticks = (n: number) => { tickCounts.push(n); return g; };
        return g;
    };
    const d3: any = {
        select: () => selection(),
        scaleBand, scaleLinear, scaleSqrt, line,
        extent: (arr: any[], fn: any) => { const v = arr.map(fn); return [Math.min(...v), Math.max(...v)]; },
        max: (arr: any[], fn: any) => Math.max(...arr.map(fn)),
        axisBottom, axisLeft,
    };
    return { d3, tickCounts };
}

const barData = [
    { label: '0', value: 350 }, { label: '1', value: 352 }, { label: '2', value: 354 },
    { label: '3', value: 355 }, { label: '4', value: 350 },
];

describe('basicChart render — cramped chart thins the y axis, both themes (D-373)', () => {
    it.each([false, true])('a 130x100 bar chart asks for <=3 y ticks (isDarkMode=%p)', (dark) => {
        const r = makeRecorder();
        basicChartPlugin.render(
            document.createElement('div'),
            r.d3,
            { type: 'bar', width: 130, height: 100, data: barData },
            dark,
        );
        expect(r.tickCounts.length).toBeGreaterThan(0);
        expect(Math.min(...r.tickCounts)).toBeLessThanOrEqual(3);
    });

    it.each([false, true])('a default 600x400 bar chart keeps ~10 y ticks (isDarkMode=%p)', (dark) => {
        const r = makeRecorder();
        basicChartPlugin.render(
            document.createElement('div'),
            r.d3,
            { type: 'bar', data: barData },
            dark,
        );
        expect(r.tickCounts).toContain(10);
    });
});
