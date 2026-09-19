/**
 * @jest-environment jsdom
 *
 * G-99d33f: above-staff and deep-ledger reserve for the music plugin.
 *
 * Two structural defects shared musicPlugin.ts's vertical-reserve maths:
 *   - D-436 (music-w2-11): 60 six-note chords alternating extreme low/high
 *     registers.  The stacked-system layout reserved nothing for a chord's
 *     ledger reach ABOVE the top line / BELOW the bottom line, so the topmost
 *     combs were pushed off the canvas top and, between systems, an extreme
 *     chord in one system collided with the extreme chord in its neighbour.
 *   - D-438 (music-w2-09): 40 notes each carrying an above-staff `chordSymbol`
 *     AND an above `annotation`.  `needsRoomAbove` omitted both, so a score
 *     built entirely from them reserved ZERO top margin and the symbols clipped
 *     off the canvas top.
 *
 * These suites pin the pure helpers that back both reserves (RED pre-fix: the
 * symbols did not exist, so the import yielded `undefined` and every call
 * threw).  The byte-identity guarantee is pinned too: an ordinary-register
 * score reserves nothing extra, and a chord-symbol-free score is not flagged.
 */

import {
  diatonicIndexOfKey,
  noteBandExtentPx,
  ledgerReservePx,
  hasAboveStaffChordOrAnnotation,
} from '../musicPlugin';

describe('diatonicIndexOfKey', () => {
  it('is monotonic in pitch and ignores the accidental', () => {
    expect(diatonicIndexOfKey('c/4')).toBe(4 * 7 + 0);
    expect(diatonicIndexOfKey('f/8')).toBe(8 * 7 + 3);
    // Higher pitch => strictly greater index.
    expect((diatonicIndexOfKey('f/8') as number))
      .toBeGreaterThan(diatonicIndexOfKey('c/2') as number);
    // The accidental does not move the staff position.
    expect(diatonicIndexOfKey('f#/8')).toBe(diatonicIndexOfKey('f/8'));
    expect(diatonicIndexOfKey('bb/2')).toBe(diatonicIndexOfKey('b/2'));
  });

  it('returns null for a key that does not parse', () => {
    expect(diatonicIndexOfKey('not-a-key')).toBeNull();
    expect(diatonicIndexOfKey('')).toBeNull();
  });
});

describe('noteBandExtentPx (D-436)', () => {
  it('measures a deep reach above the top line and below the bottom line', () => {
    // f/8 is 21 diatonic steps above treble's top line F5 -> 105px.
    expect(noteBandExtentPx(['f/8'], 'treble').above).toBe(105);
    // c/2 is 16 steps below treble's bottom line E4 -> 80px.
    expect(noteBandExtentPx(['c/2'], 'treble').below).toBe(80);
  });

  it('contributes nothing for a key that sits inside the staff', () => {
    // g/4 (below the top line) and c/4 (near the bottom line) reach little.
    const ext = noteBandExtentPx(['g/4'], 'treble');
    expect(ext.above).toBe(0);
  });
});

describe('ledgerReservePx (D-436)', () => {
  it('reserves extra room for an extreme-register score', () => {
    const staves = [{
      clef: 'treble',
      keys: [
        ['c/7', 'e/7', 'g/7', 'b/7', 'd/8', 'f/8'], // extreme high
        ['c/2', 'e/2', 'g/2', 'b/2', 'd/3', 'f/3'], // extreme low
      ],
    }];
    const reserve = ledgerReservePx(staves);
    // 105px above and 80px below, minus the 40px baseline VexFlow handles.
    expect(reserve.above).toBe(65);
    expect(reserve.below).toBe(40);
  });

  it('reserves nothing for an ordinary-register score (byte-identical)', () => {
    const staves = [{
      clef: 'treble',
      keys: [['c/4'], ['d/4'], ['e/4'], ['f/4'], ['g/4']],
    }];
    expect(ledgerReservePx(staves)).toEqual({ above: 0, below: 0 });
  });
});

describe('hasAboveStaffChordOrAnnotation (D-438)', () => {
  const read = (s: any) => s.notes ?? [];

  it('flags an above-staff chord symbol and an above annotation', () => {
    expect(hasAboveStaffChordOrAnnotation(
      [{ notes: [{ keys: ['c/4'], chordSymbol: 'Cmaj7' }] }], read,
    )).toBe(true);
    expect(hasAboveStaffChordOrAnnotation(
      [{ notes: [{ keys: ['c/4'], annotation: { text: 'a0', position: 'above' } }] }],
      read,
    )).toBe(true);
  });

  it('does not flag a below-position chord symbol or a plain note', () => {
    expect(hasAboveStaffChordOrAnnotation(
      [{ notes: [{ keys: ['c/4'], chordSymbol: { text: 'I', position: 'below' } }] }],
      read,
    )).toBe(false);
    expect(hasAboveStaffChordOrAnnotation(
      [{ notes: [{ keys: ['c/4'] }] }], read,
    )).toBe(false);
  });
});
