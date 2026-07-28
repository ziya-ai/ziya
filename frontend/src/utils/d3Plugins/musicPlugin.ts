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
  /** Beat unit for the bpm: w, h, q, 8, 16.  Required to show a bpm. */
  duration?: string;
  /** Augmentation dots on the beat unit. */
  dots?: number;
  /** Beats per minute. */
  bpm?: number;
}

export interface MusicVolta {
  /** Bracket placement.  Defaults to "begin". */
  type?: 'begin' | 'mid' | 'end' | 'begin-end';
  /** Label inside the bracket, e.g. "1." */
  label?: string;
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
}

/**
 * One staff of a multi-staff system.  Carries everything that is per-staff;
 * meter, tempo and the navigation marks stay on the parent spec because they
 * apply to the system as a whole.
 */
export interface MusicStaff {
  clef?: MusicClef;
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
}

export interface MusicSpec {
  type: 'music';
  clef?: MusicClef;
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

/** Build the EasyScore note list for a spec, parenthesising chords. */
export function buildNoteString(notes: MusicNoteSpec[], clef: string = 'treble'): string {
  const restPitch = REST_PITCH_FOR_CLEF[clef] ?? REST_PITCH_FOR_CLEF.treble;
  return notes
    .map((n) => {
      if (n.rest) {
        // The augmentation dot goes AFTER the /r, not on the duration:
        // "B4/q./r" parses as a NOTE (verified: rests=0, draws a notehead)
        // while "B4/q/r." is a dotted rest.  Since the spec uses one
        // `duration` field for both, split the dots off and re-append them.
        const parsed = /^([a-z0-9]+?)(\.*)$/i.exec(String(n.duration).trim());
        const base = parsed ? parsed[1] : String(n.duration);
        const dots = parsed ? parsed[2] : '';
        return `${restPitch}/${base}/r${dots}`;
      }
      const keys = (n.keys ?? []).map(toEasyScoreKey);
      const pitch = keys.length > 1 ? `(${keys.join(' ')})` : keys[0];
      return `${pitch}/${n.duration}`;
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
  const textFill = isDarkMode ? '#e0e0e0' : '#1F2937';
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
    Barline, Repetition, Volta, ChordSymbol, StaveTempo, BarNote, Beam, Fraction,
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
        measures: spec.measures,
        slurs: spec.slurs, ties: spec.ties,
        glissandos: spec.glissandos, hairpins: spec.hairpins,
        brackets: spec.brackets, trillLines: spec.trillLines,
        beams: spec.beams,
      }];

  // Count across measures, and add room for each barline: a barline is a
  // tickable too, so without the allowance the notes are squeezed to make
  // space for it.
  const longestStaff = Math.max(...staffSpecs.map((s) => notesOf(s).length));
  const mostBarlines = Math.max(
    ...staffSpecs.map((s) => Math.max(0, measuresOf(s).length - 1)),
  );
  const width = spec.width
    ?? Math.max(340, 110 + longestStaff * 78 + mostBarlines * 24);
  // Dynamics sit below the staff and hairpins below those, so a fixed 160
  // clips them.  Grow the canvas only when those features are present, to
  // avoid padding every plain staff with dead space.
  const needsRoomBelow = staffSpecs.some((s) =>
    notesOf(s).some((n) =>
      n.dynamic
      // A below-staff chord symbol (roman-numeral analysis) needs the same
      // room a dynamic does.
      || (typeof n.chordSymbol === 'object' && n.chordSymbol?.position === 'below'))
    || (s.hairpins?.length ?? 0) > 0
    || (s.brackets ?? []).some((b) => b.position === 'below'));
  // Tempo / marks / volta / measure number all render ABOVE the top staff and
  // are clipped without headroom.
  const needsRoomAbove = Boolean(
    spec.tempo || spec.mark || spec.volta ||
    spec.measureNumber != null || spec.section
    // Brackets and trill lines also occupy the band above the staff.
    || staffSpecs.some((s) =>
      (s.brackets ?? []).some((b) => b.position !== 'below')
      || (s.trillLines?.length ?? 0) > 0),
  );
  // Both the tempo lift (TEMPO_SHIFT_Y) and the bracket lift
  // (BRACKET_LINE_WITH_TEMPO) push material further up than the previous flat
  // 40px allowance covered, so a stacked tempo + bracket needs more room or
  // the topmost glyph is clipped at y<0.
  const roomAbove = needsRoomAbove
    ? (spec.tempo && staffSpecs.some((s) => (s.brackets?.length ?? 0) > 0) ? 60 : 46)
    : 0;
  const height = spec.height
    ?? (needsRoomBelow ? 230 : 160) * staffSpecs.length + roomAbove;

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
  const system = factory.System({
    width: width - 20,
    y: needsRoomAbove ? 40 : 10,
    ...(staffSpecs.length > 1 ? { spaceBetweenStaves: 12 } : {}),
  });

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

  /** Built staves, kept so spans and hairpins can be attached per staff. */
  const built: Array<{
    stave: any; notes: any[]; staffSpec: MusicStaff;
    specNotes: MusicNoteSpec[]; tickables: any[];
    /** Rendered notes grouped by measure, for per-measure auto-beaming. */
    byMeasure: any[][];
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
    const measures = measuresOf(staffSpec);
    const specNotes = notesOf(staffSpec);
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
        if (mark && DYNAMIC_MARKS.has(mark)) {
          return factory.TextDynamics({ text: mark, duration: specNote.duration });
        }
        if (mark) problems.push(`unknown dynamic "${mark}"`);
        return new GhostNote({ duration: specNote.duration });
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

    const stave = system.addStave({ voices });
    stave.addClef(clef);
    if (spec.timeSignature) stave.addTimeSignature(spec.timeSignature);
    const key = staffSpec.keySignature ?? spec.keySignature;
    if (key) stave.addKeySignature(key);
    built.push({ stave, notes: easyNotes, staffSpec, specNotes, tickables, byMeasure });
  });

