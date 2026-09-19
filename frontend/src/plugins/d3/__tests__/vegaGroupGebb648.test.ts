import {
  relaxRotatedAxisLabelOverlap,
  estimateVegaAxisBandCount,
} from '../vegaPlugin';
import {
  reconcileNativeVegaTextInk,
  reconcileThemeColors,
  contrastRatio,
  resolveColorToRgb,
  compositeOver,
} from '../vegaRecovery';

/**
 * G-ebb648 — native-Vega authored-input contrast/overlap reconciliation.
 *
 * Defects (all vega engine, both themes):
 *   D-517 authored-text-mark-fill-not-theme-reconciled — FIX. reconcileNative-
 *         VegaTextInk reconciles a native `marks[].encode.<phase>.fill.value`
 *         against the effective theme canvas (compositing fillOpacity + rgba
 *         alpha), repainting a sub-4.5:1 ink with the themed readable ink.
 *         Neither reconcileTextMarkColors (Vega-Lite shape) nor
 *         reconcileNativeVegaFills (skips text) touched these before.
 *   D-519 low-opacity-hairline-stroke-dissolves — FIX. reconcileNativeVegaFills
 *         now RAISES strokeOpacity for a hairline whose low opacity keeps the
 *         composited stroke below the floor even after a full colour nudge
 *         (w2-14's #888 @0.25 reference rule).
 *   D-509 axis-label-overlap-filter-drops-rotated-labels — FIX. relaxRotated-
 *         AxisLabelOverlap restores Vega's native labelOverlap:false on a
 *         modest-count diagonally-rotated band axis (w3-01's 10 CJK/emoji bands
 *         at -35°), so the rotated-bbox overlap filter stops dropping labels.
 *
 * Every guard is written to FAIL on the pre-fix code and pass after.
 */

const TEXT_FLOOR = 4.5;
const WHITE: [number, number, number] = [255, 255, 255];
const DARK: [number, number, number] = [51, 51, 51];

function crStr(color: string, bg: [number, number, number], alpha = 1): number {
  const rgb = resolveColorToRgb(color)!;
  const eff = alpha >= 1 ? rgb : compositeOver(rgb, bg, alpha);
  return contrastRatio(eff, bg);
}

describe('D-517 authored native text-mark fill reconciled to the theme canvas', () => {
  it('lifts a #333 value label that vanishes once the light card is dropped under dark theme (w3-09)', () => {
    // #333 on the dark canvas = 1.00:1; light card already stripped by the
    // background reconciler at this point.
    const spec: any = {
      marks: [
        { type: 'rect', from: { data: 't' }, encode: { enter: { fill: { scale: 'col', field: 'c' } } } },
        { type: 'text', from: { data: 't' }, encode: { enter: { fill: { value: '#333333' } } } },
      ],
    };
    expect(crStr('#333333', DARK)).toBeLessThan(TEXT_FLOOR);
    const n = reconcileNativeVegaTextInk(spec, /* isDarkMode */ true);
    expect(n).toBe(1);
    const fill = spec.marks[1].encode.enter.fill.value;
    expect(crStr(fill, DARK)).toBeGreaterThanOrEqual(TEXT_FLOOR);
  });

  it('lifts a #eeeeee label illegible on the light canvas (w3-10 light)', () => {
    const spec: any = {
      marks: [{ type: 'text', from: { data: 't' }, encode: { enter: { fill: { value: '#eeeeee' } } } }],
    };
    expect(crStr('#eeeeee', WHITE)).toBeLessThan(TEXT_FLOOR);
    reconcileNativeVegaTextInk(spec, /* isDarkMode */ false);
    expect(crStr(spec.marks[0].encode.enter.fill.value, WHITE)).toBeGreaterThanOrEqual(TEXT_FLOOR);
  });

  it('lifts a #b03a2e annotation only in dark, keeping it verbatim in light (w3-03)', () => {
    expect(crStr('#b03a2e', WHITE)).toBeGreaterThanOrEqual(TEXT_FLOOR); // 6.02 — legible on white
    expect(crStr('#b03a2e', DARK)).toBeLessThan(TEXT_FLOOR); // 2.10 — illegible on the dark card

    const light: any = { marks: [{ type: 'text', from: { data: 'agg' }, encode: { enter: { fill: { value: '#b03a2e' } } } }] };
    reconcileNativeVegaTextInk(light, false);
    expect(light.marks[0].encode.enter.fill.value).toBe('#b03a2e'); // untouched in light

    const dark: any = { marks: [{ type: 'text', from: { data: 'agg' }, encode: { enter: { fill: { value: '#b03a2e' } } } }] };
    reconcileNativeVegaTextInk(dark, true);
    expect(crStr(dark.marks[0].encode.enter.fill.value, DARK)).toBeGreaterThanOrEqual(TEXT_FLOOR);
  });

  it('resolves an rgba() caption and its alpha, lifting it in dark (w4-12)', () => {
    const spec: any = {
      marks: [{ type: 'text', encode: { update: { fill: { value: 'rgba(20, 20, 20, 0.9)' } } } }],
    };
    reconcileNativeVegaTextInk(spec, true);
    const fill = spec.marks[0].encode.update.fill.value;
    expect(fill).not.toContain('rgba');
    expect(crStr(fill, DARK)).toBeGreaterThanOrEqual(TEXT_FLOOR);
  });

  it('lifts a #000 @0.45 overlay annotation and drops its dimming opacity (w3-11)', () => {
    const spec: any = {
      marks: [{ type: 'text', encode: { enter: { fill: { value: '#000000' }, fillOpacity: { value: 0.45 } } } }],
    };
    // #000 @0.45 composites to 3.35:1 on white / 1.35:1 on dark — both sub-floor.
    expect(crStr('#000000', WHITE, 0.45)).toBeLessThan(TEXT_FLOOR);
    reconcileNativeVegaTextInk(spec, false);
    const enc = spec.marks[0].encode.enter;
    expect(enc.fillOpacity.value).toBe(1);
    expect(crStr(enc.fill.value, WHITE)).toBeGreaterThanOrEqual(TEXT_FLOOR);
  });

  it('leaves a backdrop-relative signal label and an already-legible ink untouched', () => {
    const spec: any = {
      marks: [
        { type: 'text', encode: { enter: { fill: { signal: "contrast('#fff', x) >= contrast('#000', x) ? '#fff' : '#000'" } } } },
        { type: 'text', encode: { enter: { fill: { value: '#111111' } } } }, // 18.9:1 on white — fine
      ],
    };
    const n = reconcileNativeVegaTextInk(spec, false);
    expect(n).toBe(0);
    expect(spec.marks[0].encode.enter.fill.signal).toBeDefined();
    expect(spec.marks[1].encode.enter.fill.value).toBe('#111111');
  });
});

