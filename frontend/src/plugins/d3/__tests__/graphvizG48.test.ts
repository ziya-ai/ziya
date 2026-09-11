/**
 * @jest-environment jsdom
 *
 * G-48 / D-053: three graphviz theme sub-defects, one test file.
 *
 *  (a) graphviz-w1-13 (gradient, HIGH): a node fill resolving to `url(#grad)`
 *      cannot be parsed by the contrast math, so `findElementBackground` handed
 *      `url(#..)` to `getOptimalTextColor`, which FAILS OPEN to white and
 *      overrode the authored dark fontcolor -> black label forced white over a
 *      LIGHT gradient. Fix: `resolveGradientPaint` averages the gradient stops
 *      so the enhancer measures against the real (light) surface and keeps the
 *      authored black.
 *
 *  (b) graphviz-w2-05 (cluster border, LIGHT): the cluster polygon stroke was
 *      re-applied only inside the `if (isDarkMode)` branch, so an authored
 *      color==fill cluster (#d3d3d3 border on #d3d3d3 fill, 1.0:1) stayed
 *      border-less in light and 12 nested clusters collapsed into one flat box.
 *      Fix: `restrokeInvisibleClusterBorder` mirrors the dark re-stroke in light
 *      when the border fails the 3:1 graphical floor against its own fill.
 *
 *  (c) graphviz-w1-09 (edge fontcolor, DARK): D-125's blanket edge-label skip
 *      (kept, so the light white-on-white misfire stays fixed) also stopped the
 *      enhancer rescuing an AUTHORED dark edge fontcolor stranded on the dark
 *      panel (#1f4e79 = 1.92:1, #7f1d1d = 1.66:1). Fix:
 *      `retintStrandedEdgeLabels` re-themes an edge label measured BELOW the
 *      4.5:1 text floor against the effective PAGE background (not a sibling
 *      arrowhead), leaving theme-correct/injected labels untouched.
 *
 * DIRECTION: every "fixed" assertion is paired with the pre-fix behaviour it
 * overturns (documented inline); the three new symbols do not exist on the
 * unpatched tree, so this suite is RED before the change. Theme defect =>
 * BOTH themes asserted for (b)/(c).
 */
import {
    findElementBackground,
    resolveGradientPaint,
    enhanceSVGVisibility,
    calculateContrastRatio,
} from '../../../utils/colorUtils';
import {
    restrokeInvisibleClusterBorder,
    retintStrandedEdgeLabels,
    GRAPHVIZ_LIGHT_CLUSTER_BORDER,
} from '../graphvizPlugin';

const SVGNS = 'http://www.w3.org/2000/svg';

function el(tag: string, attrs: Record<string, string> = {}) {
    const e = document.createElementNS(SVGNS, tag);
    for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
    return e;
}

afterEach(() => { document.body.innerHTML = ''; });

