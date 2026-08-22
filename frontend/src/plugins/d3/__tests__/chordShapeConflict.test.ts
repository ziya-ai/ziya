/**
 * Regression test for Issue 50 — chord CONFLICTING DUAL-SHAPE input.
 *
 * A chord spec may arrive in the matrix form (`matrix: number[][]`) OR the
 * links form (`nodes` + `links`). Some inputs supply BOTH at once. The
 * render path and `isChordSpec` both test `Array.isArray(spec.matrix)`
 * FIRST, so when both are present the matrix wins unconditionally and the
 * ENTIRE nodes/links structure (named nodes, colors, links) is SILENTLY
 * DROPPED — the diagram degenerates to bare numeric-index arcs (major
 * silent data loss).
 *
 * `resolveChordShapeConflict` drops the conflicting `matrix` when a NON-EMPTY
 * links form co-exists with it, so downstream shape-selection consistently
 * uses the higher-information links form. `resolveChordSpec` applies it up
 * front (and to a wrapped/parsed `definition`).
 *
 * This imports the REAL exported helpers (non-vacuous: `resolveChordShapeConflict`
 * did not exist pre-fix, so this import fails against pre-fix source), and
 * pins BOTH directions:
 *   - a both-present spec loses its matrix and keeps nodes/links, so
 *     resolveChordSpec routes to the links form, and
 *   - matrix-only / links-only / empty-nodes specs are returned REF-EQUAL
 *     (byte-identical) so the fix is not a catch-all.
 */
import {
  resolveChordShapeConflict,
  resolveChordSpec,
  normalizeChordCollections,
} from '../chordPlugin';

describe('resolveChordShapeConflict (Issue 50 — dual-shape precedence)', () => {
  const nodes = [{ id: 'Alpha', color: 'steelblue' }, { id: 'Beta' }, { id: 'Gamma' }];
  const links = [
    { source: 'Alpha', target: 'Beta', value: 50 },
    { source: 'Beta', target: 'Gamma', value: 20 },
  ];
  const matrix = [[0, 1, 5], [1, 0, -3], [5, -3, 0]];

  it('drops the matrix when a non-empty links form co-exists (the data-loss fix)', () => {
    const spec = { type: 'chord', nodes, links, matrix };
    const out = resolveChordShapeConflict(spec);
    expect(out.matrix).toBeUndefined();       // pre-fix: matrix retained + wins
    expect(out.nodes).toBe(nodes);            // links form preserved (ref-equal)
    expect(out.links).toBe(links);
    expect(out.type).toBe('chord');
  });

  it('routes a both-present spec through resolveChordSpec to the LINKS form', () => {
    const spec = { type: 'chord', nodes, links, matrix };
    const resolved = resolveChordSpec(spec);
    // The render branch checks Array.isArray(spec.matrix) FIRST; after the fix
    // there is no matrix, so the nodes/links branch (all 3 named nodes) runs.
    expect(Array.isArray(resolved.matrix)).toBe(false);
    expect(resolved.nodes).toEqual(nodes);
    expect(resolved.links).toEqual(links);
  });

  it('also resolves the conflict inside a wrapped definition-string spec', () => {
    const wrapped = {
      type: 'chord',
      definition: JSON.stringify({ nodes, links, matrix }),
    };
    const resolved = resolveChordSpec(wrapped);
    expect(Array.isArray(resolved.matrix)).toBe(false);
    expect(resolved.nodes).toEqual(nodes);
    expect(resolved.links).toEqual(links);
  });

  it('also handles the data.nodes / data.links nesting', () => {
    const spec = { type: 'chord', data: { nodes, links }, matrix };
    const out = resolveChordShapeConflict(spec);
    expect(out.matrix).toBeUndefined();
    expect(out.data.nodes).toBe(nodes);
  });

  // ---- guards: must remain byte-identical / ref-equal (not a catch-all) ----

  it('leaves a matrix-ONLY spec untouched (ref-equal)', () => {
    const spec = { type: 'chord', matrix, names: ['a', 'b', 'c'] };
    expect(resolveChordShapeConflict(spec)).toBe(spec);
  });

  it('leaves a links-ONLY spec untouched (ref-equal)', () => {
    const spec = { type: 'chord', nodes, links };
    expect(resolveChordShapeConflict(spec)).toBe(spec);
  });

  it('does NOT drop the matrix when nodes array is EMPTY (no real links form)', () => {
    const spec = { type: 'chord', nodes: [], links: [], matrix };
    // empty nodes -> matrix is the only usable data -> keep it, ref-equal
    expect(resolveChordShapeConflict(spec)).toBe(spec);
    expect(spec.matrix).toBe(matrix);
  });

  it('does NOT drop the matrix when links is absent (nodes without links is not a links form)', () => {
    const spec = { type: 'chord', nodes, matrix };
    expect(resolveChordShapeConflict(spec)).toBe(spec);
  });

  it('leaves a degenerate/empty matrix + links form alone (no matrix to win)', () => {
    const spec = { type: 'chord', nodes, links, matrix: [] };
    // hasMatrix is false (empty), so nothing to resolve -> ref-equal
    expect(resolveChordShapeConflict(spec)).toBe(spec);
  });

  it('tolerates non-object input', () => {
    expect(resolveChordShapeConflict(null)).toBeNull();
    expect(resolveChordShapeConflict(undefined)).toBeUndefined();
    expect(resolveChordShapeConflict('chord')).toBe('chord');
  });

  it('is idempotent (resolving twice is a no-op)', () => {
    const spec = { type: 'chord', nodes, links, matrix };
    const once = resolveChordShapeConflict(spec);
    const twice = resolveChordShapeConflict(once);
    expect(twice).toEqual(once);
    expect(twice.matrix).toBeUndefined();
  });
});

