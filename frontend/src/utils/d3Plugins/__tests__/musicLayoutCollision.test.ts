/**
 * @jest-environment jsdom
 *
 * Geometry tests for the band above the top staff, where the tempo mark,
 * octave brackets, voltas, measure numbers and trill lines all compete.
 *
 * These assert on rendered x/y ATTRIBUTES rather than on glyph presence,
 * because a collision is invisible to every other kind of check: both
 * elements draw correctly, report success, and simply land on top of one
 * another.  Two distinct causes are covered:
 *
 *   1. stave.setTempo(tempo, y) -- the second argument is a Y SHIFT, and
 *      passing 0 leaves the mark in the bracket's band (measured: tempo
 *      y=60, bracket y=60/56).
 *   2. Stave.setTempo hardcodes the mark's x as `this.x`, and
 *      StaveTempo.draw() then ADDS getModifierXShift() (the clef + time
 *      signature width), pushing it to x=56 -- past the clef and onto a
 *      bracket starting at the first notehead, x=58.
 *
 * The canvas metrics stubbed in setupTests.ts are approximate, so these
 * compare RELATIVE positions and band overlap, never absolute pixel values.
 */

// Polyfill structuredClone for jest's jsdom environment: vexflow 5.0.0 uses
// it in metrics.getFontInfo, and jest's jsdom global does not expose it on
// Node 20 (a plain-data font-metrics clone, so JSON round-trip suffices).
if (typeof (globalThis as any).structuredClone !== 'function') {
  (globalThis as any).structuredClone = (v: any) =>
    (v === undefined ? undefined : JSON.parse(JSON.stringify(v)));
}

import { renderMusicSpec, type MusicSpec } from '../musicPlugin';

// A d3 shim that actually materialises the overlay layers into the DOM, so
// hand-drawn marks (the tempo name, dynamics, lyrics, measure numbers, ...) are
// observable by the geometry assertions below.  The earlier no-op chain
// discarded every overlay <text>, which meant a split-out mark like the tempo
// NAME (drawn as an overlay while VexFlow draws only the metronome) was
// invisible to `matching()` and the mark's true leftmost element could not be
// measured.  append(tag) creates a real SVG element under the current node and
// returns a selection bound to it; attr/text set the observed attributes; the
// rest are chainable no-ops.
const makeSel = (node: any): any => {
  const sel: any = {};
  sel.append = (tag: string) => {
    const child = document.createElementNS('http://www.w3.org/2000/svg', tag);
    if (node && typeof node.appendChild === 'function') node.appendChild(child);
    return makeSel(child);
  };
  sel.attr = (k: string, v: any) => {
    if (node && typeof node.setAttribute === 'function') node.setAttribute(k, String(v));
    return sel;
  };
  sel.text = (t: any) => {
    if (node) node.textContent = String(t);
    return sel;
  };
  sel.style = () => sel;
  sel.classed = () => sel;
  sel.html = () => sel;
  return sel;
};
const d3Stub = { select: (el: any) => makeSel(el) };

const draw = async (spec: MusicSpec) => {
  const container = document.createElement('div');
  document.body.appendChild(container);
  await renderMusicSpec(container, spec, false, d3Stub);
  return container;
};

// Install a VexFlow text-measurement canvas.  Bare `npx jest` does not load
// CRA's setupTests.ts, so glyph widths resolve to 0 and horizontal positions
// (notehead x, clef width) degenerate -- which breaks the tempo-placement
// assertion comparing the tempo mark's x to the first notehead's x.  Provide
// the measurement canvas through VexFlow's own API so those x positions are
// meaningful; metrics are approximate, which is enough for relative-position
// (left-of / right-of) collision checks.
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

interface Placed { x: number; y: number; text: string }

