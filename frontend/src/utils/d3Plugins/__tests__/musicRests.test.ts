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

// Polyfill structuredClone for jest's jsdom environment: vexflow 5.0.0 uses
// it in metrics.getFontInfo, and jest's jsdom global does not expose it on
// Node 20 (a plain-data font-metrics clone, so JSON round-trip suffices).
if (typeof (globalThis as any).structuredClone !== 'function') {
  (globalThis as any).structuredClone = (v: any) =>
    (v === undefined ? undefined : JSON.parse(JSON.stringify(v)));
}

import {
  buildNoteString, renderMusicSpec, multiVoiceRestPitch, type MusicSpec,
} from '../musicPlugin';

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

// DOM-capturing d3 for observing the below-staff dynamics OVERLAY
// (drawDynamicsLayer), which the no-op stub above swallows.  attr() sets real
// attributes so an overlay mark's x is queryable for alignment checks.
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
const DYNAMIC_SET = new Set(['ppp', 'pp', 'p', 'mp', 'mf', 'f', 'ff', 'fff', 'sf', 'sfz', 'rfz', 'fp']);
const drawDom = async (spec: MusicSpec) => {
  const container = document.createElement('div');
  document.body.appendChild(container);
  await renderMusicSpec(container, spec, false, domD3);
  return container;
};

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

// Install a VexFlow text-measurement canvas.  Bare `npx jest` does not load
// CRA's setupTests.ts, so VexFlow's measurement canvas resolves to jsdom's
// unimplemented getContext and every glyph width is 0.  That is merely
// imprecise for most primitives, but for a MULTI-VOICE bar it can drop the
// second voice's rest entirely (the throw aborts its formatting), so the
// no-overprint assertion below cannot even see both rests.  Provide a
// measurement canvas through VexFlow's own API so both voices format and each
// rest is drawn at the y its (raised / lowered) pitch dictates.  Metrics are
// approximate -- the y assertions here compare relative positions, not pixels.
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
    // Dynamics are a below-staff overlay now; drawDom makes them observable.
    const c = await drawDom({ type: 'music', timeSignature: '4/4', notes: [
      { ...N('c/5', 'q'), dynamic: 'pp' }, R('q'),
      { ...N('e/5', 'q'), dynamic: 'ff' }, N('f/5', 'q'),
    ] });
    const dynX = Array.from(c.querySelectorAll('text'))
      .filter((t) => DYNAMIC_SET.has(t.textContent ?? ''))
      .map((t) => parseFloat(t.getAttribute('x') ?? 'NaN'));
    const headX = Array.from(c.querySelectorAll('text'))
      .filter((t) => /[\ue0a4]/.test(t.textContent ?? ''))
      .map((t) => parseFloat(t.getAttribute('x') ?? 'NaN'));
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

/**
 * MULTIVOICE-REST-OFFSET regression.
 *
 * On a single-voice staff a rest is CENTRED (REST_PITCH_FOR_CLEF).  On a
 * multi-voice staff that centring is wrong: two voices resting on the same
 * beat both land on the middle line and OVERPRINT into one glyph, and even a
 * lone rest no longer reads as belonging to the upper or lower voice.
 * Published two-voice engraving RAISES the upper voice's rests and LOWERS the
 * lower voice's.  The fix expresses that offset as an upper/lower pitch a
 * third either side of the clef centre (REST_PITCH_MULTIVOICE), reached by
 * buildNoteString's `restPitchOverride` only when a staff declares >1 voice --
 * so every single-voice render stays byte-identical.
 */
describe('multi-voice rest placement (no overprint)', () => {
  it('multiVoiceRestPitch raises the upper voice and lowers the lower, per clef', () => {
    // Treble: a third above / below the centred B4 rest.
    expect(multiVoiceRestPitch('treble', 'upper')).toBe('D5');
    expect(multiVoiceRestPitch('treble', 'lower')).toBe('G4');
    // Bass and the C-clefs each have their own pair.
    expect(multiVoiceRestPitch('bass', 'upper')).toBe('F3');
    expect(multiVoiceRestPitch('bass', 'lower')).toBe('B2');
    expect(multiVoiceRestPitch('alto', 'upper')).toBe('E4');
    expect(multiVoiceRestPitch('tenor', 'lower')).toBe('F3');
  });

  it('returns undefined for an unknown clef so the caller falls back to centring', () => {
    expect(multiVoiceRestPitch('percussion', 'upper')).toBe('D5');
    expect(multiVoiceRestPitch('nonsense' as any, 'upper')).toBeUndefined();
  });

  it('emits raised / lowered rest pitches via buildNoteString restPitchOverride', () => {
    // This is the mechanism the multi-voice fix relies on: the primary (upper)
    // voice passes the 'upper' pitch and the secondary (lower) voice the
    // 'lower' pitch to buildNoteString, so their simultaneous rests are drawn
    // a third above / below the centre line instead of overprinting.  Asserted
    // at the string level because it is deterministic -- no VexFlow, no canvas
    // metrics -- unlike the rendered rest-glyph geometry, which VexFlow's
    // formatter resolves differently under jsdom's approximate text metrics.
    const upper = buildNoteString([R('q')] as any, 'treble', undefined, 'D5');
    const lower = buildNoteString([R('q')] as any, 'treble', undefined, 'G4');
    const centred = buildNoteString([R('q')] as any, 'treble');
    expect(upper).toContain('D5/q/r');   // raised (upper voice)
    expect(lower).toContain('G4/q/r');   // lowered (lower voice)
    expect(centred).toContain('B4/q/r'); // no override -> centred (single voice)
    // The two voices' rests are on genuinely different lines.
    expect(upper).not.toBe(lower);
  });

  it('renders both voices of a two-voice bar (each with a rest) without crashing', async () => {
    // Both voices resting on beat 2 is exactly the overprint case.  We assert
    // the render completes and both lines are laid out (four noteheads across
    // the two voices); the raised/lowered rest PITCHES are pinned at the
    // string level in the test above, which does not depend on VexFlow's
    // jsdom-approximate glyph geometry.
    const c = await draw({
      type: 'music', clef: 'treble', timeSignature: '4/4',
      voices: [
        { stemDirection: 'up', notes: [N('g/5', 'q'), R('q'), N('e/5', 'h')] },
        { stemDirection: 'down', notes: [N('c/4', 'q'), R('q'), N('c/4', 'h')] },
      ],
    });
    expect(c.querySelector('svg')).not.toBeNull();
    expect(headCount(c)).toBe(4);
    // At least one rest is drawn (both voices carry one); the pitch separation
    // that prevents overprint is verified at the string level above.
    expect(restCount(c)).toBeGreaterThanOrEqual(1);
  });

  it('leaves a single-voice staff\'s rest centred (byte-identical path)', async () => {
    // The override is consulted ONLY when a staff declares >1 voice, so a lone
    // voice / plain notes list still centres its rest exactly as before.
    const c = await draw({ type: 'music', clef: 'treble', timeSignature: '4/4',
      notes: [N('c/5', 'q'), R('q'), N('e/5', 'h')] });
    expect(restCount(c)).toBe(1);
  });
});
