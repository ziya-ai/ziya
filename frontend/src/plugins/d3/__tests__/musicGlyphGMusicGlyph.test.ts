/**
 * @jest-environment jsdom
 *
 * Regression tests for group G-MUSIC-GLYPH in musicPlugin.ts.
 *
 *   D-146  Per-measure SCORE-EDGE barlines were dropped.  The opening bar's
 *          `beginBar` (e.g. "repeat-begin") and the closing bar's `endBar`
 *          (e.g. "final") name the two outer barlines of the piece, but the
 *          render only read the spec-level `beginBar`/`endBar`; `barlineBetween`
 *          reaches only barlines that sit BETWEEN two bars, so an interior
 *          `endBar` ("repeat-end") rendered while the first bar's begin and the
 *          last bar's end were silently discarded.  resolveScoreEdgeBarlines()
 *          falls back to those per-measure spellings.  This test imports the
 *          helper (absent from unpatched source, so the import itself fails
 *          there) and asserts the fallback in both directions plus spec-level
 *          precedence.
 *
 *   D-144  A rest on a multi-voice staff must be displaced off the centre line
 *          per voice, or two simultaneous rests overprint into one glyph.
 *          multiVoiceRestPitch() supplies the raised (upper) / lowered (lower)
 *          rest pitch that buildNoteString applies on BOTH the primary
 *          (voices[0]) and the secondary (voices[1..]) paths.  This guards that
 *          the two positions are distinct, valid pitches for every clef, so the
 *          anti-overprint displacement cannot silently collapse.
 *
 * These are pure helpers -- importing the module does NOT load VexFlow (that
 * import is dynamic, inside renderMusicSpec), matching the other music*.test.ts
 * suites.
 */
import {
  resolveScoreEdgeBarlines,
  multiVoiceRestPitch,
  BARLINE_TYPES,
} from '../../../utils/d3Plugins/musicPlugin';

describe('D-146 resolveScoreEdgeBarlines: per-measure score-edge barlines', () => {
  test('falls back to the first bar beginBar and the last bar endBar', () => {
    // The w1-05 shape: measures[0].beginBar "repeat-begin", last measure
    // endBar "final", no spec-level begin/end.  Pre-fix these edges were
    // dropped (the loop read only spec.beginBar/spec.endBar, both undefined).
    const edges = resolveScoreEdgeBarlines(
      undefined,
      undefined,
      { beginBar: 'repeat-begin' },
      { endBar: 'final' },
    );
    expect(edges.beginBar).toBe('repeat-begin');
    expect(edges.endBar).toBe('final');
    // Both names must be barlines VexFlow can draw.
    expect(BARLINE_TYPES[edges.beginBar!]).toBe('REPEAT_BEGIN');
    expect(BARLINE_TYPES[edges.endBar!]).toBe('END');
  });

  test('the last bar endBar "final" is honoured (w1-14 shape)', () => {
    const edges = resolveScoreEdgeBarlines(
      undefined,
      undefined,
      { /* first bar has no beginBar */ },
      { endBar: 'final' },
    );
    expect(edges.beginBar).toBeUndefined();
    expect(edges.endBar).toBe('final');
  });

  test('spec-level beginBar/endBar take precedence over per-measure edges', () => {
    const edges = resolveScoreEdgeBarlines(
      'repeat-begin',
      'double',
      { beginBar: 'single' },
      { endBar: 'final' },
    );
    expect(edges.beginBar).toBe('repeat-begin');
    expect(edges.endBar).toBe('double');
  });

  test('no edges when neither spec nor first/last measure carry them', () => {
    // A plain score keeps VexFlow's default single edges: both undefined so the
    // render loop skips setBegBarType/setEndBarType, byte-identical to before.
    const edges = resolveScoreEdgeBarlines(undefined, undefined, {}, {});
    expect(edges.beginBar).toBeUndefined();
    expect(edges.endBar).toBeUndefined();
  });

  test('tolerates a missing first/last measure (empty staff)', () => {
    const edges = resolveScoreEdgeBarlines(undefined, undefined, undefined, undefined);
    expect(edges.beginBar).toBeUndefined();
    expect(edges.endBar).toBeUndefined();
  });
});

describe('D-144 multiVoiceRestPitch: per-voice rest displacement', () => {
  const clefs = ['treble', 'bass', 'alto', 'tenor', 'percussion'];

  test.each(clefs)(
    'upper and lower rest pitches differ and are valid for %s',
    (clef) => {
      const upper = multiVoiceRestPitch(clef, 'upper');
      const lower = multiVoiceRestPitch(clef, 'lower');
      expect(upper).toBeDefined();
      expect(lower).toBeDefined();
      // Distinct positions -> two simultaneous rests cannot overprint.
      expect(upper).not.toBe(lower);
      // Well-formed rest-pitch override: "<letter>[accidental]<octave>" (the
      // bare form buildNoteString interpolates as `${restPitch}/${base}/r`).
      expect(upper).toMatch(/^[a-gA-G][#b]?\d$/);
      expect(lower).toMatch(/^[a-gA-G][#b]?\d$/);
    },
  );

  test('an unknown clef yields undefined so the caller centres the rest', () => {
    expect(multiVoiceRestPitch('nonsense', 'upper')).toBeUndefined();
    expect(multiVoiceRestPitch('nonsense', 'lower')).toBeUndefined();
  });
});
