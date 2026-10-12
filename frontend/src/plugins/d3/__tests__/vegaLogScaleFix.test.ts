/**
 * Fix 3.7 / Fix 3.8: log-scale preprocessing (design/harness-small-items.md §3).
 *
 * Defect (reproduced 2026-10-02 with render_diagram): Fix 3.7 deleted an
 * authored `scale.type: "log"` whenever max/min of the channel's `field` was
 * below 100. It read only that field, ignoring x2/y2 and sibling layers that
 * share the scale, so a rule from 5 to 25,000 was judged by its low ends (4x)
 * and drawn on a linear axis. It also skipped layers nested in concat children.
 *
 * Contract now:
 *   - an authored log scale is never removed because of the data range;
 *   - log -> symlog only when some value on the SCALE (field, x2/y2, every
 *     layer sharing it, numeric datums) is <= 0;
 *   - Fix 3.8 leaves a pre-logged axis with an axis.labelExpr alone.
 *
 * Each "keeps log" case is paired with a case that must still convert, so a
 * pass that does nothing cannot satisfy the suite.
 */
import * as fs from 'fs';
import * as path from 'path';
import { fixLogScales, convertManualLogAxes } from '../vegaLitePlugin';

const ruleSpec = (hi: number[]): any => ({
  data: { values: [{ lo: 5, hi: hi[0], r: 'a' }, { lo: 20, hi: hi[1], r: 'b' }] },
  mark: 'rule',
  encoding: {
    y: { field: 'r', type: 'nominal' },
    x: { field: 'lo', type: 'quantitative', scale: { type: 'log' } },
    x2: { field: 'hi' },
  },
});

const twoLayer = (pointValues: number[]): any => ({
  data: { values: pointValues.map((v, i) => ({ lo: 5 + i, hi: 25000, v, r: `r${i}` })) },
  encoding: { y: { field: 'r', type: 'nominal' } },
  layer: [
    {
      mark: 'rule',
      encoding: {
        x: { field: 'lo', type: 'quantitative', scale: { type: 'log' } },
        x2: { field: 'hi' },
      },
    },
    { mark: 'point', encoding: { x: { field: 'v', type: 'quantitative' } } },
  ],
});

describe('fixLogScales: authored log survives a small per-field range', () => {
  it('keeps log on the repro rule (x spans 4x, x2 reaches 25,000)', () => {
    const spec = ruleSpec([25000, 900]);
    expect(fixLogScales(spec)).toEqual([]);
    expect(spec.encoding.x.scale.type).toBe('log');
  });

  it('keeps log when every field on the scale spans < 100x (range is never a reason)', () => {
    const spec = ruleSpec([30, 40]);
    expect(fixLogScales(spec)).toEqual([]);
    expect(spec.encoding.x.scale.type).toBe('log');
  });

  it('keeps log on a top-level two-layer spec with log declared on one layer', () => {
    const spec = twoLayer([7, 12]);
    expect(fixLogScales(spec)).toEqual([]);
    expect(spec.layer[0].encoding.x.scale.type).toBe('log');
  });
});

