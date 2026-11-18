/**
 * sanitizeResolveScale: a faceted / repeated spec keeps its TOP-LEVEL resolve.
 *
 * Observed 2026-11-10: a two-column facet of satellite elevation, panels two
 * hours apart with 30-second spans, authored `resolve: {scale: {x:
 * "independent"}}` at the top level (where Vega-Lite documents facet-level
 * resolution). sanitizeResolveScale treated only layer/concat as composite,
 * deleted the resolve, and both panels shared one 10:45-12:15 axis: each
 * panel's data collapsed to a cluster of dots at one edge.
 *
 * The strip was justified by a "render hang" (commit 9c8df872), but that
 * comment describes the NESTED `spec.spec.resolve` form. Probed headless with
 * vega-lite + vega: the top-level form compiles and runs; the nested form
 * fails in vega.parse with "Unrecognized scale name: child_layer_N_y". The
 * nested strip stays; the top-level one was collateral.
 *
 * The seam test asserts on the COMPILED Vega scales: with the resolve kept,
 * `x` is no longer a top-level (shared) scale.
 */
import { sanitizeResolveScale } from '../vegaRecovery';

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

const topScaleNames = (spec: any): string[] =>
  (vl.compile(spec, { logger: quiet }).spec.scales || []).map((s: any) => s.name);

const facetSpec = () => ({
  $schema: 'https://vega.github.io/schema/vega-lite/v5.json',
  data: { values: [
    { h: 'A', t: '2026-11-10T10:37:42Z', el: 78 },
    { h: 'A', t: '2026-11-10T10:38:11Z', el: 62.8 },
    { h: 'B', t: '2026-11-10T12:24:58Z', el: 39.7 },
    { h: 'B', t: '2026-11-10T12:25:27Z', el: 33 },
  ] },
  facet: { column: { field: 'h', type: 'nominal' } },
  resolve: { scale: { x: 'independent' } },
  spec: { layer: [
    { mark: 'rule', encoding: { y: { datum: 35 } } },
    { mark: 'line', encoding: {
      x: { field: 't', type: 'temporal' },
      y: { field: 'el', type: 'quantitative', scale: { domain: [28, 80], zero: false } } } },
  ] },
});

describe('sanitizeResolveScale — facet / repeat keep their top-level resolve', () => {
  test('a faceted spec keeps resolve.scale.x independent (was deleted)', () => {
    const spec: any = facetSpec();
    sanitizeResolveScale(spec);
    expect(spec.resolve).toEqual({ scale: { x: 'independent' } });
  });

  test('a repeated spec keeps its top-level resolve', () => {
    const spec: any = {
      repeat: { column: ['b', 'c'] },
      resolve: { scale: { y: 'independent' } },
      spec: { mark: 'line', encoding: {
        x: { field: 'a', type: 'quantitative' },
        y: { field: { repeat: 'column' }, type: 'quantitative' } } },
    };
    sanitizeResolveScale(spec);
    expect(spec.resolve).toEqual({ scale: { y: 'independent' } });
  });

  test('the nested spec.resolve is still stripped (the real hang: vega.parse fails on it)', () => {
    const spec: any = facetSpec();
    spec.spec.resolve = { scale: { y: 'independent' } };
    sanitizeResolveScale(spec);
    expect(spec.spec.resolve).toBeUndefined();
    expect(spec.resolve).toEqual({ scale: { x: 'independent' } });
  });

  test('a single-view spec (no layer/concat/facet/repeat) still has resolve stripped', () => {
    const spec: any = { mark: 'bar', resolve: { scale: { y: 'independent' } } };
    sanitizeResolveScale(spec);
    expect(spec.resolve).toBeUndefined();
    // `spec` present but no facet/repeat is not a composite either.
    const odd: any = { spec: { mark: 'bar' }, resolve: { scale: { y: 'independent' } } };
    sanitizeResolveScale(odd);
    expect(odd.resolve).toBeUndefined();
  });

  test('seam: with the resolve kept, x is a per-cell scale, not a shared top-level one', () => {
    const kept: any = facetSpec();
    sanitizeResolveScale(kept);
    expect(topScaleNames(kept)).not.toContain('x');
    expect(topScaleNames(kept)).toContain('y');
    // Positive control: the pre-fix behaviour (resolve deleted) shares x.
    const stripped: any = facetSpec();
    delete stripped.resolve;
    expect(topScaleNames(stripped)).toContain('x');
  });
});
