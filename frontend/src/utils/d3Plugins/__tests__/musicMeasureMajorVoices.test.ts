/**
 * Measure-major multi-voice normalisation.
 *
 * The skill prompt documents two equivalent spellings of polyphony:
 *   - voice-major   `voices: [{stemDirection, measures:[...]}]`  (what the
 *                   render core reads)
 *   - measure-major `measures: [{voices:[...], endBar, ...}]`     (one list per
 *                   BAR, each bar carrying its own voices)
 * Only voice-major was implemented, so a measure-major spec tested EMPTY at
 * every recognition gate -> "No compatible plugin" -> ~30s hang / data loss.
 * `normalizeMeasureMajorVoices` transposes measure-major back to voice-major.
 * Exported pure/DOM-free for exactly this regression test.
 */
import { normalizeMeasureMajorVoices } from '../musicPlugin';

const q = (k: string) => ({ keys: [k], duration: 'q' });

describe('normalizeMeasureMajorVoices', () => {
  it('returns a voice-major staff untouched (voice-major wins)', () => {
    const staff = { voices: [{ notes: [q('c/5')] }] };
    expect(normalizeMeasureMajorVoices(staff)).toBe(staff);
  });

  it('returns a plain measures staff by reference (no voices anywhere)', () => {
    const staff = { measures: [{ notes: [q('c/5')] }] };
    expect(normalizeMeasureMajorVoices(staff)).toBe(staff);
  });

  it('returns an empty/absent-measures staff untouched', () => {
    const staff = { clef: 'treble' as const };
    expect(normalizeMeasureMajorVoices(staff)).toBe(staff);
  });

  it('transposes a two-voice, two-bar measure-major staff to voice-major', () => {
    const staff = {
      measures: [
        { voices: [{ notes: [q('c/5')] }, { notes: [q('e/4')] }] },
        { voices: [{ notes: [q('d/5')] }, { notes: [q('f/4')] }] },
      ],
    };
    const out: any = normalizeMeasureMajorVoices(staff);
    expect(out.measures).toBeUndefined();            // measures dropped
    expect(out.voices).toHaveLength(2);              // one list per line
    expect(out.voices[0].measures).toHaveLength(2);  // one bar each
    expect(out.voices[0].measures[0].notes).toEqual([q('c/5')]);
    expect(out.voices[0].measures[1].notes).toEqual([q('d/5')]);
    expect(out.voices[1].measures[0].notes).toEqual([q('e/4')]);
    expect(out.voices[1].measures[1].notes).toEqual([q('f/4')]);
  });

  it('carries the first-declared stemDirection onto each voice line', () => {
    const staff = {
      measures: [
        {
          voices: [
            { stemDirection: 'up', notes: [q('c/5')] },
            { stemDirection: 'down', notes: [q('e/4')] },
          ],
        },
      ],
    };
    const out: any = normalizeMeasureMajorVoices(staff);
    expect(out.voices[0].stemDirection).toBe('up');
    expect(out.voices[1].stemDirection).toBe('down');
  });

  it('keeps bar-level fields (endBar, timeSignature) on the transposed measure', () => {
    const staff = {
      measures: [
        { voices: [{ notes: [q('c/5')] }], endBar: 'repeat-end', timeSignature: '3/4' },
      ],
    };
    const out: any = normalizeMeasureMajorVoices(staff);
    expect(out.voices[0].measures[0].endBar).toBe('repeat-end');
    expect(out.voices[0].measures[0].timeSignature).toBe('3/4');
  });

  it('folds a plain-notes bar among voiced bars into voice 0', () => {
    const staff = {
      measures: [
        { voices: [{ notes: [q('c/5')] }, { notes: [q('e/4')] }] },
        { notes: [q('d/5')] }, // no voices: contributes to voice 0 only
      ],
    };
    const out: any = normalizeMeasureMajorVoices(staff);
    expect(out.voices).toHaveLength(2);
    expect(out.voices[0].measures[1].notes).toEqual([q('d/5')]);
    expect(out.voices[1].measures[1].notes).toEqual([]); // voice 1 rests
  });
});
