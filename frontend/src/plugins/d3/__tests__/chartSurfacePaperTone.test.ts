/**
 * @jest-environment jsdom
 */
/**
 * Light-mode diagrams follow the selected paper tone.
 *
 * Light mode stopped being pure white (design/visual-refresh.md: linen, warm,
 * cool, white), but every renderer still resolved its light surface to the
 * constant #ffffff.  Six of them painted it (vega-lite, plotly, joint, packet,
 * orbit, chord) and showed a white slab on the paper; the rest used it as the
 * colour of edge-label chips and marker halos, and as the reference for
 * contrast maths.
 *
 * `lightChartSurface()` reads the page canvas token.  These tests cover the
 * helper and, for each pure resolver that can be imported here, that the
 * value actually reaches it.  The renderers that paint inside render() are
 * covered at source level in components/__tests__/d3RendererPaperTone.test.ts.
 *
 * The token is supplied by stubbing getComputedStyle: jsdom has no stylesheet
 * for theme-tokens.css, which is also why every pre-existing test keeps
 * seeing #ffffff.
 */
import {
    CHART_DARK_BG, CHART_LIGHT_BG, chartSurface, lightChartSurface,
    pinLightChartSurface, resolveChartColors, _resetChartSurfaceForTests,
} from '../chartTheme';
import { resolveChordBackground } from '../chordPlugin';
import { resolveNetworkColors } from '../networkDiagram';
import { resolveForceColors } from '../forceDirectedPlugin';
import { resolveEdgeLabelBackground } from '../drawioPlugin';
import { applyPlotlyTheme } from '../plotlyPlugin';
import * as plotly from '../plotlyPlugin';

const LINEN = '#f7f6f3';
const WARM = '#f6f3ee';

let token = '';

function paper(tone: string, hex: string): void {
    document.documentElement.setAttribute('data-paper', tone);
    token = hex;
}

beforeEach(() => {
    token = '';
    _resetChartSurfaceForTests();
    document.documentElement.removeAttribute('data-paper');
    jest.spyOn(window, 'getComputedStyle').mockImplementation((() => ({
        getPropertyValue: (name: string) => (name === '--z-canvas' ? token : ''),
    })) as any);
});

afterEach(() => {
    jest.restoreAllMocks();
    _resetChartSurfaceForTests();
});

describe('lightChartSurface', () => {
    it('is white when the page defines no canvas token', () => {
        expect(lightChartSurface()).toBe(CHART_LIGHT_BG);
        expect(chartSurface(false)).toBe('#ffffff');
    });

    it('is the page canvas when the token is defined', () => {
        paper('linen', ` ${LINEN} `);
        expect(lightChartSurface()).toBe(LINEN);
        expect(chartSurface(false)).toBe(LINEN);
    });

    it('follows a change of paper tone', () => {
        paper('linen', LINEN);
        expect(lightChartSurface()).toBe(LINEN);
        paper('warm', WARM);
        expect(lightChartSurface()).toBe(WARM);
        paper('white', '#ffffff');
        expect(lightChartSurface()).toBe('#ffffff');
    });

    it('does not remember a read made before the stylesheet was there', () => {
        document.documentElement.setAttribute('data-paper', 'linen');
        expect(lightChartSurface()).toBe('#ffffff');     // token still empty
        token = LINEN;                                    // same tone, CSS now loaded
        expect(lightChartSurface()).toBe(LINEN);
    });

    it('expands a shorthand hex and ignores a value it cannot resolve', () => {
        paper('white', '#FFF');
        expect(lightChartSurface()).toBe('#ffffff');
        paper('odd', 'var(--something-else)');
        expect(lightChartSurface()).toBe('#ffffff');
    });

    it('a pinned surface wins, and unpinning restores the page value', () => {
        paper('linen', LINEN);
        pinLightChartSurface('#ffffff');
        expect(lightChartSurface()).toBe('#ffffff');
        pinLightChartSurface(null);
        expect(lightChartSurface()).toBe(LINEN);
    });

    it('dark is not affected by the paper tone', () => {
        paper('linen', LINEN);
        expect(chartSurface(true)).toBe(CHART_DARK_BG);
    });
});