describe('fixLogScales: log -> symlog when a value on the scale is <= 0', () => {
  it('converts when the 0 is only in x2', () => {
    const spec = ruleSpec([25000, 0]);
    const msgs = fixLogScales(spec);
    expect(spec.encoding.x.scale.type).toBe('symlog');
    expect(msgs).toHaveLength(1);
    expect(msgs[0]).toMatch(/Converted x log scale to symlog at top level/);
  });

  it('converts when the <= 0 value is only in the sibling layer', () => {
    const spec = twoLayer([7, -3]);
    const msgs = fixLogScales(spec);
    expect(spec.layer[0].encoding.x.scale.type).toBe('symlog');
    expect(msgs).toHaveLength(1);
  });

  it('converts a shared parent declaration, not a per-layer copy', () => {
    const spec: any = {
      data: { values: [{ a: 0, b: 10 }] },
      encoding: { x: { field: 'a', type: 'quantitative', scale: { type: 'log' } } },
      layer: [{ mark: 'point' }, { mark: 'tick' }],
    };
    fixLogScales(spec);
    expect(spec.encoding.x.scale.type).toBe('symlog');
    expect(spec.layer[0].encoding).toBeUndefined();
    expect(spec.layer[1].encoding).toBeUndefined();
  });

  it('counts a numeric datum on the secondary channel', () => {
    const spec: any = {
      data: { values: [{ lo: 5 }] },
      mark: 'rule',
      encoding: {
        x: { field: 'lo', type: 'quantitative', scale: { type: 'log' } },
        x2: { datum: 0 },
      },
    };
    fixLogScales(spec);
    expect(spec.encoding.x.scale.type).toBe('symlog');
  });

  it('counts numeric strings and handles y/y2', () => {
    const spec: any = {
      data: { values: [{ v: '0' }, { v: '50' }] },
      mark: 'bar',
      encoding: { y: { field: 'v', type: 'quantitative', scale: { type: 'log' } } },
    };
    expect(fixLogScales(spec)[0]).toMatch(/Converted y log scale/);
    expect(spec.encoding.y.scale.type).toBe('symlog');
  });
});

describe('fixLogScales: traversal', () => {
  const concatWithLayers = (v: number): any => ({
    data: { values: [{ lo: 5, hi: 25000, v, r: 'a' }] },
    vconcat: [{
      encoding: { y: { field: 'r', type: 'nominal' } },
      layer: [
        { mark: 'rule', encoding: { x: { field: 'lo', type: 'quantitative', scale: { type: 'log' } }, x2: { field: 'hi' } } },
        { mark: 'point', encoding: { x: { field: 'v', type: 'quantitative', scale: { type: 'log' } } } },
      ],
    }],
  });

  it('visits layers inside a concat child, using inherited data', () => {
    const spec = concatWithLayers(0);
    const msgs = fixLogScales(spec);
    expect(spec.vconcat[0].layer[0].encoding.x.scale.type).toBe('symlog');
    expect(spec.vconcat[0].layer[1].encoding.x.scale.type).toBe('symlog');
    expect(msgs).toEqual([expect.stringMatching(/at vconcat\[0\] /)]);
  });

  it('leaves the same concat spec alone when every value is positive', () => {
    const spec = concatWithLayers(3);
    expect(fixLogScales(spec)).toEqual([]);
    expect(spec.vconcat[0].layer[0].encoding.x.scale.type).toBe('log');
  });

  it('gives concat siblings separate scales', () => {
    const spec: any = {
      hconcat: [
        { data: { values: [{ a: 0 }] }, mark: 'point', encoding: { x: { field: 'a', type: 'quantitative', scale: { type: 'log' } } } },
        { data: { values: [{ a: 3 }] }, mark: 'point', encoding: { x: { field: 'a', type: 'quantitative', scale: { type: 'log' } } } },
      ],
    };
    fixLogScales(spec);
    expect(spec.hconcat[0].encoding.x.scale.type).toBe('symlog');
    expect(spec.hconcat[1].encoding.x.scale.type).toBe('log');
  });

  it('visits a facet sub-spec with inherited data', () => {
    const spec: any = {
      data: { values: [{ a: 0, g: 'x' }] },
      facet: { field: 'g', type: 'nominal' },
      spec: { mark: 'point', encoding: { x: { field: 'a', type: 'quantitative', scale: { type: 'log' } } } },
    };
    fixLogScales(spec);
    expect(spec.spec.encoding.x.scale.type).toBe('symlog');
  });

  it('with resolve.scale independent, converts only the layer that has the 0', () => {
    const spec: any = {
      data: { values: [{ a: 0, b: 10 }] },
      resolve: { scale: { x: 'independent' } },
      encoding: { x: { type: 'quantitative', scale: { type: 'log' } } },
      layer: [
        { mark: 'point', encoding: { x: { field: 'a', type: 'quantitative', scale: { type: 'log' } } } },
        { mark: 'point', encoding: { x: { field: 'b', type: 'quantitative', scale: { type: 'log' } } } },
      ],
    };
    fixLogScales(spec);
    expect(spec.layer[0].encoding.x.scale.type).toBe('symlog');
    expect(spec.layer[1].encoding.x.scale.type).toBe('log');
  });

  it('overrides an inherited declaration locally when the parent resolves independently', () => {
    const spec: any = {
      data: { values: [{ a: 0, b: 10 }] },
      resolve: { scale: { x: 'independent' } },
      encoding: { x: { field: 'a', type: 'quantitative', scale: { type: 'log' } } },
      layer: [{ mark: 'point' }, { mark: 'point', encoding: { x: { field: 'b', type: 'quantitative' } } }],
    };
    fixLogScales(spec);
    expect(spec.layer[0].encoding.x.scale.type).toBe('symlog');
    expect(spec.encoding.x.scale.type).toBe('log');
  });

  it('is a no-op on specs without log scales or without inline data', () => {
    const plain: any = { data: { values: [{ a: 0 }] }, mark: 'point', encoding: { x: { field: 'a', type: 'quantitative' } } };
    const urlData: any = { data: { url: 'x.csv' }, mark: 'point', encoding: { x: { field: 'a', type: 'quantitative', scale: { type: 'log' } } } };
    expect(fixLogScales(plain)).toEqual([]);
    expect(fixLogScales(urlData)).toEqual([]);
    expect(urlData.encoding.x.scale.type).toBe('log');
    expect(fixLogScales(null)).toEqual([]);
  });
});

