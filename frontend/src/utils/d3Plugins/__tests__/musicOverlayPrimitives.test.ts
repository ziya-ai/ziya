/**
 * @jest-environment jsdom
 *
 * Post-format d3 overlay primitives that VexFlow has no glyph for: breath /
 * caesura marks, the leading trill "tr", cue-note shrinking, harp-pedal
 * diagrams, sustain/sostenuto/una-corda pedal lines, and the multi-measure
 * rest H-bar.  Each is an exported `draw*` that appends into a d3 selection and
 * reads only simple note/stave geometry, so it is tested in isolation with a
 * DOM-capturing selection stub -- no vexflow, no canvas, no full render.
 *
 * Also pins the closed lookup tables (WIGGLE_CODES / BREATH_MARKS /
 * ARPEGGIO_STROKE_TYPES / PEDAL_TYPE_LABELS): these exist so an unknown name is
 * skipped with a warning rather than drawn as garbage, so the contract is that
 * the documented friendly names resolve and nothing else silently sneaks in.
 */
import {
  drawBreathMarks,
  drawTrillGlyph,
  drawCueNotes,
  drawHarpPedalDiagram,
  drawPedalLine,
  drawMultiMeasureRest,
  WIGGLE_CODES,
  BREATH_MARKS,
  ARPEGGIO_STROKE_TYPES,
  PEDAL_TYPE_LABELS,
  type MusicNoteSpec,
} from '../musicPlugin';

const NS = 'http://www.w3.org/2000/svg';
function sel(el: Element): any {
  const s: any = {
    node: () => el,
    append: (tag: string) => {
      const c = document.createElementNS(NS, tag);
      el.appendChild(c);
      return sel(c);
    },
    attr: (k: string, v: any) => { el.setAttribute(k, String(v)); return s; },
    style: () => s,
    classed: () => s,
    html: () => s,
    text: (t: any) => { el.textContent = String(t); return s; },
  };
  return s;
}
function freshSvg() {
  const el = document.createElementNS(NS, 'svg');
  document.body.appendChild(el);
  return { el: el as SVGElement, selection: sel(el) };
}
const texts = (el: Element) =>
  Array.from(el.querySelectorAll('text')).map((t) => t.textContent ?? '');
const count = (el: Element, tag: string) => el.querySelectorAll(tag).length;

const fakeStave = (x = 10) => ({
  getX: () => x,
  getNoteStartX: () => x + 40,
  getNoteEndX: () => x + 340,
  getWidth: () => 402,
  getYForLine: (l: number) => 50 + l * 10,
});
const note = (x: number) => ({ getAbsoluteX: () => x });
const d3 = { select: (el: Element) => sel(el) };

let warn: jest.SpyInstance;
beforeEach(() => { warn = jest.spyOn(console, 'warn').mockImplementation(() => {}); });
afterEach(() => { warn.mockRestore(); });

describe('closed lookup tables', () => {
  it('WIGGLE_CODES maps the documented wiggles to SMuFL codepoints', () => {
    expect(WIGGLE_CODES.trill).toBe(0xeaa4);
    expect(Object.keys(WIGGLE_CODES).sort()).toEqual(
      ['sawtooth', 'trill', 'vibrato', 'vibrato-wide'],
    );
  });
  it('BREATH_MARKS resolves every documented value including caesura-curved', () => {
    expect(BREATH_MARKS.comma).toBe('comma');
    expect(BREATH_MARKS.railroad).toBe('caesura');
    expect(BREATH_MARKS['grand-pause']).toBe('caesura');
    expect(BREATH_MARKS['caesura-curved']).toBe('caesura-curved');
  });
  it('ARPEGGIO_STROKE_TYPES resolves the roll and guitar strokes', () => {
    expect(ARPEGGIO_STROKE_TYPES.arpeggio).toBe('ARPEGGIO_DIRECTIONLESS');
    expect(ARPEGGIO_STROKE_TYPES['arpeggio-up']).toBe('ROLL_UP');
    expect(ARPEGGIO_STROKE_TYPES['rasgueado-down']).toBe('RASGUEADO_DOWN');
  });
  it('PEDAL_TYPE_LABELS holds the two fixed-wording pedals and NOT sustain', () => {
    expect(PEDAL_TYPE_LABELS.sostenuto).toEqual(['Sost. Ped.', '*']);
    expect(PEDAL_TYPE_LABELS['una-corda']).toEqual(['una corda', 'tre corde']);
    expect(PEDAL_TYPE_LABELS.sustain).toBeUndefined();
  });
});

describe('drawBreathMarks', () => {
  const specWith = (breath: any): MusicNoteSpec[] => [
    { keys: ['c/5'], duration: 'q', breath }, { keys: ['d/5'], duration: 'q' },
  ];
  const rendered = [note(60), note(120)];

  it('draws a raised comma for breath:true', () => {
    const { el, selection } = freshSvg();
    drawBreathMarks(d3, selection, fakeStave(), rendered, specWith(true), false);
    expect(texts(el)).toContain('\u2019');
  });
  it('draws a tick as a single line segment', () => {
    const { el, selection } = freshSvg();
    drawBreathMarks(d3, selection, fakeStave(), rendered, specWith('tick'), false);
    expect(count(el, 'line')).toBe(1);
    expect(texts(el)).toHaveLength(0);
  });
  it('draws a straight caesura as two line strokes', () => {
    const { el, selection } = freshSvg();
    drawBreathMarks(d3, selection, fakeStave(), rendered, specWith('caesura'), false);
    expect(count(el, 'line')).toBe(2);
    expect(count(el, 'path')).toBe(0);
  });
  it('draws a curved caesura as two path strokes', () => {
    const { el, selection } = freshSvg();
    drawBreathMarks(d3, selection, fakeStave(), rendered, specWith('caesura-curved'), false);
    expect(count(el, 'path')).toBe(2);
    expect(count(el, 'line')).toBe(0);
  });
  it('skips an unknown breath value with a warning', () => {
    const { el, selection } = freshSvg();
    drawBreathMarks(d3, selection, fakeStave(), rendered, specWith('sneeze'), false);
    expect(el.childNodes).toHaveLength(0);
    expect(warn).toHaveBeenCalled();
  });
});

