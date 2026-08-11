/**
 * @jest-environment jsdom
 *
 * Verification of key-signature accidental filtering against the ACTUAL spec
 * that rendered badly: movement III ("Duett -- Zwei Klingen") of the F# major
 * operetta, whose guitar bar emitted 14 redundant accidental glyphs.
 *
 * Asserts on rendered glyph counts rather than on buildNoteString's output,
 * because the string is an intermediate -- what matters is what VexFlow draws.
 */
import { renderMusicSpec, buildNoteString, type MusicSpec } from '../musicPlugin';
import { keySignatureMap } from '../musicAccidentals';

// Fully chainable d3 stub: named staves route through drawStaffLabels, which
// chains .attr().attr().attr().style().text().
const chain: any = new Proxy({}, {
  get: () => (..._a: any[]) => chain,
});
const d3Stub = { select: () => chain };

const draw = async (spec: MusicSpec) => {
  const container = document.createElement('div');
  document.body.appendChild(container);
  await renderMusicSpec(container, spec, false, d3Stub);
  return container;
};

/** SMuFL codepoints for accidental glyphs. */
const ACC = { sharp: '\ue262', flat: '\ue260', natural: '\ue261', dsharp: '\ue263' };

const countGlyph = (c: HTMLElement, glyph: string): number =>
  Array.from(c.querySelectorAll('text'))
    .reduce((n, t) => n + ((t.textContent ?? '').split(glyph).length - 1), 0);

/** The real movement III guitar bar 1: eight dyads, every pitch in F# major. */
const GUITAR_BAR_1 = [
  { keys: ['f#/3', 'c#/4'], duration: '8' },
  { keys: ['f#/3', 'c#/4'], duration: '8' },
  { keys: ['a#/3', 'e#/4'], duration: '8' },
  { keys: ['a#/3', 'e#/4'], duration: '8' },
  { keys: ['b/3', 'f#/4'], duration: '8' },
  { keys: ['b/3', 'f#/4'], duration: '8' },
  { keys: ['c#/4', 'g#/4'], duration: '8' },
  { keys: ['c#/4', 'g#/4'], duration: '8' },
];

