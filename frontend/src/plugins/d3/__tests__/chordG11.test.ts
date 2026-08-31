/**
 * G-11 — chord plugin (shared file: chordPlugin.ts, part 2).
 *
 * Defects covered:
 *   D-058 (structural)  custom spec.height was clipped: the plugin declared
 *         sizingConfig.needsDynamicHeight = false, which drives D3Renderer to
 *         pin the container to a fixed pixel height (`height: '${height}px'`,
 *         `maxHeight: 'unset'`) with overflow:hidden — so an SVG taller than the
 *         container was shaved. Now `true`, so the container shrink-wraps the
 *         SVG (height:auto / maxHeight:none) and a custom height survives.
 *   D-059 (structural)  outer radius `min(w,h)*0.5 - 60` went NEGATIVE below a
 *         ~120px canvas, feeding d3.arc()/ribbon() a negative radius -> NaN
 *         paths -> empty canvas / render timeout. chordRadii() now floors the
 *         outer radius at 10 and inner at 1 (always inside outer), leaving every
 *         normal-size canvas byte-identical.
 *   D-064 (recovery)   a wrapped `definition` string was parsed with a bare
 *         strict JSON.parse whose catch returned the spec unchanged, so any
 *         JSON5 dialect slip (trailing comma / unquoted keys / single quotes /
 *         smart quotes) left the spec unclaimable -> "No plugin found" -> 30s
 *         timeout. Now recovered via the shared lenientParseObject (JSON5).
 *   D-065 (recovery)   the same path bailed when the first non-space char was
 *         not '{', rejecting a markdown-fenced definition outright. The fence is
 *         now stripped before parse.
 *
 * Direction: every recovery assertion first shows the raw definition is NOT
 * strict-JSON-parseable (or is fence-guarded), so it fails against the pre-fix
 * path; D-059 pairs the clamped radius against the negative value the old
 * arithmetic produced; D-058 asserts the exact flag the pre-fix config lacked.
 * Guard cases pin the REJECTION direction so recovery is not a catch-all.
 */
import {
  chordPlugin,
  resolveChordSpec,
  chordRadii,
} from '../chordPlugin';

const linksBody = {
  type: 'chord',
  directed: true,
  nodes: ['A', 'B', 'C'],
  links: [
    { source: 'A', target: 'B', value: 5 },
    { source: 'B', target: 'C', value: 3 },
  ],
};

const matrixBody = {
  type: 'chord',
  matrix: [[0, 5, 2], [3, 0, 1], [4, 2, 0]],
  names: ['A', 'B', 'C'],
};

// ---------------------------------------------------------------------------
// D-058 — custom height no longer clipped by a fixed container
// ---------------------------------------------------------------------------
describe('D-058 sizingConfig.needsDynamicHeight — container follows the SVG height', () => {
  it('needsDynamicHeight is true (drives D3Renderer to height:auto / maxHeight:none)', () => {
    // Pre-fix this was `false`, so D3Renderer set the container to a FIXED
    // `${height}px` with maxHeight:'unset' and overflow:hidden, clipping any
    // taller custom SVG. `true` makes it height:auto / maxHeight:none.
    expect(chordPlugin.sizingConfig?.needsDynamicHeight).toBe(true);
  });

  it('the fixed strategy and hidden-overflow default are otherwise preserved (minimal change)', () => {
    expect(chordPlugin.sizingConfig?.sizingStrategy).toBe('fixed');
    expect(chordPlugin.sizingConfig?.containerStyles?.overflow).toBe('hidden');
  });
});

// ---------------------------------------------------------------------------
// D-059 — radii clamped so a tiny canvas never goes negative
// ---------------------------------------------------------------------------
describe('D-059 chordRadii — never negative on a small canvas', () => {
  it('normal-size canvas is byte-identical to the old arithmetic', () => {
    // Old: outer = min*0.5-60, inner = outer-18.
    expect(chordRadii(600, 600)).toEqual({ outerRadius: 240, innerRadius: 222 });
    expect(chordRadii(300, 400)).toEqual({ outerRadius: 90, innerRadius: 72 });
  });

  it('a <120px canvas produced a NEGATIVE outer radius under the old formula (regression baseline)', () => {
    // This is exactly the value the pre-fix code fed to d3.arc() -> NaN paths.
    expect(Math.min(100, 100) * 0.5 - 60).toBe(-10);
    expect(Math.min(80, 60) * 0.5 - 60).toBeLessThan(0);
  });

  it('clamps a tiny canvas to strictly-positive radii with inner < outer', () => {
    for (const [w, h] of [[100, 100], [80, 60], [40, 40], [1, 1]] as const) {
      const { outerRadius, innerRadius } = chordRadii(w, h);
      expect(outerRadius).toBeGreaterThan(0);
      expect(innerRadius).toBeGreaterThan(0);
      expect(innerRadius).toBeLessThan(outerRadius);
    }
    expect(chordRadii(100, 100)).toEqual({ outerRadius: 10, innerRadius: 1 });
  });
});

