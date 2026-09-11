/**
 * @jest-environment jsdom
 *
 * D-036 / G-33: a FLAT `notes[]` melody never wrapped.
 *
 * `measuresOf` collapses a flat-notes staff into a single measure
 * (`[{ notes }]`), and `planSystemBreaks` never splits a lone measure ("a
 * system exceeds the budget only when it holds a single measure that cannot
 * fit anywhere"), so a long flat melody rendered as ONE system that ran off
 * the canvas or was crushed sub-pixel (music-w2-01 = 120 flat notes, etc.).
 *
 * The fix segments a flat melody into meter-sized measures BEFORE the existing
 * measure-based wrap engine runs, preserving note order and count exactly.
 * This suite pins:
 *   - the pure duration/capacity/segmentation helpers (RED pre-fix: the
 *     symbols do not exist, so the import yields `undefined`);
 *   - a long flat melody now draws MORE THAN ONE system, while a short flat
 *     melody (<= one measure) stays on ONE system (byte-identical layout);
 *   - segmentation preserves every note (no drop/duplicate).
 *
 * A "system" is counted by its clef glyph (every system re-prints the clef).
 * Structural defect (geometry only), so it is theme-independent; the shared
 * render stage confirms both themes.
 */

// Polyfill structuredClone for jest's jsdom environment (vexflow 5 uses it in
// metrics.getFontInfo; jsdom on Node 20 does not expose it).
if (typeof (globalThis as any).structuredClone !== 'function') {
  (globalThis as any).structuredClone = (v: any) =>
    (v === undefined ? undefined : JSON.parse(JSON.stringify(v)));
}

import {
  renderMusicSpec,
  segmentFlatStaffIntoMeasures,
  measureCapacityWholeNotes,
  noteWholeNoteUnits,
  type MusicNoteSpec,
  type MusicSpec,
} from '../musicPlugin';

// ── jsdom / d3 / VexFlow harness (mirrors musicSystemWrap.test.ts) ──────
const SVG_NS = 'http://www.w3.org/2000/svg';
const makeSel = (node: any): any => {
  const sel: any = {};
  sel.append = (tag: string) => {
    const child = document.createElementNS(SVG_NS, tag);
    if (node && typeof node.appendChild === 'function') node.appendChild(child);
    return makeSel(child);
  };
  sel.attr = (k: string, v: any) => {
    if (node && typeof node.setAttribute === 'function') node.setAttribute(k, String(v));
    return sel;
  };
  sel.text = (t: any) => { if (node) node.textContent = String(t); return sel; };
  sel.style = () => sel;
  sel.classed = () => sel;
  sel.html = () => sel;
  return sel;
};
const d3Stub = { select: (el: any) => makeSel(el) };

let warnSpy: jest.SpyInstance;
beforeEach(() => { warnSpy = jest.spyOn(console, 'warn').mockImplementation(() => {}); });
afterEach(() => { warnSpy.mockRestore(); });

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

const draw = async (spec: MusicSpec) => {
  const container = document.createElement('div');
  document.body.appendChild(container);
  await renderMusicSpec(container, spec, false, d3Stub);
  return container;
};
const glyphs = (c: HTMLElement) =>
  Array.from(c.querySelectorAll('text')).map((t) => t.textContent ?? '').join('');
/** Treble clef glyph; one per system, so this counts systems. */
const systemsDrawn = (c: HTMLElement) => (glyphs(c).match(/\ue050/g) ?? []).length;
/** Noteheads (half/quarter/eighth) — proves wrapping never drops music. */
const noteheads = (c: HTMLElement) =>
  (glyphs(c).match(/[\ue0a2\ue0a3\ue0a4]/g) ?? []).length;

const flat = (n: number, duration = '8'): MusicNoteSpec[] =>
  Array.from({ length: n }, () => ({ keys: ['c/5'], duration }));

// ── pure helpers ────────────────────────────────────────────────────
describe('noteWholeNoteUnits', () => {
  it('maps the base durations to whole-note fractions', () => {
    expect(noteWholeNoteUnits('w')).toBeCloseTo(1);
    expect(noteWholeNoteUnits('h')).toBeCloseTo(0.5);
    expect(noteWholeNoteUnits('q')).toBeCloseTo(0.25);
    expect(noteWholeNoteUnits('8')).toBeCloseTo(0.125);
    expect(noteWholeNoteUnits('16')).toBeCloseTo(0.0625);
  });
  it('applies augmentation dots (base * (2 - 2^-dots))', () => {
    expect(noteWholeNoteUnits('h.')).toBeCloseTo(0.75);
    expect(noteWholeNoteUnits('q..')).toBeCloseTo(0.25 * 1.75);
  });
  it('falls back to a quarter for an unknown/absent duration', () => {
    expect(noteWholeNoteUnits(undefined)).toBeCloseTo(0.25);
    expect(noteWholeNoteUnits('zzz')).toBeCloseTo(0.25);
  });
});

