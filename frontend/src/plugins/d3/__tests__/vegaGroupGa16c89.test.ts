/**
 * Group G-a16c89 regression guards (vega engine).
 *
 * D-279 (dataflow-error-escapes-catch-blank-canvas): vega-w3-04 is a native
 *   tree/link spec whose `stratify` dataset is malformed — a duplicate id, a
 *   self-parent edge and a 2-cycle. d3-hierarchy's stratify THROWS on each
 *   ("ambiguous: b" for the dup), and Vega routes that throw to the view LOGGER:
 *   `runAsync()` resolves and the view `'error'` event never fires, so the throw
 *   escapes render()'s try/catch AND the D-274 error listener alike → silent
 *   blank canvas scored as a successful render. Fix: repair the degenerate
 *   hierarchy in sanitizeVegaStratify BEFORE the runtime (dedupe ids, null a
 *   self/dangling parent, break cycles, keep a single root) so a valid tree
 *   renders. Theme-independent (a data repair, no colour touched).
 *
 * D-510 (geoshape-graticule-flood-and-bbox-shrink): vega-w1-11 is a mercator
 *   projection + world graticule + geoshape spec authored with `autosize:"none"`
 *   ON PURPOSE — the graticule spans the whole projected globe, and `none` clips
 *   that spill to the authored 460×300 frame (D-283 then honours the viewport).
 *   D-505's normalizeNativeVegaAutosize rewrote `none`→`pad` unconditionally,
 *   making Vega lay the view out to CONTAIN the whole-globe graticule and
 *   collapsing the intended map into a sliver (~75% blank). Fix: leave a
 *   projection spec's authored `none` intact. Theme-independent (a sizing gate).
 */
import { sanitizeVegaStratify, sanitizeVegaSpec } from '../vegaGraphSanitizer';
import { normalizeNativeVegaAutosize } from '../vegaPlugin';

// vega-w3-04's malformed hierarchy dataset (id/p, tidy tree).
const w304Tree = () => ([
  { id: 'a', p: null },
  { id: 'b', p: 'a' },
  { id: 'b', p: 'a' },   // duplicate id  → "ambiguous: b"
  { id: 'c', p: 'c' },   // self edge     → cycle
  { id: 'd', p: 'e' },   // 2-cycle d<->e
  { id: 'e', p: 'd' },
  { id: 'f', p: 'b' },
]);

const w304Spec = () => ({
  data: [{
    name: 'tree',
    values: w304Tree(),
    transform: [
      { type: 'stratify', key: 'id', parentKey: 'p' },
      { type: 'tree', method: 'tidy', size: [{ signal: 'height' }, { signal: 'width - 100' }] },
    ],
  }],
  marks: [{ type: 'symbol', from: { data: 'tree' } }],
});

describe('D-279 malformed stratify hierarchy is repaired to a valid single tree', () => {
  const idsOf = (rows: any[]) => rows.map((r) => r.id);

  test('BEFORE the repair the dataset has a duplicate id, a self edge and a cycle', () => {
    const rows = w304Tree();
    // duplicate id present
    expect(idsOf(rows).filter((x) => x === 'b').length).toBe(2);
    // self edge present
    expect(rows.some((r) => r.p === r.id)).toBe(true);
  });

  test('sanitizeVegaStratify removes the duplicate id', () => {
    const spec: any = w304Spec();
    sanitizeVegaStratify(spec);
    const rows = spec.data[0].values;
    const ids = rows.map((r: any) => r.id);
    // every id unique
    expect(new Set(ids).size).toBe(ids.length);
    expect(ids.filter((x: string) => x === 'b').length).toBe(1);
  });

  test('sanitizeVegaStratify yields exactly one root and no self/dangling parent', () => {
    const spec: any = w304Spec();
    sanitizeVegaStratify(spec);
    const rows = spec.data[0].values;
    const validIds = new Set(rows.map((r: any) => r.id));
    const roots = rows.filter((r: any) => r.p == null);
    expect(roots.length).toBe(1);
    for (const r of rows) {
      expect(r.p === r.id).toBe(false);                 // no self edge
      if (r.p != null) expect(validIds.has(r.p)).toBe(true); // no dangling parent
    }
  });

  test('sanitizeVegaStratify breaks every parent cycle (walk terminates at the root)', () => {
    const spec: any = w304Spec();
    sanitizeVegaStratify(spec);
    const rows = spec.data[0].values;
    const byId = new Map(rows.map((r: any) => [r.id, r]));
    for (const start of rows) {
      const seen = new Set<string>();
      let cur: any = start;
      while (cur && cur.p != null) {
        expect(seen.has(cur.id)).toBe(false); // no cycle
        seen.add(cur.id);
        cur = byId.get(cur.p);
      }
    }
  });

  test('sanitizeVegaSpec applies the stratify repair end-to-end', () => {
    const spec: any = w304Spec();
    sanitizeVegaSpec(spec);
    const ids = spec.data[0].values.map((r: any) => r.id);
    expect(new Set(ids).size).toBe(ids.length);
    expect(spec.data[0].values.filter((r: any) => r.p == null).length).toBe(1);
  });

  test('a well-formed hierarchy is left byte-for-byte unchanged (no regression)', () => {
    const spec: any = {
      data: [{ name: 't', values: [
        { id: 'a', p: null }, { id: 'b', p: 'a' }, { id: 'c', p: 'a' }, { id: 'd', p: 'b' },
      ], transform: [{ type: 'stratify', key: 'id', parentKey: 'p' }] }],
    };
    const before = JSON.stringify(spec);
    expect(sanitizeVegaStratify(spec)).toBe(0);
    expect(JSON.stringify(spec)).toBe(before);
  });

  test('a spec with no stratify transform is a no-op', () => {
    const spec: any = { data: [{ name: 'x', values: [{ id: 'a', p: 'a' }] }], marks: [{ type: 'rect' }] };
    const before = JSON.stringify(spec);
    expect(sanitizeVegaStratify(spec)).toBe(0);
    expect(JSON.stringify(spec)).toBe(before);
  });
});

describe('D-510 autosize:"none" is preserved for a projection spec', () => {
  const projSpec = () => ({
    width: 460, height: 300, autosize: 'none',
    projections: [{ name: 'proj', type: 'mercator', scale: 380, center: [10, 50] }],
    data: [{ name: 'grid', transform: [{ type: 'graticule', step: [10, 10] }] }],
    marks: [{ type: 'shape', from: { data: 'grid' }, transform: [{ type: 'geoshape', projection: 'proj' }] }],
  });

  test('a projection spec keeps its authored autosize:"none" (no pad rewrite → no bbox shrink)', () => {
    const spec: any = projSpec();
    const rewritten = normalizeNativeVegaAutosize(spec);
    expect(rewritten).toBe(false);
    expect(spec.autosize).toBe('none');
  });

  test('object {type:"none"} is likewise preserved when a projection is present', () => {
    const spec: any = { ...projSpec(), autosize: { type: 'none' } };
    expect(normalizeNativeVegaAutosize(spec)).toBe(false);
    expect(spec.autosize).toEqual({ type: 'none' });
  });

  test('a NON-projection autosize:"none" spec is still rewritten to pad (D-505 preserved)', () => {
    const spec: any = { width: 340, height: 340, autosize: 'none',
      marks: [{ type: 'arc', from: { data: 't' } }], data: [{ name: 't', values: [{ v: 1 }] }] };
    expect(normalizeNativeVegaAutosize(spec)).toBe(true);
    expect(spec.autosize).toEqual({ type: 'pad', contains: 'padding' });
  });
});