describe('drawTrillGlyph', () => {
  it('draws the leading "tr" above the note', () => {
    const { el, selection } = freshSvg();
    drawTrillGlyph(d3, selection, fakeStave(), note(60), false);
    expect(texts(el)).toContain('tr');
  });
});

describe('drawCueNotes', () => {
  const build = () => {
    const { el, selection } = freshSvg();
    const g = document.createElementNS(NS, 'g');
    g.setAttribute('id', 'vf-n1');
    el.appendChild(g);
    return { el, selection, g };
  };
  const cueNote = () => ({
    getAttribute: (_k: string) => 'n1',
    getAbsoluteX: () => 60,
    getYs: () => [65],
  });

  it('shrinks a cue note around its notehead (default 2/3)', () => {
    const { selection, g } = build();
    drawCueNotes(selection, [cueNote()], [{ keys: ['c/5'], duration: 'q', cue: true }]);
    const tf = g.getAttribute('transform') ?? '';
    expect(tf).toContain('scale(0.66)');
    expect(tf).toContain('translate(60,65)');
  });
  it('honours an explicit cue scale', () => {
    const { selection, g } = build();
    drawCueNotes(selection, [cueNote()], [{ keys: ['c/5'], duration: 'q', cue: 0.5 }]);
    expect(g.getAttribute('transform')).toContain('scale(0.5)');
  });
  it('no-ops on a full-size (>=1) or absent cue', () => {
    const { selection, g } = build();
    drawCueNotes(selection, [cueNote()], [{ keys: ['c/5'], duration: 'q', cue: 1 }]);
    expect(g.getAttribute('transform')).toBeNull();
    drawCueNotes(selection, [cueNote()], [{ keys: ['c/5'], duration: 'q' }]);
    expect(g.getAttribute('transform')).toBeNull();
  });
});

describe('drawHarpPedalDiagram', () => {
  it('maps ^ / v / - to flat / sharp / natural and | to a divider line', () => {
    const { el, selection } = freshSvg();
    drawHarpPedalDiagram(d3, selection, '^v-|^', 20, 30, false);
    const t = texts(el).join('');
    expect(t).toContain('\u266D'); // flat  (^)
    expect(t).toContain('\u266F'); // sharp (v)
    expect(t).toContain('\u266E'); // natural (-)
    expect(count(el, 'line')).toBe(1); // one divider
  });
});

describe('drawPedalLine', () => {
  const stave = fakeStave();
  it('draws a sustain bracket (rail + two legs) by default, no text', () => {
    const { el, selection } = freshSvg();
    drawPedalLine(d3, selection, stave, {} as any, note(60), note(200), false);
    expect(count(el, 'line')).toBe(3);
    expect(texts(el)).toHaveLength(0);
  });
  it('draws the older "Ped." ... "*" text style', () => {
    const { el, selection } = freshSvg();
    drawPedalLine(d3, selection, stave, { style: 'text' } as any, note(60), note(200), false);
    const t = texts(el);
    expect(t).toContain('Ped.');
    expect(t).toContain('*');
  });
  it('prints the fixed sostenuto wording and ignores style', () => {
    const { el, selection } = freshSvg();
    drawPedalLine(d3, selection, stave, { pedal: 'sostenuto', style: 'bracket' } as any,
      note(60), note(200), false);
    const t = texts(el);
    expect(t).toContain('Sost. Ped.');
    expect(t).toContain('*');
  });
  it('prints the una-corda / tre corde wording', () => {
    const { el, selection } = freshSvg();
    drawPedalLine(d3, selection, stave, { pedal: 'una-corda' } as any,
      note(60), note(200), false);
    const t = texts(el);
    expect(t).toContain('una corda');
    expect(t).toContain('tre corde');
  });
  it('skips an unknown pedal name with a warning', () => {
    const { el, selection } = freshSvg();
    drawPedalLine(d3, selection, stave, { pedal: 'moon' } as any,
      note(60), note(200), false);
    expect(el.childNodes).toHaveLength(0);
    expect(warn).toHaveBeenCalled();
  });
  it('draws "Ped." plus a bracket for the mixed style', () => {
    const { el, selection } = freshSvg();
    drawPedalLine(d3, selection, stave, { style: 'mixed' } as any, note(60), note(200), false);
    expect(texts(el)).toContain('Ped.');
    expect(count(el, 'line')).toBe(3); // rail + two legs
  });
});

describe('drawMultiMeasureRest', () => {
  it('draws the H-bar (rect + two caps) and the bar count', () => {
    const { el, selection } = freshSvg();
    drawMultiMeasureRest(d3, selection, fakeStave(), 200, 8, false);
    expect(count(el, 'rect')).toBe(1);
    expect(count(el, 'line')).toBe(2);   // end caps
    expect(texts(el)).toContain('8');
  });
});
