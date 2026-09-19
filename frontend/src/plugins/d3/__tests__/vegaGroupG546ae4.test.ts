/**
 * Group G-546ae4 regression guards.
 *
 * D-275 (native theme colour reconciliation): the named-colour table was
 *   grayscale-only, so chromatic CSS names (cornflowerblue / darkseagreen /
 *   lightgoldenrodyellow) resolved to null and ESCAPED reconciliation; and the
 *   fill reconciler was Vega-Lite-only, so a native `scales[].range` palette was
 *   never touched. vega-w4-13's pale native palette then vanished on white.
 * D-514 (VL body under Vega schema): the misleading Vega `$schema` overrode the
 *   Vega-Lite mode in vega-embed's guessMode → blank canvas (vega-w4-07).
 * D-510 (geoshape winding flood): a counter-clockwise GeoJSON exterior ring is
 *   read by d3-geo as the whole-sphere complement → solid flood (vega-w1-11).
 */
import {
  reconcileNativeVegaFills,
  reconcileThemeColors,
  resolveColorToRgb,
  contrastRatio,
} from '../vegaRecovery';
import { reconcileVlBodySchema, isVegaLiteBody, applyVegaMinimalDefaults, normalizeVegaEncodeLifecycle } from '../vegaPlugin';
import { rewindVegaGeoshapePolygons, sanitizeVegaSpec } from '../vegaGraphSanitizer';

const WHITE: [number, number, number] = [255, 255, 255];
const DARKCARD: [number, number, number] = [51, 51, 51];
const ratio = (c: string, bg: [number, number, number]) => {
  const rgb = resolveColorToRgb(c);
  return rgb ? contrastRatio(rgb, bg) : 0;
};

// Planar shoelace in lon/lat (lat = up): POSITIVE => clockwise ring. d3-geo
// requires a CLOCKWISE exterior ring; a counter-clockwise one is read as the
// whole-sphere complement and floods (verified directly against d3-geo's
// geoArea while authoring: CCW ring => 12.55 ≈ 4π, CW ring => 0.013).
const ringSum = (ring: number[][]): number => {
  let s = 0;
  for (let i = 0; i < ring.length - 1; i++) {
    s += (ring[i + 1][0] - ring[i][0]) * (ring[i + 1][1] + ring[i][1]);
  }
  return s;
};
const isClockwise = (ring: number[][]) => ringSum(ring) > 0;

describe('D-275 chromatic CSS names now resolve', () => {
  test.each(['cornflowerblue', 'darkseagreen', 'lightgoldenrodyellow', 'dodgerblue', 'tomato'])(
    '%s resolves to rgb',
    (name) => {
      expect(resolveColorToRgb(name)).not.toBeNull();
    },
  );
});

describe('D-275 native Vega palette + guide reconciliation', () => {
  const mk = () => ({
    background: 'transparent',
    data: [{ name: 't', values: [{ c: 'a', v: 30 }, { c: 'b', v: 52 }, { c: 'd', v: 25 }, { c: 'e', v: 41 }] }],
    scales: [
      { name: 'x', type: 'band', domain: { data: 't', field: 'c' }, range: 'width', padding: 0.2 },
      { name: 'y', type: 'linear', domain: { data: 't', field: 'v' }, range: 'height', nice: true },
      { name: 'col', type: 'ordinal', domain: { data: 't', field: 'c' },
        range: ['cornflowerblue', 'darkseagreen', 'lightgoldenrodyellow', 'gainsboro'] },
    ],
    axes: [
      { orient: 'bottom', scale: 'x', labelColor: 'dimgray' },
      { orient: 'left', scale: 'y', gridColor: 'whitesmoke', grid: true },
    ],
    marks: [{ type: 'rect', from: { data: 't' }, encode: { update: {
      x: { scale: 'x', field: 'c' }, width: { scale: 'x', band: 1 },
      y: { scale: 'y', field: 'v' }, y2: { scale: 'y', value: 0 },
      fill: { scale: 'col', field: 'c' }, stroke: { value: 'transparent' } } } }],
  });

  test('light: every native palette entry clears the 3:1 contrast floor on white', () => {
    const light: any = mk();
    reconcileThemeColors(light, false);
    const range = light.scales.find((s: any) => s.name === 'col').range as string[];
    // The two pale entries (lightgoldenrodyellow 1.07:1, gainsboro 1.37:1) must
    // have been nudged; ALL entries must now be legible on white.
    for (const c of range) {
      expect(ratio(c, WHITE)).toBeGreaterThanOrEqual(3);
    }
  });

  test('dark: near-black guide labelColor (dimgray) nudged legible on the dark card', () => {
    const dark: any = mk();
    reconcileThemeColors(dark, true);
    expect(ratio(dark.axes[0].labelColor, DARKCARD)).toBeGreaterThanOrEqual(3);
  });

  test('a legible native palette is left byte-for-byte unchanged (no light regression)', () => {
    const spec: any = { scales: [{ name: 'col', type: 'ordinal', range: ['#1f77b4', '#d62728', '#2ca02c'] }] };
    const before = JSON.stringify(spec);
    reconcileNativeVegaFills(spec, WHITE, false);
    expect(JSON.stringify(spec)).toBe(before);
  });
});

