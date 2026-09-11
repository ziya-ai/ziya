/**
 * @jest-environment jsdom
 *
 * G-632224 / D-295 — mermaid box/edge outlines that dissolve into the theme
 * canvas.
 *
 * Two spec families:
 *   - w3-08 (block-beta): `style a fill:#f5f5f5,stroke:#dddddd` merges box `a`
 *     into the LIGHT canvas (fill 1.09:1, stroke 1.36:1); `style e
 *     fill:#1a1a1a,stroke:#000000` merges box `e` into the DARK canvas
 *     (1.04:1 / 1.26:1). Each theme loses exactly one box outline.
 *   - w3-13 (init palette): forces #ffffff node fills + #f8f8f8 lines
 *     regardless of theme, so on the LIGHT surface every shape/edge sits at
 *     1.0-1.06:1 (invisible).
 *
 * `ensureShapeBordersAgainstCanvas` must repaint ONLY a dissolved shape, with a
 * THEME-RESOLVED outline (light -> #333333 = 12.63:1 on #ffffff, dark ->
 * #e6e6e6 = 13.36:1 on #1e1e1e), and leave a shape that is already visible in
 * the current theme untouched. Theme defect => BOTH themes asserted.
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

/** Append `<g class=cls><rect style=...></g>` and return the rect. */
function addNodeRect(svg: Element, style: string, cls = 'node'): SVGRectElement {
    const g = document.createElementNS(SVGNS, 'g');
    g.setAttribute('class', cls);
    const rect = document.createElementNS(SVGNS, 'rect') as SVGRectElement;
    rect.setAttribute('style', style);
    g.appendChild(rect);
    svg.appendChild(g);
    return rect;
}

function addEdge(svg: Element, style: string): SVGPathElement {
    const g = document.createElementNS(SVGNS, 'g');
    g.setAttribute('class', 'edgePath');
    const p = document.createElementNS(SVGNS, 'path') as SVGPathElement;
    p.setAttribute('style', style);
    g.appendChild(p);
    svg.appendChild(g);
    return p;
}

/** Effective stroke: the forced `stroke` attribute wins, else the style prop. */
function effectiveStroke(el: Element): string {
    const attr = el.getAttribute('stroke');
    if (attr) return attr.trim();
    const m = (el.getAttribute('style') || '').match(/stroke\s*:\s*([^;!]+)/i);
    return m ? m[1].trim() : '';
}

function strokeRatio(el: Element, bg: { r: number; g: number; b: number }): number {
    const rgb = resolveStyleColorToRgb(effectiveStroke(el));
    if (!rgb) return 0;
    return contrastRatioRgb(rgb, bg);
}

describe('ensureShapeBordersAgainstCanvas (G-632224 / D-295)', () => {
    it('w3-08 LIGHT: repaints the light-matching box, leaves the dark-matching box alone', () => {
        const svg = svgRoot();
        const a = addNodeRect(svg, 'fill:#f5f5f5;stroke:#dddddd');   // merges into light canvas
        const e = addNodeRect(svg, 'fill:#1a1a1a;stroke:#000000');   // visible on light canvas

        const fixed = ensureShapeBordersAgainstCanvas(svg, /*isDarkMode*/ false);

        // WITHOUT the fix the dissolved box `a` keeps stroke #dddddd (1.36:1) —
        // this assertion is what fails on the unpatched build.
        expect(strokeRatio(a, LIGHT_BG)).toBeGreaterThanOrEqual(4.5);
        expect(effectiveStroke(a)).toBe('#333333');
        // The dark-matching box is visible in light theme and must be untouched.
        expect(effectiveStroke(e)).toBe('#000000');
        expect(fixed).toBe(1);
    });

    it('w3-08 DARK: repaints the dark-matching box, leaves the light-matching box alone', () => {
        const svg = svgRoot();
        const a = addNodeRect(svg, 'fill:#f5f5f5;stroke:#dddddd');   // visible on dark canvas
        const e = addNodeRect(svg, 'fill:#1a1a1a;stroke:#000000');   // merges into dark canvas

        const fixed = ensureShapeBordersAgainstCanvas(svg, /*isDarkMode*/ true);

        expect(strokeRatio(e, DARK_BG)).toBeGreaterThanOrEqual(4.5);
        expect(effectiveStroke(e)).toBe('#e6e6e6');
        expect(effectiveStroke(a)).toBe('#dddddd'); // untouched (visible on dark)
        expect(fixed).toBe(1);
    });

    it('w3-13 LIGHT: repaints white-on-white node fills and near-white edge strokes', () => {
        const svg = svgRoot();
        const node = addNodeRect(svg, 'fill:#ffffff;stroke:#fefefe'); // both ~1.0:1 on white
        const edge = addEdge(svg, 'stroke:#f8f8f8;fill:none');        // 1.06:1 on white

        ensureShapeBordersAgainstCanvas(svg, /*isDarkMode*/ false);

        expect(strokeRatio(node, LIGHT_BG)).toBeGreaterThanOrEqual(4.5);
        expect(strokeRatio(edge, LIGHT_BG)).toBeGreaterThanOrEqual(4.5);
    });

    it('does NOT touch a box or edge already visible in the current theme', () => {
        const svg = svgRoot();
        const node = addNodeRect(svg, 'fill:#ececff;stroke:#9370db'); // default light node, visible border
        const edge = addEdge(svg, 'stroke:#333333;fill:none');        // default light edge (12.6:1)

        const fixed = ensureShapeBordersAgainstCanvas(svg, /*isDarkMode*/ false);

        expect(effectiveStroke(node)).toBe('#9370db');
        expect(effectiveStroke(edge)).toBe('#333333');
        expect(fixed).toBe(0);
    });
});
