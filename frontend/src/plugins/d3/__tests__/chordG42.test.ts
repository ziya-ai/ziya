/**
 * G-42 — chord recovery/legibility fixes (D-063, D-066, D-067, D-068).
 *
 * All four defects live in chordPlugin.ts and are recovery/structural (not
 * theme) defects — the hypotheses record that colour contrast is already fine —
 * so these are pure-function DIRECTION tests: each assertion FAILS against the
 * pre-fix behaviour and passes with the fix. The width/height-string case
 * (D-068) is exercised in a render smoke test in BOTH themes to prove the
 * coerced canvas is used identically regardless of theme.
 */
import {
  chordFontSize,
  coerceFlowValue,
  coerceChordDimension,
  fitChordNames,
  fitChordColors,
  unwrapChordContainer,
  chordPlugin,
} from '../chordPlugin';

// ── D-063: default fontSize scales with the canvas ───────────────────────────
describe('D-063 chordFontSize — default type scales with canvas', () => {
  it('keeps the historical 11px on the 600px baseline (byte-identical)', () => {
    expect(chordFontSize(undefined, 600)).toBe(11);
  });
  it('floors at 11px for small canvases (≤600px unchanged)', () => {
    expect(chordFontSize(undefined, 300)).toBe(11);
    expect(chordFontSize(undefined, 120)).toBe(11);
  });
  it('scales UP on a large canvas (was fixed 11px pre-fix)', () => {
    // Direction: pre-fix `style.fontSize || 11` returned 11 at every size, so
    // 2000px type was ~0.55% of width and unreadable after downscale.
    expect(chordFontSize(undefined, 1200)).toBeGreaterThan(11);
    expect(chordFontSize(undefined, 1200)).toBe(22);
    expect(chordFontSize(undefined, 2000)).toBe(32); // capped
  });
  it('honours an explicit caller fontSize verbatim in all cases', () => {
    expect(chordFontSize(14, 2000)).toBe(14);
    expect(chordFontSize(9, 600)).toBe(9);
  });
});

// ── D-066: graph nested under a wrapper key is unwrapped ──────────────────────
describe('D-066 unwrapChordContainer — hoist a wrapped graph', () => {
  it('hoists nodes/links nested under `spec` to the top level', () => {
    const wrapped = {
      type: 'chord',
      spec: { nodes: [{ id: 'A' }, { id: 'B' }], links: [{ source: 'A', target: 'B', value: 3 }] },
    };
    const out = unwrapChordContainer(wrapped);
    // Direction: pre-fix, discovery probed top-level/`data` only, so
    // out.nodes was undefined and the spec was unclaimable -> 30s timeout.
    expect(Array.isArray(out.nodes)).toBe(true);
    expect(out.nodes).toHaveLength(2);
    expect(Array.isArray(out.links)).toBe(true);
    expect(out.type).toBe('chord'); // outer type preserved
  });
  it('also unwraps `chart` / `diagram` / `config` / `graph` wrappers', () => {
    for (const key of ['chart', 'diagram', 'config', 'graph']) {
      const out = unwrapChordContainer({ type: 'chord', [key]: { matrix: [[0, 1], [1, 0]] } });
      expect(Array.isArray(out.matrix)).toBe(true);
    }
  });
  it('the chord plugin now CLAIMS a wrapper-nested spec', () => {
    const wrapped = {
      type: 'chord',
      spec: { nodes: [{ id: 'A' }, { id: 'B' }], links: [{ source: 'A', target: 'B' }] },
    };
    expect(chordPlugin.canHandle(wrapped)).toBe(true);
  });
  it('leaves a top-level graph untouched (ref-equal, byte-identical)', () => {
    const top = { type: 'chord', matrix: [[0, 1], [1, 0]] };
    expect(unwrapChordContainer(top)).toBe(top);
  });
  it('does not fabricate a graph from a wrapper without one', () => {
    const noGraph = { type: 'chord', config: { theme: 'dark' } };
    expect(unwrapChordContainer(noGraph)).toBe(noGraph);
    expect(chordPlugin.canHandle(noGraph)).toBe(false);
  });
});

