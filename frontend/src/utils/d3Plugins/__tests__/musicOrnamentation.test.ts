/**
 * @jest-environment jsdom
 *
 * Tests for fingering, string numbers, labeled chord symbols, placement
 * brackets and extended trill lines.
 *
 * Assertions target SMuFL codepoints and rendered text rather than element
 * counts, because every failure mode in this area is silent:
 *
 *   - factory.TextBracket / VibratoBracket take {from, to, options:{...}}.
 *     The shape the CLASS documents ({start, stop, superscript, position})
 *     throws a TypeError from inside VexFlow, and any other misshape draws
 *     nothing.
 *   - VibratoBracket's `code` is a raw SMuFL codepoint.  A small integer
 *     (1..4) is accepted and renders an empty glyph -- no error at all.
 *   - An unknown ChordSymbol glyph name is not rejected; it would misstate
 *     the harmony silently.
 *
 * These require the structuredClone and canvas-2d polyfills in setupTests.ts;
 * without them VexFlow cannot construct an Element at all.
 */

// Polyfill structuredClone for jest's jsdom environment: vexflow 5.0.0 uses
// it in metrics.getFontInfo, and jest's jsdom global does not expose it on
// Node 20 (a plain-data font-metrics clone, so JSON round-trip suffices).
if (typeof (globalThis as any).structuredClone !== 'function') {
  (globalThis as any).structuredClone = (v: any) =>
    (v === undefined ? undefined : JSON.parse(JSON.stringify(v)));
}

import { renderMusicSpec, WIGGLE_CODES, type MusicSpec } from '../musicPlugin';

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

// VexFlow measures glyph/text width through its own measurement canvas
// (Element.getTextMeasurementCanvas().getContext('2d')).  In this jsdom
// environment that canvas resolves to a context whose metrics are empty, so
// VexFlow logs "No context for txtCanvas" and every glyph width is 0.  That is
// merely imprecise for most primitives, but FATAL for Vibrato: its constructor
// divides by the glyph width and throws "Cannot set vibrato width if width is
// 0", so a trill line aborts the whole render.  The setupTests.ts prototype
// stub is meant to cover this but does not reach VexFlow's cached measurement
// canvas here; install one directly through VexFlow's own API so glyph
// emission (and therefore trill wiggles, and non-degenerate x positions) is
// faithful.  Approximate metrics are fine -- layout assertions here compare
// relative positions, not exact pixels.
beforeAll(() => {
  // eslint-disable-next-line @typescript-eslint/no-var-requires
  const { Element } = require('vexflow');
  const CH = 8;
  Element.setTextMeasurementCanvas({
    getContext: () => ({
      font: '',
      measureText: (t: string) => ({
        width: (t ?? '').length * CH,
        actualBoundingBoxAscent: CH,
        actualBoundingBoxDescent: 2,
        actualBoundingBoxLeft: 0,
        actualBoundingBoxRight: (t ?? '').length * CH,
        fontBoundingBoxAscent: CH,
        fontBoundingBoxDescent: 2,
      }),
    }),
  });
});

// DOM-capturing d3 for observing OVERLAY text (the tempo NAME via drawTempoName,
// dynamics via drawDynamicsLayer): these are drawn as d3 <text>, not VexFlow
// glyphs, so the no-op d3Stub above swallows them.  This appends real nodes so
// their text is queryable, without disturbing VexFlow's own SVG output.
const NS_ = 'http://www.w3.org/2000/svg';
const domSel = (el: Element): any => {
  const s: any = {
    node: () => el,
    append: (t: string) => { const c = document.createElementNS(NS_, t); el.appendChild(c); return domSel(c); },
    attr: (k: string, v: any) => { el.setAttribute(k, String(v)); return s; },
    style: () => s, classed: () => s, html: () => s,
    text: (t: any) => { el.textContent = String(t); return s; },
  };
  return s;
};
const domD3 = { select: (el: Element) => domSel(el) };
const drawDom = async (spec: MusicSpec) => {
  const container = document.createElement('div');
  document.body.appendChild(container);
  await renderMusicSpec(container, spec, false, domD3);
  return container;
};

