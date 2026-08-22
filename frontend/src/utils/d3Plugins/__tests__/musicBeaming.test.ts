/**
 * @jest-environment jsdom
 *
 * Tests for beaming (automatic and explicit) and the harmonic indicator.
 *
 * Beams are drawn as filled paths, not glyphs, so their PRESENCE is hard to
 * assert directly.  What is unambiguous is the FLAG glyph (U+E240..U+E24F): an
 * unbeamed eighth draws its own flag, a beamed one does not.  Every assertion
 * here therefore counts flags -- 8 eighths unbeamed give 8 flags, correctly
 * beamed give 0 -- which also catches the ordering trap below.
 *
 * The trap: a beam must be constructed BEFORE factory.draw().  Built
 * afterwards it renders over flags the notes have already drawn (measured: 8
 * flags remain versus 0).  This is the reverse of hairpins, which must be
 * drawn after formatting resolves note positions.
 */

// Polyfill structuredClone for jest's jsdom environment: vexflow 5.0.0 uses
// it in metrics.getFontInfo, and jest's jsdom global does not expose it on
// Node 20 (a plain-data font-metrics clone, so JSON round-trip suffices).
if (typeof (globalThis as any).structuredClone !== 'function') {
  (globalThis as any).structuredClone = (v: any) =>
    (v === undefined ? undefined : JSON.parse(JSON.stringify(v)));
}

import { renderMusicSpec, type MusicSpec } from '../musicPlugin';

