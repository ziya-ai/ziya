/**
 * Courtesy / cautionary accidentals (D13).
 *
 * `planCautionaryAccidentals` is exported pure and DOM-free specifically for
 * regression testing: it decides where the parenthesised reminder accidental
 * belongs when a pitch altered in one bar returns in the NEXT bar sounding
 * differently.  The documented house rule (Dorico/Finale):
 *   - the reminder is added only to a note that would otherwise print BARE
 *     (a note carrying its own accidental is already its own reminder);
 *   - only for a ONE-bar-back change of the SAME pitch (letter + octave);
 *   - only on the FIRST occurrence of that pitch in the current bar.
 *
 * These import the REAL planner and never re-implement its logic.
 */
import { planCautionaryAccidentals, type MusicNoteSpec } from '../musicPlugin';

const N = (keys: string[], duration = 'q'): MusicNoteSpec => ({ keys, duration });

describe('planCautionaryAccidentals', () => {
  it('marks a bare note whose pitch sounded altered in the previous bar', () => {
    // f#/4 last bar, plain f/4 this bar -> a courtesy natural on the f/4.
    const prev = [N(['f#/4'])];
    const cur = [N(['f/4'])];
    const marks = planCautionaryAccidentals(cur, prev);
    expect(marks).toHaveLength(1);
    expect(marks[0]).toEqual({ noteIndex: 0, keyIndex: 0, code: 'n' });
  });

  it('marks a bare note that is now altered where it sounded natural before', () => {
    // f natural last bar, f#/4 would print its own sharp -> NOT bare, no mark;
    // but a bare note that is now flat-by-nothing... use the symmetric case:
    // last bar f#/4, this bar plain f/4 already covered; here last bar plain
    // f/4 (natural), this bar plain f/4 (natural) -> unchanged -> no mark.
    const marks = planCautionaryAccidentals([N(['f/4'])], [N(['f/4'])]);
    expect(marks).toHaveLength(0);
  });

  it('does not mark a note that already prints its own accidental', () => {
    // f#/4 last bar, f#/4 this bar: the note carries its own sharp, so it is
    // already its own reminder -> no parenthesised courtesy.
    const marks = planCautionaryAccidentals([N(['f#/4'])], [N(['f#/4'])]);
    expect(marks).toHaveLength(0);
  });

  it('does not mark a pitch that was absent in the previous bar', () => {
    const marks = planCautionaryAccidentals([N(['f/4'])], [N(['c/5'])]);
    expect(marks).toHaveLength(0);
  });

  it('marks only the FIRST occurrence of a repeated pitch in the bar', () => {
    const prev = [N(['f#/4'])];
    const cur = [N(['f/4']), N(['f/4'])];
    const marks = planCautionaryAccidentals(cur, prev);
    expect(marks).toHaveLength(1);
    expect(marks[0].noteIndex).toBe(0);
  });

  it('distinguishes octaves: f#/4 last bar does not remind f/5 this bar', () => {
    const marks = planCautionaryAccidentals([N(['f/5'])], [N(['f#/4'])]);
    expect(marks).toHaveLength(0);
  });

  it('addresses the right key within a chord', () => {
    // chord (c/4 f/4): only the f, altered last bar, gets the reminder.
    const prev = [N(['f#/4'])];
    const cur = [N(['c/4', 'f/4'])];
    const marks = planCautionaryAccidentals(cur, prev);
    expect(marks).toHaveLength(1);
    expect(marks[0]).toEqual({ noteIndex: 0, keyIndex: 1, code: 'n' });
  });

  it('returns nothing when there is no previous bar to compare against', () => {
    expect(planCautionaryAccidentals([N(['f/4'])], undefined)).toHaveLength(0);
    expect(planCautionaryAccidentals([N(['f/4'])], [])).toHaveLength(0);
  });

  it('ignores rests on both sides', () => {
    const prev: MusicNoteSpec[] = [{ duration: 'q', rest: true }, N(['f#/4'])];
    const cur: MusicNoteSpec[] = [{ duration: 'q', rest: true }, N(['f/4'])];
    const marks = planCautionaryAccidentals(cur, prev);
    // the f/4 (index 1) still gets its reminder; the rest is skipped
    expect(marks).toHaveLength(1);
    expect(marks[0].noteIndex).toBe(1);
  });

  it('carries the alteration reminder in the flat direction too', () => {
    // bb/4 last bar, plain b/4 this bar -> courtesy natural.
    const marks = planCautionaryAccidentals([N(['b/4'])], [N(['bb/4'])]);
    expect(marks).toHaveLength(1);
    expect(marks[0].code).toBe('n');
  });
});
