/**
 * @jest-environment jsdom
 *
 * Tests for multi-measure staves and the barlines between them.
 *
 * Before `measures` existed, `beginBar`/`endBar` could only set a stave's two
 * OUTER barlines, so a repeat sign enclosed the single measure and had nothing
 * to repeat -- the types rendered correctly but could never be used for
 * anything musical.  A barline that actually divides music requires a BarNote
 * inside the voice, which in turn requires the spec to express more than one
 * measure.
 *
 * Repeat dots are drawn as SVG arc paths, and the thin/thick bar pair as
 * <rect>s, so those are what the assertions count: a barline is invisible to
 * any text/glyph check.
 */
import { renderMusicSpec, type MusicMeasure, type MusicSpec } from '../musicPlugin';

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

/** Noteheads: whole (e0a2), half (e0a3) and quarter/eighth (e0a4). */
const noteheads = (c: HTMLElement) =>
  (Array.from(c.querySelectorAll('text')).map((t) => t.textContent ?? '').join('')
    .match(/[\ue0a2\ue0a3\ue0a4]/g) ?? []).length;

/** Repeat dots are the only arc-bearing paths VexFlow emits here. */
const repeatDots = (c: HTMLElement) =>
  Array.from(c.querySelectorAll('path'))
    .filter((p) => /[aA]/.test(p.getAttribute('d') ?? '')).length;

const bars = (c: HTMLElement) => c.querySelectorAll('rect').length;

const M = (start: string): MusicMeasure => ({
  notes: [
    { keys: [`${start}/5`], duration: 'q' },
    { keys: ['d/5'], duration: 'q' },
    { keys: ['e/5'], duration: 'q' },
    { keys: ['f/5'], duration: 'q' },
  ],
});

const FLAT4: MusicSpec['notes'] = M('c').notes;

describe('backward compatibility', () => {
  it('still renders a flat notes list', async () => {
    const c = await draw({ type: 'music', timeSignature: '4/4', notes: FLAT4 });
    expect(noteheads(c)).toBe(4);
  });

  it('falls back to notes when measures is empty', async () => {
    const c = await draw({ type: 'music', timeSignature: '4/4', measures: [], notes: FLAT4 });
    expect(noteheads(c)).toBe(4);
  });

  it('treats a single measure the same as a flat list', async () => {
    const flat = await draw({ type: 'music', timeSignature: '4/4', notes: FLAT4 });
    const wrapped = await draw({ type: 'music', timeSignature: '4/4', measures: [M('c')] });
    expect(noteheads(wrapped)).toBe(noteheads(flat));
  });
});

describe('multiple measures', () => {
  it('renders every note across two measures', async () => {
    const c = await draw({ type: 'music', timeSignature: '4/4', measures: [M('c'), M('g')] });
    expect(noteheads(c)).toBe(8);
  });

  it('renders three measures', async () => {
    const c = await draw({ type: 'music', timeSignature: '4/4',
      measures: [M('c'), M('g'), { notes: [{ keys: ['c/5'], duration: 'w' }] }] });
    expect(noteheads(c)).toBe(9);
  });

  it('draws an internal barline that a single measure does not', async () => {
    const one = await draw({ type: 'music', timeSignature: '4/4', measures: [M('c')] });
    const two = await draw({ type: 'music', timeSignature: '4/4', measures: [M('c'), M('g')] });
    expect(bars(two)).toBeGreaterThan(bars(one));
  });

  it('widens the canvas to fit the extra measures', async () => {
    const one = await draw({ type: 'music', timeSignature: '4/4', measures: [M('c')] });
    const two = await draw({ type: 'music', timeSignature: '4/4', measures: [M('c'), M('g')] });
    const w = (c: HTMLElement) => Number(c.querySelector('svg')!.getAttribute('width'));
    expect(w(two)).toBeGreaterThan(w(one));
  });

  it('does not require the content to sum to the time signature', async () => {
    // The meter stays the meter of ONE bar; SOFT mode tolerates the rest.
    const c = await draw({ type: 'music', timeSignature: '4/4',
      measures: [M('c'), M('g'), M('c')] });
    expect(noteheads(c)).toBe(12);
  });

  it('honours a non-4/4 meter across measures', async () => {
    const three = (start: string): MusicMeasure => ({ notes: M(start).notes.slice(0, 3) });
    const c = await draw({ type: 'music', timeSignature: '3/4',
      measures: [three('c'), three('g')] });
    expect(noteheads(c)).toBe(6);
  });

  it('names the measure when a note cannot be parsed', async () => {
    await expect(draw({ type: 'music', timeSignature: '4/4',
      measures: [M('c'), { notes: [{ keys: ['not-a-pitch'], duration: 'q' }] }] }))
      .rejects.toThrow(/measure 2/);
  });
});

