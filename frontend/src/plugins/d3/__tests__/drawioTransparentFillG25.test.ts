import { drawioFillPaintsOpaque, reconcileCanvasLabelColor } from '../drawioPlugin';
import { calculateContrastRatio } from '../../../utils/colorUtils';

/**
 * G-25 / D-027 — a non-painting fill KEYWORD (transparent/inherit/default) was
 * treated as an opaque fill by the render-path contrast gate, so a label on a
 * `fillColor=transparent` vertex kept its dark author fontColor on the dark
 * canvas and vanished.
 *
 * The old gate was `styleObj['fillColor'] && styleObj['fillColor'] !== 'none'`,
 * which excluded only `none`. A `fillColor=transparent` vertex (drawio-w4-09
 * "Outline Only", fontColor #102040) therefore entered the OPAQUE branch, where
 * contrast is measured against the unparseable string 'transparent' (the ratio
 * collapses to 1), so the dark author font was never lifted off the dark canvas
 * (#102040 on #1e1e1e = 1.03:1, invisible). maxGraph paints NO fill for these
 * keywords, so the label actually sits on the themed canvas and must be
 * reconciled against it — exactly the else/canvas-reconcile branch.
 *
 * `drawioFillPaintsOpaque` did NOT exist before the fix, so this suite fails to
 * import against pre-fix code (red-by-construction). Beyond that, the theme
 * assertions are paired per the theme-fix contract: the BROKEN theme (dark) is
 * now correct AND the other theme (light) is still correct — a real per-theme
 * resolution, not a swap-one-constant.
 */

const DARK_CANVAS = '#1e1e1e';
const LIGHT_CANVAS = '#ffffff';
const GRAPHICAL_FLOOR = 3.0;

describe('G-25 / D-027 — drawioFillPaintsOpaque', () => {
    it('a non-painting keyword paints NO opaque fill (transparent/none/inherit/default)', () => {
        expect(drawioFillPaintsOpaque('transparent')).toBe(false);
        expect(drawioFillPaintsOpaque('none')).toBe(false);
        expect(drawioFillPaintsOpaque('inherit')).toBe(false);
        expect(drawioFillPaintsOpaque('default')).toBe(false);
        // case / whitespace tolerant
        expect(drawioFillPaintsOpaque('  TRANSPARENT ')).toBe(false);
    });

    it('an absent fill paints no opaque fill', () => {
        expect(drawioFillPaintsOpaque(undefined)).toBe(false);
        expect(drawioFillPaintsOpaque('')).toBe(false);
    });

    it('a real colour DOES paint an opaque fill (still routed to the opaque contrast pass)', () => {
        expect(drawioFillPaintsOpaque('#d5e8d4')).toBe(true);
        expect(drawioFillPaintsOpaque('#dae8fc')).toBe(true);
        expect(drawioFillPaintsOpaque('cornflowerblue')).toBe(true);
        expect(drawioFillPaintsOpaque('rgb(10,20,30)')).toBe(true);
    });
});

describe('G-25 / D-027 — transparent-fill vertex label reconciled against the canvas (both themes)', () => {
    // drawio-w4-09 "Outline Only": style fillColor=transparent, fontColor=#102040.
    const AUTHOR_FONT = '#102040';

    it('DIRECTION: the author font on the dark canvas is below the graphical floor (the bug)', () => {
        expect(calculateContrastRatio(AUTHOR_FONT, DARK_CANVAS)).toBeLessThan(GRAPHICAL_FLOOR); // ~1.03
    });

    it('DARK (was broken): transparent fill routes to the canvas branch → dark author font is lifted', () => {
        // The render path passes hasFillColor=true for a present-but-non-painting
        // keyword, so the backdrop is the themed canvas (not the #C3D9FF default).
        expect(drawioFillPaintsOpaque('transparent')).toBe(false); // → else branch
        const out = reconcileCanvasLabelColor(AUTHOR_FONT, /*isVertex*/ true, /*hasFillColor*/ true, /*dark*/ true);
        expect(out).not.toBe(AUTHOR_FONT);
        expect(calculateContrastRatio(out, DARK_CANVAS)).toBeGreaterThanOrEqual(GRAPHICAL_FLOOR); // #e0e0e0 ≈ 12.6
    });

    it('LIGHT (was fine): the SAME author font is PRESERVED (16:1 on white — no regression)', () => {
        const out = reconcileCanvasLabelColor(AUTHOR_FONT, /*isVertex*/ true, /*hasFillColor*/ true, /*dark*/ false);
        expect(out).toBe(AUTHOR_FONT); // 16.12:1 — kept verbatim
    });

    it('an already-readable author font is preserved on the canvas in BOTH themes', () => {
        expect(reconcileCanvasLabelColor('#ffffff', true, true, true)).toBe('#ffffff');  // white on dark canvas
        expect(reconcileCanvasLabelColor('#000000', true, true, false)).toBe('#000000'); // black on light canvas
    });
});
