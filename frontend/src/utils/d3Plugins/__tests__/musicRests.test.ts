/**
 * @jest-environment jsdom
 *
 * Tests for rests.
 *
 * Two EasyScore traps are pinned here because both fail SILENTLY -- the parse
 * succeeds, something is drawn, and it is simply the wrong thing:
 *
 *   1. The augmentation dot must follow the /r suffix.  "B4/q./r" is accepted
 *      and yields a NOTEHEAD (verified: rests=0); only "B4/q/r." is a dotted
 *      rest.  The spec carries dots on `duration`, so the builder has to move
 *      them.
 *   2. A rest still carries a pitch, and that pitch positions it vertically.
 *      B4 centres a rest on a treble staff (y=70 of lines 50..90) but leaves
 *      it floating above a bass staff (y=10), so the pitch must follow the
 *      clef.
 *
 * Assertions count SMuFL rest codepoints (U+E4E3..U+E4E7) rather than
 * elements, since a rest that silently became a note still draws a glyph.
 */
import { buildNoteString, renderMusicSpec, type MusicSpec } from '../musicPlugin';

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

/** Rest glyphs: whole E4E3, half E4E4, quarter E4E5, 8th E4E6, 16th E4E7. */
const restCount = (c: HTMLElement) => (glyphs(c).match(/[\ue4e0-\ue4ef]/g) ?? []).length;
/** Noteheads, to prove a rest did not silently become a note. */
const headCount = (c: HTMLElement) => (glyphs(c).match(/[\ue0a2\ue0a3\ue0a4]/g) ?? []).length;

const N = (key: string, duration: string) => ({ keys: [key], duration });
const R = (duration: string) => ({ rest: true, duration });

describe('buildNoteString rest syntax', () => {
  it('emits the /r suffix EasyScore requires', () => {
    expect(buildNoteString([N('c/5', 'q'), R('q')], 'treble'))
      .toBe('C5/q, B4/q/r');
  });

  it('places the dot AFTER /r, not on the duration', () => {
    // "B4/q./r" would parse as a note and draw a notehead.
    expect(buildNoteString([R('q.')], 'treble')).toBe('B4/q/r.');
    expect(buildNoteString([R('h.')], 'treble')).toBe('B4/h/r.');
  });

  it('chooses a clef-appropriate rest pitch', () => {
    expect(buildNoteString([R('q')], 'treble')).toBe('B4/q/r');
    expect(buildNoteString([R('q')], 'bass')).toBe('D3/q/r');
    expect(buildNoteString([R('q')], 'alto')).toBe('C4/q/r');
  });

  it('falls back to the treble pitch for an unknown clef', () => {
    expect(buildNoteString([R('q')], 'nonsense')).toBe('B4/q/r');
  });

  it('ignores keys supplied alongside rest', () => {
    expect(buildNoteString([{ keys: ['g/5'], duration: 'q', rest: true }], 'treble'))
      .toBe('B4/q/r');
  });

  it('leaves ordinary notes untouched', () => {
    expect(buildNoteString([N('c/5', 'q'), N('d/5', 'q.')], 'treble'))
      .toBe('C5/q, D5/q.');
  });
});

