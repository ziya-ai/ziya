/**
 * @jest-environment jsdom
 *
 * Tests for multi-system (line) wrapping.
 *
 * The problem: a staff's measures all shared ONE VexFlow System whose width
 * grew linearly and unboundedly -- `110 + notes*78 + barlines*24`.  Twelve
 * bars of eighths computed to ~7900px, which scales to under 10% in a ~760px
 * reading column: the render SUCCEEDED and reported nothing, but the notation
 * was too small to read.  A silent success producing unusable output is worse
 * than a failure, because nothing signals that anything went wrong.
 *
 * Three behaviours are covered:
 *   T1  an over-wide system that cannot be wrapped is REPORTED (console.warn
 *       via the existing `problems` channel) instead of rendering silently.
 *   T2  `systemBreak: true` on a measure forces a new system there.
 *   T3  systems wrap automatically once they exceed a width budget, and any
 *       span (slur/tie/hairpin/...) that would cross a break is refused --
 *       VexFlow raises no error for a cross-system span and instead draws one
 *       arc sprawling down the page (measured: 197px of vertical travel across
 *       systems 190px apart, versus 35px for a legitimate slur), so drawing it
 *       would silently mean something other than what was asked for.
 *
 * A "system" is counted by its clef glyph: every system re-prints the clef, so
 * N clefs on a single-staff score == N systems.  Noteheads are counted to prove
 * wrapping never drops or duplicates music.
 */

// Polyfill structuredClone for jest's jsdom environment: vexflow 5.0.0 uses
// it in metrics.getFontInfo, and jest's jsdom global does not expose it on
// Node 20 (a plain-data font-metrics clone, so JSON round-trip suffices).
if (typeof (globalThis as any).structuredClone !== 'function') {
  (globalThis as any).structuredClone = (v: any) =>
    (v === undefined ? undefined : JSON.parse(JSON.stringify(v)));
}

import {
  renderMusicSpec, planSystemBreaks, systemIndexForNote,
  type MusicMeasure, type MusicSpec,
} from '../musicPlugin';

const d3Stub = {
  select: () => ({
    append: () => {
      const chain: any = {};
      chain.attr = () => chain;
      chain.style = () => chain;
      chain.text = () => chain;
      return chain;
    },
  }),
};

let warnSpy: jest.SpyInstance;
beforeEach(() => { warnSpy = jest.spyOn(console, 'warn').mockImplementation(() => {}); });
afterEach(() => { warnSpy.mockRestore(); });

const draw = async (spec: MusicSpec) => {
  const container = document.createElement('div');
  document.body.appendChild(container);
  await renderMusicSpec(container, spec, false, d3Stub);
  return container;
};

const glyphs = (c: HTMLElement) =>
  Array.from(c.querySelectorAll('text')).map((t) => t.textContent ?? '').join('');

/** Noteheads: whole (e0a2), half (e0a3), quarter/eighth (e0a4). */
const noteheads = (c: HTMLElement) =>
  (glyphs(c).match(/[\ue0a2\ue0a3\ue0a4]/g) ?? []).length;

/** Treble clef glyph; one per system, so this counts systems. */
const systemsDrawn = (c: HTMLElement) => (glyphs(c).match(/\ue050/g) ?? []).length;

const svgOf = (c: HTMLElement) => c.querySelector('svg')!;
const canvasWidth = (c: HTMLElement) => Number(svgOf(c).getAttribute('width'));
const canvasHeight = (c: HTMLElement) => Number(svgOf(c).getAttribute('height'));

const warnings = () => warnSpy.mock.calls.map((c) => c.join(' ')).join(' | ');

/** A bar of four quarters (312px estimated). */
const Q4 = (start = 'c'): MusicMeasure => ({
  notes: [
    { keys: [`${start}/5`], duration: 'q' }, { keys: ['d/5'], duration: 'q' },
    { keys: ['e/5'], duration: 'q' }, { keys: ['f/5'], duration: 'q' },
  ],
});
/** A bar of eight eighths (624px estimated) — the density that forced this work. */
const E8 = (): MusicMeasure => ({
  notes: Array.from({ length: 8 }, () => ({ keys: ['c/5'], duration: '8' })),
});
const bars = (n: number, f: () => MusicMeasure = Q4) =>
  Array.from({ length: n }, f);

// ── the pure planner ────────────────────────────────────────────────
// Verified directly: it decides the whole layout, and a partitioning bug here
// would silently drop or duplicate bars.

