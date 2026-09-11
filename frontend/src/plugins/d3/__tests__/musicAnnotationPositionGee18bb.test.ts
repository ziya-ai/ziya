/**
 * Regression test for group G-ee18bb / D-166 in musicPlugin.ts.
 *
 *   D-166  A note annotation with `position:"below"` engraved ABOVE the staff.
 *          The render used `ann.setPosition(Annotation.Position.BOTTOM)`, but
 *          VexFlow 5 controls an Annotation's vertical placement through
 *          `setVerticalJustification(AnnotationVerticalJustify)`, and its
 *          inherited ModifierPosition enum carries only
 *          CENTER/LEFT/RIGHT/ABOVE/BELOW -- no TOP or BOTTOM.  So both
 *          `Annotation.Position.TOP` and `Annotation.Position.BOTTOM`
 *          evaluated to `undefined`, and `setPosition(undefined)` left every
 *          annotation at the default (TOP) justification.
 *
 * resolveAnnotationVerticalJustify() maps the spec `position` to the correct
 * AnnotationVerticalJustify member.  This test imports that helper -- absent
 * from the unpatched source, so the import resolves to `undefined` and the
 * calls below throw, failing the suite without the fix (the same
 * fails-without/passes-with pattern the D-146/D-144 helper suite uses).
 *
 * VexFlow itself is NOT imported: loading it in jsdom trips a font-metrics
 * path that needs `structuredClone`, which is why every other music*.test.ts
 * keeps to pure helpers.  The real AnnotationVerticalJustify values are
 * mirrored here as a stub (TOP=1, CENTER=2, BOTTOM=3, CENTER_STEM=4 -- verified
 * against vexflow/build/types/src/annotation.d.ts) so the mapping is still
 * checked against the genuine enum shape.
 */
import { resolveAnnotationVerticalJustify } from '../../../utils/d3Plugins/musicPlugin';

// Mirrors VexFlow 5's AnnotationVerticalJustify enum exactly.
const VJ = { TOP: 1, CENTER: 2, BOTTOM: 3, CENTER_STEM: 4 } as const;

// Mirrors the inherited ModifierPosition enum the OLD code reached for via
// `Annotation.Position.*` -- note it has NO TOP/BOTTOM, which is why the old
// lookup produced `undefined`.
const MODIFIER_POSITION = {
  CENTER: 0, LEFT: 1, RIGHT: 2, ABOVE: 3, BELOW: 4,
} as Record<string, number>;

describe('D-166 resolveAnnotationVerticalJustify: annotation below-placement', () => {
  test('"below" resolves to AnnotationVerticalJustify.BOTTOM (3), not TOP', () => {
    expect(resolveAnnotationVerticalJustify('below', VJ)).toBe(VJ.BOTTOM);
    expect(resolveAnnotationVerticalJustify('below', VJ)).not.toBe(VJ.TOP);
  });

  test('"above" and the default resolve to AnnotationVerticalJustify.TOP (1)', () => {
    expect(resolveAnnotationVerticalJustify('above', VJ)).toBe(VJ.TOP);
    expect(resolveAnnotationVerticalJustify(undefined, VJ)).toBe(VJ.TOP);
  });

  test('below and above are distinct so "below" can leave the default band', () => {
    expect(resolveAnnotationVerticalJustify('below', VJ))
      .not.toBe(resolveAnnotationVerticalJustify('above', VJ));
  });

  test('the old ModifierPosition enum has no BOTTOM -- documents the bug', () => {
    // The pre-fix code read `Annotation.Position.BOTTOM`; Annotation.Position
    // is the inherited ModifierPosition, which has no BOTTOM key, so the old
    // expression was `undefined` and the annotation stayed at the default TOP.
    expect(MODIFIER_POSITION.BOTTOM).toBeUndefined();
    expect(MODIFIER_POSITION.TOP).toBeUndefined();
    // The fixed helper, given the correct enum, never yields undefined.
    expect(resolveAnnotationVerticalJustify('below', VJ)).not.toBeUndefined();
    expect(resolveAnnotationVerticalJustify('above', VJ)).not.toBeUndefined();
  });
});
