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

export interface MusicNoteSpec {
  /** One entry per note in a chord, e.g. ["c/5"] or ["c/5", "e/5", "g/5"] */
  keys: string[];
  /** VexFlow duration code: w, h, q, 8, 16 (+ "." for dotted) */
  duration: string;
  annotations?: MusicAnnotation[];
  /** LilyPond-style harp pedal diagram string, e.g. "^v-|vv-^" */
  harpPedal?: string;
  /** Optional chord symbol text (rendered like an annotation, above the note) */
  chordSymbol?: string;
}

export interface MusicSpec {
  type: 'music';
  clef?: MusicClef;
  keySignature?: string;
  timeSignature?: string;
  notes: MusicNoteSpec[];
  width?: number;
}

export const isMusicSpec = (spec: any): spec is MusicSpec => {
  return (
    typeof spec === 'object' &&
    spec !== null &&
    spec.type === 'music' &&
    Array.isArray(spec.notes) &&
    spec.notes.length > 0
  );
};

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
  const { Factory, Annotation } = Vex as any;

  container.innerHTML = '';
  const width = spec.width ?? Math.max(300, 80 + spec.notes.length * 70);
  const height = 160;

  const factory = new Factory({
    renderer: { elementId: container, width, height, backend: 1 /* SVG */ },
  });

  const score = factory.EasyScore();
  const system = factory.System({ width: width - 20 });

  const clef = spec.clef ?? 'treble';
  const noteStrings = spec.notes.map(n => `${n.keys.join(' ')}/${n.duration}`).join(', ');
  const easyNotes = score.notes(noteStrings, { clef });

  const stave = system.addStave({ voices: [score.voice(easyNotes)] });
  stave.addClef(clef);
  if (spec.timeSignature) stave.addTimeSignature(spec.timeSignature);
  if (spec.keySignature) stave.addKeySignature(spec.keySignature);

  // Attach annotations (chord symbols / text) before draw so VexFlow
  // accounts for them during formatting.
  easyNotes.forEach((note: any, i: number) => {
    const specNote = spec.notes[i];
    if (!specNote) return;
    const texts: MusicAnnotation[] = [
      ...(specNote.chordSymbol ? [{ text: specNote.chordSymbol, position: 'above' as const }] : []),
      ...(specNote.annotations ?? []),
    ];
    for (const a of texts) {
      const ann = new Annotation(a.text);
      ann.setPosition(a.position === 'below' ? Annotation.Position.BOTTOM : Annotation.Position.TOP);
      note.addModifier(ann, 0);
    }
  });

  factory.draw();

  // Harp pedal overlay — anchor to each note's resolved x-position after
  // VexFlow has completed layout/formatting.
  const svgEl = container.querySelector('svg');
  if (svgEl) {
    const svg = d3.select(svgEl);
    const topLineY = typeof stave.getYForLine === 'function' ? stave.getYForLine(0) : 20;
    easyNotes.forEach((note: any, i: number) => {
      const specNote = spec.notes[i];
      if (!specNote?.harpPedal) return;
      const noteX = typeof note.getAbsoluteX === 'function' ? note.getAbsoluteX() : null;
      if (noteX == null) return;
      drawHarpPedalDiagram(d3, svg, specNote.harpPedal, noteX, topLineY - 14, isDarkMode);
    });
  }
}