const glyphs = (c: HTMLElement) =>
  Array.from(c.querySelectorAll('text')).map((t) => t.textContent ?? '').join('');
/** Rendered text with SMuFL private-use glyphs removed. */
const plainText = (c: HTMLElement) => glyphs(c).replace(/[\ue000-\uf8ff]/g, '');
const countIn = (c: HTMLElement, re: RegExp) => (glyphs(c).match(re) ?? []).length;

/** Chord-symbol glyph ranges: csym* symbols and csym accidentals. */
const CHORD_GLYPHS = /[\ue870-\ue87f]|[\ued60-\ued6f]/g;
/** Wiggle glyphs used by trill / vibrato lines. */
const WIGGLES = /[\ueaa0-\ueabf]/g;

const NOTES4: MusicSpec['notes'] = [
  { keys: ['c/5'], duration: 'q' },
  { keys: ['d/5'], duration: 'q' },
  { keys: ['e/5'], duration: 'q' },
  { keys: ['f/5'], duration: 'q' },
];

describe('fingering', () => {
  it('renders a digit per note', async () => {
    const c = await draw({ type: 'music', timeSignature: '4/4',
      notes: NOTES4.map((n, i) => ({ ...n, fingering: ['1', '2', '3', '5'][i] })) });
    expect(plainText(c)).toContain('1');
    expect(plainText(c)).toContain('5');
  });

  it('accepts a bare number as shorthand', async () => {
    const c = await draw({ type: 'music',
      notes: [{ keys: ['c/5'], duration: 'q', fingering: 2 }] });
    expect(plainText(c)).toContain('2');
  });

  it.each(['above', 'below', 'left', 'right'] as const)(
    'honours position %s', async (position) => {
      const c = await draw({ type: 'music',
        notes: [{ keys: ['c/5'], duration: 'q', fingering: { number: '3', position } }] });
      expect(plainText(c)).toContain('3');
    });

  it('warns about an unknown position instead of failing', async () => {
    const warn = jest.spyOn(console, 'warn').mockImplementation(() => {});
    const c = await draw({ type: 'music', notes: [
      { keys: ['c/5'], duration: 'q', fingering: { number: '1', position: 'sideways' as any } }] });
    expect(c.querySelector('svg')).not.toBeNull();
    expect(warn).toHaveBeenCalledWith(expect.any(String), expect.stringContaining('sideways'));
    warn.mockRestore();
  });

  it('renders a string number without throwing on a missing position', async () => {
    // StringNumber throws InvalidPosition when position is not supplied.
    const c = await draw({ type: 'music',
      notes: [{ keys: ['c/5'], duration: 'q', stringNumber: '3' }] });
    expect(c.querySelector('svg')).not.toBeNull();
    expect(plainText(c)).toContain('3');
  });
});

