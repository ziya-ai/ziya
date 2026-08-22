/**
 * @jest-environment jsdom
 *
 * Tests for structural notation: tempo, barlines/repeats, navigation marks
 * (coda/segno/Fine/D.C./D.S.), voltas, measure numbers, section labels and
 * the multi-staff grand staff.
 *
 * The load-bearing case here is `renders a single staff without a formatter
 * error`.  Passing `spaceBetweenStaves: undefined` (rather than omitting the
 * key) makes VexFlow's option merge treat it as a real value, System.format()
 * never runs, and draw() throws NoFormatter -- so EVERY single-staff render
 * dies while grand-staff renders keep working.  A test suite covering only
 * the new grand-staff feature would have passed with the app fully broken.
 *
 * Assertions use SMuFL codepoints where a glyph is the deliverable, because
 * element counts cannot distinguish a real symbol from ASCII fallback text.
 */

// Polyfill structuredClone for jest's jsdom environment: vexflow 5.0.0 uses
// it in metrics.getFontInfo, and jest's jsdom global does not expose it on
// Node 20 (a plain-data font-metrics clone, so JSON round-trip suffices).
if (typeof (globalThis as any).structuredClone !== 'function') {
  (globalThis as any).structuredClone = (v: any) =>
    (v === undefined ? undefined : JSON.parse(JSON.stringify(v)));
}

import { renderMusicSpec, type MusicSpec } from '../musicPlugin';

const SVG_NS = 'http://www.w3.org/2000/svg';

/**
 * A d3 stand-in that appends REAL elements, so overlay-drawn output is
 * assertable instead of silently discarded.
 *
 * Two reasons this is not the usual inert stub:
 *   1. It chains to any depth.  `drawVoltaBracket` chains six `.attr()` calls;
 *      a two-level stub failed with "svg.append(...).attr(...).attr(...).attr
 *      is not a function", which read as a plugin TypeError but was purely a
 *      test artefact.
 *   2. The volta LABEL is a d3 overlay, not a VexFlow glyph, so an inert stub
 *      discards the very string this suite asserts on.
 *
 * Real d3 cannot be used: v7 ships ESM only and this project's jest transform
 * rejects it ("Unexpected token 'export'" from node_modules/d3/src/index.js).
 */
const wrapNode = (el: Element | null): any => {
  const sel: any = {
    append: (tag: string) => {
      if (!el) return wrapNode(null);
      const child = document.createElementNS(SVG_NS, tag);
      el.appendChild(child);
      return wrapNode(child);
    },
    // d3 treats a null value as a no-op rather than the string "null".
    attr: (name: string, value: unknown) => {
      if (el && value != null) el.setAttribute(name, String(value));
      return sel;
    },
    style: () => sel,
    text: (value: unknown) => {
      if (el) el.textContent = value == null ? '' : String(value);
      return sel;
    },
    node: () => el,
  };
  return sel;
};
const d3Stub = {
  select: (target: any) =>
    wrapNode(typeof target === 'string' ? document.querySelector(target) : target ?? null),
};

const draw = async (spec: MusicSpec) => {
  const container = document.createElement('div');
  document.body.appendChild(container);
  await renderMusicSpec(container, spec, false, d3Stub);
  return container;
};

const glyphs = (c: HTMLElement) =>
  Array.from(c.querySelectorAll('text')).map((t) => t.textContent ?? '').join('');

/** Text with SMuFL private-use glyphs stripped, i.e. the human-readable part. */
const plainText = (c: HTMLElement) => glyphs(c).replace(/[\ue000-\uf8ff]/g, '');

const CODA = '\ue048';
const SEGNO = '\ue047';
/** Metronome note glyphs: quarter e1d5.. in Bravura's tempo range. */
const TEMPO_NOTE = /[\uec9f-\uecbf]/;

const NOTES4: MusicSpec['notes'] = [
  { keys: ['c/5'], duration: 'q' },
  { keys: ['d/5'], duration: 'q' },
  { keys: ['e/5'], duration: 'q' },
  { keys: ['f/5'], duration: 'q' },
];

describe('single-staff regression guard', () => {
  it('renders a single staff without a formatter error', async () => {
    // Passing spaceBetweenStaves as undefined breaks exactly this path.
    const c = await draw({ type: 'music', timeSignature: '4/4', notes: NOTES4 });
    expect(c.querySelector('svg')).not.toBeNull();
    expect(c.querySelectorAll('.vf-stave').length).toBe(1);
  });

  it('still renders a plain staff with no structural markings at all', async () => {
    const c = await draw({ type: 'music', notes: NOTES4 });
    expect(c.querySelectorAll('path').length).toBeGreaterThan(4);
  });
});