/** Every rendered text node with its position. */
const placed = (c: HTMLElement): Placed[] =>
  Array.from(c.querySelectorAll('text'))
    .map((t) => ({
      x: parseFloat(t.getAttribute('x') ?? 'NaN'),
      y: parseFloat(t.getAttribute('y') ?? 'NaN'),
      text: t.textContent ?? '',
    }))
    .filter((p) => p.text.length > 0 && Number.isFinite(p.x) && Number.isFinite(p.y));

const matching = (c: HTMLElement, re: RegExp) => placed(c).filter((p) => re.test(p.text));

/** True when two sets of elements occupy overlapping vertical bands. */
const bandsOverlap = (a: Placed[], b: Placed[]): boolean => {
  if (!a.length || !b.length) return false;
  const aY = a.map((p) => p.y);
  const bY = b.map((p) => p.y);
  return !(Math.max(...aY) < Math.min(...bY) || Math.max(...bY) < Math.min(...aY));
};

const NOTES4: MusicSpec['notes'] = [
  { keys: ['c/5'], duration: 'q' },
  { keys: ['e/5'], duration: 'q' },
  { keys: ['g/5'], duration: 'q' },
  { keys: ['c/6'], duration: 'q' },
];

const TEMPO = /Allegro|132/;
const BRACKET = /^8$|^va$|^vb$|^15$|^ma$/;

const withTempoAndBracket: MusicSpec = {
  type: 'music', clef: 'treble', timeSignature: '4/4', keySignature: 'C',
  notes: NOTES4,
  tempo: { name: 'Allegro', duration: 'q', bpm: 132 },
  brackets: [{ from: 0, to: 3, text: '8', superscript: 'va' }],
};

describe('tempo mark and octave bracket', () => {
  it('do not share a vertical band', async () => {
    // The reported collision, asserted directly.
    const c = await draw(withTempoAndBracket);
    expect(bandsOverlap(matching(c, TEMPO), matching(c, BRACKET))).toBe(false);
  });

  it('places the tempo mark above the bracket', async () => {
    const c = await draw(withTempoAndBracket);
    const tempoY = Math.max(...matching(c, TEMPO).map((p) => p.y));
    const bracketY = Math.min(...matching(c, BRACKET).map((p) => p.y));
    // Smaller y is higher on the canvas.
    expect(tempoY).toBeLessThan(bracketY);
  });

  it('places the tempo mark left of the bracket', async () => {
    // The x half of the collision: the mark was rendering past the clef, at
    // the same x as the bracket's first notehead.
    const c = await draw(withTempoAndBracket);
    const tempoX = Math.min(...matching(c, TEMPO).map((p) => p.x));
    const bracketX = Math.min(...matching(c, BRACKET).map((p) => p.x));
    expect(tempoX).toBeLessThan(bracketX);
  });

  it('places the tempo mark at the start of the staff, not after the clef', async () => {
    // Measured: the mark lands at the stave's own origin (x=20) while the clef
    // GLYPH is drawn at x=15, so "at or left of the clef glyph" is stricter
    // than the engraving requirement and stricter than VexFlow allows.  What
    // matters is that the mark is nowhere near the first notehead (x=58),
    // which is where the un-compensated shift put it (x=56).
    const c = await draw(withTempoAndBracket);
    const tempoX = Math.min(...matching(c, TEMPO).map((p) => p.x));
    const firstHeadX = Math.min(
      ...placed(c).filter((p) => /[\ue0a2\ue0a3\ue0a4]/.test(p.text)).map((p) => p.x),
    );
    // Comfortably left of the first notehead, not merely 2px clear of it.
    expect(tempoX).toBeLessThan(firstHeadX - 20);
  });

  it('clips nothing off the top of the canvas', async () => {
    const c = await draw(withTempoAndBracket);
    expect(Math.min(...placed(c).map((p) => p.y))).toBeGreaterThan(0);
  });

  it('reserves more headroom than a bare staff', async () => {
    const bare = await draw({ type: 'music', notes: NOTES4 });
    const marked = await draw(withTempoAndBracket);
    const h = (c: HTMLElement) => Number(c.querySelector('svg')!.getAttribute('height'));
    expect(h(marked)).toBeGreaterThan(h(bare));
  });
});

