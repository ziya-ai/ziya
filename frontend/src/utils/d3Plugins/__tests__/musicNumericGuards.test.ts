/**
 * Numeric / string-field guards that sit between a spec and VexFlow's display
 * APIs.  Every function here is exported pure and DOM-free, so these run in the
 * plain node path with no vexflow, no canvas and no structuredClone -- the same
 * robust style as musicPlugin.test.ts / musicPitchlessGuards.test.ts.
 *
 * Each guard was written because VexFlow stringifies whatever it is given with
 * NO numeric check, so a degenerate value is drawn verbatim onto the score
 * (or, for durations/octaves, HANGS the formatter).  These assert both the
 * neutralised bad path AND that a well-formed value round-trips byte-identical,
 * which is the property that keeps the guard from silently rewriting good specs.
 */
import {
  sanitizeMeter,
  sanitizeTempoBpm,
  sanitizeModifierNumber,
  sanitizeMeasureNumber,
  MAX_MEASURE_NUMBER,
  clampKeyOctave,
  MIN_OCTAVE,
  MAX_OCTAVE,
  sanitizeDuration,
  toNoteStructDuration,
  sanitizeBeamGroups,
  sanitizeTupletCounts,
  MAX_TUPLET_COUNT,
} from '../musicPlugin';

let warn: jest.SpyInstance;
beforeEach(() => {
  warn = jest.spyOn(console, 'warn').mockImplementation(() => {});
});
afterEach(() => {
  warn.mockRestore();
});

describe('sanitizeMeter (D22: mid-score meter must not throw BadTimeSignature)', () => {
  it('passes a well-formed n/d meter through unchanged', () => {
    for (const m of ['4/4', '3/4', '6/8', '12/8', '2/2']) {
      expect(sanitizeMeter(m)).toBe(m);
    }
    expect(warn).not.toHaveBeenCalled();
  });

  it('accepts the two glyph meters C (common) and C| (cut) verbatim', () => {
    expect(sanitizeMeter('C')).toBe('C');
    expect(sanitizeMeter('C|')).toBe('C|');
  });

  it('normalises internal whitespace to canonical n/d', () => {
    expect(sanitizeMeter(' 3 / 4 ')).toBe('3/4');
  });

  it('returns undefined (never a throw) for the shapes that abort the render', () => {
    for (const bad of ['0/4', '4/0', '3', 'x/4', '', '4/', '/4']) {
      expect(sanitizeMeter(bad)).toBeUndefined();
    }
  });

  it('treats an absent meter as "no meter", not an error', () => {
    expect(sanitizeMeter(undefined)).toBeUndefined();
    expect(sanitizeMeter(null)).toBeUndefined();
    // absent is silent; only a genuinely malformed value warns
    expect(warn).not.toHaveBeenCalled();
    sanitizeMeter('4/0');
    expect(warn).toHaveBeenCalled();
  });
});

describe('sanitizeTempoBpm (D28: bpm is stringified straight into StaveTempo)', () => {
  it('returns a well-formed integer bpm verbatim', () => {
    expect(sanitizeTempoBpm(120)).toBe(120);
    expect(sanitizeTempoBpm(60)).toBe(60);
  });

  it('preserves a fractional bpm but trims a long float tail', () => {
    expect(sanitizeTempoBpm(92.5)).toBe(92.5);
    expect(sanitizeTempoBpm(120.00001)).toBe(120);
  });

  it('drops a non-finite or non-positive bpm so no dangling metronome is drawn', () => {
    expect(sanitizeTempoBpm(NaN)).toBeUndefined();
    expect(sanitizeTempoBpm(Infinity)).toBeUndefined();
    expect(sanitizeTempoBpm(-120)).toBeUndefined();
    expect(sanitizeTempoBpm(0)).toBeUndefined();
    expect(warn).toHaveBeenCalled();
  });

  it('clamps an absurdly large bpm to the on-system cap rather than dropping it', () => {
    expect(sanitizeTempoBpm(1e21)).toBe(999);
    expect(sanitizeTempoBpm(5000)).toBe(999);
  });

  it('treats an absent bpm as "name only"', () => {
    expect(sanitizeTempoBpm(undefined)).toBeUndefined();
    expect(sanitizeTempoBpm(null)).toBeUndefined();
  });
});

