/**
 * Fix 3.8, zero half: an authored numeric domain that excludes 0 must not be
 * widened to 0 by a sibling layer's default `zero: true`.
 *
 * Observed 2026-11-10 on a faceted elevation chart: the line layer carried
 * `y.scale.domain: [28, 80]`, a sibling `rule` layer at `y: {datum: 35}` had
 * no scale of its own. Vega-Lite defaults `zero` to false for an encoding
 * WITH a custom domain, but the rule contributed the default `zero: true` to
 * the shared scale and the compiler kept both (`domain: [28, 80], zero:
 * true`), which Vega extends to [0, 80]. No warning. The axis rendered 0-80
 * and the 35-degree mask line sat in the wrong place relative to the data.
 *
 * respectExplicitNumericDomain already set `nice: false` for an authored
 * domain; it now also sets `zero: false` when the domain excludes 0. The
 * compile tests here assert on the OUTERMOST surface -- the compiled Vega
 * scale -- so a change in vega-lite's merge rules would surface here.
 */
import * as fs from 'fs';
import * as path from 'path';

import { respectExplicitNumericDomain } from '../vegaLitePlugin';

// vega / vega-lite publish ESM entry points that jest's node runtime cannot
// load untransformed; the UMD bundles work (same loader as
// vegaRangedBarRender.test.ts). jsdom lacks structuredClone, which
// vega-lite calls at module load.
/* eslint-disable @typescript-eslint/no-var-requires */
if (typeof (globalThis as any).structuredClone !== 'function') {
  (globalThis as any).structuredClone = (v: any) => JSON.parse(JSON.stringify(v));
}
jest.mock('vega', () => require('vega/build/vega.js'));
const vl = require('vega-lite/build/vega-lite.min.js');

const quiet = {
  level() { return this; }, warn() { return this; }, info() { return this; },
  debug() { return this; }, error() { return this; },
} as any;

const compiledScale = (spec: any, name: string) =>
  (vl.compile(spec, { logger: quiet }).spec.scales || []).find((s: any) => s.name === name);

/** The reported shape: facet > layer[rect, rule with datum, line with domain]. */
const reported = () => ({
  data: { values: [
    { h: 'A', a: '2026-11-10T10:37:49Z', b: '2026-11-10T10:38:04Z' },
    { h: 'A', t: '2026-11-10T10:37:42Z', sat: 's', el: 78 },
    { h: 'A', t: '2026-11-10T10:38:11Z', sat: 's', el: 62.8 },
  ] },
  facet: { column: { field: 'h', type: 'nominal' } },
  spec: { layer: [
    { transform: [{ filter: 'isValid(datum.a)' }], mark: 'rect',
      encoding: { x: { field: 'a', type: 'temporal' }, x2: { field: 'b' } } },
    { mark: 'rule', encoding: { y: { datum: 35 } } },
    { transform: [{ filter: 'isValid(datum.el)' }], mark: 'line',
      encoding: {
        x: { field: 't', type: 'temporal' },
        y: { field: 'el', type: 'quantitative', scale: { domain: [28, 80] } },
      } },
  ] },
});

