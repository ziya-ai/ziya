/**
 * D-508 — long band labels truncated but the LEFT-axis TITLE overprints them.
 *
 * vega-w2-04: eight 120-190 char category names on a LEFT band axis, plus a long
 * left-axis title. The labels are correctly ellipsis-truncated (config
 * labelLimit 320px), but Vega's automatic axis-title placement under-measures
 * the reserved label band on the headless path, so the rotated title lands
 * THROUGH the middle of the truncated labels — overprinting every one (in dark,
 * white title over white label text). The fix (reserveSideBandAxisTitleGap in
 * vegaPlugin.ts) sets an explicit `titleX` beyond the label band's guaranteed
 * maximum width, so the title clears the labels in BOTH themes (a geometry
 * change; theme-independent).
 *
 * Exercises the real exported helpers.
 */
import {
  reserveSideBandAxisTitleGap,
  estimateVegaAxisMaxLabelChars,
  hasVegaAxisTitle,
} from '../vegaPlugin';

// vega-w2-04 (essential shape): LEFT band axis with a long title + long labels.
const longLeftBandSpec = () => ({
  $schema: 'https://vega.github.io/schema/vega/v5.json',
  width: 520,
  height: 300,
  autosize: 'pad',
  data: [
    {
      name: 't',
      values: [
        { k: 'Aggregate end-to-end p99 request latency for the primary customer-facing ingress tier measured across all availability zones in the us-east-1 region during peak business hours', v: 91 },
        { k: 'Aggregate end-to-end p99 request latency for the secondary internal service mesh sidecar proxy layer measured across all availability zones in eu-west-2 during off-peak maintenance windows', v: 74 },
        { k: 'Rate of checksum mismatch events detected during background scrub of cold archival storage volumes', v: 9 },
      ],
    },
  ],
  scales: [
    { name: 'y', type: 'band', domain: { data: 't', field: 'k' }, range: 'height', padding: 0.2 },
    { name: 'x', type: 'linear', domain: { data: 't', field: 'v' }, range: 'width', nice: true },
  ],
  axes: [
    { orient: 'left', scale: 'y', title: 'Extremely verbose fully-qualified telemetry metric identifier as reported by the collection agent' },
    { orient: 'bottom', scale: 'x', title: 'Observed magnitude in engine-defined normalised units', grid: true },
  ],
  marks: [
    { type: 'rect', from: { data: 't' }, encode: { enter: { y: { scale: 'y', field: 'k' }, height: { scale: 'y', band: 1 }, x: { scale: 'x', value: 0 }, x2: { scale: 'x', field: 'v' }, fill: { value: '#4c78a8' } } } },
  ],
});

// A short-label band axis — Vega's default title placement already clears it.
const shortLeftBandSpec = () => ({
  width: 400,
  height: 200,
  data: [{ name: 'd', values: [{ c: 'Jan', v: 1 }, { c: 'Feb', v: 2 }, { c: 'Mar', v: 3 }] }],
  scales: [
    { name: 'y', type: 'band', domain: { data: 'd', field: 'c' }, range: 'height' },
    { name: 'x', type: 'linear', domain: { data: 'd', field: 'v' }, range: 'width' },
  ],
  axes: [{ orient: 'left', scale: 'y', title: 'Month' }],
  marks: [],
});

describe('D-508: LEFT band-axis title cleared of a wide label band', () => {
  it('estimates the widest category label length from the resolved domain', () => {
    const spec = longLeftBandSpec();
    const leftAxis = spec.axes[0];
    const maxChars = estimateVegaAxisMaxLabelChars(spec, leftAxis);
    expect(maxChars).not.toBeNull();
    // The secondary-mesh label is ~187 chars.
    expect(maxChars!).toBeGreaterThan(150);
  });

  it('pushes the long LEFT-axis title far left, clear of the 320px label band', () => {
    const spec = longLeftBandSpec();
    // WOULD FAIL before the fix: no preprocessing set titleX, so Vega's own
    // under-measured auto-placement drew the title over the labels.
    expect((spec.axes[0] as any).titleX).toBeUndefined();

    const n = reserveSideBandAxisTitleGap(spec, 320);
    expect(n).toBe(1);
    const titleX = (spec.axes[0] as any).titleX;
    // Negative (leftward for orient:left) and beyond the reserved band (>= ~320).
    expect(typeof titleX).toBe('number');
    expect(titleX).toBeLessThan(-320);
    // The bottom (horizontal) axis title is never touched.
    expect((spec.axes[1] as any).titleX).toBeUndefined();
  });

  it('leaves a SHORT-label band axis unchanged (Vega default already clears it)', () => {
    const spec = shortLeftBandSpec();
    const n = reserveSideBandAxisTitleGap(spec, 320);
    expect(n).toBe(0);
    expect((spec.axes[0] as any).titleX).toBeUndefined();
  });

  it('respects an author-set titleX and a title-less axis (no-op)', () => {
    const authored = longLeftBandSpec();
    (authored.axes[0] as any).titleX = -50;
    expect(reserveSideBandAxisTitleGap(authored, 320)).toBe(0);
    expect((authored.axes[0] as any).titleX).toBe(-50);

    const titleless = longLeftBandSpec();
    delete (titleless.axes[0] as any).title;
    expect(reserveSideBandAxisTitleGap(titleless, 320)).toBe(0);
    expect((titleless.axes[0] as any).titleX).toBeUndefined();
  });

  it('hasVegaAxisTitle recognises string / {text} / array titles and rejects empty', () => {
    expect(hasVegaAxisTitle('Metric')).toBe(true);
    expect(hasVegaAxisTitle({ text: 'Metric' })).toBe(true);
    expect(hasVegaAxisTitle(['line one', 'line two'])).toBe(true);
    expect(hasVegaAxisTitle('   ')).toBe(false);
    expect(hasVegaAxisTitle(undefined)).toBe(false);
    expect(hasVegaAxisTitle({})).toBe(false);
  });
});
