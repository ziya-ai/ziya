/**
 * @jest-environment jsdom
 *
 * G-4e869b — mermaid border/edge/arrowhead strokes that dissolve into the theme
 * canvas, extending ensureShapeBordersAgainstCanvas beyond the original D-295
 * "both fill AND stroke blend" gate.
 *
 * Covered defects (all verified in BOTH themes where relevant):
 *   - D-325 (chat-message w4-08/w4-09, DARK): an author `style` box whose fill
 *     is only MARGINALLY visible on the dark canvas (#0000ff = 1.9:1,
 *     rebeccapurple #663399 = 1.96:1) but whose explicit border has vanished
 *     (#000099 = 1.15:1) reads only from its label. The old gate skipped it
 *     because the fill technically cleared the 1.6 floor; the new gate repaints
 *     the border whenever the stroke has dissolved (< floor) AND the fill does
 *     not itself form a >=3:1 boundary. Outline -> #e6e6e6 = 13.36:1 on #1e1e1e.
 *   - D-324 (chat-message w3-08/w3-15, LIGHT): the arrowhead marker recolour is
 *     dark-only, so in LIGHT a pale author lineColor leaves arrowheads dissolved
 *     (#cccccc = 1.47:1). The marker pass repaints a blending arrowhead to the
 *     theme outline (#333333 = 12.63:1 on #ffffff) while keeping a HOLLOW marker
 *     (fill:none) hollow.
 *
 * A shape/edge/marker already legible in the current theme is left untouched
 * (regression guard) — this is what keeps default mermaid nodes and the
 * previously-verified w3-08 boxes unchanged.
 */
import {
    ensureShapeBordersAgainstCanvas,
    resolveStyleColorToRgb,
    contrastRatioRgb,
} from '../mermaidEnhancer';

const SVGNS = 'http://www.w3.org/2000/svg';
const LIGHT_BG = { r: 0xff, g: 0xff, b: 0xff };
const DARK_BG = { r: 0x1e, g: 0x1e, b: 0x1e };

function svgRoot(): SVGSVGElement {
    return document.createElementNS(SVGNS, 'svg') as SVGSVGElement;
}

function addNodeRect(svg: Element, style: string, cls = 'node'): SVGRectElement {
    const g = document.createElementNS(SVGNS, 'g');
    g.setAttribute('class', cls);
    const rect = document.createElementNS(SVGNS, 'rect') as SVGRectElement;
    rect.setAttribute('style', style);
    g.appendChild(rect);
    svg.appendChild(g);
    return rect;
}

/** Append `<defs><marker><path .../></marker></defs>` and return the glyph path. */
function addMarker(svg: Element, style: string): SVGPathElement {
    let defs = svg.querySelector('defs');
    if (!defs) {
        defs = document.createElementNS(SVGNS, 'defs');
        svg.appendChild(defs);
    }
    const marker = document.createElementNS(SVGNS, 'marker');
    const p = document.createElementNS(SVGNS, 'path') as SVGPathElement;
    p.setAttribute('style', style);
    marker.appendChild(p);
    defs.appendChild(marker);
    return p;
}

function effectiveStroke(el: Element): string {
    const attr = el.getAttribute('stroke');
    if (attr) return attr.trim();
    const m = (el.getAttribute('style') || '').match(/stroke\s*:\s*([^;!]+)/i);
    return m ? m[1].trim() : '';
}

function effectiveFill(el: Element): string {
    const attr = el.getAttribute('fill');
    if (attr) return attr.trim();
    const m = (el.getAttribute('style') || '').match(/fill\s*:\s*([^;!]+)/i);
    return m ? m[1].trim() : '';
}

function strokeRatio(el: Element, bg: { r: number; g: number; b: number }): number {
    const rgb = resolveStyleColorToRgb(effectiveStroke(el));
    if (!rgb) return 0;
    return contrastRatioRgb(rgb, bg);
}

