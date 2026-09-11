/**
 * Regression test for D-037 (fix group G-34): the music strict-parse
 * alias / content-gate gaps.
 *
 * `resolveMusicSpec` lifts a `{type:'music', definition:{...}}` wrapper into a
 * music spec and stamps `type`, but historically did NOT canonicalise the
 * near-miss field spellings an LLM commonly emits.  Five sweep specs passed
 * the isMusicSpec content gate (their `notes`/`measures` arrays are non-empty)
 * yet rendered wrong or crashed because the note payloads used non-canonical
 * shapes:
 *   - music-w4-10: root `time`/`key`/`staff` (not timeSignature/keySignature/
 *     clef) and per-note `pitch`/`dur` (not keys/duration) -> every note lost
 *     its pitch and drew as a rest (a musically-empty score shipped as success).
 *   - music-w4-12: `keys:"c/4"` as a bare string (not an array) -> `keys.map`
 *     threw; and English long-form durations ("quarter"/"half"/"whole") ->
 *     sanitizeDuration fell them all back to a quarter (wrong rhythm).
 *   - music-w4-09: `notes:[[..],[..]]` double-nested (two measures inlined into
 *     notes) -> each inner ARRAY was read as a note with no keys -> rests.
 *   - music-w4-08: numeric fields as quoted strings incl. a separate `dots`
 *     field that buildNoteString never reads -> the dotted rhythm was dropped.
 *
 * The fix adds a pure `normalizeMusicShape` stage inside resolveMusicSpec that
 * maps the root/per-note aliases, coerces a scalar `keys`, maps long-form
 * duration names to codes, folds a separate `dots` field onto the duration,
 * and lifts a double-nested `notes` list to `measures` -- while returning a
 * correctly-authored spec byte-identically.
 *
 * These assertions describe the POST-FIX behaviour, so they FAIL against the
 * unpatched source (which returns the definition un-normalised: `time` stays,
 * `keys` stays a string, `notes` stays nested).  D-037 is a recovery defect
 * (theme-independent: resolveMusicSpec is a pure DOM-free transform), so the
 * both-theme obligation is discharged at the shared render stage.
 */
import { resolveMusicSpec } from '../../../utils/d3Plugins/musicPlugin';

// The failing sweep specs ship an OBJECT definition (the JSON body parsed
// upstream from a nested ```d3 fence); a couple of cases also exercise the
// STRING-definition wrapper the render_diagram tool builds.
const wrapObj = (definition: object) => ({ type: 'music', definition });
const wrapStr = (body: object) => ({ type: 'music', definition: JSON.stringify(body) });

describe('D-037 / G-34: music field-alias + content-gate normalisation', () => {
  test('w4-10: root time/key/staff and per-note pitch/dur aliases are canonicalised', () => {
    const r = resolveMusicSpec(wrapObj({
      time: '3/4', key: 'Bb', staff: 'treble',
      notes: [
        { pitch: ['bb/4'], dur: 'q' },
        { pitch: ['d/5'], dur: 'q' },
        { pitch: ['bb/5'], dur: 'h.' },
      ],
    }));
    expect(r.timeSignature).toBe('3/4');
    expect(r.keySignature).toBe('Bb');
    expect(r.clef).toBe('treble');
    // Alias keys are consumed, not left dangling.
    expect(r.time).toBeUndefined();
    expect(r.key).toBeUndefined();
    expect(r.staff).toBeUndefined();
    expect(r.notes[0]).toMatchObject({ keys: ['bb/4'], duration: 'q' });
    expect(r.notes[0].pitch).toBeUndefined();
    expect(r.notes[0].dur).toBeUndefined();
    expect(r.notes[2].duration).toBe('h.'); // dotted code preserved
  });

  test('w4-12: scalar keys string coerced to an array; long-form duration names mapped', () => {
    const r = resolveMusicSpec(wrapObj({
      timeSignature: '4/4', clef: 'treble', keySignature: 'C',
      notes: [
        { keys: 'c/4', duration: 'quarter' },
        { keys: 'e/4', duration: 'quarter' },
        { keys: 'g/4', duration: 'half' },
        { keys: 'c/5', duration: 'whole' },
      ],
    }));
    expect(Array.isArray(r.notes[0].keys)).toBe(true);
    expect(r.notes[0].keys).toEqual(['c/4']);
    expect(r.notes.map((n: any) => n.duration)).toEqual(['q', 'q', 'h', 'w']);
  });

  test('w4-09: double-nested notes list is lifted to measures', () => {
    const r = resolveMusicSpec(wrapObj({
      timeSignature: '4/4', clef: 'treble',
      notes: [
        [{ keys: ['c/4'], duration: 'q' }, { keys: ['d/4'], duration: 'q' }],
        [{ keys: ['e/4'], duration: 'q' }, { keys: ['f/4'], duration: 'q' }],
      ],
    }));
    expect(r.notes).toBeUndefined();
    expect(Array.isArray(r.measures)).toBe(true);
    expect(r.measures).toHaveLength(2);
    expect(r.measures[0].notes).toEqual([
      { keys: ['c/4'], duration: 'q' },
      { keys: ['d/4'], duration: 'q' },
    ]);
    expect(r.measures[1].notes[0].keys).toEqual(['e/4']);
  });

  test('w4-08: a separate quoted `dots` field is folded onto the duration code', () => {
    const r = resolveMusicSpec(wrapObj({
      timeSignature: '4/4', clef: 'treble', keySignature: 'C',
      tempo: '120', width: '900', height: '260',
      notes: [
        { keys: ['c/4'], duration: 'q', dots: '1' }, // dotted quarter
        { keys: ['e/4'], duration: '8' },
        { keys: ['g/4'], duration: 'q', dots: '0' }, // zero dots -> plain
      ],
    }));
    expect(r.notes[0].duration).toBe('q.');
    expect(r.notes[1].duration).toBe('8');
    expect(r.notes[2].duration).toBe('q');
    // Numeric-string layout fields pass through resolveMusicSpec untouched
    // (the downstream sanitizers coerce them); they must not be dropped.
    expect(r.tempo).toBe('120');
    expect(r.width).toBe('900');
  });

  test('string-definition wrapper (the render_diagram tool shape) is normalised too', () => {
    const r = resolveMusicSpec(wrapStr({
      time: '4/4', staff: 'bass',
      notes: [{ pitch: ['c/3'], dur: 'half' }],
    }));
    expect(r.timeSignature).toBe('4/4');
    expect(r.clef).toBe('bass');
    expect(r.notes[0].keys).toEqual(['c/3']);
    expect(r.notes[0].duration).toBe('h');
  });

  test('a correctly-authored spec is returned byte-identically (no needless rewrite)', () => {
    const canonical = {
      timeSignature: '4/4', clef: 'treble', keySignature: 'C',
      notes: [
        { keys: ['c/4'], duration: 'q' },
        { keys: ['e/4'], duration: '8' },
      ],
    };
    const r = resolveMusicSpec(wrapObj(canonical));
    // Same field contents; keys/duration untouched.
    expect(r.notes).toEqual(canonical.notes);
    expect(r.timeSignature).toBe('4/4');
  });

  test('a non-music spec is never claimed or mutated', () => {
    const other = { type: 'music', definition: { nodes: [{ id: 'a' }], links: [] } };
    // No music content -> resolveMusicSpec returns the wrapper untouched.
    const r = resolveMusicSpec(other);
    expect(r).toBe(other);
  });
});