describe('chord symbols', () => {
  it('accepts a plain string', async () => {
    const c = await draw({ type: 'music',
      notes: [{ keys: ['c/4'], duration: 'q', chordSymbol: 'Cmaj7' }] });
    expect(plainText(c)).toContain('Cmaj7');
  });

  it('renders a superscript quality', async () => {
    const c = await draw({ type: 'music', notes: [
      { keys: ['c/4'], duration: 'q', chordSymbol: { text: 'C', superscript: 'maj7' } }] });
    expect(plainText(c)).toContain('C');
    expect(plainText(c)).toContain('maj7');
  });

  it('renders an engraved diminished glyph, not the letters', async () => {
    const c = await draw({ type: 'music', notes: [
      { keys: ['c/4'], duration: 'q', chordSymbol: { text: 'C', glyph: 'diminished' } }] });
    expect(countIn(c, CHORD_GLYPHS)).toBeGreaterThan(0);
    expect(plainText(c)).not.toMatch(/diminished/);
  });

  it('renders a half-diminished glyph', async () => {
    const c = await draw({ type: 'music', notes: [
      { keys: ['b/3'], duration: 'q', chordSymbol: { text: 'B', glyph: 'halfDiminished' } }] });
    expect(countIn(c, CHORD_GLYPHS)).toBeGreaterThan(0);
  });

  it('places roman numerals below the staff', async () => {
    const c = await draw({ type: 'music', timeSignature: '4/4', notes: [
      { keys: ['c/4'], duration: 'q', chordSymbol: { text: 'I', position: 'below' } },
      { keys: ['d/4'], duration: 'q', chordSymbol: { text: 'ii', position: 'below' } },
      { keys: ['g/4'], duration: 'q',
        chordSymbol: { text: 'V', superscript: '7', position: 'below' } },
      { keys: ['c/4'], duration: 'q', chordSymbol: { text: 'I', position: 'below' } },
    ] });
    const text = plainText(c);
    expect(text).toContain('ii');
    expect(text).toContain('V');
  });

  it('reserves room below for a below-staff symbol', async () => {
    const above = await draw({ type: 'music',
      notes: [{ keys: ['c/4'], duration: 'q', chordSymbol: { text: 'I' } }] });
    const below = await draw({ type: 'music',
      notes: [{ keys: ['c/4'], duration: 'q', chordSymbol: { text: 'I', position: 'below' } }] });
    const h = (c: HTMLElement) => Number(c.querySelector('svg')!.getAttribute('height'));
    expect(h(below)).toBeGreaterThan(h(above));
  });

  it('warns about an unknown glyph rather than misstating the harmony', async () => {
    const warn = jest.spyOn(console, 'warn').mockImplementation(() => {});
    const c = await draw({ type: 'music', notes: [
      { keys: ['c/4'], duration: 'q', chordSymbol: { text: 'C', glyph: 'bogus' } }] });
    expect(glyphs(c)).not.toMatch(/bogus/);
    expect(warn).toHaveBeenCalledWith(expect.any(String), expect.stringContaining('bogus'));
    warn.mockRestore();
  });
});

describe('placement brackets', () => {
  it('renders an 8va bracket', async () => {
    const c = await draw({ type: 'music', timeSignature: '4/4', notes: NOTES4,
      brackets: [{ from: 0, to: 3, text: '8', superscript: 'va' }] });
    expect(plainText(c)).toContain('8va');
  });

  it('renders an 8vb bracket below the staff', async () => {
    const c = await draw({ type: 'music', timeSignature: '4/4', notes: NOTES4,
      brackets: [{ from: 0, to: 3, text: '8', superscript: 'vb', position: 'below' }] });
    expect(plainText(c)).toContain('8vb');
  });

  it('renders a 15ma bracket', async () => {
    const c = await draw({ type: 'music', timeSignature: '4/4', notes: NOTES4,
      brackets: [{ from: 0, to: 3, text: '15', superscript: 'ma' }] });
    expect(plainText(c)).toContain('15ma');
  });

  it('renders a spanning text direction with no superscript', async () => {
    const c = await draw({ type: 'music', timeSignature: '4/4', notes: NOTES4,
      brackets: [{ from: 0, to: 3, text: 'rit.', dashed: false }] });
    expect(plainText(c)).toContain('rit.');
  });

  it('drops a bracket with a bad index without failing the score', async () => {
    const warn = jest.spyOn(console, 'warn').mockImplementation(() => {});
    const c = await draw({ type: 'music', notes: [{ keys: ['c/5'], duration: 'q' }],
      brackets: [{ from: 0, to: 9, text: '8', superscript: 'va' }] });
    expect(c.querySelector('svg')).not.toBeNull();
    expect(warn).toHaveBeenCalledWith(expect.any(String), expect.stringContaining('out of range'));
    warn.mockRestore();
  });
});

