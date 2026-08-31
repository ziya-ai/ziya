/**
 * G-30 regression tests for the Vega-Lite theme + structural helpers.
 *
 * Covers:
 *   D-258  an authored light `background` under the dark theme renders a white
 *          card AND collapses the dark theme's white guide-title onto it
 *          (white-on-#fff = 1.00:1).  reconcileBackground drops the wrong-
 *          polarity background so the theme surface applies.
 *   D-259  the boxplot composite mark's whisker/cap RULES keep a near-black
 *          stroke on the dark card (#000 on #333 = 1.66:1); themeBoxplotStrokes
 *          themes rule/ticks/median on a dark canvas only.
 *   D-261  arc/donut labels were placed with radiusOffset and no base radius,
 *          collapsing every label to the donut centre; enhanceArcChartsWith-
 *          TextLabels now emits an absolute `radius` + stacked theta so labels
 *          spread to each slice's centroid angle.
 *   D-262  a top-level layered dual-axis chart lost `resolve.scale.y:independent`
 *          to the blanket delete, flattening the second series; sanitizeResolve-
 *          Scale preserves resolve for a layered spec, still strips the nested
 *          faceted case.
 *
 * Both-theme direction is asserted for the theme defects: the broken theme is
 * shown fixed AND the other theme shown unchanged/still-correct.
 */

import {
    reconcileThemeColors,
    reconcileBackground,
    themeBoxplotStrokes,
    enhanceArcChartsWithTextLabels,
    sanitizeResolveScale,
    resolveColorToRgb,
    contrastRatio,
} from '../vegaRecovery';

const cr = (a: string, b: string) =>
    contrastRatio(resolveColorToRgb(a)!, resolveColorToRgb(b)!);

// ── D-258: authored background of the wrong polarity ─────────────────────────

describe('reconcileBackground — wrong-polarity background dropped (D-258)', () => {
    test('light background under dark theme is dropped (was a white card)', () => {
        const spec: any = { background: '#fff', mark: 'bar' };
        reconcileBackground(spec, /* isDarkMode */ true);
        expect(spec.background).toBeUndefined();
    });

    test('light background under LIGHT theme is kept (unchanged)', () => {
        const spec: any = { background: '#fff', mark: 'bar' };
        reconcileBackground(spec, false);
        expect(spec.background).toBe('#fff');
    });

    test('dark background under dark theme is kept (author intent honoured)', () => {
        const spec: any = { background: '#101020', mark: 'bar' };
        reconcileBackground(spec, true);
        expect(spec.background).toBe('#101020');
    });

    test('unresolvable / transparent background is left untouched', () => {
        const s1: any = { background: 'transparent' };
        const s2: any = { background: 'var(--page)' };
        reconcileBackground(s1, true);
        reconcileBackground(s2, true);
        expect(s1.background).toBe('transparent');
        expect(s2.background).toBe('var(--page)');
    });

    test('w4-12: via reconcileThemeColors the white bg is dropped in dark and the #333 label is made readable; light keeps both', () => {
        const make = () => ({
            background: '#fff',
            mark: { type: 'bar', color: '#38a' },
            encoding: {
                x: { field: 'c', type: 'nominal', axis: { labelColor: '#333' } },
                y: { field: 'v', type: 'quantitative', axis: { labelColor: '#333' } },
            },
        });

        // DARK: background removed → theme dark surface (#333). The #333 label
        // (1.00:1 on #333) is nudged to a readable value.
        const dark: any = make();
        reconcileThemeColors(dark, true);
        expect(dark.background).toBeUndefined();
        const darkLabel = dark.encoding.x.axis.labelColor;
        expect(darkLabel).not.toBe('#333');
        expect(cr(darkLabel, '#333333')).toBeGreaterThanOrEqual(3);
        // Direction: the dark theme's white guide title would have been
        // white-on-#fff at 1.00 had the background survived.
        expect(cr('#ffffff', '#ffffff')).toBeCloseTo(1.0, 2);
        expect(cr('#ffffff', '#333333')).toBeGreaterThan(12); // now readable

        // LIGHT: background stays, #333 label stays (12.63:1 on white).
        const light: any = make();
        reconcileThemeColors(light, false);
        expect(light.background).toBe('#fff');
        expect(light.encoding.x.axis.labelColor).toBe('#333');
        expect(cr('#333333', '#ffffff')).toBeGreaterThan(12);
    });
});

// ── D-259: boxplot whisker/cap rule stroke ───────────────────────────────────

