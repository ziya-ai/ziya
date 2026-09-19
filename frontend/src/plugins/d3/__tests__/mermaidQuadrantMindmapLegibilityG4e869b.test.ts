/**
 * @jest-environment jsdom
 *
 * G-4e869b / D-422 — quadrantChart point labels and mindmap link ribbons that
 * mermaid 11 paints through its embedded class palette dissolve into the theme
 * canvas in LIGHT (white point labels ~1.02:1 on the pale quadrant fill; pale
 * pastel mindmap ribbons ~1.05:1 on white). enhanceQuadrantAndMindmapLegibility
 * repaints ONLY a dissolved element, to the theme-resolved ink/outline:
 *   point-label text -> light #1a1a1a (17.40:1 on #ffffff) / dark #f5f5f5
 *   mindmap ribbon   -> light #333333 (12.63:1 on #ffffff) / dark #e6e6e6
 * An element already legible in the current theme is left untouched, so the
 * passing dark renders (light-on-dark) are byte-for-byte unchanged.
 */
import {
    enhanceQuadrantAndMindmapLegibility,
    resolveStyleColorToRgb,
    contrastRatioRgb,
} from '../mermaidEnhancer';

const SVGNS = 'http://www.w3.org/2000/svg';
const LIGHT_BG = { r: 0xff, g: 0xff, b: 0xff };
const DARK_BG = { r: 0x1e, g: 0x1e, b: 0x1e };

function svgRoot(): SVGSVGElement {
    return document.createElementNS(SVGNS, 'svg') as SVGSVGElement;
}

function addPointLabel(svg: Element, style: string): SVGTextElement {
    const g = document.createElementNS(SVGNS, 'g');
    g.setAttribute('class', 'data-point');
    const t = document.createElementNS(SVGNS, 'text') as SVGTextElement;
    t.setAttribute('style', style);
    t.textContent = 'Feature';
    g.appendChild(t);
    svg.appendChild(g);
    return t;
}

function addRibbon(svg: Element, style: string): SVGPathElement {
    const g = document.createElementNS(SVGNS, 'g');
    g.setAttribute('class', 'mindmap-edges');
    const p = document.createElementNS(SVGNS, 'path') as SVGPathElement;
    p.setAttribute('style', style);
    p.setAttribute('d', 'M0 0 L10 10');
    g.appendChild(p);
    svg.appendChild(g);
    return p;
}

function effFill(el: Element): string {
    const m = (el.getAttribute('style') || '').match(/fill\s*:\s*([^;!]+)/i);
    if (m) return m[1].trim();
    return (el.getAttribute('fill') || '').trim();
}
function effStroke(el: Element): string {
    const m = (el.getAttribute('style') || '').match(/stroke\s*:\s*([^;!]+)/i);
    if (m) return m[1].trim();
    return (el.getAttribute('stroke') || '').trim();
}
function ratio(hex: string, bg: { r: number; g: number; b: number }): number {
    const rgb = resolveStyleColorToRgb(hex);
    expect(rgb).not.toBeNull();
    return contrastRatioRgb(rgb!, bg);
}

describe('G-4e869b / D-422 quadrant + mindmap legibility (LIGHT)', () => {
    it('repaints a white quadrant point label to dark ink clearing the 4.5:1 text floor on white', () => {
        const svg = svgRoot();
        const label = addPointLabel(svg, 'fill:#ffffff;'); // white on white ~1:1
        // FAILS WITHOUT THE FIX: the pale label is untouched and stays illegible.
        const n = enhanceQuadrantAndMindmapLegibility(svg, /*isDarkMode*/ false);
        expect(n).toBe(1);
        expect(effFill(label)).toBe('#1a1a1a');
        expect(ratio(effFill(label), LIGHT_BG)).toBeGreaterThanOrEqual(4.5);
    });

    it('repaints a pale pastel mindmap ribbon stroke to the theme outline clearing 3:1 on white', () => {
        const svg = svgRoot();
        const ribbon = addRibbon(svg, 'stroke:#ffff99;stroke-width:6px;'); // ~1.07:1
        expect(ratio('#ffff99', LIGHT_BG)).toBeLessThan(3.0); // precondition: dissolved
        const n = enhanceQuadrantAndMindmapLegibility(svg, false);
        expect(n).toBe(1);
        expect(effStroke(ribbon)).toBe('#333333');
        expect(ratio(effStroke(ribbon), LIGHT_BG)).toBeGreaterThanOrEqual(3.0);
        // Preserves the ribbon's wider (magnitude-encoding) stroke width.
        expect((ribbon.getAttribute('style') || '')).toMatch(/stroke-width:6px/);
    });

    it('leaves an already-legible dark label and dark-visible ribbon untouched (regression guard)', () => {
        const svg = svgRoot();
        const okLabel = addPointLabel(svg, 'fill:#1a1a1a;');   // 17.4:1 on white
        const okRibbon = addRibbon(svg, 'stroke:#333333;');    // 12.63:1 on white
        const n = enhanceQuadrantAndMindmapLegibility(svg, false);
        expect(n).toBe(0);
        expect(effFill(okLabel)).toBe('#1a1a1a');
        expect(effStroke(okRibbon)).toBe('#333333');
    });

    it('DARK: a white point label / pale-but-dark-visible ribbon that already passes stays unchanged', () => {
        const svg = svgRoot();
        // In dark the point label is white -> 15.9:1 on #1e1e1e, already legible.
        const label = addPointLabel(svg, 'fill:#ffffff;');
        expect(ratio('#ffffff', DARK_BG)).toBeGreaterThanOrEqual(4.5);
        // A ribbon that reads on dark (pale) is above the 3:1 graphic floor there.
        const ribbon = addRibbon(svg, 'stroke:#ffff99;');
        expect(ratio('#ffff99', DARK_BG)).toBeGreaterThanOrEqual(3.0);
        const n = enhanceQuadrantAndMindmapLegibility(svg, /*isDarkMode*/ true);
        expect(n).toBe(0);
        expect(effFill(label)).toBe('#ffffff');
        expect(effStroke(ribbon)).toBe('#ffff99');
    });

    it('DARK: repaints a point label that dissolves into the dark canvas to light ink', () => {
        const svg = svgRoot();
        const label = addPointLabel(svg, 'fill:#222222;'); // ~1.1:1 on #1e1e1e
        const n = enhanceQuadrantAndMindmapLegibility(svg, true);
        expect(n).toBe(1);
        expect(effFill(label)).toBe('#f5f5f5');
        expect(ratio(effFill(label), DARK_BG)).toBeGreaterThanOrEqual(4.5);
    });
});