describe('movement III guitar bar: the reported regression', () => {
  it('emits no redundant sharps now that the key signature is honoured', async () => {
    const c = await draw({
      type: 'music', clef: 'treble', keySignature: 'F#', timeSignature: '4/4',
      notes: GUITAR_BAR_1 as any,
    });
    // The 6 signature sharps are drawn by the stave; note sharps must be 0.
    const sharps = countGlyph(c, ACC.sharp);
    // eslint-disable-next-line no-console
    console.log(`F# major, all-in-key bar: ${sharps} sharp glyphs (6 = signature only)`);
    expect(sharps).toBe(6);
  });

  it('still prints an out-of-key accidental in the same bar', async () => {
    // g/3 is G natural; F# major sharpens G, so a natural MUST appear.
    const c = await draw({
      type: 'music', clef: 'treble', keySignature: 'F#', timeSignature: '4/4',
      notes: [
        { keys: ['g#/3'], duration: 'q' },
        { keys: ['g/3'], duration: 'q' },
        { keys: ['f#/3'], duration: 'q' },
        { keys: ['c#/4'], duration: 'q' },
      ] as any,
    });
    expect(countGlyph(c, ACC.natural)).toBeGreaterThanOrEqual(1);
  });

  it('leaves a C major score byte-identical (no key signature to imply)', async () => {
    // C major implies nothing, so every accidental in the spec is a deviation
    // and must survive verbatim.  This is the assertion that caught rule 3
    // transposing a repeated accidental down a semitone (F#3 -> F3).
    const before = buildNoteString(GUITAR_BAR_1 as any, 'treble');
    const after = buildNoteString(GUITAR_BAR_1 as any, 'treble', 'C');
    expect(after).toBe(before);
  });

  // ------------------------------------------------------------------
  // The assertions that MATTER: sounding pitch, not emitted string.
  //
  // The first round of logic tests asserted on strings and encoded the bug
  // as expected behaviour ("f/3" read as correctly-bare).  A filter may only
  // ever suppress a GLYPH; if it changes which semitone sounds it is wrong,
  // however tidy the notation looks.
  // ------------------------------------------------------------------

  /** Semitone of an emitted EasyScore pitch, with the key signature applied. */
  const sounding = (emitted: string, key: string): number => {
    const m = /^([A-Ga-g])(#{1,2}|b{1,2}|n)?(-?\d+)$/.exec(emitted.trim());
    if (!m) throw new Error(`unparseable emitted pitch: ${emitted}`);
    const letter = m[1].toLowerCase();
    const acc = m[2] ?? '';
    const base: Record<string, number> = { c: 0, d: 2, e: 4, f: 5, g: 7, a: 9, b: 11 };
    let semis = base[letter] + Number(m[3]) * 12;
    if (acc === 'n') return semis;              // explicit natural cancels the signature
    if (acc) {
      semis += acc.startsWith('#') ? acc.length : -acc.length;
      return semis;                             // explicit accidental wins outright
    }
    // Bare letter: the key signature re-supplies its accidental at render time.
    const implied = keySignatureMap(key)?.[letter] ?? '';
    if (implied === '#') semis += 1;
    if (implied === 'b') semis -= 1;
    return semis;
  };

  /** Semitone the SPEC asked for, read from the slash-form key. */
  const intended = (specKey: string): number => {
    const m = /^([a-gA-G])(#{1,2}|b{1,2})?\/(-?\d+)$/.exec(specKey.trim());
    if (!m) throw new Error(`unparseable spec key: ${specKey}`);
    const letter = m[1].toLowerCase();
    const acc = m[2] ?? '';
    const base: Record<string, number> = { c: 0, d: 2, e: 4, f: 5, g: 7, a: 9, b: 11 };
    let semis = base[letter] + Number(m[3]) * 12;
    if (acc) semis += acc.startsWith('#') ? acc.length : -acc.length;
    return semis;
  };

  const flatKeysOf = (notes: any[]): string[] =>
    notes.flatMap((n) => (n.rest ? [] : (n.keys ?? [])));

  /** Emitted pitches, stripped of duration and chord punctuation. */
  const emittedPitches = (noteStr: string): string[] =>
    noteStr.split(',')
      .flatMap((tok) => {
        const body = tok.trim().replace(/\/[^/]+$/, '');   // drop /duration
        return body.startsWith('(')
          ? body.slice(1, -1).trim().split(/\s+/)          // chord members
          : [body];
      })
      .filter((p) => p.length > 0 && !/\/r/.test(p));

  it.each(['C', 'F#', 'D', 'Bb', 'A'])(
    'preserves every sounding pitch in %s major',
    (key) => {
      const specKeys = flatKeysOf(GUITAR_BAR_1 as any[]);
      const emitted = emittedPitches(buildNoteString(GUITAR_BAR_1 as any, 'treble', key));
      expect(emitted).toHaveLength(specKeys.length);
      emitted.forEach((e, i) => {
        expect(sounding(e, key)).toBe(intended(specKeys[i]));
      });
    },
  );

  it('preserves pitch for a repeated accidental, the rule-3 regression', () => {
    // ['f#/3','f#/3'] emitted "F#3, F3" before the fix: the second note
    // sounded a semitone flat.  C major is the revealing key, because F#
    // major masks it by re-supplying the sharp from the signature.
    const repeated = [
      { keys: ['f#/3'], duration: '8' },
      { keys: ['f#/3'], duration: '8' },
      { keys: ['c#/4'], duration: '8' },
      { keys: ['c#/4'], duration: '8' },
    ];
    for (const key of ['C', 'F#', 'D']) {
      const emitted = emittedPitches(buildNoteString(repeated as any, 'treble', key));
      const specKeys = flatKeysOf(repeated);
      emitted.forEach((e, i) => {
        expect(sounding(e, key)).toBe(intended(specKeys[i]));
      });
    }
  });

  it('preserves pitch when a natural cancels the signature', () => {
    // g/3 in F# major must sound G natural (55), not G# (56).
    const notes = [{ keys: ['g/3'], duration: 'q' }, { keys: ['g#/3'], duration: 'q' }];
    const emitted = emittedPitches(buildNoteString(notes as any, 'treble', 'F#'));
    expect(sounding(emitted[0], 'F#')).toBe(intended('g/3'));
    expect(sounding(emitted[1], 'F#')).toBe(intended('g#/3'));
  });

  it('renders the full 8-stave movement III system without error', async () => {
    // The integration case: 8 staves, multi-measure, chords, lyrics, harp
    // pedals -- exercising both call sites and the label gutter.
    const spec: MusicSpec = {
      type: 'music', keySignature: 'F#', timeSignature: '4/4', autoBeam: true,
      tempo: { name: 'Allegro', duration: 'q', bpm: 152 },
      staves: [
        { name: 'Flöte', clef: 'treble', measures: [{ notes: [
          { keys: ['c#/6'], duration: 'q' }, { keys: ['b/5'], duration: '8' },
          { keys: ['a#/5'], duration: '8' }, { keys: ['g#/5'], duration: 'q' },
          { keys: ['f#/5'], duration: 'q' },
        ] }] },
        { name: 'E-Gitarre', clef: 'treble', measures: [{ notes: GUITAR_BAR_1 }] },
        { name: 'Schlagzeug', clef: 'percussion', measures: [{ notes: [
          { keys: ['f/4'], duration: 'q' }, { keys: ['c/5'], duration: 'q' },
          { keys: ['f/4'], duration: 'q' }, { keys: ['c/5'], duration: 'q' },
        ] }] },
        { name: 'Pedalharfe', clef: 'treble', measures: [{ notes: [
          { keys: ['f#/4', 'a#/4', 'c#/5'], duration: 'q', harpPedal: 'vv-|vvvv' },
          { keys: ['f#/4', 'a#/4', 'c#/5'], duration: 'q' },
          { keys: ['f#/4', 'a#/4', 'c#/5'], duration: 'q' },
          { keys: ['f#/4', 'a#/4', 'c#/5'], duration: 'q' },
        ] }] },
        { name: 'Pedalharfe', clef: 'bass', measures: [{ notes: [
          { keys: ['f#/2'], duration: 'h' }, { keys: ['c#/3'], duration: 'h' },
        ] }] },
        { name: 'Hakenharfe', clef: 'treble', measures: [{ notes: [
          { keys: ['f#/4'], duration: 'q' }, { keys: ['c#/5'], duration: 'q' },
          { keys: ['a#/4'], duration: 'q' }, { keys: ['c#/5'], duration: 'q' },
        ] }] },
        { name: 'Sopran', clef: 'treble', measures: [{ notes: [
          { keys: ['f#/5'], duration: 'q', lyric: { text: 'Zwei' } },
          { keys: ['a#/5'], duration: 'q', lyric: { text: 'Klin' } },
          { keys: ['c#/6'], duration: 'h', lyric: { text: 'gen' } },
        ] }] },
        { name: 'Violoncello', clef: 'bass', measures: [{ notes: [
          { keys: ['f#/2'], duration: 'q' }, { keys: ['a#/2'], duration: 'q' },
          { keys: ['b/2'], duration: 'q' }, { keys: ['c#/3'], duration: 'q' },
        ] }] },
      ],
    } as any;

    const c = await draw(spec);
    expect(c.querySelector('svg')).toBeTruthy();
    // 8 staves x 6 signature sharps = 48, and nothing more.
    const sharps = countGlyph(c, ACC.sharp);
    // eslint-disable-next-line no-console
    console.log(`8-stave movement III system: ${sharps} sharp glyphs (48 = 8 staves x signature)`);
    expect(sharps).toBe(48);
  });
});
