/**
 * G-e8d304 Vega-Lite cluster regression tests.
 *
 * Two structural defects, each fixed by an exported pure helper in
 * vegaLitePlugin.ts so the test exercises the ACTUAL production code path:
 *
 *   - D-313 computeLegendLabelLimit / longestCommonPrefixLen
 *       (vega-lite-w2-12): a 50-entry legend whose labels share the long prefix
 *       "category-name-" and differ only in the trailing digits. The old flat
 *       labelLimit:80px clips the labels at ~13 chars — exactly inside the
 *       shared prefix — so every distinct series collapses to the identical
 *       visible "category-name-…". The colour encoding stops being injective.
 *
 *   - D-262 declutterDenseTextMarks
 *       (vega-lite-w2-15): 150 text labels over 150 points at fontSize 9 with no
 *       collision avoidance. The fix thins the LABEL layer to a readable density
 *       (uniform every-Nth sample) while leaving the point/data marks intact.
 *
 * Both defects are structural and THEME-INDEPENDENT: neither helper takes a
 * theme argument nor emits a colour, so the fix is identical in light and dark.
 * Assertions are written to FAIL against the pre-fix code (direction noted).
 */

import {
  longestCommonPrefixLen,
  computeLegendLabelLimit,
  declutterDenseTextMarks,
  computeTextMarkReadableCap,
  LEGEND_LABEL_LIMIT_FALLBACK_PX,
  TEXT_MARK_DECLUTTER_IDX,
} from '../vegaLitePlugin';

// ── D-313: legend labels truncated to an identical prefix ───────────────────
describe('computeLegendLabelLimit (D-313)', () => {
  // The 50 distinct labels of vega-lite-w2-12: "category-name-00".."category-name-49".
  const w2_12_labels = Array.from({ length: 50 }, (_v, i) =>
    'category-name-' + String(i).padStart(2, '0'),
  );

  it('finds the shared prefix length of the w2-12 labels', () => {
    // "category-name-" is 14 chars; the digits are what distinguish the series.
    expect(longestCommonPrefixLen(w2_12_labels)).toBe(14);
  });

  it('picks a labelLimit wide enough to reveal the distinguishing suffix (was a flat 80px clip)', () => {
    const limit = computeLegendLabelLimit(w2_12_labels);
    // Direction: pre-fix labelLimit was a hardcoded 80px, which truncates the
    // 16-char "category-name-NN" labels (~96px) inside the shared prefix, so all
    // 50 entries render as the identical "category-name-…".
    expect(limit).toBeGreaterThan(80);
    // Must clear the width needed to show the full 16-char label (~96px) so the
    // trailing digits survive.
    expect(limit).toBeGreaterThanOrEqual(96);
  });

  it('falls back to a generous (non-clipping) width when label strings are unknown', () => {
    // Generated/transform-derived data yields no raw label strings; the fallback
    // must NOT be the old collision-prone 80px.
    const limit = computeLegendLabelLimit([]);
    expect(limit).toBe(LEGEND_LABEL_LIMIT_FALLBACK_PX);
    expect(limit).toBeGreaterThan(80);
  });

  it('never clips short, already-distinct labels below the safe fallback', () => {
    const limit = computeLegendLabelLimit(['Red', 'Green', 'Blue']);
    expect(limit).toBeGreaterThanOrEqual(LEGEND_LABEL_LIMIT_FALLBACK_PX);
  });

  it('longestCommonPrefixLen is 0 when the first characters already differ', () => {
    expect(longestCommonPrefixLen(['apple', 'banana'])).toBe(0);
    expect(longestCommonPrefixLen([])).toBe(0);
  });
});

