/**
 * @jest-environment jsdom
 *
 * Tests for pitchless-entry guards and per-staff autoBeam.
 *
 * All three cases here were found by probing a real score that had silently
 * come out wrong, and two of them are HANGS rather than wrong output:
 *
 *   1. A note with no `keys` and no `rest: true` interpolated the literal
 *      token "undefined/q" (`keys[0]` of an empty array stringifies as text).
 *      EasyScore cannot parse it into a pitch, the note gets a NaN position,
 *      and the formatter's justification loop never returns -- a 30s timeout
 *      with a blank canvas and no error, losing the entire score.
 *   2. A grace note with `keys: []` hangs the same way inside
 *      GraceNoteGroup's pre-format loop.
 *   3. `autoBeam` on a `staves[]` entry was read only from the spec root, so
 *      it was silently ignored -- a multi-staff score drew every sixteenth
 *      with an individual flag and gave no clue why.
 *
 * A hang cannot be asserted directly (it exhausts the test timeout rather than
 * failing), so these assert the POSITIVE outcome the guard produces: a rest
 * glyph, a surviving main note, a beam. Each would also fail -- by timing out
 * -- if its guard were removed, which is the regression that matters.
 */

// Polyfill structuredClone for jest's jsdom environment: vexflow 5.0.0 uses
// it in metrics.getFontInfo, and jest's jsdom global does not expose it on
// Node 20 (a plain-data font-metrics clone, so JSON round-trip suffices).
if (typeof (globalThis as any).structuredClone !== 'function') {
  (globalThis as any).structuredClone = (v: any) =>
    (v === undefined ? undefined : JSON.parse(JSON.stringify(v)));
}

import { buildNoteString, renderMusicSpec, type MusicSpec } from '../musicPlugin';

const makeChain = () => {
  const chain: any = {};
  for (const m of ['attr', 'style', 'text', 'append', 'classed', 'html']) {
    chain[m] = () => chain;
  }
  return chain;
};
const d3Stub = { select: () => makeChain() };

const draw = async (spec: MusicSpec) => {
  const container = document.createElement('div');
  document.body.appendChild(container);
  await renderMusicSpec(container, spec, false, d3Stub);
  return container;
};

const glyphs = (c: HTMLElement) =>
  Array.from(c.querySelectorAll('text')).map((t) => t.textContent ?? '').join('');
/** Rest glyphs: whole E4E3 .. 16th E4E7. */
const restCount = (c: HTMLElement) => (glyphs(c).match(/[\ue4e0-\ue4ef]/g) ?? []).length;
/** Noteheads, to prove a rest did not silently become a note. */
const headCount = (c: HTMLElement) => (glyphs(c).match(/[\ue0a2\ue0a3\ue0a4]/g) ?? []).length;
/** Flag glyphs: present only on an UNBEAMED eighth or shorter. */
const flagCount = (c: HTMLElement) => (glyphs(c).match(/[\ue240-\ue24f]/g) ?? []).length;

const SIXTEEN_16THS = Array.from({ length: 16 }, () => ({ keys: ['e/5'], duration: '16' }));