  // Tempo, navigation marks, volta and labels go on the TOP staff only: in
  // engraving they describe the system, and repeating them per staff would be
  // wrong rather than merely redundant.
  const topStave = built[0].stave;
  if (spec.tempo) {
    // Constructed directly rather than via stave.setTempo() because that
    // helper hardcodes the x as `this.x`, giving no way to cancel the
    // clef-width shift that draw() adds -- see tempoLeftShift.
    const tempoMark = new StaveTempo(
      {
        name: spec.tempo.name,
        duration: spec.tempo.duration,
        dots: spec.tempo.dots ?? 0,
        bpm: spec.tempo.bpm,
      },
      topStave.x - tempoLeftShift(topStave),
      TEMPO_SHIFT_Y,
    );
    topStave.addModifier(tempoMark);
  }
  if (spec.mark) {
    const key = NAVIGATION_MARKS[spec.mark];
    if (key) topStave.setRepetitionType(Repetition.type[key], 0);
    else problems.push(`unknown mark "${spec.mark}"`);
  }
  if (spec.volta) {
    const key = VOLTA_TYPES[spec.volta.type ?? 'begin'];
    if (key) topStave.setVoltaType(Volta.type[key], spec.volta.label ?? '1.', 0);
    else problems.push(`unknown volta type "${spec.volta.type}"`);
  }
  if (spec.measureNumber != null) topStave.setMeasure(spec.measureNumber);
  if (spec.section) topStave.setSection(spec.section, 0);

  // Barlines, by contrast, must be set on EVERY staff or a grand staff's
  // repeat signs appear on the top line only and the system looks broken.
  for (const { stave } of built) {
    for (const [value, apply] of [
      [spec.beginBar, (t: number) => stave.setBegBarType(t)],
      [spec.endBar, (t: number) => stave.setEndBarType(t)],
    ] as Array<[string | undefined, (t: number) => void]>) {
      if (!value) continue;
      const key = BARLINE_TYPES[value];
      if (key) apply(Barline.type[key]);
      else problems.push(`unknown barline "${value}"`);
    }
  }

  if (built.length > 1) {
    // The brace is what makes two staves read as one instrument.
    system.addConnector('brace');
    system.addConnector('singleLeft');
    system.addConnector('singleRight');
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

  // Spans are per-staff: their indices address that staff's own note list.
  for (const [staffIndex, { notes, staffSpec }] of built.entries()) {
    for (const slur of staffSpec.slurs ?? []) {
      const from = noteAt(notes, slur.from);
      const to = noteAt(notes, slur.to);
      if (from && to) factory.Curve({ from, to, options: {} });
    }
    for (const tie of staffSpec.ties ?? []) {
      const from = noteAt(notes, tie.from);
      const to = noteAt(notes, tie.to);
      if (from && to) {
        factory.StaveTie({ from, to, firstIndices: [0], lastIndices: [0] });
      }
    }
    for (const gliss of staffSpec.glissandos ?? []) {
      const from = noteAt(notes, gliss.from);
      const to = noteAt(notes, gliss.to);
      // VexFlow ships no glissando primitive (there is a TabSlide, but only
      // for tablature), so a labelled StaveLine between the noteheads
      // stands in.
      if (from && to) {
        factory.StaveLine({
          from, to, first_indices: [0], last_indices: [0],
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
      const from = noteAt(notes, bracket.from);
      const to = noteAt(notes, bracket.to);
      if (!from || !to) continue;
      const rendered = factory.TextBracket({
        from, to, text: String(bracket.text ?? '8'),
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
      const from = noteAt(notes, trillLine.from);
      const to = noteAt(notes, trillLine.to);
      if (!from || !to) continue;
      const name = trillLine.wiggle ?? 'trill';
      const code = WIGGLE_CODES[name];
      if (!code) { problems.push(`unknown wiggle "${name}"`); continue; }
      factory.VibratoBracket({ from, to, options: { code } });
    }
  }

  // Beams must be constructed BEFORE factory.draw(): a beamed note suppresses
  // its own flag during drawing, so a beam created afterwards renders on top of
  // flags that are already there (verified: 8 flags remain, versus 0 when the
  // beam is built first).  This is the opposite ordering from hairpins below,
  // which need the resolved x-positions that only exist after formatting.
  const beams: any[] = [];
  for (const { staffSpec, notes, byMeasure } of built) {
    if (spec.autoBeam) {
      // Beam per measure, never across a barline -- a beam spanning a bar is
      // wrong engraving, and the flat note list would happily produce one.
      const groups = spec.beamGroups?.length
        ? spec.beamGroups.map(([n, d]) => new Fraction(n, d))
        : undefined;
      for (const measureNotes of byMeasure) {
        beams.push(...Beam.generateBeams(measureNotes, {
          ...(groups ? { groups } : {}),
          // A rest breaks a beam group in ordinary engraving; beaming over one
          // is a deliberate stylistic choice, not a default.
          beamRests: false,
        }));
      }
    }
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
  for (const { notes, staffSpec } of built) {
    for (const hairpin of staffSpec.hairpins ?? []) {
      const firstNote = noteAt(notes, hairpin.from);
      const lastNote = noteAt(notes, hairpin.to);
      if (!firstNote || !lastNote) continue;
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

  // Harp pedal overlay — anchor to each note's resolved x-position after
  // VexFlow has completed layout/formatting.
  const svgEl = container.querySelector('svg');
  if (svgEl) {
    const svg = d3.select(svgEl);
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
    }
  }
}
