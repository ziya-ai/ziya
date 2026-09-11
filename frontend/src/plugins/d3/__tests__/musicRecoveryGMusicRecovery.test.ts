/**
 * @jest-environment jsdom
 *
 * Regression tests for fix group G-MUSIC-RECOVERY — the music recovery gaps
 * that turned a near-miss LLM spec into a 30s hang or a silently-wrong render.
 *
 * Covered here (the members NOT already certified by musicAliasNormalizationG34
 * and musicRecoveryNormalize):
 *   - D-145 (music-w3-09): a well-formed but ZERO-CONTENT music spec (title +
 *     empty measures/notes/staves) was unclaimed -> ~30s no-plugin timeout.
 *     The plugin now CLAIMS it (canHandle) and draws a titled blank staff.
 *   - D-152 (music-w4-12): a SCALAR `keys` string is coerced to an array so the
 *     draw path no longer iterates it character-by-character and hangs.
 *   - D-153 (music-w4-08): a scalar `tempo` (`120` / `"120"`) is lifted to
 *     `{ bpm }` at the tempo block (coerceTempoSpec) so the metronome mark
 *     renders instead of being dropped.
 *
 * Each assertion FAILS against the unpatched source: coerceTempoSpec /
 * degenerateMusicBody / isEmptyMusicShape do not exist there (import is
 * undefined), the scalar-keys coercion is absent, and canHandle rejects the
 * empty spec.  D-145/152/153 are structural/recovery (theme-independent
 * transforms); the D-145 render is asserted in BOTH themes.
 */

// vexflow 5.0.0 uses structuredClone in metrics.getFontInfo; jest's jsdom on
// Node 20 does not expose it (matches musicPlugin.test.ts).
if (typeof (globalThis as any).structuredClone !== 'function') {
  (globalThis as any).structuredClone = (v: any) =>
    (v === undefined ? undefined : JSON.parse(JSON.stringify(v)));
}

import {
  coerceTempoSpec,
  degenerateMusicBody,
  isEmptyMusicShape,
  normalizeMusicNote,
  resolveMusicSpec,
} from '../../../utils/d3Plugins/musicPlugin';
import { musicPlugin } from '../musicPlugin';

// The w3-09 wave spec: valid music, zero content, title present.
const EMPTY_BODY = {
  type: 'music',
  clef: 'treble',
  keySignature: 'C',
  timeSignature: '4/4',
  title: 'Empty',
  measures: [],
  notes: [],
  slurs: [],
  ties: [],
  hairpins: [],
  staves: [],
};
const wrapObj = (definition: object) => ({ type: 'music', definition });
const wrapStr = (body: object) => ({ type: 'music', definition: JSON.stringify(body) });

describe('D-153 — coerceTempoSpec lifts a scalar tempo to { bpm }', () => {
  it('lifts a number', () => {
    expect(coerceTempoSpec(120)).toEqual({ bpm: 120 });
  });
  it('lifts a numeric string', () => {
    expect(coerceTempoSpec('120')).toEqual({ bpm: 120 });
  });
  it('leaves a tempo object unchanged', () => {
    const obj = { name: 'Allegro', bpm: 132, duration: 'q' };
    expect(coerceTempoSpec(obj)).toBe(obj);
  });
  it('yields no bpm for a non-positive / non-numeric scalar', () => {
    expect(coerceTempoSpec(0)).toEqual({});
    expect(coerceTempoSpec('fast')).toEqual({});
    expect(coerceTempoSpec(-40)).toEqual({});
  });
});

describe('D-152 — a scalar keys string is coerced to an array', () => {
  it('normalizeMusicNote wraps a bare keys string', () => {
    expect(normalizeMusicNote({ keys: 'c/4', duration: 'q' }).keys).toEqual(['c/4']);
  });
  it('resolveMusicSpec coerces scalar keys through the recovery path (w4-12)', () => {
    const r = resolveMusicSpec(wrapObj({
      timeSignature: '4/4', clef: 'treble',
      notes: [{ keys: 'c/4', duration: 'quarter' }],
    }));
    expect(Array.isArray(r.notes[0].keys)).toBe(true);
    expect(r.notes[0].keys).toEqual(['c/4']);
    expect(r.notes[0].duration).toBe('q'); // long-form name mapped too
  });
});

describe('D-145 — degenerate zero-content music spec', () => {
  it('isEmptyMusicShape recognises a music-shaped body with no content', () => {
    expect(isEmptyMusicShape(EMPTY_BODY)).toBe(true);
    expect(isEmptyMusicShape({ notes: [] })).toBe(true);
    // A body with real content is NOT empty.
    expect(isEmptyMusicShape({ notes: [{ keys: ['c/4'], duration: 'q' }] })).toBe(false);
    // A non-music object (no music structural keys) is not claimed.
    expect(isEmptyMusicShape({ nodes: [], links: [] })).toBe(false);
  });

  it('degenerateMusicBody resolves the empty body from an object OR string wrapper', () => {
    expect(degenerateMusicBody(wrapObj(EMPTY_BODY))).not.toBeNull();
    expect(degenerateMusicBody(wrapStr(EMPTY_BODY))).not.toBeNull();
    // A content spec is not degenerate; a non-music wrapper is not claimed.
    expect(degenerateMusicBody(wrapObj({ notes: [{ keys: ['c/4'], duration: 'q' }] }))).toBeNull();
    expect(degenerateMusicBody(wrapStr({ nodes: [{ id: 'a' }], links: [] }))).toBeNull();
  });

  it('resolveMusicSpec still does NOT claim the empty spec (no hijack contract)', () => {
    // The empty body must stay unclaimed by resolveMusicSpec/isMusicSpec; the
    // claim happens only at canHandle so render() can draw a placeholder.
    const w = wrapObj(EMPTY_BODY);
    expect(resolveMusicSpec(w)).toBe(w);
  });

  it('canHandle CLAIMS the empty music spec (was rejected -> 30s timeout)', () => {
    expect(musicPlugin.canHandle(wrapObj(EMPTY_BODY))).toBe(true);
    expect(musicPlugin.canHandle(wrapStr(EMPTY_BODY))).toBe(true);
  });

  it('canHandle still REJECTS a non-music wrapper (no hijack)', () => {
    expect(musicPlugin.canHandle({
      type: 'network',
      definition: JSON.stringify({ nodes: [{ id: 'a' }], links: [] }),
    })).toBe(false);
  });

  const d3Stub = { select: () => ({} as any) };

  const renderEmpty = async (isDarkMode: boolean) => {
    const container = document.createElement('div');
    document.body.appendChild(container);
    await musicPlugin.render(container, d3Stub, wrapObj(EMPTY_BODY), isDarkMode);
    return container;
  };

  it('renders a titled blank staff (svg, not an error card) in LIGHT', async () => {
    const c = await renderEmpty(false);
    const svg = c.querySelector('svg');
    expect(svg).not.toBeNull();
    // Not the red error card (which carries data-diagram-error).
    expect(c.querySelector('[data-diagram-error]')).toBeNull();
    // Title is drawn, and there is a staff (>=1 line).
    expect(c.textContent).toContain('Empty');
    expect(c.querySelectorAll('line').length).toBeGreaterThanOrEqual(5);
  });

  it('renders a titled blank staff (svg, not an error card) in DARK', async () => {
    const c = await renderEmpty(true);
    expect(c.querySelector('svg')).not.toBeNull();
    expect(c.querySelector('[data-diagram-error]')).toBeNull();
    expect(c.textContent).toContain('Empty');
    expect(c.querySelectorAll('line').length).toBeGreaterThanOrEqual(5);
  });
});