describe('pitchless note entries', () => {
  it('emits a rest token for an entry with no keys', () => {
    // The bug was a literal "undefined/q" here.
    expect(buildNoteString([{ duration: 'q' } as any], 'treble'))
      .toBe('B4/q/r');
  });

  it('emits a rest token for an entry with an empty keys array', () => {
    expect(buildNoteString([{ keys: [], duration: 'h' } as any], 'treble'))
      .toBe('B4/h/r');
  });

  it('never emits the string "undefined" for any pitchless shape', () => {
    for (const n of [{ duration: 'q' }, { keys: [], duration: '8' }]) {
      expect(buildNoteString([n as any], 'treble')).not.toContain('undefined');
    }
  });

  it('keeps the clef-appropriate rest pitch for a keyless entry', () => {
    expect(buildNoteString([{ duration: 'q' } as any], 'bass')).toBe('D3/q/r');
  });

  it('carries dots after the /r suffix for a keyless entry', () => {
    // "B4/q./r" would parse as a NOTE; the dot must follow /r.
    expect(buildNoteString([{ duration: 'q.' } as any], 'treble')).toBe('B4/q/r.');
  });

  it('renders a keyless entry as a rest, not a notehead', async () => {
    const c = await draw({ type: 'music', timeSignature: '4/4', notes: [
      { keys: ['c/5'], duration: 'q' },
      { duration: 'q' } as any,
      { keys: ['e/5'], duration: 'h' },
    ] });
    expect(restCount(c)).toBe(1);
    expect(headCount(c)).toBe(2);
  });

  it('leaves an explicit rest:true entry unchanged', () => {
    // The widened condition must not alter the documented spelling.
    expect(buildNoteString([{ rest: true, duration: 'q' }], 'treble'))
      .toBe('B4/q/r');
  });

  it('still renders ordinary pitched notes normally', () => {
    expect(buildNoteString([{ keys: ['c/5'], duration: 'q' }], 'treble'))
      .toBe('C5/q');
  });
});

describe('pitchless grace notes', () => {
  it('drops a keyless grace note but keeps its main note', async () => {
    const c = await draw({ type: 'music', timeSignature: '4/4', notes: [
      { keys: ['c/5'], duration: 'q', graceNotes: [{ keys: [], duration: '8' } as any] },
      { keys: ['d/5'], duration: 'q' },
      { keys: ['e/5'], duration: 'h' },
    ] });
    // Three main noteheads survive; the empty grace contributes none.
    expect(headCount(c)).toBe(3);
  });

  it('keeps the playable graces when only some are keyless', async () => {
    const c = await draw({ type: 'music', timeSignature: '4/4', notes: [
      { keys: ['c/5'], duration: 'h', graceNotes: [
        { keys: [], duration: '16' } as any,
        { keys: ['b/4'], duration: '16' },
      ] },
      { keys: ['e/5'], duration: 'h' },
    ] });
    // Two mains + the one surviving grace.
    expect(headCount(c)).toBe(3);
  });
});

describe('per-staff autoBeam', () => {
  it('beams a staff that sets autoBeam itself', async () => {
    const c = await draw({ type: 'music', timeSignature: '4/4', staves: [
      { clef: 'treble', autoBeam: true, notes: SIXTEEN_16THS },
    ] });
    expect(flagCount(c)).toBe(0);
  });

  it('still honours the spec-level flag for staves that set nothing', async () => {
    const c = await draw({ type: 'music', timeSignature: '4/4', autoBeam: true, staves: [
      { clef: 'treble', notes: SIXTEEN_16THS },
      { clef: 'bass', notes: SIXTEEN_16THS },
    ] });
    expect(flagCount(c)).toBe(0);
  });

  it('lets a staff opt OUT with autoBeam:false', async () => {
    // The per-staff value wins over the spec's, including when it is false.
    const c = await draw({ type: 'music', timeSignature: '4/4', autoBeam: true, staves: [
      { clef: 'treble', autoBeam: false, notes: SIXTEEN_16THS },
    ] });
    expect(flagCount(c)).toBe(16);
  });

  it('leaves a staff flagged when neither level sets the flag', async () => {
    const c = await draw({ type: 'music', timeSignature: '4/4', staves: [
      { clef: 'treble', notes: SIXTEEN_16THS },
    ] });
    expect(flagCount(c)).toBe(16);
  });

  it('prefers the staff beamGroups over the spec ones', async () => {
    // 6/8 in two groups of three rather than the meter default: assert only
    // that beaming happened, since group BOUNDARIES are not glyph-visible.
    const c = await draw({ type: 'music', timeSignature: '6/8',
      autoBeam: true, beamGroups: [[1, 8]], staves: [
      { clef: 'treble', beamGroups: [[3, 8]],
        notes: Array.from({ length: 6 }, () => ({ keys: ['e/5'], duration: '8' })) },
    ] });
    expect(flagCount(c)).toBe(0);
  });
});
