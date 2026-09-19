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
  TRANSPOSE_LABEL_CHARS,
  TRANSPOSE_MAX_CATEGORIES,
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
    // The authored axis title rides along with its channel onto y.
    expect(spec.encoding.y.axis).toEqual({ title: 'programme' });
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