// ── D-262: dense text-mark overplot with no collision avoidance ─────────────
describe('declutterDenseTextMarks (D-262)', () => {
  // Structural shape of vega-lite-w2-15: 150 sequence rows, a point layer and a
  // text-label layer bound to the derived field "lab", at fontSize 9.
  const makeW2_15 = () => ({
    $schema: 'https://vega.github.io/schema/vega-lite/v5.json',
    data: { sequence: { start: 0, stop: 150, step: 1, as: 'n' } },
    transform: [
      { calculate: '50+45*sin(datum.n*2.399)', as: 'x' },
      { calculate: '50+45*cos(datum.n*1.618)', as: 'y' },
      { calculate: "'node-'+format(datum.n,'03d')", as: 'lab' },
    ],
    height: 420,
    layer: [
      { mark: { type: 'point', size: 40, filled: true } },
      { mark: { type: 'text', dy: -9, fontSize: 9 }, encoding: { text: { field: 'lab', type: 'nominal' } } },
    ],
    encoding: {
      x: { field: 'x', type: 'quantitative' },
      y: { field: 'y', type: 'quantitative' },
    },
  });

  it('derives a readable label cap from the chart height', () => {
    expect(computeTextMarkReadableCap(420)).toBe(35);
    // clamped band
    expect(computeTextMarkReadableCap(60)).toBe(20);
    expect(computeTextMarkReadableCap(5000)).toBe(60);
  });

  it('thins the 150-label text layer while leaving the point layer untouched', () => {
    const spec = makeW2_15();
    const changed = declutterDenseTextMarks(spec);
    // Direction: pre-fix there was NO text-mark declutter pass, so all 150 labels
    // rendered and overprinted; changed would be 0 and no transform injected.
    expect(changed).toBe(1);

    const textLayer = spec.layer[1] as any;
    const pointLayer = spec.layer[0] as any;

    // The text (label) layer gains a collision-based grid cull (D-501): a
    // row_number window PARTITIONED by x/y grid cells, keeping one label per
    // cell — a position-keyed cull, not the old every-Nth index sample.
    expect(Array.isArray(textLayer.transform)).toBe(true);
    const winT = textLayer.transform.find(
      (t: any) => Array.isArray(t.window) && t.window.some((w: any) => w.as === TEXT_MARK_DECLUTTER_IDX),
    );
    expect(winT).toBeTruthy();
    expect(Array.isArray(winT.groupby) && winT.groupby.length === 2).toBe(true);
    // Grid cull keeps the first datum in each cell; NO index-modulo filter.
    const filterT = textLayer.transform.find((t: any) => typeof t.filter === 'string');
    expect(filterT.filter).toContain('=== 1');
    expect(filterT.filter).not.toContain('%');

    // The point/data layer is NOT thinned — every datum is still plotted.
    expect(pointLayer.transform).toBeUndefined();
  });

  it('is idempotent — a second pass injects nothing more', () => {
    const spec = makeW2_15();
    expect(declutterDenseTextMarks(spec)).toBe(1);
    expect(declutterDenseTextMarks(spec)).toBe(0);
    // Still exactly one window transform on the text layer.
    const windows = (spec.layer[1] as any).transform.filter((t: any) => Array.isArray(t.window));
    expect(windows.length).toBe(1);
  });

  it('does NOT thin a small text layer (below the overplot threshold)', () => {
    const spec = {
      data: { sequence: { start: 0, stop: 20, step: 1 } },
      height: 300,
      layer: [
        { mark: { type: 'point' } },
        { mark: { type: 'text' }, encoding: { text: { field: 'lab', type: 'nominal' } } },
      ],
      encoding: { x: { field: 'x', type: 'quantitative' }, y: { field: 'y', type: 'quantitative' } },
    };
    expect(declutterDenseTextMarks(spec)).toBe(0);
    expect((spec.layer[1] as any).transform).toBeUndefined();
  });

  it('does NOT thin a text mark bound to a constant annotation (no data field)', () => {
    const spec = {
      data: { sequence: { start: 0, stop: 200, step: 1 } },
      height: 400,
      layer: [
        { mark: { type: 'point' } },
        { mark: { type: 'text' }, encoding: { text: { value: 'annotation' } } },
      ],
      encoding: { x: { field: 'x', type: 'quantitative' }, y: { field: 'y', type: 'quantitative' } },
    };
    expect(declutterDenseTextMarks(spec)).toBe(0);
    expect((spec.layer[1] as any).transform).toBeUndefined();
  });
});