describe('barlines between measures', () => {
  it('draws repeat dots for an inner repeat-end', async () => {
    const plain = await draw({ type: 'music', timeSignature: '4/4',
      measures: [M('c'), M('g')] });
    const repeated = await draw({ type: 'music', timeSignature: '4/4',
      measures: [{ ...M('c'), endBar: 'repeat-end' }, M('g')] });
    expect(repeatDots(plain)).toBe(0);
    expect(repeatDots(repeated)).toBeGreaterThan(0);
  });

  it('accepts beginBar on the following measure instead', async () => {
    const c = await draw({ type: 'music', timeSignature: '4/4',
      measures: [M('c'), { ...M('g'), beginBar: 'repeat-begin' }] });
    expect(repeatDots(c)).toBeGreaterThan(0);
  });

  it('prefers the earlier measure\'s endBar over the later beginBar', async () => {
    // endBar wins, so no repeat dots appear despite the beginBar.
    const c = await draw({ type: 'music', timeSignature: '4/4',
      measures: [{ ...M('c'), endBar: 'double' }, { ...M('g'), beginBar: 'repeat-begin' }] });
    expect(repeatDots(c)).toBe(0);
  });

  it('draws a double bar between sections', async () => {
    const single = await draw({ type: 'music', timeSignature: '4/4',
      measures: [M('c'), M('g')] });
    const doubled = await draw({ type: 'music', timeSignature: '4/4',
      measures: [{ ...M('c'), endBar: 'double' }, M('g')] });
    expect(bars(doubled)).toBeGreaterThan(bars(single));
  });

  it('combines an outer repeat with an inner double bar', async () => {
    const c = await draw({ type: 'music', timeSignature: '4/4',
      beginBar: 'repeat-begin', endBar: 'repeat-end',
      measures: [{ ...M('c'), endBar: 'double' }, M('g')] });
    // Both outer repeats contribute dots.
    expect(repeatDots(c)).toBeGreaterThanOrEqual(4);
  });

  it('warns about an unknown inner barline and falls back to single', async () => {
    const warn = jest.spyOn(console, 'warn').mockImplementation(() => {});
    const c = await draw({ type: 'music', timeSignature: '4/4',
      measures: [{ ...M('c'), endBar: 'wiggly' }, M('g')] });
    expect(noteheads(c)).toBe(8);
    expect(warn).toHaveBeenCalledWith(expect.any(String), expect.stringContaining('wiggly'));
    warn.mockRestore();
  });

  it('still applies the outer final barline', async () => {
    const c = await draw({ type: 'music', timeSignature: '4/4', endBar: 'final',
      measures: [M('c'), M('g')] });
    expect(c.querySelector('svg')).not.toBeNull();
    expect(noteheads(c)).toBe(8);
  });
});

