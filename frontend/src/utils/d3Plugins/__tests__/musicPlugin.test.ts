/**
 * @jest-environment jsdom
 *
 * Regression tests for the VexFlow music-notation core.
 *
 * Every case here corresponds to a bug that made music blocks render nothing
 * at all.  The two that matter most are silent failures, which is why they
 * survived: EasyScore reports a grammar mismatch by returning an empty array
 * rather than raising, and the CANVAS/SVG backend constant was off by one with
 * a comment asserting the wrong value.
 */
import {
  buildNoteString,
  isMusicSpec,
  toEasyScoreKey,
  renderMusicSpec,
  type MusicSpec,
} from '../musicPlugin';

describe('toEasyScoreKey', () => {
  it('converts StaveNote key form to EasyScore pitch form', () => {
    // "c/5" + "/q" would give "c/5/q", which EasyScore does not match.
    expect(toEasyScoreKey('c/5')).toBe('C5');
    expect(toEasyScoreKey('f/4')).toBe('F4');
  });

  it('preserves accidentals, including doubles', () => {
    expect(toEasyScoreKey('c#/5')).toBe('C#5');
    expect(toEasyScoreKey('bb/4')).toBe('Bb4');
    expect(toEasyScoreKey('g##/3')).toBe('G##3');
  });

  it('passes through a key already in EasyScore form', () => {
    expect(toEasyScoreKey('C5')).toBe('C5');
  });

  it('tolerates surrounding whitespace', () => {
    expect(toEasyScoreKey('  d/5  ')).toBe('D5');
  });
});

describe('buildNoteString', () => {
  it('emits pitch/duration pairs EasyScore can parse', () => {
    expect(buildNoteString([
      { keys: ['c/5'], duration: 'q' },
      { keys: ['d/5'], duration: 'q' },
    ])).toBe('C5/q, D5/q');
  });

  it('parenthesises chords so they become one tickable', () => {
    expect(buildNoteString([
      { keys: ['c/4', 'e/4', 'g/4'], duration: 'h' },
    ])).toBe('(C4 E4 G4)/h');
  });

  it('keeps dotted durations intact', () => {
    expect(buildNoteString([{ keys: ['c/5'], duration: 'q.' }])).toBe('C5/q.');
  });

  it('never emits a double slash', () => {
    const out = buildNoteString([{ keys: ['c/5'], duration: 'q' }]);
    expect(out).not.toContain('//');
    expect(out).not.toMatch(/[a-gA-G][#b]*\/\d+\//);
  });
});

describe('isMusicSpec', () => {
  it('accepts a well-formed spec', () => {
    expect(isMusicSpec({ type: 'music', notes: [{ keys: ['c/5'], duration: 'q' }] })).toBe(true);
  });

  it('rejects a spec with no notes', () => {
    expect(isMusicSpec({ type: 'music', notes: [] })).toBe(false);
  });

  it('rejects a non-music spec', () => {
    expect(isMusicSpec({ type: 'mermaid', notes: [{ keys: ['c/5'], duration: 'q' }] })).toBe(false);
  });

  it('rejects null without throwing', () => {
    expect(isMusicSpec(null)).toBe(false);
  });
});

/**
 * These drive real VexFlow.  jsdom cannot provide a 2D canvas context, so
 * VexFlow's text measurement degrades to empty metrics and logs a warning --
 * layout is approximate but drawing still completes, which is enough to prove
 * the pipeline is wired correctly.
 */
describe('renderMusicSpec (integration)', () => {
  const d3Stub = { select: () => ({ append: () => ({ attr: () => ({ attr: () => ({}) }) }) }) };

  const render = async (spec: MusicSpec) => {
    const container = document.createElement('div');
    document.body.appendChild(container);
    await renderMusicSpec(container, spec, false, d3Stub);
    return container;
  };

  it('produces an SVG, not a canvas', async () => {
    // backend:1 (CANVAS) threw BadElement on a <div> before drawing anything.
    const c = await render({
      type: 'music',
      notes: [{ keys: ['c/5'], duration: 'q' }, { keys: ['d/5'], duration: 'q' }],
    });
    expect(c.querySelector('svg')).not.toBeNull();
    expect(c.querySelector('canvas')).toBeNull();
  });

  it('draws stave lines and note glyphs', async () => {
    const c = await render({
      type: 'music',
      clef: 'treble',
      keySignature: 'C',
      timeSignature: '4/4',
      notes: [
        { keys: ['c/5'], duration: 'q' },
        { keys: ['d/5'], duration: 'q' },
        { keys: ['e/5'], duration: 'q' },
        { keys: ['f/5'], duration: 'q' },
      ],
    });
    expect(c.querySelectorAll('path').length).toBeGreaterThan(4);
  });

  it('renders an underfull measure instead of refusing it', async () => {
    // STRICT mode raised IncompleteVoice for 3 quarter notes in 4/4.
    const c = await render({
      type: 'music',
      timeSignature: '4/4',
      notes: [
        { keys: ['c/5'], duration: 'q' },
        { keys: ['d/5'], duration: 'q' },
        { keys: ['e/5'], duration: 'q' },
      ],
    });
    expect(c.querySelector('svg')).not.toBeNull();
  });

  it('renders an overfull measure instead of refusing it', async () => {
    // score.voice() raised BadArgument: Too many ticks for 5 quarters in 4/4.
    const c = await render({
      type: 'music',
      timeSignature: '4/4',
      notes: [
        { keys: ['c/5'], duration: 'q' },
        { keys: ['d/5'], duration: 'q' },
        { keys: ['e/5'], duration: 'q' },
        { keys: ['f/5'], duration: 'q' },
        { keys: ['g/5'], duration: 'q' },
      ],
    });
    expect(c.querySelector('svg')).not.toBeNull();
  });

  it('renders chords, accidentals and annotations together', async () => {
    const c = await render({
      type: 'music',
      notes: [
        {
          keys: ['c#/5', 'e/5', 'g/5'],
          duration: 'h',
          chordSymbol: 'C#m',
          annotations: [{ text: 'soft', position: 'below' }],
        },
        { keys: ['bb/4'], duration: 'h' },
      ],
    });
    expect(c.querySelectorAll('text').length).toBeGreaterThan(0);
  });

  it('honours a non-4/4 meter', async () => {
    const c = await render({
      type: 'music',
      clef: 'bass',
      timeSignature: '3/4',
      keySignature: 'F',
      notes: [
        { keys: ['c/3'], duration: 'q' },
        { keys: ['d/3'], duration: 'q' },
        { keys: ['e/3'], duration: 'q' },
      ],
    });
    expect(c.querySelector('svg')).not.toBeNull();
  });

  it('names the real cause when a key is unparseable', async () => {
    await expect(render({
      type: 'music',
      notes: [{ keys: ['not-a-pitch'], duration: 'q' }],
    })).rejects.toThrow(/Could not parse/);
  });
});
