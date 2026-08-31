/**
 * G-53 Vega-Lite cluster regression tests.
 *
 * Covers three defects, each fixed by an exported pure helper in
 * vegaLitePlugin.ts so the tests exercise the ACTUAL code path:
 *   - D-264 upgradeStaleVegaLiteSchema : stale v1..v4 $schema -> v5
 *   - D-266 estimateLegendCardinality  : cardinality for generated/derived data
 *   - D-269 applyTemporalTickCountFix  : low-cardinality temporal tick de-dup
 *
 * All three defects are kind recovery/structural and THEME-INDEPENDENT: none of
 * these helpers takes a theme argument and none emits a colour, so the fix is
 * identical in light and dark. Each assertion is written to FAIL against the
 * pre-fix code (direction checks noted inline).
 */

import {
  upgradeStaleVegaLiteSchema,
  estimateLegendCardinality,
  applyTemporalTickCountFix,
  VEGA_DEFAULT_SYMBOL_LIMIT,
} from '../vegaLitePlugin';

// ── D-264: stale Vega-Lite schema upgrade ──────────────────────────────────
describe('upgradeStaleVegaLiteSchema (D-264)', () => {
  it('upgrades the v2 schema of vega-lite-w4-05 to v5 (pre-fix only handled v4)', () => {
    // Direction: the old code matched only "v4"; a v2 URL was left stale and the
    // v5-only legend/cornerRadiusEnd syntax silently dropped.
    expect(upgradeStaleVegaLiteSchema('https://vega.github.io/schema/vega-lite/v2.json'))
      .toBe('https://vega.github.io/schema/vega-lite/v5.json');
  });

  it('upgrades v1 and v3 as well', () => {
    expect(upgradeStaleVegaLiteSchema('https://vega.github.io/schema/vega-lite/v1.json'))
      .toBe('https://vega.github.io/schema/vega-lite/v5.json');
    expect(upgradeStaleVegaLiteSchema('https://vega.github.io/schema/vega-lite/v3.json'))
      .toBe('https://vega.github.io/schema/vega-lite/v5.json');
  });

  it('leaves an already-v5 Vega-Lite schema untouched (no over-reach)', () => {
    expect(upgradeStaleVegaLiteSchema('https://vega.github.io/schema/vega-lite/v5.json')).toBeNull();
  });

  it('never touches a non-lite Vega schema, even an old one', () => {
    // '/vega/v4' is the Vega (not Vega-Lite) runtime; upgrading it to a
    // vega-lite URL would break the spec.
    expect(upgradeStaleVegaLiteSchema('https://vega.github.io/schema/vega/v4.json')).toBeNull();
    expect(upgradeStaleVegaLiteSchema('https://vega.github.io/schema/vega/v5.json')).toBeNull();
  });

  it('is null-safe for non-string / unknown input', () => {
    expect(upgradeStaleVegaLiteSchema(undefined)).toBeNull();
    expect(upgradeStaleVegaLiteSchema(null)).toBeNull();
    expect(upgradeStaleVegaLiteSchema(42 as any)).toBeNull();
    expect(upgradeStaleVegaLiteSchema('not-a-schema')).toBeNull();
  });
});

// ── D-266: legend cardinality for generated / transform-derived data ────────
describe('estimateLegendCardinality (D-266)', () => {
  it('estimates 40 for the vega-lite-w2-11 sequence (0..40 step 1)', () => {
    // Direction: pre-fix, the legend optimiser counted only data.values (absent
    // for sequence data) -> 0 -> no symbolLimit override -> Vega truncated at 30.
    const est = estimateLegendCardinality({ sequence: { start: 0, stop: 40, step: 1, as: 'n' } }, 'k');
    expect(est).toBe(40);
    expect(est).toBeGreaterThan(VEGA_DEFAULT_SYMBOL_LIMIT); // exceeds the silent 30-cap -> triggers uncap
  });

  it('estimates 50 for the vega-lite-w2-12 sequence (0..50 step 1)', () => {
    expect(estimateLegendCardinality({ sequence: { start: 0, stop: 50, step: 1 } }, 'k')).toBe(50);
  });

  it('counts distinct values when the field IS present in raw rows', () => {
    const data = { values: [{ g: 'A' }, { g: 'B' }, { g: 'A' }, { g: 'C' }] };
    expect(estimateLegendCardinality(data, 'g')).toBe(3);
  });

  it('falls back to row count when the legend field is transform-derived (not in raw rows)', () => {
    const data = { values: [{ n: 0 }, { n: 1 }, { n: 2 }] };
    // 'k' is produced by a transform, absent from the raw rows -> upper bound = 3
    expect(estimateLegendCardinality(data, 'k')).toBe(3);
  });

  it('returns 0 when cardinality is undeterminable (no values, no sequence)', () => {
    expect(estimateLegendCardinality({}, 'k')).toBe(0);
    expect(estimateLegendCardinality({ name: 'someDataset' }, 'k')).toBe(0);
    expect(estimateLegendCardinality(null)).toBe(0);
  });

  it('a 30-entry sequence does NOT exceed the cap (w2-13 stays untouched)', () => {
    // Control from the triage: 30 entries render complete; we must not disturb it.
    const est = estimateLegendCardinality({ sequence: { start: 0, stop: 30, step: 1 } }, 'k');
    expect(est).toBe(30);
    expect(est > VEGA_DEFAULT_SYMBOL_LIMIT).toBe(false); // <=30 => no uncap, w2-13 untouched
  });
});