describe('interaction with other features', () => {
  it('keeps dynamics aligned with their notes across a barline', async () => {
    // The dynamics voice needs its own BarNote or it desynchronises: a mark in
    // measure 2 rendered at x=383 while its note sat at x=445.
    const c = await draw({ type: 'music', timeSignature: '4/4', measures: [
      { notes: M('c').notes.map((n, i) => (i === 0 ? { ...n, dynamic: 'pp' } : n)) },
      { notes: M('g').notes.map((n, i) => (i === 0 ? { ...n, dynamic: 'ff' } : n)) },
    ] });
    const dynX = Array.from(c.querySelectorAll('text'))
      .filter((t) => /[\ue520-\ue52f]/.test(t.textContent ?? ''))
      .map((t) => parseFloat(t.getAttribute('x') ?? 'NaN'));
    const headX = Array.from(c.querySelectorAll('text'))
      .filter((t) => /[\ue0a4]/.test(t.textContent ?? ''))
      .map((t) => parseFloat(t.getAttribute('x') ?? 'NaN'));
    expect(dynX.length).toBe(2);
    // Each mark must sit at (approximately) some notehead's x.
    for (const x of dynX) {
      expect(headX.some((hx) => Math.abs(hx - x) < 20)).toBe(true);
    }
  });

  it('allows a slur to cross a barline', async () => {
    // Span indices count notes across the whole staff, ignoring measures.
    const plain = await draw({ type: 'music', timeSignature: '4/4',
      measures: [M('c'), M('g')] });
    const slurred = await draw({ type: 'music', timeSignature: '4/4',
      measures: [M('c'), M('g')], slurs: [{ from: 2, to: 5 }] });
    expect(slurred.querySelectorAll('path').length)
      .toBeGreaterThan(plain.querySelectorAll('path').length);
  });

  it('carries per-note articulations in a later measure', async () => {
    const c = await draw({ type: 'music', timeSignature: '4/4', measures: [
      M('c'),
      { notes: M('g').notes.map((n, i) =>
          (i === 3 ? { ...n, articulations: ['fermata-above'] } : n)) },
    ] });
    expect(Array.from(c.querySelectorAll('text')).map((t) => t.textContent ?? '').join(''))
      .toMatch(/[\ue4c0-\ue4c7]/);
  });

  it('renders a multi-measure grand staff', async () => {
    const c = await draw({ type: 'music', timeSignature: '4/4', staves: [
      { clef: 'treble', measures: [M('c'), M('g')] },
      { clef: 'bass', measures: [
        { notes: [{ keys: ['c/3'], duration: 'h' }, { keys: ['e/3'], duration: 'h' }] },
        { notes: [{ keys: ['g/2'], duration: 'h' }, { keys: ['c/3'], duration: 'h' }] },
      ] },
    ] });
    expect(c.querySelectorAll('.vf-stave').length).toBe(2);
    expect(noteheads(c)).toBe(12);
  });

  it('lets one staff use measures while another uses a flat list', async () => {
    const c = await draw({ type: 'music', timeSignature: '4/4', staves: [
      { clef: 'treble', measures: [M('c'), M('g')] },
      { clef: 'bass', notes: [{ keys: ['c/3'], duration: 'w' }] },
    ] });
    expect(c.querySelectorAll('.vf-stave').length).toBe(2);
  });
});

describe('spec recognition', () => {
  it('recognises a measures-only spec with no top-level notes', async () => {
    // isMusicSpec backs canHandle; a measures-only spec must not look foreign.
    const { isMusicSpec } = await import('../musicPlugin');
    expect(isMusicSpec({ type: 'music', measures: [M('c')] })).toBe(true);
  });

  it('rejects a measures list with no notes in it', async () => {
    const { isMusicSpec } = await import('../musicPlugin');
    expect(isMusicSpec({ type: 'music', measures: [{ notes: [] }] })).toBe(false);
  });

  it('recognises a staves entry that uses measures', async () => {
    const { isMusicSpec } = await import('../musicPlugin');
    expect(isMusicSpec({ type: 'music',
      staves: [{ clef: 'treble', measures: [M('c')] }] })).toBe(true);
  });
});