// ---------------------------------------------------------------------------
// D-064 / D-065 — lenient + fence-tolerant definition recovery
// ---------------------------------------------------------------------------
describe('D-065 markdown-fenced definition is recovered', () => {
  it('recovers a links-form spec wrapped in a ```json fence (old code bailed on the leading backtick)', () => {
    const definition = '```json\n' + JSON.stringify(linksBody) + '\n```';
    // Direction: the fenced body does NOT begin with '{' so the old guard
    // `definition.trimStart()[0] !== '{'` rejected it outright.
    expect(definition.trimStart()[0]).not.toBe('{');
    const resolved = resolveChordSpec({ type: 'chord', definition });
    expect(Array.isArray(resolved.nodes)).toBe(true);
    expect(resolved.nodes).toHaveLength(3);
    expect(chordPlugin.canHandle({ type: 'chord', definition })).toBe(true);
  });

  it('recovers a matrix-form spec wrapped in a bare (no-lang) fence', () => {
    const definition = '```\n' + JSON.stringify(matrixBody) + '\n```';
    const resolved = resolveChordSpec({ type: 'chord', definition });
    expect(Array.isArray(resolved.matrix)).toBe(true);
    expect(resolved.names).toEqual(['A', 'B', 'C']);
  });
});

describe('D-064 JSON5-dialect definition is recovered', () => {
  it('recovers a matrix definition with a trailing comma + unquoted keys + single quotes', () => {
    const definition = "{ type: 'chord', matrix: [[0,5],[3,0],], names: ['A','B'], }";
    // Direction: strict JSON.parse throws on this, so the old bare parse
    // returned the spec unchanged (unclaimable -> timeout).
    expect(() => JSON.parse(definition)).toThrow();
    const resolved = resolveChordSpec({ type: 'chord', definition });
    expect(Array.isArray(resolved.matrix)).toBe(true);
    expect(resolved.matrix).toHaveLength(2);
    expect(resolved.names).toEqual(['A', 'B']);
    expect(chordPlugin.canHandle({ type: 'chord', definition })).toBe(true);
  });

  it('recovers a definition using smart/curly quotes (json5 alone rejects them)', () => {
    const definition = '{ \u201Ctype\u201D: \u201Cchord\u201D, \u201Cmatrix\u201D: [[0,1],[1,0]] }';
    expect(() => JSON.parse(definition)).toThrow();
    const resolved = resolveChordSpec({ type: 'chord', definition });
    expect(Array.isArray(resolved.matrix)).toBe(true);
    expect(resolved.matrix).toHaveLength(2);
  });

  it('recovers a fenced JSON5 definition (D-064 + D-065 compounded)', () => {
    const definition = '```json\n{ type: "chord", nodes: ["A","B"], links: [{source:"A",target:"B",value:2},], }\n```';
    const resolved = resolveChordSpec({ type: 'chord', definition });
    expect(Array.isArray(resolved.nodes)).toBe(true);
    expect(resolved.nodes).toHaveLength(2);
    expect(chordPlugin.canHandle({ type: 'chord', definition })).toBe(true);
  });
});

describe('recovery guards — still rejects non-chord / non-JSON (not a catch-all)', () => {
  it('a non-JSON definition is left untouched and declined', () => {
    const wrapped = { type: 'chord', definition: 'graph TD; A-->B' };
    expect(resolveChordSpec(wrapped)).toBe(wrapped);
    expect(chordPlugin.canHandle(wrapped)).toBe(false);
  });

  it('a fenced but non-chord JSON definition is declined', () => {
    const wrapped = { type: 'chord', definition: '```json\n{ "foo": "bar" }\n```' };
    expect(chordPlugin.canHandle(wrapped)).toBe(false);
  });

  it('a non-chord TYPE carrying chord content in its definition is still declined', () => {
    const wrapped = { type: 'mermaid', definition: '```json\n' + JSON.stringify(linksBody) + '\n```' };
    expect(chordPlugin.canHandle(wrapped)).toBe(false);
  });

  it('an empty / whitespace definition is left untouched', () => {
    const wrapped = { type: 'chord', definition: '   ' };
    expect(resolveChordSpec(wrapped)).toBe(wrapped);
    expect(chordPlugin.canHandle(wrapped)).toBe(false);
  });
});
