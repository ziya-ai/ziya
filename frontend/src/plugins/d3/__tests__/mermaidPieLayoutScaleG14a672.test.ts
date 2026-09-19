/**
 * @jest-environment jsdom
 */
/**
 * G-14a672 / D-152 (pie-legend-truncated-and-slice-labels-collide-at-scale).
 *
 * Distinct from the D-159 palette defect (G-79): this is a STRUCTURAL at-scale
 * failure in the vendored (upstream) mermaid pie renderer. At 60 slices it
 *   (1) lays the legend out as one row per slice down a single column that is
 *       VERTICALLY CENTRED on the pie hub (upstream places row `t` at local y
 *       `t*E - E*n/2` inside the group translated to the pie centre), while
 *       sizing the viewBox as `minX 0 w <pieHeight>` — origin at 0, height the
 *       pie square only. So the column runs from a large NEGATIVE y (above the
 *       box top) to a large positive y (below the box bottom) and only the
 *       middle band of rows is visible ("rows 20-39 of 60");
 *   (2) leaves the title squeezed against / occluded by that legend block; and
 *   (3) emits a percentage <text> at every wedge centroid, which at 60 slices
 *       converge on the hub into an illegible mass.
 *
 * fixPieLayoutAtScale() repairs the emitted SVG (post-render surgery, like the
 * gantt grid z-order / edge-reroute passes) rather than special-casing a spec:
 * it grows the viewBox to the UNION of pie box + full legend column (in ALL
 * four directions, so the rows ABOVE minY are un-clipped too, not just the ones
 * below), and once labels are dense enough to collide it removes them (values
 * still carried by the legend/showData).
 *
 * DIRECTION: each assertion is paired with a demonstration that the UNFIXED
 * SVG exhibits the defect (both the FIRST and LAST legend rows fall outside the
 * centred viewBox; 60 slice labels present), so the fix is doing real work. The
 * repair is layout-only and theme-independent, so it is asserted to behave
 * identically for a light-themed and a dark-themed pie SVG.
 */
import {
    fixPieLayoutAtScale,
    PIE_LABEL_COLLISION_THRESHOLD,
    buildPieThemeVariables,
    recolorPieSlicesAtScale,
    recolorPieTextForTheme,
} from '../mermaidPlugin';
import { calculateContrastRatio } from '../../../utils/colorUtils';

// Build a mermaid-like pie <svg> with `n` slices matching the UPSTREAM default
// layout: a group `m` translated to the pie centre (225,225); a title above the
// pie; `n` centroid percentage labels; and a legend column that is VERTICALLY
// CENTRED on the hub (row `t` at local y `t*E - E*n/2`, exactly as upstream).
// The viewBox has origin y = 0 and height = the pie square only (450) — so at
// scale the centred column overflows the box at BOTH the top and the bottom.
const PIE_SIZE = 450;
const LEGEND_ROW = 22; // upstream E
function buildPieSvg(n: number): SVGSVGElement {
    const SVG_NS = 'http://www.w3.org/2000/svg';
    const half = PIE_SIZE / 2; // 225 — the centre translate
    const legendPane = 300;
    const vbW = PIE_SIZE + legendPane;
    const svg = document.createElementNS(SVG_NS, 'svg') as SVGSVGElement;
    // Upstream sizes the box as `V 0 q W`: origin Y is 0, height is the pie
    // square only (it does NOT account for the centred legend column height).
    svg.setAttribute('viewBox', `0 0 ${vbW} ${PIE_SIZE}`);
    svg.setAttribute('width', String(vbW));
    svg.setAttribute('height', String(PIE_SIZE));

    // The centred group `m` — legend rows are its children, so their absolute
    // position is (half + localX, half + localY).
    const m = document.createElementNS(SVG_NS, 'g');
    m.setAttribute('transform', `translate(${half}, ${half})`);
    svg.appendChild(m);

    // Title just above the pie (upstream y = -200 within m).
    const title = document.createElementNS(SVG_NS, 'text');
    title.setAttribute('class', 'pieTitleText');
    title.setAttribute('x', '0');
    title.setAttribute('y', '-200');
    title.textContent = `${n}-slice categorical palette recycling test`;
    m.appendChild(title);

    const centreOffset = (LEGEND_ROW * n) / 2; // a = E*n/2
    for (let i = 0; i < n; i++) {
        const slice = document.createElementNS(SVG_NS, 'path');
        slice.setAttribute('class', 'pieCircle');
        m.appendChild(slice);

        const label = document.createElementNS(SVG_NS, 'text');
        label.setAttribute('class', 'slice');
        label.textContent = `${(100 / n).toFixed(1)}%`;
        m.appendChild(label);

        // Legend row: swatch + label, VERTICALLY CENTRED (y = t*E - E*n/2).
        const leg = document.createElementNS(SVG_NS, 'g');
        leg.setAttribute('class', 'legend');
        leg.setAttribute('transform', `translate(216, ${i * LEGEND_ROW - centreOffset})`);
        const rect = document.createElementNS(SVG_NS, 'rect');
        rect.setAttribute('width', '18');
        rect.setAttribute('height', '18');
        leg.appendChild(rect);
        const lt = document.createElementNS(SVG_NS, 'text');
        lt.textContent = `Category number ${i}`;
        leg.appendChild(lt);
        m.appendChild(leg);
    }
    return svg;
}