describe('D-278 minimal-defaults spec must be given a plot box', () => {
  // vega-w4-15: unnamed dataset referenced as "t", range-less scales, from-less
  // mark, AND no top-level width/height. Without a plot box the width/height
  // ranges collapse to 0 and the chart renders empty.
  const w415 = () => ({
    data: [{ values: [{ c: 'a', v: 30 }, { c: 'b', v: 52 }, { c: 'd', v: 25 }] }],
    scales: [
      { name: 'x', type: 'band', domain: { data: 't', field: 'c' } },
      { name: 'y', type: 'linear', domain: { data: 't', field: 'v' } },
    ],
    axes: [{ orient: 'bottom', scale: 'x' }, { orient: 'left', scale: 'y' }],
    marks: [{ type: 'rect', encode: { update: {
      x: { scale: 'x', field: 'c' }, width: { scale: 'x', band: 1 },
      y: { scale: 'y', field: 'v' }, y2: { scale: 'y', value: 0 } } } }],
  });

  test('names the dataset, infers ranges, binds the mark AND sets width/height', () => {
    const spec: any = w415();
    normalizeVegaEncodeLifecycle(spec);
    applyVegaMinimalDefaults(spec);
    expect(spec.data[0].name).toBe('t');
    expect(spec.scales[0].range).toBe('width');
    expect(spec.scales[1].range).toBe('height');
    expect(spec.marks[0].from).toEqual({ data: 't' });
    // The plot box the width/height ranges need — absent, this chart is empty.
    expect(spec.width).toBeGreaterThan(0);
    expect(spec.height).toBeGreaterThan(0);
  });

  test('a spec that already sizes itself keeps its authored dimensions', () => {
    const spec: any = { width: 380, height: 200,
      scales: [{ name: 'x', type: 'band', range: 'width' }] };
    applyVegaMinimalDefaults(spec);
    expect(spec.width).toBe(380);
    expect(spec.height).toBe(200);
  });
});

describe('D-514 VL body under a Vega schema', () => {
  const w407 = () => ({
    $schema: 'https://vega.github.io/schema/vega/v5.json',
    width: 380, height: 200,
    data: { values: [{ c: 'a', v: 30 }, { c: 'b', v: 52 }] },
    mark: 'bar',
    encoding: { x: { field: 'c', type: 'nominal' }, y: { field: 'v', type: 'quantitative' } },
  });

  test('is detected as a Vega-Lite body', () => {
    expect(isVegaLiteBody(w407())).toBe(true);
  });

  test('misleading Vega $schema is stripped so vega-embed guessMode honours vega-lite mode', () => {
    const spec: any = w407();
    expect(reconcileVlBodySchema(spec)).toBe(true);
    expect(spec.$schema).toBeUndefined();
  });

  test('a real Vega-Lite $schema is preserved', () => {
    const spec: any = { $schema: 'https://vega.github.io/schema/vega-lite/v5.json', mark: 'bar', encoding: {} };
    expect(reconcileVlBodySchema(spec)).toBe(false);
    expect(spec.$schema).toContain('vega-lite');
  });
});

describe('D-510 geoshape polygon winding flood (verified against d3-geo)', () => {
  // w1-11's land polygons are counter-clockwise (RFC 7946) — d3-geo reads a CCW
  // exterior ring as the sphere's COMPLEMENT and floods the panel.
  const geoshapeSpec = () => ({
    marks: [{ type: 'shape', from: { data: 'land' }, transform: [{ type: 'geoshape', projection: 'proj' }] }],
    data: [{
      name: 'land',
      values: {
        type: 'FeatureCollection',
        features: [{
          type: 'Feature', properties: { v: 3 },
          // counter-clockwise ring (east, then north, then west, then south)
          geometry: { type: 'Polygon', coordinates: [[[-2, 44], [6, 44], [6, 52], [-2, 52], [-2, 44]]] },
        }],
      },
    }],
  });

  test('BEFORE rewind, the authored exterior ring is counter-clockwise (d3-geo flood)', () => {
    const ring = geoshapeSpec().data[0].values.features[0].geometry.coordinates[0];
    expect(isClockwise(ring)).toBe(false);
  });

  test('AFTER rewind, the exterior ring is clockwise (d3-geo draws a small polygon)', () => {
    const spec: any = geoshapeSpec();
    rewindVegaGeoshapePolygons(spec);
    const ring = spec.data[0].values.features[0].geometry.coordinates[0];
    expect(isClockwise(ring)).toBe(true);
  });

  test('sanitizeVegaSpec applies the rewind end-to-end', () => {
    const spec: any = geoshapeSpec();
    sanitizeVegaSpec(spec);
    expect(isClockwise(spec.data[0].values.features[0].geometry.coordinates[0])).toBe(true);
  });

  test('a correctly-wound (clockwise) exterior ring is left untouched', () => {
    const spec: any = {
      marks: [{ type: 'shape', from: { data: 'land' }, transform: [{ type: 'geoshape' }] }],
      data: [{ name: 'land', values: { type: 'FeatureCollection', features: [{
        type: 'Feature', geometry: { type: 'Polygon', coordinates: [[[-2, 44], [-2, 52], [6, 52], [6, 44], [-2, 44]]] },
      }] } }],
    };
    expect(isClockwise(spec.data[0].values.features[0].geometry.coordinates[0])).toBe(true);
    const before = JSON.stringify(spec);
    rewindVegaGeoshapePolygons(spec);
    expect(JSON.stringify(spec)).toBe(before);
  });

  test('no geoshape transform → no-op', () => {
    const spec: any = { marks: [{ type: 'rect' }], data: [{ name: 'x', values: [{ a: 1 }] }] };
    const before = JSON.stringify(spec);
    rewindVegaGeoshapePolygons(spec);
    expect(JSON.stringify(spec)).toBe(before);
  });
});
