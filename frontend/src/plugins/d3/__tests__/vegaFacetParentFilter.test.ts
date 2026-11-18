/**
 * absorbFacetParentFilters: a facet sub-layer with its own dataset filtered
 * on `parent.<facetField>` is folded into the faceted dataset.
 *
 * Observed 2026-11-10: a two-column facet of satellite elevation shaded the
 * per-panel slot with a `rect` layer that carried its own `data.values` and
 * `{"filter": "datum.h == parent.h"}`. Vega-Lite hoists layer data to the
 * root of the compiled Vega spec, so the filter runs where no `parent`
 * signal exists and the render fails with `Unrecognized signal name:
 * "parent"`. There is no supported Vega-Lite spelling of this intent.
 *
 * The rows are moved into the faceted dataset tagged with FACET_LAYER_TAG;
 * the owning layer selects the tag, siblings that inherit the faceted data
 * exclude it. The seam tests compile AND run the result headless in Vega
 * (the defect is only raised by vega.parse), and check the scenegraph has
 * one shaded rect per panel while the line layer's colour legend has no
 * `undefined` entry.
 */
import * as fs from 'fs';
import * as path from 'path';

import {
  FACET_LAYER_TAG,
  absorbFacetParentFilters,
  stripFacetParentClauses,
} from '../vegaRecovery';

// vega / vega-lite publish ESM entry points that jest's node runtime cannot
// load untransformed; the UMD bundles work (same loader as
// vegaRangedBarRender.test.ts). jsdom lacks structuredClone, which
// vega-lite calls at module load.
/* eslint-disable @typescript-eslint/no-var-requires */
if (typeof (globalThis as any).structuredClone !== 'function') {
  (globalThis as any).structuredClone = (v: any) => JSON.parse(JSON.stringify(v));
}
jest.mock('vega', () => require('vega/build/vega.js'));
const vega = require('vega');
const vl = require('vega-lite/build/vega-lite.min.js');

const quiet = {
  level() { return this; }, warn() { return this; }, info() { return this; },
  debug() { return this; }, error() { return this; },
} as any;

/** Compile and run headless; resolves to the Vega View or rejects as Vega would. */
async function runHeadless(spec: any) {
  const { spec: vg } = vl.compile(spec, { logger: quiet });
  const view = new vega.View(vega.parse(vg), { renderer: 'none' });
  await view.runAsync();
  return view;
}

/** Every mark item of a given mark type anywhere in the scenegraph. */
function collectItems(node: any, markType: string, out: any[] = []): any[] {
  if (!node) return out;
  if (node.marktype === markType && Array.isArray(node.items)) out.push(...node.items);
  if (Array.isArray(node.items)) node.items.forEach((i: any) => collectItems(i, markType, out));
  return out;
}

/** The reported shape, data trimmed to the minimum that exercises every layer. */
const reported = (): any => ({
  $schema: 'https://vega.github.io/schema/vega-lite/v5.json',
  data: { values: [
    { h: 'A', t: '2026-11-10T10:37:42Z', sat: 'KS145', el: 78 },
    { h: 'A', t: '2026-11-10T10:38:11Z', sat: 'KS145', el: 62.8 },
    { h: 'B', t: '2026-11-10T12:24:58Z', sat: 'KS142', el: 39.7 },
    { h: 'B', t: '2026-11-10T12:25:27Z', sat: 'KS142', el: 33.1 },
  ] },
  facet: { column: { field: 'h', type: 'nominal' } },
  resolve: { scale: { x: 'independent' } },
  spec: { width: 200, height: 120, layer: [
    { data: { values: [
        { h: 'A', a: '2026-11-10T10:37:49Z', b: '2026-11-10T10:38:04Z' },
        { h: 'B', a: '2026-11-10T12:25:05Z', b: '2026-11-10T12:25:20Z' },
      ] },
      transform: [{ filter: 'datum.h == parent.h' }],
      mark: { type: 'rect', opacity: 0.12, color: 'red' },
      encoding: { x: { field: 'a', type: 'temporal' }, x2: { field: 'b' } } },
    { mark: { type: 'rule', strokeDash: [4, 4] }, encoding: { y: { datum: 35 } } },
    { mark: { type: 'line', point: true },
      encoding: {
        x: { field: 't', type: 'temporal' },
        y: { field: 'el', type: 'quantitative', scale: { domain: [28, 80], zero: false } },
        color: { field: 'sat', type: 'nominal' },
      } },
  ] },
});

