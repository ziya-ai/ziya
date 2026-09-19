/**
 * Group G-6d6c01 — native-Vega sizing/recovery (vega engine).
 *
 * Covers two independent defects whose fix sites are the native-Vega
 * preprocessors in vegaPlugin.ts:
 *
 *   D-505 "autosize-none-canvas-oversized-underfill" (BOTH themes):
 *     a native spec with `autosize:"none"` (donut vega-w1-05 340², sunburst
 *     w1-06 400², circle-pack w1-09 420², force/fan w2-12 700×560) renders
 *     complete but at native size in the corner of a much larger delivered
 *     canvas. `autosize:"none"` suppresses Vega's layout fit and the responsive
 *     viewBox the headless sizing/capture path relies on. The fix rewrites a
 *     native `autosize:"none"` / `{type:"none"}` to Vega's default
 *     `{type:"pad", contains:"padding"}`; every other autosize (pad / fit /
 *     fit-x / absent) is left untouched.
 *
 *   D-516 "no-width-height-collapses-to-zero-extent" (BOTH themes):
 *     vega-w4-15 omits everything optional — no $schema, no width/height, an
 *     UNNAMED dataset while scales reference `t`, range-less scales, and a
 *     from-less mark. applyVegaMinimalDefaults must name the dataset, infer the
 *     positional scale ranges, DEFAULT width/height (so the width/height signals
 *     the ranges map into are non-zero), and bind the mark to the sole dataset.
 *
 * DIRECTION: `normalizeNativeVegaAutosize` does not exist on the pre-fix tree,
 * so its import + the autosize assertions fail without the change and pass with
 * it. The D-516 assertions on width/height/range/from fail on a tree lacking
 * the width/height-defaulting (2b) block in applyVegaMinimalDefaults.
 *
 * Sizing is theme-independent (no colour is touched), so a single structural
 * assertion covers BOTH themes; this is called out explicitly per the contract.
 */
import {
  normalizeNativeVegaAutosize,
  applyVegaMinimalDefaults,
} from '../vegaPlugin';

describe('G-6d6c01 / D-505 — native autosize:"none" is normalised to a fitting default', () => {
  test('string autosize:"none" is rewritten to {type:"pad", contains:"padding"}', () => {
    const spec: any = {
      $schema: 'https://vega.github.io/schema/vega/v5.json',
      width: 340,
      height: 340,
      autosize: 'none',
      marks: [{ type: 'arc', from: { data: 'slices' } }],
    };
    const changed = normalizeNativeVegaAutosize(spec);
    expect(changed).toBe(true);
    expect(spec.autosize).toEqual({ type: 'pad', contains: 'padding' });
    // Authored geometry must be preserved verbatim (D-505 is a sizing-only fix).
    expect(spec.width).toBe(340);
    expect(spec.height).toBe(340);
    expect(spec.marks).toHaveLength(1);
  });

  test('object {type:"none"} is rewritten too', () => {
    const spec: any = { width: 700, height: 560, autosize: { type: 'none' } };
    expect(normalizeNativeVegaAutosize(spec)).toBe(true);
    expect(spec.autosize).toEqual({ type: 'pad', contains: 'padding' });
  });

  test('autosize "pad" / "fit" / "fit-x" / object-fit / absent are left untouched (no-op)', () => {
    for (const a of ['pad', 'fit', 'fit-x', 'fit-y'] as const) {
      const spec: any = { width: 100, height: 100, autosize: a };
      expect(normalizeNativeVegaAutosize(spec)).toBe(false);
      expect(spec.autosize).toBe(a);
    }
    const objFit: any = { autosize: { type: 'fit', contains: 'padding' } };
    expect(normalizeNativeVegaAutosize(objFit)).toBe(false);
    expect(objFit.autosize).toEqual({ type: 'fit', contains: 'padding' });

    const none: any = { width: 200, height: 200 };
    expect(normalizeNativeVegaAutosize(none)).toBe(false);
    expect(none.autosize).toBeUndefined();
  });

  test('the same structural outcome holds regardless of theme (no colour touched)', () => {
    // The function takes no theme argument, so light and dark share the result.
    const mk = () => ({ width: 400, height: 400, autosize: 'none' as const });
    const light: any = mk();
    const dark: any = mk();
    normalizeNativeVegaAutosize(light);
    normalizeNativeVegaAutosize(dark);
    expect(light.autosize).toEqual(dark.autosize);
    expect(light.autosize).toEqual({ type: 'pad', contains: 'padding' });
  });
});

describe('G-6d6c01 / D-516 — everything-omitted native spec recovers a real layout', () => {
  // The exact vega-w4-15 body: no $schema, no width/height/padding, an unnamed
  // dataset while scales reference "t", range-less scales, and a from-less mark.
  const w415 = () => ({
    data: [{ values: [{ c: 'a', v: 30 }, { c: 'b', v: 52 }, { c: 'd', v: 25 }] }],
    scales: [
      { name: 'x', type: 'band', domain: { data: 't', field: 'c' } },
      { name: 'y', type: 'linear', domain: { data: 't', field: 'v' } },
    ],
    axes: [
      { orient: 'bottom', scale: 'x' },
      { orient: 'left', scale: 'y' },
    ],
    marks: [
      {
        type: 'rect',
        encode: {
          update: {
            x: { scale: 'x', field: 'c' },
            width: { scale: 'x', band: 1 },
            y: { scale: 'y', field: 'v' },
            y2: { scale: 'y', value: 0 },
          },
        },
      },
    ],
  });

  test('names the unnamed dataset that the scales reference', () => {
    const spec: any = w415();
    applyVegaMinimalDefaults(spec);
    expect(spec.data[0].name).toBe('t');
  });

  test('infers the positional scale ranges from the channels they drive', () => {
    const spec: any = w415();
    applyVegaMinimalDefaults(spec);
    const x = spec.scales.find((s: any) => s.name === 'x');
    const y = spec.scales.find((s: any) => s.name === 'y');
    expect(x.range).toBe('width');
    expect(y.range).toBe('height');
  });

  test('defaults width/height so the width/height signals the ranges map into are non-zero', () => {
    const spec: any = w415();
    // Pre-condition of the defect: no width/height authored.
    expect(spec.width).toBeUndefined();
    expect(spec.height).toBeUndefined();
    applyVegaMinimalDefaults(spec);
    expect(typeof spec.width).toBe('number');
    expect(typeof spec.height).toBe('number');
    expect(spec.width).toBeGreaterThan(0);
    expect(spec.height).toBeGreaterThan(0);
  });

  test('binds the from-less data mark to the sole dataset', () => {
    const spec: any = w415();
    applyVegaMinimalDefaults(spec);
    expect(spec.marks[0].from).toEqual({ data: 't' });
  });
});
