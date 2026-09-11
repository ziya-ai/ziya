/**
 * G-91491d — music layout-recovery helpers (D-162 / D-163 / D-164).
 *
 * These three structural defects share one root: renderMusicSpec sized the
 * canvas and planned system breaks from geometry that lost content.
 *
 *   D-162  a FLAT `notes[]` staff is one indivisible measure that planSystemBreaks
 *          can never wrap, and an explicit `width` opted out of wrapping entirely,
 *          so 100-120 notes became a single sub-pixel system.
 *   D-163  a wrapped score budgeted the per-system tail ONCE, not per system, so
 *          the canvas was short by systemTail*(systems-1) and the trailing
 *          system(s) fell off the bottom ("draw loop stopped early").
 *   D-164  an undersized author `width`/`height` was clamped blind, squeezing or
 *          collapsing the music and DROPPING content instead of growing the box.
 *
 * The fix routes each through a pure helper.  This suite pins the fix DIRECTION
 * against the pre-fix behaviour so a regression is caught without a render:
 *   - synthesizeMeasures / synthesizeFlatNotesMeasures split a long flat run into
 *     >1 measure while preserving every note, and leave a legible short run and
 *     any measures/voices staff BYTE-IDENTICAL (returned by reference);
 *   - stackedCanvasHeight budgets systemTail once PER SYSTEM (the pre-fix bug was
 *     "+ systemTail" once) — height grows by exactly systemTail per added system;
 *   - resolveAuthorCanvasDimension never shrinks below the content floor.
 *
 * Structural (geometry only), so theme-independent; the shared render stage
 * confirms both light and dark.
 */

import {
  synthesizeMeasures,
  synthesizeFlatNotesMeasures,
  resolveAuthorCanvasDimension,
  stackedCanvasHeight,
  noteDurationInWholes,
  type MusicNoteSpec,
  type MusicStaff,
} from '../musicPlugin';

const eighths = (n: number): MusicNoteSpec[] =>
  Array.from({ length: n }, () => ({ keys: ['c/5'], duration: '8' }));

describe('D-162 flat notes[] gains meter barlines so the wrapper can break it', () => {
  it('splits a long flat run into more than one measure, preserving every note', () => {
    const notes = eighths(120); // 120 eighths in 4/4 = 8 per bar => 15 bars
    const measures = synthesizeMeasures(notes, 4, 4);
    expect(measures.length).toBeGreaterThan(1);
    const total = measures.reduce((s, m) => s + (m.notes?.length ?? 0), 0);
    expect(total).toBe(120); // no note dropped or duplicated
    // 4/4 budget = 1 whole note; each 4/4 bar holds exactly 8 eighths.
    expect(measures[0].notes?.length).toBe(8);
  });

  it('never splits a note across a barline (accumulates whole-note budget)', () => {
    // A whole note fills a 4/4 bar on its own; the next note must open a new bar.
    const notes: MusicNoteSpec[] = [
      { keys: ['c/5'], duration: 'w' },
      { keys: ['d/5'], duration: 'q' },
    ];
    const measures = synthesizeMeasures(notes, 4, 4);
    expect(measures.length).toBe(2);
    expect(measures[0].notes?.length).toBe(1);
  });

  it('reshapes an illegibly-wide flat staff but leaves a short one by reference', () => {
    const longStaff: MusicStaff = { clef: 'treble', notes: eighths(120) } as MusicStaff;
    const reshaped = synthesizeFlatNotesMeasures(longStaff, 4, 4);
    expect(Array.isArray(reshaped.measures)).toBe(true);
    expect((reshaped.measures?.length ?? 0)).toBeGreaterThan(1);
    expect(reshaped.notes).toBeUndefined(); // flat run consumed into measures

    const shortStaff: MusicStaff = { clef: 'treble', notes: eighths(4) } as MusicStaff;
    // A short, already-legible flat run keeps its exact previous layout: same
    // object, still flat, no synthesized barlines.
    expect(synthesizeFlatNotesMeasures(shortStaff, 4, 4)).toBe(shortStaff);
  });

  it('leaves a measures[] / voices staff byte-identical (returned by reference)', () => {
    const measuresStaff: MusicStaff = {
      clef: 'treble', measures: [{ notes: eighths(8) }],
    } as MusicStaff;
    expect(synthesizeFlatNotesMeasures(measuresStaff, 4, 4)).toBe(measuresStaff);
  });

  it('noteDurationInWholes: base and dotted lengths are correct', () => {
    expect(noteDurationInWholes('w')).toBeCloseTo(1);
    expect(noteDurationInWholes('q')).toBeCloseTo(0.25);
    expect(noteDurationInWholes('8')).toBeCloseTo(0.125);
    // one dot = 1.5x
    expect(noteDurationInWholes('q.')).toBeCloseTo(0.375);
  });
});

describe('D-163 canvas height budgets the system tail PER SYSTEM, not once', () => {
  const args = {
    numStaves: 2, staveAdvance: 120, systemTail: 50,
    systemSpacing: 36, roomAbove: 10, titleH: 0,
  };

  it('grows by exactly one systemTail (plus spacing + staves) per added system', () => {
    const h1 = stackedCanvasHeight(
      args.numStaves, 1, args.staveAdvance, args.systemTail,
      args.systemSpacing, args.roomAbove, args.titleH,
    );
    const h2 = stackedCanvasHeight(
      args.numStaves, 2, args.staveAdvance, args.systemTail,
      args.systemSpacing, args.roomAbove, args.titleH,
    );
    // Per-system advance = staveAdvance*staves + systemTail + one spacing gap.
    const expectedDelta = args.staveAdvance * args.numStaves + args.systemTail + args.systemSpacing;
    expect(h2 - h1).toBe(expectedDelta);
    // The pre-fix "+ systemTail once" bug would have grown by only
    // staveAdvance*staves + spacing here, i.e. systemTail short.
    expect(h2 - h1).toBeGreaterThan(args.staveAdvance * args.numStaves + args.systemSpacing);
  });

  it('a 28-system score reserves 28 tails, not one', () => {
    const h = stackedCanvasHeight(1, 28, 120, 50, 36, 10, 0);
    // 120*1*28 + 50*28 + 36*27 + 10 + 0
    expect(h).toBe(120 * 28 + 50 * 28 + 36 * 27 + 10);
  });
});

describe('D-164 an author dimension never shrinks the canvas below the music', () => {
  it('grows an undersized author width up to the content width', () => {
    expect(resolveAuthorCanvasDimension(120, 3200)).toBe(3200);
  });
  it('honours a roomy author width unchanged', () => {
    expect(resolveAuthorCanvasDimension(4000, 3200)).toBe(4000);
  });
  it('uses content size when the author gives no dimension', () => {
    expect(resolveAuthorCanvasDimension(undefined, 900)).toBe(900);
  });
});
