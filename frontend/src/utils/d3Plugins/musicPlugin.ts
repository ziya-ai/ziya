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
 * An explicit beam over a run of notes, by index into the staff's own note
 * list.  Use this only when the automatic grouping is wrong; `autoBeam` on the
 * spec handles the ordinary case.
 */
export interface MusicBeam extends MusicSpan {}

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

/** Friendly ornament name -> VexFlow code.  Closed for the same reason. */
export const ORNAMENT_CODES: Readonly<Record<string, string>> = {
  trill: 'tr',
  mordent: 'mordent',
  'mordent-inverted': 'mordent_inverted',
  turn: 'turn',
  'turn-inverted': 'turn_inverted',
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
  /** Beats per minute. */
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
  /** Explicit beam groups.  Overrides nothing; adds to any autoBeam output. */
  beams?: MusicBeam[];
  /** Tuplets (triplets etc.) over runs of this staff's notes. */
  tuplets?: MusicTuplet[];
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
   * Tuplets (triplets, quintuplets, ...) for a single-staff spec, by note
   * index.  Rescales the spanned notes' beat time and draws the number
   * bracket; see MusicTuplet.
   */
  tuplets?: MusicTuplet[];
  /**
   * Grand staff / multi-staff system.  When present, `notes` and the
   * per-staff span lists above are ignored in favour of these, and a brace
   * plus left/right connectors join the staves.
   */
  staves?: MusicStaff[];
  /** Tempo marking above the top staff. */
  tempo?: MusicTempo;
  /** Navigation mark, a key of NAVIGATION_MARKS. */
  mark?: string;
  /** Repeat-ending bracket above the top staff. */
  volta?: MusicVolta;
  /** Opening barline, a key of BARLINE_TYPES. */
  beginBar?: string;
  /** Closing barline, a key of BARLINE_TYPES. */
  endBar?: string;
  /** Measure number shown above the staff. */
  measureNumber?: number;
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
 */
export function toEasyScoreKey(key: string): string {
  const match = /^([a-gA-G])([#b]{0,2})\/(-?\d+)$/.exec(String(key).trim());
  if (match) return match[1].toUpperCase() + match[2] + match[3];
  // Already in EasyScore form (or unrecognised): pass through with any stray
  // slash removed so a partially-correct key still parses.
  return String(key).trim().replace('/', '');
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
 */
export function toStaveNoteKey(key: string): string {
  const raw = String(key).trim();
  // Already slash form (letter[accidental]/octave): keep as-is.
  if (/^[a-gA-G][#b]{0,2}\/-?\d+$/.test(raw)) return raw;
  // EasyScore form (letter[accidental]octave, no slash): insert the slash.
  const match = /^([a-gA-G])([#b]{0,2})(-?\d+)$/.exec(raw);
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

/** Build the EasyScore note list for a spec, parenthesising chords. */
export function buildNoteString(notes: MusicNoteSpec[], clef: string = 'treble'): string {
  const restPitch = REST_PITCH_FOR_CLEF[clef] ?? REST_PITCH_FOR_CLEF.treble;
  return notes
    .map((n) => {
      // Neutralise a degenerate/unknown duration BEFORE it reaches EasyScore:
      // an unrecognised code builds a 0/NaN-tick note that hangs the formatter.
      const { base, dots } = sanitizeDuration(n.duration);
      const dotStr = '.'.repeat(dots);
      if (n.rest) {
        // The augmentation dot goes AFTER the /r, not on the duration:
        // "B4/q./r" parses as a NOTE (verified: rests=0, draws a notehead)
        // while "B4/q/r." is a dotted rest.
        return `${restPitch}/${base}/r${dotStr}`;
      }
      const keys = (n.keys ?? []).map(toEasyScoreKey);
      const pitch = keys.length > 1 ? `(${keys.join(' ')})` : keys[0];
      return `${pitch}/${base}${dotStr}`;
    })
    .join(', ');
}

/**
 * True when `value` is a non-empty array of playable entries.  A rest counts:
 * a measure of silence is legitimate content, and rejecting it would make a
 * score that opens with a rest unrenderable.
 */
const hasNotes = (value: any): boolean => Array.isArray(value) && value.length > 0;

/**
 * A staff's measures, treating a flat `notes` list as a single measure.
 *
 * Normalising here means the render path has exactly one shape to walk, so a
 * multi-measure staff is not a second code path that can drift from the
 * single-measure one.
 */
function measuresOf(staffSpec: { notes?: MusicNoteSpec[]; measures?: MusicMeasure[] }): MusicMeasure[] {
  if (Array.isArray(staffSpec.measures) && staffSpec.measures.length > 0) {
    return staffSpec.measures;
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
      && spec.measures.some((m: any) => hasNotes(m?.notes))) return true;
  return (
    Array.isArray(spec.staves) &&
    spec.staves.length > 0 &&
    // A staves list of empty staves is not renderable, so require real notes
    // somewhere rather than accepting the key's mere presence.
    spec.staves.some((staff: any) => hasNotes(staff?.notes)
      || (Array.isArray(staff?.measures)
          && staff.measures.some((m: any) => hasNotes(m?.notes))))
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
    || (Array.isArray(s.measures) && s.measures.some((m: any) => hasNotes(m?.notes)))
    || (Array.isArray(s.staves) && s.staves.length > 0
        && s.staves.some((staff: any) => hasNotes(staff?.notes)
          || (Array.isArray(staff?.measures)
              && staff.measures.some((m: any) => hasNotes(m?.notes)))))
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
  const x2 = endNoteX + 24;
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
    StaveHairpin, Articulation, Ornament, Modifier, GhostNote,
    Barline, Repetition, ChordSymbol, StaveTempo, BarNote, Beam, Fraction,
    GraceNote, GraceNoteGroup,
  } = Vex as any;

  container.innerHTML = '';
  /** Non-fatal spec problems, reported together rather than failing the render. */
  const problems: string[] = [];

  // A single-staff spec is treated as a one-element multi-staff spec, so the
  // grand staff is not a second code path that can drift from the first.
  const staffSpecs: MusicStaff[] = (spec.staves?.length ?? 0) > 0
    ? spec.staves!
    : [{
        clef: spec.clef, keySignature: spec.keySignature, notes: spec.notes,
        name: spec.name,
        measures: spec.measures,
        slurs: spec.slurs, ties: spec.ties,
        glissandos: spec.glissandos, hairpins: spec.hairpins,
        brackets: spec.brackets, trillLines: spec.trillLines,
        beams: spec.beams, tuplets: spec.tuplets,
      }];

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
    measuresOf(s).map((m) => estimateMeasureWidthFromNotes(m.notes ?? [])));
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
  // and the single-system behaviour is preserved byte-for-byte.
  const wrapEnabled = spec.width == null;
  const systemPlan = wrapEnabled
    ? planSystemBreaks(
        measureWidths, explicitBreaks,
        spec.maxSystemWidth ?? DEFAULT_MAX_SYSTEM_WIDTH,
      )
    : [measureWidths.map((_, i) => i)];
  // Degenerate specs (no measures at all) still need one system to draw into.
  const systems = systemPlan.length > 0 ? systemPlan : [[]];

  const contentWidth = wrapEnabled
    // Widest planned system, so every system shares one canvas width and the
    // right-hand margins line up down the page as engraving requires.
    ? Math.max(340, ...systems.map((sys) => estimateSystemWidth(measureWidths, sys)))
    : Math.max(340, 110 + longestStaff * 78 + mostBarlines * 24);
  const width = (spec.width ?? contentWidth) + labelGutter;

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
      (spec.width != null
        ? 'Remove the explicit `width` to enable automatic system breaks, or '
        : 'Split the music across more measures, or ') +
      'set `maxSystemWidth`, or add `"systemBreak": true` to a measure',
    );
  }
  // Dynamics sit below the staff and hairpins below those, so a fixed 160
  // clips them.  Grow the canvas only when those features are present, to
  // avoid padding every plain staff with dead space.
  const needsRoomBelow = staffSpecs.some((s) =>
    notesOf(s).some((n) =>
      n.dynamic
      // A below-staff chord symbol (roman-numeral analysis) needs the same
      // room a dynamic does.
      || (typeof n.chordSymbol === 'object' && n.chordSymbol?.position === 'below')
      // Lyrics are underlaid beneath the staff and need the same headroom.
      || n.lyric != null)
    || (s.hairpins?.length ?? 0) > 0
    || (s.brackets ?? []).some((b) => b.position === 'below')
    // A below-staff tuplet number sits where a dynamic would.
    || (s.tuplets ?? []).some((t) => t.position === 'below'));
  // Tempo / marks / volta / measure number all render ABOVE the top staff and
  // are clipped without headroom.
  const needsRoomAbove = Boolean(
    spec.tempo || spec.mark || spec.volta ||
    spec.measureNumber != null || spec.section
    // Brackets and trill lines also occupy the band above the staff.
    || staffSpecs.some((s) =>
      (s.brackets ?? []).some((b) => b.position !== 'below')
      || (s.trillLines?.length ?? 0) > 0
      // A tuplet number defaults above the staff.
      || (s.tuplets ?? []).some((t) => t.position !== 'below')),
  );
  // Both the tempo lift (TEMPO_SHIFT_Y) and the bracket lift
  // (BRACKET_LINE_WITH_TEMPO) push material further up than the previous flat
  // 40px allowance covered, so a stacked tempo + bracket needs more room or
  // the topmost glyph is clipped at y<0.
  // A tempo lifted onto its own row above a navigation mark
  // (TEMPO_SHIFT_Y_WITH_MARK = -64, 30px higher than the ordinary -34 whose
  // topmost glyph sat at y≈26) needs a taller reserve or it clips at y<0.
  const tempoAboveMark = Boolean(spec.tempo && spec.mark);
  const roomAbove = needsRoomAbove
    ? (tempoAboveMark
        ? 76
        : spec.tempo && staffSpecs.some((s) => (s.brackets?.length ?? 0) > 0) ? 60 : 46)
    : 0;
  // The title block sits above everything else, so its height is added to the
  // canvas and the whole system is pushed down by the same amount -- see
  // titleY below.  Computed once so the reserve and the draw agree.
  const titleH = titleBlockHeight(spec);
  const systemSpacing = spec.systemSpacing ?? DEFAULT_SYSTEM_SPACING;
  /** Vertical room one system (all its staves) occupies. */
  const perSystemHeight = (needsRoomBelow ? 230 : 160) * staffSpecs.length;
  const height = spec.height
    ?? perSystemHeight * systems.length
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
    /** Which planned system this stave belongs to. */
    systemIndex: number;
    /** Flat index, within the staff, of this entry's first note. */
    noteOffset: number;
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
    /** Accumulated across systems, for the per-staff span view. */
    const staffNotes: any[] = [];
    const staffNoteSystem: number[] = [];

  systems.forEach((systemMeasures, systemIndex) => {
    // This staff's slice of the score for this system.  A staff with fewer
    // measures than the plan contributes an empty stave here rather than
    // being dropped, so the system keeps its full complement of staves and
    // the brace/connectors still line up.
    const measures = systemMeasures
      .map((m) => allMeasures[m])
      .filter((m): m is MusicMeasure => m != null);
    const noteOffset = staffNotes.length;
    const specNotes = measures.flatMap((m) => m.notes ?? []);
    // easyNotes is the flat note list that span indices address; tickables
    // additionally carries the BarNotes, in playing order.
    const easyNotes: any[] = [];
    const tickables: any[] = [];
    const byMeasure: any[][] = [];

    measures.forEach((measure, measureIndex) => {
      if (measureIndex > 0) {
        // A BarNote is what draws a barline INSIDE a stave; setBegBarType /
        // setEndBarType only reach the stave's two outer edges.
        tickables.push(new BarNote(barlineBetween(measures[measureIndex - 1], measure)));
      }
      const measureNotes = measure.notes ?? [];
      const noteStrings = buildNoteString(measureNotes, clef);
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
    // space for them.
    easyNotes.forEach((note: any, i: number) => {
      const specNote = specNotes[i];
      if (!specNote) return;
      for (const name of specNote.articulations ?? []) {
        const code = ARTICULATION_CODES[name];
        if (!code) { problems.push(`unknown articulation "${name}"`); continue; }
        note.addModifier(new Articulation(code).setPosition(Modifier.Position.ABOVE), 0);
      }
      for (const name of specNote.ornaments ?? []) {
        const code = ORNAMENT_CODES[name];
        if (!code) { problems.push(`unknown ornament "${name}"`); continue; }
        note.addModifier(new Ornament(code), 0);
      }
      // Grace notes: an appoggiatura/acciaccatura or ornamental run engraved
      // small BEFORE the main note.  VexFlow models them as a GraceNoteGroup
      // modifier rather than as voice tickables, which is why they cannot ride
      // the EasyScore note string and are built by hand here.  The duration is
      // routed through toNoteStructDuration for the same reason the dynamics
      // path is: GraceNote goes through Note.parseDuration, which rejects a
      // trailing "." and hangs on a degenerate code, so dots must be split out.
      if (Array.isArray(specNote.graceNotes) && specNote.graceNotes.length > 0) {
        const graceNotes = specNote.graceNotes.map((g) => {
          const { duration, dots } = toNoteStructDuration(g.duration);
          return new GraceNote({
            // StaveNote's constructor (which GraceNote extends) parses its
            // keys with the slash `note/octave` grammar, NOT EasyScore's
            // slashless form -- feeding it a toEasyScoreKey result ("B4")
            // yields an unparseable pitch and hangs GraceNoteGroup's format
            // loop.  toStaveNoteKey keeps/repairs the slash form it needs.
            keys: (g.keys ?? []).map(toStaveNoteKey),
            duration, dots,
            // The slash is the acciaccatura ("crushed") vs the plain
            // appoggiatura; VexFlow draws it on a flagged/first grace note.
            slash: Boolean(g.slash),
          });
        });
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
      // Fingering: a bare value means "below", which is the piano convention.
      if (specNote.fingering != null) {
        const fingering = typeof specNote.fingering === 'object'
          ? specNote.fingering
          : { number: specNote.fingering };
        const place = fingering.position ?? 'below';
        if (!['above', 'below', 'left', 'right'].includes(place)) {
          problems.push(`unknown fingering position "${place}"`);
        } else {
          note.addModifier(
            factory.Fingering({ number: String(fingering.number), position: place }), 0,
          );
        }
      }
      // StringNumber REQUIRES an explicit position -- omitting it throws
      // "InvalidPosition: The position undefined is invalid", unlike
      // Fingering which defaults happily.
      if (specNote.stringNumber != null) {
        note.addModifier(
          factory.StringNumber({
            number: String(specNote.stringNumber), position: 'above',
          }), 0,
        );
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
    });

    const voices: any[] = [
      factory.Voice({ time: meter }).setMode(Voice.Mode.SOFT).addTickables(tickables),
    ];

    // Dynamics live in their own voice.  Placing them in the melody voice
    // makes them consume beat time and displace the notes (measured: note 2
    // moved from x=145 to x=236); a parallel voice padded with GhostNotes
    // keeps note spacing byte-identical to a melody-only render while
    // aligning each mark under its own note.
    if (specNotes.some((n) => n.dynamic)) {
      const markFor = (specNote: MusicNoteSpec) => {
        const mark = specNote.dynamic;
        // "h." is legal in the EasyScore string grammar the melody voice uses,
        // but noteStruct-based notes need the dots split out or the Note
        // constructor rejects the object outright.
        const { duration, dots } = toNoteStructDuration(specNote.duration);
        if (mark && DYNAMIC_MARKS.has(mark)) {
          return factory.TextDynamics({ text: mark, duration, dots });
        }
        if (mark) problems.push(`unknown dynamic "${mark}"`);
        return new GhostNote({ duration, dots });
      };
      // The dynamics voice must carry a BarNote at every position the melody
      // voice does, or the two desynchronise after the first barline: a mark
      // in measure 2 landed at x=383 while its note sat at x=445, because the
      // barline consumes a tick slot in one voice and not the other.
      const marks: any[] = [];
      measures.forEach((measure, measureIndex) => {
        if (measureIndex > 0) {
          marks.push(new BarNote(barlineBetween(measures[measureIndex - 1], measure)));
        }
        for (const specNote of measure.notes ?? []) marks.push(markFor(specNote));
      });
      voices.push(
        factory.Voice({ time: meter }).setMode(Voice.Mode.SOFT).addTickables(marks),
      );
    }

    const stave = vexSystems[systemIndex].addStave({ voices });
    // Clef, key and time signature are re-printed on EVERY system, which is
    // what a printed score does at a line break -- a continuation line with no
    // clef would be unreadable.
    stave.addClef(clef);
    if (spec.timeSignature) stave.addTimeSignature(spec.timeSignature);
    const key = staffSpec.keySignature ?? spec.keySignature;
    if (key) stave.addKeySignature(key);
    built.push({
      stave, notes: easyNotes, staffSpec, specNotes, tickables, byMeasure,
      systemIndex, noteOffset,
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
  if (spec.tempo) {
    // VexFlow's StaveTempo only engraves the metronome portion when it is
    // given a beat `duration`: a bpm with no duration draws the note glyph
    // and "= N" as nothing at all, so `{"bpm":120}` silently produced an
    // empty mark.  Default the beat unit to a quarter when a bpm is supplied
    // without one -- "♩ = N" is the overwhelming metronome convention, so the
    // shorthand should render rather than vanish.  A tempo carrying neither a
    // name nor a bpm has nothing to show; warn rather than add an empty mark.
    const hasBpm = spec.tempo.bpm != null;
    const tempoDuration = spec.tempo.duration ?? (hasBpm ? 'q' : undefined);
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
      const tempoShiftY = spec.mark ? TEMPO_SHIFT_Y_WITH_MARK : TEMPO_SHIFT_Y;
      const tempoMark = new StaveTempo(
        {
          name: spec.tempo.name,
          duration: tempoDuration,
          dots: spec.tempo.dots ?? 0,
          bpm: spec.tempo.bpm,
        },
        topStave.x - tempoLeftShift(topStave),
        tempoShiftY,
      );
      topStave.addModifier(tempoMark);
    }
  }
  if (spec.mark) {
    const key = NAVIGATION_MARKS[spec.mark];
    if (key) topStave.setRepetitionType(Repetition.type[key], 0);
    else problems.push(`unknown mark "${spec.mark}"`);
  }
  // Volta (repeat-ending bracket).  NOT drawn via topStave.setVoltaType --
  // that stave modifier spans the whole stave, i.e. the entire system, so it
  // drew the "1." bracket over every bar instead of over the ending it names.
  // Instead resolve which measures the ending covers and defer the draw to the
  // post-format overlay pass, where the notes' resolved x-positions exist.
  let voltaPlan:
    | { volta: MusicVolta; systemIndex: number; fromNote: any; toNote: any }
    | null = null;
  if (spec.volta) {
    // The volta rides the TOP staff, whose measures define the range.  Note
    // counts per measure let a 1-based measure range become flat note indices.
    const topMeasures = measuresOf(staffSpecs[0]);
    const perMeasureCounts = topMeasures.map((m) => (m.notes ?? []).length);
    const flatStartOf = (measure0: number): number =>
      perMeasureCounts.slice(0, measure0).reduce((a, b) => a + b, 0);

    // Default range: the measure closing a repeat-end (a 1st ending's usual
    // home), else the last measure -- so an unanchored volta still lands
    // plausibly rather than over the whole system.
    let from0: number;
    let to0: number;
    if (Array.isArray(spec.volta.measures) && spec.volta.measures.length === 2) {
      from0 = Math.max(0, Math.floor(spec.volta.measures[0]) - 1);
      to0 = Math.min(topMeasures.length - 1, Math.floor(spec.volta.measures[1]) - 1);
      if (to0 < from0) { const t = from0; from0 = to0; to0 = t; }
    } else {
      const repeatEnd = topMeasures.findIndex((m) => m.endBar === 'repeat-end');
      from0 = repeatEnd >= 0 ? repeatEnd : Math.max(0, topMeasures.length - 1);
      to0 = from0;
    }

    const firstFlat = flatStartOf(from0);
    const lastFlat = flatStartOf(to0) + Math.max(0, perMeasureCounts[to0] - 1);
    const top = perStaff[0];
    const fromNote = top?.notes[firstFlat] ?? null;
    const toNote = top?.notes[lastFlat] ?? null;
    if (fromNote && toNote && top.noteSystem[firstFlat] === top.noteSystem[lastFlat]) {
      voltaPlan = {
        volta: spec.volta,
        systemIndex: top.noteSystem[firstFlat],
        fromNote,
        toNote,
      };
    } else {
      problems.push('volta range is empty or crosses a system break and was skipped');
    }
  }
  if (spec.measureNumber != null) topStave.setMeasure(spec.measureNumber);
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

  // Spans are per-staff: their indices address that staff's own note list,
  // which runs across every system the staff occupies.
  for (const [staffIndex, { notes, staffSpec, noteSystem }] of perStaff.entries()) {
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
      factory.VibratoBracket({ from: ends.from, to: ends.to, options: { code } });
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
  if (spec.autoBeam) {
    const groups = spec.beamGroups?.length
      ? spec.beamGroups.map(([n, d]) => new Fraction(n, d))
      : undefined;
    for (const { byMeasure } of built) {
      // Beam per measure, never across a barline -- a beam spanning a bar is
      // wrong engraving, and the flat note list would happily produce one.
      for (const measureNotes of byMeasure) {
        beams.push(...Beam.generateBeams(measureNotes, {
          ...(groups ? { groups } : {}),
          // A rest breaks a beam group in ordinary engraving; beaming over one
          // is a deliberate stylistic choice, not a default.
          beamRests: false,
        }));
      }
    }
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

  factory.draw();

  // Beams from Beam.generateBeams are not factory-owned, so the factory's own
  // draw pass does not render them; they need an explicit context.  Those from
  // factory.Beam are already drawn and must not be drawn twice.
  for (const beam of beams) {
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
    // Instrument / part labels in the left gutter, one per named staff.  Drawn
    // here (post-format) so each staff's resolved x/y are available.
    drawStaffLabels(d3, svg, built, isDarkMode);
    // Volta bracket over its measure range.  Anchored to the TOP staff of the
    // system the ending falls on (built entries are one per staff x system);
    // drawn here because it needs the endpoint notes' resolved x-positions.
    if (voltaPlan) {
      const entry = built.find(
        (b) => b.staffSpec === staffSpecs[0] && b.systemIndex === voltaPlan!.systemIndex,
      );
      if (entry) {
        drawVoltaBracket(
          d3, svg, entry.stave, voltaPlan.volta,
          voltaPlan.fromNote, voltaPlan.toNote, isDarkMode,
        );
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
    }
  }
}