describe('respectExplicitNumericDomain — zero-inclusion', () => {
  beforeEach(() => jest.spyOn(console, 'info').mockImplementation(() => undefined));
  afterEach(() => jest.restoreAllMocks());

  it('sets zero:false (and nice:false) on an authored domain that excludes 0, through facet > layer', () => {
    const spec: any = reported();
    expect(respectExplicitNumericDomain(spec)).toBe(1);
    const y = spec.spec.layer[2].encoding.y.scale;
    expect(y).toEqual({ domain: [28, 80], nice: false, zero: false });
    // The rule layer has no scale and gets none: the fix lives on the
    // encoding that owns the domain.
    expect(spec.spec.layer[1].encoding.y).toEqual({ datum: 35 });
    expect(spec.spec.layer[0].encoding.x.scale).toBeUndefined();
  });

  it('leaves zero alone when the authored domain already includes 0 (nice still disabled)', () => {
    const spec: any = { mark: 'line', encoding: {
      y: { field: 'v', type: 'quantitative', scale: { domain: [0, 430] } } } };
    expect(respectExplicitNumericDomain(spec)).toBe(1);
    expect(spec.encoding.y.scale).toEqual({ domain: [0, 430], nice: false });
  });

  it('handles an all-negative and a reversed domain', () => {
    const neg: any = { mark: 'line', encoding: {
      y: { field: 'v', type: 'quantitative', scale: { domain: [-80, -28] } } } };
    respectExplicitNumericDomain(neg);
    expect(neg.encoding.y.scale.zero).toBe(false);
    const rev: any = { mark: 'line', encoding: {
      x: { field: 'v', type: 'quantitative', scale: { domain: [80, 28] } } } };
    respectExplicitNumericDomain(rev);
    expect(rev.encoding.x.scale.zero).toBe(false);
  });

  it('respects an authored zero (either value) and an authored nice', () => {
    const spec: any = { mark: 'line', encoding: {
      x: { field: 'a', type: 'quantitative', scale: { domain: [28, 80], zero: true } },
      y: { field: 'b', type: 'quantitative', scale: { domain: [28, 80], nice: true } } } };
    expect(respectExplicitNumericDomain(spec)).toBe(2); // x: nice set; y: zero set
    expect(spec.encoding.x.scale).toEqual({ domain: [28, 80], zero: true, nice: false });
    expect(spec.encoding.y.scale).toEqual({ domain: [28, 80], nice: true, zero: false });
    // Nothing more to do on a second pass.
    expect(respectExplicitNumericDomain(spec)).toBe(0);
  });

  it('ignores non-numeric, non-pair and absent domains, and malformed input', () => {
    const spec: any = { layer: [
      { mark: 'bar', encoding: { x: { field: 'c', type: 'nominal', scale: { domain: ['a', 'b'] } } } },
      { mark: 'bar', encoding: { y: { field: 'v', type: 'quantitative', scale: { domain: [1, 2, 3] } } } },
      { mark: 'bar', encoding: { y: { field: 'v', type: 'quantitative' } } },
      null, 'text',
    ] };
    const before = JSON.parse(JSON.stringify(spec));
    expect(respectExplicitNumericDomain(spec)).toBe(0);
    expect(spec).toEqual(before);
    expect(respectExplicitNumericDomain(null)).toBe(0);
    expect(respectExplicitNumericDomain(undefined)).toBe(0);
  });
});

describe('compiled vega scale (the seam)', () => {
  it('without the fix the shared y scale carries zero:true over the [28,80] domain (positive control)', () => {
    const y = compiledScale({ $schema: 'https://vega.github.io/schema/vega-lite/v5.json', ...reported() }, 'y');
    expect(y.domain).toEqual([28, 80]);
    expect(y.zero).toBe(true);
  });

  it('after the fix the compiled y scale has zero:false, so Vega keeps [28,80]', () => {
    const spec: any = { $schema: 'https://vega.github.io/schema/vega-lite/v5.json', ...reported() };
    respectExplicitNumericDomain(spec);
    const y = compiledScale(spec, 'y');
    expect(y.domain).toEqual([28, 80]);
    expect(y.zero).toBe(false);
  });
});

describe('vegaLitePlugin wiring', () => {
  const src = fs.readFileSync(path.resolve(__dirname, '..', 'vegaLitePlugin.ts'), 'utf8');
  const code = src.split('\n').filter(l => !l.trim().startsWith('//')).join('\n');

  it('the render path calls the exported helper, and the inline closure is gone', () => {
    expect(code).toMatch(/^export function respectExplicitNumericDomain\(/m);
    expect(code).toContain('respectExplicitNumericDomain(spec);');
    expect(code).not.toContain('const respectExplicitNumericDomain =');
  });
});
