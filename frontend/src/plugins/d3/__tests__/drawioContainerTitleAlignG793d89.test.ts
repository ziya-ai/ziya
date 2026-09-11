/**
 * G-793d89 / D-090 — auto-layout-geometry-label-desync, the container /
 * boundary title strand (drawio wave 1 specs w1-08, w1-09).
 *
 * RECORDED FAILURE (resweep 066f898f): "container titles (Region us-west-2 /
 * VPC / Private subnet / Public subnet) stranded at far-right canvas edge,
 * clipped, NOT on their boxes" (w1-08) and "boundary titles Trust boundary:
 * public/internal stranded at top-right of canvas, detached from their dashed
 * fillColor=none boxes" (w1-09). Child-box labels were correct. In dark the
 * stranded dark title lands on the bare canvas and collapses to ~1:1.
 *
 * ROOT CAUSE (confirmed against the code, differs from the gate/autosize
 * hypotheses of the earlier attempts): the explicit-layout gate and the
 * autoSizeCells freeze are BOTH already in place and correct for these specs —
 * the fills render at author geometry. What strands the TITLE is the enhancer's
 * generic overflow handling: maxGraph emits the wide box's title with a
 * margin-left that carries the text toward the right, and the old path only
 * CLAMPED that margin-left to the box's right edge (bbox.width - textWidth - 16)
 * rather than honouring the author's `align=left`. Clamping a left-aligned title
 * to the right edge IS the "stranded at the far-right, clipped" symptom.
 *
 * FIX: honour the author alignment for the box-title idiom
 * (`verticalAlign=top;align=left`) exactly as text-only cells are handled —
 * place the title at the box left edge + spacingLeft. `isBoxTitleLabel` selects
 * the idiom; `computeAlignedMarginLeft` is the shared solver. Neither exists on
 * the unpatched tree, so importing them makes this suite RED before the fix and
 * GREEN after. Structural / theme-independent: the same left placement holds in
 * light and dark, which is what removes the dark ~1:1 collapse.
 */
import { isBoxTitleLabel, computeAlignedMarginLeft } from '../drawioEnhancer';

// Resulting on-screen left edge of the label div after applying newMl.
// margin-left lives in the pre-scale frame, so a delta of (newMl-currentMl)
// moves the div by (newMl-currentMl)*accumScale screen px.
const resultingDivLeft = (
    divLeft: number, currentMl: number, newMl: number, accumScale: number,
) => divLeft + (newMl - currentMl) * accumScale;

describe('isBoxTitleLabel — G-793d89/D-090 box-title idiom detection', () => {
    it('matches the container/boundary title idiom (top-left)', () => {
        expect(isBoxTitleLabel({ align: 'left', verticalAlign: 'top' })).toBe(true);
    });

    it('does NOT match ordinary centre/middle leaf boxes', () => {
        expect(isBoxTitleLabel({ align: 'center', verticalAlign: 'middle' })).toBe(false);
        expect(isBoxTitleLabel({ align: 'center', verticalAlign: 'top' })).toBe(false);
        expect(isBoxTitleLabel({ align: 'left', verticalAlign: 'middle' })).toBe(false);
        expect(isBoxTitleLabel({})).toBe(false);        // maxGraph defaults center/middle
        expect(isBoxTitleLabel(undefined)).toBe(false);
        expect(isBoxTitleLabel('rounded=0;html=1;')).toBe(false); // unparsed string
    });
});

describe('computeAlignedMarginLeft — G-793d89/D-090 title alignment solver', () => {
    // A wide container/boundary box, its title currently emitted toward the
    // RIGHT of the box (the maxGraph behaviour the old clamp merely bounded).
    const shapeLeft = 100, shapeWidth = 500;   // box spans x=100..600
    const divWidth = 120;                       // "Trust boundary: public"
    const divLeft = 470;                        // currently rendered near the right
    const currentMl = 350;                      // maxGraph's emitted margin-left
    const spacingLeft = 8;

    it('places a LEFT-aligned title at the box left edge + spacingLeft (not the right edge)', () => {
        const newMl = computeAlignedMarginLeft({
            shapeLeft, shapeWidth, divLeft, divWidth,
            accumScale: 1, currentMl, align: 'left', spacingLeft,
        });
        const left = resultingDivLeft(divLeft, currentMl, newMl, 1);
        // Title now sits at the box's left edge + inset ...
        expect(left).toBeCloseTo(shapeLeft + spacingLeft, 3);
        // ... and is emphatically NOT stranded near the right edge, which is
        // where the old right-edge clamp (shapeRight - textW - 16) would pin it.
        const oldClampLeft = (shapeLeft + shapeWidth) - divWidth - 16; // = 464
        expect(left).toBeLessThan(oldClampLeft - 200);
    });

    it('honours accumulated parent scale for left alignment', () => {
        const accumScale = 0.5;
        const newMl = computeAlignedMarginLeft({
            shapeLeft, shapeWidth, divLeft, divWidth,
            accumScale, currentMl, align: 'left', spacingLeft,
        });
        const left = resultingDivLeft(divLeft, currentMl, newMl, accumScale);
        expect(left).toBeCloseTo(shapeLeft + spacingLeft * accumScale, 3);
    });

    it('still centres a centre-aligned label (leaf boxes unaffected)', () => {
        const newMl = computeAlignedMarginLeft({
            shapeLeft, shapeWidth, divLeft, divWidth,
            accumScale: 1, currentMl, align: 'center',
        });
        const left = resultingDivLeft(divLeft, currentMl, newMl, 1);
        expect(left + divWidth / 2).toBeCloseTo(shapeLeft + shapeWidth / 2, 3);
    });

    it('right-aligns a right-aligned label at the box right edge - spacingRight', () => {
        const spacingRight = 6;
        const newMl = computeAlignedMarginLeft({
            shapeLeft, shapeWidth, divLeft, divWidth,
            accumScale: 1, currentMl, align: 'right', spacingRight,
        });
        const left = resultingDivLeft(divLeft, currentMl, newMl, 1);
        expect(left + divWidth).toBeCloseTo(shapeLeft + shapeWidth - spacingRight, 3);
    });

    it('defaults an unscaled accumScale to 1 (no divide-by-zero)', () => {
        const newMl = computeAlignedMarginLeft({
            shapeLeft, shapeWidth, divLeft, divWidth,
            accumScale: 0, currentMl, align: 'left', spacingLeft,
        });
        const left = resultingDivLeft(divLeft, currentMl, newMl, 1);
        expect(left).toBeCloseTo(shapeLeft + spacingLeft, 3);
    });
});