describe('stripFacetParentClauses', () => {
  it.each([
    ['datum.h == parent.h', ''],
    ['parent.h === datum.h', ''],
    ["datum['h'] == parent[\"h\"]", ''],
    ['(datum.h == parent.h)', ''],
    ['datum.h == parent.h && datum.el > 30', 'datum.el > 30'],
    ['datum.el > 30 && datum.h == parent.h', 'datum.el > 30'],
    ['datum.x > 1 && datum.h == parent.h && datum.y < 2', 'datum.x > 1 && datum.y < 2'],
  ])('%s -> %p', (expr, expected) => {
    expect(stripFacetParentClauses(expr, ['h'])).toBe(expected);
  });

  it('refuses a parent reference to a non-facet field, a disjunction, and a prefix-colliding field', () => {
    expect(stripFacetParentClauses('datum.g == parent.g', ['h'])).toBeNull();
    expect(stripFacetParentClauses('datum.h == parent.h || datum.x', ['h'])).toBeNull();
    // `hole` is not `h`: the clause must not be eaten by a prefix match.
    expect(stripFacetParentClauses('datum.hole == parent.hole', ['h'])).toBeNull();
  });
});

describe('absorbFacetParentFilters — mutation', () => {
  it('moves the sub-layer rows into the faceted data, tagged, and rewrites the filters', () => {
    const spec = reported();
    expect(absorbFacetParentFilters(spec)).toBe(1);
    const rect = spec.spec.layer[0];
    expect(rect.data).toBeUndefined();
    expect(rect.transform).toEqual([{ filter: `datum.${FACET_LAYER_TAG} == 0` }]);
    // Two slot rows appended, each tagged and still carrying the facet field.
    const tagged = spec.data.values.filter((r: any) => r[FACET_LAYER_TAG] === 0);
    expect(tagged.map((r: any) => r.h)).toEqual(['A', 'B']);
    expect(tagged[0]).toEqual({ h: 'A', a: '2026-11-10T10:37:49Z', b: '2026-11-10T10:38:04Z', [FACET_LAYER_TAG]: 0 });
    expect(spec.data.values).toHaveLength(6);
    // Siblings that inherit the faceted data exclude the merged rows; their
    // own transforms (none here) would follow the exclusion.
    expect(spec.spec.layer[1].transform).toEqual([{ filter: `datum.${FACET_LAYER_TAG} == null` }]);
    expect(spec.spec.layer[2].transform).toEqual([{ filter: `datum.${FACET_LAYER_TAG} == null` }]);
    // Nothing left to do on a second pass.
    expect(absorbFacetParentFilters(spec)).toBe(0);
  });

  it('keeps the remainder of a conjunctive filter and prepends the exclusion to a sibling transform', () => {
    const spec = reported();
    spec.spec.layer[0].transform = [{ filter: 'datum.h == parent.h && datum.a != null' }];
    spec.spec.layer[2].transform = [{ calculate: 'datum.el * 2', as: 'el2' }];
    expect(absorbFacetParentFilters(spec)).toBe(1);
    expect(spec.spec.layer[0].transform).toEqual([
      { filter: `datum.${FACET_LAYER_TAG} == 0` },
      { filter: 'datum.a != null' },
    ]);
    expect(spec.spec.layer[2].transform).toEqual([
      { filter: `datum.${FACET_LAYER_TAG} == null` },
      { calculate: 'datum.el * 2', as: 'el2' },
    ]);
  });

  it('handles row/column facets and the bare facet.field form', () => {
    const rc = reported();
    rc.facet = { row: { field: 'h', type: 'nominal' } };
    expect(absorbFacetParentFilters(rc)).toBe(1);
    const bare = reported();
    bare.facet = { field: 'h', type: 'nominal' };
    expect(absorbFacetParentFilters(bare)).toBe(1);
  });

  it.each<[string, (s: any) => void]>([
    ['sub-layer data is a url', (s) => { s.spec.layer[0].data = { url: 'x.json' }; }],
    ['parent names a non-facet field', (s) => { s.spec.layer[0].transform = [{ filter: 'datum.g == parent.g' }]; }],
    ['a merged row lacks the facet field', (s) => { delete s.spec.layer[0].data.values[1].h; }],
    ['no parent reference at all', (s) => { s.spec.layer[0].transform = [{ filter: 'datum.a != null' }]; }],
    ['not a facet', (s) => { delete s.facet; }],
    ['top-level data is not inline', (s) => { s.data = { url: 'main.json' }; }],
  ])('leaves the spec untouched when %s', (_label, mutate) => {
    const spec = reported();
    mutate(spec);
    const before = JSON.parse(JSON.stringify(spec));
    expect(absorbFacetParentFilters(spec)).toBe(0);
    expect(spec).toEqual(before);
  });

  it('tolerates malformed input', () => {
    expect(absorbFacetParentFilters(null)).toBe(0);
    expect(absorbFacetParentFilters('x')).toBe(0);
    const odd: any = reported();
    odd.spec.layer.push(null, 'text', { mark: 'bar' });
    expect(absorbFacetParentFilters(odd)).toBe(1);
  });
});

