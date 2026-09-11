/**
 * @jest-environment jsdom
 *
 * G-25c219 — drawio theme regressions D-099 / D-100, re-checked at the RENDER
 * stage (the post-render `enhanceSVGVisibility` safety net), not just the pure
 * plugin helpers.
 *
 * The plugin-level helpers (reconcileSwimlaneLabelColor / resolveFilllessFontColor)
 * already resolve the correct label colour and are covered by drawioG14/G15.
 * But EVERY drawio render then passes through enhanceSVGVisibility(), which
 * RE-MEASURES each label against the background it discovers via
 * findElementBackground() and repaints when contrast is below the floor.
 *
 * D-100 real cause: findElementBackground() read a swimlane fill's raw colour
 * (#dae8fc) and IGNORED its `fill-opacity="0.2"`. The lane fill is actually a
 * 20% composite over the themed canvas (~#50586a in dark), on which the
 * plugin's white lane-title is readable (~7:1); but measured against the opaque
 * #dae8fc the white title reads 1.3:1, so the net FLIPPED it to black — black
 * on the dark composite is ~2.7:1, invisible. That flip is the regression.
 *
 * Theme defect => BOTH themes are asserted. The default-fill vertex case (D-099)
 * is asserted as a guard: the net must NOT strand the plugin's dark label on the
 * opaque #C3D9FF default fill in either theme.
 */
import { enhanceSVGVisibility, calculateContrastRatio } from '../../../utils/colorUtils';

const SVGNS = 'http://www.w3.org/2000/svg';
const TEXT_FLOOR = 4.5;
const GRAPHICAL_FLOOR = 3.0;

// The plugin composites a 20% swimlane fill over the themed canvas. Enhancer
// pageBg is #2e3440 (dark) / #ffffff (light); mirror that here to know the real
// surface the flipped/kept colour lands on.
function composite(fillHex: string, bgHex: string, a: number): string {
    const p = (h: string) => [
        parseInt(h.slice(1, 3), 16),
        parseInt(h.slice(3, 5), 16),
        parseInt(h.slice(5, 7), 16),
    ];
    const f = p(fillHex), b = p(bgHex);
    const c = f.map((fc, i) => Math.round(fc * a + b[i] * (1 - a)));
    return '#' + c.map(v => v.toString(16).padStart(2, '0')).join('');
}
const DARK_PAGE = '#2e3440';
const LIGHT_PAGE = '#ffffff';

function makeSvg(): SVGSVGElement {
    const svg = document.createElementNS(SVGNS, 'svg');
    document.body.appendChild(svg);
    return svg as SVGSVGElement;
}

/** A maxGraph-style swimlane group: a 20%-opacity fill rect + a label <text>. */
function addSwimlane(svg: SVGElement, fill: string, titleColor: string, title: string): SVGTextElement {
    const g = document.createElementNS(SVGNS, 'g');
    const rect = document.createElementNS(SVGNS, 'rect');
    rect.setAttribute('x', '0');
    rect.setAttribute('y', '0');
    rect.setAttribute('width', '560');
    rect.setAttribute('height', '110');
    rect.setAttribute('fill', fill);
    rect.setAttribute('fill-opacity', '0.2'); // fillOpacity=20 (the swimlane branch)
    g.appendChild(rect);
    const text = document.createElementNS(SVGNS, 'text');
    text.setAttribute('fill', titleColor);
    text.textContent = title;
    g.appendChild(text);
    svg.appendChild(g);
    return text as SVGTextElement;
}

/** A styled vertex with NO author fill → maxGraph paints the #C3D9FF default. */
function addDefaultFillVertex(svg: SVGElement, labelColor: string, label: string): SVGTextElement {
    const g = document.createElementNS(SVGNS, 'g');
    const rect = document.createElementNS(SVGNS, 'rect');
    rect.setAttribute('x', '0');
    rect.setAttribute('y', '0');
    rect.setAttribute('width', '140');
    rect.setAttribute('height', '50');
    rect.setAttribute('fill', '#C3D9FF'); // maxGraph default vertex fill (opaque)
    g.appendChild(rect);
    const text = document.createElementNS(SVGNS, 'text');
    text.setAttribute('fill', labelColor);
    text.textContent = label;
    g.appendChild(text);
    svg.appendChild(g);
    return text as SVGTextElement;
}

describe('D-100 — swimlane 20% fill composite must be measured with fill-opacity', () => {
    it('DARK: the plugin white lane-title is NOT flipped to black on the composite', () => {
        const svg = makeSvg();
        const title = addSwimlane(svg, '#dae8fc', '#ffffff', 'Customer');
        enhanceSVGVisibility(svg, /* isDarkMode */ true, { textMinContrast: TEXT_FLOOR });
        const applied = title.getAttribute('fill') || '#ffffff';
        // real surface the label lands on
        const surface = composite('#dae8fc', DARK_PAGE, 0.2);
        // the net must leave a readable colour on the ACTUAL composite
        expect(calculateContrastRatio(applied, surface)).toBeGreaterThanOrEqual(GRAPHICAL_FLOOR);
        // specifically it must not have been crushed to a dark colour
        expect(applied.toLowerCase()).not.toBe('#000000');
    });

    it('LIGHT: a dark lane-title is preserved (already readable on the near-white composite)', () => {
        const svg = makeSvg();
        const title = addSwimlane(svg, '#dae8fc', '#000000', 'Customer');
        enhanceSVGVisibility(svg, /* isDarkMode */ false, { textMinContrast: TEXT_FLOOR });
        const applied = title.getAttribute('fill') || '#000000';
        const surface = composite('#dae8fc', LIGHT_PAGE, 0.2);
        expect(calculateContrastRatio(applied, surface)).toBeGreaterThanOrEqual(GRAPHICAL_FLOOR);
    });
});

describe('D-099 — default-fill (#C3D9FF) vertex label must not be stranded', () => {
    it('DARK: a dark label on the opaque default fill is kept readable (not flipped light)', () => {
        const svg = makeSvg();
        const label = addDefaultFillVertex(svg, '#000000', 'Start');
        enhanceSVGVisibility(svg, true, { textMinContrast: TEXT_FLOOR });
        const applied = label.getAttribute('fill') || '#000000';
        expect(calculateContrastRatio(applied, '#C3D9FF')).toBeGreaterThanOrEqual(TEXT_FLOOR);
    });

    it('LIGHT: the same label stays readable on the default fill', () => {
        const svg = makeSvg();
        const label = addDefaultFillVertex(svg, '#000000', 'Start');
        enhanceSVGVisibility(svg, false, { textMinContrast: TEXT_FLOOR });
        const applied = label.getAttribute('fill') || '#000000';
        expect(calculateContrastRatio(applied, '#C3D9FF')).toBeGreaterThanOrEqual(TEXT_FLOOR);
    });
});
