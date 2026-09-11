/**
 * D-239 (dense-nominal-labels-overprint-no-thinning) / G-VEGALITE-THEME-LABELS.
 *
 * REAL CAUSE (differs from triage): the triage supposed "labelOverlap:true is
 * insufficient after the labelAngle:0 fix reached this spec". In fact the
 * readable label defaults NEVER reached vega-lite-w2-01 at all — it authors an
 * axis carrying only a `title`, and applyUnitAxisDefaults' hands-off guard
 * (`!enc.axis`) skipped EVERY default the moment any axis object existed. So
 * the 200 nominal labels rendered rotated 90° with no overlap thinning, not
 * because labelOverlap failed but because it (and labelAngle:0) were never
 * applied.
 *
 * THE FIX: when the author's axis touched no label-* property (a title-only /
 * grid-only axis), fill in the omitted label defaults (labelAngle:0,
 * labelOverlap:true, labelLimit, labelFontSize) WITHOUT overriding anything the
 * author set. An axis where the author DID configure labels stays untouched —
 * the established hands-off contract is preserved.
 *
 * Structural / geometry-only: applySharedAxisDefaults takes no theme argument,
 * so the mutation is byte-identical in light and dark. The both-theme
 * assertion here is that the resulting spec is theme-independent (identical
 * across two invocations that stand in for the two themes).
 *
 * Direction: on the unpatched tree a title-only authored axis yields [] and the
 * axis keeps only its title, so the labelAngle assertion below throws.
 */
import { applySharedAxisDefaults } from '../vegaLayerDefaults';

// Reduced form of vega-lite-w2-01: 200 nominal categories, x axis authored with
// only a title (no label-* property).
const titleOnlyNominalX = () => ({
  $schema: 'https://vega.github.io/schema/vega-lite/v5.json',
  height: 260,
  data: { sequence: { start: 0, stop: 200, step: 1, as: 'n' } },
  transform: [{ calculate: "'C'+format(datum.n,'03d')", as: 'cat' }],
  mark: 'bar',
  encoding: {
    x: { field: 'cat', type: 'nominal', axis: { title: 'category (200 distinct)' } },
    y: { field: 'v', type: 'quantitative', axis: { title: 'value' } },
  },
});

describe('D-239 dense-nominal-label defaults on a title-only authored axis', () => {
  it('fills labelAngle:0 + labelOverlap thinning into a title-only x axis, preserving the title', () => {
    const spec: any = titleOnlyNominalX();
    const injected = applySharedAxisDefaults(spec);

    // Readable label defaults now applied (they never were on unpatched code)...
    expect(spec.encoding.x.axis.labelAngle).toBe(0);
    expect(spec.encoding.x.axis.labelOverlap).toBe(true);
    expect(spec.encoding.x.axis.labelLimit).toBeGreaterThan(0);
    // ...without clobbering the author's title.
    expect(spec.encoding.x.axis.title).toBe('category (200 distinct)');
    // ...and the fill is reported.
    expect(injected).toContain('x~labels');
  });

  it('leaves an axis the author gave a label-* property entirely alone (hands-off contract)', () => {
    // Author set labelAngle:-45 explicitly — a deliberate rotation. The fill
    // must NOT fire, and NOTHING (incl. labelOverlap) may be added.
    const spec: any = {
      mark: 'bar',
      encoding: { x: { field: 'cat', type: 'nominal', axis: { labelAngle: -45, values: ['a'] } } },
    };
    const injected = applySharedAxisDefaults(spec);
    expect(spec.encoding.x.axis).toEqual({ labelAngle: -45, values: ['a'] });
    expect(injected).toEqual([]);
  });

  it('is theme-independent: identical mutation in both themes (no theme input)', () => {
    // applySharedAxisDefaults takes no theme, so light and dark produce the
    // same axis. Stand in for both themes with two fresh invocations.
    const light: any = titleOnlyNominalX();
    const dark: any = titleOnlyNominalX();
    applySharedAxisDefaults(light);
    applySharedAxisDefaults(dark);
    expect(light.encoding.x.axis).toEqual(dark.encoding.x.axis);
    // Both carry the horizontal-label + overlap-thinning defaults.
    expect(light.encoding.x.axis.labelAngle).toBe(0);
    expect(dark.encoding.x.axis.labelOverlap).toBe(true);
  });
});