describe('seam: compiled and run in Vega', () => {
  it('positive control: as written, vega.parse rejects the parent signal', async () => {
    await expect(runHeadless(reported())).rejects.toThrow(/Unrecognized signal name: "parent"/);
  });

  it('after the rewrite it runs, with one shaded rect per panel and a clean colour legend', async () => {
    const spec = reported();
    absorbFacetParentFilters(spec);
    const view = await runHeadless(spec);
    const root = view.scenegraph().root;
    // Two panels, one slot rect in each (the merged rows were partitioned by
    // the facet, not by the parent signal).
    const rects = collectItems(root, 'rect').filter((i: any) => i.fill === 'red');
    expect(rects).toHaveLength(2);
    expect(rects.every((r: any) => r.width > 0)).toBe(true);
    // Each panel draws its own line through its own two points. A line
    // mark's scenegraph items are its VERTICES, so count distinct marks.
    const lineVertices = collectItems(root, 'line');
    expect(new Set(lineVertices.map((v: any) => v.mark)).size).toBe(2);
    expect(lineVertices).toHaveLength(4);
    // Every vertex came from an elevation row, never from a merged slot row.
    expect(lineVertices.every((v: any) => v.datum.el !== undefined && v.datum[FACET_LAYER_TAG] == null)).toBe(true);
    // The merged rows did not leak into the line layer's colour scale.
    const colorScale = view.scale('color');
    // (Vega-Lite sorts a nominal domain, so compare membership, not order.)
    expect([...colorScale.domain()].sort()).toEqual(['KS142', 'KS145']);
  });
});

describe('vegaLitePlugin wiring', () => {
  const src = fs.readFileSync(path.resolve(__dirname, '..', 'vegaLitePlugin.ts'), 'utf8');
  const code = src.split('\n').filter(l => !l.trim().startsWith('//')).join('\n');

  it('the render path calls absorbFacetParentFilters after sanitizeResolveScale', () => {
    const sanitize = code.indexOf('sanitizeResolveScale(vegaSpec);');
    const absorb = code.indexOf('absorbFacetParentFilters(vegaSpec);');
    expect(sanitize).toBeGreaterThan(-1);
    expect(absorb).toBeGreaterThan(sanitize);
  });
});
