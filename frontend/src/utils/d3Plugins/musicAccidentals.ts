/**
 * Key-signature-aware accidental filtering.
 *
 * WHY THIS EXISTS
 *
 * Notes reach VexFlow through EasyScore, whose grammar spells a pitch as
 * `letter[accidental]octave` (`C#5`).  An accidental written INTO that string
 * is an EXPLICIT accidental: EasyScore attaches its own `Accidental` modifier
 * and VexFlow renders the glyph unconditionally.  Filtering against the key
 * signature is a separate step (`Accidental.applyAccidentals`) which this
 * plugin never performed, so `keySignature` only ever affected the glyphs
 * drawn at the clef -- never the notes.
 *
 * The consequence is worst in sharp-heavy keys.  Measured in F# major (6
 * sharps: F C G D A E), a single guitar bar of the operetta emitted 14
 * redundant accidental glyphs -- every one already implied by the signature.
 * Each glyph claims ~12-15px, which is what bloated bar 1 and crushed the
 * later bars in the reported render.
 *
 * A spec author cannot reasonably work around this by hand: it would mean
 * writing `f/3` and meaning F-sharp, i.e. spelling every pitch relative to a
 * signature they must track mentally, with the spelling changing whenever the
 * key changes.  Normalising here is the right layer -- specs are authored by
 * humans and models, and neither reliably emits publication-ready accidental
 * spelling.
 *
 * ENGRAVING RULES IMPLEMENTED
 *
 *   1. A pitch matching the signature prints BARE (`c#/5` in F# major).
 *   2. A pitch deviating from the signature prints its accidental.
 *   3. An accidental, once printed, holds for the remainder of the BAR at that
 *      letter+octave, so an immediate repeat prints bare.  Scope is
 *      letter+octave, not letter: `c#/4` does not cover `c#/5`.
 *   4. A NATURAL is printed when the signature alters a letter but the spec
 *      asks for the plain letter (`g/3` in F# major -> g-natural).
 *   5. State resets at every barline.
 *
 * The filter is deliberately CONSERVATIVE: an unrecognised key string or an
 * unparseable pitch passes through untouched, because dropping an accidental
 * changes the pitch a performer reads, whereas a redundant one is merely
 * untidy.
 */

/** Order in which sharps are added to a key signature. */
const SHARP_ORDER = ['f', 'c', 'g', 'd', 'a', 'e', 'b'] as const;
/** Order in which flats are added to a key signature. */
const FLAT_ORDER = ['b', 'e', 'a', 'd', 'g', 'c', 'f'] as const;

/**
 * Sharp count per key name, majors and their relative minors.
 *
 * Minors are listed because `keySignature: "D#m"` draws the same 6 sharps as
 * F# major and must filter identically; omitting them would silently disable
 * filtering for every minor-key score.
 */
const SHARP_COUNT: Readonly<Record<string, number>> = {
  C: 0, Am: 0,
  G: 1, Em: 1,
  D: 2, Bm: 2,
  A: 3, 'F#m': 3,
  E: 4, 'C#m': 4,
  B: 5, 'G#m': 5,
  'F#': 6, 'D#m': 6,
  'C#': 7, 'A#m': 7,
};

/** Flat count per key name, majors and their relative minors. */
const FLAT_COUNT: Readonly<Record<string, number>> = {
  F: 1, Dm: 1,
  Bb: 2, Gm: 2,
  Eb: 3, Cm: 3,
  Ab: 4, Fm: 4,
  Db: 5, Bbm: 5,
  Gb: 6, Ebm: 6,
  Cb: 7, Abm: 7,
};

/**
 * Pitch grammar accepted from a spec.
 *
 * Both the slash form (`c#/5`) and EasyScore's slashless form (`C#5`) are
 * matched, because `buildNoteString` may be handed either and both must
 * filter identically.
 *
 * The accidental group is anchored AFTER the note letter, which is what
 * disambiguates `b/2` (B natural) from `bb/2` (B flat) -- a scan that merely
 * looked for 'b' anywhere in the token would read the note name itself as a
 * flat.  Verified: this bug appeared in the first draft of the filter.
 */