describe('trill lines', () => {
  it('draws a wiggle line across the span', async () => {
    const c = await draw({ type: 'music', timeSignature: '4/4', notes: NOTES4,
      trillLines: [{ from: 0, to: 3 }] });
    expect(countIn(c, WIGGLES)).toBeGreaterThan(0);
  });

  it.each(Object.keys(WIGGLE_CODES))('renders the %s wiggle', async (wiggle) => {
    const c = await draw({ type: 'music', timeSignature: '4/4', notes: NOTES4,
      trillLines: [{ from: 0, to: 3, wiggle }] });
    expect(countIn(c, WIGGLES)).toBeGreaterThan(0);
  });

  it('maps names to codepoints, since an integer code draws nothing', async () => {
    // Guards the trap directly: every mapped value must be a real SMuFL
    // codepoint, never a small ordinal.
    for (const code of Object.values(WIGGLE_CODES)) {
      expect(code).toBeGreaterThan(0xe000);
    }
  });

  it('combines with a trill ornament for a full tr~~~~', async () => {
    const c = await draw({ type: 'music', timeSignature: '4/4',
      notes: NOTES4.map((n, i) => (i === 0 ? { ...n, ornaments: ['trill'] } : n)),
      trillLines: [{ from: 0, to: 3 }] });
    expect(glyphs(c)).toMatch(/[\ue560-\ue56f]/);   // the tr glyph
    expect(countIn(c, WIGGLES)).toBeGreaterThan(0);  // and the line
  });

  it('warns about an unknown wiggle name', async () => {
    const warn = jest.spyOn(console, 'warn').mockImplementation(() => {});
    const c = await draw({ type: 'music', notes: NOTES4,
      trillLines: [{ from: 0, to: 1, wiggle: 'squiggle' }] });
    expect(c.querySelector('svg')).not.toBeNull();
    expect(warn).toHaveBeenCalledWith(expect.any(String), expect.stringContaining('squiggle'));
    warn.mockRestore();
  });
});

describe('combined', () => {
  it('renders fingering, chords, brackets and a trill line together', async () => {
    // Uses the DOM-capturing stub: the tempo NAME "Allegro" is drawn by the
    // drawTempoName d3 overlay (VexFlow only draws the "= 132" metronome), so
    // the no-op stub cannot see it -- while 8va / Cmaj7 / the wiggle are all
    // VexFlow SVG glyphs and appear either way.
    const c = await drawDom({
      type: 'music', timeSignature: '4/4', keySignature: 'C',
      tempo: { name: 'Allegro', duration: 'q', bpm: 132 },
      staves: [
        { clef: 'treble', notes: [
            { keys: ['c/5'], duration: 'q', fingering: '1', dynamic: 'pp',
              articulations: ['staccato'],
              chordSymbol: { text: 'C', superscript: 'maj7' } },
            { keys: ['e/5'], duration: 'q', fingering: '3', ornaments: ['trill'] },
            { keys: ['g/5'], duration: 'q', fingering: '5' },
            { keys: ['c/6'], duration: 'q', dynamic: 'fff',
              articulations: ['fermata-above'] }],
          slurs: [{ from: 0, to: 2 }],
          brackets: [{ from: 0, to: 3, text: '8', superscript: 'va' }],
          trillLines: [{ from: 1, to: 2 }],
          hairpins: [{ from: 0, to: 3, type: 'cresc' }] },
        { clef: 'bass', notes: [
            { keys: ['c/3'], duration: 'h',
              chordSymbol: { text: 'I', position: 'below' } },
            { keys: ['g/2'], duration: 'h',
              chordSymbol: { text: 'V', position: 'below' } }] },
      ],
    });
    const text = plainText(c);
    expect(c.querySelectorAll('.vf-stave').length).toBe(2);
    expect(text).toContain('Allegro');
    expect(text).toContain('8va');
    expect(text).toContain('Cmaj7');
    expect(countIn(c, WIGGLES)).toBeGreaterThan(0);
  });
});