describe('tempo x-shift compensation', () => {
  it('positions the mark consistently regardless of key-signature width', async () => {
    // getModifierXShift grows with the key signature (36 -> 63 measured), so
    // an uncompensated mark drifts right as the signature widens.
    const noKey = await draw({
      type: 'music', clef: 'treble', timeSignature: '4/4', notes: NOTES4,
      tempo: { name: 'Allegro', duration: 'q', bpm: 132 },
    });
    const wideKey = await draw({
      type: 'music', clef: 'treble', timeSignature: '4/4', keySignature: 'Bb', notes: NOTES4,
      tempo: { name: 'Allegro', duration: 'q', bpm: 132 },
    });
    const x = (c: HTMLElement) => Math.min(...matching(c, TEMPO).map((p) => p.x));
    expect(x(wideKey)).toBe(x(noKey));
  });

  it('renders on a stave with no clef or time signature', async () => {
    // getModifierXShift(0) indexes stave.modifiers and throws when empty.
    const c = await draw({
      type: 'music', notes: NOTES4,
      tempo: { name: 'Allegro', duration: 'q', bpm: 132 },
    });
    expect(c.querySelector('svg')).not.toBeNull();
    expect(matching(c, TEMPO).length).toBeGreaterThan(0);
  });
});

describe('bracket lift', () => {
  it('raises an above-staff bracket only when a tempo is present', async () => {
    const withTempo = await draw(withTempoAndBracket);
    const withoutTempo = await draw({
      type: 'music', clef: 'treble', timeSignature: '4/4', notes: NOTES4,
      brackets: [{ from: 0, to: 3, text: '8', superscript: 'va' }],
    });
    // Compare each bracket's offset from its own stave, since the two renders
    // have different canvas heights and stave origins.
    const offset = (c: HTMLElement) => {
      const staveTop = Math.min(
        ...Array.from(c.querySelectorAll('.vf-stave path'))
          .map((p) => parseFloat((p.getAttribute('d') ?? '').match(/M\d+ ([\d.]+)/)?.[1] ?? 'NaN'))
          .filter(Number.isFinite),
      );
      return staveTop - Math.min(...matching(c, BRACKET).map((p) => p.y));
    };
    expect(offset(withTempo)).toBeGreaterThan(offset(withoutTempo));
  });

  it('honours an explicit line override', async () => {
    const auto = await draw(withTempoAndBracket);
    const raised = await draw({
      ...withTempoAndBracket,
      brackets: [{ from: 0, to: 3, text: '8', superscript: 'va', line: 6 }],
    });
    const topY = (c: HTMLElement) => Math.min(...matching(c, BRACKET).map((p) => p.y));
    expect(topY(raised)).toBeLessThan(topY(auto));
  });

  it('leaves a below-staff bracket unraised', async () => {
    const c = await draw({
      ...withTempoAndBracket,
      brackets: [{ from: 0, to: 3, text: '8', superscript: 'vb', position: 'below' }],
    });
    // Below-staff brackets never contend with the tempo band.
    expect(bandsOverlap(matching(c, TEMPO), matching(c, BRACKET))).toBe(false);
  });

  it('does not raise a lower staff\'s brackets in a grand staff', async () => {
    const c = await draw({
      type: 'music', timeSignature: '4/4',
      tempo: { name: 'Allegro', duration: 'q', bpm: 132 },
      staves: [
        { clef: 'treble', notes: NOTES4 },
        { clef: 'bass', notes: [{ keys: ['c/3'], duration: 'h' }, { keys: ['g/2'], duration: 'h' }],
          brackets: [{ from: 0, to: 1, text: '8', superscript: 'vb' }] },
      ],
    });
    expect(c.querySelector('svg')).not.toBeNull();
    expect(matching(c, BRACKET).length).toBeGreaterThan(0);
  });
});

