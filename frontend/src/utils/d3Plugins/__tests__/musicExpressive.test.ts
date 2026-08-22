/**
 * @jest-environment jsdom
 *
 * Tests for expressive notation: slurs, ties, glissandos, dynamics, hairpins,
 * articulations and ornaments.
 *
 * These assert on SMuFL CODEPOINTS rather than on element counts, because the
 * failures in this area are silent and an element count cannot see them:
 *
 *   - A dynamics voice built with `new Voice` is dropped by System without an
 *     error, so the render "succeeds" with the marks missing.  Counting
 *     <text> elements finds 5 either way (clef + 4 noteheads).
 *   - An unknown articulation code is not rejected: VexFlow draws the literal
 *     ASCII word onto the staff, which also counts as one <text>.
 *
 * jsdom has no 2D canvas context, so text measurement degrades to empty
 * metrics; glyph emission (what is under test) still happens.
 */
// Polyfill structuredClone for jest's jsdom environment: vexflow 5.0.0 uses it
// in metrics.getFontInfo, and jest's jsdom global does not expose it on Node 20
// (a plain-data font-metrics clone, so JSON round-trip is a faithful stand-in).
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

// DOM-capturing d3 stub: the default no-op stub swallows the below-staff
// dynamics OVERLAY (drawDynamicsLayer, added in the D4 rewrite that moved
// dynamics off VexFlow's TextDynamics), so its output cannot be observed
// through the rendered SVG.  This variant wraps the real element passed to
// select(), so overlay <text> lands in the DOM where a query can see it.
const NS = 'http://www.w3.org/2000/svg';
const domSel = (el: Element): any => {
  const s: any = {
    node: () => el,
    append: (tag: string) => { const c = document.createElementNS(NS, tag); el.appendChild(c); return domSel(c); },
    attr: () => s, style: () => s, classed: () => s, html: () => s,
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
/** Below-staff dynamic marks the overlay draws, one <text> per mark. */
const DYNAMIC_SET = new Set(['ppp', 'pp', 'p', 'mp', 'mf', 'f', 'ff', 'fff', 'sf', 'sfz', 'rfz', 'fp']);
const dynMarks = (c: HTMLElement) =>
  Array.from(c.querySelectorAll('text'))
    .map((t) => t.textContent ?? '')
    .filter((t) => DYNAMIC_SET.has(t));

/** All glyph text in the rendered SVG, concatenated. */
const glyphs = (c: HTMLElement) =>
  Array.from(c.querySelectorAll('text')).map((t) => t.textContent ?? '').join('');

const countIn = (c: HTMLElement, re: RegExp) => (glyphs(c).match(re) ?? []).length;

/** SMuFL ranges, verified against vexflow 5.0.0 output. */
const DYNAMICS = /[\ue520-\ue52f]/g;      // e520 piano, e522 forte
const ORNAMENTS = /[\ue560-\ue56f]/g;     // e566 trill, e56c mordent
const ARTICULATIONS = /[\ue4a0-\ue4cf]|\ue1e7|\ue614|\ue631/g;

const NOTES4: MusicSpec['notes'] = [
  { keys: ['c/5'], duration: 'q' },
  { keys: ['d/5'], duration: 'q' },
  { keys: ['e/5'], duration: 'q' },
  { keys: ['f/5'], duration: 'q' },
];

describe('dynamics', () => {
  // Dynamics are now a below-staff d3 OVERLAY (drawDynamicsLayer), drawn as
  // one <text> per mark rather than VexFlow's per-letter SMuFL glyphs, so
  // these observe the overlay via the DOM-capturing stub and assert on the
  // mark text (the D4 rewrite superseded the old SMuFL-codepoint assertions).
  it('emits a dynamic mark for each annotated note', async () => {
    // The regression this guards: a dropped dynamics voice rendered nothing
    // and said nothing.
    const c = await drawDom({ type: 'music', notes: [
      { keys: ['c/5'], duration: 'h', dynamic: 'pp' },
      { keys: ['g/5'], duration: 'h', dynamic: 'ff' },
    ] });
    expect(dynMarks(c)).toEqual(expect.arrayContaining(['pp', 'ff']));
  });

  it('does not displace the notes it annotates', async () => {
    // The overlay takes no beat time, so a dynamic must not shift the notes.
    const withDyn = await draw({ type: 'music', timeSignature: '4/4',
      notes: NOTES4.map((n, i) => (i === 0 ? { ...n, dynamic: 'pp' } : n)) });
    const without = await draw({ type: 'music', timeSignature: '4/4', notes: NOTES4 });
    const xs = (c: HTMLElement) =>
      Array.from(c.querySelectorAll('.vf-stavenote')).length;
    expect(xs(withDyn)).toBe(xs(without));
  });

  it('skips an unknown mark rather than emitting it', async () => {
    const warn = jest.spyOn(console, 'warn').mockImplementation(() => {});
    const c = await drawDom({ type: 'music', notes: [
      { keys: ['c/5'], duration: 'q', dynamic: 'loud' },
    ] });
    expect(glyphs(c)).not.toMatch(/loud/);
    // drawDynamicsLayer warns with a single message string.
    expect(warn).toHaveBeenCalledWith(expect.stringContaining('loud'));
    warn.mockRestore();
  });

  it('renders a mark on some notes and not others', async () => {
    const c = await drawDom({ type: 'music', timeSignature: '4/4',
      notes: NOTES4.map((n, i) =>
        i === 0 ? { ...n, dynamic: 'pp' } : i === 3 ? { ...n, dynamic: 'fff' } : n) });
    // one overlay mark per annotated note: pp on the first, fff on the last.
    expect(dynMarks(c)).toEqual(['pp', 'fff']);
  });
});

describe('articulations and ornaments', () => {
  it('renders each articulation as a music glyph', async () => {
    const c = await draw({ type: 'music', timeSignature: '4/4', notes: [
      { keys: ['c/5'], duration: 'q', articulations: ['staccato'] },
      { keys: ['d/5'], duration: 'q', articulations: ['accent'] },
      { keys: ['e/5'], duration: 'q', articulations: ['tenuto'] },
      { keys: ['f/5'], duration: 'q', articulations: ['marcato'] },
    ] });
    expect(countIn(c, ARTICULATIONS)).toBe(4);
  });

  it('renders ornaments as music glyphs', async () => {
    const c = await draw({ type: 'music', notes: [
      { keys: ['c/5'], duration: 'h', ornaments: ['trill'] },
      { keys: ['d/5'], duration: 'h', ornaments: ['mordent'] },
    ] });
    expect(countIn(c, ORNAMENTS)).toBe(2);
  });

  it('never draws an unknown name as literal text on the staff', async () => {
    // new Articulation('bogus') renders the WORD "bogus" onto the staff.
    const warn = jest.spyOn(console, 'warn').mockImplementation(() => {});
    const c = await draw({ type: 'music', notes: [
      { keys: ['c/5'], duration: 'q', articulations: ['bogus'], ornaments: ['nope'] },
    ] });
    expect(glyphs(c)).not.toMatch(/bogus|nope/);
    warn.mockRestore();
  });

  it('rejects a raw VexFlow code, which is not a friendly name', async () => {
    const warn = jest.spyOn(console, 'warn').mockImplementation(() => {});
    const c = await draw({ type: 'music', notes: [
      { keys: ['c/5'], duration: 'q', articulations: ['a.'] },
    ] });
    expect(countIn(c, ARTICULATIONS)).toBe(0);
    warn.mockRestore();
  });
});

describe('spans', () => {
  it('draws a slur across a range of notes', async () => {
    const plain = await draw({ type: 'music', timeSignature: '4/4', notes: NOTES4 });
    const slurred = await draw({ type: 'music', timeSignature: '4/4', notes: NOTES4,
      slurs: [{ from: 0, to: 2 }] });
    expect(slurred.querySelectorAll('path').length)
      .toBeGreaterThan(plain.querySelectorAll('path').length);
  });

  it('draws a tie between two notes', async () => {
    const notes = [{ keys: ['c/5'], duration: 'q' }, { keys: ['c/5'], duration: 'q' }];
    const plain = await draw({ type: 'music', notes });
    const tied = await draw({ type: 'music', notes, ties: [{ from: 0, to: 1 }] });
    expect(tied.querySelectorAll('path').length)
      .toBeGreaterThan(plain.querySelectorAll('path').length);
  });

  it('labels a glissando, defaulting to "gliss."', async () => {
    const c = await draw({ type: 'music', notes: [
      { keys: ['c/5'], duration: 'h' }, { keys: ['g/5'], duration: 'h' },
    ], glissandos: [{ from: 0, to: 1 }] });
    expect(glyphs(c)).toContain('gliss.');
  });

  it('honours a custom glissando label', async () => {
    const c = await draw({ type: 'music', notes: [
      { keys: ['c/5'], duration: 'h' }, { keys: ['g/5'], duration: 'h' },
    ], glissandos: [{ from: 0, to: 1, text: 'port.' }] });
    expect(glyphs(c)).toContain('port.');
  });

  it('draws crescendo and diminuendo hairpins', async () => {
    const plain = await draw({ type: 'music', timeSignature: '4/4', notes: NOTES4 });
    for (const type of ['cresc', 'dim'] as const) {
      const c = await draw({ type: 'music', timeSignature: '4/4', notes: NOTES4,
        hairpins: [{ from: 0, to: 3, type }] });
      expect(c.querySelectorAll('path').length)
        .toBeGreaterThan(plain.querySelectorAll('path').length);
    }
  });

  it('drops a span with an out-of-range index without failing the render', async () => {
    const warn = jest.spyOn(console, 'warn').mockImplementation(() => {});
    const c = await draw({ type: 'music', notes: [{ keys: ['c/5'], duration: 'q' }],
      slurs: [{ from: 0, to: 9 }] });
    expect(c.querySelector('svg')).not.toBeNull();
    expect(warn).toHaveBeenCalledWith(expect.any(String), expect.stringContaining('out of range'));
    warn.mockRestore();
  });
});

describe('combined', () => {
  it('renders every expressive feature together', async () => {
    // drawDom so the below-staff dynamics overlay is observable alongside the
    // VexFlow-native articulations / ornaments / gliss label.
    const c = await drawDom({
      type: 'music', clef: 'treble', keySignature: 'C', timeSignature: '4/4',
      notes: [
        { keys: ['c/5'], duration: 'q', dynamic: 'pp', articulations: ['staccato'] },
        { keys: ['e/5'], duration: 'q', ornaments: ['trill'] },
        { keys: ['g/5'], duration: 'q' },
        { keys: ['c/6'], duration: 'q', dynamic: 'fff', articulations: ['accent'] },
      ],
      slurs: [{ from: 0, to: 2 }],
      glissandos: [{ from: 2, to: 3 }],
      hairpins: [{ from: 0, to: 3, type: 'cresc' }],
    });
    expect(dynMarks(c)).toEqual(['pp', 'fff']);       // two overlay marks
    expect(countIn(c, ARTICULATIONS)).toBe(2);
    expect(countIn(c, ORNAMENTS)).toBe(1);
    expect(glyphs(c)).toContain('gliss.');
  });
});