describe('convertManualLogAxes (Fix 3.8)', () => {
  const preLogged = (axis?: any): any => ({
    data: { values: [{ n: 10 }, { n: 10000 }] },
    transform: [{ calculate: 'log(datum.n)/log(10)', as: 'log_n' }],
    mark: 'point',
    encoding: { x: { field: 'log_n', type: 'quantitative', scale: { domain: [0, 5] }, ...(axis ? { axis } : {}) } },
  });

  it('leaves a pre-logged axis with labelExpr alone', () => {
    const spec = preLogged({ labelExpr: "'1e' + datum.value" });
    expect(convertManualLogAxes(spec)).toEqual([]);
    expect(spec.encoding.x.field).toBe('log_n');
    expect(spec.encoding.x.scale.type).toBeUndefined();
  });

  it('still rewrites a pre-logged axis without labelExpr', () => {
    const spec = preLogged();
    const msgs = convertManualLogAxes(spec);
    expect(spec.encoding.x.field).toBe('n');
    expect(spec.encoding.x.scale).toEqual({ type: 'log', domain: [1, 100000] });
    expect(msgs).toHaveLength(1);
  });

  it('treats axis: null (hidden axis) as having no labelExpr', () => {
    const spec = preLogged();
    spec.encoding.x.axis = null;
    convertManualLogAxes(spec);
    expect(spec.encoding.x.field).toBe('n');
  });
});

/**
 * Seam: the helpers passing in isolation proves nothing unless the preprocess
 * pipeline calls them. Comments are stripped so documentation does not
 * satisfy the check.
 */
describe('vegaLitePlugin wiring', () => {
  const source = fs.readFileSync(path.join(__dirname, '../vegaLitePlugin.ts'), 'utf8');
  const code = source
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .split('\n')
    .map(line => line.replace(/\/\/.*$/, ''))
    .join('\n');

  it('calls fixLogScales and convertManualLogAxes from the preprocess pipeline', () => {
    expect(code).toMatch(/fixLogScales\(spec\)/);
    expect(code).toMatch(/convertManualLogAxes\(spec\)/);
  });

  it('no longer removes a log scale for a small range', () => {
    expect(code).not.toMatch(/range too small/);
    expect(code).not.toMatch(/delete\s+\w+\.scale\.type/);
  });
});