describe('sanitizeModifierNumber (fingering / string number)', () => {
  it('renders a finite number as its string', () => {
    expect(sanitizeModifierNumber(3)).toBe('3');
    expect(sanitizeModifierNumber(0)).toBe('0');
  });

  it('passes an extended-technique letter through untouched', () => {
    for (const s of ['T', 'p', 'i', 'm', 'a']) {
      expect(sanitizeModifierNumber(s)).toBe(s);
    }
  });

  it('drops the degenerate values that would print "NaN"/"undefined"/empty', () => {
    expect(sanitizeModifierNumber(NaN)).toBeUndefined();
    expect(sanitizeModifierNumber(Infinity)).toBeUndefined();
    expect(sanitizeModifierNumber(undefined)).toBeUndefined();
    expect(sanitizeModifierNumber(null)).toBeUndefined();
    expect(sanitizeModifierNumber('')).toBeUndefined();
    expect(sanitizeModifierNumber('   ')).toBeUndefined();
    expect(warn).toHaveBeenCalled();
  });
});

describe('sanitizeMeasureNumber', () => {
  it('returns a well-formed positive bar index verbatim', () => {
    expect(sanitizeMeasureNumber(1)).toBe(1);
    expect(sanitizeMeasureNumber(47)).toBe(47);
  });

  it('truncates a fractional index to a whole bar', () => {
    expect(sanitizeMeasureNumber(1.5)).toBe(1);
    expect(sanitizeMeasureNumber(3.9)).toBe(3);
  });

  it('drops a non-finite or non-positive number', () => {
    expect(sanitizeMeasureNumber(NaN)).toBeUndefined();
    expect(sanitizeMeasureNumber(Infinity)).toBeUndefined();
    expect(sanitizeMeasureNumber(0)).toBeUndefined();
    expect(sanitizeMeasureNumber(-3)).toBeUndefined();
    expect(warn).toHaveBeenCalled();
  });

  it('clamps a wildly large index to the on-system cap', () => {
    expect(sanitizeMeasureNumber(1e21)).toBe(MAX_MEASURE_NUMBER);
  });

  it('treats an absent number as "no opening measure number"', () => {
    expect(sanitizeMeasureNumber(undefined)).toBeUndefined();
    expect(sanitizeMeasureNumber(null)).toBeUndefined();
  });
});

describe('clampKeyOctave (ledger-line hang guard)', () => {
  it('leaves an in-range key untouched, both grammars', () => {
    expect(clampKeyOctave('c/5')).toBe('c/5');
    expect(clampKeyOctave('C5')).toBe('C5');
    expect(clampKeyOctave('f#/4')).toBe('f#/4');
    expect(clampKeyOctave('bb/3')).toBe('bb/3');
    expect(clampKeyOctave('cn/5')).toBe('cn/5');
  });

  it('clamps an out-of-range octave, preserving the input grammar', () => {
    expect(clampKeyOctave('c/999')).toBe(`c/${MAX_OCTAVE}`);
    expect(clampKeyOctave('C999')).toBe(`C${MAX_OCTAVE}`);
    expect(clampKeyOctave('c/-5')).toBe(`c/${MIN_OCTAVE}`);
  });

  it('honours the documented [MIN_OCTAVE, MAX_OCTAVE] = [-1, 9] bounds', () => {
    expect(MIN_OCTAVE).toBe(-1);
    expect(MAX_OCTAVE).toBe(9);
    expect(clampKeyOctave(`c/${MAX_OCTAVE}`)).toBe(`c/${MAX_OCTAVE}`);
    expect(clampKeyOctave(`c/${MIN_OCTAVE}`)).toBe(`c/${MIN_OCTAVE}`);
  });

  it('returns an unparseable key untouched (valid path byte-identical)', () => {
    expect(clampKeyOctave('nonsense')).toBe('nonsense');
  });
});