// ── D-269: temporal axis tick-count de-duplication ──────────────────────────
describe('applyTemporalTickCountFix (D-269)', () => {
  const monthlyRows = [
    '2024-01-01', '2024-01-01', '2024-01-01',
    '2024-02-01', '2024-02-01', '2024-02-01',
    '2024-03-01', '2024-03-01', '2024-03-01',
    '2024-04-01', '2024-04-01', '2024-04-01',
    '2024-05-01', '2024-05-01', '2024-05-01',
  ].map((month, i) => ({ month, visits: 100 + i }));

  const makeW103 = () => ({
    $schema: 'https://vega.github.io/schema/vega-lite/v5.json',
    data: { values: monthlyRows },
    mark: 'area',
    encoding: {
      x: { field: 'month', type: 'temporal', title: 'Month', axis: { format: '%b %Y' } },
      y: { field: 'visits', type: 'quantitative' },
    },
  });

  it('sets tickCount to the 5 distinct months of vega-lite-w1-03 and preserves the format', () => {
    const spec = makeW103();
    const changed = applyTemporalTickCountFix(spec);
    expect(changed).toBe(1);
    // Direction: pre-fix there was no temporal tick normalisation, so tickCount
    // was undefined and Vega over-ticked, repeating "Jan 2024" and dropping May.
    expect(spec.encoding.x.axis.tickCount).toBe(5);
    expect(spec.encoding.x.axis.format).toBe('%b %Y'); // existing axis prop kept
  });

  it('does NOT touch a temporal axis the author already controls (tickCount set)', () => {
    const spec = makeW103();
    (spec.encoding.x.axis as any).tickCount = 3;
    const changed = applyTemporalTickCountFix(spec);
    expect(changed).toBe(0);
    expect(spec.encoding.x.axis.tickCount).toBe(3);
  });

  it('does NOT touch a temporal axis that already has a timeUnit', () => {
    const spec = makeW103();
    (spec.encoding.x as any).timeUnit = 'yearmonth';
    const changed = applyTemporalTickCountFix(spec);
    expect(changed).toBe(0);
    expect((spec.encoding.x.axis as any).tickCount).toBeUndefined();
  });

  it('does NOT touch a non-temporal (quantitative) axis', () => {
    const spec = {
      data: { values: [{ a: 1 }, { a: 2 }, { a: 3 }] },
      encoding: { x: { field: 'a', type: 'quantitative' }, y: { field: 'a', type: 'quantitative' } },
    };
    expect(applyTemporalTickCountFix(spec)).toBe(0);
    expect((spec.encoding.x as any).axis).toBeUndefined();
  });

  it('does NOT touch a hidden axis (axis:null)', () => {
    const spec = makeW103();
    (spec.encoding.x as any).axis = null;
    const changed = applyTemporalTickCountFix(spec);
    expect(changed).toBe(0);
    expect(spec.encoding.x.axis).toBeNull();
  });

  it('leaves a high-cardinality temporal axis (>12 distinct) to Vega defaults', () => {
    const many = Array.from({ length: 40 }, (_v, i) => ({
      month: `2024-${String((i % 12) + 1).padStart(2, '0')}-${String((i % 28) + 1).padStart(2, '0')}`,
      visits: i,
    }));
    // 40 distinct dates -> above the gate; no forced tickCount.
    const spec = {
      data: { values: many },
      encoding: {
        x: { field: 'month', type: 'temporal', axis: { format: '%b %Y' } },
        y: { field: 'visits', type: 'quantitative' },
      },
    };
    expect(applyTemporalTickCountFix(spec)).toBe(0);
    expect((spec.encoding.x.axis as any).tickCount).toBeUndefined();
  });
});