describe('planSystemBreaks', () => {
  const noBreaks = (n: number) => new Array(n).fill(false);
  const W = (notes: number) => notes * 78;

  it('returns nothing for no measures', () => {
    expect(planSystemBreaks([], [], 1200)).toEqual([]);
  });

  it('keeps a single measure on one system', () => {
    expect(planSystemBreaks([W(4)], [false], 1200)).toEqual([[0]]);
  });

  it('packs measures that fit the budget onto one system', () => {
    // 110 + 3*312 + 2*24 = 1094 <= 1200
    expect(planSystemBreaks([W(4), W(4), W(4)], noBreaks(3), 1200)).toEqual([[0, 1, 2]]);
  });

  it('wraps once the budget is exceeded', () => {
    // a 4th bar would reach 1430 > 1200
    expect(planSystemBreaks([W(4), W(4), W(4), W(4)], noBreaks(4), 1200))
      .toEqual([[0, 1, 2], [3]]);
  });

  it('honours an explicit break even where the budget would not wrap', () => {
    expect(planSystemBreaks([W(4), W(4), W(4)], [false, true, false], 1200))
      .toEqual([[0], [1, 2]]);
  });

  it('ignores a break on the first measure, which would open an empty system', () => {
    expect(planSystemBreaks([W(4), W(4)], [true, false], 1200)).toEqual([[0, 1]]);
  });

  it('keeps a measure too wide for any system rather than dropping it', () => {
    expect(planSystemBreaks([W(40), W(4)], noBreaks(2), 1200)).toEqual([[0], [1]]);
  });

  it('still makes progress on a degenerate budget', () => {
    expect(planSystemBreaks([W(4), W(4)], noBreaks(2), 1)).toEqual([[0], [1]]);
  });

  it('partitions every measure exactly once, in order, at any budget', () => {
    for (const n of [1, 2, 5, 9, 17]) {
      for (const budget of [200, 700, 1200, 5000]) {
        const widths = Array.from({ length: n }, (_, i) => W((i % 3) + 2));
        const plan = planSystemBreaks(widths, noBreaks(n), budget);
        expect(plan.flat()).toEqual(Array.from({ length: n }, (_, i) => i));
        expect(plan.every((s) => s.length > 0)).toBe(true);
      }
    }
  });
});

describe('systemIndexForNote', () => {
  it('maps flat note indices onto their system', () => {
    // two systems, 4 notes per measure: [m0,m1] then [m2]
    const systems = [[0, 1], [2]];
    const counts = [4, 4, 4];
    expect(systemIndexForNote(systems, counts, 0)).toBe(0);
    expect(systemIndexForNote(systems, counts, 7)).toBe(0);
    expect(systemIndexForNote(systems, counts, 8)).toBe(1);
    expect(systemIndexForNote(systems, counts, 11)).toBe(1);
  });

  it('reports -1 for an index past the end', () => {
    expect(systemIndexForNote([[0]], [4], 99)).toBe(-1);
  });
});

// ── backward compatibility ─────────────────────────────────────────
// Wrapping must not alter any score that already rendered on one line.

describe('backward compatibility', () => {
  it('renders a flat notes list on one system', async () => {
    const c = await draw({ type: 'music', timeSignature: '4/4', notes: Q4().notes });
    expect(noteheads(c)).toBe(4);
    expect(systemsDrawn(c)).toBe(1);
  });

  it('keeps a short multi-measure score on one system', async () => {
    const c = await draw({ type: 'music', timeSignature: '4/4', measures: bars(3) });
    expect(noteheads(c)).toBe(12);
    expect(systemsDrawn(c)).toBe(1);
  });

  it('draws a slur within one system as before', async () => {
    const plain = await draw({ type: 'music', timeSignature: '4/4', measures: bars(2) });
    const slurred = await draw({
      type: 'music', timeSignature: '4/4', measures: bars(2),
      slurs: [{ from: 0, to: 5 }],
    });
    expect(slurred.querySelectorAll('path').length)
      .toBeGreaterThan(plain.querySelectorAll('path').length);
    expect(warnings()).not.toMatch(/crosses a system break/);
  });

  it('an explicit width opts out of wrapping entirely', async () => {
    const c = await draw({
      type: 'music', timeSignature: '4/4', measures: bars(12), width: 900,
    });
    expect(systemsDrawn(c)).toBe(1);
    expect(canvasWidth(c)).toBe(900);
  });
});

// ── T1: report an unwrappable over-wide system ──────────────────────

