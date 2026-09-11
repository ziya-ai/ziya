/**
 * G-17 / D-018 — labelAngle:0 must reach a channel encoded at the TOP LEVEL of
 * a layered spec.
 *
 * applySharedAxisDefaults iterated only the layers: for a dual-axis combo whose
 * x lives in top-level `encoding` and whose y is authored per layer, the shared
 * branch did `layers.findIndex(encodes) === -1 -> return`, so the top-level x
 * axis never received labelAngle:0 and Jan..Jun rendered rotated 90 degrees.
 *
 * The fix injects the shared axis default onto the top-level encoding when no
 * layer encodes the channel and no axis is authored anywhere for it.
 *
 * Direction: on the unpatched tree the layered branch has no top-level path, so
 * `spec.encoding.x.axis` is left undefined and the labelAngle assertion throws.
 */
import { applySharedAxisDefaults } from '../vegaLayerDefaults';

// Reduced form of vega-lite-w1-10: x is a shared top-level nominal encoding;
// each layer authors only its own y axis (dual-axis combo).
const dualAxisCombo = () => ({
  $schema: 'https://vega.github.io/schema/vega-lite/v5.json',
  width: 440,
  height: 260,
  encoding: { x: { field: 'month', type: 'nominal', title: 'Month' } },
  layer: [
    {
      mark: { type: 'bar' },
      encoding: {
        y: { field: 'signups', type: 'quantitative', axis: { titleColor: '#8da0cb' } },
      },
    },
    {
      mark: { type: 'line' },
      encoding: {
        y: { field: 'conv', type: 'quantitative', axis: { titleColor: '#e7298a' } },
      },
    },
  ],
  resolve: { scale: { y: 'independent' } },
});

describe('G-17 D-018 top-level axis default on a layered spec', () => {
  it('injects labelAngle:0 onto the shared top-level x encoding', () => {
    const spec = dualAxisCombo();
    const injected = applySharedAxisDefaults(spec);

    // The top-level x encoding now carries the horizontal-label default...
    expect(spec.encoding.x.axis).toBeDefined();
    expect((spec.encoding.x.axis as any).labelAngle).toBe(0);
    // ...and the injection is reported against the top level.
    expect(injected).toContain('x@top');
  });

  it('does not touch a y axis the author configured per layer', () => {
    const spec = dualAxisCombo();
    applySharedAxisDefaults(spec);

    // Author-owned y axes are left exactly as written (no labelAngle stomped in,
    // titleColor preserved) so the shared scale merge is not disturbed.
    expect((spec.layer[0].encoding.y.axis as any).titleColor).toBe('#8da0cb');
    expect((spec.layer[1].encoding.y.axis as any).titleColor).toBe('#e7298a');
    expect((spec.layer[0].encoding.y.axis as any).labelAngle).toBeUndefined();
  });

  it('leaves the top-level channel alone when the author already gave it an axis', () => {
    const spec: any = dualAxisCombo();
    spec.encoding.x.axis = { labelAngle: 270, title: 'Month' };
    applySharedAxisDefaults(spec);

    // Author intent (rotated) preserved — the default must not overwrite it.
    expect(spec.encoding.x.axis.labelAngle).toBe(270);
  });

  it('still injects onto the first layer that encodes the channel (unchanged path)', () => {
    // Regression guard: when x IS encoded inside a layer, behaviour is the
    // established per-layer injection, not the new top-level path.
    const spec: any = {
      layer: [
        { mark: 'bar', encoding: { x: { field: 'a', type: 'nominal' }, y: { field: 'v', type: 'quantitative' } } },
      ],
    };
    const injected = applySharedAxisDefaults(spec);
    expect((spec.layer[0].encoding.x.axis as any).labelAngle).toBe(0);
    expect(injected).toContain('x@0');
  });
});