describe('the paper tone reaches each light-mode resolver', () => {
    beforeEach(() => paper('linen', LINEN));

    it('basicChart: surface and marker halo', () => {
        const c = resolveChartColors(false);
        expect(c.bg).toBe(LINEN);
        expect(c.markerStroke).toBe(LINEN);
    });

    it('chord canvas', () => {
        expect(resolveChordBackground(undefined, false)).toBe(LINEN);
    });

    it('network and force-directed effective background', () => {
        expect(resolveNetworkColors(false).effectiveBg).toBe(LINEN);
        expect(resolveForceColors(false).effectiveBg).toBe(LINEN);
    });

    it('drawio edge-label chip', () => {
        const style = resolveEdgeLabelBackground({}, { hasLabel: true, isDarkMode: false });
        expect(style.labelBackgroundColor).toBe(LINEN);
    });

    it('plotly paper, plot and the subplot surfaces plotly defaults to white', () => {
        const out = applyPlotlyTheme({}, false);
        expect(out.paper_bgcolor).toBe(LINEN);
        expect(out.plot_bgcolor).toBe(LINEN);
        expect(out.polar.bgcolor).toBe(LINEN);
        expect(out.ternary.bgcolor).toBe(LINEN);
        expect(out.geo.bgcolor).toBe(LINEN);
    });
});

describe('plotly drag-out export uses a white surface in light mode', () => {
    const exportLayout = (plotly as any).plotlyExportLayout as (l: any, dark: boolean) => any;

    it('surfaces that equal the paper become white; the input is not mutated', () => {
        paper('linen', LINEN);
        const themed = applyPlotlyTheme({}, false);
        expect(themed.paper_bgcolor).toBe(LINEN);                 // control
        const out = exportLayout(themed, false);
        expect(out.paper_bgcolor).toBe('#ffffff');
        expect(out.plot_bgcolor).toBe('#ffffff');
        expect(out.polar.bgcolor).toBe('#ffffff');
        expect(out.ternary.bgcolor).toBe('#ffffff');
        expect(out.geo.bgcolor).toBe('#ffffff');
        expect(out.font).toEqual(themed.font);
        expect(themed.paper_bgcolor).toBe(LINEN);
        expect(themed.polar.bgcolor).toBe(LINEN);
    });

    it('a surface the author pinned is kept', () => {
        paper('linen', LINEN);
        const out = exportLayout({ paper_bgcolor: LINEN, plot_bgcolor: '#102030', polar: { bgcolor: '#eeeeee' } }, false);
        expect(out).toEqual({ paper_bgcolor: '#ffffff', plot_bgcolor: '#102030', polar: { bgcolor: '#eeeeee' } });
    });

    it('dark mode and white paper return the same object', () => {
        paper('linen', LINEN);
        const dark = { paper_bgcolor: LINEN };
        expect(exportLayout(dark, true)).toBe(dark);
        paper('white', '#ffffff');
        const white = { paper_bgcolor: '#ffffff' };
        expect(exportLayout(white, false)).toBe(white);
    });
});

describe('what the paper tone must not change', () => {
    beforeEach(() => paper('linen', LINEN));

    it('dark surfaces', () => {
        expect(resolveChartColors(true).bg).toBe(CHART_DARK_BG);
        expect(resolveChordBackground(undefined, true)).toBe('#1a1a2e');
        expect(applyPlotlyTheme({}, true).paper_bgcolor).toBe('#1e1e1e');
        expect(resolveEdgeLabelBackground({}, { hasLabel: true, isDarkMode: true })
            .labelBackgroundColor).toBe(CHART_DARK_BG);
    });

    it('a background the author pinned', () => {
        expect(resolveChartColors(false, { background: '#102030' }).bg).toBe('#102030');
        expect(resolveChordBackground('#102030', false)).toBe('#102030');
        expect(resolveNetworkColors(false, { background: '#102030' }).effectiveBg).toBe('#102030');
        const out = applyPlotlyTheme({ paper_bgcolor: '#fafafa', polar: { bgcolor: '#eeeeee' } }, false);
        expect(out.paper_bgcolor).toBe('#fafafa');
        expect(out.polar.bgcolor).toBe('#eeeeee');
    });

    it('an edge with no label gets no chip', () => {
        expect(resolveEdgeLabelBackground({}, { hasLabel: false, isDarkMode: false })
            .labelBackgroundColor).toBeUndefined();
    });

    it('on white paper the plotly layout is what it was before', () => {
        paper('white', '#ffffff');
        const out = applyPlotlyTheme({}, false);
        expect(out).toEqual({ paper_bgcolor: '#ffffff', plot_bgcolor: '#ffffff', font: { color: '#333333' } });
    });
});
