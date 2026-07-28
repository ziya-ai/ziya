/**
 * Regression test for Issue 10 (chord renderer): two layered defects.
 *
 * 1. CONTRACT MISMATCH (total outage): the render_diagram tool wrapper always
 *    packs the caller's payload into `spec.definition` as a JSON STRING and
 *    never hoists `matrix`/`nodes`/`links` onto the top-level spec. The chord
 *    plugin's `isChordSpec` read those fields directly off `spec`, so EVERY
 *    chord spec submitted via render_diagram was invisible to the plugin:
 *    `findPluginForSpec` returned undefined and the D3Renderer orchestrator
 *    retried to a ~30-35s timeout with zero output. Fix: `resolveChordSpec`
 *    (minified `RC`) recovers structured fields from a JSON `definition`.
 *
 * 2. DEGENERATE MATRIX (silent geometry loss): once the plugin was selected,
 *    an adversarial matrix that is ragged (rows of unequal length), negative
 *    (-500), or non-finite fed `d3.chord()` and produced `<path d="MNaN,NaN…">`
 *    — invalid SVG geometry, so all ribbons/arcs vanished. Fix:
 *    `sanitizeChordMatrix` (minified `CM`) coerces the matrix into a square,
 *    finite, non-negative grid before layout.
 *
 * WHY THIS TEST READS THE BUNDLE: the shipped fix is a hot-patch applied to
 * the compiled chunk that the server actually serves (the source .ts change
 * ships as a separate diff for the next real build). To guard the SHIPPED
 * behavior — and to detect drift if a rebuild regenerates the bundle without
 * the patch — this test locates the served chord chunk, extracts the REAL
 * `RC` and `CM` helper bytes, executes them, and asserts their behavior. It
 * deliberately does NOT re-implement the logic locally (the pre-existing
 * chordPlugin.test.ts does, and therefore cannot detect drift). If the bundle
 * is rebuilt from the (diffed) source the same exported helpers survive and
 * this test continues to guard them.
 */
import * as fs from 'fs';
import * as path from 'path';

/** Locate the compiled chord chunk (19750.*.chunk.js) in a served static dir. */
function findChordBundle(): string {
  const candidates = [
    path.resolve(__dirname, '../../../../../templates/static/js'),
    path.resolve(__dirname, '../../../../templates/static/js'),
    path.resolve(process.cwd(), '../templates/static/js'),
    path.resolve(process.cwd(), 'templates/static/js'),
  ];
  for (const dir of candidates) {
    try {
      const hit = fs
        .readdirSync(dir)
        .find((f) => /^19750\..*\.chunk\.js$/.test(f));
      if (hit) {
        const full = path.join(dir, hit);
        if (fs.readFileSync(full, 'utf8').includes('chord-renderer')) return full;
      }
    } catch {
      /* dir missing — try next */
    }
  }
  throw new Error('chord bundle (19750.*.chunk.js) not found in any served static dir');
}

/** Extract a named `const <name>=function(...){...};` helper from bundle source. */
function extractHelper(src: string, name: string): (...a: any[]) => any {
  const marker = `const ${name}=function(`;
  const start = src.indexOf(marker);
  if (start === -1) throw new Error(`helper ${name} not found in bundle`);
  const end = src.indexOf('};', start) + 2;
  const code = src.slice(start, end).replace(`const ${name}=`, 'globalThis.__x=');
  // eslint-disable-next-line no-eval
  (0, eval)(code);
  return (globalThis as any).__x;
}

const bundle = fs.readFileSync(findChordBundle(), 'utf8');
const RC = extractHelper(bundle, 'RC'); // resolveChordSpec
const CM = extractHelper(bundle, 'CM'); // sanitizeChordMatrix

