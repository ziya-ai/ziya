/**
 * @jest-environment jsdom
 *
 * D-315 (group G-ce6844): a Vega-Lite `repeat` operator whose inner spec puts a
 * `{repeat: "column"}` reference in a TRANSFORM position (here a `regression`
 * transform) is NOT substituted by Vega-Lite during repeat expansion. The
 * transform then receives an object where a field name is expected, executes
 * against every generated view, and hangs the headless renderer past its
 * timeout in BOTH themes.
 *
 * The fix expands such repeats ourselves into an explicit concat of concrete,
 * fully-substituted sub-specs. These tests assert the pure preprocessor:
 *   - it fires only for the unsupported transform-position ref,
 *   - it leaves ordinary (encoding-only) repeat specs untouched, and
 *   - every `{repeat}` ref — in encodings AND transforms — is resolved to a
 *     concrete field string so nothing survives for the library to mishandle.
 */
import {
  expandRepeatRefsInTransforms,
  specHasRepeatRefInTransform,
} from '../vegaLitePlugin';

// The failing spec on disk (.ziya/gfx-sweep/specs/vega-lite/vega-lite-w3-14.json).
const w3_14_inner = () => ({
  width: 260,
  height: 200,
  layer: [
    { mark: { type: 'point', filled: true }, encoding: { y: { field: { repeat: 'column' }, type: 'quantitative' } } },
    {
      transform: [{ regression: { repeat: 'column' }, on: 'n' }],
      mark: { type: 'line', stroke: '#e45756' },
      encoding: { y: { field: { repeat: 'column' }, type: 'quantitative' } },
    },
    {
      transform: [{ window: [{ op: 'mean', field: 'n', as: 'rm' }], frame: [-4, 0] }],
      mark: { type: 'line', strokeDash: [3, 2] },
      encoding: { y: { field: 'rm', type: 'quantitative' } },
    },
  ],
  encoding: { x: { field: 'n', type: 'quantitative' } },
});

const w3_14_spec = () => ({
  data: { sequence: { start: 0, stop: 30, as: 'n' } },
  transform: [
    { calculate: 'datum.n*2+8*sin(datum.n)', as: 'u' },
    { calculate: '60-datum.n*1.5+10*cos(datum.n)', as: 'w' },
  ],
  repeat: { column: ['u', 'w'] },
  resolve: { scale: { y: 'independent' } },
  spec: w3_14_inner(),
});

// Collect every surviving `{repeat}` reference in a subtree.
function findRepeatRefs(node: any, acc: any[] = []): any[] {
  if (Array.isArray(node)) { node.forEach((c) => findRepeatRefs(c, acc)); return acc; }
  if (node && typeof node === 'object') {
    if (typeof node.repeat === 'string' && Object.keys(node).length === 1) acc.push(node);
    Object.values(node).forEach((v) => findRepeatRefs(v, acc));
  }
  return acc;
}

describe('D-315: repeat reference in a transform position', () => {
  it('detects a repeat ref inside a transform (the unsupported case)', () => {
    expect(specHasRepeatRefInTransform(w3_14_inner())).toBe(true);
  });

  it('does NOT flag an encoding-only repeat ref', () => {
    const encodingOnly = {
      layer: [{ mark: 'point', encoding: { y: { field: { repeat: 'column' } } } }],
    };
    expect(specHasRepeatRefInTransform(encodingOnly)).toBe(false);
  });

  it('expands the repeat operator into an explicit concat (regression here)', () => {
    const out = expandRepeatRefsInTransforms(w3_14_spec());
    // Repeat operator is gone; replaced by a concrete concat container.
    expect(out.repeat).toBeUndefined();
    expect(out.spec).toBeUndefined();
    expect(Array.isArray(out.hconcat)).toBe(true);
    expect(out.hconcat).toHaveLength(2); // one cell per repeated column: u, w
    // Container keeps data / top-level transform / resolve for inheritance.
    expect(out.data).toBeDefined();
    expect(out.resolve).toEqual({ scale: { y: 'independent' } });
  });

  it('substitutes EVERY repeat ref — including the one in the regression transform', () => {
    const out = expandRepeatRefsInTransforms(w3_14_spec());
    // Nothing must survive for Vega-Lite to mis-compile.
    expect(findRepeatRefs(out)).toHaveLength(0);
    // The regression field in cell 0 resolves to the concrete field "u".
    const cell0 = out.hconcat[0];
    const regLayer = cell0.layer.find((l: any) => Array.isArray(l.transform) && l.transform.some((t: any) => 'regression' in t));
    const regTransform = regLayer.transform.find((t: any) => 'regression' in t);
    expect(regTransform.regression).toBe('u');
    expect(regTransform.on).toBe('n');
    // And the point encoding field in the same cell resolves too.
    expect(cell0.layer[0].encoding.y.field).toBe('u');
    // Cell 1 resolves to "w".
    const cell1 = out.hconcat[1];
    expect(cell1.layer[0].encoding.y.field).toBe('w');
  });

  it('leaves an ordinary encoding-only repeat spec untouched (no over-reach)', () => {
    const ordinary = {
      repeat: { column: ['a', 'b'] },
      spec: { mark: 'point', encoding: { y: { field: { repeat: 'column' }, type: 'quantitative' } } },
    };
    const out = expandRepeatRefsInTransforms(ordinary);
    // Returned as-is: Vega-Lite handles encoding refs natively.
    expect(out).toBe(ordinary);
    expect(out.repeat).toBeDefined();
  });

  it('handles a row-only repeat with a transform ref (vconcat)', () => {
    const spec = {
      repeat: { row: ['p', 'q'] },
      spec: {
        transform: [{ regression: { repeat: 'row' }, on: 't' }],
        mark: 'line',
        encoding: { y: { field: { repeat: 'row' }, type: 'quantitative' } },
      },
    };
    const out = expandRepeatRefsInTransforms(spec);
    expect(Array.isArray(out.vconcat)).toBe(true);
    expect(out.vconcat).toHaveLength(2);
    expect(findRepeatRefs(out)).toHaveLength(0);
    expect(out.vconcat[0].transform[0].regression).toBe('p');
    expect(out.vconcat[1].transform[0].regression).toBe('q');
  });
});
