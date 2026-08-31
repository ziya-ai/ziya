/**
 * @jest-environment jsdom
 *
 * G-64 / D-125 regression: graphviz edge-label text lost in LIGHT mode.
 *
 * Root cause (confirmed against source): graphvizPlugin injects a theme-correct
 * edge fontcolor (`#000000` light / `#ffffff` dark, `defaultTextColor` at
 * graphvizPlugin.ts:815). The shared enhancer `enhanceSVGVisibility`
 * (frontend/src/utils/colorUtils.ts) then re-derives each label's colour from
 * `findElementBackground`, whose Strategy-1 `querySelector('rect, ellipse,
 * polygon, circle, path[fill]:not([fill="none"])')` matches the ARROWHEAD
 * `<polygon>` (filled with the dark edge colour) sitting in the same
 * `<g class="edge">`. It concludes the label is on a dark surface and, in
 * light mode, forces the label fill to white → white-on-white (~1:1). In dark
 * the forced white merely repeats the already-white injected ink (why dark
 * looked fine).
 *
 * In-scope fix: the plugin passes `skipSelectors: GRAPHVIZ_ENHANCER_SKIP_SELECTORS`
 * (`['g.edge text']`) so the enhancer leaves the (authoritative, per-theme)
 * edge-label ink untouched. colorUtils.ts itself is outside this task's writable
 * scope; the enhancer's `skipSelectors` is its documented extension point.
 *
 * DIRECTION: the "no skip" case asserts the enhancer forces white (the bug), so
 * the "skip" case (fill preserved) fails against unpatched wiring. BOTH themes
 * are asserted: light label stays dark (the broken theme, now correct) AND dark
 * label stays light (the other theme, still correct).
 */
import { enhanceSVGVisibility } from '../../../utils/colorUtils';
import { GRAPHVIZ_ENHANCER_SKIP_SELECTORS } from '../graphvizPlugin';

const SVGNS = 'http://www.w3.org/2000/svg';

/** Build a minimal graphviz-shaped edge: <g class="edge"><polygon/><text/></g>. */
function buildEdgeSvg(labelFill: string, arrowFill = '#333333') {
    const svg = document.createElementNS(SVGNS, 'svg') as unknown as SVGElement;
    const g = document.createElementNS(SVGNS, 'g');
    g.setAttribute('class', 'edge');
    const poly = document.createElementNS(SVGNS, 'polygon');
    poly.setAttribute('fill', arrowFill); // arrowhead: dark in light theme
    const text = document.createElementNS(SVGNS, 'text');
    text.setAttribute('fill', labelFill);
    text.textContent = 'edge label';
    g.appendChild(poly);
    g.appendChild(text);
    svg.appendChild(g);
    document.body.appendChild(svg as unknown as Node);
    return { svg, text };
}

afterEach(() => {
    document.body.innerHTML = '';
});

describe('D-125 edge-label skip selector', () => {
    it('targets edge-label text only (scope guard)', () => {
        // The exported skip list is exactly the edge-label selector.
        expect(GRAPHVIZ_ENHANCER_SKIP_SELECTORS).toContain('g.edge text');

        const { text } = buildEdgeSvg('#000000');
        // An edge-label <text> matches the skip selector...
        expect(GRAPHVIZ_ENHANCER_SKIP_SELECTORS.some((s) => text.matches(s))).toBe(true);

        // ...but a NODE-label <text> does not, so node text stays enhanced.
        const g = document.createElementNS(SVGNS, 'g');
        g.setAttribute('class', 'node');
        const nodeText = document.createElementNS(SVGNS, 'text');
        nodeText.textContent = 'node';
        g.appendChild(nodeText);
        document.body.appendChild(g);
        expect(GRAPHVIZ_ENHANCER_SKIP_SELECTORS.some((s) => nodeText.matches(s))).toBe(false);
    });

    it('LIGHT: without the skip, the enhancer forces the label to white (reproduces the bug)', () => {
        const { svg, text } = buildEdgeSvg('#000000');
        enhanceSVGVisibility(svg, /* isDarkMode */ false, {});
        // The dark arrowhead is mistaken for the label background -> forced white
        // on the white page. This is the defect.
        expect(text.getAttribute('fill')).toBe('#ffffff');
    });

    it('LIGHT: with the skip, the injected black label ink is preserved (fix)', () => {
        const { svg, text } = buildEdgeSvg('#000000');
        enhanceSVGVisibility(svg, /* isDarkMode */ false, {
            skipSelectors: GRAPHVIZ_ENHANCER_SKIP_SELECTORS,
        });
        // Black on white page = 21:1; the label is legible instead of vanished.
        expect(text.getAttribute('fill')).toBe('#000000');
    });

    it('DARK: with the skip, the injected white label ink is preserved (no regression)', () => {
        const { svg, text } = buildEdgeSvg('#ffffff', '#f72585');
        enhanceSVGVisibility(svg, /* isDarkMode */ true, {
            skipSelectors: GRAPHVIZ_ENHANCER_SKIP_SELECTORS,
        });
        // White on the dark page stays white — dark was already correct and stays so.
        expect(text.getAttribute('fill')).toBe('#ffffff');
    });
});