describe('rendering rests', () => {
  it('draws a quarter rest between notes', async () => {
    const c = await draw({ type: 'music', timeSignature: '4/4',
      notes: [N('c/5', 'q'), R('q'), N('e/5', 'q'), N('f/5', 'q')] });
    expect(restCount(c)).toBe(1);
    expect(headCount(c)).toBe(3);
  });

  it.each([['w', 1], ['h', 2], ['q', 4], ['8', 4], ['16', 4]])(
    'draws a %s rest', async (duration, n) => {
      const c = await draw({ type: 'music', timeSignature: '4/4',
        notes: Array.from({ length: n }, () => R(duration)) });
      expect(restCount(c)).toBe(n);
      expect(headCount(c)).toBe(0);
    });

  it('draws a DOTTED rest as a rest, not a note', async () => {
    // The regression: a dotted rest silently rendered as a notehead.
    const c = await draw({ type: 'music', timeSignature: '4/4',
      notes: [R('q.'), N('c/5', '8'), N('d/5', 'q')] });
    expect(restCount(c)).toBe(1);
    expect(headCount(c)).toBe(2);
  });

  it('renders a measure of complete silence', async () => {
    const c = await draw({ type: 'music', timeSignature: '4/4', notes: [R('w')] });
    expect(restCount(c)).toBe(1);
    expect(headCount(c)).toBe(0);
  });

  it('centres a rest on the staff for a treble clef', async () => {
    const c = await draw({ type: 'music', clef: 'treble', notes: [R('q'), N('c/5', 'q')] });
    const rest = Array.from(c.querySelectorAll('text'))
      .find((t) => /[\ue4e0-\ue4ef]/.test(t.textContent ?? ''));
    const y = parseFloat(rest!.getAttribute('y') ?? 'NaN');
    // Must sit between the top and bottom stave lines, not above or below.
    const lineYs = Array.from(c.querySelectorAll('.vf-stave path'))
      .map((p) => parseFloat((p.getAttribute('d') ?? '').match(/M[\d.]+ ([\d.]+)/)?.[1] ?? 'NaN'))
      .filter(Number.isFinite);
    expect(y).toBeGreaterThan(Math.min(...lineYs));
    expect(y).toBeLessThan(Math.max(...lineYs));
  });

  it('centres a rest on the staff for a bass clef', async () => {
    // B4 would place it above the staff entirely.
    const c = await draw({ type: 'music', clef: 'bass', notes: [R('q'), N('c/3', 'q')] });
    const rest = Array.from(c.querySelectorAll('text'))
      .find((t) => /[\ue4e0-\ue4ef]/.test(t.textContent ?? ''));
    const y = parseFloat(rest!.getAttribute('y') ?? 'NaN');
    const lineYs = Array.from(c.querySelectorAll('.vf-stave path'))
      .map((p) => parseFloat((p.getAttribute('d') ?? '').match(/M[\d.]+ ([\d.]+)/)?.[1] ?? 'NaN'))
      .filter(Number.isFinite);
    expect(y).toBeGreaterThan(Math.min(...lineYs));
    expect(y).toBeLessThan(Math.max(...lineYs));
  });

  it('honours a non-4/4 meter with rests', async () => {
    const c = await draw({ type: 'music', timeSignature: '3/4',
      notes: [N('c/5', 'q'), R('q'), N('e/5', 'q')] });
    expect(restCount(c)).toBe(1);
  });

  it('mixes rests with chords', async () => {
    const c = await draw({ type: 'music', timeSignature: '4/4',
      notes: [{ keys: ['c/4', 'e/4', 'g/4'], duration: 'h' }, R('h')] });
    expect(restCount(c)).toBe(1);
    expect(headCount(c)).toBe(3);
  });
});

describe('rests with other features', () => {
  it('keeps dynamics aligned when a rest sits between marked notes', async () => {
    // The dynamics voice pads a rest with a GhostNote; a mismatch would shift
    // every later mark off its note.
    const c = await draw({ type: 'music', timeSignature: '4/4', notes: [
      { ...N('c/5', 'q'), dynamic: 'pp' }, R('q'),
      { ...N('e/5', 'q'), dynamic: 'ff' }, N('f/5', 'q'),
    ] });
    const xOf = (re: RegExp) => Array.from(c.querySelectorAll('text'))
      .filter((t) => re.test(t.textContent ?? ''))
      .map((t) => parseFloat(t.getAttribute('x') ?? 'NaN'));
    const dynX = xOf(/[\ue520-\ue52f]/);
    const headX = xOf(/[\ue0a4]/);
    expect(dynX.length).toBe(2);
    for (const x of dynX) {
      expect(headX.some((hx) => Math.abs(hx - x) < 20)).toBe(true);
    }
  });

  it('counts a rest as an index for spans', async () => {
    // A slur from note 0 to index 2 must reach the note AFTER the rest.
    const c = await draw({ type: 'music', timeSignature: '4/4',
      notes: [N('c/5', 'q'), R('q'), N('e/5', 'q'), N('f/5', 'q')],
      slurs: [{ from: 0, to: 2 }] });
    expect(c.querySelector('svg')).not.toBeNull();
    expect(restCount(c)).toBe(1);
  });

  it('renders a rest inside a later measure', async () => {
    const c = await draw({ type: 'music', timeSignature: '4/4', measures: [
      { notes: [N('c/5', 'q'), N('d/5', 'q'), N('e/5', 'q'), N('f/5', 'q')] },
      { notes: [R('h'), N('g/5', 'h')] },
    ] });
    expect(restCount(c)).toBe(1);
    expect(headCount(c)).toBe(5);
  });

  it('renders rests on one staff of a grand staff', async () => {
    const c = await draw({ type: 'music', timeSignature: '4/4', staves: [
      { clef: 'treble', notes: [N('c/5', 'q'), R('q'), N('e/5', 'h')] },
      { clef: 'bass', notes: [R('w')] },
    ] });
    expect(c.querySelectorAll('.vf-stave').length).toBe(2);
    expect(restCount(c)).toBe(2);
  });
});