describe('normalizeChordCollections (Issue 50 — object-map nodes/links)', () => {
  it('converts an object-map `nodes` to an array with key-as-id', () => {
    const spec = {
      type: 'chord',
      nodes: { Alpha: { color: 'steelblue' }, Beta: { color: 'orange' } },
      links: [{ source: 'Alpha', target: 'Beta', value: 5 }],
    };
    const out = normalizeChordCollections(spec);
    expect(Array.isArray(out.nodes)).toBe(true);
    expect(out.nodes).toEqual([
      { color: 'steelblue', id: 'Alpha' },
      { color: 'orange', id: 'Beta' },
    ]);
    // the map KEY is forced as id even if the value carried a different id
    const clash = normalizeChordCollections({
      nodes: { Alpha: { id: 'WRONG', color: 'x' } },
    });
    expect(clash.nodes[0].id).toBe('Alpha');
  });

  it('converts an object-map `links` to its values array', () => {
    const out = normalizeChordCollections({
      nodes: [{ id: 'A' }, { id: 'B' }],
      links: { l1: { source: 'A', target: 'B', value: 3 } },
    });
    expect(out.links).toEqual([{ source: 'A', target: 'B', value: 3 }]);
  });

  it('normalizes the data.nodes / data.links nesting too', () => {
    const out = normalizeChordCollections({
      data: { nodes: { A: {}, B: {} }, links: [{ source: 'A', target: 'B' }] },
    });
    expect(out.data.nodes).toEqual([{ id: 'A' }, { id: 'B' }]);
  });

  it('leaves array-form nodes/links REF-EQUAL (not a catch-all)', () => {
    const spec = {
      type: 'chord',
      nodes: [{ id: 'A' }, { id: 'B' }],
      links: [{ source: 'A', target: 'B' }],
    };
    expect(normalizeChordCollections(spec)).toBe(spec);
  });

  it('leaves a matrix-only spec REF-EQUAL', () => {
    const spec = { type: 'chord', matrix: [[0, 1], [1, 0]] };
    expect(normalizeChordCollections(spec)).toBe(spec);
  });

  it('tolerates non-object input', () => {
    expect(normalizeChordCollections(null)).toBeNull();
    expect(normalizeChordCollections('chord')).toBe('chord');
  });
});

describe('Issue 50 end-to-end — object-map nodes + conflicting matrix', () => {
  // This is the ACTUAL adversarial shape: nodes keyed by id AND a 3x3 matrix.
  // Pre-fix: object-map nodes fail Array.isArray, the conflict resolver could
  // not fire, the matrix short-circuited, and all named nodes were dropped
  // (rendered as bare "0"/"1"/"2" arcs).
  const objMapSpec = {
    type: 'chord',
    nodes: {
      Alpha: { color: 'steelblue' },
      Beta: { color: 'orange' },
      Gamma: { color: 'green' },
    },
    links: [
      { source: 'Alpha', target: 'Beta', value: 50 },
      { source: 'Beta', target: 'Gamma', value: 20 },
    ],
    matrix: [[0, 1, 5], [1, 0, -3], [5, -3, 0]],
  };

  it('resolveChordSpec normalizes to array nodes AND drops the matrix', () => {
    const resolved = resolveChordSpec(objMapSpec);
    // matrix must be gone so the render path takes the named links branch
    expect(Array.isArray(resolved.matrix)).toBe(false);
    // nodes now an array carrying the real named identities
    expect(Array.isArray(resolved.nodes)).toBe(true);
    expect(resolved.nodes.map((n: any) => n.id)).toEqual(['Alpha', 'Beta', 'Gamma']);
    expect(resolved.links).toHaveLength(2);
  });

  it('resolves the same conflict inside a wrapped definition string', () => {
    const wrapped = { type: 'chord', definition: JSON.stringify(objMapSpec) };
    const resolved = resolveChordSpec(wrapped);
    expect(Array.isArray(resolved.matrix)).toBe(false);
    expect(resolved.nodes.map((n: any) => n.id)).toEqual(['Alpha', 'Beta', 'Gamma']);
  });
});
