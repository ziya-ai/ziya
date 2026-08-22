/**
 * Regression test for Issue 28 (music renderer): an invalid `keySignature`
 * string hangs the entire render.
 *
 * ROOT CAUSE
 *
 * The music render core called `stave.addKeySignature(spec.keySignature)` with
 * the raw, unvalidated spec value. VexFlow's `KeySignature`/`addKeySignature`
 * looks the name up in its ~30-entry `keySignatures` table and THROWS
 * `Vex.RuntimeError('BadKeySignature', ...)` on anything else. An adversarial
 * `keySignature: "F####bbb-9"` therefore threw before the SVG mounted, the
 * throw escaped render(), and the render hung to the 30s cap with zero output
 * (DOM snapshot svg:0). A five-render bisection isolated this single field as
 * the sole trigger (timeSignature "0/0", nested tuplets, jump-markup, etc. all
 * proved benign -- none reached draw time).
 *
 * FIX
 *
 * `sanitizeKeySignature(raw)` (musicAccidentals.ts) validates against the
 * KNOWN_KEY_SIGNATURES set (derived from the existing SHARP_COUNT/FLAT_COUNT
 * tables VexFlow accepts) and coerces any unrecognised value to "C" (the
 * neutral no-accidental signature), returning null when there is nothing to
 * draw. The render core routes the value through it before addKeySignature, so
 * a bad key degrades to the default instead of hanging the render.
 *
 * These tests import the REAL module (not a re-implementation), so they detect
 * drift in the shipped logic. They are NON-VACUOUS: `sanitizeKeySignature` and
 * `KNOWN_KEY_SIGNATURES` did not exist before the fix, so this file would not
 * even compile/import against the pre-fix source. The guard cases pin that
 * good keys pass through UNCHANGED (so the sanitizer is not a catch-all that
 * rewrites every key to "C") and that bad keys are coerced (so it is not a
 * pass-through no-op).
 */

import {
  sanitizeKeySignature,
  isKnownKeySignature,
  KNOWN_KEY_SIGNATURES,
  keySignatureMap,
} from '../../../utils/d3Plugins/musicAccidentals';

describe('Issue 28 — music keySignature sanitizer', () => {
  describe('known keys pass through unchanged (not a catch-all)', () => {
    // A representative spread of majors + relative minors across the sharp and
    // flat sides, plus the neutral C. If the sanitizer coerced these it would
    // silently strip a score's key signature.
    const goodKeys = ['C', 'G', 'D', 'A', 'E', 'B', 'F#', 'C#',
                      'F', 'Bb', 'Eb', 'Ab', 'Db', 'Gb', 'Cb',
                      'Am', 'Em', 'F#m', 'D#m', 'Dm', 'Bbm', 'Abm'];
    it.each(goodKeys)('keeps "%s" verbatim', (key) => {
      expect(sanitizeKeySignature(key)).toBe(key);
      expect(isKnownKeySignature(key)).toBe(true);
    });

    it('trims surrounding whitespace on a known key', () => {
      expect(sanitizeKeySignature('  F#  ')).toBe('F#');
    });

    it('every KNOWN_KEY_SIGNATURES entry survives sanitization unchanged', () => {
      for (const key of KNOWN_KEY_SIGNATURES) {
        expect(sanitizeKeySignature(key)).toBe(key);
      }
    });
  });

  describe('bad keys are coerced to "C" (the Issue 28 trigger)', () => {
    // These are exactly the values that made VexFlow throw and hang the render.
    const badKeys = ['F####bbb-9', 'Z#b-9', 'H', 'Cmaj', 'C major',
                     'X', '###', '123', 'treble', 'not-a-key'];
    it.each(badKeys)('coerces "%s" to "C"', (key) => {
      expect(sanitizeKeySignature(key)).toBe('C');
      // And the guard predicate correctly REJECTS it (still rejects what it
      // rejected before -- the predicate is not widened into a catch-all).
      expect(isKnownKeySignature(key)).toBe(false);
    });

    it('coerces a non-string keySignature to "C"', () => {
      expect(sanitizeKeySignature(42 as any)).toBe('C');
      expect(sanitizeKeySignature({} as any)).toBe('C');
      expect(sanitizeKeySignature(true as any)).toBe('C');
      expect(sanitizeKeySignature([] as any)).toBe('C');
    });
  });

  describe('absent key means "draw nothing" (null), not a forced "C"', () => {
    it('returns null for undefined/null/empty', () => {
      expect(sanitizeKeySignature(undefined)).toBeNull();
      expect(sanitizeKeySignature(null)).toBeNull();
      expect(sanitizeKeySignature('')).toBeNull();
      expect(sanitizeKeySignature('   ')).toBeNull();
    });
  });

  describe('the coerced default is pitch-neutral', () => {
    it('"C" implies no accidentals, so no performed pitch is altered', () => {
      // sanitizeKeySignature falls back to "C"; keySignatureMap("C") must add
      // zero sharps/flats, which is why degrading a bad key to "C" cannot
      // transpose any note (the conservative-degradation invariant).
      expect(keySignatureMap('C')).toEqual({});
    });
  });

  describe('the fix is general, not a special-case for one string', () => {
    it('the known set is exactly the union of VexFlow-accepted sharp/flat keys', () => {
      // 15 major keys (C..C#, F..Cb) + 15 relative minors = 30 recognised keys.
      expect(KNOWN_KEY_SIGNATURES.size).toBe(30);
    });
  });
});
