/**
 * @jest-environment jsdom
 *
 * G-e3e70f regression tests for the graphviz plugin. Three THEME defects, all in
 * graphvizPlugin.ts:
 *   D-286  `strict graph` / `strict digraph` headers bypassed theme injection
 *          (the header regex did not admit the `strict` keyword) — w3-14.
 *   D-285  a hardcoded LIGHT graph background (bgcolor="#ffffff") was darkened
 *          to a mid-grey slab (#666666) on which the panel-tuned dark palette
 *          fell below the stroke floor (pink edges 1.52:1) — w3-15.
 *   D-284  an UNFILLED cluster's label stayed black on the ~#1e1e1e panel; the
 *          fill-gated dark loop only re-themed FILLED clusters — w3-07.
 *
 * Each assertion is a DIRECTION check: the pre-fix constant/behaviour is asserted
 * so the test would FAIL against the unpatched module (the new exports did not
 * exist pre-fix, so the import itself throws). Theme defects assert BOTH themes.
 */
import {
    GRAPHVIZ_GRAPH_HEADER_RE,
    GRAPHVIZ_ENHANCER_SKIP_SELECTORS,
    GRAPHVIZ_DARK_PANEL_BG,
    GRAPHVIZ_LIGHT_PAGE_BG,
    neutralizeGraphBackgroundForDark,
    retintStrandedClusterLabels,
} from '../graphvizPlugin';
import { contrastRatio } from '../chartTheme';

const svgns = 'http://www.w3.org/2000/svg';

