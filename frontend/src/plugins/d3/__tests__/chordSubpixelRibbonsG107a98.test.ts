/**
 * G-107a98 — chord plugin D-042: sub-pixel ribbons at high edge count.
 *
 * At 870/2450 edges each ribbon's angular width is deeply sub-pixel, so a
 * stroke-less fill (the Stage-2 D-057 behaviour, stroke=0 past ~50 edges)
 * renders NOTHING — 100% of ribbons vanished while the arcs stayed healthy.
 * The corrected fix is a minimum-effective-width guarantee: past the onset the
 * ribbon keeps a small POSITIVE stroke painted in the ribbon's OWN reconciled
 * fill colour (not the neutral arcStroke, which smeared to a monochrome disc in
 * D-057), so the dense bundle paints its data colours and no ribbon disappears.
 *
 * DIRECTION: `chordRibbonStrokeColor` is a NEW export absent on the pre-fix tree
 * (this file will not compile against it), and the width assertion pins that a
 * dense count now yields a positive width where D-057 returned 0. Structural
 * defect -> asserted for BOTH themes via light/dark arcStroke + fill inputs.
 */
import {
  chordRibbonStrokeWidth,
  chordRibbonStrokeColor,
} from '../chordPlugin';

describe('D-042 — sub-pixel ribbons keep a minimum effective width', () => {
  it('dense edge counts get a positive stroke width, low counts keep 0.5px', () => {
    expect(chordRibbonStrokeWidth(40)).toBe(0.5);
    // Onset boundary unchanged.
    expect(chordRibbonStrokeWidth(50)).toBe(0.5);
    // Past the onset the width is positive so the sub-pixel fill has something
    // to paint (pre-fix this was 0 -> ribbons vanished).
    expect(chordRibbonStrokeWidth(870)).toBeGreaterThan(0);
    expect(chordRibbonStrokeWidth(2450)).toBeGreaterThan(0);
  });

  it('dense stroke is the ribbon OWN fill; sparse stroke is the neutral separator — in BOTH themes', () => {
    // Light theme: neutral arcStroke #555555, a reconciled dark-ish ribbon fill.
    const lightArc = '#555555';
    const lightFill = '#1f77b4';
    // Sparse: neutral separator preserved.
    expect(chordRibbonStrokeColor(40, lightArc, lightFill)).toBe(lightArc);
    // Dense: the ribbon paints its own data colour, NOT the neutral stroke that
    // would smear to a disc.
    expect(chordRibbonStrokeColor(870, lightArc, lightFill)).toBe(lightFill);
    expect(chordRibbonStrokeColor(870, lightArc, lightFill)).not.toBe(lightArc);

    // Dark theme: neutral arcStroke #cfcfcf, a reconciled light-ish ribbon fill.
    const darkArc = '#cfcfcf';
    const darkFill = '#8ecae6';
    expect(chordRibbonStrokeColor(40, darkArc, darkFill)).toBe(darkArc);
    expect(chordRibbonStrokeColor(2450, darkArc, darkFill)).toBe(darkFill);
    expect(chordRibbonStrokeColor(2450, darkArc, darkFill)).not.toBe(darkArc);
  });
});