describe('tempo', () => {
  it('renders a metronome mark with a beat-unit glyph', async () => {
    const c = await draw({ type: 'music', notes: NOTES4,
      tempo: { duration: 'q', bpm: 120 } });
    expect(plainText(c)).toContain('=120');
    expect(glyphs(c)).toMatch(TEMPO_NOTE);
  });

  it('renders a name-only marking', async () => {
    const c = await draw({ type: 'music', notes: NOTES4, tempo: { name: 'Allegro' } });
    expect(plainText(c)).toContain('Allegro');
  });

  it('renders name and metronome mark together', async () => {
    const c = await draw({ type: 'music', notes: NOTES4,
      tempo: { name: 'Andante', duration: 'q', bpm: 72 } });
    const text = plainText(c);
    expect(text).toContain('Andante');
    expect(text).toContain('=72');
  });

  it('honours a dotted beat unit', async () => {
    const c = await draw({ type: 'music', notes: NOTES4,
      tempo: { duration: 'q', dots: 1, bpm: 90 } });
    expect(plainText(c)).toContain('=90');
  });

  it('reserves headroom so the marking is not clipped', async () => {
    const plain = await draw({ type: 'music', notes: NOTES4 });
    const tempoed = await draw({ type: 'music', notes: NOTES4, tempo: { name: 'Largo' } });
    const h = (c: HTMLElement) => Number(c.querySelector('svg')!.getAttribute('height'));
    expect(h(tempoed)).toBeGreaterThan(h(plain));
  });
});

describe('barlines and repeats', () => {
  it('draws a repeated section', async () => {
    const plain = await draw({ type: 'music', notes: NOTES4 });
    const repeated = await draw({ type: 'music', notes: NOTES4,
      beginBar: 'repeat-begin', endBar: 'repeat-end' });
    expect(repeated.querySelectorAll('path,rect').length)
      .toBeGreaterThan(plain.querySelectorAll('path,rect').length);
  });

  it('accepts "final" as an alias for the ending barline', async () => {
    const c = await draw({ type: 'music', notes: NOTES4, endBar: 'final' });
    expect(c.querySelector('svg')).not.toBeNull();
  });

  it('warns about an unknown barline instead of failing', async () => {
    const warn = jest.spyOn(console, 'warn').mockImplementation(() => {});
    const c = await draw({ type: 'music', notes: NOTES4, endBar: 'squiggly' });
    expect(c.querySelector('svg')).not.toBeNull();
    expect(warn).toHaveBeenCalledWith(expect.any(String), expect.stringContaining('squiggly'));
    warn.mockRestore();
  });
});

describe('navigation marks', () => {
  it('draws the coda symbol', async () => {
    const c = await draw({ type: 'music', notes: NOTES4, mark: 'coda' });
    expect(glyphs(c)).toContain(CODA);
  });

  it('draws the segno symbol', async () => {
    const c = await draw({ type: 'music', notes: NOTES4, mark: 'segno' });
    expect(glyphs(c)).toContain(SEGNO);
  });

  it.each([
    ['fine', 'Fine'],
    ['da-capo', 'D.C.'],
    ['da-capo-al-fine', 'D.C. al Fine'],
    ['dal-segno', 'D.S.'],
    ['dal-segno-al-fine', 'D.S. al Fine'],
  ])('renders %s as "%s"', async (mark, expected) => {
    const c = await draw({ type: 'music', notes: NOTES4, mark });
    expect(plainText(c)).toContain(expected);
  });

  it('pairs "To" with the coda glyph for to-coda', async () => {
    const c = await draw({ type: 'music', notes: NOTES4, mark: 'to-coda' });
    expect(plainText(c)).toContain('To');
    expect(glyphs(c)).toContain(CODA);
  });

  it('warns about an unknown mark instead of failing', async () => {
    const warn = jest.spyOn(console, 'warn').mockImplementation(() => {});
    const c = await draw({ type: 'music', notes: NOTES4, mark: 'da-capo-al-brunch' });
    expect(c.querySelector('svg')).not.toBeNull();
    expect(warn).toHaveBeenCalled();
    warn.mockRestore();
  });
});

describe('labels', () => {
  it('renders a volta bracket with its label', async () => {
    const c = await draw({ type: 'music', notes: NOTES4,
      volta: { type: 'begin', label: '1.' }, endBar: 'repeat-end' });
    expect(plainText(c)).toContain('1.');
  });

  it('renders a measure number', async () => {
    const c = await draw({ type: 'music', notes: NOTES4, measureNumber: 7 });
    expect(plainText(c)).toContain('7');
  });

  it('renders a section label', async () => {
    const c = await draw({ type: 'music', notes: NOTES4, section: 'A' });
    expect(plainText(c)).toContain('A');
  });
});

describe('fermata', () => {
  it('is a per-note articulation, not a structural mark', async () => {
    const c = await draw({ type: 'music', notes: [
      { keys: ['c/5'], duration: 'w', articulations: ['fermata-above'] },
    ] });
    expect(glyphs(c)).toMatch(/[\ue4c0-\ue4c7]/);
  });

  it('supports the below-staff form', async () => {
    const c = await draw({ type: 'music', notes: [
      { keys: ['c/5'], duration: 'w', articulations: ['fermata-below'] },
    ] });
    expect(glyphs(c)).toMatch(/[\ue4c0-\ue4c7]/);
  });
});

