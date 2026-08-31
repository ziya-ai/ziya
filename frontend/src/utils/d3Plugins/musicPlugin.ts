/**
 * Shared VexFlow rendering core for music notation.
 *
 * Used by both:
 *   - musicPlugin.ts (Tier 2, full-chrome ```music``` fenced blocks)
 *   - MusicInlineRenderer (Tier 1, no-chrome inline `music: ...` codespans)
 *
 * Keeping the actual VexFlow setup/annotation/harp-pedal-overlay logic in
 * one place avoids the two tiers drifting apart.
 */

import {
  filterPitch,
  isKnownKeySignature,
  keySignatureMap,
  newBarState,
  sanitizeKeySignature,
} from './musicAccidentals';

export type MusicClef = 'treble' | 'bass' | 'alto' | 'tenor' | 'percussion';

export interface MusicAnnotation {
  text: string;
  position?: 'above' | 'below';
}

/**
 * A note-index span, used by slurs, ties, glissandos and hairpins.
 * Indices refer to positions in the spec's own `notes` array, which is the
 * only stable handle a spec author has -- VexFlow's internal note objects
 * are created during rendering.
 */
export interface MusicSpan {
  from: number;
  to: number;
}

export interface MusicGlissando extends MusicSpan {
  /** Label drawn along the line.  Defaults to "gliss." */
  text?: string;
}

export interface MusicHairpin extends MusicSpan {
  /** "cresc" (default) widens; "dim"/"decresc" narrows. */
  type?: 'cresc' | 'dim' | 'decresc';
}

/**
 * A sustain-pedal marking spanning a run of notes: press at `from`, release at
 * `to`.  Drawn BELOW the staff, beneath the dynamics band, as published piano
 * scores set it.
 *
 * VexFlow ships a `PedalMarking` primitive, but it is a stave-attached modifier
 * that positions itself from the notes' resolved geometry the same way the
 * dynamics/volta layers do -- and, like `TextDynamics`, its vertical placement
 * is not controllable in VexFlow 5.0 (it lands in the same band the hairpins
 * and dynamics already occupy).  So -- exactly like the hairpin, dynamics,
 * volta and trill-glyph layers -- the pedal is hand-drawn with d3 in the
 * post-format overlay pass (see drawPedalLine), reading the press/release
 * notes' resolved x, which is the only way to keep it on its own band clear of
 * the dynamics above it.
 */
export interface MusicPedal extends MusicSpan {
  /**
   * Which of the three real piano pedals this marking is for.  A piano has
   * three, and they are NOT engraved alike -- naming the pedal is the only way
   * to reach the middle or left pedal, which print their own fixed wording
   * rather than the damper bracket:
   *   "sustain" (default) -- the damper (right) pedal; drawn per `style` below.
   *   "sostenuto" -- the middle pedal; always engraved "Sost. Ped." ... "*",
   *     so `style` is ignored (its bracket/text distinction is a sustain-only
   *     convention).
   *   "una-corda" -- the soft (left) pedal; always engraved
   *     "una corda" ... "tre corde", so `style` is likewise ignored.
   * An unknown name is skipped with a console warning, matching the plugin's
   * unknown-name convention.
   */
  pedal?: 'sustain' | 'sostenuto' | 'una-corda';
  /**
   * How a SUSTAIN pedal is drawn (ignored for sostenuto / una-corda, which
   * print their own wording above):
   *   "bracket" (default) -- the modern line-with-down-hooks bracket (Dorico /
   *     Henle house style): a horizontal rail with a short leg dropping at the
   *     press and release ends.
   *   "text" -- the older piano notation: "Ped." at the press, a "*" at the
   *     release.
   *   "mixed" -- the hybrid published notation: "Ped." at the press, then a
   *     bracket running to the release.
   */
  style?: 'bracket' | 'text' | 'mixed';
  /**
   * Extra distance, in stave-line units, to drop the pedal line BELOW its
   * default band -- raise it to clear a lyric or a dynamic sharing the space
   * beneath the staff.  Omitted, the pedal sits on its usual band beneath the
   * dynamics; a value <= 0 is treated as no drop, so the default path is
   * byte-identical.
   */
  line?: number;
}

/**
 * Engraved [press, release] wording for the two named pedals that do NOT use
 * the sustain bracket/text styles.
 *
 * A closed map for the same reason as ARTICULATION_CODES: an unknown `pedal`
 * value is skipped with a console warning rather than guessed at.  "sustain"
 * is deliberately ABSENT -- it is drawn by `style` (bracket / text / mixed),
 * not by fixed wording -- so a lookup miss cleanly distinguishes the
 * damper-pedal path from a genuinely unknown name.  The release strings are
 * the published counterparts a score prints ("*" cancels the sostenuto, "tre
 * corde" restores all three strings after "una corda").
 */
export const PEDAL_TYPE_LABELS: Readonly<Record<string, [string, string]>> = {
  sostenuto: ['Sost. Ped.', '*'],
  'una-corda': ['una corda', 'tre corde'],
};

/**
 * An explicit beam over a run of notes, by index into the staff's own note
 * list.  Use this only when the automatic grouping is wrong; `autoBeam` on the
 * spec handles the ordinary case.
 */
export interface MusicBeam extends MusicSpan {}

/**
 * A beam that threads across TWO OR MORE staves of a grand staff -- the
 * running keyboard figure that flows from the bass staff up into the treble
 * (or back) under ONE continuous beam.
 *
 * A per-staff `beams` / `autoBeam` cannot express it: each addresses a SINGLE
 * staff's own note list, so a run that alternates between the hands (bass,
 * bass, treble, treble) simply cannot be written -- neither staff's list holds
 * the run in playing order.  So the members are named at the SPEC level as
 * `[staffIndex, noteIndex]` pairs into the addressed staves' own `notes`, IN
 * PLAYING ORDER (left to right), which is the only encoding that can thread the
 * beam between the two lists.
 *
 * VexFlow draws the beam by reading each member note's own stave Y, which is
 * the standard cross-staff mechanism; a beam requires a single shared stem
 * side, so every member's stem is forced to `stemDirection`.  Only meaningful
 * on a multi-staff (`staves`) spec; on one staff use `beams` / `autoBeam`.
 */
export interface MusicCrossStaffBeam {
  /** Members as [staffIndex, noteIndex] pairs, in playing order. */
  notes: Array<[number, number]>;
  /**
   * Force the whole group's stems onto one side, which a beam requires.
   * "up" (default) puts the beam BETWEEN the staves -- the usual keyboard
   * case; "down" puts it below.
   */
  stemDirection?: 'up' | 'down';
}

/**
 * A slur or tie whose two ends lie on DIFFERENT staves of a grand staff -- a
 * phrase arc, or a single held pitch, passed from one hand's staff into the
 * other.
 *
 * A per-staff `slurs` / `ties` entry cannot express it: each addresses a
 * SINGLE staff's own note list, so an arc whose endpoints live on two staves
 * has no one list to index them in.  So the two ends are named at the SPEC
 * level as `[staffIndex, noteIndex]` pairs into the addressed staves' own
 * `notes` -- the same encoding MusicCrossStaffBeam uses for its members.
 *
 * VexFlow's Curve / StaveTie position themselves from each endpoint note's own
 * resolved stave Y, which is exactly the cross-staff mechanism the beam relies
 * on, so a curve whose ends sit on different staves simply arcs between them --
 * no special primitive is needed, only the two-staff addressing a per-staff
 * span cannot provide.  Refused (not drawn wrongly) when an endpoint is out of
 * range or the two ends land on different systems, matching the same-system
 * rule every other span (see spanEnds) obeys.  Only meaningful on a
 * multi-staff (`staves`) spec; within one staff use `slurs` / `ties`.
 */
export interface MusicCrossStaffSlur {
  /**
   * "slur" (default) draws the phrase arc between two different pitches;
   * "tie" holds ONE sustained pitch across the staff change.
   */
  curve?: 'slur' | 'tie';
  /** Start endpoint as a [staffIndex, noteIndex] pair. */
  from: [number, number];
  /** End endpoint as a [staffIndex, noteIndex] pair. */
  to: [number, number];
}

/**
 * A tuplet (triplet, quintuplet, ...) over a run of notes, by index into the
 * staff's own note list.  VexFlow has no way to spell an irrational duration
 * such as "an eighth of a triplet" in a duration code, so the notes are
 * written at their FACE value (three eighths for an eighth triplet) and this
 * span both draws the "3" bracket AND rescales their tick values so the group
 * occupies the correct beat time -- without it the three eighths fill 3/8 of
 * a bar instead of a quarter, and the horizontal spacing is wrong.
 */
export interface MusicTuplet extends MusicSpan {
  /** Notes played.  Defaults to the number of notes in the span (3 -> triplet). */
  num?: number;
  /**
   * In the time of how many notes of the same value.  Defaults to 2, which is
   * the triplet case (3 in the time of 2); a quintuplet is num 5, inSpaceOf 4.
   */
  inSpaceOf?: number;
  /** Show the full ratio ("3:2") rather than just the count.  Defaults to
   *  VexFlow's heuristic (ratio shown only when num - inSpaceOf > 1). */
  ratioed?: boolean;
  /** Draw the enclosing bracket.  Defaults to VexFlow's heuristic: on when the
   *  notes are not beamed, off when they are. */
  bracketed?: boolean;
  /** "above" (default) or "below" the staff. */
  position?: 'above' | 'below';
}

/**
 * A placement bracket: 8va / 8vb / 15ma, or a spanning direction like "rit.".
 * Drawn above (or below) a run of notes with a hooked dashed line.
 */
export interface MusicBracket extends MusicSpan {
  /** Main text, e.g. "8" or "rit.".  Defaults to "8". */
  text?: string;
  /** Raised suffix, e.g. "va" / "vb" / "ma". */
  superscript?: string;
  /** "above" (default) or "below" the staff. */
  position?: 'above' | 'below';
  /** Dashed by convention; set false for a solid line. */
  dashed?: boolean;
  /**
   * Distance from the staff in stave-line units.  Raise this to clear other
   * material in the same band; the renderer already lifts brackets by one
   * line when a tempo mark is present, so this is for finer control.
   */
  line?: number;
}

/**
 * An extended trill line -- the wavy line after a `tr`, or a vibrato squiggle.
 * A per-note `trill` ornament marks a single note; this spans a range.
 *
 * For the default `trill` wiggle the renderer prepends the "tr" glyph to the
 * start note automatically (a published trill is "tr" + wavy line; a bare
 * squiggle reads as vibrato, not a trill), so the line is self-sufficient --
 * no separate ornament is required.  If the start note already carries a
 * `trill` ornament the auto "tr" is suppressed so it is not printed twice.
 * The vibrato / sawtooth wiggles are not trills and get no "tr".
 */
export interface MusicTrillLine extends MusicSpan {
  /** Wiggle glyph, a key of WIGGLE_CODES.  Defaults to "trill". */
  wiggle?: string;
}

/**
 * A chord label above (or below) a note.  A bare string is the common case
 * ("Cmaj7"); the object form reaches the engraved symbols and superscripts
 * that a plain string cannot express.
 */
export interface MusicChordSymbol {
  /** Root and any plain-text quality, e.g. "C" or "Dm". */
  text?: string;
  /** Engraved symbol from CHORD_SYMBOL_GLYPHS, e.g. "halfDiminished". */
  glyph?: string;
  /** Raised text, e.g. "maj7" or "7" for a roman numeral. */
  superscript?: string;
  /** Lowered text, e.g. an inversion figure. */
  subscript?: string;
  /** "above" (default) for chord charts; "below" for roman-numeral analysis. */
  position?: 'above' | 'below';
}

/** Left-hand fingering digit, with optional placement. */
export interface MusicFingering {
  number: string | number;
  position?: 'above' | 'below' | 'left' | 'right';
}

/**
 * A grace note attached BEFORE a main note -- an appoggiatura or acciaccatura,
 * or an ornamental run of several small notes leading into the beat.
 *
 * Grace notes carry no beat time of their own: they are engraved small, tucked
 * against the left of the note they decorate, and (unlike a tuplet) do not
 * change where the main notes fall.  VexFlow models them as a `GraceNoteGroup`
 * MODIFIER on the main note rather than as tickables in the voice, which is
 * exactly why they cannot be expressed through the EasyScore note string and
 * need this separate structure.
 *
 * `keys` and `duration` mirror MusicNoteSpec so a chord grace ("(c/5 e/5)") and
 * the usual eighth/sixteenth ornamental values both work.
 */
export interface MusicGraceNote {
  /** One entry per note in the grace chord, e.g. ["b/4"] or ["e/5","g/5"]. */
  keys: string[];
  /** VexFlow duration code: w, h, q, 8, 16, 32 (+ "." for dotted). */
  duration: string;
  /**
   * Draw the slash through the stem -- the acciaccatura ("crushed", played as
   * fast as possible).  Its absence is the appoggiatura (a leaning grace that
   * takes time from the main note).  Only meaningful on a single flagged
   * grace; a beamed run carries the slash on its first note.
   */
  slash?: boolean;
}

/**
 * A sung syllable placed beneath a note (vocal underlay).
 *
 * A bare string is the common case ("love"); the object form reaches the
 * engraving details a plain string cannot: which VERSE line it belongs to,
 * whether a HYPHEN should join it to the next syllable of the same word, and
 * whether a melisma EXTENDER line should run to the following note.
 */
export interface MusicLyric {
  /** The syllable text, e.g. "love" or "lo". */
  text: string;
  /** Verse line, 1-based; verses stack downward.  Defaults to 1. */
  verse?: number;
  /**
   * Word position.  "begin"/"middle" draw a hyphen to the next syllable so a
   * split word ("lo-ver") reads as one word; "single" (default) and "end" do
   * not.  This is the only way to distinguish "a hyphen belongs here" from
   * "these are two separate words", which note spacing alone cannot convey.
   */
  syllabic?: 'single' | 'begin' | 'middle' | 'end';
  /**
   * Draw a melisma extender -- the horizontal line held under a word sung
   * across several notes -- from this syllable to the following note.
   */
  extend?: boolean;
}

/**
 * Wiggle glyphs for trill lines, as raw SMuFL codepoints.
 *
 * VexFlow's `code` option is the codepoint itself, NOT an index -- passing a
 * small integer like 1..4 draws nothing at all, silently, because the
 * resulting character has no glyph.  Names are mapped here so a spec author
 * never has to know a codepoint.
 */
export const WIGGLE_CODES: Readonly<Record<string, number>> = {
  trill: 0xeaa4,
  vibrato: 0xeab0,
  'vibrato-wide': 0xeab1,
  sawtooth: 0xeabc,
};

/**
 * Engraved chord-symbol glyphs VexFlow ships.  A closed set for the same
 * reason as ARTICULATION_CODES: an unknown name is not rejected, and the
 * fallback would silently misrepresent the harmony.
 */
export const CHORD_SYMBOL_GLYPHS: ReadonlySet<string> = new Set([
  'diminished', 'dim', 'halfDiminished', 'augmented', 'majorSeventh',
  'minor', '+', '-', '#', 'b', 'over', '/',
  'leftParen', 'rightParen', 'leftBracket', 'rightBracket',
]);

/**
 * Friendly articulation name -> VexFlow code.
 *
 * A map rather than a pass-through because VexFlow does NOT reject an unknown
 * code: `new Articulation('staccato')` renders the literal *word* "staccato"
 * onto the staff as ASCII glyphs (verified -- U+73 U+74 U+61 ... instead of
 * the staccato dot U+E1E7).  Only a closed set can prevent that, and the
 * codes themselves ("a.", "a>") are not guessable by a spec author.
 */
export const ARTICULATION_CODES: Readonly<Record<string, string>> = {
  staccato: 'a.',
  staccatissimo: 'av',
  accent: 'a>',
  tenuto: 'a-',
  marcato: 'a^',
  'fermata-above': 'a@a',
  'fermata-below': 'a@u',
  harmonic: 'ah',
  'open-string': 'ao',
  upbow: 'a|',
  downbow: 'am',
};

/**
 * Which side of the staff each articulation is engraved on.
 *
 * Every articulation was previously force-placed ABOVE the staff
 * (`.setPosition(Modifier.Position.ABOVE)`), but that is WRONG for the
 * inverted fermata: `fermata-below` selects the below-staff glyph ("a@u", the
 * downward-curving fermata a score prints UNDER the lowest note or beneath a
 * barline for the lower part), yet forcing it ABOVE drew that below-glyph
 * hanging over the top of the staff -- the curve pointing the wrong way, on the
 * wrong side.  Its name literally says "below", so the placement contradicted
 * the request.
 *
 * A sparse map with an ABOVE default: only the entries that belong below are
 * listed, so every OTHER articulation keeps its exact previous placement and
 * the render is byte-identical for them.  Kept as its own lookup (rather than
 * hard-coding the one case) so the friendly-name-through-a-table convention the
 * rest of the plugin follows extends to placement too.
 */
export const ARTICULATION_POSITIONS: Readonly<Record<string, 'above' | 'below'>> = {
  'fermata-below': 'below',
};

/** Friendly ornament name -> VexFlow code.  Closed for the same reason. */
export const ORNAMENT_CODES: Readonly<Record<string, string>> = {
  trill: 'tr',
  mordent: 'mordent',
  'mordent-inverted': 'mordent_inverted',
  turn: 'turn',
  'turn-inverted': 'turn_inverted',
};

/**
 * Friendly breath-mark name -> the glyph KIND drawBreathMarks draws.
 *
 * A closed map for the same reason as ARTICULATION_CODES/ORNAMENT_CODES: an
 * unknown name is skipped with a warning rather than guessed at.  Several
 * spellings collapse onto one kind because a grand pause is called by more
 * than one name in the wild ("caesura" / "grand pause" / "railroad tracks"),
 * and an author should not have to know which the renderer prefers.  These are
 * KINDS rather than VexFlow codes because VexFlow ships no breath-mark
 * primitive: the marks are hand-drawn as a post-format d3 overlay, exactly
 * like the trill glyph and dynamics (see drawBreathMarks).
 */
export const BREATH_MARKS: Readonly<Record<string, 'comma' | 'tick' | 'caesura' | 'caesura-curved'>> = {
  comma: 'comma',
  tick: 'tick',
  caesura: 'caesura',
  'grand-pause': 'caesura',
  railroad: 'caesura',
  // The alternate published caesura: two BOWED strokes rather than the
  // straight "railroad-tracks" diagonals.  Given its own kind (not folded onto
  // 'caesura') so drawBreathMarks can curve it; documented as a `breath` value
  // in the skill prompt, which is why its omission here left the value looked
  // up as unknown and silently skipped.
  'caesura-curved': 'caesura-curved',
};

/**
 * Friendly arpeggio / stroke name -> VexFlow `Stroke.Type` key.
 *
 * A chord roll (the vertical wavy line to the left of a chord meaning "spread
 * the notes") is a VexFlow `Stroke` MODIFIER, whose `type` is one of the
 * numeric `Stroke.Type` constants -- not a string.  A map to the constant's
 * KEY (resolved to `Stroke.Type[key]` at attach time, the same indirection
 * BARLINE_TYPES/NAVIGATION_MARKS use) keeps the raw numbers and the internal
 * enum out of the public spec, and makes the friendly names an author actually
 * reaches for ("arpeggio", "arpeggio-up") the contract.  Closed for the same
 * reason as ARTICULATION_CODES: an unknown value is skipped with a warning
 * rather than guessed at.  The plain "arpeggio" is the directionless roll
 * (published default); the "-up"/"-down" variants add the arrowhead, and the
 * guitar brush / rasgueado strokes are the remaining Stroke.Type members.
 */
export const ARPEGGIO_STROKE_TYPES: Readonly<Record<string, string>> = {
  arpeggio: 'ARPEGGIO_DIRECTIONLESS',
  'arpeggio-up': 'ROLL_UP',
  'arpeggio-down': 'ROLL_DOWN',
  'brush-up': 'BRUSH_UP',
  'brush-down': 'BRUSH_DOWN',
  'rasgueado-up': 'RASGUEADO_UP',
  'rasgueado-down': 'RASGUEADO_DOWN',
};

/**
 * Dynamic marks VexFlow can typeset.  TextDynamics builds each mark from
 * per-letter glyphs and only knows f, p, m, s, z and r, so anything outside
 * this set would emit ASCII or nothing at all.
 */
export const DYNAMIC_MARKS: ReadonlySet<string> = new Set([
  'ppp', 'pp', 'p', 'mp', 'mf', 'f', 'ff', 'fff', 'sf', 'sfz', 'rfz', 'fp',
]);

/**
 * Dynamics are drawn BELOW the staff, on a consistent band, as a hand-drawn
 * d3 overlay (see drawDynamicsLayer) rather than via VexFlow's TextDynamics.
 *
 * TextDynamics positions its mark with a `line` option resolving to
 * `stave.getYForLine(line - 3)`, which in principle lands a large `line` (9 ->
 * getYForLine(6)) two stave-spaces below the bottom line.  In VexFlow 5.0 that
 * option does NOT move the mark below the staff -- verified against the built
 * bundle: `line: 9` still rendered `p`/`f` ABOVE the top line, colliding with
 * the tempo / chord-symbol band.  The overlay places them beneath the staff
 * reliably and, taking no beat time, cannot displace the notes the way a
 * parallel TextDynamics voice's padding once had to be engineered around.
 */
/**
 * Friendly barline name -> Barline.type key.
 *
 * "final" is accepted alongside "end" because a spec author reaching for the
 * thin-thick ending barline is far more likely to call it final than to know
 * VexFlow's END.
 */
export const BARLINE_TYPES: Readonly<Record<string, string>> = {
  single: 'SINGLE',
  double: 'DOUBLE',
  end: 'END',
  final: 'END',
  'repeat-begin': 'REPEAT_BEGIN',
  'repeat-end': 'REPEAT_END',
  'repeat-both': 'REPEAT_BOTH',
  none: 'NONE',
};

/**
 * Navigation marks (coda, segno, D.C./D.S., Fine) -> Repetition.type key.
 *
 * The bare names place the symbol at the LEFT of the measure, which is the
 * convention for a target you jump TO; "-right" variants place it at the
 * right, for a point you jump FROM.
 */
export const NAVIGATION_MARKS: Readonly<Record<string, string>> = {
  coda: 'CODA_LEFT',
  'coda-right': 'CODA_RIGHT',
  segno: 'SEGNO_LEFT',
  'segno-right': 'SEGNO_RIGHT',
  'da-capo': 'DC',
  'da-capo-al-coda': 'DC_AL_CODA',
  'da-capo-al-fine': 'DC_AL_FINE',
  'dal-segno': 'DS',
  'dal-segno-al-coda': 'DS_AL_CODA',
  'dal-segno-al-fine': 'DS_AL_FINE',
  fine: 'FINE',
  'to-coda': 'TO_CODA',
};

/**
 * Engraved wording + anchor side for each Repetition.type key, used when a
 * navigation mark has to be HAND-DRAWN in the overlay pass instead of pushed
 * to VexFlow (see drawNavOverflowMarks).
 *
 * Why an overlay is needed at all: VexFlow's `Repetition` glyph is IMMOVABLE.
 * `Repetition.draw()` computes its y as `stave.getYForTopText(numLines) +
 * offset` and its x from the stave's left/right edge, and NEVER references the
 * modifier's own `xShift`/`yShift` (verified against vexflow 5.0.0
 * staverepetition.js -- both are stored in the constructor and then ignored by
 * draw()).  So every LEFT-anchored mark (coda/segno) lands on the same left x
 * and every RIGHT-anchored mark (D.C./D.S./Fine/To/coda-right) lands on the
 * same right x, ALL sharing one y band -- meaning a real jump scheme with two
 * marks on the same side (e.g. a "To Coda" and a "Fine" near the end, or a
 * "D.S. al Coda" beside the closing "Coda") drew them ONE ON TOP OF THE OTHER,
 * illegibly.  There is no VexFlow lever to separate them, so -- exactly as the
 * tempo, dynamics, volta and breath layers do for their own immovable /
 * mispositioned VexFlow primitives -- the SECOND and later marks on a side are
 * drawn by hand, stacked on their own rows.
 *
 * `side` mirrors Repetition.draw()'s own switch: only CODA_LEFT / SEGNO_LEFT
 * anchor left; everything else anchors right.  `text` is the wording VexFlow
 * would have engraved, with the coda glyph rendered as the standard Unicode
 * MUSICAL SYMBOL CODA (U+1D10C, surrogate pair D834 DD0C) / segno (U+1D10B,
 * D834 DD0B) -- the widely-supported code points, the same choice the harp-
 * pedal overlay makes with U+266D/E/F rather than VexFlow's private-use SMuFL
 * code points, which a plain <text> without the Bravura font cannot show.
 */
export const NAV_OVERLAY_LABELS: Readonly<
  Record<string, { text: string; side: 'left' | 'right' }>
> = {
  CODA_LEFT: { text: '\uD834\uDD0C', side: 'left' },
  CODA_RIGHT: { text: '\uD834\uDD0C', side: 'right' },
  SEGNO_LEFT: { text: '\uD834\uDD0B', side: 'left' },
  SEGNO_RIGHT: { text: '\uD834\uDD0B', side: 'right' },
  DC: { text: 'D.C.', side: 'right' },
  DC_AL_CODA: { text: 'D.C. al \uD834\uDD0C', side: 'right' },
  DC_AL_FINE: { text: 'D.C. al Fine', side: 'right' },
  DS: { text: 'D.S.', side: 'right' },
  DS_AL_CODA: { text: 'D.S. al \uD834\uDD0C', side: 'right' },
  DS_AL_FINE: { text: 'D.S. al Fine', side: 'right' },
  FINE: { text: 'Fine', side: 'right' },
  TO_CODA: { text: 'To \uD834\uDD0C', side: 'right' },
};

/** Volta (repeat-ending bracket) placement -> Volta.type key. */
export const VOLTA_TYPES: Readonly<Record<string, string>> = {
  begin: 'BEGIN',
  mid: 'MID',
  end: 'END',
  'begin-end': 'BEGIN_END',
};

export interface MusicTempo {
  /** Text name, e.g. "Allegro".  Renders alone, or before a bpm in parens. */
  name?: string;
  /** Beat unit for the bpm: w, h, q, 8, 16.  Defaults to a quarter ("q")
   *  when a bpm is given without one, so `{bpm: 120}` renders "♩ = 120". */
  duration?: string;
  /** Augmentation dots on the beat unit. */
  dots?: number;
  /**
   * Beats per minute -- a positive number.  A non-finite, zero or negative
   * value is ignored (the mark then shows the `name` alone, never a dangling
   * "♩ ="); an absurdly large value is clamped so the metronome stays on the
   * system.  See sanitizeTempoBpm.
   */
  bpm?: number;
}

export interface MusicVolta {
  /**
   * Bracket placement, which controls the vertical end-hooks:
   *   "begin"      left hook only  (an ending that continues past the frame)
   *   "end"        right hook only
   *   "begin-end"  both hooks      (a fully-enclosed 1st/2nd ending)
   *   "mid"        no hooks        (a middle segment of a wrapped ending)
   * Defaults to "begin".
   */
  type?: 'begin' | 'mid' | 'end' | 'begin-end';
  /** Label inside the bracket, e.g. "1." */
  label?: string;
  /**
   * 1-based inclusive measure range the ending bracket covers, `[from, to]`
   * (a single measure is `[n, n]`).
   *
   * A volta names the measures of ONE repeat ending, not the whole line -- the
   * "1." bracket sits over exactly the bars played the first time through,
   * before the repeat-end barline.  VexFlow's `setVoltaType` is a STAVE
   * modifier that can only span the full stave, i.e. the entire system, so it
   * drew the bracket across every measure regardless of where the ending
   * actually was.  This range is what lets the renderer draw the bracket over
   * just its measures, as published scores do.
   *
   * When omitted the bracket falls back to the measure carrying a `repeat-end`
   * barline (the usual home of a 1st ending), or the last measure if none, so
   * an unanchored volta still lands somewhere plausible rather than over the
   * whole system.
   */
  measures?: [number, number];
}

/**
 * One measure (bar) of a staff.
 *
 * A staff that supplies `measures` instead of a flat `notes` list gets a real
 * barline drawn between each pair, which is what makes a repeat sign mean
 * anything: `beginBar`/`endBar` on the SPEC are the outer barlines of the
 * whole system, so a repeat there encloses the entire (single) measure and has
 * nothing to repeat.  The barline between measures N and N+1 comes from
 * measure N's `endBar`, or failing that measure N+1's `beginBar`, defaulting
 * to a plain single bar.
 */
export interface MusicMeasure {
  notes: MusicNoteSpec[];
  /** Barline closing this measure, a key of BARLINE_TYPES. */
  endBar?: string;
  /** Barline opening this measure; used when the previous has no `endBar`. */
  beginBar?: string;
  /**
   * Meter change taking effect AT this measure, e.g. "3/4".
   *
   * A piece is not fixed to one meter -- a mid-score change (4/4 to 3/4 to
   * 6/8) is ordinary published practice, and the changed signature is engraved
   * at the measure where it begins and then reads as governing every bar until
   * the next change.  The spec's top-level `timeSignature` is a single field
   * and cannot express this, so the meter glyph could only ever be printed
   * once at the opening clef no matter how the beat count changed later.
   *
   * Drawn INSIDE the stave via a 0-tick TimeSigNote tickable (the same
   * mechanism as the mid-stave BarNote), not the stave's own
   * `addTimeSignature`, because the latter only prints the signature at the
   * stave's left edge.  Omit on the first measure -- the opening meter is
   * printed by the stave once at the clef -- and on any measure whose meter is
   * unchanged from the measure before it.
   */
  timeSignature?: string;
  /**
   * Key-signature change taking effect AT this measure, e.g. "D" or "Bb".
   *
   * A piece is not fixed to one key -- a modulation mid-score (C to G to Eb)
   * is ordinary published practice, and the new signature is engraved at the
   * measure where it begins and then reads as governing every bar until the
   * next change.  The spec's top-level `keySignature` is a single field and
   * cannot express this, so the accidentals could only ever be printed once at
   * the opening clef no matter how the tonality shifted later -- AND, worse,
   * every bar's accidentals went on being FILTERED against that one opening key
   * (see buildNoteString), so a note that is bare in the new key still had the
   * old key's accidental suppressed or added.
   *
   * Drawn INSIDE the stave via a 0-tick KeySigNote tickable (the same mechanism
   * as the mid-stave TimeSigNote/BarNote), carrying the PREVIOUS key as a
   * cancel spec so the naturals that void the old sharps/flats print, exactly
   * as an engraved modulation shows them.  The change also re-seeds the
   * accidental filter from this bar on, so -- as always -- spell every note as
   * its true sounding pitch and let the renderer decide which accidentals
   * print.  Omit on the first measure (the opening key is printed by the stave
   * once at the clef) and on any measure whose key is unchanged from the one
   * before it.
   */
  keySignature?: string;
  /**
   * Start a new system (line) at this measure.
   *
   * Without wrapping, a staff's measures all share one System whose width
   * grows linearly and unboundedly -- 12 bars of eighths computed to ~7900px,
   * which scales to under 10% in a reading column and is illegible.  A break
   * here ends the previous system and begins a fresh one, re-printing clef,
   * key and time signature exactly as a printed score does at a line break.
   *
   * Ignored on the first measure, where it would open an empty system.  An
   * automatic break is inserted anyway once a system exceeds its width budget
   * (see `maxSystemWidth`); this field only forces a break somewhere the
   * budget would not have chosen one.
   */
  systemBreak?: boolean;
  /**
   * Mark this measure as a pickup (anacrusis) -- an incomplete opening bar
   * that carries only the upbeat before the first full measure.
   *
   * Only meaningful on the FIRST measure.  A pickup already RENDERS today (a
   * short bar of one or two notes is underfull, and the SOFT-mode voice draws
   * an underfull bar without complaint), so this flag changes not the notes
   * but the MEASURE COUNT: published scores do NOT count the anacrusis, they
   * leave it unnumbered and call the first FULL bar "measure 1".  With
   * `measureNumbers` on, the pickup bar is therefore skipped and the running
   * count starts at the second bar, so the numbers read 1, 2, 3, ... over the
   * complete bars rather than mislabelling the upbeat as bar 1.  Ignored when
   * `measureNumbers` is not set (there are no numbers to shift).
   */
  pickup?: boolean;
  /**
   * Draw this measure as a MULTI-MEASURE REST consolidating `multiRest` empty
   * bars -- the thick horizontal H-bar with the bar count above it that
   * published instrumental PARTS use so a player counts "8 bars rest" from one
   * symbol instead of reading eight identical empty bars.  A named
   * rest-placement gap (a part without it is unreadable at the desk), so the
   * count is the whole point: `2` reads as two bars, `16` as sixteen.
   *
   * The measure carries NO `notes` (any given are ignored, with a warning) --
   * it is by definition silent.  Internally the bar is filled with a 0-drawn
   * GhostNote spacer so it claims layout width like any other measure, and the
   * H-bar + count are hand-drawn as a d3 overlay (see drawMultiMeasureRest)
   * reading the spacer's resolved x -- exactly like the pedal / volta layers,
   * because VexFlow's own MultiMeasureRest is a stave-attached modifier that
   * does not fit the System/voice pipeline this renderer formats through.  A
   * value below 1 (or non-integer) is not a rest count and is skipped with a
   * console warning, matching the plugin's unknown-value convention.
   */
  multiRest?: number;
  /**
   * Alias for `multiRest`.  The skill prompt documents this feature under BOTH
   * names, so the field is accepted under either spelling (see multiRestOf);
   * `multiRest` is canonical and wins when both are given.
   */
  multiMeasureRest?: number;
  /**
   * Independent simultaneous voices for THIS bar -- the MEASURE-MAJOR spelling
   * of a multi-voice staff, the mirror of the VOICE-MAJOR `staffSpec.voices`.
   *
   * The skill prompt documents both axes as equivalent: voice-major
   * (`voices: [{stemDirection, measures:[...]}]`, one list per line) and
   * measure-major (`measures: [{voices:[...], endBar, ...}]`, one list per
   * BAR, each bar carrying its own voices with the bar-level fields on the
   * measure).  Only voice-major was ever implemented, so a measure-major spec
   * tested EMPTY at every recognition gate (measureHasContent looked only at
   * `notes`/`multiRest`) and was rejected as non-music -> "No compatible
   * plugin" -> ~30s hang / total data loss.  normalizeMeasureMajorVoices
   * transposes this back to the voice-major shape the render core consumes, so
   * the two spellings render identically.  Bar-level fields (`endBar`,
   * `timeSignature`, `systemBreak`, `multiRest`) stay on the measure and apply
   * to every voice in it.
   */
  voices?: MusicVoice[];
}

/**
 * One independent rhythmic voice within a single staff.
 *
 * A staff is not limited to one melodic line: keyboard, choral (SATB reduced
 * to two staves) and much contrapuntal writing put two or more simultaneous
 * lines on ONE staff, each with its own rhythm and, crucially, its own stem
 * direction -- soprano stems up, alto stems down -- so the reader can follow
 * each line independently even where they share noteheads.  A chord (stacked
 * `keys` on one note) cannot express this: it forces a single shared stem and
 * a single shared rhythm, so an eighth-note upper line against a quarter-note
 * lower line is simply unwritable as a chord.
 *
 * VexFlow models this as multiple `Voice`s formatted together on one stave --
 * the very mechanism the dynamics underlay already uses -- so a `voices` list
 * builds one VexFlow voice per entry.  The FIRST voice is the primary line and
 * takes the full modifier path (and is what the staff's spans/beams/tuplets
 * address); the rest ride alongside with their stems forced so the lines stay
 * visually separated as published scores set them.
 */
export interface MusicVoice {
  /**
   * Force every stem in this voice up or down.  Independent voices are read
   * apart chiefly by stem direction (upper voice up, lower voice down), so a
   * multi-voice staff without forced stems is ambiguous.  Omitted on the
   * primary voice, VexFlow's automatic by-staff-position choice applies.
   */
  stemDirection?: 'up' | 'down';
  /** Flat single-measure note list.  Ignored when `measures` is present. */
  notes?: MusicNoteSpec[];
  /** Multi-measure content; measures align by index with the other voices. */
  measures?: MusicMeasure[];
}

/**
 * One staff of a multi-staff system.  Carries everything that is per-staff;
 * meter, tempo and the navigation marks stay on the parent spec because they
 * apply to the system as a whole.
 */
