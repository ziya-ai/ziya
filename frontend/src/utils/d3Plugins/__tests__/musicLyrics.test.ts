/**
 * @jest-environment jsdom
 *
 * Vocal underlay (lyrics).  VexFlow has no lyric primitive that also draws
 * word hyphens and melisma extenders, so `drawLyricLayer` hand-draws the
 * syllables as a d3 overlay on one shared baseline, adding a hyphen for a
 * split word (begin/middle syllabic) and an extender line for a melisma.
 *
 * Tested against the exported overlay directly with a DOM-capturing selection
 * stub -- no vexflow, no canvas -- so the syllable text, hyphens and extenders
 * are observable (the full-render suites' no-op d3 stub swallows all overlay
 * output, which is why lyric CONTENT cannot be asserted there).
 */
import { drawLyricLayer, type MusicNoteSpec } from '../musicPlugin';

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
const lines = (el: Element) => el.querySelectorAll('line').length;

const fakeStave = () => ({ getYForLine: (l: number) => 50 + l * 10 });
const note = (x: number) => ({ getAbsoluteX: () => x });
const d3 = { select: (el: Element) => sel(el) };

describe('drawLyricLayer', () => {
  it('draws one syllable per note from a bare-string lyric', () => {
    const { el, selection } = freshSvg();
    const specNotes: MusicNoteSpec[] = [
      { keys: ['c/5'], duration: 'q', lyric: 'A' },
      { keys: ['d/5'], duration: 'q', lyric: 'men' },
    ];
    drawLyricLayer(d3, selection, fakeStave(), [note(60), note(120)], specNotes, false);
    expect(texts(el)).toEqual(expect.arrayContaining(['A', 'men']));
  });

  it('draws a hyphen between a begin/middle syllable and the next of the same word', () => {
    const { el, selection } = freshSvg();
    const specNotes: MusicNoteSpec[] = [
      { keys: ['c/5'], duration: 'q', lyric: { text: 'lo', syllabic: 'begin' } },
      { keys: ['d/5'], duration: 'q', lyric: { text: 'ver', syllabic: 'end' } },
    ];
    drawLyricLayer(d3, selection, fakeStave(), [note(60), note(120)], specNotes, false);
    const t = texts(el);
    expect(t).toContain('lo');
    expect(t).toContain('ver');
    expect(t).toContain('-'); // hyphen in the gap
  });

  it('does not draw a hyphen for a single (whole-word) syllable', () => {
    const { el, selection } = freshSvg();
    const specNotes: MusicNoteSpec[] = [
      { keys: ['c/5'], duration: 'q', lyric: { text: 'love', syllabic: 'single' } },
      { keys: ['d/5'], duration: 'q', lyric: { text: 'you', syllabic: 'single' } },
    ];
    drawLyricLayer(d3, selection, fakeStave(), [note(60), note(120)], specNotes, false);
    expect(texts(el)).not.toContain('-');
  });

  it('draws a melisma extender line to the following note', () => {
    const { el, selection } = freshSvg();
    const specNotes: MusicNoteSpec[] = [
      { keys: ['c/5'], duration: 'q', lyric: { text: 'ah', extend: true } },
      { keys: ['d/5'], duration: 'q' },
    ];
    drawLyricLayer(d3, selection, fakeStave(), [note(60), note(180)], specNotes, false);
    expect(lines(el)).toBeGreaterThan(0);
  });

  it('stacks a second verse on a lower baseline than the first', () => {
    const { el, selection } = freshSvg();
    const specNotes: MusicNoteSpec[] = [
      { keys: ['c/5'], duration: 'q', lyric: { text: 'one', verse: 1 } },
      { keys: ['d/5'], duration: 'q', lyric: { text: 'two', verse: 2 } },
    ];
    drawLyricLayer(d3, selection, fakeStave(), [note(60), note(120)], specNotes, false);
    const els = Array.from(el.querySelectorAll('text'));
    const v1 = els.find((t) => t.textContent === 'one')!;
    const v2 = els.find((t) => t.textContent === 'two')!;
    expect(Number(v2.getAttribute('y'))).toBeGreaterThan(Number(v1.getAttribute('y')));
  });

  it('skips notes without a lyric', () => {
    const { el, selection } = freshSvg();
    const specNotes: MusicNoteSpec[] = [
      { keys: ['c/5'], duration: 'q' },
      { keys: ['d/5'], duration: 'q', lyric: 'sing' },
    ];
    drawLyricLayer(d3, selection, fakeStave(), [note(60), note(120)], specNotes, false);
    expect(texts(el)).toEqual(['sing']);
  });
});