describe('grand staff', () => {
  it('renders two staves from a staves list', async () => {
    const c = await draw({ type: 'music', timeSignature: '4/4', staves: [
      { clef: 'treble', notes: NOTES4 },
      { clef: 'bass', notes: [{ keys: ['c/3'], duration: 'h' }, { keys: ['g/2'], duration: 'h' }] },
    ] });
    expect(c.querySelectorAll('.vf-stave').length).toBe(2);
  });

  it('draws both a treble and a bass clef', async () => {
    const c = await draw({ type: 'music', staves: [
      { clef: 'treble', notes: [{ keys: ['c/5'], duration: 'w' }] },
      { clef: 'bass', notes: [{ keys: ['c/3'], duration: 'w' }] },
    ] });
    expect(glyphs(c)).toContain('\ue050');   // treble
    expect(glyphs(c)).toContain('\ue062');   // bass
  });

  it('defaults the second staff to bass when no clef is given', async () => {
    const c = await draw({ type: 'music', staves: [
      { notes: [{ keys: ['c/5'], duration: 'w' }] },
      { notes: [{ keys: ['c/3'], duration: 'w' }] },
    ] });
    expect(glyphs(c)).toContain('\ue062');
  });

  it('grows the canvas per staff so the lower staff is not clipped', async () => {
    const one = await draw({ type: 'music', notes: NOTES4 });
    const two = await draw({ type: 'music', staves: [
      { clef: 'treble', notes: NOTES4 }, { clef: 'bass', notes: NOTES4 },
    ] });
    const h = (c: HTMLElement) => Number(c.querySelector('svg')!.getAttribute('height'));
    expect(h(two)).toBeGreaterThan(h(one));
  });

  it('scopes span indices to each staff', async () => {
    // A slur on the bass staff must index the bass staff's own notes; if it
    // indexed the treble list it would silently attach to the wrong notes.
    const warn = jest.spyOn(console, 'warn').mockImplementation(() => {});
    const c = await draw({ type: 'music', staves: [
      { clef: 'treble', notes: NOTES4 },
      { clef: 'bass', notes: [{ keys: ['c/3'], duration: 'h' }, { keys: ['g/2'], duration: 'h' }],
        slurs: [{ from: 0, to: 1 }] },
    ] });
    expect(c.querySelector('svg')).not.toBeNull();
    expect(warn).not.toHaveBeenCalled();
    warn.mockRestore();
  });

  it('reports an out-of-range span index against the right staff length', async () => {
    const warn = jest.spyOn(console, 'warn').mockImplementation(() => {});
    await draw({ type: 'music', staves: [
      { clef: 'treble', notes: NOTES4 },
      { clef: 'bass', notes: [{ keys: ['c/3'], duration: 'w' }], slurs: [{ from: 0, to: 3 }] },
    ] });
    expect(warn).toHaveBeenCalledWith(expect.any(String), expect.stringContaining('out of range'));
    warn.mockRestore();
  });

  it('carries dynamics and hairpins on individual staves', async () => {
    const c = await draw({ type: 'music', timeSignature: '4/4', staves: [
      { clef: 'treble', notes: NOTES4, hairpins: [{ from: 0, to: 3, type: 'cresc' }] },
      { clef: 'bass', notes: [
        { keys: ['c/3'], duration: 'h', dynamic: 'pp' },
        { keys: ['g/2'], duration: 'h', dynamic: 'ff' }] },
    ] });
    expect((glyphs(c).match(/[\ue520-\ue52f]/g) ?? []).length).toBeGreaterThan(0);
  });
});

describe('combined', () => {
  it('renders a grand staff with every structural marking at once', async () => {
    const c = await draw({
      type: 'music', timeSignature: '4/4', keySignature: 'C',
      tempo: { name: 'Allegro', duration: 'q', bpm: 132 },
      mark: 'to-coda', beginBar: 'repeat-begin', endBar: 'repeat-end',
      volta: { type: 'begin', label: '1.' }, measureNumber: 12, section: 'B',
      staves: [
        { clef: 'treble', notes: [
            { keys: ['c/5'], duration: 'q', dynamic: 'pp', articulations: ['staccato'] },
            { keys: ['e/5'], duration: 'q', ornaments: ['trill'] },
            { keys: ['g/5'], duration: 'q' },
            { keys: ['c/6'], duration: 'q', dynamic: 'fff', articulations: ['fermata-above'] }],
          slurs: [{ from: 0, to: 2 }], glissandos: [{ from: 2, to: 3 }],
          hairpins: [{ from: 0, to: 3, type: 'cresc' }] },
        { clef: 'bass', notes: [
            { keys: ['c/3'], duration: 'h' }, { keys: ['g/2'], duration: 'h' }] },
      ],
    });
    const text = plainText(c);
    expect(c.querySelectorAll('.vf-stave').length).toBe(2);
    expect(text).toContain('Allegro');
    expect(text).toContain('12');
    expect(text).toContain('gliss.');
    expect(glyphs(c)).toContain(CODA);
    expect((glyphs(c).match(/[\ue520-\ue52f]/g) ?? []).length).toBe(5);
  });
});