function vbTop(svg: Element): number {
    const [, y] = (svg.getAttribute('viewBox') || '0 0 0 0')
        .split(/[\s,]+/).map(Number);
    return y;
}
function vbBottom(svg: Element): number {
    const [, y, , h] = (svg.getAttribute('viewBox') || '0 0 0 0')
        .split(/[\s,]+/).map(Number);
    return y + h;
}
function vbRight(svg: Element): number {
    const [x, , w] = (svg.getAttribute('viewBox') || '0 0 0 0')
        .split(/[\s,]+/).map(Number);
    return x + w;
}
function transY(el: Element): number {
    const m = (el.getAttribute('transform') || '').match(/translate\(\s*(-?[\d.]+)[ ,]+(-?[\d.]+)/);
    return m ? parseFloat(m[2]) : 0;
}
// Absolute Y of a legend row in ROOT/viewBox space: its own translate plus its
// centred parent-group translate (the same accumulation the fix does).
function rowTop(el: Element): number {
    const parentY = el.parentElement ? transY(el.parentElement) : 0;
    return parentY + transY(el);
}
function firstLegendTop(svg: Element): number {
    const items = svg.querySelectorAll('g.legend');
    return rowTop(items[0]);
}
function lastLegendBottom(svg: Element): number {
    const items = svg.querySelectorAll('g.legend');
    const last = items[items.length - 1];
    return rowTop(last) + 18;
}

describe('G-14a672 D-152 pie legend/label layout at scale', () => {
    it('DIRECTION: the unfixed 60-slice svg clips the legend at BOTH top and bottom', () => {
        const svg = buildPieSvg(60);
        // The centred column runs above the box top (negative-ish, < minY=0)…
        expect(firstLegendTop(svg)).toBeLessThan(vbTop(svg));
        // …and below the box bottom — only the middle band is visible.
        expect(lastLegendBottom(svg)).toBeGreaterThan(vbBottom(svg));
        // All 60 centroid percentage labels are present (the colliding mass).
        expect(svg.querySelectorAll('text.slice')).toHaveLength(60);
    });

    it('grows the viewBox to enclose the full centred legend column at scale', () => {
        const svg = buildPieSvg(60);
        const r = fixPieLayoutAtScale(svg);
        expect(r.isPie).toBe(true);
        expect(r.sliceCount).toBe(60);
        expect(r.viewBoxExpanded).toBe(true);
        // Every legend row now falls inside the viewBox — the TOP rows (above the
        // original minY=0) are the ones the old downward-only fix left clipped.
        expect(vbTop(svg)).toBeLessThanOrEqual(firstLegendTop(svg));
        expect(vbBottom(svg)).toBeGreaterThanOrEqual(lastLegendBottom(svg));
        // And there is horizontal room for the legend labels to the right.
        expect(vbRight(svg)).toBeGreaterThan(450);
    });

    it('removes the colliding centroid percentage labels past the threshold', () => {
        const svg = buildPieSvg(60);
        const r = fixPieLayoutAtScale(svg);
        expect(r.labelsRemoved).toBe(60);
        expect(svg.querySelectorAll('text.slice')).toHaveLength(0);
    });

    it('is theme-independent: identical repair on a dark-styled pie svg', () => {
        // Structural fix must not depend on theme; a dark-themed svg (only the
        // fills would differ) gets the same viewBox growth + label removal.
        const light = buildPieSvg(60);
        const dark = buildPieSvg(60);
        dark.querySelectorAll('path.pieCircle').forEach((p) =>
            p.setAttribute('style', 'fill:#7fb3e0'));
        const rl = fixPieLayoutAtScale(light);
        const rd = fixPieLayoutAtScale(dark);
        expect(rd.viewBoxExpanded).toBe(rl.viewBoxExpanded);
        expect(rd.labelsRemoved).toBe(rl.labelsRemoved);
        expect(dark.getAttribute('viewBox')).toBe(light.getAttribute('viewBox'));
    });

    it('leaves a small pie untouched (centred legend fits, labels legible)', () => {
        const n = PIE_LABEL_COLLISION_THRESHOLD - 4; // 20 slices: fits the square
        const svg = buildPieSvg(n);
        const beforeVB = svg.getAttribute('viewBox');
        // A 20-row centred column spans root y 225 + (t*22 - 220) for t=0..19,
        // i.e. 5..423 — inside the 0..450 box, so nothing overflows.
        expect(firstLegendTop(svg)).toBeGreaterThanOrEqual(vbTop(svg));
        expect(lastLegendBottom(svg)).toBeLessThanOrEqual(vbBottom(svg));
        const r = fixPieLayoutAtScale(svg);
        expect(r.isPie).toBe(true);
        // No labels removed below the collision threshold.
        expect(r.labelsRemoved).toBe(0);
        expect(svg.querySelectorAll('text.slice')).toHaveLength(n);
        // viewBox not grown (small pie is a no-op).
        expect(r.viewBoxExpanded).toBe(false);
        expect(svg.getAttribute('viewBox')).toBe(beforeVB);
    });

    it('ignores non-pie svgs', () => {
        const SVG_NS = 'http://www.w3.org/2000/svg';
        const svg = document.createElementNS(SVG_NS, 'svg');
        svg.setAttribute('viewBox', '0 0 100 100');
        const r = fixPieLayoutAtScale(svg);
        expect(r.isPie).toBe(false);
        expect(r.viewBoxExpanded).toBe(false);
    });
});

// ---------------------------------------------------------------------------
// G-14a672 / D-152 (light branch): the at-scale legend rendered WHITE-ON-WHITE
// in light mode — only ~3 of 60 rows visible — because the light path (mermaid
// theme 'default') never pinned a pie legend/section/title text colour, so its
// near-white default leaked through, while dark (theme 'dark') supplied a light
// text colour and read fine. The fix resolves the pie text colour FROM the
// theme inside buildPieThemeVariables. These assertions fail against the old
// build (the keys were absent) and pass now, in BOTH themes.
// ---------------------------------------------------------------------------
describe('G-14a672 D-152 pie legend/section/title text colour resolves per theme', () => {
    const LIGHT_BG = '#ffffff';
    const DARK_BG = '#1f1f1f';
    const TEXT_FLOOR = 4.5;
    const PIE_TEXT_KEYS = ['pieLegendTextColor', 'pieSectionTextColor', 'pieTitleTextColor'];

    it('light theme pins all pie text keys, legible on the white canvas', () => {
        const vars = buildPieThemeVariables(false);
        for (const k of PIE_TEXT_KEYS) {
            expect(vars[k]).toBeDefined();
            // regression guard: not the near-white default that caused the bug
            expect(vars[k].toLowerCase()).not.toBe('#ffffff');
            expect(calculateContrastRatio(vars[k], LIGHT_BG)).toBeGreaterThanOrEqual(TEXT_FLOOR);
            // and it must NOT be legible-only-on-dark (i.e. resolved from theme)
            expect(calculateContrastRatio(vars[k], DARK_BG)).toBeLessThan(TEXT_FLOOR);
        }
    });

    it('dark theme pins all pie text keys, legible on the dark canvas', () => {
        const vars = buildPieThemeVariables(true);
        for (const k of PIE_TEXT_KEYS) {
            expect(vars[k]).toBeDefined();
            expect(calculateContrastRatio(vars[k], DARK_BG)).toBeGreaterThanOrEqual(TEXT_FLOOR);
        }
    });

    it('the two themes use DIFFERENT text colours (resolved, not a constant)', () => {
        const light = buildPieThemeVariables(false);
        const dark = buildPieThemeVariables(true);
        expect(light.pieLegendTextColor).not.toBe(dark.pieLegendTextColor);
    });
});

// ---------------------------------------------------------------------------
// G-14a672 / D-152 REGRESSION GUARD — full pie post-render pipeline ORDER.
//
// The isolation suites above prove fixPieLayoutAtScale and buildPieThemeVariables
// are individually correct. But the headless renderer runs THREE post-render
// passes on the emitted pie SVG, in a fixed order:
//     recolorPieSlicesAtScale  ->  recolorPieTextForTheme  ->  fixPieLayoutAtScale
// (see renderSingleDiagram: the `if (diagramType === 'pie')` block). D-152
// regressed after later passes (the D-157 slice recolour and D-420 text recolour)
// were inserted AHEAD of the layout fix. Nothing exercised that composition, so a
// pass that mutated the legend/slice structure the layout fix depends on — e.g.
// removing `g.legend` rows or `path.pieCircle` slices while recolouring — would
// silently defeat the viewBox growth and re-clip the legend, exactly the
// regressed symptom, while every isolation test stayed green.
//
// This suite runs the passes in the real pipeline order on a 60-slice pie in
// BOTH themes and asserts the layout fix STILL sees an intact legend and grows
// the box to enclose it, the colliding centroid labels are still dropped, and
// the recoloured legend/title text is legible on the theme's own canvas.
// DIRECTION: a control run that drops the legend before the layout fix (the
// shape of the regression) is shown to leave the box UNGROWN, so the guard is
// verified to be load-bearing.
// ---------------------------------------------------------------------------
describe('G-14a672 D-152 full pie pipeline order (regression guard)', () => {
    const CANVAS = { light: '#ffffff', dark: '#1f1f1f' } as const;
    const TEXT_FLOOR = 4.5;

    const runPipeline = (svg: SVGSVGElement, isDark: boolean) => {
        // Exactly the order renderSingleDiagram applies for diagramType === 'pie'.
        const rc = recolorPieSlicesAtScale(svg, isDark);
        const tn = recolorPieTextForTheme(svg, isDark);
        const rl = fixPieLayoutAtScale(svg);
        return { rc, tn, rl };
    };

    (['light', 'dark'] as const).forEach((theme) => {
        const isDark = theme === 'dark';

        it(`[${theme}] the layout fix still encloses the full legend AFTER the recolour passes`, () => {
            const svg = buildPieSvg(60);
            // Precondition: the emitted (unfixed) SVG clips both ends.
            expect(firstLegendTop(svg)).toBeLessThan(vbTop(svg));
            expect(lastLegendBottom(svg)).toBeGreaterThan(vbBottom(svg));

            const { rc, rl } = runPipeline(svg, isDark);

            // Recolour ran on the >12-slice chart, and CRUCIALLY did not remove
            // the legend/slice structure the layout fix consumes.
            expect(rc.recolored).toBe(60);
            expect(svg.querySelectorAll('g.legend')).toHaveLength(60);

            // Layout fix, running LAST, still grew the box around every row.
            expect(rl.viewBoxExpanded).toBe(true);
            expect(vbTop(svg)).toBeLessThanOrEqual(firstLegendTop(svg));
            expect(vbBottom(svg)).toBeGreaterThanOrEqual(lastLegendBottom(svg));
            expect(vbRight(svg)).toBeGreaterThan(450);
            // and the colliding centroid labels are gone.
            expect(rl.labelsRemoved).toBe(60);
            expect(svg.querySelectorAll('text.slice')).toHaveLength(0);
        });

        it(`[${theme}] recoloured legend + title text is legible on the ${theme} canvas`, () => {
            const svg = buildPieSvg(60);
            const { tn } = runPipeline(svg, isDark);
            // Title + one label per legend row were recoloured.
            expect(tn).toBeGreaterThanOrEqual(61);
            const bg = CANVAS[theme];
            svg.querySelectorAll('.pieTitleText, g.legend text').forEach((el) => {
                const fill = (el as HTMLElement).getAttribute('fill') || '';
                expect(fill).toBeTruthy();
                expect(calculateContrastRatio(fill, bg)).toBeGreaterThanOrEqual(TEXT_FLOOR);
            });
        });
    });

    it('DIRECTION: dropping the legend before the layout fix leaves the box UNGROWN (the regressed shape)', () => {
        const svg = buildPieSvg(60);
        recolorPieSlicesAtScale(svg, false);
        recolorPieTextForTheme(svg, false);
        // Simulate a mis-ordered / destructive pass that removes the legend rows
        // the layout fix relies on — the exact failure mode this guard protects
        // against. With no legend to enclose, the box cannot be grown.
        svg.querySelectorAll('g.legend').forEach((g) => g.parentNode?.removeChild(g));
        const before = svg.getAttribute('viewBox');
        const rl = fixPieLayoutAtScale(svg);
        expect(rl.viewBoxExpanded).toBe(false);
        expect(svg.getAttribute('viewBox')).toBe(before);
    });
});