export interface MusicStaff {
  clef?: MusicClef;
  /**
   * Instrument / part name printed to the LEFT of this staff, e.g. "Violin",
   * "Piano", "Vln. I".  Without it a multi-staff system is ambiguous -- three
   * treble staves give no clue which line is which part -- and "clear staff
   * labels" is a baseline expectation of published ensemble scores.
   *
   * Drawn as a left-gutter overlay rather than via VexFlow's StaveText because
   * StaveText anchors INSIDE the staff and reserves no horizontal room, so it
   * collides with the clef; the overlay approach (like the title/lyric/harp
   * layers) reserves a gutter and centres the label vertically on the staff.
   */
  name?: string;
  /**
   * Abbreviated part name for the SECOND and later systems of a wrapped score,
   * e.g. "Fl." where `name` is "Flute".  Published scores name each part in
   * full beside the first system and abbreviate thereafter; without this the
   * continuation systems are left unlabelled rather than repeating the full
   * name down the margin.
   */
  shortName?: string;
  keySignature?: string;
  /** Flat single-measure note list.  Ignored when `measures` is present. */
  notes?: MusicNoteSpec[];
  /** Multi-measure content, with barlines drawn between measures. */
  measures?: MusicMeasure[];
  slurs?: MusicSpan[];
  ties?: MusicSpan[];
  glissandos?: MusicGlissando[];
  hairpins?: MusicHairpin[];
  brackets?: MusicBracket[];
  trillLines?: MusicTrillLine[];
  /** Sustain-pedal markings drawn below this staff. */
  pedals?: MusicPedal[];
  /**
   * Beam this staff's eighths-and-shorter automatically, overriding the
   * spec-level `autoBeam` for this staff only.  `false` leaves the staff
   * flagged while the rest of the system beams.
   *
   * Declared because a `staves[]` entry already carries its own `clef`,
   * `notes`, `slurs`, `beams` and `tuplets`, so an author reasonably expects
   * beaming to be settable here too -- and beaming genuinely is a per-part
   * decision (a flowing harp figure wants beams while a wind part's isolated
   * notes want flags).  Before this it was silently ignored: the flag was read
   * only from the spec root, so a multi-staff score that set it here drew every
   * sixteenth with an individual flag and gave no indication why.
   */
  autoBeam?: boolean;
  /** Beat groups for this staff's `autoBeam`; falls back to the spec's. */
  beamGroups?: Array<[number, number]>;
  /** Explicit beam groups.  Overrides nothing; adds to any autoBeam output. */
  beams?: MusicBeam[];
  /** Tuplets (triplets etc.) over runs of this staff's notes. */
  tuplets?: MusicTuplet[];
  /**
   * Independent simultaneous voices sharing this one staff (keyboard, SATB,
   * counterpoint).  When present, this staff's own `notes`/`measures` are
   * ignored in favour of `voices[0]`, and the remaining entries are drawn
   * alongside it with forced stems.  The staff's spans/beams/tuplets address
   * the FIRST voice, since a span between two independent voices is not a
   * primitive VexFlow can draw.
   */
  voices?: MusicVoice[];
}

export interface MusicNoteSpec {
  /**
   * One entry per note in a chord, e.g. ["c/5"] or ["c/5", "e/5", "g/5"].
   * Omitted (or ignored) when `rest` is true.
   */
  keys?: string[];
  /** VexFlow duration code: w, h, q, 8, 16 (+ "." for dotted) */
  duration: string;
  /**
   * Draw a rest of `duration` instead of a note.  `keys` is not needed: the
   * renderer picks the pitch that centres the rest on the staff for the
   * active clef, since a rest's pitch is what positions it vertically.
   */
  rest?: boolean;
  annotations?: MusicAnnotation[];
  /** LilyPond-style harp pedal diagram string, e.g. "^v-|vv-^" */
  harpPedal?: string;
  /**
   * Chord label.  A string is shorthand for {text}; the object form adds
   * engraved glyphs, super/subscripts, and below-staff placement for
   * roman-numeral analysis.
   */
  chordSymbol?: string | MusicChordSymbol;
  /** Articulation names, keys of ARTICULATION_CODES. */
  articulations?: string[];
  /** Ornament names, keys of ORNAMENT_CODES. */
  ornaments?: string[];
  /**
   * Chord roll (arpeggio) drawn to the LEFT of a chord, meaning "spread the
   * notes rather than strike them together".  A friendly name from
   * ARPEGGIO_STROKE_TYPES: "arpeggio" (the plain directionless roll), or
   * "arpeggio-up"/"arpeggio-down" to add the arrowhead published scores use;
   * the guitar strokes "brush-up"/"brush-down"/"rasgueado-up"/"rasgueado-down"
   * are also accepted.  `true` is shorthand for "arpeggio".  Meaningful only on
   * a note whose `keys` name two or more pitches (the stroke spans the chord);
   * an unknown name is skipped with a console warning.
   */
  arpeggio?: string | boolean;
  /**
   * Single-note tremolo: the number of slashes drawn through the stem, one
   * (1) through three (3), meaning the note is rapidly repeated.  A repeated-
   * note tremolo is engraved as beam-slashes across the stem, not as a
   * separate glyph, which is why it is a stroke COUNT rather than a name in a
   * lookup table -- there is no "tremolo symbol", only "n slashes".  A value
   * outside 1..3 is not real notation and is skipped with a console warning,
   * following the plugin's unknown-name convention.
   */
  tremolo?: number;
  /** Dynamic mark placed below this note; must be in DYNAMIC_MARKS. */
  dynamic?: string;
  /** Left-hand fingering digit; a bare value defaults to below the staff. */
  fingering?: string | number | MusicFingering;
  /** String number for bowed/plucked instruments, drawn above the staff. */
  stringNumber?: string | number;
  /**
   * Sung syllable under this note.  A string is shorthand for {text}; the
   * object form adds verse stacking, word hyphens and melisma extenders.
   */
  lyric?: string | MusicLyric;
  /**
   * Grace notes played before this note (appoggiatura / acciaccatura / run).
   * They attach as a GraceNoteGroup modifier and take no beat time, so adding
   * them does not shift where any main note falls.
   */
  graceNotes?: MusicGraceNote[];
  /**
   * A phrasing break drawn ABOVE the staff, just AFTER this note -- where a
   * wind or vocal player breathes, or (a caesura) the "railroad-tracks" grand
   * pause.  A friendly name from BREATH_MARKS: "comma" (the ordinary breath, a
   * small raised comma), "tick" (a terser slanted stroke), or "caesura" (the
   * two parallel diagonal strokes of a grand pause -- also "grand-pause" /
   * "railroad"), or "caesura-curved" (that same grand pause drawn with two
   * BOWED strokes, the alternate published caesura glyph).  `true` is shorthand
   * for "comma"; a number 0.3..1 sets an
   * explicit scale for the comma.  An unknown name is skipped with a console
   * warning, matching the plugin's unknown-name convention.
   *
   * Drawn as a d3 overlay (see drawBreathMarks) rather than a VexFlow modifier
   * for the same reason as the trill glyph and dynamics: VexFlow ships no
   * breath-mark primitive that engraves after the note, so like those layers
   * it is hand-drawn after formatting, reading the note's resolved x.
   */
  breath?: string | boolean | number;
  /**
   * Engrave this note SMALL -- a CUE note, the roughly two-thirds-size note
   * published scores use for an editorial suggestion, a colla-parte lead-in or
   * an ossia alternative.  `true` is the 2/3 default; a number 0.3..1 sets an
   * explicit scale.
   *
   * Unlike a grace note a cue note KEEPS its beat time and occupies real
   * rhythmic space in the bar -- only its SIZE changes -- which is exactly why
   * it is applied as a post-format visual scale (see drawCueNotes) rather than
   * built as a different note: VexFlow lays the note out full size (spacing,
   * stem direction, accidentals all the normal ones) and the rendered glyph
   * group is then shrunk in place around its notehead, so notehead, stem and
   * flag shrink together and stay internally consistent.  VexFlow ships no
   * "cue note" primitive (GraceNote is close but drops beat time) and the
   * per-element fontScale does not cascade from a StaveNote to its child
   * noteheads/stem, so -- like the dynamics / trill / breath layers -- the
   * effect lives in the overlay pass.
   */
  cue?: boolean | number;
}

export interface MusicSpec {
  type: 'music';
  clef?: MusicClef;
  /**
   * Instrument / part name printed to the left of the (single) staff.  For a
   * multi-staff system put the name on each `staves[]` entry instead; this
   * top-level field is the single-staff shorthand, mirroring how `clef`/
   * `keySignature` fall through to the synthesized lone staff.
   */
  name?: string;
  keySignature?: string;
  timeSignature?: string;
  /** Single-measure note list.  Ignored when `measures` or `staves` is set. */
  notes?: MusicNoteSpec[];
  /**
   * Multi-measure content for a single staff, with real barlines between
   * measures.  Span indices (slurs, ties, brackets) count notes across the
   * whole staff, ignoring the measure divisions.
   */
  measures?: MusicMeasure[];
  width?: number;
  height?: number;
  /**
   * Width budget in px for ONE system before an automatic break is inserted.
   * Defaults to DEFAULT_MAX_SYSTEM_WIDTH (1200), a width that stays legible
   * when scaled into a typical reading column.
   *
   * Set it larger to fit more bars per line (at smaller scale), or pass
   * `width` to pin the canvas and opt out of automatic wrapping entirely --
   * an explicit `width` is taken as the author having chosen the layout.
   */
  maxSystemWidth?: number;
  /** Vertical gap in px between stacked systems.  Defaults to 36. */
  systemSpacing?: number;
  /** Phrase curves drawn over the notes. */
  slurs?: MusicSpan[];
  /** Ties joining two notes of the same pitch. */
  ties?: MusicSpan[];
  /** Pitch slides.  VexFlow has no glissando primitive; a labelled
   *  StaveLine between the two noteheads is the standard substitute. */
  glissandos?: MusicGlissando[];
  /** Crescendo / diminuendo wedges drawn below the staff. */
  hairpins?: MusicHairpin[];
  /** Placement brackets (8va, 8vb, rit.) spanning a run of notes. */
  brackets?: MusicBracket[];
  /** Extended trill / vibrato lines spanning a run of notes. */
  trillLines?: MusicTrillLine[];
  /** Sustain-pedal markings drawn below the staff (piano). */
  pedals?: MusicPedal[];
  /**
   * Beam eighths and shorter automatically, per measure, using the meter's
   * natural beat grouping.  Without this every eighth and sixteenth draws an
   * individual flag, which is correct only for isolated notes.
   */
  autoBeam?: boolean;
  /**
   * Beat groups for `autoBeam`, as [numerator, denominator] pairs -- e.g.
   * [[3, 8]] to beam 6/8 in two groups of three.  Omit for the meter's
   * default grouping.
   */
  beamGroups?: Array<[number, number]>;
  /**
   * Explicit beams for a single-staff spec, by note index.  Additive with
   * `autoBeam`, so use one or the other for a given run of notes.
   */
  beams?: MusicBeam[];
  /**
   * Beams that thread across the staves of a grand staff -- a keyboard run
   * flowing from one hand's staff into the other under ONE continuous beam.
   * A per-staff `beams` / `autoBeam` cannot join it (each addresses a single
   * staff's own notes), so members are named here as [staffIndex, noteIndex]
   * pairs in playing order; see MusicCrossStaffBeam.  Only meaningful with
   * `staves`.
   */
  crossStaffBeams?: MusicCrossStaffBeam[];
  /**
   * Slurs / ties that arc across the staves of a grand staff -- a phrase, or a
   * single held pitch, passed from one hand's staff into the other.  A
   * per-staff `slurs` / `ties` cannot join it (each addresses a single staff's
   * own notes), so each entry's `from`/`to` are [staffIndex, noteIndex] pairs;
   * see MusicCrossStaffSlur.  Only meaningful with `staves`.
   */
  crossStaffSlurs?: MusicCrossStaffSlur[];
  /**
   * Tuplets (triplets, quintuplets, ...) for a single-staff spec, by note
   * index.  Rescales the spanned notes' beat time and draws the number
   * bracket; see MusicTuplet.
   */
  tuplets?: MusicTuplet[];
  /**
   * Independent simultaneous voices on a SINGLE staff (the single-staff
   * shorthand, mirroring how `notes`/`clef` fall through to a lone staff).
   * When present, the top-level `notes`/`measures` are ignored in favour of
   * `voices[0]`, and the rest are drawn alongside with forced stems.  For a
   * multi-staff system put `voices` on each `staves[]` entry instead.
   */
  voices?: MusicVoice[];
  /**
   * Grand staff / multi-staff system.  When present, `notes` and the
   * per-staff span lists above are ignored in favour of these, and a brace
   * plus left/right connectors join the staves.
   */
  staves?: MusicStaff[];
  /** Tempo marking above the top staff. */
  tempo?: MusicTempo;
  /**
   * A single navigation mark, a key of NAVIGATION_MARKS.  A real jump scheme
   * needs TWO or more (a segno at the target AND a "D.S. al Coda" at the
   * source, or a "To Coda" and a "Coda"), so `mark` alone cannot express one --
   * use `marks` for that.  Kept for the single-mark shorthand and backward
   * compatibility; when both are given, `marks` wins.
   */
  mark?: string;
  /**
   * Navigation marks, one per symbol -- this is what a full D.S./D.C.-al-Coda
   * scheme requires: e.g. `["segno", "to-coda", "dal-segno-al-coda", "coda"]`
   * so the reader is told where to jump FROM and where the target IS.  A single
   * `mark` field could only ever place one of them, leaving the rest of the
   * scheme unmarked.  VexFlow's `setRepetitionType` PUSHES each mark as its own
   * stave modifier (verified: repeated calls stack rather than replace), so a
   * list simply adds each one; the `-left`/`-right` suffix on a name still
   * chooses which end of the measure it anchors to.  Each entry is looked up in
   * NAVIGATION_MARKS and an unknown name is skipped with a warning, exactly as
   * the single `mark` path does.  When both `marks` and `mark` are given,
   * `marks` wins.
   */
  marks?: string[];
  /**
   * Repeat-ending bracket above the top staff.  A single volta only expresses
   * ONE ending, so it cannot draw the ordinary 1st-and-2nd-ending pair a
   * repeat needs -- use `voltas` for that.  Kept for the single-ending
   * shorthand and for backward compatibility.
   */
  volta?: MusicVolta;
  /**
   * Repeat-ending brackets, one per ending -- this is what a real repeat
   * scheme needs: a "1." bracket over the bars played the first time through
   * and a "2." bracket over the alternate ending after the repeat-end barline.
   * A single `volta` field could only ever name one of the two, leaving the
   * other unmarked; a list draws every ending in its own place.  Each entry
   * carries its own `measures` range (strongly recommended once there is more
   * than one, since the unanchored fallback would stack them on the same bar).
   * When both `voltas` and `volta` are given, `voltas` wins.
   */
  voltas?: MusicVolta[];
  /** Opening barline, a key of BARLINE_TYPES. */
  beginBar?: string;
  /** Closing barline, a key of BARLINE_TYPES. */
  endBar?: string;
  /**
   * Measure number of the OPENING bar.  Drawn above the first system's top
   * staff on its own; with `measureNumbers` it becomes the starting count the
   * per-system numbering runs from (so a movement that begins at bar 47 sets
   * `measureNumber: 47`).  Defaults to 1 for the running count.
   */
  measureNumber?: number;
  /**
   * Number the FIRST measure of EVERY system, as published scores do so a
   * reader can locate any bar at a glance.
   *
   * `measureNumber` alone labels only the opening bar; on a score that wraps to
   * several systems that leaves every continuation line unnumbered, which is
   * why a wrapped score needs this.  The running count starts at
   * `measureNumber` (default 1) and advances by each system's opening-bar
   * index, so line 2 beginning at the 4th bar shows "4".  Drawn on the top
   * staff of each system only -- a measure number repeated down every staff of
   * a grand staff is wrong engraving.
   */
  measureNumbers?: boolean;
  /**
   * The opening bar is a pickup (anacrusis).  Top-level shorthand, mirroring
   * how `clef`/`keySignature` fall through to the lone staff, and equivalent
   * to setting `pickup: true` on `measures[0]` -- either spelling is honoured.
   *
   * The short upbeat is written as the first measure and engraves as-is (an
   * underfull opening bar already draws correctly); the flag only changes the
   * MEASURE COUNT so that, with `measureNumbers` on, the anacrusis is left
   * unnumbered and the first FULL bar becomes "measure 1", per published
   * convention.  No effect without `measureNumbers`.
   */
  pickup?: boolean;
  /**
   * Add courtesy (cautionary) accidentals -- the parenthesised reminder
   * published editions print when a pitch altered in one bar returns in the
   * NEXT bar sounding differently.  An `f#/4` in bar 1 followed by a plain
   * `f/4` in bar 2 gets a parenthesised natural on that `f`, reassuring the
   * reader the sharp no longer applies across the barline.
   *
   * The mark is added only to a note that would otherwise print BARE (a note
   * carrying its own accidental is already its own reminder) and only for a
   * one-bar-back change, matching the Dorico/Finale house style.  Off by
   * default for a clean, minimally-marked score; the no-flag path is
   * byte-identical.  As always, spell every note as its true sounding pitch --
   * the renderer decides where a courtesy mark is warranted (see
   * planCautionaryAccidentals).
   */
  cautionaryAccidentals?: boolean;
  /** Rehearsal-mark style section label, e.g. "A". */
  section?: string;
  /**
   * Work title, centred above the system in large type -- the headline of a
   * published score's title block.  Drawn as an overlay because VexFlow has no
   * title primitive (StaveText is a per-stave label, not a page heading).
   */
  title?: string;
  /** Secondary title line, centred and smaller beneath `title`. */
  subtitle?: string;
  /**
   * Composer credit, conventionally right-aligned beneath the title block.
   * A lyricist, when present, mirrors it on the left.
   */
  composer?: string;
  /** Lyricist / author credit, conventionally left-aligned beneath the title. */
  lyricist?: string;
}

/**
 * Convert a spec key to EasyScore pitch syntax.
 *
 * The spec uses VexFlow's *StaveNote* key form (`"c/5"`, `"c#/5"`), but
 * EasyScore's grammar wants `pitch[accidental]octave` with no slash (`C5`,
 * `C#5`) and then appends `/duration`.  Concatenating the raw key with
 * `/duration` yields `"c/5/q"`, which EasyScore does not match -- and it
 * reports that by returning an EMPTY array rather than raising, so the
 * failure surfaced later and misleadingly as "Voice does not have enough
 * notes".  Verified against vexflow 5.0.0: `notes("c/5/q")` -> 0 notes,
 * `notes("C5/q")` -> 1 note.
 *
 * The accidental class includes `n` (explicit natural) because the key-
 * signature filter emits it to cancel a signature accidental (rule 4, see
 * musicAccidentals.ts).  Without `n` the pattern failed to match, `en/5` fell
 * through to the passthrough branch and kept its lowercase letter -- emitting
 * `en5` where EasyScore's grammar wants `En5`.  Sharps and flats were
 * unaffected, so this surfaced only on the natural path: measured on an
 * Eb-major bass staff whose spec asked for a plain `e/5`.
 */
/**
 * Musical octave bounds.  A key's octave feeds VexFlow's ledger-line loop:
 * StaveNote.drawLedgerLines iterates ONE stave-line at a time from the staff
 * out to the notehead (`for (line = 6; line <= highestLine; ++line)` and its
 * downward twin), and highestLine/lowestLine are derived straight from the
 * note's octave.  An out-of-range octave -- `c/999` -- therefore drives that
 * loop through hundreds of thousands of iterations and HANGS the render with a
 * blank canvas, exactly as an unbounded duration or tempo-dots count does (see
 * sanitizeDuration and the D25 tempo-dots clamp).  MIDI spans C-1..G9, so this
 * range admits every real pitch while capping ledger lines at a few dozen.
 */
export const MIN_OCTAVE = -1;
export const MAX_OCTAVE = 9;

/**
 * Clamp a key's octave into [MIN_OCTAVE, MAX_OCTAVE], returning the (possibly
 * rewritten) key.
 *
 * This is the single choke point that neutralises the ledger-line hang before
 * a key reaches VexFlow -- the pitch analogue of sanitizeDuration for the
 * duration path.  It accepts BOTH note grammars the two converters use -- the
 * StaveNote slash form ("c/5", "c#/5") and the EasyScore slash-less form ("C5",
 * "C#5") -- and preserves the input's own shape, so it can sit inside both
 * toEasyScoreKey and toStaveNoteKey without changing which form each emits.  A
 * key it cannot parse (or whose octave is already in range) is returned
 * untouched, so the valid path is byte-identical; only a genuinely
 * out-of-range octave is rewritten, with a console warning matching the
 * plugin's unknown-value convention.
 */