describe('sanitizeDuration (formatter-hang guard on unknown durations)', () => {
  it('accepts every advertised and supported base', () => {
    for (const b of ['w', 'h', 'q', '8', '16', '32', '64', '128']) {
      expect(sanitizeDuration(b)).toEqual({ base: b, dots: 0 });
    }
  });

  it('accepts the numeric aliases for whole/half/quarter', () => {
    expect(sanitizeDuration('1').base).toBe('1');
    expect(sanitizeDuration('2').base).toBe('2');
    expect(sanitizeDuration('4').base).toBe('4');
  });

  it('parses augmentation dots and caps a runaway dot string at four', () => {
    expect(sanitizeDuration('h.')).toEqual({ base: 'h', dots: 1 });
    expect(sanitizeDuration('q..')).toEqual({ base: 'q', dots: 2 });
    expect(sanitizeDuration('q...')).toEqual({ base: 'q', dots: 3 });   // triple dot
    expect(sanitizeDuration('q....')).toEqual({ base: 'q', dots: 4 });  // quadruple dot
    expect(sanitizeDuration('q.......').dots).toBe(4);                  // capped
  });

  it('falls back to a quarter on an unknown/degenerate base rather than hanging', () => {
    expect(sanitizeDuration('999').base).toBe('q');
    expect(sanitizeDuration('x').base).toBe('q');
    expect(sanitizeDuration(1000000000 as any).base).toBe('q');
    expect(warn).toHaveBeenCalled();
  });
});

describe('toNoteStructDuration (dots lifted into their own field)', () => {
  it('splits "h." into { duration:"h", dots:1 } for the Note constructor', () => {
    expect(toNoteStructDuration('h.')).toEqual({ duration: 'h', dots: 1 });
  });
  it('routes an unknown duration through the same quarter fallback', () => {
    expect(toNoteStructDuration('zzz')).toEqual({ duration: 'q', dots: 0 });
  });
});

describe('sanitizeBeamGroups', () => {
  it('keeps well-formed positive-integer [num, den] pairs', () => {
    expect(sanitizeBeamGroups([[3, 8]])).toEqual([[3, 8]]);
    expect(sanitizeBeamGroups([[3, 8], [2, 4]])).toEqual([[3, 8], [2, 4]]);
  });

  it('drops non-integer / non-positive entries and warns', () => {
    expect(sanitizeBeamGroups([[3, 8], [0, 4]])).toEqual([[3, 8]]);
    expect(sanitizeBeamGroups([[1.5, 8]] as any)).toBeUndefined();
    expect(warn).toHaveBeenCalled();
  });

  it('returns undefined for an absent or empty list', () => {
    expect(sanitizeBeamGroups(undefined)).toBeUndefined();
    expect(sanitizeBeamGroups([])).toBeUndefined();
  });
});

describe('sanitizeTupletCounts', () => {
  it('passes an ordinary triplet / quintuplet through byte-identically', () => {
    // The overwhelming case: the well-formed pair round-trips unchanged, which
    // is what keeps the guard from rewriting good tuplets.
    expect(sanitizeTupletCounts(3, 2)).toEqual({ num: 3, inSpaceOf: 2 });
    expect(sanitizeTupletCounts(5, 4)).toEqual({ num: 5, inSpaceOf: 4 });
    // The boundary value is admitted.
    expect(sanitizeTupletCounts(MAX_TUPLET_COUNT, MAX_TUPLET_COUNT))
      .toEqual({ num: MAX_TUPLET_COUNT, inSpaceOf: MAX_TUPLET_COUNT });
  });

  it('rejects a zero / negative / fractional count (the formatter-hang path)', () => {
    // A count of 0 divides by zero in Tuplet.attach's Fraction(inSpaceOf, num);
    // negative / fractional yields a NaN tick -- both hang the formatter.
    expect(sanitizeTupletCounts(0, 2)).toBeNull();
    expect(sanitizeTupletCounts(3, 0)).toBeNull();
    expect(sanitizeTupletCounts(-3, 2)).toBeNull();
    expect(sanitizeTupletCounts(3.5, 2)).toBeNull();
    expect(sanitizeTupletCounts(3, 2.5)).toBeNull();
  });

  it('rejects an absurd count above the cap (the "3:1000" / off-page path)', () => {
    // The bug this fix closes: an unbounded count printed a ratio label like
    // "3:1000" and drove the tick rescale off the page (or, near-zero, hung).
    expect(sanitizeTupletCounts(3, 1000)).toBeNull();
    expect(sanitizeTupletCounts(1000, 2)).toBeNull();
    expect(sanitizeTupletCounts(MAX_TUPLET_COUNT + 1, 2)).toBeNull();
    expect(sanitizeTupletCounts(3, MAX_TUPLET_COUNT + 1)).toBeNull();
  });
});