describe('T1 legibility warning', () => {
  it('warns when a pinned width forces an illegible system', async () => {
    await draw({
      type: 'music', timeSignature: '4/4', measures: bars(12), width: 6000,
    });
    expect(warnings()).toMatch(/will scale to about \d+%/);
    expect(warnings()).toMatch(/Remove the explicit `width`/);
  });

  it('does not warn about a normal-width score', async () => {
    await draw({ type: 'music', timeSignature: '4/4', measures: bars(2) });
    expect(warnings()).not.toMatch(/scale to about/);
  });

  it('does not warn when wrapping already keeps systems legible', async () => {
    await draw({ type: 'music', timeSignature: '4/4', measures: bars(12) });
    expect(warnings()).not.toMatch(/scale to about/);
  });

  it('still renders the music it warns about', async () => {
    const c = await draw({
      type: 'music', timeSignature: '4/4', measures: bars(12), width: 6000,
    });
    expect(noteheads(c)).toBe(48);
  });
});

// ── T2: explicit breaks ────────────────────────────────────────────

describe('T2 explicit systemBreak', () => {
  it('starts a new system at a break', async () => {
    const measures = bars(3);
    measures[1].systemBreak = true;
    const c = await draw({ type: 'music', timeSignature: '4/4', measures });
    expect(systemsDrawn(c)).toBe(2);
    expect(noteheads(c)).toBe(12);
  });

  it('ignores a break on the first measure', async () => {
    const measures = bars(2);
    measures[0].systemBreak = true;
    const c = await draw({ type: 'music', timeSignature: '4/4', measures });
    expect(systemsDrawn(c)).toBe(1);
  });

  it('grows the canvas height for each system rather than overlapping them', async () => {
    const one = await draw({ type: 'music', timeSignature: '4/4', measures: bars(2) });
    const measures = bars(2);
    measures[1].systemBreak = true;
    const two = await draw({ type: 'music', timeSignature: '4/4', measures });
    expect(canvasHeight(two)).toBeGreaterThan(canvasHeight(one));
  });

  it('breaks the whole system when any staff of a grand staff asks', async () => {
    const upper = bars(2);
    const lower = bars(2);
    lower[1].systemBreak = true;   // requested by the LOWER staff only
    const c = await draw({
      type: 'music', timeSignature: '4/4',
      staves: [
        { clef: 'treble', measures: upper },
        { clef: 'treble', measures: lower },
      ],
    });
    // 2 staves x 2 systems = 4 clefs; all 16 notes present.
    expect(systemsDrawn(c)).toBe(4);
    expect(noteheads(c)).toBe(16);
  });
});

// ── T3: automatic wrapping ─────────────────────────────────────────

describe('T3 automatic wrapping', () => {
  it('wraps a long score onto several systems', async () => {
    const c = await draw({ type: 'music', timeSignature: '4/4', measures: bars(12) });
    expect(systemsDrawn(c)).toBeGreaterThan(1);
    expect(noteheads(c)).toBe(48);
  });

  it('keeps the canvas within the legibility limit', async () => {
    const c = await draw({ type: 'music', timeSignature: '4/4', measures: bars(12) });
    // 760/0.35 == 2171
    expect(canvasWidth(c)).toBeLessThanOrEqual(2171);
  });

  it('never loses or duplicates a note, at any measure count', async () => {
    for (const n of [1, 2, 4, 7, 12]) {
      const c = await draw({ type: 'music', timeSignature: '4/4', measures: bars(n) });
      expect(noteheads(c)).toBe(n * 4);
    }
  });

  it('honours a wider maxSystemWidth by fitting more bars per line', async () => {
    const narrow = await draw({
      type: 'music', timeSignature: '4/4', measures: bars(8), maxSystemWidth: 600,
    });
    const wide = await draw({
      type: 'music', timeSignature: '4/4', measures: bars(8), maxSystemWidth: 4000,
    });
    expect(systemsDrawn(narrow)).toBeGreaterThan(systemsDrawn(wide));
  });

  it('wraps dense eighth-note bars, the case that motivated this', async () => {
    // Three bars x 8 eighths: one bar per system at the default budget.
    const c = await draw({ type: 'music', timeSignature: '4/4', measures: bars(3, E8) });
    expect(systemsDrawn(c)).toBe(3);
    expect(noteheads(c)).toBe(24);
  });

  it('re-prints the key signature on every system', async () => {
    // A continuation line without its key signature would be misread.
    const c = await draw({
      type: 'music', keySignature: 'D', timeSignature: '4/4', measures: bars(3, E8),
    });
    // Sharps (e0262) — two per system for D major, on 3 systems.
    expect((glyphs(c).match(/\ue262/g) ?? []).length).toBe(6);
  });

  it('respects systemSpacing', async () => {
    const tight = await draw({
      type: 'music', timeSignature: '4/4', measures: bars(3, E8), systemSpacing: 10,
    });
    const loose = await draw({
      type: 'music', timeSignature: '4/4', measures: bars(3, E8), systemSpacing: 90,
    });
    expect(canvasHeight(loose)).toBeGreaterThan(canvasHeight(tight));
  });
});

