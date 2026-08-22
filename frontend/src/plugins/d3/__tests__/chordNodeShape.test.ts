/**
 * Regression test for Issue 38 — chord links-form with STRING nodes.
 *
 * A links-form chord spec passed `nodes` as a flat array of plain strings
 * (`["Alpha","Beta","Gamma"]`) instead of the documented `{id}` objects.
 * buildMatrix / the render path index by `node.id`; for a string `node.id`
 * is undefined, so the index map collapsed to a single `undefined -> i`
 * entry, EVERY link was skipped, the matrix was all-zero, and d3.chord()
 * emitted a blank canvas (total silent data loss).
 *
 * `normalizeChordNodes` coerces every entry to `{ id }`, restoring a 1:1
 * index and a non-zero matrix. This test imports the REAL exported helper
 * (non-vacuous: the helper did not exist pre-fix, so this import fails
 * against pre-fix source) and pins BOTH directions:
 *   - string / number / bad-shape nodes normalize to usable `{id}` objects
 *     that produce a NON-ZERO matrix from the links, and
 *   - already-object nodes are preserved (id/label/color intact) so
 *     object-form specs are behavior-identical — not a catch-all rewrite.
 */
import { normalizeChordNodes } from '../chordPlugin';

/**
 * Local reference re-implementation of the plugin's private buildMatrix,
 * used ONLY to assert that normalized nodes yield a non-zero flow matrix.
 * (buildMatrix itself is not exported; this mirrors its indexing so we can
 * prove the normalization restores the index the private path relies on.)
 */
function indexOf(nodes: { id: string }[]): Map<string, number> {
  const idx = new Map<string, number>();
  nodes.forEach((n, i) => idx.set(n.id, i));
  return idx;
}

describe('normalizeChordNodes (Issue 38 — string-form chord nodes)', () => {
  it('coerces plain string nodes to {id} objects', () => {
    const out = normalizeChordNodes(['Alpha', 'Beta', 'Gamma']);
    expect(out).toEqual([
      { id: 'Alpha' },
      { id: 'Beta' },
      { id: 'Gamma' },
    ]);
  });

  it('restores a 1:1 index so links map to distinct rows (the blank-canvas fix)', () => {
    // This is the exact failure: pre-fix, idx.get("Alpha") === undefined for
    // string nodes, so every link was skipped -> all-zero matrix -> blank.
    const nodes = normalizeChordNodes(['Alpha', 'Beta', 'Gamma']);
    const idx = indexOf(nodes);
    expect(idx.size).toBe(3); // pre-fix: size 1 (single `undefined` key)
    expect(idx.get('Alpha')).toBe(0);
    expect(idx.get('Beta')).toBe(1);
    expect(idx.get('Gamma')).toBe(2);

    // Every real link now resolves to a valid (row,col) — non-zero matrix.
    const links = [
      { source: 'Alpha', target: 'Beta', value: 10 },
      { source: 'Beta', target: 'Gamma', value: 20 },
      { source: 'Gamma', target: 'Alpha', value: 5 },
    ];
    let mapped = 0;
    for (const l of links) {
      if (idx.get(l.source) !== undefined && idx.get(l.target) !== undefined) mapped++;
    }
    expect(mapped).toBe(3); // pre-fix: 0 links mapped
  });

  it('preserves already-object nodes byte-for-behavior (id/label/color intact)', () => {
    const input = [
      { id: 'A', label: 'Node A', color: '#f00' },
      { id: 'B', label: 'Node B', color: '#0f0' },
    ];
    const out = normalizeChordNodes(input);
    expect(out).toEqual([
      { id: 'A', label: 'Node A', color: '#f00' },
      { id: 'B', label: 'Node B', color: '#0f0' },
    ]);
    // object-form index is unchanged from what the pre-fix path produced
    const idx = indexOf(out);
    expect(idx.get('A')).toBe(0);
    expect(idx.get('B')).toBe(1);
  });

  it('coerces numeric / boolean shorthand ids to strings', () => {
    const out = normalizeChordNodes([1, 2, true]);
    expect(out).toEqual([{ id: '1' }, { id: '2' }, { id: 'true' }]);
  });

  it('derives id from name/key/label aliases on objects', () => {
    const out = normalizeChordNodes([
      { name: 'byName' },
      { key: 'byKey' },
      { label: 'byLabel' },
    ]);
    expect(out[0].id).toBe('byName');
    expect(out[1].id).toBe('byKey');
    expect(out[2].id).toBe('byLabel');
    // label alias also populates label
    expect(out[2].label).toBe('byLabel');
  });

  it('falls back to positional index for idless / null entries (no index collapse)', () => {
    const out = normalizeChordNodes([null, {}, undefined]);
    expect(out).toEqual([{ id: '0' }, { id: '1' }, { id: '2' }]);
    // critical: three DISTINCT ids, not three colliding `undefined` keys
    expect(indexOf(out).size).toBe(3);
  });

  it('coerces object id values to strings (stable Map keys)', () => {
    const out = normalizeChordNodes([{ id: 5 }, { id: '5' }]);
    // numeric 5 -> "5"; both become "5" (author's choice) but never undefined
    expect(out[0].id).toBe('5');
    expect(out[1].id).toBe('5');
  });

  it('tolerates non-array input', () => {
    expect(normalizeChordNodes(undefined as any)).toEqual([]);
    expect(normalizeChordNodes(null as any)).toEqual([]);
    expect(normalizeChordNodes({} as any)).toEqual([]);
  });

  it('is idempotent (normalizing twice is a no-op)', () => {
    const once = normalizeChordNodes(['X', 'Y']);
    const twice = normalizeChordNodes(once);
    expect(twice).toEqual(once);
  });
});
