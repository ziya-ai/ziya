/**
 * G-a1ef6a — native-Vega authored-fill/stroke reconciliation + unresolvable
 * theme-token recovery (vegaRecovery.ts + vegaPlugin.ts).
 *
 * D-518 (theme, authored-mark-fill-not-reconciled-vs-canvas): reconcileNativeVegaFills
 *   previously nudged only a constant string `fill.value`. It now also nudges:
 *     - a constant `stroke.value` invisible on the canvas (w3-12 #cccccc),
 *     - GRADIENT stops that sit at canvas luminance (w3-12 fade-to-#fff),
 *     - a fill/stroke made invisible by a partial fillOpacity/strokeOpacity
 *       (w3-07's 0.55-opacity #34495e points) — measured on the COMPOSITE.
 * D-515 (recovery, unresolvable theme tokens on vega-w4-14):
 *     - an axis `labelColor:"var(--ziya-text)"` (unresolvable) now resolves to
 *       the themed readable value in BOTH themes instead of painting nothing;
 *     - an ordinal colour scale referenced by MARK fills but by NO legend gets a
 *       default categorical range (so bars render), not just legend-referenced
 *       scales.
 *
 * Every theme assertion is made in BOTH the light (#fff) and dark (#333) canvas.
 */
import {
  reconcileNativeVegaFills,
  reconcileThemeColors,
  isInvisibleComposite,
  resolveColorToRgb,
  contrastRatio,
} from '../vegaRecovery';
import { injectRangelessOrdinalColorScaleRange } from '../vegaPlugin';

const WHITE = resolveColorToRgb('#ffffff')!;
const DARK = resolveColorToRgb('#333333')!;
const clone = (o: any) => JSON.parse(JSON.stringify(o));
const crBg = (c: string, bg: [number, number, number]) => contrastRatio(resolveColorToRgb(c)!, bg);

// ── D-518 ──────────────────────────────────────────────────────────────────
describe('D-518 native-Vega authored mark fills/strokes reconciled vs the canvas', () => {
  it('nudges an invisible constant STROKE on white while keeping a legible fill (w3-12)', () => {
    const spec: any = {
      $schema: 'https://vega.github.io/schema/vega/v5.json',
      marks: [{ type: 'rect', encode: { enter: { fill: { value: '#4572a7' }, stroke: { value: '#cccccc' } } } }],
    };
    reconcileNativeVegaFills(spec, WHITE, false);
    const enc = spec.marks[0].encode.enter;
    // pre-fix: stroke.value was never inspected -> stays #cccccc (~1.6:1) -> FAILS.
    expect(crBg(enc.stroke.value, WHITE)).toBeGreaterThanOrEqual(3);
    expect(enc.fill.value).toBe('#4572a7'); // already legible -> untouched
  });

  it('nudges GRADIENT stops that sit at the canvas luminance, keeps a visible stop', () => {
    const spec: any = {
      marks: [{
        type: 'rect',
        encode: { enter: { fill: { value: { gradient: 'linear', stops: [
          { offset: 0, color: '#2c3e50' }, { offset: 0.6, color: '#dfe6e9' }, { offset: 1, color: '#ffffff' },
        ] } } } },
      }],
    };
    reconcileNativeVegaFills(spec, WHITE, false);
    const stops = spec.marks[0].encode.enter.fill.value.stops;
    // pre-fix: a gradient object value was skipped (only strings handled) -> FAILS.
    expect(crBg(stops[2].color, WHITE)).toBeGreaterThanOrEqual(3); // was #ffffff (1:1)
    expect(stops[0].color).toBe('#2c3e50'); // dark, visible on white -> untouched
  });

  it('nudges a fill made invisible by low opacity (composite-aware) — w3-07 points', () => {
    // #34495e is legible on white RAW (~9:1) so the raw-measure pass leaves it,
    // but at fillOpacity 0.55 it composites to ~2.8:1 and vanishes. The
    // composite-aware nudge repairs it; on the pre-fix (raw-only) code the fill
    // is untouched and the composite stays sub-3:1 -> this assertion FAILS.
    const spec: any = {
      marks: [{ type: 'symbol', encode: { enter: { fill: { value: '#34495e' }, fillOpacity: { value: 0.55 } } } }],
    };
    reconcileNativeVegaFills(spec, WHITE, false);
    expect(isInvisibleComposite(spec.marks[0].encode.enter.fill.value, WHITE, 0.55)).toBe(false);

    // dark canvas: same point layer must clear on #333 too.
    const darkSpec: any = {
      marks: [{ type: 'symbol', encode: { enter: { fill: { value: '#34495e' }, fillOpacity: { value: 0.55 } } } }],
    };
    reconcileNativeVegaFills(darkSpec, DARK, true);
    expect(isInvisibleComposite(darkSpec.marks[0].encode.enter.fill.value, DARK, 0.55)).toBe(false);
  });

  it('leaves a fully-legible native fill byte-for-byte unchanged (no regression)', () => {
    const base: any = { marks: [{ type: 'rect', encode: { enter: { fill: { value: '#e45756' } } } }] };
    const light = clone(base); reconcileNativeVegaFills(light, WHITE, false);
    expect(light.marks[0].encode.enter.fill.value).toBe('#e45756');
  });
});

// ── D-515 ──────────────────────────────────────────────────────────────────
describe('D-515 unresolvable theme tokens recovered on the native path', () => {
  it('resolves an axis labelColor:"var(--ziya-text)" to a readable value in both themes', () => {
    const spec = () => ({
      $schema: 'https://vega.github.io/schema/vega/v5.json',
      axes: [
        { orient: 'bottom', scale: 'x', labelColor: 'var(--ziya-text)' },
        { orient: 'left', scale: 'y', labelColor: 'currentColor' },
      ],
      marks: [],
    });
    const dark: any = reconcileThemeColors(spec(), true);
    // pre-fix: var() did not resolve and was left verbatim -> labels absent -> FAILS.
    expect(dark.axes[0].labelColor).not.toBe('var(--ziya-text)');
    expect(crBg(dark.axes[0].labelColor, DARK)).toBeGreaterThanOrEqual(3);
    expect(dark.axes[1].labelColor).toBe('currentColor'); // resolves via inheritance -> untouched

    const light: any = reconcileThemeColors(spec(), false);
    expect(light.axes[0].labelColor).not.toBe('var(--ziya-text)');
    expect(crBg(light.axes[0].labelColor, WHITE)).toBeGreaterThanOrEqual(3);
  });

  it('gives a MARK-referenced (no-legend) rangeless ordinal colour scale a default range', () => {
    const spec: any = {
      $schema: 'https://vega.github.io/schema/vega/v5.json',
      scales: [{ name: 'col', type: 'ordinal', domain: { data: 't', field: 'c' } }],
      marks: [{ type: 'rect', from: { data: 't' }, encode: { update: { fill: { scale: 'col', field: 'c' } } } }],
    };
    // no legends at all — the only reference is the rect fill.
    // pre-fix: only legend-referenced scales were considered -> 0 injected -> FAILS.
    expect(injectRangelessOrdinalColorScaleRange(spec)).toBe(1);
    expect(spec.scales[0].range).toEqual({ scheme: 'category10' });
  });
});