/* ------------------------------------------------------------------ (a) */
describe('D-053(a) gradient fill resolution (graphviz-w1-13)', () => {
    function buildGradientNode(stops: string[], textFill: string) {
        const svg = el('svg') as unknown as SVGElement;
        const defs = el('defs');
        const grad = el('linearGradient', { id: 'node_l_0' });
        for (const s of stops) grad.appendChild(el('stop', { 'stop-color': s }));
        defs.appendChild(grad);
        svg.appendChild(defs);
        const g = el('g', { class: 'node' });
        g.appendChild(el('ellipse', { fill: 'url(#node_l_0)' }));
        const text = el('text', { fill: textFill });
        text.textContent = 'gradient label';
        g.appendChild(text);
        svg.appendChild(g);
        document.body.appendChild(svg as unknown as Node);
        return { svg, text };
    }

    it('resolveGradientPaint averages stops to a solid hex (not url())', () => {
        const { text } = buildGradientNode(['#ffffcc', '#a7f3d0', '#ffffff'], '#000000');
        const resolved = resolveGradientPaint('url(#node_l_0)', text);
        expect(resolved).toMatch(/^#[0-9a-f]{6}$/i);
        expect(resolved).not.toContain('url(');
        // average of the three LIGHT stops is itself light -> black text is legible.
        expect(calculateContrastRatio('#000000', resolved as string)).toBeGreaterThan(4.5);
    });

    it('findElementBackground returns the resolved surface, not the paint ref', () => {
        const { text } = buildGradientNode(['#ffffcc', '#a7f3d0'], '#000000');
        const bg = findElementBackground(text, '#ffffff');
        // pre-fix this returned 'url(#node_l_0)'
        expect(bg).not.toContain('url(');
        expect(bg).toMatch(/^#[0-9a-f]{6}$/i);
    });

    it('enhancer keeps the authored black label over a light gradient (was forced white)', () => {
        const { svg, text } = buildGradientNode(['#ffffcc', '#a7f3d0', '#ffffff'], '#000000');
        enhanceSVGVisibility(svg, /* isDarkMode */ false, {});
        // PRE-FIX: bg='url(..)' -> getOptimalTextColor -> '#ffffff' forced (invisible).
        // POST-FIX: bg resolves light -> high contrast -> black preserved.
        expect(text.getAttribute('fill')).toBe('#000000');
    });

    it('an unresolvable url() reference does NOT resolve to a colour (fail to page bg, not white)', () => {
        const { text } = buildGradientNode(['#ffffcc'], '#000000');
        expect(resolveGradientPaint('url(#does_not_exist)', text)).toBeNull();
        // a non-url value is returned unchanged (no false positives)
        expect(resolveGradientPaint('#123456', text)).toBe('#123456');
        expect(resolveGradientPaint('lightgrey', text)).toBe('lightgrey');
    });
});

/* ------------------------------------------------------------------ (b) */
describe('D-053(b) light cluster border re-stroke (graphviz-w2-05)', () => {
    it('LIGHT: re-strokes an authored color==fill cluster (invisible border)', () => {
        const poly = el('polygon', { fill: '#d3d3d3', stroke: '#d3d3d3' });
        const changed = restrokeInvisibleClusterBorder(poly, GRAPHVIZ_LIGHT_CLUSTER_BORDER);
        expect(changed).toBe(true);
        expect(poly.getAttribute('stroke')).toBe(GRAPHVIZ_LIGHT_CLUSTER_BORDER);
        // border now clears the 3:1 graphical floor against its own fill
        expect(calculateContrastRatio(GRAPHVIZ_LIGHT_CLUSTER_BORDER, '#d3d3d3'))
            .toBeGreaterThanOrEqual(3);
    });

    it('LIGHT: leaves a cluster that already has a legible border untouched (no needless repaint)', () => {
        const poly = el('polygon', { fill: '#f0f0f0', stroke: '#333333' });
        const changed = restrokeInvisibleClusterBorder(poly, GRAPHVIZ_LIGHT_CLUSTER_BORDER);
        expect(changed).toBe(false);
        expect(poly.getAttribute('stroke')).toBe('#333333');
    });
});

/* ------------------------------------------------------------------ (c) */
describe('D-053(c) stranded authored edge fontcolor (graphviz-w1-09)', () => {
    function buildEdge(labelFill: string) {
        const svg = el('svg') as unknown as SVGElement;
        const g = el('g', { class: 'edge' });
        g.appendChild(el('polygon', { fill: '#f72585' })); // arrowhead
        const text = el('text', { fill: labelFill });
        text.textContent = 'edge label';
        g.appendChild(text);
        svg.appendChild(g);
        document.body.appendChild(svg as unknown as Node);
        return { svg, text };
    }

    it('DARK: rescues an authored dark edge fontcolor stranded on the panel', () => {
        const { svg, text } = buildEdge('#1f4e79'); // 1.92:1 on #1e1e1e
        const n = retintStrandedEdgeLabels(svg, /* isDarkMode */ true);
        expect(n).toBe(1);
        expect(text.getAttribute('fill')).toBe('#ffffff');
        expect(calculateContrastRatio('#ffffff', '#1e1e1e')).toBeGreaterThan(4.5);
    });

    it('DARK: leaves an already-light (theme-correct) edge label untouched', () => {
        const { svg, text } = buildEdge('#ffffff');
        const n = retintStrandedEdgeLabels(svg, /* isDarkMode */ true);
        expect(n).toBe(0);
        expect(text.getAttribute('fill')).toBe('#ffffff');
    });

    it('LIGHT: leaves an authored black edge label untouched (still 21:1 on the page)', () => {
        const { svg, text } = buildEdge('#000000');
        const n = retintStrandedEdgeLabels(svg, /* isDarkMode */ false);
        expect(n).toBe(0);
        expect(text.getAttribute('fill')).toBe('#000000');
    });

    it('LIGHT: fixes an authored light (illegible) edge label to black', () => {
        const { svg, text } = buildEdge('#ffff00'); // ~1.07:1 on white
        const n = retintStrandedEdgeLabels(svg, /* isDarkMode */ false);
        expect(n).toBe(1);
        expect(text.getAttribute('fill')).toBe('#000000');
    });
});