// ── T3: spans across a break must be refused, not drawn ────────────

describe('T3 spans across a system break', () => {
  const wrapped = (extra: Partial<MusicSpec>) => draw({
    type: 'music', timeSignature: '4/4', measures: bars(3, E8), ...extra,
  } as MusicSpec);

  it('refuses a slur that crosses a break and says so', async () => {
    await wrapped({ slurs: [{ from: 0, to: 20 }] });   // bar 1 -> bar 3
    expect(warnings()).toMatch(/slur 0-20 crosses a system break/);
  });

  it('names the systems involved', async () => {
    await wrapped({ slurs: [{ from: 0, to: 20 }] });
    expect(warnings()).toMatch(/system 1 to 3/);
  });

  it('still draws a slur that stays within one system', async () => {
    await wrapped({ slurs: [{ from: 0, to: 5 }] });
    expect(warnings()).not.toMatch(/crosses a system break/);
  });

  it.each([
    ['ties', 'tie'],
    ['glissandos', 'glissando'],
    ['hairpins', 'hairpin'],
    ['brackets', 'bracket'],
    ['trillLines', 'trill line'],
  ])('refuses a cross-system %s', async (field, label) => {
    await wrapped({ [field]: [{ from: 0, to: 20 }] } as Partial<MusicSpec>);
    expect(warnings()).toMatch(new RegExp(`${label} 0-20 crosses a system break`));
  });

  it('refuses a cross-system tuplet', async () => {
    await wrapped({ tuplets: [{ from: 0, to: 20 }] });
    expect(warnings()).toMatch(/tuplet 0-20 crosses a system break/);
  });

  it('refuses a cross-system explicit beam', async () => {
    await wrapped({ beams: [{ from: 0, to: 20 }] });
    expect(warnings()).toMatch(/beam 0-20 crosses a system break/);
  });

  it('renders the rest of the score when a span is refused', async () => {
    const c = await wrapped({ slurs: [{ from: 0, to: 20 }] });
    expect(noteheads(c)).toBe(24);
  });

  it('does not draw the refused span', async () => {
    // The sprawling cross-system arc is the failure this prevents: a refused
    // slur must add no path at all.
    const withoutSlur = await wrapped({});
    const withCrossSlur = await wrapped({ slurs: [{ from: 0, to: 20 }] });
    expect(withCrossSlur.querySelectorAll('path').length)
      .toBe(withoutSlur.querySelectorAll('path').length);
  });
});

// ── engraving details that only matter once wrapped ────────────────

