/**
 * D-309 (long-nominal-labels-dropped-by-overlap-thinning) +
 * D-500 (axis-title-collides-with-rotated-labels) — group G-9ee008.
 *
 * Both defects are the SAME spec, vega-lite-w2-05: a bar chart with eight
 * 65-75 character nominal category names on the x axis. Laying them flat smears
 * them; the width-aware rotation branch keeps every label but at that length
 * they still overprint at the band pitch, the leftmost runs off the left canvas
 * edge, and the x-axis title collides with the rotated band. The robust, general
 * degradation is to TRANSPOSE to a horizontal bar chart — the long labels move
 * to the y axis where each gets a full row of width and lies flat.
 *
 * Direction: on the unpatched tree transposeLongLabelBarChart does not exist /
 * the x channel stays the long nominal, so the "x is the quantitative measure
 * after transpose" assertion fails. With the fix the channels are swapped.
 *
 * Structural + theme-independent: transposeLongLabelBarChart takes no theme, so
 * the mutation is byte-identical in light and dark (asserted via two fresh
 * invocations that stand in for the two themes).
 */
import {
  transposeLongLabelBarChart,
  transposedCategoryLabelLimit,
  TRANSPOSE_LABEL_CHARS,
  TRANSPOSE_MAX_CATEGORIES,
  TRANSPOSE_LABEL_LIMIT_MIN,
  TRANSPOSE_LABEL_LIMIT_MAX,
  TRANSPOSE_LABEL_LIMIT_DEFAULT,
  MAX_AXIS_LABEL_LIMIT,
} from '../vegaLayerDefaults';

// Reduced form of vega-lite-w2-05: 8 categories with 65-75 char labels.
const longLabelBar = () => ({
  $schema: 'https://vega.github.io/schema/vega-lite/v5.json',
  height: 300,
  data: {
    values: [
      { p: 'Enterprise Resource Planning Migration Programme Phase Two Northern Region', v: 10 },
      { p: 'Customer Relationship Management Consolidation And Data Hygiene Initiative', v: 19 },
      { p: 'Distributed Ledger Reconciliation Service Decommissioning Workstream', v: 28 },
      { p: 'Legacy Mainframe Batch Window Compression And Throughput Optimisation', v: 37 },
      { p: 'Cross Border Regulatory Reporting Automation Delivery Track Alpha', v: 46 },
      { p: 'Warehouse Automation Robotics Fleet Firmware Rollout Coordination', v: 55 },
      { p: 'Multi Tenant Identity Federation And Single Sign On Hardening Effort', v: 64 },
      { p: 'Realtime Fraud Signal Ingestion Pipeline Latency Reduction Project', v: 73 },
    ],
  },
  mark: 'bar',
  encoding: {
    x: { field: 'p', type: 'nominal', axis: { title: 'programme' } },
    y: { field: 'v', type: 'quantitative' },
  },
});

