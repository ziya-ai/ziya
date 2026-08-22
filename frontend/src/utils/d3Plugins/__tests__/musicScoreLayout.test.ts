/**
 * @jest-environment jsdom
 *
 * Score-layout chrome: title block, part/staff labels, system-start measure
 * numbers, and whole-bar centering.
 *
 * VexFlow ships no primitive for any of these, so the plugin hand-draws them
 * as d3 overlays in the post-format pass (see the drawTitleBlock / drawStaffLabels
 * / drawMeasureNumbers comments).  Each `draw*` function is exported, takes a
 * d3 selection to append into, and reads only simple geometry off a stave, so
 * they are tested in isolation with a DOM-capturing selection stub -- no
 * vexflow, no canvas, no full render.  This is both faster and far less fragile
 * than the x-position snapshots in musicWrapParity, which the missing optional
 * `canvas` package breaks.
 */
import {
  drawTitleBlock,
  drawStaffLabels,
  drawMeasureNumbers,
  shouldCenterLoneWholeBar,
  centerLoneWholeBar,
  type MusicSpec,
  type MusicNoteSpec,
} from '../musicPlugin';

const NS = 'http://www.w3.org/2000/svg';
/** A d3-like selection that appends REAL SVG nodes so overlay output is queryable. */
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

const fakeStave = (x = 10) => ({
  getX: () => x,
  getNoteStartX: () => x + 40,
  getNoteEndX: () => x + 340,
  getWidth: () => 402,
  getYForLine: (l: number) => 50 + l * 10,
});
const d3 = { select: (el: Element) => sel(el) };

describe('drawTitleBlock', () => {
  it('draws title, subtitle, composer and lyricist when all present', () => {
    const { el, selection } = freshSvg();
    const spec: MusicSpec = {
      type: 'music', notes: [],
      title: 'Sonata', subtitle: 'in C', composer: 'Composer X', lyricist: 'Poet Y',
    };
    drawTitleBlock(d3, selection, spec, 600, false);
    const t = texts(el);
    expect(t).toContain('Sonata');
    expect(t).toContain('in C');
    expect(t).toContain('Composer X');
    expect(t).toContain('Poet Y');
  });

  it('draws nothing when the spec carries no title-block fields', () => {
    const { el, selection } = freshSvg();
    drawTitleBlock(d3, selection, { type: 'music', notes: [] } as MusicSpec, 600, false);
    expect(texts(el)).toHaveLength(0);
  });

  it('centres the title and right-aligns the composer credit', () => {
    const { el, selection } = freshSvg();
    drawTitleBlock(d3, selection, {
      type: 'music', notes: [], title: 'T', composer: 'C',
    } as MusicSpec, 600, false);
    const title = Array.from(el.querySelectorAll('text')).find((t) => t.textContent === 'T')!;
    const composer = Array.from(el.querySelectorAll('text')).find((t) => t.textContent === 'C')!;
    expect(title.getAttribute('text-anchor')).toBe('middle');
    expect(Number(title.getAttribute('x'))).toBe(300); // width/2
    expect(composer.getAttribute('text-anchor')).toBe('end');
  });
});

describe('drawStaffLabels', () => {
  it('prints the full part name beside the first system', () => {
    const { el, selection } = freshSvg();
    drawStaffLabels(d3, selection, [
      { stave: fakeStave(), staffSpec: { name: 'Violin', clef: 'treble' }, systemIndex: 0 },
    ], false);
    expect(texts(el)).toContain('Violin');
  });

  it('uses the abbreviated shortName on continuation systems', () => {
    const { el, selection } = freshSvg();
    drawStaffLabels(d3, selection, [
      { stave: fakeStave(), staffSpec: { name: 'Flute', shortName: 'Fl.' }, systemIndex: 1 },
    ], false);
    const t = texts(el);
    expect(t).toContain('Fl.');
    expect(t).not.toContain('Flute'); // full name only on system 0
  });

  it('leaves a continuation system unlabelled when no shortName is given', () => {
    const { el, selection } = freshSvg();
    drawStaffLabels(d3, selection, [
      { stave: fakeStave(), staffSpec: { name: 'Flute' }, systemIndex: 2 },
    ], false);
    expect(texts(el)).toHaveLength(0);
  });

  it('skips an unnamed staff entirely', () => {
    const { el, selection } = freshSvg();
    drawStaffLabels(d3, selection, [
      { stave: fakeStave(), staffSpec: { clef: 'bass' }, systemIndex: 0 },
    ], false);
    expect(texts(el)).toHaveLength(0);
  });
});

describe('drawMeasureNumbers', () => {
  it('draws each plan number as text anchored at the note-start x', () => {
    const { el, selection } = freshSvg();
    drawMeasureNumbers(d3, selection, [
      { stave: fakeStave(10), number: 1 },
      { stave: fakeStave(10), number: 4 },
    ], false);
    const t = texts(el);
    expect(t).toEqual(['1', '4']);
    const first = el.querySelector('text')!;
    expect(Number(first.getAttribute('x'))).toBe(50); // getNoteStartX (10+40)
  });
});

describe('shouldCenterLoneWholeBar', () => {
  it('centres a lone whole rest in any meter', () => {
    const rest: MusicNoteSpec = { duration: 'w', rest: true };
    expect(shouldCenterLoneWholeBar(rest, 3, 4)).toBe(true);
    expect(shouldCenterLoneWholeBar(rest, 6, 8)).toBe(true);
  });

  it('centres a lone whole note only in common time', () => {
    const whole: MusicNoteSpec = { keys: ['c/5'], duration: 'w' };
    expect(shouldCenterLoneWholeBar(whole, 4, 4)).toBe(true);
    expect(shouldCenterLoneWholeBar(whole, 3, 4)).toBe(false);
  });

  it('does not centre a shorter note', () => {
    expect(shouldCenterLoneWholeBar({ keys: ['c/5'], duration: 'h' }, 4, 4)).toBe(false);
  });

  it('refuses to centre a decorated whole note (its overlay would be stranded)', () => {
    const decorated: MusicNoteSpec = { keys: ['c/5'], duration: 'w', dynamic: 'mf' };
    expect(shouldCenterLoneWholeBar(decorated, 4, 4)).toBe(false);
  });

  it('is false for an absent note', () => {
    expect(shouldCenterLoneWholeBar(undefined, 4, 4)).toBe(false);
  });
});

describe('centerLoneWholeBar', () => {
  it('translates the note group to the measure midpoint', () => {
    const { el, selection } = freshSvg();
    // a rendered note group vf-n1 at x=60; measure midpoint = (50+390)/2 = 220.
    const g = document.createElementNS(NS, 'g');
    g.setAttribute('id', 'vf-n1');
    el.appendChild(g);
    const note = {
      getAbsoluteX: () => 60,
      getAttribute: (_k: string) => 'n1',
    };
    // measure midpoint = (getNoteStartX 50 + getNoteEndX 350) / 2 = 200.
    centerLoneWholeBar(selection, fakeStave(10), note);
    const tf = g.getAttribute('transform') ?? '';
    expect(tf).toContain('translate(140,0)'); // 200 - 60
  });

  it('no-ops when the note is already centred', () => {
    const { el, selection } = freshSvg();
    const g = document.createElementNS(NS, 'g');
    g.setAttribute('id', 'vf-n1');
    el.appendChild(g);
    const note = { getAbsoluteX: () => 200, getAttribute: () => 'n1' };
    centerLoneWholeBar(selection, fakeStave(10), note);
    expect(g.getAttribute('transform')).toBeNull();
  });
});