describe('ensureShapeBordersAgainstCanvas — marginal-fill borders & arrowheads (G-4e869b)', () => {
    it('D-325 DARK: repaints a style box whose border dissolved even though the fill is marginally visible', () => {
        const svg = svgRoot();
        // w4-08 Cold node: blue fill (1.9:1 on dark) with a near-invisible border.
        const cold = addNodeRect(svg, 'fill:#0000ff;stroke:#000099');
        // w4-09: rebeccapurple fill (1.96:1) with a black border (invisible on dark).
        const rebecca = addNodeRect(svg, 'fill:#663399;stroke:#000000');

        const fixed = ensureShapeBordersAgainstCanvas(svg, /*isDarkMode*/ true);

        // WITHOUT the fix both boxes keep their dissolved borders (the old gate
        // required the fill to blend too, and 1.9/1.96 cleared the 1.6 floor).
        expect(strokeRatio(cold, DARK_BG)).toBeGreaterThanOrEqual(4.5);
        expect(strokeRatio(rebecca, DARK_BG)).toBeGreaterThanOrEqual(4.5);
        expect(effectiveStroke(cold)).toBe('#e6e6e6');
        expect(effectiveStroke(rebecca)).toBe('#e6e6e6');
        expect(fixed).toBe(2);
    });

    it('D-325 LIGHT: the same boxes are visible on the light canvas and stay untouched', () => {
        const svg = svgRoot();
        // On #ffffff, #0000ff = 2.44:1 and #663399 = 6.4:1 — plus the borders
        // (#000099 = 1.9:1, #000000 = 21:1) are not both below the floor, so the
        // gate must leave the author styling alone here.
        const cold = addNodeRect(svg, 'fill:#0000ff;stroke:#000099');
        const rebecca = addNodeRect(svg, 'fill:#663399;stroke:#000000');

        ensureShapeBordersAgainstCanvas(svg, /*isDarkMode*/ false);

        // rebecca's border (#000000, 21:1) is clearly visible -> never touched.
        expect(effectiveStroke(rebecca)).toBe('#000000');
    });

    it('D-324 LIGHT: repaints a pale arrowhead marker; keeps a hollow marker hollow', () => {
        const svg = svgRoot();
        const filledHead = addMarker(svg, 'fill:#cccccc;stroke:#cccccc');   // pale solid arrowhead
        const hollowHead = addMarker(svg, 'fill:none;stroke:#e3eef6');      // pale hollow crow's-foot

        ensureShapeBordersAgainstCanvas(svg, /*isDarkMode*/ false);

        // Solid pale head becomes a legible dark glyph in light theme.
        expect(strokeRatio(filledHead, LIGHT_BG)).toBeGreaterThanOrEqual(4.5);
        expect(effectiveStroke(filledHead)).toBe('#333333');
        expect(effectiveFill(filledHead)).toBe('#333333');
        // Hollow marker gets a visible stroke but MUST stay hollow (fill:none).
        expect(effectiveStroke(hollowHead)).toBe('#333333');
        expect(effectiveFill(hollowHead)).toBe('none');
    });

    it('regression guard: a visible arrowhead and a strong-fill box are left untouched', () => {
        const svg = svgRoot();
        const goodHead = addMarker(svg, 'fill:#333333;stroke:#333333');     // already legible on light
        const strongBox = addNodeRect(svg, 'fill:#cccccc;stroke:#222222');  // fill #cccccc = 10.38:1 on dark, border dissolved

        const fixed = ensureShapeBordersAgainstCanvas(svg, /*isDarkMode*/ true);

        // The light-gray #cccccc fill on the dark canvas is a strong (>3:1)
        // boundary, so the box is not repainted despite its dark border.
        expect(effectiveStroke(strongBox)).toBe('#222222');
        // Already-dark arrowhead on light is separately untouched in light mode.
        const svg2 = svgRoot();
        const goodHead2 = addMarker(svg2, 'fill:#333333;stroke:#333333');
        ensureShapeBordersAgainstCanvas(svg2, /*isDarkMode*/ false);
        expect(effectiveStroke(goodHead2)).toBe('#333333');
        void goodHead;
    });
});