describe('measureCapacityWholeNotes', () => {
  it('computes n/d capacity', () => {
    expect(measureCapacityWholeNotes('4/4')).toBeCloseTo(1);
    expect(measureCapacityWholeNotes('3/4')).toBeCloseTo(0.75);
    expect(measureCapacityWholeNotes('6/8')).toBeCloseTo(0.75);
    expect(measureCapacityWholeNotes('2/2')).toBeCloseTo(1);
  });
  it('accepts the glyph meters C / C|', () => {
    expect(measureCapacityWholeNotes('C')).toBeCloseTo(1);
    expect(measureCapacityWholeNotes('C|')).toBeCloseTo(1);
  });
  it('returns null for an absent or unparseable meter', () => {
    expect(measureCapacityWholeNotes(undefined)).toBeNull();
    expect(measureCapacityWholeNotes('4/0')).toBeNull();
    expect(measureCapacityWholeNotes('x')).toBeNull();
  });
});

describe('segmentFlatStaffIntoMeasures', () => {
  it('splits a long flat melody into meter-sized measures', () => {
    const out = segmentFlatStaffIntoMeasures({ notes: flat(120, '8') }, '4/4');
    // 8 eighths per 4/4 bar -> 15 bars
    expect(out.measures).toHaveLength(15);
    expect((out.measures as any[]).every((m) => m.notes.length === 8)).toBe(true);
    expect((out as any).notes).toBeUndefined();
  });

  it('respects a compound meter capacity', () => {
    const out = segmentFlatStaffIntoMeasures({ notes: flat(12, '8') }, '6/8');
    expect((out.measures as any[]).map((m) => m.notes.length)).toEqual([6, 6]);
  });

  it('preserves note order and count exactly', () => {
    const notes = Array.from({ length: 10 }, (_, i) => (
      { keys: ['c/5'], duration: i % 2 ? 'q' : '8', id: i } as any
    ));
    const out = segmentFlatStaffIntoMeasures({ notes }, '4/4');
    const rebuilt = (out.measures as any[]).flatMap((m) => m.notes);
    expect(rebuilt).toHaveLength(notes.length);
    expect(rebuilt.map((n) => n.id)).toEqual(notes.map((n) => n.id));
  });

  it('is a no-op (returned by reference) for a melody that fits one measure', () => {
    const staff = { notes: flat(4, 'q') }; // 4 quarters = one 4/4 bar
    expect(segmentFlatStaffIntoMeasures(staff, '4/4')).toBe(staff);
  });

  it('is a no-op with no meter, or an explicit measures/voices staff', () => {
    const noMeter = { notes: flat(40, '8') };
    expect(segmentFlatStaffIntoMeasures(noMeter, undefined)).toBe(noMeter);
    const measured = { measures: [{ notes: flat(4, 'q') }] };
    expect(segmentFlatStaffIntoMeasures(measured, '4/4')).toBe(measured);
    const voiced = { voices: [{ notes: flat(40, '8') }] };
    expect(segmentFlatStaffIntoMeasures(voiced as any, '4/4')).toBe(voiced);
  });

  it('gives an over-long note its own measure (no infinite loop)', () => {
    const out = segmentFlatStaffIntoMeasures({ notes: flat(2, 'w') }, '2/4');
    expect((out.measures as any[]).map((m) => m.notes.length)).toEqual([1, 1]);
  });
});

// ── end-to-end render: wrapping now happens for a flat melody ──────────
describe('flat-notes melody wrapping (render)', () => {
  it('wraps a long flat melody across multiple systems', async () => {
    const c = await draw({ type: 'music', timeSignature: '4/4', notes: flat(96, '8') });
    // RED pre-fix: the flat melody was one un-splittable measure -> 1 system.
    expect(systemsDrawn(c)).toBeGreaterThan(1);
    // no music lost by the reflow
    expect(noteheads(c)).toBe(96);
  });

  it('keeps a short flat melody on a single system (byte-identical layout)', async () => {
    const c = await draw({ type: 'music', timeSignature: '4/4', notes: flat(4, 'q') });
    expect(systemsDrawn(c)).toBe(1);
    expect(noteheads(c)).toBe(4);
  });
});