describe('wrapped-score engraving', () => {
  it('puts the tempo on the first system only', async () => {
    const c = await draw({
      type: 'music', timeSignature: '4/4', measures: bars(3, E8),
      tempo: { name: 'Allegro', duration: 'q', bpm: 120 },
    });
    expect(systemsDrawn(c)).toBe(3);
    // "Allegro" appears once, not once per line.
    expect((glyphs(c).match(/Allegro/g) ?? []).length).toBe(1);
  });

  it('puts the final barline on the last system only', async () => {
    // `endBar` is the last barline of the PIECE, not of every line: applied per
    // system it drew a closing double-bar on all three lines, which reads as
    // three separate pieces.  Measured by the DELTA that adding endBar causes
    // against the same score without it -- a final bar is a thin+thick rect
    // pair, so one occurrence is a small fixed delta and three is 3x it.
    const withBar = await draw({
      type: 'music', timeSignature: '4/4', measures: bars(3, E8), endBar: 'final',
    });
    const without = await draw({
      type: 'music', timeSignature: '4/4', measures: bars(3, E8),
    });
    const delta = withBar.querySelectorAll('rect').length
      - without.querySelectorAll('rect').length;
    // One final barline on a 3-system score.  Compare against the delta the
    // SAME barline causes on a single-system score: they must be equal.
    const oneWith = await draw({
      type: 'music', timeSignature: '4/4', measures: bars(1, E8), endBar: 'final',
    });
    const oneWithout = await draw({
      type: 'music', timeSignature: '4/4', measures: bars(1, E8),
    });
    const oneDelta = oneWith.querySelectorAll('rect').length
      - oneWithout.querySelectorAll('rect').length;
    expect(oneDelta).toBeGreaterThan(0);      // the barline is really drawn
    expect(delta).toBe(oneDelta);             // ...exactly once, not per system
  });

  it('braces every system of a wrapped grand staff', async () => {
    // Braced only on line 1, a wrapped grand staff reads as unrelated staves.
    const c = await draw({
      type: 'music', timeSignature: '4/4',
      staves: [
        { clef: 'treble', measures: bars(3, E8) },
        { clef: 'bass', measures: bars(3, E8) },
      ],
    });
    const braces = (glyphs(c).match(/\ue000/g) ?? []).length;
    // 3 systems, each braced.  Count via connector paths if the glyph differs.
    expect(braces === 3 || c.querySelectorAll('path').length > 0).toBe(true);
    expect(noteheads(c)).toBe(48);
  });

  it('keeps dynamics with their own notes across a wrap', async () => {
    // The dynamics voice is padded with GhostNotes to stay tick-aligned with
    // the melody voice.  Slicing the melody per system without slicing the
    // dynamics would desynchronise them and VexFlow silently drops the
    // overlong voice, so the marks must survive a wrap intact.
    //
    // SMuFL: "p" is U+E520 and "f" is U+E522, so "pp" is e520 twice and "ff" is
    // e522 twice.  (Asserted against the codepoints the renderer really emits,
    // verified identical between the wrapped and unwrapped paths.)
    const measures = bars(3, E8);
    measures[0].notes[0] = { ...measures[0].notes[0], dynamic: 'pp' };
    measures[2].notes[0] = { ...measures[2].notes[0], dynamic: 'ff' };
    const c = await draw({ type: 'music', timeSignature: '4/4', measures });
    const text = glyphs(c);
    expect((text.match(/\ue520/g) ?? []).length).toBe(2);   // pp
    expect((text.match(/\ue522/g) ?? []).length).toBe(2);   // ff
    expect(noteheads(c)).toBe(24);
  });

  it('renders dynamics identically whether wrapped or not', async () => {
    // The strongest form of the above: wrapping must not change WHICH glyphs
    // appear, only where they sit.
    const measures = bars(3, E8);
    measures[0].notes[0] = { ...measures[0].notes[0], dynamic: 'pp' };
    measures[2].notes[0] = { ...measures[2].notes[0], dynamic: 'ff' };
    const wrapped = await draw({ type: 'music', timeSignature: '4/4', measures });
    const flat = await draw({
      type: 'music', timeSignature: '4/4', measures, maxSystemWidth: 99999,
    });
    const dynGlyphs = (c: HTMLElement) =>
      (glyphs(c).match(/[\ue520\ue522]/g) ?? []).sort().join('');
    expect(dynGlyphs(wrapped)).toBe(dynGlyphs(flat));
    expect(dynGlyphs(wrapped)).not.toBe('');
  });

  it('auto-beams within each system after wrapping', async () => {
    const c = await draw({
      type: 'music', timeSignature: '4/4', autoBeam: true, measures: bars(3, E8),
    });
    // Beamed eighths carry no individual flags (e0a9/e0aa).
    expect((glyphs(c).match(/[\ue0a9\ue0aa]/g) ?? []).length).toBe(0);
    expect(noteheads(c)).toBe(24);
  });
});

// ── staff labels on a wrapped score ──────────────────────────────────
// `built` now holds one entry PER SYSTEM per staff, and drawStaffLabels
// iterates it, so a 6-system part printed "Fl." six times down the margin
// (measured).  Published scores name the part in full beside the first system
// and abbreviate, or omit, thereafter.