// The exact pre-fix header regex — used only to document the direction.
const OLD_HEADER_RE = /^(\s*(?:di)?graph\s+[^{]*{)/;

// ---------------------------------------------------------------------------
// D-286  strict-keyword header must be admitted for theme injection
// ---------------------------------------------------------------------------
describe('D-286 theme-injection header regex admits the strict keyword', () => {
    it('matches strict graph / strict digraph as well as plain headers', () => {
        expect(GRAPHVIZ_GRAPH_HEADER_RE.test('strict graph FD {\n a; }')).toBe(true);
        expect(GRAPHVIZ_GRAPH_HEADER_RE.test('strict digraph D {\n a->b; }')).toBe(true);
        expect(GRAPHVIZ_GRAPH_HEADER_RE.test('digraph E {\n a; }')).toBe(true);
        expect(GRAPHVIZ_GRAPH_HEADER_RE.test('graph G {\n a; }')).toBe(true);
    });

    it('direction: the pre-fix regex did NOT match strict headers', () => {
        expect(OLD_HEADER_RE.test('strict graph FD {')).toBe(false);
        expect(OLD_HEADER_RE.test('strict digraph D {')).toBe(false);
        // but it did match plain ones (so only the strict case regressed)
        expect(OLD_HEADER_RE.test('digraph E {')).toBe(true);
    });

    it('injection replace() targets the whole strict header once', () => {
        const dot = 'strict graph FD {\n r1 -- r2;\n}';
        const injected = dot.replace(GRAPHVIZ_GRAPH_HEADER_RE, '$1\n bgcolor="transparent";');
        expect(injected).toContain('strict graph FD {\n bgcolor="transparent";');
    });
});

// ---------------------------------------------------------------------------
// D-285  hardcoded light graph background -> panel, not a mid-grey slab
// ---------------------------------------------------------------------------
describe('D-285 neutralize a hardcoded light graph background in dark', () => {
    const makeGraphBg = (fill: string) => {
        const g = document.createElementNS(svgns, 'g');
        g.setAttribute('class', 'graph');
        const poly = document.createElementNS(svgns, 'polygon');
        poly.setAttribute('fill', fill);
        poly.setAttribute('stroke', 'none');
        g.appendChild(poly);
        return { g, poly };
    };

    it('paints an authored white canvas transparent in dark so the palette holds', () => {
        const { poly } = makeGraphBg('#ffffff');
        // Direction (pre-fix): darkened to the mid-grey slab #666666, on which
        // the pink dark edge colour fails the 3:1 stroke floor.
        expect(contrastRatio('#f72585', '#666666')).toBeLessThan(3);

        expect(neutralizeGraphBackgroundForDark(poly, true)).toBe(true);
        expect(poly.getAttribute('fill')).toBe('transparent');
        expect(poly.getAttribute('data-original-fill')).toBe('#ffffff');

        // On the panel the palette clears its floors in DARK.
        expect(contrastRatio('#f72585', GRAPHVIZ_DARK_PANEL_BG)).toBeGreaterThanOrEqual(3); // pink edges 4.41
        expect(contrastRatio('#4cc9f0', GRAPHVIZ_DARK_PANEL_BG)).toBeGreaterThanOrEqual(3); // cyan borders 8.67
        expect(contrastRatio('#ffffff', GRAPHVIZ_DARK_PANEL_BG)).toBeGreaterThanOrEqual(4.5); // white label 16.67
    });

    it('leaves the authored white canvas untouched in LIGHT (page is white)', () => {
        const { poly } = makeGraphBg('#ffffff');
        expect(neutralizeGraphBackgroundForDark(poly, false)).toBe(false);
        expect(poly.getAttribute('fill')).toBe('#ffffff');
        // #333333 authored text on the white canvas is legible on the light page.
        expect(contrastRatio('#333333', GRAPHVIZ_LIGHT_PAGE_BG)).toBeGreaterThanOrEqual(4.5);
    });

    it('does not touch a none/transparent or already-dark background', () => {
        const { poly: none } = makeGraphBg('none');
        expect(neutralizeGraphBackgroundForDark(none, true)).toBe(false);
        const { poly: dark } = makeGraphBg('#101010');
        expect(neutralizeGraphBackgroundForDark(dark, true)).toBe(false);
        expect(dark.getAttribute('fill')).toBe('#101010');
    });

    it('only acts on a polygon that is a direct child of g.graph', () => {
        const g = document.createElementNS(svgns, 'g');
        g.setAttribute('class', 'node');
        const poly = document.createElementNS(svgns, 'polygon');
        poly.setAttribute('fill', '#ffffff');
        g.appendChild(poly);
        expect(neutralizeGraphBackgroundForDark(poly, true)).toBe(false);
        expect(poly.getAttribute('fill')).toBe('#ffffff'); // node fill handled elsewhere
    });
});

// ---------------------------------------------------------------------------
// D-284  unfilled cluster label rescue (both themes) + enhancer skip
// ---------------------------------------------------------------------------
describe('D-284 rescue an unfilled cluster label; filled clusters untouched', () => {
    const makeCluster = (fill: string, textFill: string) => {
        const root = document.createElementNS(svgns, 'g');
        const cluster = document.createElementNS(svgns, 'g');
        cluster.setAttribute('class', 'cluster');
        const border = document.createElementNS(svgns, 'polygon');
        border.setAttribute('fill', fill);
        const label = document.createElementNS(svgns, 'text');
        label.setAttribute('fill', textFill);
        label.textContent = 'one';
        cluster.appendChild(border);
        cluster.appendChild(label);
        root.appendChild(cluster);
        return { root, label };
    };

    it('whitens a black label on an UNFILLED cluster in dark', () => {
        const { root, label } = makeCluster('none', '#000000');
        // Direction: black on the panel is ~1.26 and unreadable.
        expect(contrastRatio('#000000', GRAPHVIZ_DARK_PANEL_BG)).toBeLessThan(1.5);
        const fixed = retintStrandedClusterLabels(root, true);
        expect(fixed).toBe(1);
        expect(label.getAttribute('fill')).toBe('#ffffff');
        expect(contrastRatio('#ffffff', GRAPHVIZ_DARK_PANEL_BG)).toBeGreaterThanOrEqual(4.5); // 16.67
    });

    it('LIGHT: a black label on an unfilled cluster stays black on the white page', () => {
        const { root, label } = makeCluster('none', '#000000');
        const fixed = retintStrandedClusterLabels(root, false);
        expect(fixed).toBe(0); // black on white already clears the floor
        expect(label.getAttribute('fill')).toBe('#000000');
        expect(contrastRatio('#000000', GRAPHVIZ_LIGHT_PAGE_BG)).toBeGreaterThanOrEqual(4.5); // 21
    });

    it('leaves a FILLED cluster label alone (handled by the fill loop)', () => {
        // A dark authored fill with its own (already legible) white label; the
        // sweep must not touch a filled cluster (its label sits on the fill).
        const { root, label } = makeCluster('#1a1a2e', '#000000');
        const fixed = retintStrandedClusterLabels(root, true);
        expect(fixed).toBe(0);
        expect(label.getAttribute('fill')).toBe('#000000');
    });

    it('the enhancer is told to skip cluster label text (as it does edge text)', () => {
        expect(GRAPHVIZ_ENHANCER_SKIP_SELECTORS).toContain('g.cluster text');
        expect(GRAPHVIZ_ENHANCER_SKIP_SELECTORS).toContain('g.edge text');
        const t = document.createElementNS(svgns, 'text');
        const cg = document.createElementNS(svgns, 'g');
        cg.setAttribute('class', 'cluster');
        cg.appendChild(t);
        expect(GRAPHVIZ_ENHANCER_SKIP_SELECTORS.some((s) => t.matches(s))).toBe(true);
    });
});