const makeChain = () => {
  const chain: any = {};
  // Every d3 selection method used by the overlay layers returns the chain, so
  // a call sequence of any depth resolves.  A two-level stub previously broke
  // the volta bracket's six chained .attr() calls and surfaced as a plugin
  // TypeError rather than as the test limitation it was.
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

/** Flag glyphs: present only on an UNBEAMED eighth or shorter. */
const flags = (c: HTMLElement) => (glyphs(c).match(/[\ue240-\ue24f]/g) ?? []).length;
const heads = (c: HTMLElement) => (glyphs(c).match(/[\ue0a2\ue0a3\ue0a4]/g) ?? []).length;

const EIGHTHS = ['c/5', 'd/5', 'e/5', 'f/5', 'g/5', 'a/5', 'b/5', 'c/6']
  .map((k) => ({ keys: [k], duration: '8' }));

describe('the unbeamed baseline', () => {
  it('draws a flag on every eighth when beaming is off', async () => {
    // Establishes that the flag count is a real signal, not a constant.
    const c = await draw({ type: 'music', timeSignature: '4/4', notes: EIGHTHS });
    expect(flags(c)).toBe(8);
    expect(heads(c)).toBe(8);
  });
});

describe('autoBeam', () => {
  it('removes every flag from a run of eighths', async () => {
    const c = await draw({ type: 'music', timeSignature: '4/4',
      notes: EIGHTHS, autoBeam: true });
    expect(flags(c)).toBe(0);
    expect(heads(c)).toBe(8);
  });

  it('beams sixteenths', async () => {
    const c = await draw({ type: 'music', timeSignature: '4/4', autoBeam: true,
      notes: ['c/5', 'd/5', 'e/5', 'f/5'].map((k) => ({ keys: [k], duration: '16' })) });
    expect(flags(c)).toBe(0);
    expect(heads(c)).toBe(4);
  });

  it('leaves quarters and longer alone', async () => {
    // autoBeam must be harmless on music with nothing beamable.
    const c = await draw({ type: 'music', timeSignature: '4/4', autoBeam: true,
      notes: ['c/5', 'd/5', 'e/5', 'f/5'].map((k) => ({ keys: [k], duration: 'q' })) });
    expect(flags(c)).toBe(0);
    expect(heads(c)).toBe(4);
  });

  it('beams only the eighths in a mixed rhythm', async () => {
    const c = await draw({ type: 'music', timeSignature: '4/4', autoBeam: true, notes: [
      { keys: ['c/5'], duration: '8' }, { keys: ['d/5'], duration: '8' },
      { keys: ['e/5'], duration: 'q' },
      { keys: ['f/5'], duration: '8' }, { keys: ['g/5'], duration: '8' },
      { keys: ['a/5'], duration: 'q' },
    ] });
    expect(flags(c)).toBe(0);
    expect(heads(c)).toBe(6);
  });

  it('honours a beamGroups override', async () => {
    // [[4,8]] groups all eight eighths into two beams instead of four.
    const c = await draw({ type: 'music', timeSignature: '4/4',
      notes: EIGHTHS, autoBeam: true, beamGroups: [[4, 8]] });
    expect(flags(c)).toBe(0);
    expect(heads(c)).toBe(8);
  });

  it('beams 6/8 in two groups of three', async () => {
    const c = await draw({ type: 'music', timeSignature: '6/8', autoBeam: true,
      beamGroups: [[3, 8]],
      notes: ['c/5', 'd/5', 'e/5', 'f/5', 'g/5', 'a/5']
        .map((k) => ({ keys: [k], duration: '8' })) });
    expect(flags(c)).toBe(0);
  });

  it('does not beam across a barline', async () => {
    // Beaming runs per measure; a group spanning a bar is wrong engraving.
    const c = await draw({ type: 'music', timeSignature: '4/4', autoBeam: true,
      measures: [{ notes: EIGHTHS.slice(0, 4) }, { notes: EIGHTHS.slice(4, 8) }] });
    expect(flags(c)).toBe(0);
    expect(heads(c)).toBe(8);
  });

  it('beams each staff of a grand staff', async () => {
    const c = await draw({ type: 'music', timeSignature: '4/4', autoBeam: true, staves: [
      { clef: 'treble', notes: EIGHTHS.slice(0, 4) },
      { clef: 'bass', notes: ['c/3', 'e/3', 'g/2', 'c/3']
          .map((k) => ({ keys: [k], duration: '8' })) },
    ] });
    expect(c.querySelectorAll('.vf-stave').length).toBe(2);
    expect(flags(c)).toBe(0);
    expect(heads(c)).toBe(8);
  });

  it('breaks a beam group at a rest', async () => {
    // beamRests:false is deliberate -- a rest ends a beam in ordinary
    // engraving, so the surviving flag belongs to the isolated eighth.
    const c = await draw({ type: 'music', timeSignature: '4/4', autoBeam: true, notes: [
      { keys: ['c/5'], duration: '8' }, { keys: ['d/5'], duration: '8' },
      { rest: true, duration: '8' }, { keys: ['f/5'], duration: '8' },
      { keys: ['g/5'], duration: 'q' }, { keys: ['a/5'], duration: 'q' },
    ] });
    expect(c.querySelector('svg')).not.toBeNull();
    // The pair before the rest is beamed; the lone eighth after it keeps a flag.
    expect(flags(c)).toBeLessThan(3);
  });

  it('coexists with slurs and dynamics', async () => {
    const c = await draw({ type: 'music', timeSignature: '4/4', autoBeam: true,
      notes: EIGHTHS.map((n, i) => (i === 0 ? { ...n, dynamic: 'pp' } : n)),
      slurs: [{ from: 0, to: 3 }], hairpins: [{ from: 0, to: 7, type: 'cresc' }] });
    expect(flags(c)).toBe(0);
    expect((glyphs(c).match(/[\ue520-\ue52f]/g) ?? []).length).toBeGreaterThan(0);
  });
});

describe('explicit beams', () => {
  it('beams a named range', async () => {
    const c = await draw({ type: 'music', timeSignature: '4/4', notes: EIGHTHS,
      beams: [{ from: 0, to: 3 }, { from: 4, to: 7 }] });
    expect(flags(c)).toBe(0);
    expect(heads(c)).toBe(8);
  });

  it('beams in pairs', async () => {
    const c = await draw({ type: 'music', timeSignature: '4/4', notes: EIGHTHS,
      beams: [{ from: 0, to: 1 }, { from: 2, to: 3 },
              { from: 4, to: 5 }, { from: 6, to: 7 }] });
    expect(flags(c)).toBe(0);
  });

  it('leaves notes outside any range flagged', async () => {
    const c = await draw({ type: 'music', timeSignature: '4/4', notes: EIGHTHS,
      beams: [{ from: 0, to: 3 }] });
    // The last four keep their flags.
    expect(flags(c)).toBe(4);
  });

  it('warns and skips an out-of-range span', async () => {
    const warn = jest.spyOn(console, 'warn').mockImplementation(() => {});
    const c = await draw({ type: 'music', timeSignature: '4/4', notes: EIGHTHS,
      beams: [{ from: 0, to: 99 }] });
    expect(c.querySelector('svg')).not.toBeNull();
    expect(flags(c)).toBe(8);
    expect(warn).toHaveBeenCalledWith(expect.any(String),
      expect.stringContaining('not a valid range'));
    warn.mockRestore();
  });

  it('warns on a single-note span, which cannot be beamed', async () => {
    const warn = jest.spyOn(console, 'warn').mockImplementation(() => {});
    await draw({ type: 'music', timeSignature: '4/4', notes: EIGHTHS,
      beams: [{ from: 2, to: 2 }] });
    expect(warn).toHaveBeenCalledWith(expect.any(String),
      expect.stringContaining('not a valid range'));
    warn.mockRestore();
  });

  it('accepts per-staff beams on a grand staff', async () => {
    const c = await draw({ type: 'music', timeSignature: '4/4', staves: [
      { clef: 'treble', notes: EIGHTHS.slice(0, 4), beams: [{ from: 0, to: 3 }] },
      { clef: 'bass', notes: ['c/3', 'e/3', 'g/2', 'c/3']
          .map((k) => ({ keys: [k], duration: '8' })) },
    ] });
    // Only the treble staff is beamed, so the bass keeps its four flags.
    expect(flags(c)).toBe(4);
  });
});

describe('harmonic indicator', () => {
  it('draws the open-circle harmonic glyph', async () => {
    // Already supported via ARTICULATION_CODES.harmonic -> 'ah', which VexFlow
    // maps to stringsHarmonic (U+E614).  Pinned because the name gives no hint
    // that it is the harp/string harmonic circle.
    const c = await draw({ type: 'music', clef: 'treble', notes: [
      { keys: ['c/5'], duration: 'q', articulations: ['harmonic'] },
      { keys: ['g/5'], duration: 'q' },
    ] });
    expect(glyphs(c)).toContain(String.fromCodePoint(0xe614));
  });

  it('is distinct from open-string', async () => {
    // open-string is snap pizzicato (U+E631), a different marking entirely.
    const c = await draw({ type: 'music', clef: 'treble', notes: [
      { keys: ['c/5'], duration: 'q', articulations: ['open-string'] },
    ] });
    expect(glyphs(c)).toContain(String.fromCodePoint(0xe631));
    expect(glyphs(c)).not.toContain(String.fromCodePoint(0xe614));
  });

  it('combines with a beamed passage', async () => {
    const c = await draw({ type: 'music', timeSignature: '4/4', autoBeam: true,
      notes: EIGHTHS.map((n, i) => (i === 0 ? { ...n, articulations: ['harmonic'] } : n)) });
    expect(glyphs(c)).toContain(String.fromCodePoint(0xe614));
    expect(flags(c)).toBe(0);
  });
});
