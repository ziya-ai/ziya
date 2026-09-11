/**
 * @jest-environment jsdom
 *
 * D-283 / graphviz-w3-10 regression test for the graphviz plugin.
 *
 * A MID-luminance SOLID fill — the depth-4 nested-cluster fill `#989898`
 * (sRGB relative luminance 0.314) — sits UNDER `isLightBackground`'s 0.4
 * cutoff, so the dark cluster/node loops judge it "already dark" and KEEP it
 * verbatim: neither the darken branch (light fill) nor the panel-rescue branch
 * (transparent fill) runs, and the authored `#ffffff` label is left stranded on
 * it at 2.885:1 — below the 4.5 text floor — while the sibling lighter fills
 * darken and re-tint correctly.
 *
 * `retintTextForKeptFill` closes that gap: because the kept fill is IDENTICAL in
 * both themes, it measures the paired label against the fill itself and, only
 * when it fails the floor, repaints with the genuinely max-contrast of
 * black/white. The choice is therefore correct on BOTH backgrounds.
 *
 * Direction: `retintTextForKeptFill` did not exist before this fix (so the
 * import throws against the unpatched module), and the assertions encode the
 * pre-fix state (white kept at 2.885:1) that the fix must overturn — in BOTH
 * themes.
 */
import {
    retintTextForKeptFill,
} from '../graphvizPlugin';
import { calculateContrastRatio } from '../../../utils/colorUtils';

const svgns = 'http://www.w3.org/2000/svg';
const MID_FILL = '#989898'; // depth-4 nested-cluster fill, lum 0.314 (< 0.4 gate)
const TEXT_FLOOR = 4.5;

/** Build a <g> holding a background polygon (fill) plus a label <text>. */
function makeShapeGroup(fill: string, textFill: string | null): { poly: SVGPolygonElement; text: SVGTextElement } {
    const g = document.createElementNS(svgns, 'g');
    const poly = document.createElementNS(svgns, 'polygon');
    poly.setAttribute('fill', fill);
    const text = document.createElementNS(svgns, 'text');
    if (textFill !== null) text.setAttribute('fill', textFill);
    text.textContent = 'depth 4';
    g.appendChild(poly);
    g.appendChild(text);
    return { poly, text };
}

describe('D-283 mid-luminance kept fill strands its paired label', () => {
    it('white label on #989898 starts BELOW the text floor (the defect)', () => {
        // Pre-fix reality: the fill is kept and the white label is 2.885:1.
        expect(calculateContrastRatio('#ffffff', MID_FILL)).toBeCloseTo(2.885, 2);
        expect(calculateContrastRatio('#ffffff', MID_FILL)).toBeLessThan(TEXT_FLOOR);
    });

    it('re-tints the stranded white label so it clears the floor on the kept fill', () => {
        const { poly, text } = makeShapeGroup(MID_FILL, '#ffffff');
        const fixed = retintTextForKeptFill(poly, MID_FILL, '#ffffff');
        expect(fixed).toBe(1);
        const applied = text.getAttribute('fill')!;
        // The fix picks the max-contrast option: black (7.28:1) over white (2.885:1).
        expect(applied).toBe('#000000');
        // The kept fill is identical in light and dark, so this ratio holds on
        // BOTH backgrounds — the text sits directly on #989898 either way.
        expect(calculateContrastRatio(applied, MID_FILL)).toBeGreaterThanOrEqual(TEXT_FLOOR);
        expect(calculateContrastRatio(applied, MID_FILL)).toBeCloseTo(7.28, 1);
    });

    it('is symmetric across themes (default text color used only as a fallback)', () => {
        // Even if the theme default text color differs (dark: #ffffff, light:
        // #000000), a label WITH an explicit failing fill is rescued the same
        // way because the decision is made against the kept fill, not the theme.
        const dark = makeShapeGroup(MID_FILL, '#ffffff');
        retintTextForKeptFill(dark.poly, MID_FILL, '#ffffff');
        const light = makeShapeGroup(MID_FILL, '#ffffff');
        retintTextForKeptFill(light.poly, MID_FILL, '#000000');
        expect(dark.text.getAttribute('fill')).toBe('#000000');
        expect(light.text.getAttribute('fill')).toBe('#000000');
    });

    it('rescues a label with no explicit fill using the theme default fallback', () => {
        // A dark-theme cluster whose label inherits the injected default (#ffffff)
        // is still below the floor on #989898 and must be rescued.
        const { poly, text } = makeShapeGroup(MID_FILL, null);
        const fixed = retintTextForKeptFill(poly, MID_FILL, '#ffffff');
        expect(fixed).toBe(1);
        expect(text.getAttribute('fill')).toBe('#000000');
    });

    it('leaves an already-legible label untouched (no over-reach)', () => {
        // Black on #989898 is 7.28:1 — already above the floor, so it is kept.
        const { poly, text } = makeShapeGroup(MID_FILL, '#000000');
        const fixed = retintTextForKeptFill(poly, MID_FILL, '#000000');
        expect(fixed).toBe(0);
        expect(text.getAttribute('fill')).toBe('#000000');
    });
});
