/**
 * @jest-environment jsdom
 *
 * G-9c6f76 / D-294 (mermaid-w3-11): quadrantChart points that mermaid places at
 * (near-)identical coordinates render stacked exactly on top of one another, so
 * N circles and N labels overprint into an unreadable smear. The w3-11 spec puts
 * five points at [0.5, 0.5] (the quadrant-boundary intersection at the chart
 * centre) plus one at [0.501, 0.499]; the collision makes the labels illegible
 * in BOTH themes — a PLACEMENT defect that no colour/contrast change can fix.
 *
 * `dodgeQuadrantPointCollisions` is a post-render pass that fans each cluster of
 * colliding `g.data-point` groups onto a rosette around their shared centre,
 * sized so adjacent markers clear one another. It is theme-independent (identical
 * maths in light and dark) and a strict no-op for charts whose points are already
 * distinct.
 *
 * Without the function this test cannot even import a symbol; with it, the five
 * coincident points become pairwise-separated and the distinct point is left
 * untouched.
 */
import { dodgeQuadrantPointCollisions } from '../mermaidEnhancer';

/** Build a mermaid-11-shaped quadrant SVG: g.data-points > g.data-point(circle+text). */
function buildQuadrantSvg(points: Array<{ x: number; y: number; r?: number; label: string }>): SVGElement {
    const div = document.createElement('div');
    const circles = points
        .map(
            (p) =>
                `<g class="data-point">` +
                `<circle cx="${p.x}" cy="${p.y}" r="${p.r ?? 5}" fill="#1f77b4" stroke="#ffffff" stroke-width="1"/>` +
                `<text transform="translate(${p.x}, ${p.y})" fill="#1a1a1a">${p.label}</text>` +
                `</g>`
        )
        .join('');
    div.innerHTML = `<svg><g><g class="data-points">${circles}</g></g></svg>`;
    return div.querySelector('svg') as unknown as SVGElement;
}

/** Effective centre of a point group = its circle cx/cy plus any translate() we applied. */
function effectiveCentre(g: Element): { x: number; y: number } {
    const circle = g.querySelector('circle')!;
    let x = parseFloat(circle.getAttribute('cx') || '0');
    let y = parseFloat(circle.getAttribute('cy') || '0');
    const t = g.getAttribute('transform') || '';
    const m = t.match(/translate\(\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*\)/);
    if (m) {
        x += parseFloat(m[1]);
        y += parseFloat(m[2]);
    }
    return { x, y };
}

function minPairwiseDistance(centres: Array<{ x: number; y: number }>): number {
    let min = Infinity;
    for (let i = 0; i < centres.length; i++) {
        for (let j = i + 1; j < centres.length; j++) {
            const d = Math.hypot(centres[i].x - centres[j].x, centres[i].y - centres[j].y);
            if (d < min) min = d;
        }
    }
    return min;
}

describe('G-9c6f76 / D-294: quadrantChart colliding-point dodge (w3-11)', () => {
    const R = 5;

    it('starts fully stacked, then separates five coincident points', () => {
        // w3-11: five points at exactly the [0.5,0.5] centre (pixel 200,200).
        const svg = buildQuadrantSvg([
            { x: 200, y: 200, r: R, label: 'Alpha' },
            { x: 200, y: 200, r: R, label: 'Beta' },
            { x: 200, y: 200, r: R, label: 'Gamma' },
            { x: 200, y: 200, r: R, label: 'Epsilon' },
            { x: 200, y: 200, r: R, label: 'Zeta' },
        ]);
        const groups = Array.from(svg.querySelectorAll('g.data-point'));

        // Pre-state: every circle is at the same point → min distance 0 (a smear).
        const before = groups.map(effectiveCentre);
        expect(minPairwiseDistance(before)).toBeCloseTo(0, 5);

        const moved = dodgeQuadrantPointCollisions(svg);
        expect(moved).toBe(5);

        // Post-state: no two markers overlap — adjacent centres clear the marker
        // diameter (2*r) so each circle+label reads separately.
        const after = groups.map(effectiveCentre);
        expect(minPairwiseDistance(after)).toBeGreaterThanOrEqual(2 * R - 1e-6);
    });

    it('is theme-independent (identical result whichever theme rendered)', () => {
        const mk = () =>
            buildQuadrantSvg([
                { x: 120, y: 90, r: R, label: 'A' },
                { x: 120, y: 90, r: R, label: 'B' },
                { x: 120, y: 90, r: R, label: 'C' },
            ]);
        const svgA = mk();
        const svgB = mk();
        dodgeQuadrantPointCollisions(svgA);
        dodgeQuadrantPointCollisions(svgB);
        const ta = Array.from(svgA.querySelectorAll('g.data-point')).map((g) => g.getAttribute('transform'));
        const tb = Array.from(svgB.querySelectorAll('g.data-point')).map((g) => g.getAttribute('transform'));
        expect(ta).toEqual(tb);
        // and each transform actually moved the point
        expect(ta.every((t) => !!t && /translate/.test(t))).toBe(true);
    });

    it('also pulls apart a near-coincident sub-pixel neighbour ([0.501,0.499])', () => {
        // Delta at 200.4/199.6 sits < 1px from the central cluster; it must not be
        // left overprinting the stack.
        const svg = buildQuadrantSvg([
            { x: 200, y: 200, r: R, label: 'Alpha' },
            { x: 200, y: 200, r: R, label: 'Beta' },
            { x: 200.4, y: 199.6, r: R, label: 'Delta' },
        ]);
        const moved = dodgeQuadrantPointCollisions(svg);
        expect(moved).toBe(3);
        const after = Array.from(svg.querySelectorAll('g.data-point')).map(effectiveCentre);
        expect(minPairwiseDistance(after)).toBeGreaterThanOrEqual(2 * R - 1e-6);
    });

    it('leaves already-distinct points untouched (no-op on a well-placed chart)', () => {
        const svg = buildQuadrantSvg([
            { x: 60, y: 60, r: R, label: 'Dark mode parity' },
            { x: 300, y: 80, r: R, label: 'Bulk export' },
            { x: 40, y: 260, r: R, label: 'Tooltip polish' },
            { x: 320, y: 300, r: R, label: 'Legacy SDK shim' },
        ]);
        const moved = dodgeQuadrantPointCollisions(svg);
        expect(moved).toBe(0);
        Array.from(svg.querySelectorAll('g.data-point')).forEach((g) => {
            expect(g.getAttribute('transform')).toBeNull();
        });
    });

    it('handles a chart with fewer than two points safely', () => {
        const svg = buildQuadrantSvg([{ x: 100, y: 100, r: R, label: 'solo' }]);
        expect(dodgeQuadrantPointCollisions(svg)).toBe(0);
    });
});