describe('themeBoxplotStrokes — composite rule/tick strokes themed on dark (D-259)', () => {
    const boxSpec = () => ({
        mark: { type: 'boxplot', extent: 1.5 },
        encoding: {
            y: { field: 'ep', type: 'nominal' },
            x: { field: 'ms', type: 'quantitative' },
        },
    });

    test('dark canvas: rule/ticks/median strokes set to a readable colour', () => {
        const spec: any = boxSpec();
        themeBoxplotStrokes(spec, '#e8e8e8', /* darkCanvas */ true);
        const bp = spec.config.boxplot;
        expect(bp.rule.stroke).toBe('#e8e8e8');
        expect(bp.ticks.stroke).toBe('#e8e8e8');
        expect(bp.median.stroke).toBe('#e8e8e8');
        // The default near-black whisker was 1.66:1 on the #333 card; readable now.
        expect(cr('#000000', '#333333')).toBeLessThan(2);
        expect(cr('#e8e8e8', '#333333')).toBeGreaterThanOrEqual(3);
    });

    test('light canvas: no boxplot config injected (defaults already legible)', () => {
        const spec: any = boxSpec();
        themeBoxplotStrokes(spec, '#333333', /* darkCanvas */ false);
        expect(spec.config?.boxplot).toBeUndefined();
        expect(cr('#000000', '#ffffff')).toBeGreaterThan(3); // default fine in light
    });

    test('author-pinned stroke is not overridden', () => {
        const spec: any = boxSpec();
        spec.config = { boxplot: { rule: { stroke: '#ff0000' } } };
        themeBoxplotStrokes(spec, '#e8e8e8', true);
        expect(spec.config.boxplot.rule.stroke).toBe('#ff0000');
        // ticks/median still get themed
        expect(spec.config.boxplot.ticks.stroke).toBe('#e8e8e8');
    });

    test('non-boxplot spec is untouched', () => {
        const spec: any = { mark: 'bar' };
        themeBoxplotStrokes(spec, '#e8e8e8', true);
        expect(spec.config).toBeUndefined();
    });
});

// ── D-261: arc/donut label radial placement ──────────────────────────────────

describe('enhanceArcChartsWithTextLabels — labels placed radially, not at centre (D-261)', () => {
    const donut = () => ({
        mark: { type: 'arc', innerRadius: 60, outerRadius: 120 },
        encoding: {
            theta: { field: 'tb', type: 'quantitative' },
            color: { field: 'class', type: 'nominal' },
        },
        data: { values: [{ class: 'Standard', tb: 420 }, { class: 'Archive', tb: 180 }] },
    });

    test('text layer uses an absolute radius outside the ring + stacked theta (was radiusOffset/no-radius)', () => {
        const out: any = enhanceArcChartsWithTextLabels(donut(), false);
        expect(Array.isArray(out.layer)).toBe(true);
        const textLayer = out.layer.find((l: any) => l.mark?.type === 'text');
        expect(textLayer).toBeDefined();
        // Fix: absolute radius, spreads labels to slice centroids.
        expect(typeof textLayer.mark.radius).toBe('number');
        expect(textLayer.mark.radius).toBeGreaterThan(120); // outside outerRadius
        // Direction: the unpatched form set radiusOffset and NO base radius,
        // which collapsed labels to the centre.
        expect(textLayer.mark.radiusOffset).toBeUndefined();
        // theta forced stacked so each label aligns to its slice angle.
        expect(textLayer.encoding.theta.stack).toBe(true);
    });

    test('label colour follows the theme in both directions', () => {
        const darkOut: any = enhanceArcChartsWithTextLabels(donut(), true);
        const lightOut: any = enhanceArcChartsWithTextLabels(donut(), false);
        const darkText = darkOut.layer.find((l: any) => l.mark?.type === 'text');
        const lightText = lightOut.layer.find((l: any) => l.mark?.type === 'text');
        // dark canvas (#333) → light ink readable; light canvas (#fff) → dark ink readable
        expect(cr(darkText.encoding.color.value, '#333333')).toBeGreaterThanOrEqual(3);
        expect(cr(lightText.encoding.color.value, '#ffffff')).toBeGreaterThanOrEqual(3);
    });

    test('non-arc spec is returned unchanged', () => {
        const bar: any = { mark: 'bar', encoding: { x: { field: 'a' } } };
        expect(enhanceArcChartsWithTextLabels(bar, false)).toBe(bar);
    });
});

// ── D-262: independent y-scale preserved for layered dual-axis ────────────────

describe('sanitizeResolveScale — independent scale preserved for layered charts (D-262)', () => {
    const dualAxis = () => ({
        encoding: { x: { field: 'month', type: 'nominal' } },
        layer: [
            { mark: 'bar', encoding: { y: { field: 'signups', type: 'quantitative' } } },
            { mark: 'line', encoding: { y: { field: 'conv', type: 'quantitative' } } },
        ],
        resolve: { scale: { y: 'independent' } },
    });

    test('top-level layered spec keeps resolve.scale.y independent (was flattened)', () => {
        const spec: any = dualAxis();
        sanitizeResolveScale(spec);
        expect(spec.resolve).toBeDefined();
        expect(spec.resolve.scale.y).toBe('independent');
    });

    test('non-layered spec still has resolve.scale stripped', () => {
        const spec: any = { mark: 'bar', resolve: { scale: { y: 'independent' } } };
        sanitizeResolveScale(spec);
        expect(spec.resolve).toBeUndefined();
    });

    test('nested faceted spec.resolve is still stripped (hang guard)', () => {
        const spec: any = {
            facet: { field: 'g', type: 'nominal' },
            spec: { layer: [], resolve: { scale: { y: 'independent' } } },
        };
        sanitizeResolveScale(spec);
        expect(spec.spec.resolve).toBeUndefined();
    });
});
