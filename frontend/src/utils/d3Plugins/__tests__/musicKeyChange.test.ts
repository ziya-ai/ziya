/**
 * Mid-score key-signature change (modulation): the carry-forward that
 * resolveEffectiveKeys computes and every render consumer reads.
 *
 * Pure/DOM-free: exercises only resolveEffectiveKeys, so it validates the
 * SOURCE without a bundle rebuild or a canvas-backed render (the drawn
 * KeySigNote / addKeySignature half is covered by the render suites).
 */
import { resolveEffectiveKeys } from '../musicPlugin';

describe('resolveEffectiveKeys (per-measure key carry-forward)', () => {
  it('resolves every bar to the base key when no measure changes it', () => {
    const measures = [{}, {}, {}];
    // Byte-identical to the single-key path: the value each use-site read
    // before (staffSpec.keySignature ?? spec.keySignature) at every bar.
    expect(resolveEffectiveKeys(measures, 'Bb')).toEqual(['Bb', 'Bb', 'Bb']);
  });

  it('carries an undefined base through unchanged', () => {
    expect(resolveEffectiveKeys([{}, {}], undefined)).toEqual([undefined, undefined]);
  });

  it('advances at the changed bar and holds until the next change', () => {
    const measures = [
      {},                       // opens in C (base)
      { keySignature: 'G' },    // modulates to G
      {},                       // still G
      { keySignature: 'Eb' },   // modulates to Eb
      {},                       // still Eb
    ];
    expect(resolveEffectiveKeys(measures, 'C')).toEqual(['C', 'G', 'G', 'Eb', 'Eb']);
  });

  it('leaves the previous key in force when a per-measure key is invalid', () => {
    // A typo must not silently blank the modulation, mirroring how an invalid
    // per-measure meter leaves the previous meter in force.
    const measures = [{}, { keySignature: 'not-a-key' }, {}];
    expect(resolveEffectiveKeys(measures, 'D')).toEqual(['D', 'D', 'D']);
  });

  it('honours a key change stated on the very first measure', () => {
    const measures = [{ keySignature: 'A' }, {}];
    expect(resolveEffectiveKeys(measures, 'C')).toEqual(['A', 'A']);
  });

  it('returns an empty array for no measures', () => {
    expect(resolveEffectiveKeys([], 'F')).toEqual([]);
  });
});