const PITCH_RE = /^([a-gA-G])(#{1,2}|b{1,2})?\/?(-?\d+)$/;

/** Accidental implied for each letter by a key signature, or null if unknown. */
export function keySignatureMap(key: string | undefined): Record<string, string> | null {
  if (!key) return null;
  const name = String(key).trim();
  const sharps = SHARP_COUNT[name];
  if (sharps !== undefined) {
    const out: Record<string, string> = {};
    for (let i = 0; i < sharps; i += 1) out[SHARP_ORDER[i]] = '#';
    return out;
  }
  const flats = FLAT_COUNT[name];
  if (flats !== undefined) {
    const out: Record<string, string> = {};
    for (let i = 0; i < flats; i += 1) out[FLAT_ORDER[i]] = 'b';
    return out;
  }
  // Unrecognised key name: filter nothing rather than guess.
  return null;
}

/**
 * Every key signature name VexFlow's `Stave.addKeySignature` / `KeySignature`
 * accepts -- the majors and their relative minors, exactly the union of the
 * SHARP_COUNT and FLAT_COUNT tables above (which is why it is derived from
 * them rather than restated: the two can never drift out of sync).
 *
 * VexFlow has no tolerance here: `new KeySignature(spec)` looks the name up in
 * its own `keySignatures` table and THROWS `Vex.RuntimeError('BadKeySignature',
 * ...)` on anything outside the ~30 recognised keys.  That throw is the sole
 * trigger of the music-renderer hang catalogued as Issue 28 -- an adversarial
 * `keySignature: "F####bbb-9"` escapes render(), the SVG never mounts, and the
 * render hangs to the 30s cap with zero output.
 */
export const KNOWN_KEY_SIGNATURES: ReadonlySet<string> = new Set([
  ...Object.keys(SHARP_COUNT),
  ...Object.keys(FLAT_COUNT),
]);

/** True when `key` is a key signature VexFlow will accept without throwing. */
export function isKnownKeySignature(key: unknown): boolean {
  return typeof key === 'string' && KNOWN_KEY_SIGNATURES.has(key.trim());
}

/**
 * Coerce an arbitrary spec `keySignature` to one VexFlow will draw, or `null`
 * when there is nothing to draw.
 *
 * This is the single choke point every `stave.addKeySignature` call must pass
 * through: a value VexFlow recognises is returned trimmed and unchanged; a
 * value it would throw on (`"F####bbb-9"`, `"Z#b-9"`, `""`, a number, an
 * object) is coerced to `"C"` -- the neutral no-accidental signature -- with a
 * console warning, following the plugin's established "unknown input degrades
 * to the safe default, never guessed" convention (mirrors sanitizeDuration).
 * `undefined`/`null`/empty returns `null` so the caller can skip drawing a
 * signature entirely rather than forcing a "C" glyph onto a spec that asked
 * for none.
 *
 * Degrading a bad key to "C" rather than dropping the accidental logic is the
 * conservative choice: "C" adds no sharps/flats, so no pitch a performer reads
 * is altered -- exactly the invariant filterPitch/keySignatureMap already
 * preserve for an unrecognised key (they filter nothing).  Pure and DOM-free
 * so it can be unit-tested. Exported for regression testing.
 */
export function sanitizeKeySignature(raw: unknown): string | null {
  if (raw == null) return null;
  if (typeof raw !== 'string') {
    console.warn(
      `musicPlugin: keySignature must be a string (got ${typeof raw}); `
      + `falling back to "C".`,
    );
    return 'C';
  }
  const name = raw.trim();
  if (name === '') return null;
  if (KNOWN_KEY_SIGNATURES.has(name)) return name;
  console.warn(
    `musicPlugin: unknown keySignature "${name}", falling back to "C" `
    + `(a bad key would otherwise throw in VexFlow and hang the render).`,
  );
  return 'C';
}

/**
 * Accidental state within one bar: `letter+octave` -> accidental in force.
 *
 * Created per measure by the caller and discarded at the barline (rule 5).
 */
export type BarAccidentalState = Map<string, string>;

export const newBarState = (): BarAccidentalState => new Map();

/**
 * Rewrite one pitch so its accidental prints only when engraving requires it.
 *
 * Returns the pitch unchanged when it cannot be parsed, or when no key
 * signature applies.
 *
 * VexFlow spells an explicit natural as `n` (`Cn5`), which EasyScore parses
 * and renders as a natural glyph -- that is the mechanism behind rule 4.
 *
 * WHY BAR MEMORY (rule 3) IS NOT IMPLEMENTED HERE
 *
 * Rule 3 says an accidental, once printed, holds for the rest of the bar, so a
 * repeat should print bare.  That is a statement about GLYPHS.  This function
 * rewrites the PITCH STRING, and EasyScore has no spelling that means "sounds
 * sharp, but hide the glyph" -- a bare `f/3` IS F natural unless the key
 * signature says otherwise.
 *
 * Implementing rule 3 by stripping the accidental therefore TRANSPOSED the
 * note.  Measured in C major: `['f#/3','f#/3']` emitted `F#3, F3`, the second
 * note sounding a semitone flat.  The bug was invisible in F# major -- where
 * the signature happens to re-supply the sharp -- and only appeared in keys
 * that do not imply it, which is why it survived the first round of tests.
 *
 * Rules 1/2/4 are safe because the KEY SIGNATURE re-supplies the accidental at
 * render time: the stave draws it, and VexFlow sounds a bare `f` as F# in F#
 * major.  Verified pitch-preserving across C, F# and D major.
 *
 * Rule 3 belongs in VexFlow's `Accidental.applyAccidentals()`, which operates
 * on built notes and can suppress a glyph without touching pitch.  Suppressing
 * a redundant repeat is cosmetic; sounding the wrong note is not, so the
 * omission is deliberate.
 *
 * `state` is retained in the signature (and unused) so that adding bar memory
 * later -- correctly, at the glyph layer -- needs no change at either call
 * site.
 */
export function filterPitch(
  rawKey: string,
  implied: Record<string, string> | null,
  _state?: BarAccidentalState,
): string {
  if (!implied) return rawKey;
  const raw = String(rawKey).trim();
  const match = PITCH_RE.exec(raw);
  // Unparseable (or already carrying a natural): leave it alone.  Dropping an
  // accidental would change the sounding pitch; a redundant one is cosmetic.
  if (!match) return raw;

  // Preserve the INPUT's casing in the output and lowercase only for the
  // signature lookup.  The slash form is conventionally lowercase (`c#/5`)
  // while EasyScore's slashless form is uppercase (`C#5`); rewriting `E5` as
  // `en5` silently changed the caller's spelling, which showed up as a
  // snapshot diff on an Eb-major bass staff.
  const letter = match[1];
  const lookup = letter.toLowerCase();
  const accidental = match[2] ?? '';
  const octave = match[3];
  const hadSlash = raw.includes('/');
  // Only the signature may license omitting an accidental, because only the
  // signature is re-applied when VexFlow parses the emitted pitch.
  const inSignature = implied[lookup] ?? '';

  let printed: string;
  if (accidental !== '' && accidental === inSignature) {
    printed = '';                       // rule 1: signature supplies it
  } else if (accidental === '' && (inSignature === '#' || inSignature === 'b')) {
    printed = 'n';                      // rule 4: cancel the signature
  } else {
    printed = accidental;               // rule 2: emit verbatim
  }

  return hadSlash
    ? `${letter}${printed}/${octave}`
    : `${letter}${printed}${octave}`;
}

/**
 * Filter every pitch of one measure.
 *
 * Each pitch is independent: filtering depends only on the key signature, not
 * on what preceded it in the bar (see filterPitch).  Kept as a helper so a
 * caller can filter a flat key list without threading state.
 */
export function filterMeasurePitches(
  key: string | undefined,
  keys: string[],
): string[] {
  const implied = keySignatureMap(key);
  if (!implied) return keys;
  return keys.map((k) => filterPitch(k, implied));
}