export function clampKeyOctave(key: string): string {
  const raw = String(key).trim();
  // group 1: letter + optional accidental; group 2: optional slash; group 3:
  // signed octave.  The accidental class mirrors the converters' own: an
  // explicit natural `n`, or up to two sharps/flats.
  const m = /^([a-gA-G](?:n|[#b]{0,2}))(\/?)(-?\d+)$/.exec(raw);
  if (!m) return raw;
  const oct = Number(m[3]);
  if (!Number.isFinite(oct)) return raw;
  const clamped = Math.max(MIN_OCTAVE, Math.min(MAX_OCTAVE, oct));
  if (clamped === oct) return raw;
  console.warn(
    `musicPlugin: octave ${oct} in key "${raw}" is outside [${MIN_OCTAVE}, `
    + `${MAX_OCTAVE}]; clamped to ${clamped} to avoid a ledger-line render hang`,
  );
  return `${m[1]}${m[2]}${clamped}`;
}

/**
 * Reject a pitch key whose ACCIDENTAL (or overall shape) is unspellable,
 * returning the key unchanged when renderable or `null` when it is not.
 *
 * clampKeyOctave already neutralises an out-of-range OCTAVE, but nothing
 * guarded the accidental letter -- and a typo there is the ledger-line hang's
 * sibling in the note-string path.  A pitch such as "ef/5" (the flat
 * mis-spelled `f` instead of `b`, i.e. an intended "eb/5") matches neither
 * clampKeyOctave's nor filterPitch's grammar, so it falls through unchanged;
 * toEasyScoreKey then strips the stray slash to "ef5", EasyScore cannot parse
 * the bogus accidental into a pitch/line, and the note is built with a NaN
 * position -- freezing the Formatter's justification loop for the full ~30s
 * render timeout with a blank canvas and no error, losing the WHOLE score for
 * one mistyped accidental (the same failure mode the empty-keys guard in
 * buildNoteString already prevents).
 *
 * A renderable pitch is letter [a-g] + optional accidental (n, #, ##, b, bb) +
 * optional slash + signed octave -- VexFlow's own pitch grammar.  A valid key
 * is returned UNTOUCHED, so the valid path is byte-identical; an unrenderable
 * one is returned as `null` with a console warning, matching the plugin's
 * unknown-value convention.  The caller drops a null key from a CHORD (keeping
 * its still-valid keys, so one mistyped note does not discard a whole valid
 * chord); but a note whose keys are ALL null is dropped from the emitted note
 * string entirely, which trips buildNoteString's caller (renderMusicSpec) into
 * its descriptive "Could not parse ... in measure N" error.  It is deliberately
 * NOT turned into a rest: a silent rest would hide the typo on an otherwise-
 * valid score, whereas VexFlow already fails a cleanly-unparseable key such as
 * "not-a-pitch" the same honest way (and the committed suites assert it must).
 *
 * Exported pure/DOM-free for regression testing.
 */
export function sanitizePitch(key: string): string | null {
  const raw = String(key).trim();
  if (/^[a-gA-G](?:n|#{1,2}|b{1,2})?\/?-?\d+$/.test(raw)) return raw;
  console.warn(
    `musicPlugin: unrenderable pitch "${raw}" (bad accidental or format); `
    + `dropped to avoid a Formatter render hang. Spell accidentals as `
    + `#, ##, b, bb or n -- e.g. "eb/5" for E-flat.`,
  );
  return null;
}

export function toEasyScoreKey(key: string): string {
  // Clamp the octave FIRST so an extreme value (`c/999`) cannot reach VexFlow
  // and hang the ledger-line loop.  clampKeyOctave preserves the slash form
  // this function receives from filterPitch, so the match below is unaffected.
  const safe = clampKeyOctave(key);
  const match = /^([a-gA-G])(n|[#b]{1,2})?\/(-?\d+)$/.exec(String(safe).trim());
  if (match) return match[1].toUpperCase() + (match[2] ?? '') + match[3];
  // Already in EasyScore form (or unrecognised): pass through with any stray
  // slash removed so a partially-correct key still parses.
  return String(safe).trim().replace('/', '');
}

/**
 * Normalise a spec key to VexFlow's *StaveNote* `pitch/octave` form (`"c/5"`,
 * `"c#/5"`).
 *
 * This is the INVERSE of toEasyScoreKey and exists because the two note
 * factories want opposite grammars.  Notes that ride the EasyScore string go
 * through toEasyScoreKey (slash removed).  Notes built by hand through a
 * StaveNote constructor -- grace notes are the case -- want the slash form,
 * because `new GraceNote({keys})` is StaveNote's own constructor and parses
 * its keys with the same `note/octave` grammar EasyScore explicitly rejects.
 *
 * Feeding a grace note the EasyScore key ("B4" for "b/4") was the bug behind
 * the 30s render hang: StaveNote cannot parse "B4" into a pitch/line, so the
 * grace note is built with a degenerate (NaN) y-position, and GraceNoteGroup's
 * pre-format loop -- which iterates until every grace note is positioned --
 * never converges, freezing the formatter with a blank canvas and no error.
 * A grace note therefore needs the raw slash key, not the EasyScore one.
 *
 * A key already in slash form passes through unchanged; a lone "C5" (no slash)
 * is repaired to "C/5" so a partially-correct key still renders rather than
 * hanging.
 *
 * The accidental class admits an explicit natural `n` ("cn/5" / "cn5") for the
 * same reason toEasyScoreKey (its inverse) does: a natural is a real accidental
 * an author can write on a note, grace notes bypass the key-signature filter
 * (so the sign is never dropped for them), and -- crucially -- a SLASHLESS
 * natural was the one form both branches missed.  Without `n` here, "cn5" fell
 * through to the raw return, and because it is not slash form `new GraceNote`
 * could not parse it into a pitch/line -- re-triggering the very
 * non-converging GraceNoteGroup format loop (30s hang, blank canvas) this
 * function was written to avoid.  Sharps and flats can double ([#b]{0,2}); a
 * natural is only ever single, so it is a separate alternative rather than a
 * repeat count.
 */
export function toStaveNoteKey(key: string): string {
  // Clamp the octave first (same ledger-line-hang guard as toEasyScoreKey);
  // clampKeyOctave preserves whichever grammar `key` arrives in, so the two
  // form checks below still classify it correctly.
  const raw = clampKeyOctave(String(key).trim());
  // Already slash form (letter[accidental]/octave): keep as-is.
  if (/^[a-gA-G](?:n|[#b]{0,2})\/-?\d+$/.test(raw)) return raw;
  // EasyScore form (letter[accidental]octave, no slash): insert the slash.
  const match = /^([a-gA-G])(n|[#b]{0,2})(-?\d+)$/.exec(raw);
  if (match) return `${match[1]}${match[2]}/${match[3]}`;
  return raw;
}

/**
 * Pitch that centres a rest on the staff, per clef.
 *
 * A rest in EasyScore still carries a pitch, and that pitch is what positions
 * it vertically -- it is not ignored.  Measured on a treble staff spanning
 * y=50..90: B4 lands at y=70 (middle line, correct), F5 at y=50 (riding the
 * top line), and a bass staff given B4 puts the rest at y=10, floating clear
 * above the staff.  Choosing per clef is therefore required for a rest to
 * look like a rest rather than an accident.
 */
const REST_PITCH_FOR_CLEF: Readonly<Record<string, string>> = {
  treble: 'B4',
  bass: 'D3',
  alto: 'C4',
  tenor: 'A3',
  percussion: 'B4',
};

/**
 * Rest pitch for a rest that shares a staff with ANOTHER independent voice.
 *
 * On a single-voice staff a rest is centred (REST_PITCH_FOR_CLEF), but on a
 * multi-voice staff that centring is WRONG: two voices resting on the same
 * beat both land on the middle line and OVERPRINT into a single glyph (a
 * two-voice bar of simultaneous quarter rests drew ONE rest -- verified), and
 * even a lone rest on the centre line no longer reads as belonging to the
 * upper OR the lower voice.  Published two-voice engraving RAISES the upper
 * voice's rests and LOWERS the lower voice's so each reads with its own line
 * and simultaneous rests never collide.  A rest still carries a pitch that
 * positions it (see REST_PITCH_FOR_CLEF), so the offset is expressed as an
 * `upper`/`lower` pitch a third either side of the clef's centre -- one stave
 * line up / down, which separates the two voices while keeping both rests on
 * the staff.  Only consulted when a staff declares more than one voice, via
 * buildNoteString's `restPitchOverride`, so every single-voice render is
 * byte-identical.
 */
const REST_PITCH_MULTIVOICE: Readonly<Record<string, { upper: string; lower: string }>> = {
  treble: { upper: 'D5', lower: 'G4' },
  bass: { upper: 'F3', lower: 'B2' },
  alto: { upper: 'E4', lower: 'A3' },
  tenor: { upper: 'C4', lower: 'F3' },
  percussion: { upper: 'D5', lower: 'G4' },
};

/**
 * The raised (upper-voice) / lowered (lower-voice) rest pitch for a clef, or
 * `undefined` for an unknown clef so the caller falls back to centring.
 */
export function multiVoiceRestPitch(
  clef: string,
  which: 'upper' | 'lower',
): string | undefined {
  return REST_PITCH_MULTIVOICE[clef]?.[which];
}

/**
 * Base duration codes VexFlow's EasyScore grammar actually understands.
 *
 * A closed set for the same reason as ARTICULATION_CODES: an out-of-range or
 * unrecognised duration is NOT rejected downstream -- EasyScore happily builds
 * a note with a degenerate (0 / NaN) tick value, and VexFlow's Formatter then
 * spins in its justification loop and NEVER RETURNS, hanging the entire render
 * with no error and a blank canvas.  Verified against vexflow 5.0.0: a single
 * note with duration "999" or 1000000000 -> 30s render timeout, total data
 * loss.  Only a closed set can catch that before it reaches the formatter.
 *
 * "w/h/q" are the letter codes; the power-of-two numbers are both the eighth/
 * sixteenth/... codes AND the numeric aliases VexFlow accepts for whole/half/
 * quarter ("1"=w, "2"=h, "4"=q), so one set covers letters and numbers alike.
 * 32nd/64th/128th are included because VexFlow supports them even though the
 * skill prompt only advertises w h q 8 16 -- accepting a real code we do not
 * document is harmless, silently hanging on it is not.
 */
export const VALID_DURATION_BASES: ReadonlySet<string> = new Set([
  'w', 'h', 'q', '8', '16', '32', '64', '128',
  '1', '2', '4',
]);

/** Beyond quadruple-dotted is not real notation; cap runaway dot strings. */
const MAX_DURATION_DOTS = 4;

/**
 * Split a spec duration into a GUARANTEED-VALID base code plus a dot count.
 *
 * This is the single choke point every duration passes through, so an
 * unrenderable duration is neutralised once, here, rather than hanging the
 * formatter later.  An unknown base falls back to a quarter (the neutral
 * default) with a warning -- following the plugin's "unknown names skipped
 * with a console warning rather than guessed at" convention -- because the
 * alternative (letting it through) is not a wrong note but a frozen score.
 */
export function sanitizeDuration(
  duration: string | number,
): { base: string; dots: number } {
  const raw = String(duration).trim();
  const parsed = /^([a-z0-9/]+?)(\.*)$/i.exec(raw);
  const base = (parsed ? parsed[1] : raw).toLowerCase();
  const dots = Math.min(parsed ? parsed[2].length : 0, MAX_DURATION_DOTS);
  if (!VALID_DURATION_BASES.has(base)) {
    console.warn(
      `musicPlugin: unknown duration "${raw}", falling back to quarter ("q")`,
    );
    return { base: 'q', dots };
  }
  return { base, dots };
}

/**
 * Split a spec duration such as "h." into the noteStruct form VexFlow's
 * Note constructor accepts.
 *
 * EasyScore's *string* grammar takes a trailing "." for an augmentation dot,
 * but Note.parseDuration -- which every noteStruct-based note (GhostNote,
 * TextDynamics) goes through -- matches /(\d*\/?\d+|[a-z])(d*)([nrhms]|$)/,
 * where dots are "d" characters and a trailing "." matches nothing.  Passing
 * "h." there throws "BadArguments: Invalid note initialization object", so
 * the dots must be lifted out into the separate `dots` field.  Routing
 * through sanitizeDuration additionally guards the dynamics/ghost-note path
 * against the same degenerate-duration hang buildNoteString guards for notes.
 */
export function toNoteStructDuration(
  duration: string,
): { duration: string; dots: number } {
  const { base, dots } = sanitizeDuration(duration);
  return { duration: base, dots };
}

/**
 * Validate a time-signature string BEFORE it reaches a VexFlow display API,
 * returning a safe meter or `undefined`.
 *
 * The Voice's own meter is already numerically guarded (num/den forced > 0,
 * else 4/4) so the SOFT voice never gets a degenerate beat budget.  But that
 * guard protects only the timing path -- the meter is ALSO drawn, through
 * `stave.addTimeSignature(...)` at each system's clef and through a mid-stave
 * `new TimeSigNote(...)` at every interior meter change, and BOTH of those
 * receive the RAW spec string (the per-measure `timeSignature` resolved into
 * `effectiveMeterByMeasure`, and the top-level one seeding it), not the
 * guarded value.  VexFlow's TimeSignature.parseTimeSpec THROWS
 * "BadTimeSignature" on an unparseable spec (e.g. "3" with no slash, "x/4",
 * "4/0", ""), and that throw propagates out of tickable construction and
 * aborts the ENTIRE render -- so one fat-fingered mid-score meter change
 * replaces the whole score with the plugin's red error box, while the exact
 * same malformed value at the TOP level is silently corrected to 4/4 for the
 * voice.  This closes that inconsistency for the display path.
 *
 * Accepts the two non-fraction meters VexFlow understands -- "C" (common) and
 * "C|" (cut) -- verbatim, and a plain `n/d` with a positive integer numerator
 * and denominator; anything else is skipped with a console warning (matching
 * the plugin's unknown-value convention), so an invalid per-measure change
 * simply leaves the previous meter in force rather than destroying the score.
 * Returns `undefined` for an absent meter, so a spec that never set one still
 * draws no signature -- keeping the no-meter and valid-meter paths identical.
 *
 * Exported pure/DOM-free for regression testing.
 */
export function sanitizeMeter(raw: string | undefined | null): string | undefined {
  if (raw == null) return undefined;
  const s = String(raw).trim();
  if (s === '') return undefined;
  // Common / cut time, the two glyph meters VexFlow accepts outside n/d.
  if (s === 'C' || s === 'C|') return s;
  const m = /^(\d+)\s*\/\s*(\d+)$/.exec(s);
  if (m) {
    const num = Number(m[1]);
    const den = Number(m[2]);
    if (num > 0 && den > 0) return `${num}/${den}`;
  }
  console.warn(`musicPlugin: invalid timeSignature "${raw}" ignored`);
  return undefined;
}

/**
 * Upper bound for a metronome bpm, in beats per minute.
 *
 * Practical published tempi top out well under this even for a fast marcato
 * (Presto sits around 168-200, Prestissimo rarely past ~208); 999 keeps the
 * metronome number a legible one-to-three digits and, crucially, caps the
 * width the mark claims above the staff so an absurd value cannot run the tempo
 * text off the system.  The cap mirrors the octave (clampKeyOctave) and
 * layout-dimension (D27) clamps -- a wildly out-of-range numeric input is
 * pulled back to the edge of the sane range rather than trusted.
 */
const MAX_TEMPO_BPM = 999;

/**
 * Validate a metronome bpm BEFORE it reaches VexFlow's StaveTempo, returning a
 * safe number or `undefined`.
 *
 * StaveTempo.draw renders the bpm with a bare `elText.setText('' + bpm)` -- it
 * stringifies whatever it is given, with no numeric guard (verified against
 * vexflow 5.0.0 stavetempo.js).  So an unsanitized bpm prints its raw
 * JavaScript stringification straight onto the score:
 *   - NaN / a non-numeric value -> the bpm is falsy, so `if (bpm)` skips it and
 *     the mark draws a DANGLING "♩ =" with nothing after the equals sign;
 *   - Infinity -> "♩ = Infinity";
 *   - a negative bpm -> "♩ = -120" (a negative tempo is meaningless);
 *   - an astronomically large value -> "♩ = 1e+21" (scientific notation, and it
 *     runs the mark off the system).
 * This is the WRONG-OUTPUT sibling of the D25 tempo fix, which sanitized the
 * beat `duration` and augmentation `dots` but deliberately left `bpm` raw.
 *
 * Accepts only a FINITE POSITIVE number: a non-finite or non-positive value is
 * dropped with a console warning (matching sanitizeMeter's "invalid -> ignore"
 * convention, and the plugin-wide unknown-value rule), so a bad bpm degrades to
 * "no bpm" -- the caller then draws the tempo NAME alone rather than a garbled
 * metronome.  A finite value above MAX_TEMPO_BPM is clamped to the cap (as the
 * octave / layout clamps do) rather than dropped, since the author clearly
 * intended a fast tempo.  A fractional bpm (e.g. 92.5, used by some editions)
 * is preserved; only a long floating tail is trimmed.  A well-formed integer
 * bpm is returned verbatim, so the valid path is byte-identical.
 *
 * Exported pure/DOM-free for regression testing.
 */
export function sanitizeTempoBpm(raw: number | undefined | null): number | undefined {
  if (raw == null) return undefined;
  const n = Number(raw);
  if (!Number.isFinite(n) || n <= 0) {
    console.warn(`musicPlugin: invalid tempo bpm "${raw}" ignored`);
    return undefined;
  }
  if (n > MAX_TEMPO_BPM) {
    console.warn(
      `musicPlugin: tempo bpm ${n} exceeds ${MAX_TEMPO_BPM}; clamped to `
      + `${MAX_TEMPO_BPM} to keep the metronome mark on the system`,
    );
    return MAX_TEMPO_BPM;
  }
  // Trim a long float tail (0.01 bpm precision is finer than any score needs)
  // without disturbing an integer bpm, which round-trips unchanged.
  return Math.round(n * 100) / 100;
}

/**
 * Validate the `number`/text of a Fingering or StringNumber BEFORE it reaches
 * VexFlow, returning a safe non-empty string or `undefined`.
 *
 * VexFlow's Fingering and StringNumber engrave their `number` option as
 * LITERAL text -- like StaveTempo's bpm, they stringify whatever they are
 * given with no numeric guard (verified against vexflow 5.0.0: the option is
 * passed straight to the glyph's text).  So a degenerate value is printed onto
 * the staff verbatim, the wrong-output sibling of the D28 tempo-bpm fix:
 *   - a non-finite NUMBER -> "NaN" / "Infinity" drawn beside the note;
 *   - the OBJECT form with no `number` (`{position:"below"}`) -> the literal
 *     "undefined", since `String(undefined)` is "undefined" (verified render);
 *   - an empty string -> an empty modifier box.
 *
 * A finger digit ("1".."5"/0) OR an extended-technique letter -- "T" for the
 * thumb, "p"/"i"/"m"/"a" for classical-guitar right hand -- is legitimate, so
 * a non-empty STRING is passed through UNTOUCHED; only a genuinely
 * unrenderable value is dropped, with a console warning matching the plugin's
 * unknown-value convention.  The valid path is byte-identical: a normal
 * "3" / 2 round-trips to the same string it did before.
 *
 * Exported pure/DOM-free for regression testing.
 */
export function sanitizeModifierNumber(
  raw: string | number | null | undefined,
): string | undefined {
  if (raw == null) {
    console.warn('musicPlugin: fingering/stringNumber with no number ignored');
    return undefined;
  }
  if (typeof raw === 'number') {
    if (!Number.isFinite(raw)) {
      console.warn(
        `musicPlugin: non-finite fingering/stringNumber "${raw}" ignored`,
      );
      return undefined;
    }
    return String(raw);
  }
  const s = String(raw).trim();
  if (s === '') {
    console.warn('musicPlugin: empty fingering/stringNumber ignored');
    return undefined;
  }
  return s;
}

/**
 * Upper bound for a measure number.
 *
 * Even a complete opera runs a few thousand bars; 100000 is far past any real
 * score yet keeps the drawn label a legible handful of digits and, crucially,
 * caps the width the number claims above the staff so an absurd value cannot
 * run the text off the system.  The cap mirrors MAX_TEMPO_BPM / the octave and
 * layout-dimension clamps: a wildly out-of-range numeric input is pulled back
 * to the edge of the sane range rather than trusted.
 */
export const MAX_MEASURE_NUMBER = 100000;

/**
 * Validate the OPENING measure number BEFORE it seeds the running count or
 * reaches VexFlow's Stave.setMeasure, returning a safe positive integer or
 * `undefined`.
 *
 * This is the last of the numeric-text spec fields to be guarded, the exact
 * sibling of the D28 tempo-bpm fix (and of sanitizeModifierNumber for
 * fingering/stringNumber): `measureNumber` is stringified straight onto the
 * score with no numeric guard.  On the `measureNumbers` path it seeds
 * `startNumber` and every per-system label is `numberOffset + firstMeasure`,
 * then drawn as `String(number)` (see drawMeasureNumbers); on the legacy
 * scalar path it is handed verbatim to `stave.setMeasure`.  So a degenerate
 * value prints degenerate text:
 *   - NaN / a non-numeric value -> "NaN" drawn above the first bar of every
 *     system;
 *   - Infinity -> "Infinity";
 *   - a negative or zero number -> a meaningless "0" / "-3" measure label;
 *   - a fraction (1.5) -> "1.5", which no bar is numbered;
 *   - an astronomically large value -> "1e+21" (scientific notation), which
 *     also runs the label off the system.
 *
 * Accepts only a FINITE value, truncates it to an integer (a bar index is
 * whole), and requires it to be positive: a non-finite, non-positive value is
 * dropped with a console warning (matching sanitizeTempoBpm's "invalid ->
 * ignore" convention and the plugin-wide unknown-value rule), so the caller
 * falls back to the default count start of 1 rather than numbering from
 * nonsense.  A finite value above MAX_MEASURE_NUMBER is clamped to the cap (as
 * the bpm / octave / layout clamps do) rather than dropped, since the author
 * clearly intended a high starting bar.  A well-formed positive integer (the
 * ordinary "start at bar 47" case) round-trips verbatim, so the valid path is
 * byte-identical.
 *
 * Exported pure/DOM-free for regression testing.
 */
export function sanitizeMeasureNumber(
  raw: number | null | undefined,
): number | undefined {
  if (raw == null) return undefined;
  const n = Number(raw);
  if (!Number.isFinite(n)) {
    console.warn(`musicPlugin: invalid measureNumber "${raw}" ignored`);
    return undefined;
  }
  const i = Math.trunc(n);
  if (i < 1) {
    console.warn(
      `musicPlugin: measureNumber "${raw}" is not a positive bar index; ignored`,
    );
    return undefined;
  }
  if (i > MAX_MEASURE_NUMBER) {
    console.warn(
      `musicPlugin: measureNumber ${i} exceeds ${MAX_MEASURE_NUMBER}; clamped `
      + `to ${MAX_MEASURE_NUMBER} to keep the label on the system`,
    );
    return MAX_MEASURE_NUMBER;
  }
  return i;
}

/** Build the EasyScore note list for a spec, parenthesising chords. */
/**
 * Build the EasyScore note string for one measure.
 *
 * `keySignature` is optional and, when given, filters redundant accidentals
 * out of the emitted pitches (see musicAccidentals.ts).  It is threaded in
 * here rather than applied to the finished notes because an accidental baked
 * into an EasyScore pitch string is EXPLICIT -- VexFlow draws it
 * unconditionally, and there is no later hook that can suppress it.
 *
 * Accidental state is per CALL, which is what makes a bar the reset unit:
 * both render paths already invoke this once per measure.
 */
export function buildNoteString(
  notes: MusicNoteSpec[],
  clef: string = 'treble',
  keySignature?: string,
  // Rest pitch override.  Omitted, a rest is CENTRED for the clef (the
  // single-voice case, byte-identical to before).  A multi-voice staff passes
  // the raised/lowered pitch from REST_PITCH_MULTIVOICE so its voices' rests
  // sit on their own lines and simultaneous rests do not overprint.
  restPitchOverride?: string,
): string {
  const restPitch = restPitchOverride
    ?? REST_PITCH_FOR_CLEF[clef] ?? REST_PITCH_FOR_CLEF.treble;
  const implied = keySignatureMap(keySignature);
  const barState = newBarState();
  return notes
    .map((n) => {
      // Neutralise a degenerate/unknown duration BEFORE it reaches EasyScore:
      // an unrecognised code builds a 0/NaN-tick note that hangs the formatter.
      const { base, dots } = sanitizeDuration(n.duration);
      const dotStr = '.'.repeat(dots);
      // A pitchless entry is silence.  `rest: true` is the documented spelling,
      // but an entry with no `keys` has no pitch to draw either and is treated
      // the same way -- see the guard below for why that must not fall through.
      if (n.rest || !n.keys?.length) {
        if (!n.rest) {
          // Falling through would emit the literal token "undefined/q": the
          // `keys[0]` of an empty array is undefined and template-interpolates
          // as text.  EasyScore cannot parse it into a pitch, so the note gets
          // a NaN position and the formatter's justification loop NEVER
          // RETURNS -- a 30s render timeout with a blank canvas and no error,
          // losing the whole score rather than one note (verified).
          //
          // Silence is the honest reading: the entry HAS no pitch, and the
          // likely intent of omitting `keys` was a rest.  Substituting an
          // audible pitch would invent a note the author never wrote, which is
          // worse than a bar that is too quiet -- a wrong note is heard as the
          // composer's, a missing one is visibly missing.
          console.warn(
            `musicPlugin: note with no keys and no rest:true; drawing a `
            + `${base} rest.  Use {"rest": true, "duration": "${base}"} to `
            + `write a rest explicitly.`,
          );
        }
        // The augmentation dot goes AFTER the /r, not on the duration:
        // "B4/q./r" parses as a NOTE (verified: rests=0, draws a notehead)
        // while "B4/q/r." is a dotted rest.
        return `${restPitch}/${base}/r${dotStr}`;
      }
      // Reject any unrenderable pitch (a mistyped accidental such as "ef/5")
      // BEFORE it reaches EasyScore: like an empty-keys entry, a bogus
      // accidental builds a NaN-position note that hangs the Formatter for the
      // full render timeout.  A CHORD keeps its still-valid keys (dropping only
      // the mistyped ones, so one typo does not discard a whole valid chord);
      // but a note whose keys are ALL unrenderable is DROPPED FROM THE OUTPUT
      // (return null, filtered out below), NOT turned into a rest.  A silent
      // rest would hide the author's typo on an otherwise-valid score; dropping
      // the note instead makes buildNoteString emit FEWER entries than the spec
      // has notes, which the caller's count-mismatch check (see renderMusicSpec)
      // catches and reports as a descriptive "Could not parse ... in measure N"
      // error -- honest failure, and the same clean rejection VexFlow itself
      // gives a genuinely-unparseable key like "not-a-pitch".  (A real rest
      // still comes from `rest:true` / empty `keys` above, which is unchanged.)
      const renderable = n.keys
        .map((k) => sanitizePitch(k))
        .filter((k): k is string => k !== null);
      if (renderable.length === 0) {
        return null;
      }
      // Filter BEFORE toEasyScoreKey so the filter sees the spec's own slash
      // form; it accepts either, but keeping one input shape here means the
      // two conversions cannot disagree about what an accidental is.
      const keys = renderable
        .map((k) => filterPitch(k, implied, barState))
        .map(toEasyScoreKey);
      const pitch = keys.length > 1 ? `(${keys.join(' ')})` : keys[0];
      return `${pitch}/${base}${dotStr}`;
    })
    // Drop notes whose every key was unrenderable (they returned null above):
    // emitting fewer entries than the spec has notes trips the caller's
    // count-mismatch guard, surfacing a descriptive "Could not parse" error
    // instead of a Formatter hang or a typo-hiding silent rest.  A real rest
    // (rest:true / empty keys) returns a string above and is never dropped.
    .filter((entry): entry is string => entry !== null)
    .join(', ');
}

/**
 * A courtesy (cautionary) accidental to draw on one note, addressed by
 * position WITHIN its measure so the pure planner needs no VexFlow handle.
 */
export interface CautionaryMark {
  /** Index of the note within its measure's own note list. */
  noteIndex: number;
  /** Index of the key within the note's chord (0 for a single note). */
  keyIndex: number;
  /** VexFlow accidental code to draw parenthesised: "n", "#", "b", "##", "bb". */
  code: string;
}

/** Slash-form spec key parse: letter, optional accidental, octave. */
const CAUTIONARY_KEY_RE = /^([a-gA-G])(n|[#b]{1,2})?\/(-?\d+)$/;

/**
 * Plan courtesy accidentals for ONE measure, given the measure BEFORE it.
 *
 * A courtesy accidental is the parenthesised reminder published editions print
 * when a pitch altered in one bar returns in the NEXT bar sounding
 * differently.  It is added only to a note that would otherwise print BARE (a
 * note carrying its own accidental is already its own reminder) and only when
 * the SAME pitch (letter AND octave) sounded with a DIFFERENT accidental in
 * the immediately preceding bar -- the one-bar-back rule the skill prompt
 * documents.
 *
 * Two derived notions drive the decision, both computed the same way the
 * emit path does so this planner cannot disagree with what is actually drawn:
 *   - SOUNDING accidental of a note = its explicit accidental if it has one
 *     ("n" meaning natural), else the key signature's accidental for that
 *     letter, else natural ("").
 *   - PRINTS BARE = the signature-filtered pitch carries no accidental glyph,
 *     i.e. filterPitch drops it (rule 1) because it matches the signature.
 *     This mirrors filterPitch's own printed-glyph logic in musicAccidentals.ts
 *     rather than calling it, because filterPitch returns a rewritten pitch
 *     string, not a "did it print" boolean.
 *
 * Only the FIRST occurrence of a pitch in the current bar is marked; a later
 * repeat needs no reminder.  Pure/DOM-free so it can be unit-tested without a
 * renderer.
 */
export function planCautionaryAccidentals(
  measureNotes: MusicNoteSpec[],
  prevMeasureNotes: MusicNoteSpec[] | undefined,
  keySignature?: string,
): CautionaryMark[] {
  if (!Array.isArray(measureNotes) || measureNotes.length === 0) return [];
  if (!Array.isArray(prevMeasureNotes) || prevMeasureNotes.length === 0) return [];

  const implied = keySignatureMap(keySignature);
  const sigAcc = (letter: string): string => implied?.[letter.toLowerCase()] ?? '';
  // The accidental GLYPH filterPitch would emit for this note, so the planner
  // reasons about exactly what gets drawn rather than a second interpretation
  // that could disagree with it (see musicAccidentals.ts filterPitch):
  //   rule 1: explicit sign matches the signature   -> "" (bare, signature supplies it)
  //   rule 4: bare note in a #/b signature          -> "n" (cancel the signature)
  //   rule 2: everything else                        -> the explicit sign verbatim
  const printed = (letter: string, explicit: string): string => {
    const inSig = sigAcc(letter);
    if (explicit !== '' && explicit !== 'n' && explicit === inSig) return '';    // rule 1
    if (explicit === '' && (inSig === '#' || inSig === 'b')) return 'n';         // rule 4
    return explicit;                                                             // rule 2
  };
  // Sounding accidental ("" natural, "#", "b", ...), DERIVED from the printed
  // glyph so it matches VexFlow's playback of the emitted pitch: a bare note
  // sounds as the signature, a printed natural sounds natural, else the sign.
  const sounding = (letter: string, explicit: string): string => {
    const p = printed(letter, explicit);
    if (p === '') return sigAcc(letter);
    if (p === 'n') return '';
    return p;
  };
  // Prints no accidental glyph at all -> a courtesy reminder is warranted here.
  const printsBare = (letter: string, explicit: string): boolean =>
    printed(letter, explicit) === '';
  const parse = (key: string): { letter: string; explicit: string; oct: string } | null => {
    const m = CAUTIONARY_KEY_RE.exec(String(key).trim());
    if (!m) return null;
    return { letter: m[1], explicit: m[2] ?? '', oct: m[3] };
  };

  // End-of-previous-bar sounding accidental per pitch (letter+octave); the
  // last occurrence wins, which is the state the reader carries into this bar.
  const prevSounding = new Map<string, string>();
  for (const n of prevMeasureNotes) {
    if (n?.rest || !n?.keys?.length) continue;
    for (const k of n.keys) {
      const p = parse(k);
      if (!p) continue;
      prevSounding.set(`${p.letter.toLowerCase()}${p.oct}`, sounding(p.letter, p.explicit));
    }
  }
  if (prevSounding.size === 0) return [];

  const marks: CautionaryMark[] = [];
  const seenThisBar = new Set<string>();
  measureNotes.forEach((n, noteIndex) => {
    if (n?.rest || !n?.keys?.length) return;
    n.keys.forEach((k, keyIndex) => {
      const p = parse(k);
      if (!p) return;
      const id = `${p.letter.toLowerCase()}${p.oct}`;
      if (seenThisBar.has(id)) return;          // only the first occurrence
      seenThisBar.add(id);
      const prev = prevSounding.get(id);
      if (prev === undefined) return;           // pitch absent last bar -> nothing to remind
      const now = sounding(p.letter, p.explicit);
      if (prev === now) return;                 // unchanged -> no reminder
      if (!printsBare(p.letter, p.explicit)) return; // already prints its own sign
      marks.push({ noteIndex, keyIndex, code: now === '' ? 'n' : now });
    });
  });
  return marks;
}

/**
 * True when `value` is a non-empty array of playable entries.  A rest counts:
 * a measure of silence is legitimate content, and rejecting it would make a
 * score that opens with a rest unrenderable.
 */
const hasNotes = (value: any): boolean => Array.isArray(value) && value.length > 0;

/**
 * True when a measure carries renderable content: real notes OR a
 * multi-measure rest (which holds no notes but is legitimate content -- a
 * part can consist entirely of "16 bars rest").  Used by the recognition
 * gates so a measures list whose bars are pure MMRs is still admitted.
 */
/**
 * Bar count of a multi-measure rest, honouring BOTH documented spellings.
 *
 * The skill prompt names this field `multiRest` in its primary section but
 * `multiMeasureRest` in a second one -- and the renderer only ever read
 * `multiRest`, so a spec written against the second section drew a
 * silently-empty bar: the H-bar and its count never appeared (verified -- the
 * middle bar of a "quarters / 4-bar rest / halves" spec rendered blank).
 * Accept either name (canonical `multiRest` wins when both are present) so an
 * author reaches the feature regardless of which spelling the prompt led them
 * to, matching the plugin's permissive shape-normalisation convention (see
 * normalizeVoicesShape).  Returns the RAW value (possibly non-integer) so the
 * builder's own "integer >= 1" validation still runs and warns.
 */
const multiRestOf = (m: any): number | undefined =>
  m?.multiRest ?? m?.multiMeasureRest;

const measureHasContent = (m: any): boolean =>
  hasNotes(m?.notes) || (multiRestOf(m) ?? 0) >= 1
  // A MEASURE-MAJOR bar carries its notes inside its own `voices` and has no
  // flat `notes`; without this the whole measures list tested empty and the
  // spec was rejected as non-music (voicesHaveNotes is assigned later in the
  // module but only CALLED at render time, so the forward reference is safe).
  || voicesHaveNotes(m?.voices);

/**
 * True when a `voices` list carries renderable notes in any of its voices,
 * whether flat (`voice.notes`) or measure-based (`voice.measures[].notes`).
 * Used by the recognition gates so a multi-voice staff -- which stores its
 * notes only inside `voices[]` -- is accepted rather than mistaken for empty.
 */
/**
 * Normalise a `voices` field into the canonical ARRAY-of-voice-objects shape
 * (`[{notes:[...]}, ...]`) the render core walks and indexes.
 *
 * Three spellings occur in the wild and an author cannot be expected to know
 * which one the internals prefer:
 *   - ARRAY of voice objects    `[{notes:[...]}, ...]`     (canonical)
 *   - ARRAY of bare note arrays `[[...], [...]]`
 *   - KEYED OBJECT              `{"1":[...], "2":[...]}`    (voice-NUMBER keyed,
 *                               the MusicXML `<voice>` numbering convention),
 *     whose values may each be a bare note array OR a voice object.
 *
 * The keyed-object form matched NO branch of `voicesHaveNotes` (which required
 * `Array.isArray(voices)`), so a multi-voice spec spelled that way was not
 * recognised as music at all -> `canHandle` false -> the D3Renderer reported
 * "No compatible plugin found for spec: {type: music}" and retried to the ~30s
 * inner timeout with zero output -- total data loss dressed up as a hang, the
 * same gate-vs-shape class already fixed for the staves/measures branches.
 *
 * Returns the array form (a NEW array only when a conversion was needed) or
 * `undefined` when there are no voices.  A value that is ALREADY an array is
 * returned BY REFERENCE (never rebuilt) unless it holds bare note-arrays that
 * must be wrapped -- so specs already using the canonical shape are
 * byte-identical and this cannot become a catch-all rewrite.
 *
 * Exported pure/DOM-free for regression testing.
 */
export function normalizeVoicesShape(voices: any): any[] | undefined {
  if (Array.isArray(voices)) {
    // An array of bare note-arrays (`[[...],[...]]`) -> wrap each as a voice
    // object; an array already holding voice objects is returned untouched.
    if (voices.some((v: any) => Array.isArray(v))) {
      return voices.map((v: any) => (Array.isArray(v) ? { notes: v } : v));
    }
    return voices;
  }
  if (voices && typeof voices === 'object') {
    // Keyed-object polyphony.  Order by NUMERIC voice key when every key is an
    // integer string ("1","2",...), so voice 1 stays the primary line and the
    // measure-alignment the secondary-voice pass relies on is preserved; fall
    // back to insertion order for non-numeric keys.
    const keys = Object.keys(voices);
    if (keys.length === 0) return [];
    const allNumeric = keys.every((k) => /^\d+$/.test(k));
    const ordered = allNumeric
      ? keys.slice().sort((a, b) => Number(a) - Number(b))
      : keys;
    return ordered.map((k) => {
      const v = (voices as any)[k];
      return Array.isArray(v) ? { notes: v } : v;
    });
  }
  return undefined;
}

const voicesHaveNotes = (voices: any): boolean => {
  const arr = normalizeVoicesShape(voices);
  return Array.isArray(arr) && arr.some((v: any) => hasNotes(v?.notes)
    || (Array.isArray(v?.measures) && v.measures.some((m: any) => hasNotes(m?.notes))));
};

/**
 * Canonicalise a single staff's `voices` field to the array-of-voice-objects
 * shape `measuresOf` (via `voices?.[0]`) and the secondary-voice loop index.
 *
 * `canHandle` (through `voicesHaveNotes`) now admits a keyed-object
 * (`voices:{"1":[...]}`) or bare-array (`voices:[[...]]`) spelling, but the
 * render core reads `staffSpec.voices?.[0]`, which is `undefined` for a keyed
 * object -> the staff rendered EMPTY (silent data loss the moment detection
 * started accepting the shape).  Normalising here, at the single point where
 * staff specs are assembled, keeps every downstream reader on one shape.  A
 * staff already using the canonical array is returned BY REFERENCE, so
 * existing single/grand-staff specs render byte-for-byte as before.
 */
function normalizeStaffVoiceShape<T extends { voices?: any }>(staffSpec: T): T {
  const nv = normalizeVoicesShape(staffSpec.voices);
  return nv && nv !== staffSpec.voices ? { ...staffSpec, voices: nv } : staffSpec;
}

/**
 * Transpose a MEASURE-MAJOR multi-voice staff into the canonical VOICE-MAJOR
 * shape the render core consumes.
 *
 * The skill prompt documents TWO equivalent spellings of the same polyphonic
 * music:
 *   - VOICE-MAJOR   `voices: [{stemDirection, measures:[...]}, ...]` -- one
 *     list per line, each carrying that line's bars.  This is the ONLY shape
 *     the render core reads: measuresOf takes `voices[0]`, the secondary loop
 *     `voices[1..]`.
 *   - MEASURE-MAJOR `measures: [{voices:[...], endBar, ...}, ...]` -- one list
 *     per BAR, each carrying that bar's voices, with the bar-level fields
 *     (endBar/timeSignature/systemBreak/multiRest) on the measure.
 *
 * Only voice-major was ever implemented -- nothing read a MEASURE's `voices`,
 * and MusicMeasure had no such field -- so a measure-major spec tested EMPTY
 * at every recognition gate (measureHasContent looked only at `notes`/
 * `multiRest`), isMusicSpec rejected it, canHandle returned false, and the
 * D3Renderer reported "No compatible plugin found for spec: {type: music}" and
 * retried to the ~30s inner timeout with ZERO output -- the same gate-vs-shape
 * total-data-loss class already fixed for the staves / keyed-object voices
 * branches.
 *
 * This flips the axes: for V = the most voices any bar declares, voice j
 * collects one measure per bar (`m.voices[j]?.notes`, or the bar's own flat
 * `notes` for j===0 on a bar with no `voices` at all, so a staff mixing single-
 * and multi-voice bars still works), carrying the bar-level fields so barlines,
 * meter changes, wrapping and multi-measure rests still land on the right bar.
 * Each output voice's `stemDirection` is the first one any bar declares for
 * that line.  The measure-major `measures` are then dropped so measuresOf falls
 * through to `voices[0].measures`, the standard primary-voice path.
 *
 * A staff that ALREADY has top-level `voices` (voice-major) is returned
 * untouched -- voice-major wins -- as is a staff whose measures carry no
 * `voices` at all, so every existing single-voice or measures-based spec is
 * byte-identical.  Exported pure/DOM-free for regression testing.
 */
export function normalizeMeasureMajorVoices<
  T extends { measures?: any[]; voices?: any },
>(staffSpec: T): T {
  // Voice-major already declared -> it wins; do not double-handle.
  if (staffSpec.voices != null) return staffSpec;
  const measures = staffSpec.measures;
  if (!Array.isArray(measures) || measures.length === 0) return staffSpec;
  // Only act when at least one bar actually carries a `voices` array; a plain
  // measures-based staff is returned BY REFERENCE, so its layout is unchanged.
  const anyMeasureVoiced = measures.some(
    (m: any) => normalizeVoicesShape(m?.voices) !== undefined,
  );
  if (!anyMeasureVoiced) return staffSpec;

  // Per-bar canonical voices; a bar without its own `voices` contributes its
  // flat `notes` as voice 0 only (so a bar of plain notes among voiced bars
  // still reads as the primary line, with the other voices resting).
  const perBarVoices: any[][] = measures.map((m: any) => {
    const nv = normalizeVoicesShape(m?.voices);
    return nv && nv.length > 0 ? nv : [{ notes: m?.notes ?? [] }];
  });
  const voiceCount = Math.max(1, ...perBarVoices.map((v) => v.length));

  // Bar-level fields ride with the MEASURE, not the voice, so re-attach them to
  // each transposed measure (voice 0 is what measuresOf reads for barlines /
  // meter; the copies on secondary voices are harmless -- the secondary loop
  // mirrors voice 0's barlines and reads only each bar's `notes`).
  const barFields = (m: any): Record<string, unknown> => {
    const out: Record<string, unknown> = {};
    for (const k of [
      'endBar', 'beginBar', 'timeSignature', 'systemBreak', 'pickup',
      'multiRest', 'multiMeasureRest',
    ]) {
      if (m?.[k] !== undefined) out[k] = m[k];
    }
    return out;
  };

  const voices: any[] = [];
  for (let j = 0; j < voiceCount; j += 1) {
    let stemDirection: string | undefined;
    const voiceMeasures = measures.map((m: any, bi: number) => {
      const vv = perBarVoices[bi][j];
      if (stemDirection === undefined && vv?.stemDirection) {
        stemDirection = vv.stemDirection;
      }
      return { notes: vv?.notes ?? [], ...barFields(m) };
    });
    voices.push({
      ...(stemDirection !== undefined ? { stemDirection } : {}),
      measures: voiceMeasures,
    });
  }

  const { measures: _drop, ...rest } = staffSpec as any;
  return { ...rest, voices } as T;
}

/**
 * A staff's measures, treating a flat `notes` list as a single measure.
 *
 * Normalising here means the render path has exactly one shape to walk, so a
 * multi-measure staff is not a second code path that can drift from the
 * single-measure one.
 */
function measuresOf(
  staffSpec: { notes?: MusicNoteSpec[]; measures?: MusicMeasure[]; voices?: MusicVoice[] },
): MusicMeasure[] {
  if (Array.isArray(staffSpec.measures) && staffSpec.measures.length > 0) {
    return staffSpec.measures;
  }
  if (hasNotes(staffSpec.notes)) return [{ notes: staffSpec.notes ?? [] }];
  // A voiced staff carries no direct notes/measures: its PRIMARY line is
  // voices[0], so the width estimate, spans, beams and tuplets -- all of which
  // walk measuresOf/notesOf -- transparently address the primary voice without
  // a second code path.  The secondary voices are handled explicitly in the
  // systems loop.
  const primary = staffSpec.voices?.[0];
  if (primary) {
    if (Array.isArray(primary.measures) && primary.measures.length > 0) {
      return primary.measures;
    }
    return [{ notes: primary.notes ?? [] }];
  }
  return [{ notes: staffSpec.notes ?? [] }];
}

/**
 * Every note on a staff, in order, with measure boundaries flattened away.
 * Span indices and the harp-pedal overlay both address this list, so a slur
 * or pedal marking can cross a barline.
 */
function notesOf(staffSpec: { notes?: MusicNoteSpec[]; measures?: MusicMeasure[] }): MusicNoteSpec[] {
  return measuresOf(staffSpec).flatMap((measure) => measure.notes ?? []);
}

/**
 * Recognise a music spec in either shape.
 *
 * A single-staff spec carries `notes` at the top level; a grand staff carries
 * them inside `staves[].notes` and has NO top-level `notes` at all.  Requiring
 * `spec.notes` therefore rejected every multi-staff spec, and because this
 * predicate backs the plugin's `canHandle`, the failure surfaced as
 * "No compatible plugin found for visualization type \"music\"" from the
 * D3Renderer orchestrator rather than as anything music-related.  Calling
 * renderMusicSpec() directly bypasses this gate, which is why the render core
 * tested clean while the real path was broken.
 */
export const isMusicSpec = (spec: any): spec is MusicSpec => {
  if (typeof spec !== 'object' || spec === null || spec.type !== 'music') {
    return false;
  }
  if (hasNotes(spec.notes)) return true;
  // A measures-only spec has no top-level `notes` either.
  if (Array.isArray(spec.measures)
      && spec.measures.some((m: any) => measureHasContent(m))) return true;
  // A multi-voice single-staff spec puts its notes inside `voices[]` and has
  // no top-level `notes`/`measures` at all -- requiring those would reject it
  // and (via canHandle) surface as "No compatible plugin", the same class of
  // gate bug the staves branch already documents.
  if (voicesHaveNotes(spec.voices)) return true;
  return (
    Array.isArray(spec.staves) &&
    spec.staves.length > 0 &&
    // A staves list of empty staves is not renderable, so require real notes
    // somewhere rather than accepting the key's mere presence.
    spec.staves.some((staff: any) => hasNotes(staff?.notes)
      || (Array.isArray(staff?.measures)
          && staff.measures.some((m: any) => measureHasContent(m)))
      || voicesHaveNotes(staff?.voices))
  );
};

/**
 * True when an object carries renderable music content at the top level
 * (a flat `notes` list, a `measures` list with notes, or a `staves` list
 * whose staves have notes).  This is the shape check `isMusicSpec` performs
 * AFTER the `type` gate; factored out so `resolveMusicSpec` can reuse it
 * without re-triggering the type gate it is trying to satisfy.
 */
const hasMusicContent = (s: any): boolean =>
  typeof s === 'object' && s !== null && (
    hasNotes(s.notes)
    || (Array.isArray(s.measures) && s.measures.some((m: any) => measureHasContent(m)))
    || voicesHaveNotes(s.voices)
    || (Array.isArray(s.staves) && s.staves.length > 0
        && s.staves.some((staff: any) => hasNotes(staff?.notes)
          || (Array.isArray(staff?.measures)
              && staff.measures.some((m: any) => measureHasContent(m)))
          || voicesHaveNotes(staff?.voices)))
  );

/**
 * Recover a music spec from the wrapper the `render_diagram` tool sends.
 *
 * The tool always ships `{ type: 'music', definition: '<json string>' }`,
 * and the JSON body it hands over almost never repeats the `type` field --
 * `type` lives on the wrapper, not inside the definition.  `isMusicSpec`
 * (which backs the plugin's `canHandle`) hard-requires `spec.type === 'music'`
 * on the object it inspects, so it rejected the parsed definition (no `type`)
 * AND the wrapper (has `type` but no top-level `notes`/`measures`/`staves`).
 * With `canHandle` false the D3Renderer orchestrator found no plugin for
 * `type: 'music'` and retried to the ~30s inner timeout with zero output --
 * total data loss dressed up as a hang, identical to the contract mismatch
 * already fixed for joint (Issue 2), chord (Issue 10) and network (Issue 11).
 *
 * This lifts the parsed definition's fields onto a shallow copy and stamps
 * `type: 'music'` so the downstream `isMusicSpec` gate passes, but ONLY when
 * the parsed body actually carries music content -- a non-music spec whose
 * definition happens to parse is returned untouched, so this cannot hijack
 * another renderer's spec.  Pure and DOM-free so it can be unit-tested.
 *
 * Exported for regression testing.
 */
export function resolveMusicSpec(spec: any): any {
  if (typeof spec !== 'object' || spec === null) return spec;

  // Already structured at the top level -- leave it exactly as-is so a
  // correctly-authored spec (fenced ```music block, or a spec already
  // carrying type+notes) is never rewritten.  The caller's isMusicSpec still
  // applies the type gate to this untouched object.
  if (hasMusicContent(spec)) return spec;

  // An OBJECT definition arrives when the envelope itself was authored as
  // JSON and parsed upstream (a ```d3 fence with a nested definition).  Same
  // guarded lifting as the string path below -- claim the spec only when the
  // body genuinely carries music -- and doubly important here because this
  // function also backs canHandle: before this branch existed, an
  // object-definition music spec was never SELECTED at all, which surfaced as
  // the renderer's ~30s no-plugin timeout rather than any error.
  if (spec.definition !== null && typeof spec.definition === 'object'
      && hasMusicContent(spec.definition)) {
    return { ...spec.definition, type: 'music' };
  }

  // Only attempt recovery from a JSON-object `definition` string.
  if (typeof spec.definition !== 'string' || spec.definition.trim() === '') return spec;
  if (spec.definition.trimStart()[0] !== '{') return spec;

  let parsed: any;
  try {
    parsed = JSON.parse(spec.definition);
  } catch (_e) {
    return spec;
  }
  if (typeof parsed !== 'object' || parsed === null) return spec;

  // Guard: only claim the spec when the parsed body is genuinely music.  A
  // network/chord/plotly definition parses to an object too, but none of them
  // carry a music `notes`/`measures`/`staves` shape, so they fall through
  // untouched and their own (higher- or lower-priority) plugin handles them.
  if (!hasMusicContent(parsed)) return spec;

  // The parsed body IS the music spec; stamp the type the wrapper carried so
  // the downstream isMusicSpec gate accepts it.  Render params that live on
  // the wrapper (theme/title) are intentionally dropped -- the music spec has
  // its own width/height.
  return { ...parsed, type: 'music' };
}

/**
 * Vertical shift applied to a tempo mark, in pixels (negative is up).
 *
 * `stave.setTempo(tempo, y)`'s second argument is a Y shift, and passing 0
 * leaves the mark in the same band as an 8va bracket or a volta -- measured
 * collision: tempo at y=60, bracket at y=60/56.  Engraving convention puts
 * the tempo above everything else anyway, so the lift is unconditional.
 *
 * -34 rather than a smaller lift because BRACKET_LINE_WITH_TEMPO raises the
 * bracket too, and the two corrections were fighting: measured with -16 and a
 * 3-line bracket, the tempo sat at y=44 and the bracket at y=36 -- still
 * colliding, only with the stacking order inverted.  Sweep of (shiftY, line=3)
 * pairs: -16 -> gap -8, -22 -> -2, -28 -> +4, -34 -> +10.  -34 is the first
 * value with clear separation, and nothing clips (topmost glyph y=26).
 */
const TEMPO_SHIFT_Y = -34;

/**
 * Tempo lift, in pixels, when a NAVIGATION MARK (segno / coda / D.S. ...)
 * shares the band above the top staff.
 *
 * VexFlow's `Repetition` glyph is immovable: `drawSegnoFixed`/`drawCodaFixed`
 * pin the symbol at `getYForTopText(numLines)` (~4 stave-lines, ≈40px, above
 * the staff top) and the note-start x, and ignore BOTH the `xShift` and
 * `yShift` the modifier carries -- verified in staverepetition.js.  So the
 * mark cannot be nudged aside; the only movable element is the tempo, which
 * this plugin constructs directly.
 *
 * The ordinary lift (TEMPO_SHIFT_Y = -34) was tuned to clear brackets/voltas
 * BELOW the tempo, and it happens to land the tempo text only ~6px under the
 * segno band -- so "Allegro (♩ = 120)" and the segno % overprint (measured
 * collision on a tempo+segno spec).  Lifting the tempo to -64 puts it a clear
 * ~24px above the segno's ~40px band, more than a glyph height, so the two
 * occupy separate rows as published scores set them (tempo top row, nav mark
 * beneath).  Only applied when a mark is present, so the mark-free case keeps
 * its previous placement byte-for-byte.
 */
const TEMPO_SHIFT_Y_WITH_MARK = -64;

/**
 * Extra stave-lines a bracket is raised by when a tempo mark shares the band.
 * Three lines puts the bracket at y=36 on a stave whose top line is y=50,
 * clearing the notes below while staying under the lifted tempo above.
 */
const BRACKET_LINE_WITH_TEMPO = 3;

/**
 * How far to pull a tempo mark left so it sits above the clef, not after it.
 *
 * `Stave.setTempo` hardcodes `new StaveTempo(tempo, this.x, y)`, and
 * `StaveTempo.draw()` then ADDS `stave.getModifierXShift()` -- which is
 * `startX - x`, the width of the clef and time signature.  The mark therefore
 * renders at x=56 on a treble/4-4 stave, past the clef and directly on top of
 * a bracket that begins at the first notehead (x=58).  Subtracting the same
 * shift before construction cancels the addition, landing the mark at the
 * stave's own x.  There is no supported way to set the x afterwards:
 * setPosition(ABOVE), constructing StaveTempo with stave.x, and mutating the
 * modifier post-hoc were all verified to still render at x=56.
 *
 * Returns 0 rather than throwing when the shift cannot be determined, since a
 * slightly misplaced tempo mark is much better than a failed score.
 */
function tempoLeftShift(stave: any): number {
  try {
    if (typeof stave.getModifierXShift !== 'function') return 0;
    // getModifierXShift(0) indexes stave.modifiers and throws on an empty
    // list; a stave with no clef or time signature has nothing to clear.
    const modifiers = typeof stave.getModifiers === 'function' ? stave.getModifiers() : [];
    if (!modifiers || modifiers.length === 0) return 0;
    const shift = stave.getModifierXShift(0);
    return Number.isFinite(shift) ? shift : 0;
  } catch {
    return 0;
  }
}

/** Dark-mode note ink. 13.2:1 on #1f1f1f, mirroring black's 21:1 on white. */
const DARK_INK = '#e6e6e6';
/**
 * Dark-mode ledger lines.  Deliberately dimmer than the note ink, preserving
 * VexFlow's light-mode relationship (#444 at 9.7:1 sits below black at 21:1)
 * so ledger lines stay subordinate to noteheads instead of competing.
 */
const DARK_LEDGER = '#c0c0c0';

/**
 * Explicit colours that survive root-level inheritance and so must be
 * remapped individually.  Verified against real VexFlow 5 output:
 *   - #444    Stave.defaultLedgerLineStyle
 *   - #000000 StringNumber's dashed connector line
 *
 * Stave/barline #999999 is deliberately ABSENT: it measures 5.8:1 on #1f1f1f,
 * already comfortably legible, and lightening it would make the staff lines
 * compete with the noteheads they exist to position.
 */
const DARK_COLOR_REMAP: Record<string, string> = {
  '#444': DARK_LEDGER,
  '#444444': DARK_LEDGER,
  'black': DARK_INK,
  '#000': DARK_INK,
  '#000000': DARK_INK,
};

/**
 * Recolour a rendered VexFlow SVG for dark mode.
 *
 * VexFlow hardcodes its ink to black and offers no theme hook: it writes
 * fill="black" stroke="black" onto the ROOT <svg>, and virtually every
 * notehead, stem, clef, rest and glyph inherits from there rather than
 * carrying a colour of its own.  Verified on real output -- a four-note
 * staff has exactly two colour-bearing attributes at the root and none on
 * its noteheads.  On #1f1f1f that ink measures 1.27:1, i.e. invisible.
 *
 * This does NOT use the shared enhanceSVGVisibility, for two reasons found
 * empirically rather than assumed:
 *
 *  1. It would not work.  That helper inspects each text/path element's OWN
 *     fill/stroke, and the probe shows those attributes are absent here --
 *     the colour is inherited from the root, which the helper never rewrites.
 *
 *  2. It would damage the engraving.  It force-sets stroke-width:2 on strokes
 *     it judges invisible; stems and beams are deliberately hairlines (1.5),
 *     and thickening them smears the staff.
 *
 * Setting the root attributes therefore fixes the whole score in one move,
 * and only the few explicitly-coloured exceptions need individual handling.
 */
export function applyMusicDarkTheme(svgEl: SVGElement | null): void {
  if (!svgEl) return;

  // Root-level ink: this is what the great majority of glyphs inherit.
  svgEl.setAttribute('fill', DARK_INK);
  svgEl.setAttribute('stroke', DARK_INK);

  for (const el of Array.from(svgEl.querySelectorAll('*'))) {
    for (const attr of ['fill', 'stroke'] as const) {
      const value = el.getAttribute(attr);
      // "none" is load-bearing: VexFlow marks stroke-only paths fill="none"
      // and fill-only text stroke="none".  Recolouring either would flood
      // glyph interiors or outline text.  Absent values are left alone so
      // inheritance from the root keeps working.
      if (!value || value === 'none') continue;
      const mapped = DARK_COLOR_REMAP[value.toLowerCase()];
      if (mapped) el.setAttribute(attr, mapped);
    }
  }
}

/**
 * Ink colour for hand-drawn overlays, which are authored here rather than by
 * VexFlow and so are coloured directly instead of being remapped afterwards.
 */
export function musicInkColor(isDarkMode: boolean): string {
  return isDarkMode ? DARK_INK : '#1F2937';
}

/**
 * Harp pedal glyph row, drawn as a small SVG overlay positioned above the
 * stave at a given note's x-coordinate. VexFlow has no native harp pedal
 * primitive, so this hand-draws the LilyPond-style `^`/`-`/`v`/`|` encoding
 * (flat/natural/sharp, left/right pedal group divider) using the same
 * d3-append-to-existing-svg technique as packetPlugin.ts's brackets.
 */
export function drawHarpPedalDiagram(
  d3: any,
  svg: any,
  pedalString: string,
  x: number,
  y: number,
  isDarkMode: boolean,
): void {
  const textFill = musicInkColor(isDarkMode);
  const glyphFor = (ch: string): string => {
    if (ch === '^') return '\u266D'; // flat
    if (ch === 'v') return '\u266F'; // sharp
    if (ch === '-') return '\u266E'; // natural
    return '';
  };

  let cursorX = x;
  for (const ch of pedalString) {
    if (ch === '|') {
      // Divider between left-foot and right-foot pedal groups
      svg.append('line')
        .attr('x1', cursorX).attr('x2', cursorX)
        .attr('y1', y - 8).attr('y2', y + 2)
        .attr('stroke', textFill).attr('stroke-width', 1);
      cursorX += 6;
      continue;
    }
    const glyph = glyphFor(ch);
    if (!glyph) continue;
    svg.append('text')
      .attr('x', cursorX).attr('y', y)
      .attr('text-anchor', 'middle')
      .attr('fill', textFill)
      .style('font', 'bold 11px "Segoe UI", Arial, sans-serif')
      .text(glyph);
    cursorX += 12;
  }
}

/**
 * Sung-syllable underlay drawn beneath a staff.
 *
 * VexFlow has no lyric primitive that also draws word hyphens and melisma
 * extenders, so -- as with the harp-pedal overlay above -- the layer is
 * hand-drawn with d3 AFTER VexFlow has formatted the notes, reading each
 * note's resolved x so syllables sit under their noteheads.  Drawing it here
 * rather than via a stack of Annotations also guarantees ONE shared baseline
 * per staff, which is what "baseline-aligned across a system" requires:
 * stacked Annotations drift note-to-note as each note's other modifiers
 * change the height of its below-staff band.
 */
export function drawLyricLayer(
  d3: any,
  svg: any,
  stave: any,
  renderedNotes: any[],
  specNotes: MusicNoteSpec[],
  isDarkMode: boolean,
): void {
  const textFill = musicInkColor(isDarkMode);
  const LYRIC_FONT = '400 12px "Times New Roman", Georgia, serif';
  // Bottom stave line; the underlay hangs a fixed distance below it so every
  // syllable shares the same baseline regardless of the pitches above.
  const bottomLineY = typeof stave.getYForLine === 'function' ? stave.getYForLine(4) : 60;
  // A dynamic already occupies the first band below the staff, so drop the
  // lyric line under it when one is present rather than letting them collide.
  const hasDynamic = specNotes.some((n) => n?.dynamic);
  const baselineForVerse = (verse: number): number =>
    bottomLineY + (hasDynamic ? 44 : 26) + (Math.max(1, verse) - 1) * 15;

  const xOf = (note: any): number | null =>
    note && typeof note.getAbsoluteX === 'function' ? note.getAbsoluteX() : null;
  const verseOf = (l: MusicLyric): number => Math.max(1, Math.floor(l.verse ?? 1));
  const normalise = (specNote?: MusicNoteSpec): MusicLyric | null => {
    const l = specNote?.lyric;
    if (l == null) return null;
    const obj = typeof l === 'string' ? { text: l } : l;
    return obj && obj.text ? obj : null;
  };

  renderedNotes.forEach((note, i) => {
    const lyric = normalise(specNotes[i]);
    if (!lyric) return;
    const x = xOf(note);
    if (x == null) return;
    const verse = verseOf(lyric);
    const y = baselineForVerse(verse);

    svg.append('text')
      .attr('x', x).attr('y', y)
      .attr('text-anchor', 'middle')
      .attr('fill', textFill)
      .style('font', LYRIC_FONT)
      .text(lyric.text);

    // Hyphen joining a split word to its next syllable, centred in the gap --
    // the standard engraving of "lo-ver".  Only for begin/middle syllables,
    // and only to the next syllable in the SAME verse.
    if (lyric.syllabic === 'begin' || lyric.syllabic === 'middle') {
      let j = i + 1;
      let next: MusicLyric | null = null;
      for (; j < renderedNotes.length; j++) {
        const cand = normalise(specNotes[j]);
        if (cand && verseOf(cand) === verse) { next = cand; break; }
      }
      const nx = next ? xOf(renderedNotes[j]) : null;
      if (nx != null && nx > x) {
        svg.append('text')
          .attr('x', (x + nx) / 2).attr('y', y)
          .attr('text-anchor', 'middle')
          .attr('fill', textFill)
          .style('font', LYRIC_FONT)
          .text('-');
      }
    }

    // Melisma extender: a line held from the syllable to the following note.
    // Width of the syllable is estimated (VexFlow owns the real metrics, but
    // the overlay text is ours), which is enough to start the line clear of
    // the glyph rather than through it.
    if (lyric.extend) {
      const nx = i + 1 < renderedNotes.length ? xOf(renderedNotes[i + 1]) : null;
      const startX = x + Math.max(6, lyric.text.length * 3 + 4);
      const endX = nx != null ? nx - 4 : null;
      if (endX != null && endX > startX + 4) {
        svg.append('line')
          .attr('x1', startX).attr('x2', endX)
          .attr('y1', y - 3).attr('y2', y - 3)
          .attr('stroke', textFill).attr('stroke-width', 1);
      }
    }
  });
}

/**
 * Dynamic marks (p, mf, f, sfz, ...) drawn BELOW the staff, on a consistent
 * band, aligned under their notes.
 *
 * Published instrumental scores set dynamics beneath the staff; the band above
 * is reserved for tempo, chord symbols and ornaments.  This was previously
 * done with VexFlow's TextDynamics positioned by its `line` option, but in
 * VexFlow 5.0 that option does not move the mark below the staff (verified on
 * the built bundle: `line: 9`, which should resolve to getYForLine(6) two
 * spaces under the bottom line, still rendered `p`/`f` ABOVE the top line).
 * So -- like the harp-pedal, lyric and volta layers -- the dynamics are
 * hand-drawn with d3 AFTER formatting, reading each note's resolved x.  An
 * overlay also takes no beat time, so it cannot displace the notes and removes
 * the need for the parallel GhostNote-padded voice the TextDynamics approach
 * required to keep note spacing intact.
 *
 * The band sits just below the bottom stave line and ABOVE the lyric underlay
 * (drawLyricLayer drops verse 1 from +26 to +44 when a dynamic is present), so
 * a note carrying both a dynamic and a lyric keeps them on separate rows.  An
 * unknown mark (outside DYNAMIC_MARKS) is skipped with a console warning rather
 * than drawn as ASCII, matching the plugin's unknown-name convention.
 */
export function drawDynamicsLayer(
  d3: any,
  svg: any,
  stave: any,
  renderedNotes: any[],
  specNotes: MusicNoteSpec[],
  isDarkMode: boolean,
): void {
  const textFill = musicInkColor(isDarkMode);
  // Bold italic serif is the conventional look of a dynamic; the real SMuFL
  // dynamic glyphs are VexFlow-internal vector paths that cannot be reused in
  // a d3 <text>, so a bold-italic serif approximation stands in for them.
  const DYNAMIC_FONT = 'italic 700 15px "Times New Roman", Georgia, serif';
  const bottomLineY = typeof stave.getYForLine === 'function' ? stave.getYForLine(4) : 60;
  // Two stave-spaces below the bottom line -- the standard dynamics band, and
  // the position the old TextDynamics `line: 9` was aiming for (getYForLine(6)).
  const y = bottomLineY + 24;
  const xOf = (note: any): number | null =>
    note && typeof note.getAbsoluteX === 'function' ? note.getAbsoluteX() : null;

  renderedNotes.forEach((note, i) => {
    const mark = specNotes[i]?.dynamic;
    if (!mark) return;
    if (!DYNAMIC_MARKS.has(mark)) {
      console.warn(`musicPlugin: unknown dynamic "${mark}" skipped`);
      return;
    }
    const x = xOf(note);
    if (x == null) return;
    svg.append('text')
      .attr('x', x).attr('y', y)
      .attr('text-anchor', 'middle')
      .attr('fill', textFill)
      .style('font', DYNAMIC_FONT)
      .text(mark);
  });
}

/**
 * Height of the title block, in pixels, given which of its parts are present.
 *
 * Returned so the render path can both reserve headroom (grow the canvas and
 * push the first system down by this much) and know where to draw -- the two
 * must agree or the title either overlaps the top staff or floats detached
 * above the canvas.  The bands mirror published practice: a large title, a
 * smaller subtitle, then a credits line carrying composer/lyricist.
 */
function titleBlockHeight(spec: MusicSpec): number {
  let h = 0;
  if (spec.title) h += 26;
  if (spec.subtitle) h += 18;
  if (spec.composer || spec.lyricist) h += 18;
  // A little breathing space between the block and the first staff, but only
  // when something was drawn.
  return h > 0 ? h + 8 : 0;
}

/**
 * Title block drawn above the system: work title, subtitle, and
 * composer/lyricist credits.
 *
 * Hand-drawn with d3 rather than via VexFlow because VexFlow has no page-title
 * primitive -- StaveText is a per-stave label anchored to one staff, not a
 * centred heading spanning the system, and it cannot carry the distinct
 * type sizes and left/right credit alignment a title block needs.  Like the
 * harp-pedal and lyric overlays, it is drawn after formatting and picks its
 * own theme-aware ink, so it must not be run through the dark-mode remap.
 *
 * `width` is the full canvas width so the title centres on the page and the
 * composer credit right-aligns to the right margin, matching engraving
 * convention (lyricist left, composer right, beneath a centred title).
 */
/**
 * Font for a hand-drawn tempo NAME overlay.  Bold serif, matching VexFlow's
 * `StaveTempo.name` (fontSize 14, fontWeight bold) so the split-out name reads
 * like the metronome VexFlow still draws beside it.  The SAME string is used to
 * MEASURE the name (measureTempoNameWidth) as to draw it, so the width the
 * metronome is positioned against is self-consistent -- which is exactly the
 * property VexFlow's own draw path lacks (see drawTempoName).
 */
const TEMPO_NAME_FONT = 'bold 15px "Times New Roman", Georgia, serif';

/**
 * Width in px of a tempo name in TEMPO_NAME_FONT, measured on a detached
 * canvas.  Unlike VexFlow's internal measurement this is measured in the SAME
 * font the overlay renders in, so the metronome placed off this width cannot
 * overprint the name.  Falls back to a character-count estimate when no canvas
 * 2d context is available (jsdom), which is enough to keep the two apart.
 */
function measureTempoNameWidth(text: string): number {
  try {
    const canvas = document.createElement('canvas');
    const ctx = canvas.getContext('2d');
    if (ctx) {
      ctx.font = TEMPO_NAME_FONT;
      const w = ctx.measureText(text).width;
      if (Number.isFinite(w) && w > 0) return w;
    }
  } catch {
    /* jsdom / no canvas -- fall through to the estimate. */
  }
  return text.length * 8;
}

/**
 * Tempo NAME drawn as a d3 overlay, left of the VexFlow-drawn metronome.
 *
 * VexFlow's StaveTempo chains the "(♩ = N)" metronome group off
 * `this.getWidth()` of the name, but that width is measured on a detached
 * canvas whose font need not match the SVG render font in this environment;
 * when it under-measures, the metronome overprints the end of the name and the
 * leading "(" is lost (verified: "Andante con moto" + bpm collapsed the word
 * spaces and overlapped the note glyph).  So -- exactly as the title, dynamics,
 * lyric and nav-overflow layers do for their own mis-placed VexFlow
 * primitives -- the name is hand-drawn here in the post-format pass while the
 * metronome (which renders correctly on its own) stays with VexFlow, and picks
 * its own theme-aware ink so it must run after the dark-mode recolour.  Null
 * (a no-op) for name-only / bpm-only marks, which VexFlow still draws itself.
 */
export function drawTempoName(
  d3: any,
  svg: any,
  plan: { text: string; x: number; y: number } | null,
  isDarkMode: boolean,
): void {
  if (!plan) return;
  svg.append('text')
    .attr('x', plan.x).attr('y', plan.y)
    .attr('text-anchor', 'start')
    .attr('fill', musicInkColor(isDarkMode))
    .style('font', TEMPO_NAME_FONT)
    .text(plan.text);
}

export function drawTitleBlock(
  d3: any,
  svg: any,
  spec: MusicSpec,
  width: number,
  isDarkMode: boolean,
): void {
  const textFill = musicInkColor(isDarkMode);
  const centre = width / 2;
  // Left/right margins match the system inset (factory.System uses width-20,
  // i.e. a 10px inset each side).
  const margin = 12;
  let y = 22;

  if (spec.title) {
    svg.append('text')
      .attr('x', centre).attr('y', y)
      .attr('text-anchor', 'middle')
      .attr('fill', textFill)
      .style('font', '700 20px "Times New Roman", Georgia, serif')
      .text(spec.title);
    y += 26;
  }
  if (spec.subtitle) {
    svg.append('text')
      .attr('x', centre).attr('y', y)
      .attr('text-anchor', 'middle')
      .attr('fill', textFill)
      .style('font', '400 13px "Times New Roman", Georgia, serif')
      .text(spec.subtitle);
    y += 18;
  }
  if (spec.composer || spec.lyricist) {
    const CREDIT_FONT = 'italic 400 12px "Times New Roman", Georgia, serif';
    if (spec.lyricist) {
      svg.append('text')
        .attr('x', margin).attr('y', y)
        .attr('text-anchor', 'start')
        .attr('fill', textFill)
        .style('font', CREDIT_FONT)
        .text(spec.lyricist);
    }
    if (spec.composer) {
      svg.append('text')
        .attr('x', width - margin).attr('y', y)
        .attr('text-anchor', 'end')
        .attr('fill', textFill)
        .style('font', CREDIT_FONT)
        .text(spec.composer);
    }
  }
}

/**
 * Instrument / part labels drawn in the left gutter, one per named staff.
 *
 * Hand-drawn with d3 rather than via VexFlow's StaveText: StaveText anchors
 * INSIDE the staff box and reserves no horizontal room, so it overprints the
 * clef.  Here the label is right-aligned to just left of the staff's start-x
 * (the notes' left edge, which already clears the clef and signatures) and
 * vertically centred on the staff, matching how a published score sets part
 * names.  Runs in the same post-format overlay pass as the title/lyric layers
 * and picks its own theme-aware ink, so it must not be run through the
 * dark-mode remap.
 */
export function drawStaffLabels(
  d3: any,
  svg: any,
  built: Array<{ stave: any; staffSpec: MusicStaff; systemIndex?: number }>,
  isDarkMode: boolean,
): void {
  const textFill = musicInkColor(isDarkMode);
  const LABEL_FONT = '400 13px "Times New Roman", Georgia, serif';
  for (const { stave, staffSpec, systemIndex } of built) {
    const name = staffSpec.name;
    if (!name) continue;
    // On a wrapped score the same staff appears once per system.  Printing the
    // full name beside every line is wrong engraving AND unreadable clutter (a
    // 6-system part drew "Fl." six times); published scores name the part in
    // full on the first system and use a short form, or nothing, thereafter.
    // `shortName` opts into the short form; without it later systems go bare.
    const isContinuation = (systemIndex ?? 0) > 0;
    const label = isContinuation ? staffSpec.shortName : name;
    if (!label) continue;
    // Right-align to a few px left of the staff's own x (its left edge), so
    // the label sits in the reserved gutter and never touches the barline.
    const staveX = typeof stave.getX === 'function' ? stave.getX() : 10;
    const labelX = staveX - 8;
    // Vertical centre of the five-line staff: midpoint of the top and bottom
    // lines, nudged so the text's baseline (not its top) lands on centre.
    const topY = typeof stave.getYForLine === 'function' ? stave.getYForLine(0) : 0;
    const bottomY = typeof stave.getYForLine === 'function' ? stave.getYForLine(4) : 40;
    const centreY = (topY + bottomY) / 2 + 4;
    svg.append('text')
      .attr('x', labelX).attr('y', centreY)
      .attr('text-anchor', 'end')
      .attr('fill', textFill)
      .style('font', LABEL_FONT)
      .text(label);
  }
}

/**
 * Measure numbers drawn above the first bar of each system.
 *
 * VexFlow's `Stave.setMeasure` DOES render a number for a lone, directly-placed
 * stave (the legacy scalar `measureNumber` path still uses it), but on a
 * wrapped multi-system layout -- staves created through `factory.System` -- the
 * number silently fails to draw.  Verified against the built bundle:
 * `measureNumbers: true` over four bars wrapping to four systems drew ZERO
 * numbers on every system, while the identical `setMeasure(1)` on a single
 * stave drew "1".  So, exactly like the staff-label, volta, lyric and dynamics
 * layers, the numbers are hand-drawn with d3 AFTER formatting, reading each
 * system top-stave's resolved x/y via the same getX()/getYForLine() path those
 * layers rely on (and which the volta overlay already proves works for wrapped
 * systems).  Placed small, above the top line at the start of the first
 * note (getNoteStartX, clear of the clef/key/time block), as published scores
 * set them.
 */
export function drawMeasureNumbers(
  d3: any,
  svg: any,
  plans: Array<{ stave: any; number: number }>,
  isDarkMode: boolean,
): void {
  const textFill = musicInkColor(isDarkMode);
  for (const { stave, number } of plans) {
    // Anchor the number above the FIRST NOTE of the bar, not the stave's left
    // edge.  getX() is the barline/clef position, so a number placed there was
    // drawn directly over the clef glyph -- and on the first system it also
    // collided with the tempo mark (drawn at stave.x, lifted) and the
    // rehearsal/`section` mark, crowding all three into the same top-left band.
    // getNoteStartX() is the x AFTER the clef/key/time block, i.e. the start of
    // the note area, which is where published scores place a system-start
    // measure number (above and slightly left of the first note, clear of the
    // clef and of anything in the far-left band).  Fall back to a fixed inset
    // past getX() only if getNoteStartX is unavailable.
    const x = typeof stave.getNoteStartX === 'function'
      ? stave.getNoteStartX()
      : (typeof stave.getX === 'function' ? stave.getX() + 40 : 10);
    const topY = typeof stave.getYForLine === 'function' ? stave.getYForLine(0) : 20;
    svg.append('text')
      .attr('x', x).attr('y', topY - 12)
      .attr('text-anchor', 'middle')
      .attr('fill', textFill)
      .style('font', '400 11px "Times New Roman", Georgia, serif')
      .text(String(number));
  }
}

/**
 * The SECOND and later navigation marks on a given side, hand-drawn on their
 * own stacked rows so they do not overprint the first.
 *
 * VexFlow's `Repetition` glyph is immovable (see NAV_OVERLAY_LABELS): every
 * mark on a side lands on the same x AND the same y, so a jump scheme with two
 * or more marks on one side drew them on top of each other.  The renderer lets
 * VexFlow draw the FIRST mark on each side (byte-identical to the single-mark
 * path, so every existing single-`mark` render is unchanged) and routes the
 * overflow marks here.  Each is placed on its own row just above the top stave
 * line -- row 1 nearest the line, higher rows above it -- which stays within
 * the headroom `needsRoomAbove` already reserves for a mark and sits below the
 * VexFlow-drawn primary, so nothing overprints.  Same overlay pass, and same
 * theme-aware ink, as the dynamics / measure-number / volta layers, so it must
 * run after formatting and after the dark-theme recolour.
 *
 * `plans` carries one entry per overflow mark: its Repetition.type key and its
 * 1-based row on that side.  Left-anchored marks (coda/segno) draw at the
 * stave's left edge, right-anchored ones at its right edge, matching the side
 * VexFlow would have used.  An unknown key (should not happen -- the caller
 * only pushes keys resolved through NAVIGATION_MARKS) is skipped.
 */
export function drawNavOverflowMarks(
  d3: any,
  svg: any,
  stave: any,
  plans: Array<{ key: string; row: number }>,
  isDarkMode: boolean,
): void {
  if (!plans || plans.length === 0) return;
  const textFill = musicInkColor(isDarkMode);
  const topLineY = typeof stave.getYForLine === 'function' ? stave.getYForLine(0) : 20;
  const staveX = typeof stave.getX === 'function' ? stave.getX() : 10;
  const staveW = typeof stave.getWidth === 'function' ? stave.getWidth() : 0;
  const leftX = staveX + 10;
  const rightX = staveX + staveW - 8;
  for (const { key, row } of plans) {
    const label = NAV_OVERLAY_LABELS[key];
    if (!label) continue;
    const isLeft = label.side === 'left';
    // Row 1 sits just above the top line; each further row is 14px higher, so
    // the stack rises toward (but stays below) the VexFlow-drawn primary.
    const y = topLineY - 8 - (Math.max(1, row) - 1) * 14;
    svg.append('text')
      .attr('x', isLeft ? leftX : rightX)
      .attr('y', y)
      .attr('text-anchor', isLeft ? 'start' : 'end')
      .attr('fill', textFill)
      .style('font', '700 13px "Times New Roman", "Segoe UI Symbol", Georgia, serif')
      .text(label.text);
  }
}

/**
 * The leading "tr" glyph of a trill line, hand-drawn above a note.
 *
 * A published trill is a "tr" FOLLOWED by a wavy line -- a bare squiggle reads
 * as vibrato, not a trill.  VexFlow's VibratoBracket draws only the wave, so
 * the "tr" has to come from elsewhere.  The obvious mechanism -- attaching a
 * `tr` Ornament to the start note before format -- was tried (iter3) and
 * verified INEFFECTIVE against a fresh bundle: the modifier compiled and
 * deployed but drew nothing, because the ornament was added to the note after
 * its ModifierContext had been built during the voice's own pass, so the late
 * addition never entered the layout.  Rather than fight VexFlow's modifier
 * timing, the "tr" is hand-drawn here in the post-format overlay pass, exactly
 * like the dynamics / measure-number / volta / lyric layers the plugin has
 * standardised on: reading the note's resolved x guarantees it renders and
 * lands immediately to the LEFT of where the wave begins, so the pair reads as
 * "tr~~~~~".  An italic serif "tr" stands in for the SMuFL ornament glyph, the
 * same approximation drawDynamicsLayer makes for the dynamic glyphs, since the
 * real glyph is a VexFlow-internal vector path unusable in a d3 <text>.
 */
export function drawTrillGlyph(
  d3: any,
  svg: any,
  stave: any,
  note: any,
  isDarkMode: boolean,
): void {
  const textFill = musicInkColor(isDarkMode);
  const x = note && typeof note.getAbsoluteX === 'function' ? note.getAbsoluteX() : null;
  if (x == null) return;
  // Sit just above the top stave line, on the same band the VibratoBracket
  // wave occupies, and end a few px LEFT of the notehead so the wave (which
  // starts at the note) runs on from it rather than through it.
  const topLineY = typeof stave.getYForLine === 'function' ? stave.getYForLine(0) : 20;
  svg.append('text')
    .attr('x', x - 4).attr('y', topLineY - 12)
    .attr('text-anchor', 'end')
    .attr('fill', textFill)
    .style('font', 'italic 700 13px "Times New Roman", Georgia, serif')
    .text('tr');
}

/**
 * Breath / caesura marks drawn ABOVE the staff, just AFTER the note they
 * follow.
 *
 * A breath is engraved after a note, at the top of the staff -- a wind or
 * vocal phrasing break, or (the caesura) the "railroad-tracks" grand pause.
 * VexFlow ships no breath-mark primitive that engraves after the note, so --
 * like the trill glyph, dynamics, lyric and volta layers -- it is hand-drawn
 * with d3 in the post-format overlay pass, reading each note's resolved x so
 * the mark lands immediately to the RIGHT of its notehead (and left of the
 * next note, so it reads as a break rather than an accent on the next note).
 *
 * The comma uses U+2019 (a raised comma) as a stand-in for the SMuFL
 * breathMarkComma glyph, which is a VexFlow-internal vector path unusable in a
 * d3 <text> -- the same approximation drawDynamicsLayer / drawTrillGlyph make.
 * The tick and caesura are stroke shapes and are drawn as line segments.
 */
export function drawBreathMarks(
  d3: any,
  svg: any,
  stave: any,
  renderedNotes: any[],
  specNotes: MusicNoteSpec[],
  isDarkMode: boolean,
): void {
  const textFill = musicInkColor(isDarkMode);
  const topLineY = typeof stave.getYForLine === 'function' ? stave.getYForLine(0) : 20;
  const xOf = (note: any): number | null =>
    note && typeof note.getAbsoluteX === 'function' ? note.getAbsoluteX() : null;

  renderedNotes.forEach((note, i) => {
    const raw = specNotes[i]?.breath;
    if (raw == null || raw === false) return;
    // Resolve the friendly value to a kind + scale.  `true` is the comma
    // shorthand; a number is an explicit comma scale (clamped to 0.3..1); a
    // string is looked up in BREATH_MARKS, unknown -> skip with a warning.
    let kind: 'comma' | 'tick' | 'caesura' | 'caesura-curved' = 'comma';
    let scale = 1;
    if (raw === true) {
      // comma, scale 1
    } else if (typeof raw === 'number') {
      scale = Math.max(0.3, Math.min(1, raw));
    } else {
      const resolved = BREATH_MARKS[String(raw).toLowerCase()];
      if (!resolved) {
        console.warn(`musicPlugin: unknown breath "${raw}" skipped`);
        return;
      }
      kind = resolved;
    }

    const x = xOf(note);
    if (x == null) return;
    // Sit just after the notehead, but stay left of the next note so the mark
    // reads as a break belonging to THIS note rather than an accent on the next.
    const nextX = i + 1 < renderedNotes.length ? xOf(renderedNotes[i + 1]) : null;
    const bx = nextX != null && nextX > x ? Math.min(x + 14, (x + nextX) / 2) : x + 12;
    // Band just above the top stave line, the same as the trill/harp overlays.
    const y = topLineY - 6;

    if (kind === 'comma') {
      svg.append('text')
        .attr('x', bx).attr('y', y)
        .attr('text-anchor', 'middle')
        .attr('fill', textFill)
        .style('font', `700 ${Math.round(18 * scale)}px "Times New Roman", Georgia, serif`)
        .text('\u2019');
    } else if (kind === 'tick') {
      const h = 10 * scale;
      svg.append('line')
        .attr('x1', bx - 2 * scale).attr('x2', bx + 3 * scale)
        .attr('y1', y).attr('y2', y - h)
        .attr('stroke', textFill).attr('stroke-width', 1.5);
    } else {
      // caesura: two parallel strokes marking a grand pause.  The straight
      // "railroad-tracks" variant draws them as diagonal line segments; the
      // "caesura-curved" variant bows each stroke into the alternate published
      // curved-caesura glyph.  Same two-stroke geometry, so the straight path
      // stays byte-identical (a plain <line> with the original coordinates).
      const h = 12 * scale;
      const gap = 4 * scale;
      const curved = kind === 'caesura-curved';
      for (const dx of [-gap, gap]) {
        const x0 = bx + dx - 2 * scale; // bottom-left foot
        const x1 = bx + dx + 3 * scale; // top-right head
        if (curved) {
          // Bow the stroke with a quadratic whose control point is pushed to
          // the left of the chord, so the pair reads as two curved commas
          // rather than straight ticks.
          const cx = x0 - 3 * scale;
          const cy = y - h / 2;
          svg.append('path')
            .attr('d', `M ${x0} ${y} Q ${cx} ${cy} ${x1} ${y - h}`)
            .attr('fill', 'none')
            .attr('stroke', textFill).attr('stroke-width', 1.5);
        } else {
          svg.append('line')
            .attr('x1', x0).attr('x2', x1)
            .attr('y1', y).attr('y2', y - h)
            .attr('stroke', textFill).attr('stroke-width', 1.5);
        }
      }
    }
  });
}

/**
 * Cue-note scaling: shrink a note engraved with `cue` to ~2/3 size in place.
 *
 * A cue note keeps its beat time -- VexFlow lays it out full size and it
 * occupies real rhythmic space in the bar -- and only its SIZE changes, which
 * is why this is a post-format visual transform rather than a different note
 * construction: the layout (horizontal spacing, stem direction, accidentals,
 * ledger lines) is the ordinary full-size one, and the already-drawn glyph
 * group is scaled down around its own notehead so notehead, stem and flag
 * shrink together and stay internally consistent.  VexFlow ships no cue-note
 * primitive (GraceNote is close but drops beat time), and the per-element
 * fontScale does NOT cascade from a StaveNote to its child noteheads/stem
 * (verified against vexflow 5.0.0: noteheads size from `fontInfo`, built at
 * construction, not from the note's fontScale), so -- exactly like the
 * dynamics / trill / breath layers -- the effect is applied here by
 * transforming the note's rendered <g class="vf-stavenote"> element.
 *
 * The transform is anchored at the notehead (getAbsoluteX / the first
 * resolved y) so the head stays put while the stem and flag pull inward,
 * rather than the whole note sliding toward the canvas origin a bare
 * `scale()` would cause.  A prior transform (a dark-theme rotation etc.) is
 * preserved by prepending, never replaced.
 *
 * `true` is the 2/3 default; a number is clamped to 0.3..1.  A resolved scale
 * of >= 1 is a no-op (nothing to shrink), so a full-size request costs
 * nothing and a note without `cue` is never touched -- keeping the
 * no-cue path byte-identical.
 */
export function drawCueNotes(
  svg: any,
  renderedNotes: any[],
  specNotes: MusicNoteSpec[],
): void {
  const root: SVGElement | null = typeof svg?.node === 'function' ? svg.node() : null;
  if (!root) return;
  renderedNotes.forEach((note, i) => {
    const raw = specNotes[i]?.cue;
    if (raw == null || raw === false) return;
    const scale = raw === true ? 0.66 : Math.max(0.3, Math.min(1, Number(raw)));
    // Non-finite (a bad number) or a full-size request -- nothing to do.
    if (!Number.isFinite(scale) || scale >= 1) return;
    // The note's rendered group is `vf-<id>` (SVGContext.openGroup prefixes
    // the note's own id with "vf-"; see stavenote.js draw()).  An attribute
    // selector rather than `#vf-…` so an id that begins with a digit still
    // matches.
    const id = typeof note.getAttribute === 'function' ? note.getAttribute('id') : null;
    if (!id) return;
    const group = root.querySelector(`[id="vf-${id}"]`) as SVGElement | null;
    if (!group) return;
    // Anchor at the notehead so the shrink is centred there.
    let cx: number | null = null;
    let cy: number | null = null;
    try {
      if (typeof note.getAbsoluteX === 'function') cx = note.getAbsoluteX();
      const ys = typeof note.getYs === 'function' ? note.getYs() : null;
      if (Array.isArray(ys) && ys.length > 0) cy = ys[0];
    } catch {
      // getYs throws before layout; a cue note without resolved geometry is
      // left full size rather than mis-placed.
      return;
    }
    if (cx == null || cy == null) return;
    const prior = group.getAttribute('transform');
    const shrink = `translate(${cx},${cy}) scale(${scale}) translate(${-cx},${-cy})`;
    group.setAttribute('transform', prior ? `${prior} ${shrink}` : shrink);
  });
}

/**
 * True when a measure's LONE tickable is a bare, whole-bar note or rest that
 * published engraving CENTERS in its measure rather than jamming against the
 * clef.
 *
 * A measure holding a single note/rest that fills the whole bar -- most
 * commonly a whole rest (an "empty" bar) or a whole note in common time -- is
 * centered over the measure in every published house style (Gould, "Behind
 * Bars"): there is no rhythmic reason to place it anywhere else, and a
 * left-clustered whole note reads as an early downbeat.  VexFlow's Formatter,
 * given a system wider than the single tickable's minimum width, does NOT
 * re-center it -- it leaves the lone note at the left start-x with the rest of
 * the bar empty (verified against vexflow 5.0.0: `{notes:[{keys:["c/5"],
 * duration:"w"}]}` rendered the whole note hard against the 4/4 signature with
 * ~250px of blank staff running to the closing barline).
 *
 * Deliberately STRICT so it fires only on that unambiguous case and leaves
 * every other layout byte-identical:
 *   - the tickable must FILL the whole bar: a whole REST fills any meter (the
 *     whole-bar-rest convention), a whole NOTE only common time (4/4 or C),
 *     so a half note in 4/4 -- a legitimately underfull bar -- is NOT moved;
 *   - it must be BARE: any decoration drawn by a post-format overlay that
 *     reads the note's resolved x (dynamic, lyric, chord symbol, harp pedal,
 *     breath, cue, annotation, articulation, ornament, grace, fingering,
 *     string number) would be stranded at the un-centered x if the note moved,
 *     so a decorated note is left exactly where VexFlow placed it.
 *
 * Pure/DOM-free so the decision can be unit-tested without a renderer; the
 * geometry (where the centre is) lives in centerLoneWholeBar.
 */
export function shouldCenterLoneWholeBar(
  specNote: MusicNoteSpec | undefined,
  numBeats: number,
  beatValue: number,
): boolean {
  if (!specNote) return false;
  const { base } = sanitizeDuration(specNote.duration);
  if (base !== 'w') return false;                 // only a whole note/rest fills a bar
  const isRest = Boolean(specNote.rest) || !specNote.keys?.length;
  // A whole note only fills common time; a whole rest is the whole-bar rest in
  // any meter.
  if (!isRest && !(numBeats === 4 && beatValue === 4)) return false;
  // Any x-reading overlay or decoration disqualifies it: moving the note would
  // strand the overlay at the old formatted x (the overlays read getAbsoluteX,
  // which reports where VexFlow placed the note, not this post-hoc transform).
  const decorated =
    specNote.dynamic != null || specNote.lyric != null
    || specNote.chordSymbol != null || specNote.harpPedal != null
    || specNote.breath != null || specNote.cue != null
    || specNote.tremolo != null || specNote.arpeggio != null
    || specNote.fingering != null || specNote.stringNumber != null
    || (Array.isArray(specNote.annotations) && specNote.annotations.length > 0)
    || (Array.isArray(specNote.articulations) && specNote.articulations.length > 0)
    || (Array.isArray(specNote.ornaments) && specNote.ornaments.length > 0)
    || (Array.isArray(specNote.graceNotes) && specNote.graceNotes.length > 0);
  return !decorated;
}

/**
 * Centre a measure's lone whole-bar note/rest within its measure by
 * translating its rendered group to the measure's horizontal midpoint.
 *
 * Same group-transform mechanism as drawCueNotes (the note's rendered
 * `vf-<id>` group), applied in the post-format overlay pass so the note's
 * resolved x already exists.  The target is the geometric centre of the
 * stave's note area (getNoteStartX .. getNoteEndX) -- no tuning constant: a
 * whole-bar note sits exactly halfway between the end of the clef/key/time
 * block and the closing barline, as published scores set it.  A prior
 * transform is preserved by prepending (the cue shrink is mutually excluded by
 * shouldCenterLoneWholeBar, but a future overlay may add one).  No-ops when the
 * note is already centred (|dx| tiny) or its geometry is unavailable, so a
 * correctly-placed note is byte-identical.
 */
export function centerLoneWholeBar(svg: any, stave: any, note: any): void {
  const root: SVGElement | null = typeof svg?.node === 'function' ? svg.node() : null;
  if (!root || !note || !stave) return;
  const startX = typeof stave.getNoteStartX === 'function' ? stave.getNoteStartX() : null;
  const endX = typeof stave.getNoteEndX === 'function' ? stave.getNoteEndX() : null;
  if (startX == null || endX == null || !(endX > startX)) return;
  let cur: number | null = null;
  try {
    if (typeof note.getAbsoluteX === 'function') cur = note.getAbsoluteX();
  } catch {
    return;
  }
  if (cur == null || !Number.isFinite(cur)) return;
  const target = (startX + endX) / 2;
  const dx = target - cur;
  if (!Number.isFinite(dx) || Math.abs(dx) < 2) return;   // already centred
  const id = typeof note.getAttribute === 'function' ? note.getAttribute('id') : null;
  if (!id) return;
  const group = root.querySelector(`[id="vf-${id}"]`) as SVGElement | null;
  if (!group) return;
  const prior = group.getAttribute('transform');
  const shift = `translate(${dx},0)`;
  group.setAttribute('transform', prior ? `${shift} ${prior}` : shift);
}

/**
 * Repeat-ending (volta) bracket, drawn over a measure range on one system.
 *
 * VexFlow's `Stave.setVoltaType` is a stave modifier: it can only draw the
 * bracket across the ENTIRE stave, which -- because a system is a single
 * horizontal stave carrying all its measures -- meant the "1." bracket
 * stretched over every bar of the line instead of over just the ending it
 * names.  A volta is a measure-scoped marking, so like the harp-pedal, lyric,
 * title and staff-label layers it is hand-drawn with d3 AFTER formatting,
 * reading the resolved x of the first and last notes of its range so the
 * bracket begins and ends exactly where those bars do.
 *
 * `type` selects which vertical end-hooks are drawn, mirroring engraving
 * convention: "begin"/"begin-end"/"end" have a left/both/right hook, "mid"
 * none.  The label sits just inside the left hook.  Drawn a fixed distance
 * above the top staff line so successive systems' voltas share one band.
 */
export function drawVoltaBracket(
  d3: any,
  svg: any,
  stave: any,
  volta: MusicVolta,
  fromNote: any,
  toNote: any,
  isDarkMode: boolean,
  // True when the ending's LAST measure is the final measure of its system, so
  // its closing barline is the stave's own right edge rather than a barline
  // between two bars.  Defaults false, keeping the interior-ending path (and
  // the legacy test caller) byte-identical.
  endsSystem: boolean = false,
): void {
  const textFill = musicInkColor(isDarkMode);
  const xOf = (note: any): number | null =>
    note && typeof note.getAbsoluteX === 'function' ? note.getAbsoluteX() : null;
  const startX = xOf(fromNote);
  const endNoteX = xOf(toNote);
  if (startX == null || endNoteX == null) return;
  // Start a touch left of the first notehead so the bracket opens at the
  // barline, and extend past the last notehead toward its bar's end.
  const x1 = startX - 10;
  // Right edge of the stave (its closing barline).  A volta on the FINAL
  // measure of a system has no note-to-its-right to size against, so the fixed
  // `endNoteX + 24` guess either COLLAPSED the bracket (a one- or two-note
  // final ending stopped ~24px past its notehead, far short of the barline) or
  // OVERSHOT it (a full final bar pushed the right hook past the stave's end).
  // When the ending closes the system, run the bracket to the stave's right
  // edge so the right hook lands on the final barline as published scores set
  // it; otherwise never let it cross that edge.
  const staveRightX =
    typeof stave.getX === 'function' && typeof stave.getWidth === 'function'
      ? stave.getX() + stave.getWidth()
      : null;
  let x2 = endNoteX + 24;
  if (staveRightX != null) {
    x2 = endsSystem ? staveRightX - 2 : Math.min(x2, staveRightX - 2);
  }
  // Sit above the staff, clear of any note that pokes above the top line.
  const topLineY = typeof stave.getYForLine === 'function' ? stave.getYForLine(0) : 20;
  const y = topLineY - 22;
  const hookDepth = 10;

  const type = volta.type ?? 'begin';
  const leftHook = type === 'begin' || type === 'begin-end';
  const rightHook = type === 'end' || type === 'begin-end';

  // Horizontal top rail.
  svg.append('line')
    .attr('x1', x1).attr('x2', x2)
    .attr('y1', y).attr('y2', y)
    .attr('stroke', textFill).attr('stroke-width', 1.5);
  if (leftHook) {
    svg.append('line')
      .attr('x1', x1).attr('x2', x1)
      .attr('y1', y).attr('y2', y + hookDepth)
      .attr('stroke', textFill).attr('stroke-width', 1.5);
  }
  if (rightHook) {
    svg.append('line')
      .attr('x1', x2).attr('x2', x2)
      .attr('y1', y).attr('y2', y + hookDepth)
      .attr('stroke', textFill).attr('stroke-width', 1.5);
  }
  if (volta.label) {
    svg.append('text')
      .attr('x', x1 + 6).attr('y', y + hookDepth + 1)
      .attr('text-anchor', 'start')
      .attr('fill', textFill)
      .style('font', '400 12px "Times New Roman", Georgia, serif')
      .text(volta.label);
  }
}

/**
 * Sustain-pedal marking, drawn below the staff over a press..release range.
 *
 * Placed on its own band beneath the dynamics band (drawDynamicsLayer sits at
 * bottomLine + 24), following published piano engraving where the pedal line
 * is the lowest marking under the staff.  Like drawVoltaBracket it reads the
 * resolved x of the press and release notes, so it must run in the post-format
 * overlay pass; and like the other overlays it picks its own theme-aware ink
 * and must not be run through the dark-mode remap.
 *
 * The damper (sustain) pedal is drawn in one of three published styles:
 *   "bracket" (default) -- a horizontal rail with a short leg dropping at each
 *     end (the press and release), the modern Dorico/Henle line notation.
 *   "text" -- "Ped." at the press and a "*" at the release, the older piano
 *     notation.
 *   "mixed" -- "Ped." at the press then a bracket to the release, the hybrid.
 *
 * The other two piano pedals are reached by `pedal` and print their own fixed
 * wording from PEDAL_TYPE_LABELS instead (ignoring `style`): "sostenuto"
 * engraves "Sost. Ped." ... "*", "una-corda" engraves "una corda" ... "tre
 * corde".  An optional `line` drops the whole marking further below the staff
 * to clear a lyric or dynamic sharing the band.
 */
export function drawPedalLine(
  d3: any,
  svg: any,
  stave: any,
  pedal: MusicPedal,
  fromNote: any,
  toNote: any,
  isDarkMode: boolean,
): void {
  const textFill = musicInkColor(isDarkMode);
  const xOf = (note: any): number | null =>
    note && typeof note.getAbsoluteX === 'function' ? note.getAbsoluteX() : null;
  const startX = xOf(fromNote);
  const endX = xOf(toNote);
  if (startX == null || endX == null) return;
  // Bottom stave line; the pedal sits a fixed distance below it, clear of the
  // dynamics band (bottomLine + 24) so the two never overprint.  `line` drops
  // it further to clear a lyric/dynamic sharing the band; a value <= 0 is no
  // drop, so the default (no-`line`) placement is byte-identical.
  const bottomLineY = typeof stave.getYForLine === 'function' ? stave.getYForLine(4) : 60;
  const spacing = (typeof stave.getYForLine === 'function'
    ? stave.getYForLine(4) - stave.getYForLine(3)
    : 10) || 10;
  const drop = typeof pedal.line === 'number' && pedal.line > 0 ? pedal.line * spacing : 0;
  const y = bottomLineY + 40 + drop;

  const textFont = 'italic 700 13px "Times New Roman", Georgia, serif';
  const starFont = '700 14px "Times New Roman", Georgia, serif';
  const pressText = (label: string): void => {
    svg.append('text')
      .attr('x', startX).attr('y', y + 4)
      .attr('text-anchor', 'start')
      .attr('fill', textFill)
      .style('font', textFont)
      .text(label);
  };
  const releaseText = (label: string, star: boolean): void => {
    svg.append('text')
      .attr('x', endX + 6).attr('y', y + 4)
      .attr('text-anchor', 'middle')
      .attr('fill', textFill)
      .style('font', star ? starFont : textFont)
      .text(label);
  };

  // A named non-sustain pedal (sostenuto / una-corda) prints its own fixed
  // engraved wording and ignores `style`, per published convention.
  const pedalType = pedal.pedal ?? 'sustain';
  if (pedalType !== 'sustain') {
    const labels = PEDAL_TYPE_LABELS[pedalType];
    if (!labels) {
      console.warn(`musicPlugin: unknown pedal "${pedalType}" skipped`);
      return;
    }
    const [pressLabel, releaseLabel] = labels;
    pressText(pressLabel);
    if (endX > startX && releaseLabel) {
      // The sostenuto release is a lone "*", the una-corda release is the
      // words "tre corde"; only the star gets the heavier glyph font.
      releaseText(releaseLabel, releaseLabel === '*');
    }
    return;
  }

  const style = pedal.style ?? 'bracket';
  if (style === 'text') {
    // "Ped." at the press, "*" at the release -- the older piano notation.
    pressText('Ped.');
    if (endX > startX) releaseText('*', true);
    return;
  }

  // Bracket / mixed style: a horizontal rail with short legs dropping at its
  // ends.  For "mixed" the rail is preceded by a "Ped." word, so the rail
  // starts after the text rather than at the press notehead; plain "bracket"
  // opens the rail a touch left of the press notehead as before.
  const legDepth = 8;
  let x1 = startX - 4;
  if (style === 'mixed') {
    pressText('Ped.');
    // Clear the ~24px "Ped." glyph so the rail begins after the word.
    x1 = startX + 26;
  }
  const x2 = Math.max(x1 + 8, endX + 8);
  svg.append('line')
    .attr('x1', x1).attr('x2', x2)
    .attr('y1', y).attr('y2', y)
    .attr('stroke', textFill).attr('stroke-width', 1.5);
  for (const lx of [x1, x2]) {
    svg.append('line')
      .attr('x1', lx).attr('x2', lx)
      .attr('y1', y).attr('y2', y + legDepth)
      .attr('stroke', textFill).attr('stroke-width', 1.5);
  }
}

/**
 * Multi-measure rest: the thick horizontal H-bar with a bar count above it,
 * drawn centred in an otherwise-empty measure.
 *
 * VexFlow ships a `MultiMeasureRest`, but it is a stave-attached modifier that
 * positions itself from the stave's own geometry -- it does not fit the
 * System/voice pipeline this renderer formats through (the measure is a slice
 * of one horizontal System stave, not a stave of its own), so it would draw
 * across the whole line rather than over the one empty measure.  So -- exactly
 * like the pedal, volta, dynamics and trill-glyph layers -- the symbol is
 * hand-drawn with d3 in the post-format overlay pass, reading the measure's
 * ghost-note spacer's resolved x so the bar sits over just that measure.
 *
 * The published symbol is a thick horizontal beam centred on the staff's
 * middle line, capped by short thick verticals at each end (spanning roughly
 * the 2nd-to-4th lines), with the number of consolidated bars printed in bold
 * above the top line.  `centerX` is the spacer's resolved x; the bar is drawn
 * symmetrically about it and clamped to the stave's note area so it never
 * spills past the barlines.
 */
export function drawMultiMeasureRest(
  d3: any,
  svg: any,
  stave: any,
  centerX: number,
  count: number,
  isDarkMode: boolean,
): void {
  const textFill = musicInkColor(isDarkMode);
  const lineY = (n: number): number =>
    typeof stave.getYForLine === 'function' ? stave.getYForLine(n) : 20 + n * 10;
  const topLineY = lineY(0);
  const midLineY = lineY(2);
  const spacing = (lineY(4) - topLineY) / 4 || 10;

  // Horizontal extent: symmetric about the spacer x, ~half a measure wide, but
  // clamped to the stave's note area so the bar cannot overrun the barlines on
  // a narrow measure.
  const staveStart = typeof stave.getNoteStartX === 'function'
    ? stave.getNoteStartX()
    : (typeof stave.getX === 'function' ? stave.getX() : centerX - 60);
  const staveEnd = typeof stave.getNoteEndX === 'function'
    ? stave.getNoteEndX()
    : (typeof stave.getX === 'function' && typeof stave.getWidth === 'function'
        ? stave.getX() + stave.getWidth()
        : centerX + 60);
  const halfWidth = 44;
  const x1 = Math.max(staveStart + 4, centerX - halfWidth);
  const x2 = Math.min(staveEnd - 4, centerX + halfWidth);

  // Thick horizontal beam on the middle line, roughly one staff-space tall --
  // the published H-bar body.
  const barThickness = Math.max(4, spacing * 0.9);
  svg.append('rect')
    .attr('x', x1).attr('y', midLineY - barThickness / 2)
    .attr('width', Math.max(0, x2 - x1)).attr('height', barThickness)
    .attr('fill', textFill).attr('stroke', 'none');

  // End caps: short thick verticals from the 2nd line to the 4th line.
  const capTop = lineY(1);
  const capBottom = lineY(3);
  for (const cx of [x1, x2]) {
    svg.append('line')
      .attr('x1', cx).attr('x2', cx)
      .attr('y1', capTop).attr('y2', capBottom)
      .attr('stroke', textFill).attr('stroke-width', 3);
  }

  // Bar count, bold, centred above the top line -- the number the player counts.
  svg.append('text')
    .attr('x', (x1 + x2) / 2).attr('y', topLineY - 10)
    .attr('text-anchor', 'middle')
    .attr('fill', textFill)
    .style('font', '700 15px "Times New Roman", Georgia, serif')
    .text(String(count));
}

/**
 * Horizontal layout constants for system-break planning.
 *
 * These mirror the width estimate the canvas sizing has always used
 * (`110 + notes * 78 + barlines * 24`), factored out so the planner and the
 * canvas cannot disagree about how wide a system will be.  They are estimates,
 * not measurements: VexFlow owns real glyph metrics and only knows them after
 * formatting, which is far too late to decide where lines break.
 */
/** Clef + key + time signature + margins consumed before the first note. */
const SYSTEM_LEAD_IN_PX = 110;
/** Rough horizontal room one note occupies. */
const MEASURE_NOTE_PX = 78;
/** A barline is a tickable too and takes its own slot. */
const BARLINE_PX = 24;
/** Default width budget for one system before an automatic break. */
const DEFAULT_MAX_SYSTEM_WIDTH = 1200;
/** Default vertical gap between stacked systems. */
const DEFAULT_SYSTEM_SPACING = 36;

/**
 * Crash-guard bounds for the author-set canvas/layout dimensions
 * (`width`, `height`, `maxSystemWidth`, `systemSpacing`).
 *
 * These four spec fields flow STRAIGHT into the layout math and the VexFlow
 * `Factory` renderer -- `new Factory({renderer:{width, height}})`, the
 * planSystemBreaks budget, and the per-system y-advance -- with no numeric
 * guard, unlike every note duration (sanitizeDuration), meter (sanitizeMeter,
 * D22), key signature (sanitizeKeySignature, D23), tempo (D25) and octave
 * (clampKeyOctave, D26).  A degenerate value is therefore a render failure of
 * the same class:
 *   - NaN / non-finite -> the SVG gets width="NaN"/height="NaN"; VexFlow's
 *     draw() produces nothing (blank canvas), matching the other unguarded
 *     numeric failures' signature.
 *   - 0 or negative -> an empty / invalid SVG viewport (0-height draws nothing;
 *     a negative dimension is rejected by the SVG backend).
 *   - astronomically large (e.g. width: 1e9) -> a multi-gigapixel SVG that
 *     OOMs / hangs the headless renderer -- the render-timeout crash the
 *     numeric sweep targets.
 * MAX_CANVAS_DIM is far above any legitimate wrapped score (LEGIBILITY_WIDTH_
 * LIMIT, the point notation stops being readable, is ~2200px) yet safely
 * renderable, so a real layout is never clamped -- only a runaway one.
 */
const MAX_CANVAS_DIM = 16000;
/** A single narrow measure still needs this much canvas to draw its clef. */
const MIN_CANVAS_WIDTH = 120;
/** One five-line stave plus its tail. */
const MIN_CANVAS_HEIGHT = 60;

/**
 * Clamp an author-set layout dimension to a finite, sane range BEFORE it
 * reaches the layout math or the Factory renderer.
 *
 * Returns `undefined` for an ABSENT (null/undefined) or NON-FINITE value, so
 * the caller's own `?? default` / `== null` recovery runs -- a garbage `width`
 * is thus treated as "no width given" (re-enabling automatic wrapping and
 * content-sizing) rather than pinning a broken canvas.  A finite but
 * out-of-range value is clamped into `[min, max]` and returned, with a console
 * warning (matching the plugin's degenerate-value convention).  An absent
 * value returns `undefined` silently and an in-range value is returned
 * verbatim, so every valid or unset spec is byte-identical.
 *
 * Exported pure/DOM-free for regression testing.
 */
export function sanitizeLayoutDimension(
  value: number | undefined | null,
  min: number,
  max: number,
  name: string,
): number | undefined {
  if (value == null) return undefined;
  const n = Number(value);
  if (!Number.isFinite(n)) {
    console.warn(`musicPlugin: non-finite ${name} "${value}" ignored`);
    return undefined;
  }
  const clamped = Math.max(min, Math.min(max, n));
  if (clamped !== n) {
    console.warn(
      `musicPlugin: ${name} ${n} is outside [${min}, ${max}]; clamped to `
      + `${clamped} to avoid a degenerate canvas`,
    );
  }
  return clamped;
}

/**
 * Width at which a single system stops being readable.
 *
 * A chat column is ~760px, and notation scaled below ~35% is not legible, so a
 * system wider than 760/0.35 is reported.  Used only for the advisory warning
 * on the path that opts out of wrapping (an explicit `width`); the wrapping
 * path prevents the situation instead of describing it.
 */
const REFERENCE_COLUMN_PX = 760;
const MIN_LEGIBLE_SCALE = 0.35;
const LEGIBILITY_WIDTH_LIMIT = Math.round(REFERENCE_COLUMN_PX / MIN_LEGIBLE_SCALE);

/**
 * How much horizontal room one note claims, as a fraction of a full slot.
 *
 * The flat `noteCount * MEASURE_NOTE_PX` model charged every note the same
 * slot, so a bar of sixteen 16ths estimated as wide as sixteen quarters
 * (16 * 78 = 1248px) and busted the 1200px budget on its own -- each dense bar
 * was stranded on its own system while published scores fit two, three or four
 * such bars per line.  A beamed run of 16ths (and shorter) sits far tighter
 * under its extra beams than a row of quarters, so those durations claim only
 * a fraction of a slot.
 *
 * w/h/q/8 are deliberately left at 1.0 -- the flat model's spacing for whole/
 * half/quarter/eighth notes was already tuned and is pinned by the wrap and
 * parity suites (E8 == 624px, bars(3,E8) == 3 systems), so any score built
 * only from those durations keeps its exact previous layout.  Only sub-eighth
 * durations, which never appeared in those fixtures, are down-weighted.  The
 * numeric aliases ("1"/"2"/"4") mirror w/h/q, matching VALID_DURATION_BASES.
 */
const DURATION_WIDTH_WEIGHT: Readonly<Record<string, number>> = {
  w: 1, h: 1, q: 1, '8': 1, '1': 1, '2': 1, '4': 1,
  '16': 0.4, '32': 0.3, '64': 0.25, '128': 0.25,
};

/**
 * Slot fraction for a spec duration.  Routed through sanitizeDuration so a
 * dotted or aliased code resolves to its base, and an unknown code falls back
 * to a full slot (1.0) -- the same conservative default the flat model used.
 */
function noteWidthWeight(duration: string | number): number {
  const { base } = sanitizeDuration(duration);
  return DURATION_WIDTH_WEIGHT[base] ?? 1;
}

/**
 * Duration-weighted width estimate for one measure's notes.
 *
 * Sums each note's slot fraction rather than counting notes, so a bar of
 * sixteen beamed 16ths (16 * 0.4 == 6.4 slots) estimates near a bar of eight
 * eighths instead of sixteen quarters, letting several dense bars share a line
 * (M4).  An empty measure still reserves one slot (MEASURE_NOTE_PX) for its
 * barline and padding, the same floor the flat count-based estimate used.
 */
function estimateMeasureWidthFromNotes(notes: MusicNoteSpec[]): number {
  if (!notes || notes.length === 0) return MEASURE_NOTE_PX;
  const slots = notes.reduce((sum, n) => sum + noteWidthWeight(n.duration), 0);
  return Math.max(1, slots) * MEASURE_NOTE_PX;
}

/**
 * Validate a `beamGroups` list, dropping degenerate pairs BEFORE they reach
 * VexFlow's Fraction / Beam.generateBeams machinery.
 *
 * Each pair becomes `new Fraction(n, d)` handed to Beam.generateBeams as a
 * beat-group size, and each is a user-supplied numeric input reaching the tick
 * machinery -- the same un-guarded class as the tuplet num/inSpaceOf counts.
 * Two degenerate values are unrenderable, both verified against the served
 * bundle:
 *   - a ZERO (or negative) NUMERATOR makes a 0-tick group.  generateBeams
 *     accumulates note ticks until it reaches the group boundary, and a
 *     boundary at 0 ticks it can never advance past makes that loop NEVER
 *     RETURN -- an infinite loop that hangs the whole render (`[[0,2]]` ->
 *     300s+ timeout, blank canvas, total data loss).
 *   - a ZERO (or negative) DENOMINATOR makes Fraction(n,0) = Infinity ticks per
 *     group, so every note falls into one group and the meter's beaming is lost
 *     (`[[3,0]]` -> a single beam spanning the whole bar).
 * Skip the bad pair with a console warning, matching the plugin's
 * invalid-value convention (see the tuplet loop and sanitizeDuration).  When no
 * valid pair remains, return `undefined` so the caller falls back to the
 * meter's DEFAULT grouping rather than beaming the whole bar into one group.
 *
 * A list whose pairs are all valid is returned with identical contents, so the
 * ordinary autoBeam path is byte-identical.  Exported pure/DOM-free for
 * regression testing.
 */
export function sanitizeBeamGroups(
  groups: Array<[number, number]> | undefined,
): Array<[number, number]> | undefined {
  if (!Array.isArray(groups) || groups.length === 0) return undefined;
  const valid = groups.filter((g) => {
    const ok = Array.isArray(g)
      && Number.isInteger(g[0]) && g[0] > 0
      && Number.isInteger(g[1]) && g[1] > 0;
    if (!ok) {
      console.warn(
        `musicPlugin: ignoring invalid beamGroups entry ${JSON.stringify(g)}; `
        + `each must be [numerator, denominator] of positive integers`,
      );
    }
    return ok;
  });
  return valid.length > 0 ? valid : undefined;
}

/**
 * Practical upper bound for a tuplet's `num` (notes played) and `inSpaceOf`
 * (in the time of) counts.
 *
 * Even the densest published cadenza tops out well under this -- a 12- or
 * 13-tuplet is already extreme -- so 99 admits every real tuplet while keeping
 * the ratio label ("3:2") a legible one-or-two digits and, crucially, keeping
 * the per-note tick rescale `Fraction(notesOccupied, numNotes)` (applied by
 * Tuplet.attach) inside a sane range.  The cap mirrors the octave
 * (clampKeyOctave), tempo-bpm (MAX_TEMPO_BPM) and measure-number
 * (MAX_MEASURE_NUMBER) ceilings: a wildly out-of-range numeric input is not
 * real notation and is refused rather than trusted.
 */
export const MAX_TUPLET_COUNT = 99;

/**
 * Validate a tuplet's `num` / `inSpaceOf` counts BEFORE they reach VexFlow's
 * Tuplet tick machinery, returning the safe pair or `null` when the tuplet
 * must be skipped.
 *
 * The tuplet loop already refused a count below 1 or non-integer: Tuplet.attach
 * rescales every spanned note's tick by `Fraction(notesOccupied, numNotes)`, so
 * a `num` of 0 divides by zero and a negative / fractional count yields a
 * NaN/Infinity tick that hangs the Formatter's justification loop (the same
 * non-converging-formatter hang sanitizeDuration and sanitizeBeamGroups
 * defend).  But it left the UPPER bound open -- the ONE numeric spec input that
 * capped its lower bound but not its upper, unlike clampKeyOctave /
 * sanitizeTempoBpm / sanitizeMeasureNumber / sanitizeLayoutDimension.  An absurd
 * count drives that same tick rescale to a DEGENERATE value: near-zero (`num`
 * huge) re-triggers the formatter hang, and enormous (`inSpaceOf` huge) makes
 * the bar wildly overfull -- and either way VexFlow prints a ratio label such
 * as "3:1000" that runs clean off the system (verified against the served
 * bundle: `inSpaceOf: 1000` drew a "3:1000" bracket).  Above MAX_TUPLET_COUNT
 * the value is not real notation, so -- matching the tuplet loop's own
 * skip-with-a-problem-note convention for an invalid range -- the tuplet is
 * refused and its notes are left at face value rather than rescaled by garbage.
 *
 * A well-formed pair (the overwhelming case: num = member count, inSpaceOf = 2)
 * is returned verbatim, so the ordinary triplet/quintuplet path is
 * byte-identical.  Exported pure/DOM-free for regression testing.
 */
export function sanitizeTupletCounts(
  num: number,
  inSpaceOf: number,
): { num: number; inSpaceOf: number } | null {
  if (!Number.isInteger(num) || num < 1 || num > MAX_TUPLET_COUNT
      || !Number.isInteger(inSpaceOf) || inSpaceOf < 1 || inSpaceOf > MAX_TUPLET_COUNT) {
    return null;
  }
  return { num, inSpaceOf };
}

/**
 * Key signature in effect at each measure, resolving per-measure `keySignature`
 * changes forward -- the pitch analogue of the inline `effectiveMeterByMeasure`
 * build, and the enabling piece of a mid-score modulation.  A change persists
 * until the next one, exactly as a printed score reads it.  Kept as an exported
 * pure helper (unlike the inline meter resolution) so the carry-forward can be
 * unit-tested without a renderer.
 *
 * `baseKey` seeds the running key (the staff's own key, or the spec's, RAW as
 * every use-site already receives it).  A per-measure `keySignature` advances
 * the running key only when it names a signature VexFlow recognises
 * (isKnownKeySignature) -- a typo leaves the previous key in force rather than
 * silently modulating to "C", mirroring how an invalid per-measure meter
 * leaves the previous meter.  When it advances, the trimmed canonical key is
 * stored so the drawn KeySigNote and the filtered accidentals agree; the seed
 * stays raw so a score with NO key change is byte-identical to the single-key
 * path (each measure resolves to the same base value every consumer used
 * before).
 *
 * Exported pure/DOM-free for regression testing.
 */
export function resolveEffectiveKeys(
  measures: Array<{ keySignature?: string }>,
  baseKey: string | undefined,
): Array<string | undefined> {
  const out: Array<string | undefined> = [];
  let running = baseKey;
  for (const measure of measures) {
    // Advance only on a key VexFlow genuinely recognises.  isKnownKeySignature
    // (not sanitizeKeySignature) is the gate on purpose: sanitizeKeySignature
    // coerces an unrecognised value to "C" for the DRAW path, but here that
    // would silently modulate to C major on a typo -- worse than leaving the
    // previous key in force, which is what a real score does with a fat-
    // fingered signature.  Store the trimmed canonical key so the drawn
    // KeySigNote and the filtered accidentals agree.
    if (measure?.keySignature != null && isKnownKeySignature(measure.keySignature)) {
      running = measure.keySignature.trim();
    }
    out.push(running);
  }
  return out;
}

/** Estimated width of a system holding the measures at `indices`. */
function estimateSystemWidth(measureWidths: number[], indices: number[]): number {
  if (indices.length === 0) return SYSTEM_LEAD_IN_PX;
  let total = SYSTEM_LEAD_IN_PX;
  indices.forEach((m, i) => {
    total += (measureWidths[m] ?? 0) + (i > 0 ? BARLINE_PX : 0);
  });
  return total;
}

/**
 * Group measure indices into systems (lines).
 *
 * First-fit by width budget, with a forced break wherever `explicitBreaks`
 * says so.  First-fit rather than an optimal (Knuth-Plass style) fit because
 * the inputs are estimates: a "best" break chosen from approximate widths is
 * not measurably better than a greedy one, and greedy keeps the mapping from
 * measure to system obvious when a span has to be checked against it.
 *
 * Guarantees, relied on by the caller and asserted in the tests:
 *   - every measure index appears exactly once, in order;
 *   - no system is empty;
 *   - a system exceeds the budget only when it holds a single measure that
 *     cannot fit anywhere (dropping it would lose music).
 */
export function planSystemBreaks(
  measureWidths: number[],
  explicitBreaks: boolean[],
  budget: number,
): number[][] {
  if (measureWidths.length === 0) return [];
  // Floor the budget so a degenerate value still makes progress rather than
  // emitting one system per measure forever or looping.
  const effective = Math.max(SYSTEM_LEAD_IN_PX + MEASURE_NOTE_PX, budget);
  const systems: number[][] = [];
  let current: number[] = [];
  let width = SYSTEM_LEAD_IN_PX;

  for (let i = 0; i < measureWidths.length; i += 1) {
    const cost = (measureWidths[i] ?? 0) + (current.length > 0 ? BARLINE_PX : 0);
    // A break on the first measure of a system would open an empty one.
    const forced = Boolean(explicitBreaks[i]) && current.length > 0;
    const overflows = current.length > 0 && width + cost > effective;
    if (forced || overflows) {
      systems.push(current);
      current = [];
      width = SYSTEM_LEAD_IN_PX;
    }
    width += (measureWidths[i] ?? 0) + (current.length > 0 ? BARLINE_PX : 0);
    current.push(i);
  }
  if (current.length > 0) systems.push(current);
  return systems;
}

/**
 * Which system a given flat note index falls on.
 *
 * Spans (slurs, ties, hairpins, brackets...) address a staff's flat note list,
 * but VexFlow can only draw one between notes on the SAME system: given
 * endpoints on different systems it throws nothing and draws a single arc
 * sprawling down the page (measured: 197px of vertical travel between systems
 * 190px apart, versus 35px for a legitimate slur).  Correct engraving splits
 * such a span into two partial arcs, and VexFlow ships no primitive for it, so
 * the span is refused and reported instead of drawn wrongly.
 */
export function systemIndexForNote(
  systems: number[][],
  measureNoteCounts: number[],
  noteIndex: number,
): number {
  let seen = 0;
  for (let s = 0; s < systems.length; s += 1) {
    for (const measure of systems[s]) {
      seen += measureNoteCounts[measure] ?? 0;
      if (noteIndex < seen) return s;
    }
  }
  return -1;
}

/**
 * House-style cap on beam slant.
 *
 * VexFlow's slope optimiser is bounded by `renderOptions.maxSlope` /
 * `minSlope`, which default to +/-0.25 (~14 degrees) in 5.0.0 -- steeper than
 * the shallow slant modern engraving sets.  Behind Bars (Gould) keeps a beam
 * close to horizontal, its rise growing gently with the interval but capped
 * well under a quarter-slope; a run raked at the full default angle reads as
 * mechanical rather than published.  0.2 (~11 degrees) is the tighter bound:
 * shallow enough to look engraved, but still enough tilt to track an
 * ascending or descending line rather than flattening every beam dead level
 * (which is its own house-style error).
 */
const HOUSE_STYLE_MAX_BEAM_SLOPE = 0.2;

/**
 * Narrow a beam's slope bounds to the house-style cap, in place.
 *
 * Only tightens an EXISTING numeric bound the optimiser already reads during
 * draw() -- it never widens one, adds a slope the optimiser would not have
 * chosen, or touches grouping/stem side -- so a flat or already-shallow beam
 * is unchanged and a note run carrying no beam is never reached.  Applied to
 * every constructed beam (auto, secondary-voice and explicit) before any draw
 * pass, since the slope is resolved from these bounds at draw time.
 */
function applyHouseBeamSlope(beam: any): void {
  const ro = beam?.renderOptions;
  if (!ro) return;
  if (typeof ro.maxSlope === 'number') {
    ro.maxSlope = Math.min(ro.maxSlope, HOUSE_STYLE_MAX_BEAM_SLOPE);
  }
  if (typeof ro.minSlope === 'number') {
    ro.minSlope = Math.max(ro.minSlope, -HOUSE_STYLE_MAX_BEAM_SLOPE);
  }
}

/**
 * Render a MusicSpec into an SVG-capable container using VexFlow.
 */
export async function renderMusicSpec(
  container: HTMLElement,
  spec: MusicSpec,
  isDarkMode: boolean,
  d3: any,
): Promise<void> {
  const Vex = await import('vexflow');
  const {
    Factory, Annotation, Renderer, Voice,
    StaveHairpin, Articulation, Ornament, Modifier, Accidental,
    Barline, Repetition, ChordSymbol, StaveTempo, BarNote, Beam, Fraction,
    GraceNote, GraceNoteGroup, TimeSigNote, KeySigNote, Tremolo, Stroke, GhostNote,
  } = Vex as any;

  container.innerHTML = '';
  /** Non-fatal spec problems, reported together rather than failing the render. */
  const problems: string[] = [];

  // A single-staff spec is treated as a one-element multi-staff spec, so the
  // grand staff is not a second code path that can drift from the first.
  const staffSpecs: MusicStaff[] = ((spec.staves?.length ?? 0) > 0
    ? spec.staves!
    : [{
        clef: spec.clef, keySignature: spec.keySignature, notes: spec.notes,
        name: spec.name,
        measures: spec.measures,
        voices: spec.voices,
        slurs: spec.slurs, ties: spec.ties,
        glissandos: spec.glissandos, hairpins: spec.hairpins,
        brackets: spec.brackets, trillLines: spec.trillLines,
        pedals: spec.pedals,
        beams: spec.beams, tuplets: spec.tuplets,
      }])
    // Transpose a MEASURE-MAJOR multi-voice staff (`measures:[{voices:[...]}]`)
    // into the VOICE-MAJOR shape the render core reads; a voice-major or plain
    // measures-based staff is returned by reference (byte-identical).  Runs
    // BEFORE the shape canonicaliser so its output voices are already an array
    // of voice objects.
    .map(normalizeMeasureMajorVoices)
    // Canonicalise a keyed-object / bare-array `voices` spelling to the array
    // shape measuresOf and the secondary-voice loop index; a staff already in
    // that shape is returned by reference, leaving existing specs untouched.
    .map(normalizeStaffVoiceShape);

  // Count across measures, and add room for each barline: a barline is a
  // tickable too, so without the allowance the notes are squeezed to make
  // space for it.
  const longestStaff = Math.max(...staffSpecs.map((s) => notesOf(s).length));
  const mostBarlines = Math.max(
    ...staffSpecs.map((s) => Math.max(0, measuresOf(s).length - 1)),
  );
  // Left gutter reserved for instrument / part labels.  A staff name is drawn
  // to the LEFT of the clef, so the whole system must be inset by enough to
  // hold the widest label or the text runs off the canvas edge -- estimated
  // from character count (VexFlow owns the real glyph metrics, but the label
  // is our own overlay) at ~6.5px/char in the 13px label font, plus padding,
  // and capped so one very long name cannot swallow the score.  Zero when no
  // staff is named, so an unlabelled score keeps its previous layout exactly.
  const LABEL_FONT_PX_PER_CHAR = 6.5;
  const longestLabel = Math.max(
    0,
    // Both forms share the gutter, so it must fit whichever is wider.
    ...staffSpecs.map((s) => Math.max((s.name ?? '').length, (s.shortName ?? '').length)),
  );
  const labelGutter = longestLabel > 0
    ? Math.min(120, Math.round(longestLabel * LABEL_FONT_PX_PER_CHAR) + 14)
    : 0;
  // Per-measure widths, taken from the widest staff at each measure position:
  // a system must be wide enough for whichever staff needs most room there, or
  // the flute wraps in a different place from the cello and the system stops
  // being a system.  Width is DURATION-WEIGHTED, not a flat note count, so a
  // bar of beamed 16ths (which packs tight) no longer estimates as wide as a
  // bar of the same many quarters and hog a whole line to itself (M4).
  const measureCounts: number[][] = staffSpecs.map((s) =>
    measuresOf(s).map((m) => (m.notes ?? []).length));
  const measureWidthsPerStaff: number[][] = staffSpecs.map((s) =>
    // A multi-measure rest holds no notes, so estimateMeasureWidthFromNotes
    // would floor it at one slot -- too cramped for the H-bar and its count.
    // Give it a comfortable fixed span (two slots) so the symbol has room.
    measuresOf(s).map((m) => ((multiRestOf(m) ?? 0) >= 1
      ? 2 * MEASURE_NOTE_PX
      : estimateMeasureWidthFromNotes(m.notes ?? []))));
  const measureCountAcrossStaves: number[] = [];
  const measureWidths: number[] = [];
  for (let i = 0; i < Math.max(0, ...measureCounts.map((c) => c.length)); i += 1) {
    measureCountAcrossStaves.push(Math.max(0, ...measureCounts.map((c) => c[i] ?? 0)));
    // The system's width at measure i is set by whichever staff needs the most
    // room there -- the same "widest staff wins" rule the note-count max used,
    // now applied to the weighted widths so a dense inner voice still forces
    // enough room without a sparse outer voice shrinking it.
    measureWidths.push(Math.max(0, ...measureWidthsPerStaff.map((c) => c[i] ?? 0)));
  }
  // A break requested by ANY staff breaks the whole system, for the same
  // reason: staves of one system share their barlines.
  const explicitBreaks = measureCountAcrossStaves.map((_, i) =>
    staffSpecs.some((s) => Boolean(measuresOf(s)[i]?.systemBreak)));

  // An explicit `width` is the author pinning the layout, so wrapping is off
  // and the single-system behaviour is preserved byte-for-byte.  Both `width`
  // and `maxSystemWidth` are crash-guarded first (see sanitizeLayoutDimension):
  // a non-finite `width` becomes `undefined` here, which RE-ENABLES wrapping
  // and content-sizing rather than pinning a NaN/gigapixel canvas, and a
  // runaway finite value is clamped.  A valid or absent value is unchanged, so
  // the pinned-width and wrapping paths are byte-identical for real specs.
  const authorWidth = sanitizeLayoutDimension(
    spec.width, MIN_CANVAS_WIDTH, MAX_CANVAS_DIM, 'width',
  );
  const authorMaxSystemWidth = sanitizeLayoutDimension(
    spec.maxSystemWidth, MEASURE_NOTE_PX + SYSTEM_LEAD_IN_PX, MAX_CANVAS_DIM,
    'maxSystemWidth',
  );
  const wrapEnabled = authorWidth == null;
  const systemPlan = wrapEnabled
    ? planSystemBreaks(
        measureWidths, explicitBreaks,
        authorMaxSystemWidth ?? DEFAULT_MAX_SYSTEM_WIDTH,
      )
    : [measureWidths.map((_, i) => i)];
  // Degenerate specs (no measures at all) still need one system to draw into.
  const systems = systemPlan.length > 0 ? systemPlan : [[]];

  const contentWidth = wrapEnabled
    // Widest planned system, so every system shares one canvas width and the
    // right-hand margins line up down the page as engraving requires.
    ? Math.max(340, ...systems.map((sys) => estimateSystemWidth(measureWidths, sys)))
    : Math.max(340, 110 + longestStaff * 78 + mostBarlines * 24);
  const width = (authorWidth ?? contentWidth) + labelGutter;

  // T1: an over-wide single system renders successfully but illegibly, and
  // silence makes that look intentional.  Only reachable when wrapping is off
  // (an explicit `width`, or a single measure too wide to break), because the
  // wrapping path prevents it rather than reporting it.
  if (width > LEGIBILITY_WIDTH_LIMIT && systems.length === 1) {
    const scale = Math.round((REFERENCE_COLUMN_PX / width) * 100);
    problems.push(
      `system is ~${Math.round(width)}px wide and will scale to about ${scale}% ` +
      `in a ${REFERENCE_COLUMN_PX}px column, which is below the ~` +
      `${Math.round(MIN_LEGIBLE_SCALE * 100)}% needed to read notation. ` +
      (authorWidth != null
        ? 'Remove the explicit `width` to enable automatic system breaks, or '
        : 'Split the music across more measures, or ') +
      'set `maxSystemWidth`, or add `"systemBreak": true` to a measure',
    );
  }
  // Features that occupy the band BELOW the staff, so a fixed 160 clips them.
  //
  // `n.dynamic` IS listed: dynamics are engraved BELOW the staff (drawn as a
  // d3 overlay below the bottom line -- see drawDynamicsLayer), so a dynamic
  // occupies the same below-staff band a below-tuplet number does and needs
  // BELOW_STAFF_MARK_DEPTH reserved or it clips off the canvas bottom.  A
  // dynamic ALSO deepens the band when
  // LYRICS are present (drawLyricLayer drops the verse baseline from +26 to
  // +44 to clear it), which is handled where the lyric depth is computed.
  const needsRoomBelow = staffSpecs.some((s) =>
    notesOf(s).some((n) =>
      // A below-staff chord symbol (roman-numeral analysis) needs the same
      // room a lyric does.
      (typeof n.chordSymbol === 'object' && n.chordSymbol?.position === 'below')
      // Lyrics are underlaid beneath the staff and need the same headroom.
      || n.lyric != null
      // Dynamics are engraved BELOW the staff (see drawDynamicsLayer, the d3
      // overlay drawn under the bottom stave line), so they occupy the same
      // below-staff band and need room reserved for them.
      || n.dynamic != null)
    || (s.hairpins?.length ?? 0) > 0
    || (s.brackets ?? []).some((b) => b.position === 'below')
    // A below-staff tuplet number sits where a dynamic would.
    || (s.tuplets ?? []).some((t) => t.position === 'below')
    // Pedal markings are drawn on their own band below the dynamics band.
    || (s.pedals?.length ?? 0) > 0);
  // Tempo / marks / volta / measure number all render ABOVE the top staff and
  // are clipped without headroom.
  const needsRoomAbove = Boolean(
    spec.tempo || spec.mark || (spec.marks?.length ?? 0) > 0
    || spec.volta || (spec.voltas?.length ?? 0) > 0 ||
    spec.measureNumber != null || spec.measureNumbers || spec.section
    // Brackets and trill lines also occupy the band above the staff.
    || staffSpecs.some((s) =>
      (s.brackets ?? []).some((b) => b.position !== 'below')
      || (s.trillLines?.length ?? 0) > 0
      // A tuplet number defaults above the staff.
      || (s.tuplets ?? []).some((t) => t.position !== 'below')
      // Breath / caesura marks are drawn above the top stave line.
      || notesOf(s).some((n) => n.breath != null && n.breath !== false)
      // A multi-measure rest prints its bar count above the top stave line.
      || measuresOf(s).some((m) => (multiRestOf(m) ?? 0) >= 1)),
  );
  // Both the tempo lift (TEMPO_SHIFT_Y) and the bracket lift
  // (BRACKET_LINE_WITH_TEMPO) push material further up than the previous flat
  // 40px allowance covered, so a stacked tempo + bracket needs more room or
  // the topmost glyph is clipped at y<0.
  // A tempo lifted onto its own row above a navigation mark
  // (TEMPO_SHIFT_Y_WITH_MARK = -64, 30px higher than the ordinary -34 whose
  // topmost glyph sat at y≈26) needs a taller reserve or it clips at y<0.
  const tempoAboveMark = Boolean(
    spec.tempo && (spec.mark || (spec.marks?.length ?? 0) > 0),
  );
  const roomAbove = needsRoomAbove
    ? (tempoAboveMark
        ? 76
        : spec.tempo && staffSpecs.some((s) => (s.brackets?.length ?? 0) > 0) ? 60 : 46)
    : 0;
  // The title block sits above everything else, so its height is added to the
  // canvas and the whole system is pushed down by the same amount -- see
  // titleY below.  Computed once so the reserve and the draw agree.
  const titleH = titleBlockHeight(spec);
  // Crash-guarded like width/height: a non-finite systemSpacing falls back to
  // the default, a negative one clamps to 0 (systems flush), a runaway one is
  // capped so the stacked-system y-advance cannot blow the canvas height up.
  const systemSpacing = sanitizeLayoutDimension(
    spec.systemSpacing, 0, MAX_CANVAS_DIM, 'systemSpacing',
  ) ?? DEFAULT_SYSTEM_SPACING;
  /**
   * Vertical geometry of a system, split into the two roles the previous single
   * `perSystemHeight` conflated.
   *
   * The old model multiplied ONE budget by the stave count and used the result
   * both as the canvas allowance and as the y-advance between stacked systems.
   * That over-allocated, because the band below the LAST stave was counted once
   * per stave instead of once per system.  Measured dead space at the bottom of
   * the canvas grew with the score: 43% at one stave and 27% at eight on the
   * plain path; 60% and 49% on the needsRoomBelow path.
   *
   * Measured facts this model rests on (single stave, C major, 4/4):
   *   - VexFlow's actual advance between staves of a system is 120px with
   *     `spaceBetweenStaves: 12`, not the 160 that was budgeted.
   *   - Drawn content ends ~50px below the last stave's TOP line (the stave
   *     spans 40px, plus stem/beam overshoot).
   */
  const STAVE_ADVANCE = 120;
  /** Top line to bottom line of a 5-line stave. */
  const STAVE_SPAN = 40;
  /** Stem/beam overshoot below the bottom line on a plain stave. */
  const PLAIN_TAIL = 50;
  /** drawLyricLayer: a dynamic present pushes verse 1 to bottomLine + 44. */
  const LYRIC_OFFSET_WITH_DYNAMIC = 44;
  /** drawLyricLayer: no dynamic, so verse 1 sits closer at bottomLine + 26. */
  const LYRIC_OFFSET_NO_DYNAMIC = 26;
  /** drawLyricLayer: each additional verse steps down by this much. */
  const LYRIC_VERSE_STEP = 15;
  /** Descent of the 12px lyric font. */
  const LYRIC_DESCENT = 12;
  /** Below-staff chord symbols / brackets / tuplet numbers. */
  const BELOW_STAFF_MARK_DEPTH = 34;
  /**
   * drawPedalLine sits at bottomLine + 40 with an 8px leg, so the pedal band
   * reaches ~48px below the bottom line -- deeper than BELOW_STAFF_MARK_DEPTH,
   * so a spec with pedals needs its own reserve or the rail clips off-canvas.
   */
  const PEDAL_DEPTH = 50;
  /**
   * Depth below the last stave's TOP line that must stay on canvas.
   *
   * Derived from drawLyricLayer's own geometry rather than a fixed allowance,
   * because the lyric layer is drawn by d3 AFTER formatting and is therefore
   * absent from VexFlow's bounding box -- nothing else will catch an
   * under-reserve, and the failure mode is clipped text.
   *
   * Measured on real renders:
   *   - dynamics draw ABOVE the stave, so a dynamic ALONE adds no depth below;
   *   - verse 1 with a dynamic lands ~45px below the bottom line (formula: 44);
   *   - verse 3 with a dynamic lands ~81px below it (formula: 74).
   * A fixed two-verse reserve therefore clipped three-verse scores by ~15px,
   * which is why the depth is computed from the spec's actual maximum verse.
   *
   * Taking the MAX of the candidate occupants (rather than branching on the
   * `needsRoomBelow` proxy) is what removes the waste: `needsRoomBelow` is true
   * for a dynamic alone, which reserved 230px/stave for a band that measurement
   * shows is empty.
   */
  const lyricNotes = staffSpecs.flatMap((s) => notesOf(s))
    .filter((n) => n?.lyric != null);
  const hasLyrics = lyricNotes.length > 0;
  // A dynamic only deepens the band when lyrics are present: drawLyricLayer
  // drops the baseline from +26 to +44 to clear the dynamic's band.  With no
  // lyrics there is nothing to displace, so a dynamic reserves nothing.
  const hasDynamic = staffSpecs.some((s) => notesOf(s).some((n) => n?.dynamic));
  const maxVerse = hasLyrics
    ? Math.max(1, ...lyricNotes.map((n) => {
        const l: any = n.lyric;
        const obj = typeof l === 'string' ? { text: l } : l;
        return Math.max(1, Math.floor(obj?.verse ?? 1));
      }))
    : 1;
  const lyricDepth = hasLyrics
    ? (hasDynamic ? LYRIC_OFFSET_WITH_DYNAMIC : LYRIC_OFFSET_NO_DYNAMIC)
      + (maxVerse - 1) * LYRIC_VERSE_STEP + LYRIC_DESCENT
    : 0;
  const hasPedal = staffSpecs.some((s) => (s.pedals?.length ?? 0) > 0);
  const systemTail = STAVE_SPAN + Math.max(
    // Stem/beam overshoot is always present, measured from the bottom line.
    PLAIN_TAIL - STAVE_SPAN,
    lyricDepth,
    needsRoomBelow ? BELOW_STAFF_MARK_DEPTH : 0,
    // The pedal band is the deepest below-staff marking, so it drives the tail
    // when present rather than sharing the shallower mark depth.
    hasPedal ? PEDAL_DEPTH : 0,
  );
  /**
   * Advance between stacked systems: every stave of this system, plus the tail
   * of its last stave so the next system clears it.  Previously the 40px of
   * slack in the old 160 budget happened to provide this; making it explicit is
   * why wrapped layouts shift slightly (and stop risking collision).
   */
  const perSystemHeight = STAVE_ADVANCE * staffSpecs.length + systemTail;
  // Crash-guarded like width: a non-finite `height` (or a degenerate 0, which
  // `spec.height ??` used to let through as a 0-height blank canvas) falls back
  // to the computed formula below, and a runaway value is clamped.  A valid
  // pinned height is returned verbatim, so real specs are byte-identical.
  const authorHeight = sanitizeLayoutDimension(
    spec.height, MIN_CANVAS_HEIGHT, MAX_CANVAS_DIM, 'height',
  );
  const height = authorHeight
    // Each system is PLACED at `firstSystemY + i * (perSystemHeight +
    // systemSpacing)`, and perSystemHeight = STAVE_ADVANCE*staves + systemTail
    // -- so every system, not just the last, advances by a full tail.  The old
    // formula added `systemTail` only ONCE, which under-budgeted a wrapped
    // score by systemTail*(systems-1): the final system overflowed the SVG
    // viewport and was CLIPPED off the bottom, silently losing whole measures
    // (measured: a 3-system dense score clipped its last bar by ~22px, and its
    // trailing hairpin with it).  Budget the tail per system so the canvas
    // matches the placement.  systems.length == 1 leaves the height
    // byte-for-byte unchanged (systemTail*1 == systemTail), so every
    // single-system layout -- and the parity snapshots pinning them -- is
    // untouched; only multi-system scores grow, by exactly the missing tails.
    ?? STAVE_ADVANCE * staffSpecs.length * systems.length
       + systemTail * systems.length
       + systemSpacing * Math.max(0, systems.length - 1)
       + roomAbove + titleH;

  const factory = new Factory({
    // Renderer.Backends.SVG is 2; 1 is CANVAS.  Passing 1 with a <div>
    // container throws "BadElement: CANVAS context requires an
    // HTMLCanvasElement" before anything is drawn.
    renderer: { elementId: container, width, height, backend: Renderer.Backends.SVG },
  });

  const score = factory.EasyScore();
  // spaceBetweenStaves must be OMITTED, not passed as undefined, for a single
  // staff: VexFlow's option merge accepts a present-but-undefined key as a
  // real value, System.format() then never runs, and draw() fails with
  // "NoFormatter: format() must be called before draw()".  Verified -- the
  // same call with the key absent succeeds.
  // Push the first system down past the title block so it does not collide
  // with the headroom that tempo/marks already reserve above the top staff.
  // One VexFlow System per planned line, stacked down the page.  Verified that
  // a single Factory renders several Systems on one draw() pass, which is what
  // makes wrapping possible at all.
  // Push the first system further down when the tempo is lifted onto its own
  // row above a navigation mark, so the raised tempo band (30px higher than
  // usual) still has clear headroom above it rather than clipping at y<0.
  const firstSystemY = (needsRoomAbove ? (tempoAboveMark ? 70 : 40) : 10) + titleH;
  // Width VexFlow justifies a system's notes across.  System.format() spreads
  // its voices to fill exactly this width, so a system given the full canvas
  // width is stretched to the right margin regardless of how much music it
  // holds.  That is correct for a full line but wrong for a short one: a
  // partly-filled last system stretched to the margin leaves huge gaps between
  // its few notes (measured: a 2-bar tail spread across a 680px line), whereas
  // published scores justify interior lines and leave the LAST line at natural
  // left-aligned spacing.  First-fit packs every system except the last to
  // overflow, so only the last can be under-full -- give just that one its
  // estimated natural width (never wider than the full line), leaving earlier
  // systems justified exactly as before.  Single-system and pinned-`width`
  // layouts keep the full width, so their spacing is unchanged byte-for-byte.
  const fullSystemWidth = width - 20 - labelGutter;
  const lastSystemIdx = systems.length - 1;
  const systemWidth = (i: number): number => {
    if (!wrapEnabled || systems.length < 2 || i !== lastSystemIdx) {
      return fullSystemWidth;
    }
    // The lead-in (clef/key/time) is re-printed on every system and already
    // sits inside the stave, so the natural width estimate covers it.  Cap at
    // the full width so a last system that happens to be full still justifies
    // rather than overrunning the margin.
    const natural = estimateSystemWidth(measureWidths, systems[i]);
    return Math.min(fullSystemWidth, Math.max(SYSTEM_LEAD_IN_PX, natural));
  };
  const vexSystems = systems.map((_, i) => factory.System({
    // Inset by the label gutter so the staves start clear of the part names
    // drawn in that gutter; the width shrinks by the same amount so the right
    // edge stays where it was.
    x: 10 + labelGutter,
    width: systemWidth(i),
    y: firstSystemY + i * (perSystemHeight + systemSpacing),
    ...(staffSpecs.length > 1 ? { spaceBetweenStaves: 12 } : {}),
  }));

  // A model-authored measure rarely sums exactly to the time signature.
  // score.voice() defaults to STRICT against an implicit 4/4 budget, which
  // rejects both underfull ("IncompleteVoice") and overfull ("Too many
  // ticks") bars.  Building the Voice with the spec's own meter and SOFT mode
  // renders what was asked for instead of refusing outright.
  //
  // factory.Voice (not `new Voice`) is load-bearing once a second voice is
  // involved: System only formats voices it created, so a hand-constructed
  // dynamics voice is silently dropped -- it draws nothing and reports no
  // error.  Verified: new Voice + new TextDynamics -> 0 dynamics glyphs.
  const [numBeats, beatValue] = (spec.timeSignature ?? '4/4')
    .split('/')
    .map((part) => Number(part.trim()));
  const meter =
    `${Number.isFinite(numBeats) && numBeats > 0 ? numBeats : 4}/` +
    `${Number.isFinite(beatValue) && beatValue > 0 ? beatValue : 4}`;

  // Meter-correct default beam grouping for `autoBeam` when the spec gives no
  // explicit `beamGroups`.
  //
  // Beam.generateBeams does NOT read the meter: with no `groups` option it
  // hardcodes `[new Fraction(2, 8)]` (verified in vexflow 5.0.0 beam.js:84-85),
  // so a compound/triple meter auto-beamed in TWOS instead of by its true beat
  // -- a 6/8 bar of six eighths drew as three groups of two rather than the
  // published two dotted-quarter beats, and 3/4 / 2/2 were likewise mis-grouped.
  // VexFlow ships the meter-aware table itself in Beam.getDefaultBeamGroups
  // (6/8 -> [3/8], 3/4 -> [1/4], 2/2 -> [1/2], ...), so passing its result
  // restores correct beat grouping.  Computed from the SPEC-level opening meter
  // (the dominant single-meter case); a score that changes meter mid-piece AND
  // relies on autoBeam AND sets no `beamGroups` still groups every bar by the
  // opening meter -- rare, and no worse than the previous always-twos default,
  // and such an author can set `beamGroups` per staff.  Wrapped defensively so
  // an unusable meter falls back to generateBeams' own default rather than
  // throwing.  Non-autoBeam and explicit-`beamGroups` paths never read this, so
  // they are byte-identical.
  let defaultBeamGroups: any[] | undefined;
  try {
    defaultBeamGroups = Beam.getDefaultBeamGroups(meter);
  } catch {
    defaultBeamGroups = undefined;
  }

  // Per-meter beam grouping for `autoBeam`, so a score that CHANGES meter
  // mid-piece (per-measure `timeSignature`, e.g. 4/4 -> 6/8) beams each bar by
  // ITS OWN meter rather than by the opening one.  D31 made autoBeam
  // meter-aware but read the meter ONCE at the spec level (`defaultBeamGroups`
  // above), so the 6/8 bars of a 4/4 -> 6/8 score still beamed in 4/4's
  // quarter-note groups (two eighths) instead of 6/8's dotted-quarter beats
  // (three eighths) -- the very mis-grouping D31 set out to fix, surviving on
  // the mid-score-change case.  The renderer already resolves the meter in
  // force at each bar (`effectiveMeterByMeasure`); this composes that with
  // getDefaultBeamGroups, memoized per meter string so a long score does not
  // re-derive the table for every bar.  An absent meter, or one
  // getDefaultBeamGroups cannot parse, falls back to the opening meter's
  // grouping -- never worse than before -- and a single-meter score resolves
  // every bar to `defaultBeamGroups`, so its beaming is byte-identical.
  const beamGroupsByMeterCache = new Map<string, any[] | undefined>();
  const beamGroupsForMeter = (m: string | undefined): any[] | undefined => {
    if (m == null) return defaultBeamGroups;
    if (beamGroupsByMeterCache.has(m)) return beamGroupsByMeterCache.get(m);
    let g: any[] | undefined;
    try {
      g = Beam.getDefaultBeamGroups(m);
    } catch {
      g = undefined;
    }
    const resolved = g ?? defaultBeamGroups;
    beamGroupsByMeterCache.set(m, resolved);
    return resolved;
  };

  /**
   * Built staves, one entry per (staff x system).  A three-staff score wrapped
   * onto two systems therefore yields six entries.  Spans are attached per
   * entry, which is what confines them to a single system.
   */
  const built: Array<{
    stave: any; notes: any[]; staffSpec: MusicStaff;
    specNotes: MusicNoteSpec[]; tickables: any[];
    /** Rendered notes grouped by measure, for per-measure auto-beaming. */
    byMeasure: any[][];
    /**
     * Meter-default beam groups per measure of `byMeasure` (aligned by index),
     * so autoBeam follows a mid-score meter change.  Undefined per entry when
     * the bar's meter has no known grouping; the whole field is only read on
     * the autoBeam path with no explicit `beamGroups`.
     */
    beamGroupsByMeasure: Array<any[] | undefined>;
    /** Which planned system this stave belongs to. */
    systemIndex: number;
    /** Flat index, within the staff, of this entry's first note. */
    noteOffset: number;
    /** Multi-measure-rest spacers on this stave, for the post-format overlay. */
    multiRests: Array<{ ghost: any; count: number }>;
  }> = [];
  /**
   * Per-staff view across every system: the whole flat note list and the
   * system each note landed on.  Spans address the flat list, so this is what
   * lets a span be resolved and its endpoints checked for a system crossing.
   */
  const perStaff: Array<{
    staffSpec: MusicStaff;
    notes: any[];
    specNotes: MusicNoteSpec[];
    /** systemIndex per flat note index, parallel to `notes`. */
    noteSystem: number[];
  }> = [];
  /**
   * Beams generated for SECONDARY voices (voices[1..]).  Collected as the
   * voices are built -- they are not factory-owned, so like the primary
   * auto-beams they need an explicit draw pass after factory.draw().
   */
  const secondaryBeams: any[] = [];

  /** Resolve the barline drawn between two adjacent measures. */
  const barlineBetween = (before: MusicMeasure, after: MusicMeasure): number => {
    const name = before.endBar ?? after.beginBar ?? 'single';
    const key = BARLINE_TYPES[name];
    if (!key) {
      problems.push(`unknown barline "${name}"`);
      return Barline.type.SINGLE;
    }
    return Barline.type[key];
  };

  staffSpecs.forEach((staffSpec, staffIndex) => {
    // A two-staff system with no clefs given is overwhelmingly a piano grand
    // staff, so default the lower staff to bass rather than a second treble.
    const clef = staffSpec.clef ?? (staffIndex === 0 ? 'treble' : 'bass');
    const allMeasures = measuresOf(staffSpec);
    const allSpecNotes = notesOf(staffSpec);
    // Meter in effect at each measure, resolving per-measure `timeSignature`
    // changes forward: a change persists until the next one, exactly as a
    // printed score reads.  Precomputed over the WHOLE staff (not per system)
    // so a continuation line knows the meter carried into it and can re-print
    // it at the clef, and so a change is detected against the immediately
    // preceding bar regardless of where the system break falls.  The opening
    // entry is the spec-level meter unless the first measure overrides it.
    const effectiveMeterByMeasure: Array<string | undefined> = [];
    {
      // Seed with the sanitized top-level meter and sanitize every per-measure
      // change, so effectiveMeterByMeasure only ever holds a meter VexFlow can
      // parse (or undefined).  Both consumers of this array draw the meter --
      // stave.addTimeSignature at the system clef and new TimeSigNote at an
      // interior change -- and VexFlow throws on a malformed spec, which would
      // otherwise abort the whole render for one bad value (see sanitizeMeter).
      // An invalid per-measure change is dropped with a warning, leaving the
      // previous meter in force rather than destroying the score.
      let running: string | undefined = sanitizeMeter(spec.timeSignature);
      allMeasures.forEach((measure, idx) => {
        if (measure.timeSignature) {
          const m = sanitizeMeter(measure.timeSignature);
          if (m) running = m;
        }
        effectiveMeterByMeasure[idx] = running;
      });
    }
    // Key signature in effect at each measure, resolving per-measure
    // `keySignature` changes forward the same way the meter is above -- a
    // modulation persists until the next change.  Precomputed over the WHOLE
    // staff so a continuation line re-prints the key carried into it and a
    // change is detected against the immediately preceding bar wherever the
    // system break falls.  Seeded with the staff's own key (else the spec's),
    // RAW, so a score with NO key change resolves every bar to exactly the
    // value each use-site consumed before -- byte-identical (see
    // resolveEffectiveKeys).
    const effectiveKeyByMeasure = resolveEffectiveKeys(
      allMeasures, staffSpec.keySignature ?? spec.keySignature,
    );
    /** Accumulated across systems, for the per-staff span view. */
    const staffNotes: any[] = [];
    const staffNoteSystem: number[] = [];

  systems.forEach((systemMeasures, systemIndex) => {
    // This staff's slice of the score for this system.  A staff with fewer
    // measures than the plan contributes an empty stave here rather than
    // being dropped, so the system keeps its full complement of staves and
    // the brace/connectors still line up.
    // Keep each measure paired with its GLOBAL index so a meter change can be
    // detected against the preceding bar (which may live on the previous
    // system) via effectiveMeterByMeasure.
    const globalMeasureIndices = systemMeasures.filter((m) => allMeasures[m] != null);
    const measures = globalMeasureIndices.map((m) => allMeasures[m]);
    const noteOffset = staffNotes.length;
    const specNotes = measures.flatMap((m) => m.notes ?? []);
    // easyNotes is the flat note list that span indices address; tickables
    // additionally carries the BarNotes, in playing order.
    const easyNotes: any[] = [];
    const tickables: any[] = [];
    const byMeasure: any[][] = [];
    // Multi-measure-rest spacers created in this system, drawn as an overlay
    // after formatting (see drawMultiMeasureRest).
    const multiRests: Array<{ ghost: any; count: number }> = [];

    measures.forEach((measure, measureIndex) => {
      if (measureIndex > 0) {
        // A BarNote is what draws a barline INSIDE a stave; setBegBarType /
        // setEndBarType only reach the stave's two outer edges.
        tickables.push(new BarNote(barlineBetween(measures[measureIndex - 1], measure)));
      }
      // Mid-stave meter change: engrave the new signature as a 0-tick
      // TimeSigNote before this measure's notes, exactly where a printed score
      // prints it.  Two gates:
      //   - `measureIndex > 0`: a change landing on a system's FIRST bar is
      //     already printed at that line's clef (see systemOpeningMeter, which
      //     reflects the meter carried into the line), so a mid-stave symbol
      //     there would double-print it.  Only interior bars need the inline
      //     glyph.
      //   - meter differs from the GLOBAL preceding bar: re-stating an
      //     unchanged meter mid-line is wrong engraving.
      const globalIdx = globalMeasureIndices[measureIndex];
      const meterHere = effectiveMeterByMeasure[globalIdx];
      const meterBefore = globalIdx > 0 ? effectiveMeterByMeasure[globalIdx - 1] : undefined;
      if (measureIndex > 0 && meterHere && meterBefore && meterHere !== meterBefore) {
        // ignoreTicks TimeSigNote contributes 0 ticks (like BarNote), so it
        // shifts no note and both voices stay in sync.
        tickables.push(new TimeSigNote(meterHere));
      }
      // Mid-stave key change (modulation): engrave the new signature as a
      // 0-tick KeySigNote before this measure's notes, exactly where a printed
      // score prints it, with the SAME two gates as the meter change above --
      // a change on a system's first bar is already re-printed at that line's
      // clef (see systemOpeningKey), and re-stating an unchanged key mid-line
      // is wrong engraving.  Both keys are sanitized before comparing/drawing
      // so the raw-seed vs canonical-change forms cannot spuriously differ, and
      // the PREVIOUS key is passed as the cancel spec so the naturals voiding
      // the old accidentals print, as an engraved modulation shows them.
      const keyHere = sanitizeKeySignature(effectiveKeyByMeasure[globalIdx]);
      const keyBefore = globalIdx > 0
        ? sanitizeKeySignature(effectiveKeyByMeasure[globalIdx - 1]) : undefined;
      if (measureIndex > 0 && keyHere && keyBefore && keyHere !== keyBefore) {
        tickables.push(new KeySigNote(keyHere, keyBefore));
      }
      const measureNotes = measure.notes ?? [];
      // Multi-measure rest: consolidate `multiRest` empty bars into one H-bar +
      // count.  The bar is silent by definition, so it holds no real notes --
      // instead a 0-drawn GhostNote spacer claims the measure's layout width
      // (like any other tickable) and the symbol is hand-drawn over it in the
      // overlay pass (drawMultiMeasureRest), reading the spacer's resolved x.
      // Kept OUT of easyNotes/byMeasure so it addresses no span/beam index and
      // the no-MMR path is byte-identical.
      const restCount = multiRestOf(measure) ?? 0;
      if (restCount >= 1) {
        if (!Number.isInteger(restCount)) {
          problems.push(
            `multiRest must be an integer >= 1 (got "${multiRestOf(measure)}"); skipped`,
          );
        } else {
          if (measureNotes.length > 0) {
            problems.push(
              `measure with multiRest ${restCount} also has notes; the notes `
              + `were ignored (a multi-measure rest is silent)`,
            );
          }
          // A whole-bar GhostNote spacer -- 0 drawn ink, but real ticks so the
          // SOFT voice reserves the measure's width.  SOFT mode tolerates any
          // meter, so a whole spacer stands in for a bar of any signature.
          const spacer = new GhostNote({ duration: 'w' });
          tickables.push(spacer);
          byMeasure.push([]);
          multiRests.push({ ghost: spacer, count: restCount });
        }
        return;
      }
      // Same precedence as the stave's own addKeySignature below, so the
      // notes are filtered against exactly the signature that is drawn.
      // On a multi-voice staff this is the UPPER (voice 0) line, so raise its
      // rests off the centre line -- otherwise a rest here would overprint a
      // simultaneous rest in the lower voice (see REST_PITCH_MULTIVOICE).  A
      // single-voice staff passes no override and its rests stay centred.
      const primaryRestPitch = (staffSpec.voices?.length ?? 0) > 1
        ? multiVoiceRestPitch(clef, 'upper')
        : undefined;
      const noteStrings = buildNoteString(
        // Per-measure effective key, so accidentals are filtered against the
        // signature actually in force after a modulation, not the opening one.
        measureNotes, clef, effectiveKeyByMeasure[globalIdx],
        primaryRestPitch,
      );
      const rendered = score.notes(noteStrings, { clef });

      // EasyScore signals a grammar mismatch by returning fewer notes, not by
      // throwing.  Detect it here so the error names the real cause instead of
      // surfacing downstream as an unrelated voice complaint.
      if (rendered.length !== measureNotes.length) {
        const where = measures.length > 1 ? ` in measure ${measureIndex + 1}` : '';
        throw new Error(
          `Could not parse ${measureNotes.length - rendered.length} of ` +
          `${measureNotes.length} notes${where} (parsed "${noteStrings}").  Check that ` +
          `each key looks like "c/5" or "c#/5" and each duration is one of ` +
          `w, h, q, 8, 16 (optionally dotted, e.g. "q.").`,
        );
      }
      easyNotes.push(...rendered);
      tickables.push(...rendered);
      byMeasure.push(rendered);
    });

    // Per-note modifiers are attached before formatting so VexFlow reserves
    // space for them.  Factored into a closure (rather than an inline forEach
    // body) so a secondary independent voice on the same staff runs the exact
    // same modifier logic -- articulations, ornaments, grace notes, fingering,
    // chord symbols, annotations -- instead of a parallel path that could
    // drift from the primary voice's.
    const applyNoteModifiers = (note: any, specNote?: MusicNoteSpec) => {
      if (!specNote) return;
      for (const name of specNote.articulations ?? []) {
        const code = ARTICULATION_CODES[name];
        if (!code) { problems.push(`unknown articulation "${name}"`); continue; }
        // Place on the conventional side (ABOVE unless the name says otherwise,
        // e.g. fermata-below), so the inverted below-staff fermata is not drawn
        // hanging over the top of the staff.  See ARTICULATION_POSITIONS.
        const side = ARTICULATION_POSITIONS[name] === 'below'
          ? Modifier.Position.BELOW
          : Modifier.Position.ABOVE;
        note.addModifier(new Articulation(code).setPosition(side), 0);
      }
      for (const name of specNote.ornaments ?? []) {
        const code = ORNAMENT_CODES[name];
        if (!code) { problems.push(`unknown ornament "${name}"`); continue; }
        note.addModifier(new Ornament(code), 0);
      }
      // Single-note tremolo: n slashes drawn through the stem, meaning the
      // note is rapidly repeated.  VexFlow's Tremolo is a stem modifier taking
      // a stroke COUNT (there is no glyph name to map -- one, two or three
      // slashes IS the notation), so it is validated by range rather than a
      // lookup table.  1..3 is the full published range (a whole-note repeat
      // and buzz rolls aside); anything else is not real notation and is
      // skipped with a warning, matching the unknown-name convention.  Only an
      // integer is accepted -- Tremolo iterates the count to place each slash,
      // so a fractional value would draw the wrong number or none.
      if (specNote.tremolo != null) {
        const strokes = Number(specNote.tremolo);
        if (!Number.isInteger(strokes) || strokes < 1 || strokes > 3) {
          problems.push(
            `tremolo must be an integer 1..3 (got "${specNote.tremolo}"); skipped`,
          );
        } else {
          note.addModifier(new Tremolo(strokes), 0);
        }
      }
      // Arpeggio / chord roll: the vertical wavy line to the LEFT of a chord.
      // VexFlow models it as a `Stroke` MODIFIER attached to the note via
      // addStroke (NOT addModifier), so it rides the same pre-format path as
      // the articulations/ornaments above and reserves its own space.  The
      // friendly name maps to a Stroke.Type KEY (resolved to the numeric
      // constant here, the BARLINE_TYPES indirection) so no raw VexFlow number
      // reaches the spec; `true` is the "arpeggio" shorthand.  An unknown name
      // is skipped with a warning, matching the plugin's unknown-name
      // convention.  The stroke is attached at notehead index 0 -- VexFlow
      // spans it across the whole chord from there -- so it is a no-op visual
      // on a single-note "chord", which is why the contract asks for two or
      // more `keys`; it is not rejected there, merely inconspicuous.
      if (specNote.arpeggio != null && specNote.arpeggio !== false) {
        const name = specNote.arpeggio === true ? 'arpeggio' : String(specNote.arpeggio);
        const typeKey = ARPEGGIO_STROKE_TYPES[name];
        if (!typeKey) {
          problems.push(`unknown arpeggio "${name}"`);
        } else if (typeof note.addStroke === 'function') {
          note.addStroke(0, new Stroke(Stroke.Type[typeKey]));
        }
      }
      // Grace notes: an appoggiatura/acciaccatura or ornamental run engraved
      // small BEFORE the main note.  VexFlow models them as a GraceNoteGroup
      // modifier rather than as voice tickables, which is why they cannot ride
      // the EasyScore note string and are built by hand here.  The duration is
      // routed through toNoteStructDuration for the same reason the dynamics
      // path is: GraceNote goes through Note.parseDuration, which rejects a
      // trailing "." and hangs on a degenerate code, so dots must be split out.
      if (Array.isArray(specNote.graceNotes) && specNote.graceNotes.length > 0) {
        // Drop pitchless grace notes BEFORE construction.  `new GraceNote({keys:
        // []})` builds with a degenerate (NaN) y-position and GraceNoteGroup's
        // pre-format loop -- which iterates until every grace note is placed --
        // never converges, freezing the render for 30s with a blank canvas
        // (verified).  Unlike a main note there is no rest to fall back to: an
        // ornament with no pitch has no meaning, so the only choices are to drop
        // it or lose the score.
        const playableGraces = specNote.graceNotes.filter((g) => {
          if (g.keys?.length) return true;
          console.warn('musicPlugin: grace note with no keys skipped');
          return false;
        });
        const graceNotes = playableGraces.map((g) => {
          const { duration, dots } = toNoteStructDuration(g.duration);
          // Reject an unrenderable grace pitch (a mistyped accidental such as
          // "ef/5" for "eb/5") BEFORE construction, exactly as the main-note
          // path does via sanitizePitch (see buildNoteString).  toStaveNoteKey
          // clamps only the OCTAVE; nothing guarded the ACCIDENTAL letter, so a
          // bogus one fell through to `new GraceNote`, whose StaveNote key
          // parser builds a NaN-position note that never converges in
          // GraceNoteGroup's pre-format loop -- freezing the whole render for
          // the ~30s timeout with a blank canvas, the SAME hang the empty-keys
          // and out-of-range-octave guards already defend on this path.  A
          // chord grace keeps its still-valid members; a grace whose keys are
          // ALL unrenderable returns null and is dropped below, rather than
          // being built on the wrong line or hanging.
          const keys = g.keys
            .map((k) => sanitizePitch(k))
            .filter((k): k is string => k !== null)
            .map(toStaveNoteKey);
          if (keys.length === 0) {
            console.warn('musicPlugin: grace note with no renderable keys skipped');
            return null;
          }
          const grace = new GraceNote({
            // StaveNote's constructor (which GraceNote extends) parses its
            // keys with the slash `note/octave` grammar, NOT EasyScore's
            // slashless form -- feeding it a toEasyScoreKey result ("B4")
            // yields an unparseable pitch and hangs GraceNoteGroup's format
            // loop.  toStaveNoteKey keeps/repairs the slash form it needs.
            keys,
            duration, dots,
            // The slash is the acciaccatura ("crushed") vs the plain
            // appoggiatura; VexFlow draws it on a flagged/first grace note.
            slash: Boolean(g.slash),
          });
          // Add the accidental GLYPH for any grace note that carries one.  A
          // main note gets its sharp/flat/natural sign automatically because it
          // rides the EasyScore string, which auto-attaches an Accidental; a
          // GraceNote is hand-built through StaveNote's constructor, which sets
          // the notehead LINE from the key's accidental but does NOT draw the
          // sign -- so without this a "c#/5" or "bb/4" grace printed on the
          // right line with no accidental, reading as the wrong pitch (the same
          // omission the cautionary-accidental path already handles for main
          // notes via addModifier(new Accidental(...)) below).  Grace notes
          // bypass the key-signature filter, so the accidental written in the
          // key IS the intended sign and is drawn verbatim -- one per chord
          // member that carries one, indexed like the main-note path.
          keys.forEach((k, ki) => {
            const acc = /^[a-gA-G](n|#{1,2}|b{1,2})\//.exec(k)?.[1];
            if (acc && typeof grace.addModifier === 'function') {
              grace.addModifier(new Accidental(acc), ki);
            }
          });
          return grace;
        })
          // Drop the grace notes whose keys were all unrenderable (returned
          // null above): keeping them in the group would attach a NaN-position
          // member and re-enter the very hang the sanitize pass just avoided.
          .filter((g) => g !== null);
        // Every grace note was unplayable -- attaching an empty group would
        // re-enter the same non-converging format loop the filter just avoided.
        if (graceNotes.length > 0) {
        // showSlur=false: the little curved connector VexFlow can draw from
        // the grace group to the main note is off by default in most house
        // styles and would collide with any real slur the spec draws.
        const group = new GraceNoteGroup(graceNotes, false);
        // Beam a run of two-or-more grace notes so a rapid ornament reads as a
        // single gesture with a beam rather than a row of individual flags,
        // matching how a written-out turn or run is engraved.
        if (graceNotes.length > 1 && typeof group.beamNotes === 'function') {
          group.beamNotes();
        }
        note.addModifier(group, 0);
        }
      }
      // Fingering: a bare value means "below", which is the piano convention.
      if (specNote.fingering != null) {
        const fingering = typeof specNote.fingering === 'object'
          ? specNote.fingering
          : { number: specNote.fingering };
        const place = fingering.position ?? 'below';
        if (!['above', 'below', 'left', 'right'].includes(place)) {
          problems.push(`unknown fingering position "${place}"`);
        } else {
          // Guard the digit BEFORE it reaches VexFlow: a non-finite number or
          // the object form with no `number` would otherwise engrave the
          // literal "NaN"/"undefined" beside the note (see
          // sanitizeModifierNumber).  A dropped value simply prints no
          // fingering, which is far better than junk text.
          const num = sanitizeModifierNumber(fingering.number);
          if (num !== undefined) {
            note.addModifier(
              factory.Fingering({ number: num, position: place }), 0,
            );
          }
        }
      }
      // StringNumber REQUIRES an explicit position -- omitting it throws
      // "InvalidPosition: The position undefined is invalid", unlike
      // Fingering which defaults happily.
      if (specNote.stringNumber != null) {
        // Same literal-text guard as fingering above.
        const num = sanitizeModifierNumber(specNote.stringNumber);
        if (num !== undefined) {
          note.addModifier(
            factory.StringNumber({
              number: num, position: 'above',
            }), 0,
          );
        }
      }
      // Chord symbol.  ChordSymbol (not Annotation) is used so the engraved
      // dim/half-dim/minor glyphs and true super/subscripts are reachable;
      // an Annotation can only ever draw plain text.
      if (specNote.chordSymbol != null) {
        const chord: MusicChordSymbol = typeof specNote.chordSymbol === 'string'
          ? { text: specNote.chordSymbol }
          : specNote.chordSymbol;
        const symbol = factory.ChordSymbol();
        if (chord.text) symbol.addText(String(chord.text));
        if (chord.glyph) {
          if (CHORD_SYMBOL_GLYPHS.has(chord.glyph)) symbol.addGlyph(chord.glyph);
          else problems.push(`unknown chord glyph "${chord.glyph}"`);
        }
        if (chord.superscript) symbol.addTextSuperscript(String(chord.superscript));
        if (chord.subscript) symbol.addTextSubscript(String(chord.subscript));
        // Below-staff placement is what makes roman-numeral analysis possible.
        if (chord.position === 'below') {
          symbol.setVertical(ChordSymbol.VerticalJustify.BOTTOM);
        }
        note.addModifier(symbol, 0);
      }
      for (const a of specNote.annotations ?? []) {
        const ann = new Annotation(a.text);
        ann.setPosition(
          a.position === 'below' ? Annotation.Position.BOTTOM : Annotation.Position.TOP,
        );
        note.addModifier(ann, 0);
      }
    };
    easyNotes.forEach((note: any, i: number) => applyNoteModifiers(note, specNotes[i]));

    // Force the PRIMARY voice's stems when voices[0] asked for a direction.
    // voices[0] is normalised through measuresOf into the standard staff path
    // (so width/beams/spans share one code path), and that path lets VexFlow
    // auto-pick the stem from staff position -- which silently drops the
    // requested stemDirection on the top voice of a two-voice staff, leaving
    // both voices stemmed the same way.  A published two-voice staff needs the
    // upper voice stems up and the lower down, so honour voices[0] here, the
    // mirror of the voices[1..] handling below.  Only when a direction is set,
    // so a single-voice staff keeps VexFlow's position-based default exactly.
    {
      const primaryStemSpec = staffSpec.voices?.[0]?.stemDirection;
      const primaryStem = primaryStemSpec === 'down'
        ? Vex.Stem?.DOWN ?? -1
        : primaryStemSpec === 'up'
          ? Vex.Stem?.UP ?? 1
          : undefined;
      if (primaryStem != null) {
        for (const note of easyNotes) {
          if (typeof note.setStemDirection === 'function') note.setStemDirection(primaryStem);
        }
      }
    }

    const voices: any[] = [
      factory.Voice({ time: meter }).setMode(Voice.Mode.SOFT).addTickables(tickables),
    ];

    // Dynamics are NOT built into a voice here.  They are drawn as a d3 overlay
    // below the staff after formatting (see drawDynamicsLayer), because VexFlow
    // 5.0's TextDynamics does not honour the `line` option that would place the
    // mark below the staff -- verified on the built bundle, where `line: 9`
    // (meant to resolve to two spaces under the bottom line) still rendered
    // `p`/`f` ABOVE the top line, colliding with the tempo / chord-symbol band.
    // An overlay takes no beat time, so unlike the old GhostNote-padded voice
    // it cannot displace the notes and needs no barline/meter mirroring at all.

    // Secondary independent voices (voices[1..]) share this staff, formatted
    // alongside the primary voice.  Voice 0 was already consumed as the staff's
    // primary content (see measuresOf), so only the extras are built here.  A
    // secondary voice is a REAL note voice -- it consumes beat time and stacks
    // against the primary -- unlike the dynamics/ghost voice above, so each of
    // its notes is a genuine StaveNote with its stem forced by stemDirection
    // and the same modifier logic applied.  Its measures are aligned by index
    // with the primary voice's, and BarNotes/TimeSigNotes are mirrored at the
    // same positions so the voices cannot desync after a barline or meter
    // change (the exact failure the dynamics voice documents).
    const extraVoiceSpecs = (staffSpec.voices ?? []).slice(1);
    for (const voiceSpec of extraVoiceSpecs) {
      const voiceMeasures = (Array.isArray(voiceSpec.measures)
        && voiceSpec.measures.length > 0)
        ? voiceSpec.measures
        : [{ notes: voiceSpec.notes ?? [] }];
      // Restrict to this system's measures, aligned by GLOBAL index with the
      // primary voice's slice.  A voice with fewer measures than the primary
      // simply contributes nothing on later systems rather than misaligning.
      const stemDir = voiceSpec.stemDirection === 'down'
        ? Vex.Stem?.DOWN ?? -1
        : voiceSpec.stemDirection === 'up'
          ? Vex.Stem?.UP ?? 1
          : undefined;
      const voiceTickables: any[] = [];
      const voiceByMeasure: any[][] = [];
      globalMeasureIndices.forEach((globalIdx, localIdx) => {
        const measure = voiceMeasures[globalIdx];
        if (localIdx > 0) {
          // Mirror the primary voice's barline so the two stay in tick-sync.
          const prevPrimary = measures[localIdx - 1];
          voiceTickables.push(new BarNote(barlineBetween(prevPrimary, measures[localIdx])));
        }
        const meterHere = effectiveMeterByMeasure[globalIdx];
        const meterBefore = globalIdx > 0 ? effectiveMeterByMeasure[globalIdx - 1] : undefined;
        if (localIdx > 0 && meterHere && meterBefore && meterHere !== meterBefore) {
          voiceTickables.push(new TimeSigNote(meterHere));
        }
        // Mirror the primary voice's mid-stave key change so the voices stay
        // in tick-sync across a modulation (the KeySigNote is 0-tick, like the
        // TimeSigNote), and both draw the new signature at the same x.
        const keyHere = sanitizeKeySignature(effectiveKeyByMeasure[globalIdx]);
        const keyBefore = globalIdx > 0
          ? sanitizeKeySignature(effectiveKeyByMeasure[globalIdx - 1]) : undefined;
        if (localIdx > 0 && keyHere && keyBefore && keyHere !== keyBefore) {
          voiceTickables.push(new KeySigNote(keyHere, keyBefore));
        }
        const measureNotes = measure?.notes ?? [];
        if (measureNotes.length === 0) { voiceByMeasure.push([]); return; }
        const rendered = score.notes(
          // A secondary (lower) voice: drop its rests below the centre line so
          // they sit on their own line and never overprint the raised
          // upper-voice rest at the same beat (see REST_PITCH_MULTIVOICE).
          buildNoteString(
            measureNotes, clef, effectiveKeyByMeasure[globalIdx],
            multiVoiceRestPitch(clef, 'lower'),
          ),
          { clef },
        );
        rendered.forEach((note: any, i: number) => {
          // Force the stem so the reader can tell this voice from the primary;
          // setStemDirection reflows the flag/beam side automatically.
          if (stemDir != null && typeof note.setStemDirection === 'function') {
            note.setStemDirection(stemDir);
          }
          applyNoteModifiers(note, measureNotes[i]);
        });
        voiceTickables.push(...rendered);
        voiceByMeasure.push(rendered);
      });
      if (voiceTickables.length === 0) continue;
      voices.push(
        factory.Voice({ time: meter }).setMode(Voice.Mode.SOFT).addTickables(voiceTickables),
      );
      // Auto-beam a secondary voice on the same terms as the primary (per
      // measure, respecting beamGroups), preserving its forced stem so the
      // beam sits on the correct side.  Collected for the post-draw pass with
      // the primary beams.
      if (spec.autoBeam) {
        // Same degenerate-pair guard as the primary path: a 0-tick group hangs
        // generateBeams (see sanitizeBeamGroups).
        const validGroups = sanitizeBeamGroups(spec.beamGroups);
        // An explicit beamGroups overrides every bar; otherwise each bar uses
        // ITS OWN meter's grouping so a mid-score meter change beams correctly
        // (voiceByMeasure aligns with globalMeasureIndices) -- see
        // beamGroupsForMeter.  Single-meter scores are byte-identical.
        const explicitGroups = validGroups
          ? validGroups.map(([n, d]) => new Fraction(n, d))
          : undefined;
        voiceByMeasure.forEach((measureNotes, localMi) => {
          if (measureNotes.length === 0) return;
          const groups = explicitGroups
            ?? beamGroupsForMeter(effectiveMeterByMeasure[globalMeasureIndices[localMi]]);
          const generated = Beam.generateBeams(measureNotes, {
            ...(groups ? { groups } : {}),
            beamRests: false,
            // Keep the beam on the voice's stem side rather than letting
            // VexFlow re-choose it from staff position.
            ...(stemDir != null ? { stemDirection: stemDir, maintainStemDirections: true } : {}),
          });
          generated.forEach(applyHouseBeamSlope);
          secondaryBeams.push(...generated);
        });
      }
    }

    // Courtesy (cautionary) accidentals: a parenthesised reminder on a note
    // that would otherwise print BARE but whose pitch sounded differently in
    // the immediately preceding bar (see planCautionaryAccidentals).  Added
    // BEFORE format so the notehead spacing reserves room for the glyph, and
    // as a real VexFlow Accidental modifier (not a baked pitch accidental, so
    // it cannot double a printed sign -- we only touch bare notes).  The
    // look-back reads the PREVIOUS bar from the whole-staff `allMeasures` via
    // the global index, so a reminder still fires across a system break.
    // Entirely gated on the flag; the no-flag path is byte-identical.
    if (spec.cautionaryAccidentals) {
      byMeasure.forEach((renderedMeasureNotes, localMi) => {
        if (!renderedMeasureNotes || renderedMeasureNotes.length === 0) return;
        const globalMi = globalMeasureIndices[localMi];
        // The key in force AT this bar, so a reminder is judged against the
        // signature actually reading here rather than the opening one.
        const keySig = effectiveKeyByMeasure[globalMi];
        const curSpec = allMeasures[globalMi]?.notes ?? [];
        const prevSpec = globalMi > 0 ? (allMeasures[globalMi - 1]?.notes) : undefined;
        for (const mark of planCautionaryAccidentals(curSpec, prevSpec, keySig)) {
          const note = renderedMeasureNotes[mark.noteIndex];
          if (!note || typeof note.addModifier !== 'function') continue;
          try {
            note.addModifier(new Accidental(mark.code).setAsCautionary(), mark.keyIndex);
          } catch (e: any) {
            problems.push(`cautionary accidental skipped: ${e?.message ?? e}`);
          }
        }
      });
    }

    const stave = vexSystems[systemIndex].addStave({ voices });
    // Clef, key and time signature are re-printed on EVERY system, which is
    // what a printed score does at a line break -- a continuation line with no
    // clef would be unreadable.
    stave.addClef(clef);
    // Print the meter at this system's clef.  On the first system that is the
    // opening meter; on a continuation line it is whatever meter was carried
    // into the line's first bar, so a score that changed to 3/4 on a previous
    // line re-prints 3/4 here rather than the stale opening signature.  A
    // change that first occurs mid-line is still drawn by the TimeSigNote
    // above; this only re-states the meter in force AT the line's start.
    const systemOpeningMeter = globalMeasureIndices.length > 0
      ? effectiveMeterByMeasure[globalMeasureIndices[0]]
      // The array branch is already sanitized; the empty-system fallback reads
      // the raw top-level meter, so guard it too before it reaches VexFlow.
      : sanitizeMeter(spec.timeSignature);
    if (systemOpeningMeter) stave.addTimeSignature(systemOpeningMeter);
    // Sanitize BEFORE the display API: VexFlow's addKeySignature/KeySignature
    // THROWS Vex.RuntimeError('BadKeySignature') on any name outside its ~30
    // recognised keys, and that throw escapes render() -> the SVG never mounts
    // and the render hangs to the 30s cap with zero output (Issue 28).  This is
    // the exact display-path sibling of the sanitizeMeter fix (D22): the
    // top-level key already degrades harmlessly through keySignatureMap (null
    // -> filter nothing), but the DRAWN signature reached VexFlow raw.
    // sanitizeKeySignature returns a recognised key trimmed-unchanged (valid
    // path byte-identical), null for absent/empty (skip drawing, as before),
    // or "C" with a warning for a bad value -- the neutral no-accidental
    // signature, so no pitch a reader sees is altered.
    // Print the key at this system's clef.  On the first system that is the
    // opening key; on a continuation line it is whatever key was carried into
    // the line's first bar, so a score that modulated to G on a previous line
    // re-prints G here rather than the stale opening signature -- exactly as
    // systemOpeningMeter does for the meter.  A change first occurring mid-line
    // is still drawn by the KeySigNote above; this re-states the key in force
    // AT the line's start.
    const systemOpeningKey = globalMeasureIndices.length > 0
      ? sanitizeKeySignature(effectiveKeyByMeasure[globalMeasureIndices[0]])
      : sanitizeKeySignature(staffSpec.keySignature ?? spec.keySignature);
    if (systemOpeningKey) stave.addKeySignature(systemOpeningKey);
    // Meter-default beam grouping per measure of this system's slice, aligned
    // with byMeasure (both indexed by local measure position via
    // globalMeasureIndices), so the autoBeam pass can group a bar by ITS meter
    // rather than the opening one -- see beamGroupsForMeter.
    const beamGroupsByMeasure = globalMeasureIndices.map(
      (gi) => beamGroupsForMeter(effectiveMeterByMeasure[gi]),
    );
    built.push({
      stave, notes: easyNotes, staffSpec, specNotes, tickables, byMeasure,
      systemIndex, noteOffset, multiRests, beamGroupsByMeasure,
    });
    for (const note of easyNotes) {
      staffNotes.push(note);
      staffNoteSystem.push(systemIndex);
    }
  });

    perStaff.push({
      staffSpec,
      notes: staffNotes,
      specNotes: allSpecNotes,
      noteSystem: staffNoteSystem,
    });
  });

  // Tempo, navigation marks, volta and labels go on the TOP staff only: in
  // engraving they describe the system, and repeating them per staff would be
  // wrong rather than merely redundant.  On a wrapped score that means the
  // top staff of the FIRST system -- a tempo repeated on every line would be
  // wrong engraving, not merely redundant.
  const topStave = built[0].stave;
  // A tempo NAME drawn as a d3 overlay (populated only for the name+bpm case;
  // see the tempo block below), drawn in the post-format pass with drawTempoName.
  let tempoNamePlan: { text: string; x: number; y: number } | null = null;
  if (spec.tempo) {
    // VexFlow's StaveTempo only engraves the metronome portion when it is
    // given a beat `duration`: a bpm with no duration draws the note glyph
    // and "= N" as nothing at all, so `{"bpm":120}` silently produced an
    // empty mark.  Default the beat unit to a quarter when a bpm is supplied
    // without one -- "♩ = N" is the overwhelming metronome convention, so the
    // shorthand should render rather than vanish.  A tempo carrying neither a
    // name nor a bpm has nothing to show; warn rather than add an empty mark.
    // Sanitize the bpm BEFORE it reaches StaveTempo, which stringifies it raw
    // (`'' + bpm`) -- a non-finite/negative/absurd value otherwise prints a
    // dangling "♩ =", "♩ = Infinity", "♩ = -120" or "♩ = 1e+21" onto the score
    // (WRONG-OUTPUT sibling of the D25 duration/dots fix).  hasBpm is derived
    // from the SANITIZED value so a bad bpm is treated as absent: with no name
    // the whole mark is skipped below, and the beat unit (which only pairs with
    // a bpm) is not resolved, so no lone "♩ =" is ever drawn.
    const bpm = sanitizeTempoBpm(spec.tempo.bpm);
    const hasBpm = bpm != null;
    // The beat unit is resolved ONLY when there is a bpm to pair it with.  A
    // metronome mark is inherently "beat-unit = number"; StaveTempo.draw gates
    // the ENTIRE "(♩ = N)" scaffolding on `duration`, and when it is given a
    // duration but no bpm it still draws the note glyph, the "=" and the
    // surrounding parens with nothing after the equals -- i.e. "Allegro (♩ = )"
    // (verified against vexflow 5.0.0 stavetempo.js: the "(", glyph and "=" are
    // emitted under `if (duration)`, while the number is a nested `else if
    // (bpm)` that is simply skipped when bpm is falsy).  This bit whenever an
    // author wrote a NAME plus an explicit `duration` but the bpm was absent or
    // sanitized away, since the explicit duration flowed straight through.
    // Gating the duration on `hasBpm` means such a tempo renders its name
    // ALONE (no dangling metronome), while a real "♩ = N" still resolves the
    // duration -- defaulting to a quarter when a bpm was given without one, the
    // overwhelming metronome convention.  A well-formed name+duration+bpm mark
    // is byte-identical (hasBpm true -> the ?? still yields the given duration).
    const rawTempoDuration = hasBpm ? (spec.tempo.duration ?? 'q') : undefined;
    // Sanitize the beat unit AND the augmentation-dot count before they reach
    // StaveTempo, which -- unlike every note duration -- receives them raw.
    // Two degenerate-input failures live in StaveTempo.draw (verified against
    // vexflow 5.0.0 stavetempo.js):
    //   1. the beat glyph is `this.durationToCode[duration]`, a plain object
    //      lookup: an unknown code (e.g. "999", "x") yields `undefined`, which
    //      is then setText/renderText'd -- the note glyph silently VANISHES and
    //      the whole "♩ = N" metronome mark draws nothing/"undefined".
    //   2. the dots are drawn by `for (let i = 0; i < dots; i++) renderText(...)`
    //      with NO upper bound, so a large `dots` (e.g. 1e6) turns tempo
    //      rendering into a render-hanging draw loop -- the same unbounded-loop
    //      class as the beamGroups 0-tick hang.
    // Route the beat unit through the same sanitizeDuration every note uses
    // (unknown -> quarter + warn) and clamp dots to the MAX_DURATION_DOTS the
    // rest of the plugin already honours.  A well-formed tempo is unchanged:
    // sanitizeDuration returns a valid base verbatim and the clamp is a no-op
    // for 0..4 dots, so the mark is byte-identical.
    let tempoDuration: string | undefined;
    let tempoDots = 0;
    if (rawTempoDuration !== undefined) {
      const sd = sanitizeDuration(rawTempoDuration);
      tempoDuration = sd.base;
      const rawDots = Number(spec.tempo.dots ?? sd.dots);
      tempoDots = Number.isFinite(rawDots)
        ? Math.max(0, Math.min(MAX_DURATION_DOTS, Math.floor(rawDots)))
        : 0;
    }
    if (!spec.tempo.name && !hasBpm) {
      problems.push('tempo has neither a name nor a bpm and was skipped');
    } else {
      // Constructed directly rather than via stave.setTempo() because that
      // helper hardcodes the x as `this.x`, giving no way to cancel the
      // clef-width shift that draw() adds -- see tempoLeftShift.
      // A navigation mark (segno / coda / D.S. ...) occupies a fixed,
      // immovable band above the staff (see TEMPO_SHIFT_Y_WITH_MARK); lift the
      // tempo onto its own higher row when one is present so the two do not
      // overprint.  Absent a mark, keep the ordinary lift unchanged.
      // A navigation mark (whether the singular `mark` or any of `marks`)
      // shares the immovable band above the staff, so lift the tempo onto its
      // own higher row when one is present.  tempoAboveMark already accounts
      // for both fields.
      const tempoShiftY = tempoAboveMark ? TEMPO_SHIFT_Y_WITH_MARK : TEMPO_SHIFT_Y;
      if (spec.tempo.name && hasBpm) {
        // Split the mark: hand-draw the NAME and let VexFlow draw ONLY the
        // parenthesised metronome to its right.  VexFlow's StaveTempo.draw
        // positions the "(♩ = N)" group at `this.getWidth() + 3` past the
        // name, but that width is measured on a detached canvas whose font need
        // not match the SVG render font here; when it under-measures (verified:
        // "Andante con moto" + bpm collapsed the word spaces and overprinted
        // the name with the metronome, losing the leading "("), the combined
        // mark is garbled.  Name-only and bpm-only each render correctly on
        // their own, so ONLY the combination is re-routed: the name becomes a
        // d3 overlay (measured in the SAME font it draws in, so the metronome
        // cannot overprint it) and the metronome keeps VexFlow, wrapped in
        // parens via the `parenthesis` flag StaveTempo.draw honours without a
        // `name`.  This matches how every other fragile-VexFlow-placement layer
        // here (title, dynamics, lyrics, nav overflow) is hand-drawn.
        const nameWidth = measureTempoNameWidth(spec.tempo.name);
        const nameX = topStave.x;
        const NAME_METRO_GAP = 8;
        // VexFlow draws the metronome's "(" at this.x + getModifierXShift +
        // xShift(10).  Solving for this.x so the "(" lands NAME_METRO_GAP past
        // the name end cancels the clef/key width getModifierXShift folds in
        // (tempoLeftShift returns that same value), so a wide key signature no
        // longer drifts the metronome relative to the fixed-x name.
        const metroX =
          nameX + nameWidth + NAME_METRO_GAP - 10 - tempoLeftShift(topStave);
        const tempoMark = new StaveTempo(
          { duration: tempoDuration, dots: tempoDots, bpm, parenthesis: true },
          metroX,
          tempoShiftY,
        );
        topStave.addModifier(tempoMark);
        // Align the name's baseline with the metronome's: VexFlow draws the
        // metronome at getYForTopText(1) + yShift, so the name uses the same y.
        const topTextY = typeof topStave.getYForTopText === 'function'
          ? topStave.getYForTopText(1)
          : 0;
        tempoNamePlan = { text: spec.tempo.name, x: nameX, y: topTextY + tempoShiftY };
      } else {
        // Name-only or bpm-only: VexFlow renders each correctly on its own, so
        // keep the single-modifier path exactly as before (byte-identical).
        // Constructed directly rather than via stave.setTempo() because that
        // helper hardcodes the x as `this.x`, giving no way to cancel the
        // clef-width shift that draw() adds -- see tempoLeftShift.
        const tempoMark = new StaveTempo(
          {
            name: spec.tempo.name,
            duration: tempoDuration,
            dots: tempoDots,
            bpm,
          },
          topStave.x - tempoLeftShift(topStave),
          tempoShiftY,
        );
        topStave.addModifier(tempoMark);
      }
    }
  }
  // Navigation marks.  A full jump scheme needs several (segno + D.S.-al-Coda +
  // To-Coda + Coda), so the plan is a LIST: `marks` is the general case and the
  // legacy singular `mark` is treated as a one-element list so both share this
  // loop.  setRepetitionType PUSHES each mark as its own stave modifier
  // (verified: repeated calls stack rather than replace), and the `-left`/
  // `-right` suffix on a name still selects which end of the measure it anchors
  // to, so several marks coexist on one system exactly as a published score
  // sets them.
  const navMarks: string[] = (spec.marks?.length ?? 0) > 0
    ? spec.marks!
    : spec.mark
      ? [spec.mark]
      : [];
  // VexFlow's Repetition glyph is immovable: draw() ignores its xShift/yShift,
  // so every mark on a side lands on the same x AND y and 2+ same-side marks
  // overprint (see NAV_OVERLAY_LABELS).  Let VexFlow draw the FIRST mark on
  // each side -- byte-identical to the single-mark path, so every existing
  // single-`mark` render is unchanged -- and hand-draw the rest, stacked, in
  // the overlay pass (drawNavOverflowMarks).
  const navSideCount: { left: number; right: number } = { left: 0, right: 0 };
  const navOverflowPlans: Array<{ key: string; row: number }> = [];
  for (const markName of navMarks) {
    const key = NAVIGATION_MARKS[markName];
    if (!key) { problems.push(`unknown mark "${markName}"`); continue; }
    const side = NAV_OVERLAY_LABELS[key]?.side ?? 'right';
    if (navSideCount[side] === 0) {
      topStave.setRepetitionType(Repetition.type[key], 0);
    } else {
      // 2nd+ mark on this side -> its own stacked row (row is 1-based).
      navOverflowPlans.push({ key, row: navSideCount[side] });
    }
    navSideCount[side] += 1;
  }
  // Volta (repeat-ending brackets).  NOT drawn via topStave.setVoltaType --
  // that stave modifier spans the whole stave, i.e. the entire system, so it
  // drew the "1." bracket over every bar instead of over the ending it names.
  // Instead resolve which measures each ending covers and defer the draw to
  // the post-format overlay pass, where the notes' resolved x-positions exist.
  //
  // A repeat scheme needs at least TWO brackets (a "1." ending before the
  // repeat-end barline and a "2." ending after it), so the plan is a LIST: the
  // spec's `voltas` array is the general case, and the legacy single `volta`
  // is treated as a one-element list so both paths share this resolver.
  const voltaSpecs: MusicVolta[] = (spec.voltas?.length ?? 0) > 0
    ? spec.voltas!
    : spec.volta
      ? [spec.volta]
      : [];
  const voltaPlans: Array<
    { volta: MusicVolta; systemIndex: number; fromNote: any; toNote: any; endsSystem: boolean }
  > = [];
  if (voltaSpecs.length > 0) {
    // The voltas ride the TOP staff, whose measures define the ranges.  Note
    // counts per measure let a 1-based measure range become flat note indices.
    const topMeasures = measuresOf(staffSpecs[0]);
    const perMeasureCounts = topMeasures.map((m) => (m.notes ?? []).length);
    const flatStartOf = (measure0: number): number =>
      perMeasureCounts.slice(0, measure0).reduce((a, b) => a + b, 0);
    const top = perStaff[0];
    // The default (unanchored) fallback -- the measure closing a repeat-end,
    // else the last measure -- is a SINGLE bar and would stack every
    // unanchored ending on top of one another.  It stays sensible for the lone
    // legacy `volta`, but once there are several endings each really must carry
    // its own `measures`; warn rather than pile them on the same bar.
    voltaSpecs.forEach((v, i) => {
      let from0: number;
      let to0: number;
      if (Array.isArray(v.measures) && v.measures.length === 2) {
        from0 = Math.max(0, Math.floor(v.measures[0]) - 1);
        to0 = Math.min(topMeasures.length - 1, Math.floor(v.measures[1]) - 1);
        if (to0 < from0) { const t = from0; from0 = to0; to0 = t; }
      } else {
        if (voltaSpecs.length > 1) {
          problems.push(
            `volta ${i + 1} of ${voltaSpecs.length} has no \`measures\` range; ` +
            'multiple endings each need one or they overlap. Falling back to a ' +
            'default bar',
          );
        }
        const repeatEnd = topMeasures.findIndex((m) => m.endBar === 'repeat-end');
        from0 = repeatEnd >= 0 ? repeatEnd : Math.max(0, topMeasures.length - 1);
        to0 = from0;
      }

      const firstFlat = flatStartOf(from0);
      const lastFlat = flatStartOf(to0) + Math.max(0, perMeasureCounts[to0] - 1);
      const fromNote = top?.notes[firstFlat] ?? null;
      const toNote = top?.notes[lastFlat] ?? null;
      if (fromNote && toNote && top.noteSystem[firstFlat] === top.noteSystem[lastFlat]) {
        // The ending closes its system when the note after its last note is on
        // a different system (or does not exist) -- i.e. the ending's last bar
        // is the final bar of the line, so its closing barline is the stave's
        // own right edge.  drawVoltaBracket then runs the right hook to that
        // edge instead of the fixed note+24 offset, which collapses/overshoots
        // for a final-measure ending.
        const endsSystem = top.noteSystem[lastFlat] !== top.noteSystem[lastFlat + 1];
        voltaPlans.push({
          volta: v,
          systemIndex: top.noteSystem[firstFlat],
          fromNote,
          toNote,
          endsSystem,
        });
      } else {
        problems.push(
          `volta ${i + 1} range is empty or crosses a system break and was skipped`,
        );
      }
    });
  }
  // Measure numbers.  `measureNumbers: true` labels the FIRST bar of every
  // system, as published scores do (a reader can then find any bar at a
  // glance); the running count begins at `measureNumber` (default 1) and
  // advances by each system's opening-bar GLOBAL index, so a line beginning at
  // the 4th bar reads "4".  Drawn on the top staff of each system only, never
  // repeated down a grand staff's staves.  Without `measureNumbers`, a scalar
  // `measureNumber` keeps its legacy behaviour: the opening bar labelled once,
  // on the first system's top staff.
  // Per-system measure numbers are COLLECTED here and DRAWN in the post-format
  // overlay pass (drawMeasureNumbers), NOT via VexFlow's Stave.setMeasure:
  // verified against the built bundle, setMeasure draws a number for a lone
  // directly-placed stave (the legacy scalar branch below still uses it) but
  // silently draws NOTHING for the staves of a wrapped factory.System layout,
  // so `measureNumbers: true` produced zero numbers on every system.  The
  // overlay uses the same getX()/getYForLine() path the volta and staff-label
  // layers already prove works for wrapped systems.
  const measureNumberPlans: Array<{ stave: any; number: number }> = [];
  if (spec.measureNumbers) {
    // Guard the seed: a degenerate measureNumber would otherwise stringify
    // straight onto every system's first bar as "NaN"/"Infinity"/"1.5" (see
    // sanitizeMeasureNumber).  A dropped value falls back to the default
    // count start of 1, so the numbers still read 1, 2, 3, ...
    const startNumber = sanitizeMeasureNumber(spec.measureNumber) ?? 1;
    // A pickup (anacrusis) opening bar is not counted: published scores leave
    // it unnumbered and call the first FULL bar "measure 1".  Detect it on the
    // TOP staff, whose measures drive the numbering, and (1) never label the
    // pickup bar itself and (2) pull the running count back by one so bar
    // index 1 shows `startNumber` -- i.e. the numbers land on the complete
    // bars, not on the upbeat.  With no pickup the offset is `startNumber`,
    // byte-identical to the previous behaviour.
    // Honour both spellings: the documented top-level `spec.pickup` shorthand
    // and a precise `pickup: true` on the first measure of the top staff.
    const hasPickup = Boolean(spec.pickup || measuresOf(staffSpecs[0])[0]?.pickup);
    const numberOffset = hasPickup ? startNumber - 1 : startNumber;
    for (let s = 0; s < systems.length; s += 1) {
      const firstMeasure = systems[s]?.[0];
      if (firstMeasure == null) continue;
      // The anacrusis carries no number.
      if (hasPickup && firstMeasure === 0) continue;
      const topOfSystem = built.find(
        (b) => b.staffSpec === staffSpecs[0] && b.systemIndex === s,
      );
      if (topOfSystem) {
        measureNumberPlans.push({
          stave: topOfSystem.stave,
          number: numberOffset + firstMeasure,
        });
      }
    }
  } else if (spec.measureNumber != null) {
    // A lone scalar measure number on the first bar: setMeasure works on this
    // single directly-addressed top stave (verified), so keep the light path.
    // Sanitize first, exactly as the running-count seed above does -- setMeasure
    // stringifies its argument with no numeric guard, so an unsanitized bad
    // value would draw "NaN"/"Infinity" as the opening bar label.  A dropped
    // value simply prints no number, which is the honest reading of a
    // meaningless request.
    const scalar = sanitizeMeasureNumber(spec.measureNumber);
    if (scalar != null) topStave.setMeasure(scalar);
  }
  if (spec.section) topStave.setSection(spec.section, 0);

  // Barlines must be set on EVERY staff of the system they belong to, or a
  // grand staff's repeat signs appear on the top line only and the system
  // looks broken.  But `beginBar`/`endBar` are the outer barlines of the whole
  // PIECE, not of each line: applied to every system, `endBar: "final"` drew a
  // closing double-bar at the end of all six lines (measured: +6 <rect>s where
  // +1 was correct).  So beginBar goes on the first system, endBar on the last.
  const lastSystemIndex = vexSystems.length - 1;
  for (const { stave, systemIndex } of built) {
    for (const [value, wanted, apply] of [
      [spec.beginBar, 0, (t: number) => stave.setBegBarType(t)],
      [spec.endBar, lastSystemIndex, (t: number) => stave.setEndBarType(t)],
    ] as Array<[string | undefined, number, (t: number) => void]>) {
      if (!value || systemIndex !== wanted) continue;
      const key = BARLINE_TYPES[value];
      if (key) apply(Barline.type[key]);
      else problems.push(`unknown barline "${value}"`);
    }
  }

  if (staffSpecs.length > 1) {
    // The brace is what makes two staves read as one instrument, and every
    // system needs its own: braced only on line 1, a wrapped grand staff
    // reads as unrelated single staves from line 2 down.
    for (const vexSystem of vexSystems) {
      vexSystem.addConnector('brace');
      vexSystem.addConnector('singleLeft');
      vexSystem.addConnector('singleRight');
    }
  }

  // Resolve a spec note index to a rendered note, tolerating a bad index: a
  // mistyped slur endpoint should cost that one slur, not the whole score.
  const noteAt = (notes: any[], index: number): any => {
    if (!Number.isInteger(index) || index < 0 || index >= notes.length) {
      problems.push(`note index ${index} is out of range (0-${notes.length - 1})`);
      return null;
    }
    return notes[index];
  };

  /**
   * Resolve a span's two endpoints, refusing one that crosses a system break.
   *
   * VexFlow can only draw a Curve/StaveTie/StaveLine/TextBracket between notes
   * on the same System.  Given endpoints on different Systems it raises no
   * error and draws one arc sprawling down the page (measured: 197px of
   * vertical travel across systems 190px apart, against 35px for a legitimate
   * slur).  Proper engraving splits the span into two partial arcs, which
   * VexFlow cannot express -- so refusing and reporting beats drawing a mark
   * that means something different from what was asked for.
   */
  const spanEnds = (
    staff: { notes: any[]; noteSystem: number[] },
    span: { from: number; to: number },
    kind: string,
  ): { from: any; to: any } | null => {
    const from = noteAt(staff.notes, span.from);
    const to = noteAt(staff.notes, span.to);
    if (!from || !to) return null;
    const fromSystem = staff.noteSystem[span.from];
    const toSystem = staff.noteSystem[span.to];
    if (fromSystem !== toSystem) {
      problems.push(
        `${kind} ${span.from}-${span.to} crosses a system break ` +
        `(system ${fromSystem + 1} to ${toSystem + 1}) and was skipped; ` +
        `VexFlow cannot draw a span across two systems. Keep it within one ` +
        `line, or move the break`,
      );
      return null;
    }
    return { from, to };
  };

  // Leading "tr" glyphs for trill lines, drawn in the post-format overlay pass
  // (see drawTrillGlyph): the pre-format Ornament approach was verified not to
  // render, so the glyph is deferred here alongside the note that carries it
  // and the stave it sits above.
  const trillGlyphPlans: Array<{ stave: any; note: any }> = [];

  // Sustain-pedal lines, drawn in the post-format overlay pass (drawPedalLine):
  // like the volta/dynamics overlays they need the press/release notes'
  // resolved x, so they are planned here (system-confined via spanEnds) and
  // drawn after formatting.
  const pedalPlans: Array<{ stave: any; pedal: MusicPedal; fromNote: any; toNote: any }> = [];

  // Spans are per-staff: their indices address that staff's own note list,
  // which runs across every system the staff occupies.
  for (const [staffIndex, { notes, staffSpec, noteSystem, specNotes }] of perStaff.entries()) {
    const staffView = { notes, noteSystem };
    for (const slur of staffSpec.slurs ?? []) {
      const ends = spanEnds(staffView, slur, 'slur');
      if (ends) factory.Curve({ from: ends.from, to: ends.to, options: {} });
    }
    for (const tie of staffSpec.ties ?? []) {
      const ends = spanEnds(staffView, tie, 'tie');
      if (ends) {
        factory.StaveTie({
          from: ends.from, to: ends.to, firstIndices: [0], lastIndices: [0],
        });
      }
    }
    for (const gliss of staffSpec.glissandos ?? []) {
      // VexFlow ships no glissando primitive (there is a TabSlide, but only
      // for tablature), so a labelled StaveLine between the noteheads
      // stands in.
      const ends = spanEnds(staffView, gliss, 'glissando');
      if (ends) {
        factory.StaveLine({
          from: ends.from, to: ends.to, first_indices: [0], last_indices: [0],
          options: { text: gliss.text ?? 'gliss.' },
        });
      }
    }
    // Placement brackets (8va / 8vb / rit.).  factory.TextBracket takes
    // {from, to, text, options:{...}} -- the superscript and position live
    // INSIDE options.  Passing them at the top level (which is the shape the
    // TextBracket *constructor* documents) throws
    // "Cannot read properties of undefined (reading 'superscript')".
    for (const bracket of staffSpec.brackets ?? []) {
      const ends = spanEnds(staffView, bracket, 'bracket');
      if (!ends) continue;
      const rendered = factory.TextBracket({
        from: ends.from, to: ends.to, text: String(bracket.text ?? '8'),
        options: {
          superscript: bracket.superscript ?? '',
          position: bracket.position === 'below' ? 'bottom' : 'top',
        },
      });
      // Dashed is the engraving default, so only an explicit false changes it.
      if (bracket.dashed === false) rendered.setDashed(false);
      // An above-staff bracket shares its band with the tempo mark, so raise
      // it by a line when the two coincide.  Only the top staff carries the
      // tempo, so a lower staff's brackets keep their natural position.
      const sharesBandWithTempo =
        staffIndex === 0 && Boolean(spec.tempo) && bracket.position !== 'below';
      const line = bracket.line
        ?? (sharesBandWithTempo ? BRACKET_LINE_WITH_TEMPO : undefined);
      if (line != null && typeof rendered.setLine === 'function') {
        rendered.setLine(line);
      }
    }
    // Extended trill / vibrato lines.  Same nested-options shape, and `code`
    // is a raw SMuFL codepoint: a small integer draws nothing at all.
    for (const trillLine of staffSpec.trillLines ?? []) {
      const ends = spanEnds(staffView, trillLine, 'trill line');
      if (!ends) continue;
      const name = trillLine.wiggle ?? 'trill';
      const code = WIGGLE_CODES[name];
      if (!code) { problems.push(`unknown wiggle "${name}"`); continue; }
      // A published trill is a "tr" glyph FOLLOWED by the wavy line, not the
      // wavy line alone -- a bare squiggle reads as vibrato/tremolo, not a
      // trill.  VexFlow's VibratoBracket draws only the wave, so the "tr" has
      // to come from elsewhere.  Attaching a `tr` Ornament to the start note
      // before format (iter3) was verified INEFFECTIVE -- it compiled and
      // deployed but drew nothing -- so the glyph is DEFERRED to the
      // post-format overlay pass (drawTrillGlyph), the mechanism the dynamics /
      // measure-number / volta layers already use.  Only for the default
      // `trill` wiggle: vibrato/sawtooth are NOT trills and get no "tr".
      // Guarded against a double "tr" for authors who followed the previous
      // docs and put a `trill` ornament on the first note themselves -- that
      // ornament already draws its own "tr", so skip ours when it is present.
      if (name === 'trill') {
        const startNote = specNotes?.[trillLine.from];
        const alreadyMarked = Array.isArray(startNote?.ornaments)
          && startNote!.ornaments!.includes('trill');
        if (!alreadyMarked) {
          // The wave lives on the start note's system; find that system's stave
          // for this staff so the "tr" is placed above the right line.
          const sys = noteSystem[trillLine.from];
          const entry = built.find(
            (b) => b.staffSpec === staffSpec && b.systemIndex === sys,
          );
          if (entry) trillGlyphPlans.push({ stave: entry.stave, note: ends.from });
        }
      }
      factory.VibratoBracket({ from: ends.from, to: ends.to, options: { code } });
    }
    // Sustain-pedal markings.  Resolved here (system-confined via spanEnds so a
    // pedal cannot stretch across a line break) and drawn in the post-format
    // overlay pass, since drawPedalLine reads the press/release notes' resolved
    // x.  Anchored to the stave of the system the press falls on for this staff.
    for (const pedal of staffSpec.pedals ?? []) {
      const ends = spanEnds(staffView, pedal, 'pedal');
      if (!ends) continue;
      const sys = noteSystem[pedal.from];
      const entry = built.find(
        (b) => b.staffSpec === staffSpec && b.systemIndex === sys,
      );
      if (entry) {
        pedalPlans.push({
          stave: entry.stave, pedal, fromNote: ends.from, toNote: ends.to,
        });
      }
    }
  }

  // Tuplets MUST be built before BOTH beam generation and factory.draw().
  //
  // Two reasons, in order of subtlety:
  //  1. The Tuplet constructor calls attach(), which rescales each spanned
  //     note's tick value so the group occupies the correct beat time.  Done
  //     after factory.draw() the notes are already formatted at face value and
  //     the rescale has no effect on spacing.
  //  2. It must also precede Beam.generateBeams below.  generateBeams groups
  //     notes by accumulated *tick* value against the meter's beat grid, and
  //     reads note.getTuplet() to keep a group from straddling a tuplet.  Run
  //     before the rescale it sees three triplet-eighths as three PLAIN
  //     eighths (3/8 of a beat) and beams across the triplet boundary into the
  //     next beat group -- verified: a leading eighth-note triplet in 4/4 came
  //     out beamed 0-1 / 2-3, cutting the triplet in half.  VexFlow is built
  //     for this ordering: generateBeams' own trailing pass then sets each
  //     beamed tuplet's bracket per convention (beamed -> number only).
  // Iterates perStaff, NOT built: `built` now holds one entry per
  // (staff x system), so a flat index would be re-applied to every system's
  // slice -- a tuplet on notes 0-2 would appear on every line.
  for (const { staffSpec, notes, noteSystem } of perStaff) {
    for (const tuplet of staffSpec.tuplets ?? []) {
      const { from, to } = tuplet;
      if (!Number.isInteger(from) || !Number.isInteger(to)
          || from < 0 || to >= notes.length || to <= from) {
        problems.push(
          `tuplet ${from}-${to} is not a valid range (0-${notes.length - 1}, at least two notes)`,
        );
        continue;
      }
      // A tuplet bracket is drawn on one stave, so it cannot straddle a break.
      if (noteSystem[from] !== noteSystem[to]) {
        problems.push(
          `tuplet ${from}-${to} crosses a system break and was skipped`,
        );
        continue;
      }
      const members = notes.slice(from, to + 1);
      const num = tuplet.num ?? members.length;
      const inSpaceOf = tuplet.inSpaceOf ?? 2;
      // Guard the two user-supplied tuplet counts before they reach VexFlow's
      // tick machinery.  Tuplet.attach() rescales every spanned note's tick by
      // Fraction(notesOccupied, numNotes): a count below 1 or non-integer makes
      // that a division-by-zero / NaN tick and the Formatter's justification
      // loop NEVER RETURNS (a 30s hang, blank canvas -- the same
      // non-converging-formatter failure sanitizeDuration and sanitizeBeamGroups
      // defend), and an ABSURDLY LARGE count drives the same rescale to a
      // degenerate tick (near-zero -> hang; huge -> a wildly overfull bar) while
      // printing a ratio label like "3:1000" that runs off the system (verified
      // against the served bundle).  sanitizeTupletCounts caps BOTH ends -- the
      // last numeric spec input to gain the upper bound its clamp/reject
      // siblings (clampKeyOctave, sanitizeTempoBpm, sanitizeMeasureNumber, ...)
      // already carry.  Skip with a problem note, matching this loop's other
      // invalids.  The defaults (num = members.length, inSpaceOf = 2) always
      // pass, so the ordinary triplet/quintuplet path is byte-identical.
      const counts = sanitizeTupletCounts(num, inSpaceOf);
      if (!counts) {
        problems.push(
          `tuplet ${from}-${to} has invalid num/inSpaceOf `
          + `(${num}/${inSpaceOf}); both must be integers between 1 and ${MAX_TUPLET_COUNT}`,
        );
        continue;
      }
      const options: Record<string, unknown> = {
        numNotes: num,
        notesOccupied: inSpaceOf,
        // "above"/"below" -> Tuplet.LOCATION_TOP (1) / LOCATION_BOTTOM (-1).
        location: tuplet.position === 'below' ? -1 : 1,
      };
      if (tuplet.ratioed != null) options.ratioed = tuplet.ratioed;
      if (tuplet.bracketed != null) options.bracketed = tuplet.bracketed;
      // factory.Tuplet enqueues on the factory's render list, so factory.draw()
      // renders it -- no explicit context/draw is needed, unlike generated beams.
      factory.Tuplet({ notes: members, options });
    }
  }

  // Beams must be constructed BEFORE factory.draw() (a beamed note suppresses
  // its own flag during drawing, so a beam created afterwards renders on top of
  // flags already there -- verified: 8 flags remain, versus 0 when the beam is
  // built first) but AFTER the tuplets above, so generateBeams sees the
  // rescaled tuplet ticks and groups within tuplet boundaries.  This is the
  // opposite ordering from hairpins below, which need the resolved x-positions
  // that only exist after formatting.
  const beams: any[] = [];
  // autoBeam runs per `built` entry: byMeasure is already the per-system slice,
  // and beaming is per measure anyway, so each system beams its own bars.
  //
  // Resolved PER STAFF so a `staves[]` entry can set its own flag: the entry's
  // value wins when present (including an explicit `false`, which opts one part
  // out of a spec-level `autoBeam`), otherwise the spec's applies.
  for (const { byMeasure, staffSpec, beamGroupsByMeasure } of built) {
    const autoBeam = staffSpec.autoBeam ?? spec.autoBeam;
    if (!autoBeam) continue;
    const groupSource = staffSpec.beamGroups?.length
      ? staffSpec.beamGroups
      : spec.beamGroups;
    // Drop degenerate [n,d] pairs before they reach VexFlow: a 0-tick group
    // (numerator 0) hangs generateBeams in an infinite loop, a 0-denominator
    // beams the whole bar into one group (see sanitizeBeamGroups).
    const validGroups = sanitizeBeamGroups(groupSource);
    // An explicit `beamGroups` is the author's deliberate override and applies
    // to EVERY bar unchanged.  With none, each bar falls back to ITS OWN
    // meter's grouping (beamGroupsByMeasure), so a mid-score meter change
    // beams correctly instead of forcing the opening meter's beats on the
    // changed bars -- see beamGroupsForMeter.  A single-meter score resolves
    // every bar to the opening grouping, so its beaming is byte-identical.
    const explicitGroups = validGroups
      ? validGroups.map(([n, d]) => new Fraction(n, d))
      : undefined;
    // Beam per measure, never across a barline -- a beam spanning a bar is
    // wrong engraving, and the flat note list would happily produce one.
    byMeasure.forEach((measureNotes, localMi) => {
      const groups = explicitGroups
        ?? beamGroupsByMeasure?.[localMi]
        ?? defaultBeamGroups;
      const generated = Beam.generateBeams(measureNotes, {
        ...(groups ? { groups } : {}),
        // A rest breaks a beam group in ordinary engraving; beaming over one
        // is a deliberate stylistic choice, not a default.
        beamRests: false,
      });
      generated.forEach(applyHouseBeamSlope);
      beams.push(...generated);
    });
  }
  // Explicit beams address the staff's FLAT note list, so they resolve against
  // perStaff -- and a beam, being drawn on one stave, cannot cross a break.
  for (const { staffSpec, notes, noteSystem } of perStaff) {
    for (const beam of staffSpec.beams ?? []) {
      const from = beam.from;
      const to = beam.to;
      if (!Number.isInteger(from) || !Number.isInteger(to)
          || from < 0 || to >= notes.length || to <= from) {
        problems.push(
          `beam ${from}-${to} is not a valid range (0-${notes.length - 1}, at least two notes)`,
        );
        continue;
      }
      if (noteSystem[from] !== noteSystem[to]) {
        problems.push(`beam ${from}-${to} crosses a system break and was skipped`);
        continue;
      }
      beams.push(factory.Beam({ notes: notes.slice(from, to + 1), options: {} }));
    }
  }
  // Cross-staff beams: a keyboard run threaded from one staff into another
  // under ONE beam.  A per-staff `beams`/`autoBeam` cannot join it (each
  // addresses a single staff's own notes), so the members are named at the
  // SPEC level as [staffIndex, noteIndex] pairs in PLAYING ORDER.  Resolved
  // against perStaff (the flat, rendered StaveNotes of each staff) and built
  // here -- BEFORE factory.draw(), like every other beam, because a beamed
  // note suppresses its own flag during the draw pass -- then drawn in the
  // post-format pass below where the notes' resolved x/y across both staves
  // exist.  VexFlow draws the beam by reading each member's own stave Y, which
  // is the standard cross-staff mechanism; the group is refused (not drawn
  // wrongly) when a member is out of range or the run spans a system break.
  const crossStaffBeamObjs: any[] = [];
  for (const csb of spec.crossStaffBeams ?? []) {
    if (staffSpecs.length < 2) {
      problems.push('crossStaffBeams is only meaningful with two or more staves; skipped');
      continue;
    }
    const pairs = csb.notes ?? [];
    if (pairs.length < 2) {
      problems.push('crossStaffBeams entry needs at least two notes; skipped');
      continue;
    }
    const members: any[] = [];
    let system = -1;
    let ok = true;
    for (const pair of pairs) {
      const si = Array.isArray(pair) ? pair[0] : undefined;
      const ni = Array.isArray(pair) ? pair[1] : undefined;
      const staff = si != null ? perStaff[si] : undefined;
      if (!staff || !Number.isInteger(ni) || (ni as number) < 0
          || (ni as number) >= staff.notes.length) {
        problems.push(
          `crossStaffBeams member [${si}, ${ni}] is out of range and the group was skipped`,
        );
        ok = false;
        break;
      }
      const sys = staff.noteSystem[ni as number];
      if (system === -1) system = sys;
      else if (sys !== system) {
        problems.push('crossStaffBeams group crosses a system break and was skipped');
        ok = false;
        break;
      }
      members.push(staff.notes[ni as number]);
    }
    if (!ok) continue;
    // A beam requires one shared stem side (Stem.UP = 1, Stem.DOWN = -1);
    // "up" is the usual keyboard case, placing the beam between the staves.
    const dir = csb.stemDirection === 'down' ? -1 : 1;
    for (const m of members) {
      if (typeof m.setStemDirection === 'function') m.setStemDirection(dir);
    }
    // autoStem = false so the beam keeps the stem side just forced rather than
    // re-deriving one per note from staff position (which would split the run).
    const beam = new Beam(members, false);
    applyHouseBeamSlope(beam);
    crossStaffBeamObjs.push(beam);
  }

  // Cross-staff slurs / ties: a phrase arc or a single held pitch passed from
  // one staff into the other.  Like crossStaffBeams they are named at the SPEC
  // level as [staffIndex, noteIndex] pairs and resolved against perStaff; but
  // -- exactly like the per-staff slurs/ties above -- they are created through
  // factory.Curve / factory.StaveTie, which are factory-owned and drawn in
  // factory.draw()'s own pass (no separate draw loop, unlike the `new Beam`
  // objects).  VexFlow positions each from each endpoint note's own resolved
  // stave Y, the same cross-staff mechanism the beam uses, so the arc spans
  // the two staves.  Refused (not drawn) when an endpoint is out of range or
  // the two ends land on different systems, matching spanEnds' same-system
  // rule that every other span obeys.
  for (const css of spec.crossStaffSlurs ?? []) {
    if (staffSpecs.length < 2) {
      problems.push('crossStaffSlurs is only meaningful with two or more staves; skipped');
      continue;
    }
    const resolveEnd = (pair: any): { note: any; system: number } | null => {
      const si = Array.isArray(pair) ? pair[0] : undefined;
      const ni = Array.isArray(pair) ? pair[1] : undefined;
      const staff = si != null ? perStaff[si] : undefined;
      if (!staff || !Number.isInteger(ni) || (ni as number) < 0
          || (ni as number) >= staff.notes.length) {
        problems.push(
          `crossStaffSlurs endpoint [${si}, ${ni}] is out of range and the span was skipped`,
        );
        return null;
      }
      return { note: staff.notes[ni as number], system: staff.noteSystem[ni as number] };
    };
    const from = resolveEnd(css.from);
    const to = resolveEnd(css.to);
    if (!from || !to) continue;
    if (from.system !== to.system) {
      problems.push('crossStaffSlurs span crosses a system break and was skipped');
      continue;
    }
    if (css.curve === 'tie') {
      // A tie holds ONE pitch across the staff change; firstIndices/lastIndices
      // [0] tie the primary (first) notehead, matching the per-staff tie path.
      factory.StaveTie({
        from: from.note, to: to.note, firstIndices: [0], lastIndices: [0],
      });
    } else {
      // The default phrase arc between two different pitches.
      factory.Curve({ from: from.note, to: to.note, options: {} });
    }
  }

  factory.draw();

  // Beams from Beam.generateBeams are not factory-owned, so the factory's own
  // draw pass does not render them; they need an explicit context.  Those from
  // factory.Beam are already drawn and must not be drawn twice.
  for (const beam of beams) {
    if (typeof beam.getContext === 'function' && !beam.getContext()) {
      beam.setContext(factory.getContext()).draw();
    }
  }
  // Secondary-voice auto-beams are Beam.generateBeams output too, so they need
  // the same explicit draw pass.
  for (const beam of secondaryBeams) {
    if (typeof beam.getContext === 'function' && !beam.getContext()) {
      beam.setContext(factory.getContext()).draw();
    }
  }
  // Cross-staff beams are `new Beam` output (not factory-owned), so like the
  // generateBeams beams they need an explicit context + draw pass now that the
  // notes' resolved x/y across both staves exist.
  for (const beam of crossStaffBeamObjs) {
    if (typeof beam.getContext === 'function' && !beam.getContext()) {
      beam.setContext(factory.getContext()).draw();
    }
  }

  // Hairpins are drawn after factory.draw() because StaveHairpin reads the
  // resolved x-positions of its endpoint notes, which do not exist until
  // formatting has run.  It also takes camelCase firstNote/lastNote -- the
  // snake_case form throws "BadArguments: Hairpin needs to have either
  // firstNote or lastNote".
  for (const { notes, staffSpec, noteSystem } of perStaff) {
    for (const hairpin of staffSpec.hairpins ?? []) {
      // Same-system requirement as the other spans: a hairpin reads the
      // resolved x of both endpoints and would otherwise stretch across the
      // page rather than wedging under one line.
      const ends = spanEnds({ notes, noteSystem }, hairpin, 'hairpin');
      if (!ends) continue;
      const firstNote = ends.from;
      const lastNote = ends.to;
      const kind = hairpin.type === 'dim' || hairpin.type === 'decresc'
        ? StaveHairpin.type.DECRESC
        : StaveHairpin.type.CRESC;
      new StaveHairpin({ firstNote, lastNote }, kind)
        .setContext(factory.getContext())
        .setPosition(Modifier.Position.BELOW)
        .draw();
    }
  }

  if (problems.length) {
    // Advisory, not fatal: the staff rendered, but part of the spec did not
    // take effect and silence would make that look intentional.
    console.warn('musicPlugin: %s', problems.join('; '));
  }

  const svgRoot = container.querySelector('svg');

  // Recolour AFTER every VexFlow draw call.  Hairpins, beams and tuplets are
  // drawn above and add elements of their own, so a recolour done earlier
  // would leave those later additions black.
  if (isDarkMode) applyMusicDarkTheme(svgRoot as SVGElement | null);

  // Harp pedal overlay — anchor to each note's resolved x-position after
  // VexFlow has completed layout/formatting.  Drawn after the recolour
  // because it picks its own theme-aware ink and must not be remapped.
  const svgEl = svgRoot;
  if (svgEl) {
    const svg = d3.select(svgEl);
    // Title block above the system.  Overlay-drawn (VexFlow has no page title)
    // and, like the other overlays, after the recolour so its theme-aware ink
    // is not remapped.  `width` is the full canvas so the title centres and
    // the composer credit right-aligns to the margin.
    drawTitleBlock(d3, svg, spec, width, isDarkMode);
    // Tempo NAME, when paired with a metronome: hand-drawn so VexFlow's
    // under-measured metronome group cannot overprint it (see drawTempoName).
    // Null (a no-op) for name-only / bpm-only marks, which VexFlow draws itself.
    drawTempoName(d3, svg, tempoNamePlan, isDarkMode);
    // Instrument / part labels in the left gutter, one per named staff.  Drawn
    // here (post-format) so each staff's resolved x/y are available.
    drawStaffLabels(d3, svg, built, isDarkMode);
    // Measure numbers above the first bar of each system.  Collected earlier
    // (measureNumberPlans) and drawn here because VexFlow's setMeasure is
    // ineffective for the wrapped factory.System staves -- see
    // drawMeasureNumbers.  Empty for scores without `measureNumbers`, so this
    // no-ops and the layout is unchanged for them.
    drawMeasureNumbers(d3, svg, measureNumberPlans, isDarkMode);
    // Overflow navigation marks: the 2nd+ mark on a side, which VexFlow's
    // immovable Repetition glyph would overprint (see drawNavOverflowMarks).
    // Anchored to the FIRST system's top staff, the same staff the primary
    // marks ride.  Empty for the common single-mark case, so this no-ops and
    // that path is byte-identical.
    drawNavOverflowMarks(d3, svg, topStave, navOverflowPlans, isDarkMode);
    // Leading "tr" glyphs for trill lines.  Deferred from the pre-format span
    // pass (drawTrillGlyph) because attaching the glyph as a note Ornament
    // before format was verified not to render; drawn here where the note's
    // resolved x exists, immediately left of the wave the VibratoBracket drew.
    for (const { stave, note } of trillGlyphPlans) {
      drawTrillGlyph(d3, svg, stave, note, isDarkMode);
    }
    // Volta brackets over their measure ranges -- one per ending (a "1." and a
    // "2." for the usual repeat scheme).  Each is anchored to the TOP staff of
    // the system its ending falls on (built entries are one per staff x
    // system); drawn here because they need the endpoint notes' resolved
    // x-positions.  Sharing one band above the staff, they never overlap
    // vertically because their measure ranges do not overlap horizontally.
    for (const plan of voltaPlans) {
      const entry = built.find(
        (b) => b.staffSpec === staffSpecs[0] && b.systemIndex === plan.systemIndex,
      );
      if (entry) {
        drawVoltaBracket(
          d3, svg, entry.stave, plan.volta,
          plan.fromNote, plan.toNote, isDarkMode, plan.endsSystem,
        );
      }
    }
    // Sustain-pedal lines below the staff, on their own band beneath the
    // dynamics band.  Planned above (system-confined) and drawn here where the
    // press/release notes' resolved x exist.
    for (const plan of pedalPlans) {
      drawPedalLine(
        d3, svg, plan.stave, plan.pedal,
        plan.fromNote, plan.toNote, isDarkMode,
      );
    }
    // Multi-measure rests: the H-bar + bar count, centred over each empty
    // measure's ghost-note spacer.  Drawn here (post-format) because the
    // spacer's resolved x only exists after formatting -- like the pedal /
    // volta layers.  Empty for scores without any `multiRest`, so this no-ops
    // and their layout is unchanged.
    for (const { stave, multiRests } of built) {
      for (const mr of multiRests) {
        const cx = mr.ghost && typeof mr.ghost.getAbsoluteX === 'function'
          ? mr.ghost.getAbsoluteX() : null;
        if (cx == null) continue;
        drawMultiMeasureRest(d3, svg, stave, cx, mr.count, isDarkMode);
      }
    }
    // Whole-bar centering: a lone whole note or whole rest is CENTERED in its
    // measure in every published house style, but VexFlow leaves it jammed at
    // the left start-x with the rest of the bar empty (verified).  Restricted
    // to the unambiguous single-staff, single-system, single-measure,
    // single-bare-tickable case so it cannot disturb vertical alignment across
    // a grand staff, a second voice, a cross-staff span, or the spacing of a
    // multi-note bar -- all of which keep their exact current layout.  See
    // shouldCenterLoneWholeBar (which case) and centerLoneWholeBar (geometry).
    if (built.length === 1
        && !(spec.crossStaffBeams && spec.crossStaffBeams.length)
        && !(spec.crossStaffSlurs && spec.crossStaffSlurs.length)) {
      const only = built[0];
      const vs = only.staffSpec.voices;
      const singleVoice = !vs || vs.length <= 1;
      if (singleVoice
          && only.byMeasure.length === 1 && only.byMeasure[0].length === 1
          && only.notes.length === 1
          && shouldCenterLoneWholeBar(only.specNotes[0], numBeats, beatValue)) {
        centerLoneWholeBar(svg, only.stave, only.notes[0]);
      }
    }
    // specNotes is the FLATTENED note list, parallel to `notes`; indexing
    // staffSpec.notes would find nothing on a measures-based staff.
    for (const { stave, notes, specNotes } of built) {
      const topLineY = typeof stave.getYForLine === 'function' ? stave.getYForLine(0) : 20;
      notes.forEach((note: any, i: number) => {
        const specNote = specNotes[i];
        if (!specNote?.harpPedal) return;
        const noteX = typeof note.getAbsoluteX === 'function' ? note.getAbsoluteX() : null;
        if (noteX == null) return;
        drawHarpPedalDiagram(d3, svg, specNote.harpPedal, noteX, topLineY - 14, isDarkMode);
      });
      // Lyrics share the post-format overlay pass: both need resolved note
      // x-positions and both pick theme-aware ink, so must run after the
      // dark-theme recolour rather than be remapped by it.
      drawLyricLayer(d3, svg, stave, notes, specNotes, isDarkMode);
      // Dynamics (p/mf/f...) share the same overlay pass and, for the same
      // reasons, are drawn below the staff here rather than via TextDynamics
      // (whose `line` option VexFlow 5.0 ignores -- see drawDynamicsLayer).
      drawDynamicsLayer(d3, svg, stave, notes, specNotes, isDarkMode);
      // Breath / caesura marks above the staff, just after each marked note.
      // drawBreathMarks (and drawCueNotes below) were implemented and their
      // canvas headroom reserved (needsRoomAbove counts `breath`, the cue
      // scale is size-only), and both fields are documented in the skill
      // prompt -- but the invocation was omitted from this overlay pass, so
      // `breath` and `cue` were silently DEAD: fully specced, documented and
      // budgeted for, yet drawing nothing.  Wire them in alongside the other
      // post-format overlays (same reason they belong here: they read each
      // note's resolved x and pick their own theme-aware ink, so they must run
      // after formatting and after the dark-theme recolour).  A note without
      // `breath`/`cue` is untouched, so the no-mark path is unchanged.
      drawBreathMarks(d3, svg, stave, notes, specNotes, isDarkMode);
      // Cue notes: shrink notes flagged `cue` to ~2/3 size in place.  Takes no
      // d3/ink (it transforms the already-rendered note group), so its
      // signature differs from the layers above.
      drawCueNotes(svg, notes, specNotes);
    }
  }
}