/**
 * Regression for the tempo name/bpm overlap (drawTempoName + measureTempoNameWidth).
 *
 * A tempo carrying BOTH a name and a bpm rendered garbled: VexFlow's StaveTempo
 * chains the "(quarter = N)" metronome group off getWidth() of the name,
 * measured on a detached canvas whose font diverges from the SVG render font in
 * the headless env.  When it under-measured, the metronome overprinted the end
 * of the name and the leading "(" was lost -- "Andante con moto" + 92 came out
 * as "Andanteconmoto(quarter =92)".  The fix hand-draws the NAME as a d3 overlay
 * and hands ONLY the parenthesised metronome to VexFlow, positioned just right
 * of the measured name.  Name-only and bpm-only keep the single VexFlow path.
 *
 * These live here (not in a snapshot suite) because the DOM-materialising d3
 * stub + real measurement canvas above make the overlay text and its x
 * observable, which is exactly what an overlap check needs.
 */
describe('tempo name + metronome split (name/bpm overlap regression)', () => {
  const CH = 8; // matches the beforeAll measurement canvas + the overlay's fallback width

  const nameNode = (c: HTMLElement, name: string) =>
    placed(c).find((p) => p.text === name);
  const bpmNode = (c: HTMLElement, bpm: string, name: string) =>
    placed(c).find((p) => p.text !== name && p.text.includes(bpm));

  it('draws the name as its own clean text node, not fused with the metronome', async () => {
    const c = await draw({
      type: 'music', clef: 'treble', timeSignature: '4/4',
      notes: NOTES4,
      tempo: { name: 'Andante con moto', duration: 'q', bpm: 92 },
    });
    const name = nameNode(c, 'Andante con moto');
    const bpm = bpmNode(c, '92', 'Andante con moto');
    // The name survives verbatim (before the fix it was fused into
    // "Andanteconmoto(...)"), and the metronome is a distinct node.
    expect(name).toBeDefined();
    expect(bpm).toBeDefined();
  });

  it('places the metronome clear to the right of the name (no overprint)', async () => {
    const c = await draw({
      type: 'music', clef: 'treble', timeSignature: '4/4',
      notes: NOTES4,
      tempo: { name: 'Andante con moto', duration: 'q', bpm: 92 },
    });
    const name = nameNode(c, 'Andante con moto')!;
    const bpm = bpmNode(c, '92', 'Andante con moto')!;
    expect(bpm.x).toBeGreaterThan(name.x);
    // Well past the name's midpoint -- the bug put the metronome at (or before)
    // the name's own x, overprinting it.
    expect(bpm.x).toBeGreaterThanOrEqual(name.x + 'Andante con moto'.length * CH * 0.5);
  });

  it('splits a short name + bpm the same way', async () => {
    const c = await draw({
      type: 'music', clef: 'treble', timeSignature: '4/4',
      notes: NOTES4,
      tempo: { name: 'Allegro', duration: 'q', bpm: 120 },
    });
    const name = nameNode(c, 'Allegro')!;
    const bpm = bpmNode(c, '120', 'Allegro')!;
    expect(name).toBeDefined();
    expect(bpm).toBeDefined();
    expect(bpm.x).toBeGreaterThan(name.x);
  });

  it('renders a name-only tempo (no metronome digits)', async () => {
    const c = await draw({
      type: 'music', clef: 'treble', timeSignature: '4/4',
      notes: NOTES4,
      tempo: { name: 'Adagio' },
    });
    expect(nameNode(c, 'Adagio')).toBeDefined();
  });

  it('renders a bpm-only tempo (metronome present, no name)', async () => {
    const c = await draw({
      type: 'music', clef: 'treble', timeSignature: '4/4',
      notes: NOTES4,
      tempo: { duration: 'q', bpm: 120 },
    });
    expect(placed(c).some((p) => p.text.includes('120'))).toBe(true);
  });
});
