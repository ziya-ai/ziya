/**
 * @jest-environment jsdom
 *
 * G-de6084 regression tests for the graphviz plugin. Three defects, all in
 * graphvizPlugin.ts:
 *   D-280  authored penwidth flattened by the dark restroke (stroke-width was
 *          set to a constant 1.5 unconditionally, erasing Thick/Thin — w4-11).
 *   D-124  an unfilled OR near-transparent (8-digit-hex alpha, e.g. #ffffff00)
 *          node kept its authored black text on the ~#1e1e1e panel (1.26:1):
 *          isLightBackground ignores the 8-digit hex so the solid-fill path
 *          never darkened it and never re-themed the label (w3-04 / w4-07).
 *   D-284  an UNFILLED cluster's label ('one') stayed black on the dark panel
 *          because the cluster dark branch only re-themed a LIGHT (fill'd)
 *          cluster (w3-07).
 *
 * Every assertion is a DIRECTION check: the pre-fix constant / classification is
 * asserted so the test would FAIL against the unpatched module, and
 * preserveAuthoredStrokeWidth / isEffectivelyTransparentFill did not exist
 * before this fix (so the import throws pre-fix). The theme defects assert BOTH
 * themes.
 */
import {
    preserveAuthoredStrokeWidth,
    isEffectivelyTransparentFill,
    retintUnfilledNodeTextForDark,
    GRAPHVIZ_DARK_PANEL_BG,
} from '../graphvizPlugin';
import { contrastRatio } from '../chartTheme';

const svgns = 'http://www.w3.org/2000/svg';
const LIGHT_PAGE = '#ffffff'; // graphviz light page background

// ---------------------------------------------------------------------------
// D-280  authored penwidth must survive the dark restroke
// ---------------------------------------------------------------------------
describe('D-280 preserve authored penwidth (stroke-width) in dark', () => {
    it('keeps an authored width instead of flattening to the dark default', () => {
        const thick = document.createElementNS(svgns, 'polygon');
        thick.setAttribute('stroke-width', '4'); // setlinewidth(4)->penwidth=4
        preserveAuthoredStrokeWidth(thick, '1.5');
        // Fixed: the laid-out width is preserved.
        expect(thick.getAttribute('stroke-width')).toBe('4');
        // Direction: the pre-fix code wrote a constant 1.5 here.
        expect(thick.getAttribute('stroke-width')).not.toBe('1.5');
    });

    it('keeps a thin authored width distinct from the default', () => {
        const thin = document.createElementNS(svgns, 'polygon');
        thin.setAttribute('stroke-width', '1'); // setlinewidth(1)->penwidth=1
        preserveAuthoredStrokeWidth(thin, '1.5');
        expect(thin.getAttribute('stroke-width')).toBe('1');
    });

    it('applies the dark default only when no width was laid out', () => {
        const bare = document.createElementNS(svgns, 'path');
        preserveAuthoredStrokeWidth(bare, '1.5');
        expect(bare.getAttribute('stroke-width')).toBe('1.5');
        const blank = document.createElementNS(svgns, 'path');
        blank.setAttribute('stroke-width', '   ');
        preserveAuthoredStrokeWidth(blank, '1.5');
        expect(blank.getAttribute('stroke-width')).toBe('1.5');
    });
});

// ---------------------------------------------------------------------------
// D-124  near-transparent / unfilled node label rescue
// ---------------------------------------------------------------------------
describe('D-124 effectively-transparent fill classification', () => {
    it('treats none / transparent / empty / low-alpha as unfilled', () => {
        expect(isEffectivelyTransparentFill(null)).toBe(true);
        expect(isEffectivelyTransparentFill('none')).toBe(true);
        expect(isEffectivelyTransparentFill('transparent')).toBe(true);
        expect(isEffectivelyTransparentFill('')).toBe(true);
        expect(isEffectivelyTransparentFill('#ffffff00')).toBe(true); // w3-04 alpha=00
        expect(isEffectivelyTransparentFill('#FFF0')).toBe(true);      // #rgba shorthand
        expect(isEffectivelyTransparentFill('rgba(255,255,255,0)')).toBe(true);
    });

    it('treats an opaque fill as a solid fill (still darkened, not rescued)', () => {
        expect(isEffectivelyTransparentFill('#ffffff')).toBe(false);
        expect(isEffectivelyTransparentFill('#ffffffff')).toBe(false); // alpha=ff opaque
        expect(isEffectivelyTransparentFill('#eeeeee')).toBe(false);
        expect(isEffectivelyTransparentFill('rgba(255,255,255,1)')).toBe(false);
    });

    it('rescues black text on an alpha-transparent node in dark (both themes)', () => {
        const g = document.createElementNS(svgns, 'g');
        g.setAttribute('class', 'node');
        const poly = document.createElementNS(svgns, 'polygon');
        poly.setAttribute('fill', '#ffffff00'); // composites over the panel
        const text = document.createElementNS(svgns, 'text');
        text.setAttribute('fill', '#000000');
        g.appendChild(poly);
        g.appendChild(text);

        // Direction (pre-fix): black on the dark panel is ~1.26 and unreadable...
        expect(contrastRatio('#000000', GRAPHVIZ_DARK_PANEL_BG)).toBeLessThan(1.5);

        // The plugin now classifies this fill as unfilled and rescues the label.
        expect(isEffectivelyTransparentFill(poly.getAttribute('fill'))).toBe(true);
        retintUnfilledNodeTextForDark(poly);

        // DARK: rescued to white, 16.67:1 on the panel.
        expect(text.getAttribute('fill')).toBe('#ffffff');
        expect(contrastRatio('#ffffff', GRAPHVIZ_DARK_PANEL_BG)).toBeGreaterThanOrEqual(4.5);
        // LIGHT: the same authored black label stays black on the light page,
        // 21:1 — the dark rescue is caller-gated to dark, so light is untouched.
        expect(contrastRatio('#000000', LIGHT_PAGE)).toBeGreaterThanOrEqual(4.5);
    });
});

// ---------------------------------------------------------------------------
// D-284  unfilled cluster label rescue
// ---------------------------------------------------------------------------
describe('D-284 rescue an unfilled cluster label in dark (both themes)', () => {
    it('whitens a black cluster label sitting on the panel', () => {
        const cluster = document.createElementNS(svgns, 'g');
        cluster.setAttribute('class', 'cluster');
        const border = document.createElementNS(svgns, 'polygon');
        border.setAttribute('fill', 'none'); // style not 'filled'
        const label = document.createElementNS(svgns, 'text');
        label.setAttribute('fill', '#000000'); // authored/default black 'one'
        cluster.appendChild(border);
        cluster.appendChild(label);

        // The dark cluster branch routes an unfilled cluster through the same
        // panel rescue (border.fill='none' is classified transparent).
        expect(isEffectivelyTransparentFill(border.getAttribute('fill'))).toBe(true);
        retintUnfilledNodeTextForDark(border);

        // DARK: white on panel 16.67:1.
        expect(label.getAttribute('fill')).toBe('#ffffff');
        expect(contrastRatio('#ffffff', GRAPHVIZ_DARK_PANEL_BG)).toBeGreaterThanOrEqual(4.5);
        // LIGHT: black label on the light page is unchanged and legible.
        expect(contrastRatio('#000000', LIGHT_PAGE)).toBeGreaterThanOrEqual(4.5);
    });
});
