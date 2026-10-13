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
 * HISTORY: this suite first ran against minified helpers (`RC`, `CM`) pulled
 * out of the served bundle in templates/static/js, because the fix was
 * hot-patched into the compiled chunk while the source change "shipped as a
 * separate diff". That diff only half landed: resolveChordSpec reached the
 * source, but the square-padding sanitizer did not, so the next build would
 * have dropped the ragged-matrix fix. Minified names also change on every
 * build, which broke the suite outright. It now tests the source exports and
 * checks that the render path calls the sanitizer.
 */
import * as fs from 'fs';
import * as path from 'path';
import { resolveChordSpec, sanitizeChordMatrix } from '../chordPlugin';

const RC = resolveChordSpec;
const CM = sanitizeChordMatrix;

describe('chordPlugin render path uses sanitizeChordMatrix (seam)', () => {
  it('the matrix branch of render calls sanitizeChordMatrix', () => {
    const src = fs.readFileSync(path.join(__dirname, '..', 'chordPlugin.ts'), 'utf-8');
    expect(src).toContain('matrix = sanitizeChordMatrix(spec.matrix)');
    // No second, non-padding sanitizer left for render to call instead.
    expect(src).not.toMatch(/\bfunction sanitizeMatrix\(/);
  });
});

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