describe('D-309/D-500 long-label bar chart transposed to horizontal', () => {
  it('swaps the long nominal x for the quantitative y so labels lie flat on y', () => {
    const spec: any = longLabelBar();
    const transposed = transposeLongLabelBarChart(spec);

    expect(transposed).toBe(true);
    // The long labels now sit on the y axis...
    expect(spec.encoding.y.field).toBe('p');
    expect(spec.encoding.y.type).toBe('nominal');
    // ...and the quantitative measure drives the horizontal bar extent.
    expect(spec.encoding.x.field).toBe('v');
    expect(spec.encoding.x.type).toBe('quantitative');
    // The authored axis title rides along with its channel onto y...
    expect(spec.encoding.y.axis.title).toBe('programme');
    // ...and the category label column is bounded (D-500) so the rotated
    // axis title can never be clamped back onto the labels. Without the fix
    // the axis carried only { title } and the 320px MAX_AXIS_LABEL_LIMIT
    // default applied, overprinting the middle labels.
    expect(spec.encoding.y.axis.labelLimit).toBeLessThan(MAX_AXIS_LABEL_LIMIT);
    expect(spec.encoding.y.axis.labelLimit).toBe(TRANSPOSE_LABEL_LIMIT_DEFAULT);
  });

  it('D-500: bounds the label column relative to the available width', () => {
    // Narrow container (headless 400px floor): column stays well under the
    // observed ~145px collision point.
    const narrow: any = longLabelBar();
    transposeLongLabelBarChart(narrow, 400);
    expect(narrow.encoding.y.axis.labelLimit).toBeGreaterThanOrEqual(TRANSPOSE_LABEL_LIMIT_MIN);
    expect(narrow.encoding.y.axis.labelLimit).toBeLessThan(MAX_AXIS_LABEL_LIMIT);
    expect(narrow.encoding.y.axis.labelLimit).toBeLessThanOrEqual(145);

    // Wide viewport: the column is capped so it never dominates the plot.
    const wide: any = longLabelBar();
    transposeLongLabelBarChart(wide, 2000);
    expect(wide.encoding.y.axis.labelLimit).toBe(TRANSPOSE_LABEL_LIMIT_MAX);

    // The pure limit helper: floor / proportional / ceil.
    expect(transposedCategoryLabelLimit(undefined)).toBe(TRANSPOSE_LABEL_LIMIT_DEFAULT);
    expect(transposedCategoryLabelLimit(300)).toBe(TRANSPOSE_LABEL_LIMIT_MIN); // 96 -> floor
    expect(transposedCategoryLabelLimit(5000)).toBe(TRANSPOSE_LABEL_LIMIT_MAX); // clamp ceil
  });

  it('is theme-independent: identical mutation across two invocations', () => {
    const light: any = longLabelBar();
    const dark: any = longLabelBar();
    transposeLongLabelBarChart(light);
    transposeLongLabelBarChart(dark);
    expect(light.encoding).toEqual(dark.encoding);
  });

  it('leaves a short-label bar chart (labels rotate/flat in place) untouched', () => {
    const spec: any = {
      mark: 'bar',
      data: { values: [{ p: 'Jan', v: 1 }, { p: 'Feb', v: 2 }, { p: 'Mar', v: 3 }] },
      encoding: {
        x: { field: 'p', type: 'nominal' },
        y: { field: 'v', type: 'quantitative' },
      },
    };
    const before = JSON.parse(JSON.stringify(spec.encoding));
    expect(transposeLongLabelBarChart(spec)).toBe(false);
    expect(spec.encoding).toEqual(before);
  });

  it('does not transpose a HIGH-cardinality axis (many rows help nothing)', () => {
    // TRANSPOSE_MAX_CATEGORIES + a long label each — long, but too many to lay
    // out as flat rows, so the transpose must decline.
    const values = Array.from({ length: TRANSPOSE_MAX_CATEGORIES + 5 }, (_, i) => ({
      p: `Very Long Category Name Number ${String(i).padStart(3, '0')} That Exceeds The Limit`,
      v: i,
    }));
    const spec: any = {
      mark: 'bar',
      data: { values },
      encoding: {
        x: { field: 'p', type: 'nominal' },
        y: { field: 'v', type: 'quantitative' },
      },
    };
    expect(transposeLongLabelBarChart(spec)).toBe(false);
    expect(spec.encoding.x.field).toBe('p');
  });

  it('only fires past the length threshold', () => {
    const atThreshold = 'x'.repeat(TRANSPOSE_LABEL_CHARS); // == threshold, not >
    const spec: any = {
      mark: 'bar',
      data: { values: [{ p: atThreshold, v: 1 }, { p: atThreshold + 'y', v: 2 }] },
      encoding: {
        x: { field: 'p', type: 'nominal' },
        y: { field: 'v', type: 'quantitative' },
      },
    };
    // Longest label here is TRANSPOSE_LABEL_CHARS+1 (from the second row) -> fires.
    expect(transposeLongLabelBarChart(spec)).toBe(true);
  });

  it('leaves a non-bar mark alone even with long labels', () => {
    const spec: any = longLabelBar();
    spec.mark = 'point';
    expect(transposeLongLabelBarChart(spec)).toBe(false);
    expect(spec.encoding.x.field).toBe('p');
  });
});