describe('D-519 low-opacity hairline stroke raised to a visible opacity', () => {
  it('raises strokeOpacity on a #888 @0.25 reference rule that no colour nudge can rescue', () => {
    const spec: any = {
      marks: [
        {
          type: 'rule',
          encode: { enter: { stroke: { value: '#888888' }, strokeOpacity: { value: 0.25 }, strokeWidth: { value: 0.5 } } },
        },
      ],
    };
    // Pre-fix: colour-nudge alone tops out well below 3:1 at 0.25 opacity.
    reconcileThemeColors(spec, /* isDarkMode */ false);
    const enc = spec.marks[0].encode.enter;
    const raised = enc.strokeOpacity.value;
    expect(raised).toBeGreaterThan(0.25); // opacity was actually lifted
    const rgb = resolveColorToRgb(enc.stroke.value)!;
    expect(contrastRatio(compositeOver(rgb, WHITE, raised), WHITE)).toBeGreaterThanOrEqual(3);
  });

  it('does not touch a translucent FILL (an intended area/overlap blend)', () => {
    const spec: any = {
      marks: [
        { type: 'area', from: { data: 't' }, encode: { enter: { fill: { value: '#e74c3c' }, fillOpacity: { value: 0.3 } } } },
      ],
    };
    reconcileThemeColors(spec, false);
    // fillOpacity is preserved — the alpha blend is the visualization, not a defect.
    expect(spec.marks[0].encode.enter.fillOpacity.value).toBe(0.3);
  });
});

describe('D-509 rotated modest-count band axis keeps every label', () => {
  const makeSpec = (labelAngle: number, nBands: number) => ({
    scales: [{ name: 'x', type: 'band', domain: { data: 't', field: 'c' } }],
    data: [{ name: 't', values: Array.from({ length: nBands }, (_, i) => ({ c: `cat-${i}`, v: i })) }],
    axes: [{ orient: 'bottom', scale: 'x', labelAngle }],
  });

  it('counts the distinct bands of an inline band domain', () => {
    const spec = makeSpec(-35, 10);
    expect(estimateVegaAxisBandCount(spec, spec.axes[0])).toBe(10);
  });

  it('sets labelOverlap:false on a -35° 10-band axis (w3-01)', () => {
    const spec: any = makeSpec(-35, 10);
    const n = relaxRotatedAxisLabelOverlap(spec);
    expect(n).toBe(1);
    expect(spec.axes[0].labelOverlap).toBe(false);
  });

  it('leaves a horizontal (0°) or vertical (90°) axis on the thinning default', () => {
    const flat: any = makeSpec(0, 10);
    const vert: any = makeSpec(90, 10);
    expect(relaxRotatedAxisLabelOverlap(flat)).toBe(0);
    expect(relaxRotatedAxisLabelOverlap(vert)).toBe(0);
    expect(flat.axes[0].labelOverlap).toBeUndefined();
    expect(vert.axes[0].labelOverlap).toBeUndefined();
  });

  it('keeps thinning a genuinely dense rotated axis and respects an author labelOverlap', () => {
    const dense: any = makeSpec(-35, 400);
    expect(relaxRotatedAxisLabelOverlap(dense)).toBe(0);
    expect(dense.axes[0].labelOverlap).toBeUndefined();

    const authored: any = makeSpec(-35, 10);
    authored.axes[0].labelOverlap = true;
    expect(relaxRotatedAxisLabelOverlap(authored)).toBe(0);
    expect(authored.axes[0].labelOverlap).toBe(true); // author intent preserved
  });
});