describe('resolveChordSpec (RC) — structured-input recovery from definition string', () => {
  it('recovers a matrix from a JSON definition string (render_diagram shape)', () => {
    const spec = {
      type: 'chord',
      definition: JSON.stringify({
        type: 'chord',
        directed: true,
        matrix: [[0, 5, 2], [3, 0, 1], [4, 2, 0]],
        names: ['A', 'B', 'C'],
      }),
      theme: 'light',
    };
    const out = RC(spec);
    expect(Array.isArray(out.matrix)).toBe(true);
    expect(out.matrix.length).toBe(3);
    expect(out.names).toEqual(['A', 'B', 'C']);
    expect(out.directed).toBe(true);
  });

  it('recovers nodes/links (links form) from a JSON definition string', () => {
    const spec = {
      type: 'chord',
      definition: JSON.stringify({
        type: 'chord',
        nodes: [{ id: 'A' }, { id: 'B' }],
        links: [{ source: 'A', target: 'B', value: 3 }],
      }),
    };
    const out = RC(spec);
    expect(Array.isArray(out.nodes)).toBe(true);
    expect(Array.isArray(out.links)).toBe(true);
  });

  // ── GUARD CASES: the recovery must NOT become a catch-all ──────────────
  it('returns an already-structured spec unchanged (identity)', () => {
    const spec = { type: 'chord', matrix: [[0, 1], [1, 0]], names: ['X', 'Y'] };
    expect(RC(spec)).toBe(spec);
  });

  it('leaves a non-JSON (line-DSL-ish) definition untouched', () => {
    const spec = { type: 'chord', definition: 'A -> B' };
    expect(RC(spec)).toBe(spec);
  });

  it('does NOT hoist a JSON definition lacking matrix/nodes/links', () => {
    const spec = { type: 'chord', definition: JSON.stringify({ type: 'chord', foo: 1 }) };
    expect(RC(spec)).toBe(spec); // no structured fields → not a chord payload
  });

  it('leaves malformed JSON untouched (no throw)', () => {
    const spec = { type: 'chord', definition: '{not valid json' };
    expect(RC(spec)).toBe(spec);
  });
});

describe('sanitizeChordMatrix (CM) — square/finite/non-negative coercion', () => {
  it('pads a ragged matrix to a square NxN grid (N = max dimension)', () => {
    // 3 rows, longest row has 3 cells, but last row is short (2 cells).
    const ragged = [[0, 1, 2], [3, 0, 4], [5, 6]];
    const out = CM(ragged);
    expect(out.length).toBe(3);
    expect(out.every((r: number[]) => r.length === 3)).toBe(true);
    expect(out[2][2]).toBe(0); // the missing cell filled with 0
  });

  it('replaces negative values with 0 (arc width cannot be negative)', () => {
    expect(CM([[0, -500], [5, 0]])).toEqual([[0, 0], [5, 0]]);
  });

  it('replaces non-finite values (Infinity/NaN) with 0', () => {
    expect(CM([[0, Infinity], [NaN, 0]])).toEqual([[0, 0], [0, 0]]);
  });

  it('preserves legitimate huge and microscopic finite positive values', () => {
    const out = CM([[0, 1e15], [1e-7, 0]]);
    expect(out[0][1]).toBe(1e15);
    expect(out[1][0]).toBe(1e-7);
  });

  it('coerces numeric strings, drops non-numeric to 0', () => {
    expect(CM([['3', 'x'], [0, 0]])).toEqual([[3, 0], [0, 0]]);
  });

  it('leaves a clean square non-negative matrix numerically unchanged', () => {
    expect(CM([[0, 8, 3], [5, 0, 4], [2, 6, 0]]))
      .toEqual([[0, 8, 3], [5, 0, 4], [2, 6, 0]]);
  });

  it('produces a matrix whose row sums are all finite for the adversarial grid', () => {
    // ragged + negative + non-finite + huge, all in one — the Issue-10 shape.
    const adversarial = [
      [0, -1, Infinity, 1e15],
      [NaN, 0, 5],            // ragged (3 of 4)
      [2, 3, 0, '7'],
      [1],                    // very ragged
    ];
    const out = CM(adversarial);
    expect(out.length).toBe(4);
    expect(out.every((r: number[]) => r.length === 4)).toBe(true);
    const rowSums = out.map((r: number[]) => r.reduce((a, b) => a + b, 0));
    expect(rowSums.every((s: number) => Number.isFinite(s))).toBe(true);
    // every cell finite & non-negative
    for (const row of out) for (const v of row) {
      expect(Number.isFinite(v)).toBe(true);
      expect(v).toBeGreaterThanOrEqual(0);
    }
  });
});