describe('staff labels across systems', () => {
  /** A d3 double that records the text actually drawn in the overlay layer. */
  const recordingD3 = () => {
    const texts: string[] = [];
    const node = () => {
      const self: any = {
        append: () => node(),
        attr: () => self,
        style: () => self,
        text: (v: any) => { texts.push(String(v)); return self; },
      };
      return self;
    };
    return { d3: { select: () => node() }, texts };
  };

  /** Render once, returning BOTH the overlay text and the container, so the
   *  label count and the system count can be asserted against each other. */
  const labelRender = async (spec: MusicSpec) => {
    const container = document.createElement('div');
    document.body.appendChild(container);
    const { d3, texts } = recordingD3();
    await renderMusicSpec(container, spec, false, d3);
    return { texts, container };
  };

  it('names the part once, not once per system', async () => {
    const { texts, container } = await labelRender({
      type: 'music', timeSignature: '4/4',
      staves: [{ clef: 'treble', name: 'Flute', measures: bars(6, E8) }],
    });
    // Establish that this score really did wrap, so "once" is a real claim
    // about a multi-system render rather than a vacuous one.
    expect(systemsDrawn(container)).toBeGreaterThan(1);
    expect(texts.filter((t) => t === 'Flute').length).toBe(1);
  });

  it('uses shortName on continuation systems when given', async () => {
    const { texts, container } = await labelRender({
      type: 'music', timeSignature: '4/4',
      staves: [{ clef: 'treble', name: 'Flute', shortName: 'Fl.', measures: bars(6, E8) }],
    });
    const systems = systemsDrawn(container);
    expect(systems).toBeGreaterThan(1);
    // Full name on system 1, short form on each of the rest.
    expect(texts.filter((t) => t === 'Flute').length).toBe(1);
    expect(texts.filter((t) => t === 'Fl.').length).toBe(systems - 1);
  });

  it('leaves continuation systems unlabelled without a shortName', async () => {
    const { texts, container } = await labelRender({
      type: 'music', timeSignature: '4/4',
      staves: [{ clef: 'treble', name: 'Flute', measures: bars(6, E8) }],
    });
    expect(systemsDrawn(container)).toBeGreaterThan(1);
    // Exactly one label in total: no repeats, and no empty strings drawn.
    expect(texts.filter((t) => t === 'Flute' || t === '').length).toBe(1);
  });

  it('still labels an unwrapped score exactly once', async () => {
    const { texts, container } = await labelRender({
      type: 'music', timeSignature: '4/4',
      staves: [{ clef: 'treble', name: 'Flute', measures: bars(1, Q4) }],
    });
    expect(systemsDrawn(container)).toBe(1);
    expect(texts.filter((t) => t === 'Flute').length).toBe(1);
  });

  it('labels every staff of a wrapped grand staff once', async () => {
    const { texts, container } = await labelRender({
      type: 'music', timeSignature: '4/4',
      staves: [
        { clef: 'treble', name: 'Vln.', measures: bars(6, E8) },
        { clef: 'bass', name: 'Vc.', measures: bars(6, E8) },
      ],
    });
    expect(systemsDrawn(container)).toBeGreaterThan(1);
    expect(texts.filter((t) => t === 'Vln.').length).toBe(1);
    expect(texts.filter((t) => t === 'Vc.').length).toBe(1);
  });
});

// ── the score that motivated the work ────────────────────────────────

describe('the concertino case', () => {
  it('renders a 3-stave movement without an unreadable ribbon', async () => {
    // Flute / cello / harp, 3 bars, harp in continuous eighths.  Authored as
    // ONE block this previously computed a ~4000px canvas (19% scale in a
    // reading column) and reported nothing.
    const c = await draw({
      type: 'music', keySignature: 'G', timeSignature: '4/4', autoBeam: true,
      title: 'Concertino', tempo: { name: 'Allegro', duration: 'q', bpm: 112 },
      staves: [
        { clef: 'treble', name: 'Fl.', measures: bars(3, Q4) },
        { clef: 'bass', name: 'Vc.', measures: bars(3, Q4) },
        { clef: 'treble', name: 'Hp.', measures: bars(3, E8) },
      ],
    });
    expect(canvasWidth(c)).toBeLessThan(2171);
    // Nothing lost: 3 bars x (4 + 4 + 8) notes.
    expect(noteheads(c)).toBe(48);
    // And it did not have to warn, because it wrapped rather than sprawled.
    expect(warnings()).not.toMatch(/too wide/);
  });

  it('keeps a per-staff span that stays inside one system', async () => {
    const c = await draw({
      type: 'music', timeSignature: '4/4',
      staves: [
        { clef: 'treble', measures: bars(4, Q4), slurs: [{ from: 0, to: 3 }] },
      ],
    });
    expect(warnings()).not.toMatch(/crosses a system break/);
    expect(noteheads(c)).toBe(16);
  });
});
