/**
 * Regression test for group G-95e228 / D-428 (music-w1-04) in musicPlugin.ts.
 *
 * music-w1-04 is a grand staff whose two staves each set `autoBeam: true` AND
 * whose `crossStaffBeams` names the very same eighth notes.  The per-staff
 * auto-beamer beamed those notes, and then the cross-staff Beam beamed them
 * again over the identical noteheads: the same notes carried two beams, and
 * VexFlow drew conflicting stems/flags -- the degenerate "elongated stems into
 * one thick bar above the treble" grand-staff artefact triage saw.  (Triage
 * read this as "never builds a proper Beam/Curve", but the Beam/Curve/StaveTie
 * ARE built; the real fault is the auto-beamer double-beaming cross-staff
 * notes.)
 *
 * splitBeamRunsExcluding() is the withholding primitive: it hands the per-staff
 * auto-beamer only the runs of notes a cross-staff beam does NOT own, split AT
 * each owned note.  It is absent from the unpatched source, so the import
 * resolves to `undefined` and every call below throws -- the suite fails
 * without the fix and passes with it.  VexFlow is deliberately NOT imported
 * (loading it in jsdom trips a font-metrics path needing structuredClone), so
 * this checks the pure run-splitter against object-identity note lists that
 * mirror the w1-04 shape.
 */
import { splitBeamRunsExcluding } from '../../../utils/d3Plugins/musicPlugin';

describe('D-428 splitBeamRunsExcluding: cross-staff notes are withheld from auto-beam', () => {
  test('empty exclusion set returns the whole measure as one run (ordinary score, unchanged)', () => {
    const n = [{ k: 'a' }, { k: 'b' }, { k: 'c' }];
    expect(splitBeamRunsExcluding(n, new Set())).toEqual([n]);
  });

  test('empty note list yields no runs', () => {
    expect(splitBeamRunsExcluding([], new Set())).toEqual([]);
  });

  test('w1-04 treble: the two claimed leading eighths are dropped, leaving the tail', () => {
    // a/4(8), c/5(8), f/5(h), e/5(q); crossStaffBeams claims the two eighths.
    const a4 = { k: 'a/4' };
    const c5 = { k: 'c/5' };
    const f5 = { k: 'f/5' };
    const e5 = { k: 'e/5' };
    const measure = [a4, c5, f5, e5];
    const claimed = new Set([a4, c5]);
    const runs = splitBeamRunsExcluding(measure, claimed);
    // Only the un-claimed tail survives; the claimed eighths go to the
    // cross-staff beam alone, so the auto-beamer never re-beams them.
    expect(runs).toEqual([[f5, e5]]);
    expect(runs.flat()).not.toContain(a4);
    expect(runs.flat()).not.toContain(c5);
  });

  test('an interior claimed note SPLITS the measure so its neighbours are not merged into one beam', () => {
    const a = { k: 'a' };
    const mid = { k: 'mid' };
    const b = { k: 'b' };
    const runs = splitBeamRunsExcluding([a, mid, b], new Set([mid]));
    // Two separate runs -- a and b must never end up in one auto-beam group
    // across the cross-staff-owned note between them.
    expect(runs).toEqual([[a], [b]]);
  });

  test('every note claimed yields no runs at all (nothing left to auto-beam)', () => {
    const a = { k: 'a' };
    const b = { k: 'b' };
    expect(splitBeamRunsExcluding([a, b], new Set([a, b]))).toEqual([]);
  });
});