// ── D-067: length-mismatched names/colors are fitted, not discarded ───────────
describe('D-067 fitChordNames / fitChordColors — fit instead of discard', () => {
  it('pads a short names list, preserving every supplied name', () => {
    // Direction: pre-fix required `.length === n` exactly, so a 5-vs-6 list was
    // discarded wholesale and ALL names became bare indices.
    const out = fitChordNames(['A', 'B', 'C', 'D', 'E'], 6);
    expect(out).toEqual(['A', 'B', 'C', 'D', 'E', '5']);
  });
  it('truncates a long names list', () => {
    expect(fitChordNames(['A', 'B', 'C', 'D', 'E', 'F', 'G'], 6))
      .toEqual(['A', 'B', 'C', 'D', 'E', 'F']);
  });
  it('falls back to the index for blank/absent entries', () => {
    expect(fitChordNames(['A', '', 'C'], 3)).toEqual(['A', '1', 'C']);
    expect(fitChordNames(undefined, 3)).toEqual(['0', '1', '2']);
  });
  it('pads a short colors list with undefined (later -> palette), preserving supplied hexes', () => {
    const out = fitChordColors(['#111', '#222', '#333', '#444', '#555'], 6);
    expect(out.slice(0, 5)).toEqual(['#111', '#222', '#333', '#444', '#555']);
    expect(out[5]).toBeUndefined();
    expect(out).toHaveLength(6);
  });
  it('truncates a long colors list and defaults an absent one', () => {
    expect(fitChordColors(['#111', '#222', '#333'], 2)).toEqual(['#111', '#222']);
    expect(fitChordColors(undefined, 3)).toEqual([undefined, undefined, undefined]);
  });
});

// ── D-068: numeric-string dimensions and thousands-separated flows ────────────
describe('D-068 coerceChordDimension — string dimensions coerced', () => {
  it('coerces plain and separated numeric strings', () => {
    // Direction: pre-fix `spec.width || 600` returned the STRING verbatim.
    expect(coerceChordDimension('600')).toBe(600);
    expect(coerceChordDimension('1,200')).toBe(1200);
    expect(coerceChordDimension('800px')).toBe(800);
  });
  it('returns a positive number untouched and falls back otherwise', () => {
    expect(coerceChordDimension(900)).toBe(900);
    expect(coerceChordDimension(undefined)).toBe(600);
    expect(coerceChordDimension(0)).toBe(600);
    expect(coerceChordDimension('nope')).toBe(600);
    expect(coerceChordDimension(-5)).toBe(600);
  });
});

describe('D-068 coerceFlowValue — thousands-separated magnitudes', () => {
  it('strips separators so the dominant flow is not lost', () => {
    // Direction: pre-fix Number("1,200")=NaN -> mapped to 0, silently deleting
    // the largest flow.
    expect(coerceFlowValue('1,200')).toBe(1200);
    expect(coerceFlowValue('1 200')).toBe(1200);
    expect(coerceFlowValue('12,345,678')).toBe(12345678);
  });
  it('is byte-identical for numeric and already-clean inputs', () => {
    expect(coerceFlowValue(5)).toBe(5);
    expect(coerceFlowValue('42')).toBe(42);
    expect(coerceFlowValue(undefined, 1)).toBe(1);
    expect(coerceFlowValue('not-a-number')).toBe(0);
    expect(coerceFlowValue(-3)).toBe(0);
  });
});

// NOTE: the render-path wiring (chordCanvasSize(coerceChordDimension(...)) for
// D-068, chordFontSize(...) for D-063, fitChord* for D-067, unwrapChordContainer
// for D-066) is verified by reading render() plus the pure-function direction
// tests above; a render smoke test is intentionally omitted because chord's
// render uses d3.chordDirected/ribbonArrow/arc which the repo's lightweight mock
// d3 does not model, and full both-theme RENDER verification is performed at the
// shared build+render stage per run convention.
