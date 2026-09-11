/**
 * Regression test for group G-ee18bb / D-166 (music-w2-09) in musicPlugin.ts.
 *
 * music-w2-09 gives all 40 notes an above-staff annotation written in the
 * SINGULAR `annotation: {text, position}` form (alongside a `chordSymbol`).
 * The render core only ever iterated the PLURAL `specNote.annotations` array,
 * so every singular annotation was silently dropped -- 40 lost.  Triage read
 * this as an above-band "negotiation" failure, but the true cause is upstream:
 * the singular field was never read at all.  Once both the plural array and
 * the singular field are collected, VexFlow's ModifierContext stacks the
 * Annotation above the ChordSymbol on its own, so no bespoke band logic is
 * needed.
 *
 * normalizeNoteAnnotations() is the collector.  It is absent from the
 * unpatched source, so the import resolves to `undefined` and every call below
 * throws -- the suite fails without the fix and passes with it (the same
 * fails-without/passes-with pattern the sibling D-166 helper suite uses).
 * VexFlow is deliberately NOT imported (loading it in jsdom trips a
 * font-metrics path needing structuredClone), so this checks the pure
 * collector against the exact spec shapes.
 */
import { normalizeNoteAnnotations } from '../../../utils/d3Plugins/musicPlugin';

describe('D-166 normalizeNoteAnnotations: singular `annotation` is not dropped', () => {
  test('a note with ONLY the singular `annotation` yields that annotation', () => {
    // This is exactly music-w2-09's per-note shape.
    const note = {
      chordSymbol: 'Cmaj7',
      annotation: { text: 'a0', position: 'above' as const },
    };
    const anns = normalizeNoteAnnotations(note);
    expect(anns).toHaveLength(1);
    expect(anns[0].text).toBe('a0');
    expect(anns[0].position).toBe('above');
  });

  test('the whole w2-09-style bar keeps ALL 40 annotations (none dropped)', () => {
    const notes = Array.from({ length: 40 }, (_, i) => ({
      chordSymbol: 'Cmaj7',
      annotation: { text: `a${i}`, position: 'above' as const },
    }));
    const total = notes.reduce((n, x) => n + normalizeNoteAnnotations(x).length, 0);
    expect(total).toBe(40);
  });

  test('the plural `annotations` array still works and is preserved in order', () => {
    const note = {
      annotations: [
        { text: 'x', position: 'above' as const },
        { text: 'y', position: 'below' as const },
      ],
    };
    const anns = normalizeNoteAnnotations(note);
    expect(anns.map((a) => a.text)).toEqual(['x', 'y']);
  });

  test('both forms merge, plural first then singular, deterministically', () => {
    const note = {
      annotations: [{ text: 'plural', position: 'above' as const }],
      annotation: { text: 'singular', position: 'below' as const },
    };
    expect(normalizeNoteAnnotations(note).map((a) => a.text))
      .toEqual(['plural', 'singular']);
  });

  test('a note with no annotations at all yields an empty list (no throw)', () => {
    expect(normalizeNoteAnnotations({ chordSymbol: 'Cmaj7' })).toEqual([]);
    expect(normalizeNoteAnnotations(null)).toEqual([]);
    expect(normalizeNoteAnnotations(undefined)).toEqual([]);
  });

  test('the returned array is a copy -- mutating it never aliases the spec', () => {
    const source = [{ text: 'x', position: 'above' as const }];
    const note = { annotations: source };
    const anns = normalizeNoteAnnotations(note);
    anns.push({ text: 'injected', position: 'above' as const });
    expect(source).toHaveLength(1);
  });
});
