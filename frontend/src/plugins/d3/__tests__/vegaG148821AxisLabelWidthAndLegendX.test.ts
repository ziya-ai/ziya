/**
 * G-148821 — vegaLayerDefaults axis/legend defaults on layered & wide-label specs.
 *
 * Three defects share frontend/src/plugins/d3/vegaLayerDefaults.ts:
 *
 *  - D-309 (long-nominal-labels-dropped-by-overlap-thinning): the x label
 *    default forced labelAngle:0 + labelOverlap:true UNCONDITIONALLY. For a
 *    handful of very long nominal labels (vega-lite-w2-05: eight 65-75 char
 *    names) that lays them flat and then THINS six of the eight away. The fix
 *    makes the x default width-aware: long labels rotate (-45) with thinning
 *    OFF, while many short labels keep the flat+thinned behaviour that w2-01
 *    needs.
 *
 *  - D-259 (nominal-axis-labels-forced-90deg) on the dual-axis combo
 *    vega-lite-w1-10: the shared top-level x must receive labelAngle:0 so the
 *    short month labels stay horizontal instead of Vega's default 90°.
 *
 *  - D-310 (phantom-undefined-x-category) on the same combo: synthesizeColorLegend
 *    appends an invisible layer that INHERITS the shared top-level x channel but
 *    whose own rows carry no x field, so x resolves to `undefined` and adds a
 *    phantom category to the band scale. The fix pins each legend row's x to a
 *    real in-domain value.
 *
 * Exercises the real exported helpers.
 */
import {
  applySharedAxisDefaults,
  synthesizeColorLegend,
  resolveXAxisLabelDefaults,
  LONG_LABEL_CHARS,
} from '../vegaLayerDefaults';

// vega-lite-w2-05: eight 65-75 char nominal category names, title-only x axis.
const wideLabelUnitSpec = () => ({
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
  height: 300,
  encoding: {
    x: { field: 'p', type: 'nominal', axis: { title: 'programme' } },
    y: { field: 'v', type: 'quantitative' },
  },
});

// Many SHORT nominal categories — the w2-01 case that flat+thinned defaults fix.
const denseShortUnitSpec = () => ({
  data: { values: Array.from({ length: 40 }, (_, i) => ({ c: `c${i}`, v: i })) },
  mark: 'bar',
  encoding: {
    x: { field: 'c', type: 'nominal' },
    y: { field: 'v', type: 'quantitative' },
  },
});

// vega-lite-w1-10: dual-axis combo, top-level x, per-layer y, resolve.scale.y independent.
const dualAxisComboSpec = () => ({
  title: 'Signups vs Conversion Rate',
  width: 440,
  height: 260,
  data: {
    values: [
      { month: 'Jan', signups: 1200, conv: 4.1 },
      { month: 'Feb', signups: 1450, conv: 4.6 },
      { month: 'Mar', signups: 1310, conv: 3.9 },
      { month: 'Apr', signups: 1680, conv: 5.2 },
      { month: 'May', signups: 1890, conv: 5.8 },
      { month: 'Jun', signups: 1760, conv: 5.1 },
    ],
  },
  encoding: { x: { field: 'month', type: 'nominal', title: 'Month' } },
  layer: [
    { mark: { type: 'bar', color: '#8da0cb' }, encoding: { y: { field: 'signups', type: 'quantitative' } } },
    { mark: { type: 'line', color: '#e7298a' }, encoding: { y: { field: 'conv', type: 'quantitative' } } },
  ],
  resolve: { scale: { y: 'independent' } },
});

describe('D-309: x label defaults are width/cardinality-aware', () => {
  it('rotates long nominal labels and disables overlap-thinning (keeps every label)', () => {
    const spec = wideLabelUnitSpec();
    applySharedAxisDefaults(spec);
    const axis = spec.encoding.x.axis as any;
    // WOULD FAIL before the fix: the old code forced labelAngle:0 + labelOverlap:true,
    // laying eight 65-75 char labels flat and thinning six of them away.
    expect(axis.labelAngle).toBe(-45);
    expect(axis.labelOverlap).toBe(false);
  });

  it('keeps flat + overlap-thinning for many short nominal labels (w2-01 case unregressed)', () => {
    const spec = denseShortUnitSpec();
    applySharedAxisDefaults(spec);
    const axis = spec.encoding.x.axis as any;
    expect(axis.labelAngle).toBe(0);
    expect(axis.labelOverlap).toBe(true);
  });

  it('resolveXAxisLabelDefaults only rotates when a label exceeds the long threshold', () => {
    const longEnc = { field: 'p', type: 'nominal' };
    const longData = { data: { values: [{ p: 'x'.repeat(LONG_LABEL_CHARS + 1) }] } };
    expect(resolveXAxisLabelDefaults(longData, longEnc).labelAngle).toBe(-45);

    const shortEnc = { field: 'c', type: 'nominal' };
    const shortData = { data: { values: [{ c: 'Jan' }, { c: 'Feb' }] } };
    expect(resolveXAxisLabelDefaults(shortData, shortEnc).labelAngle).toBe(0);
  });
});

describe('D-259: dual-axis combo keeps short month labels horizontal', () => {
  it('injects labelAngle:0 on the shared top-level x', () => {
    const spec = dualAxisComboSpec();
    applySharedAxisDefaults(spec);
    expect((spec.encoding.x as any).axis.labelAngle).toBe(0);
    expect((spec.encoding.x as any).axis.labelOverlap).toBe(true);
  });
});

describe('D-310: synthesized legend layer introduces no phantom x category', () => {
  it('pins every legend row to a real in-domain x value', () => {
    const spec = dualAxisComboSpec();
    const before = spec.layer.length;
    const result = synthesizeColorLegend(spec);
    expect(result.added).toBe(true);
    expect(spec.layer.length).toBe(before + 1);

    const legendLayer: any = spec.layer[spec.layer.length - 1];
    const rows = legendLayer.data.values as any[];
    const domain = spec.data.values.map((d) => d.month);

    // WOULD FAIL before the fix: rows carried only {series,color} and no `month`,
    // so the inherited shared x resolved to undefined -> phantom category.
    for (const row of rows) {
      expect(row.month).toBeDefined();
      expect(domain).toContain(row.month);
    }
  });
});
