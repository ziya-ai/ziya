/**
 * @jest-environment jsdom
 *
 * Source-level structural guards for fix-group G-c360ed (music engine):
 *   D-431 / D-164  undersized author width must GROW the canvas, not drop notes
 *   D-433          a 12/8 dotted quarter must keep its stem (not be swallowed
 *                  into a degenerate single-member beam)
 *   D-168 / D-429  two independent voices on one staff must both render, each
 *                  pitched note keeping a stem (no voice collapse / detach)
 *
 * These lock the CURRENT source behaviour: every assertion passes against the
 * present musicPlugin.ts and would fail if the layout-recovery, autobeam or
 * multi-voice paths regressed to the triaged failure modes.  They render
 * through renderMusicSpec in jsdom, which computes VexFlow structure (note
 * count, stems, canvas dimensions) identically to the SVG backend, so a
 * structural regression is caught headless without a golden image.
 */
if (typeof (globalThis as any).structuredClone !== 'function') {
  (globalThis as any).structuredClone = (v: any) =>
    (v === undefined ? undefined : JSON.parse(JSON.stringify(v)));
}
import {
  renderMusicSpec,
  resolveAuthorCanvasDimension,
  sanitizeLayoutDimension,
  type MusicSpec,
} from '../musicPlugin';

const NS_ = 'http://www.w3.org/2000/svg';
const domSel = (el: Element): any => {
  const s: any = {
    node: () => el,
    append: (t: string) => { const c = document.createElementNS(NS_, t); el.appendChild(c); return domSel(c); },
    attr: (k: string, v: any) => { el.setAttribute(k, String(v)); return s; },
    style: () => s, classed: () => s, html: () => s,
    text: (t: any) => { el.textContent = String(t); return s; },
  };
  return s;
};
const domD3 = { select: (el: Element) => domSel(el) };
const draw = async (spec: MusicSpec) => {
  const container = document.createElement('div');
  document.body.appendChild(container);
  await renderMusicSpec(container, spec, false, domD3);
  return container.querySelector('svg') as SVGSVGElement;
};

describe('G-c360ed music structural guards', () => {
  test('D-431/D-164: width:120 forced onto 40 notes grows the canvas and keeps every note', async () => {
    const scale = ['c/4', 'd/4', 'e/4', 'f/4', 'g/4', 'a/4', 'b/4', 'c/5'];
    const notes = Array.from({ length: 40 }, (_, i) => ({ keys: [scale[i % 8]], duration: '8' }));
    const spec: any = {
      type: 'music', clef: 'treble', keySignature: 'Ab', timeSignature: '4/4',
      width: 120, autoBeam: true, notes,
    };
    const svg = await draw(spec);
    const svgWidth = parseFloat(svg.getAttribute('width') || '0');
    // A blind clamp would pin the canvas at MIN_CANVAS_WIDTH (120) and drop the
    // notes that no longer fit; the D-140 content-floor grows it far past 120.
    expect(svgWidth).toBeGreaterThan(1000);
    // All 40 notes survive -- the undersized-dimension-destroys-content failure
    // dropped most of them.
    expect(svg.querySelectorAll('.vf-stavenote').length).toBe(40);
  });

  test('D-431 pure: resolveAuthorCanvasDimension never shrinks below content', () => {
    expect(resolveAuthorCanvasDimension(120, 3230)).toBe(3230);
    expect(resolveAuthorCanvasDimension(5000, 3230)).toBe(5000);
    expect(resolveAuthorCanvasDimension(undefined, 500)).toBe(500);
    // A width AT MIN_CANVAS_WIDTH is in-range and returned verbatim by the
    // sanitizer, so the growth must come from resolveAuthorCanvasDimension.
    expect(sanitizeLayoutDimension(120, 120, 16000, 'width')).toBe(120);
  });

  test('D-433: 12/8 dotted quarter keeps its stem', async () => {
    const spec: any = {
      type: 'music', clef: 'treble', keySignature: 'Eb', timeSignature: '12/8', autoBeam: true,
      notes: [
        { keys: ['g/4'], duration: '8' },
        { keys: ['bb/4'], duration: '8' },
        { keys: ['eb/5'], duration: '8' },
        { keys: ['g/5'], duration: 'q.' },
        { keys: ['f/5'], duration: '8' },
        { keys: ['eb/5'], duration: '8' },
      ],
    };
    const svg = await draw(spec);
    const stavenotes = Array.from(svg.querySelectorAll('.vf-stavenote'));
    expect(stavenotes.length).toBe(6);
    // The dotted quarter is the 4th note (index 3).  A degenerate single-member
    // beam would suppress its stem; assert the stem element is present.
    const dottedQuarter = stavenotes[3];
    expect(dottedQuarter.querySelector('.vf-stem')).not.toBeNull();
    // Every note in the bar has a stem somewhere in the SVG (beamed eighths'
    // stems live under the beam group, not the notehead group).
    expect(svg.querySelectorAll('.vf-stem').length).toBe(6);
  });

  test('D-168/D-429: two voices on one staff both render with stems attached', async () => {
    const spec: any = {
      type: 'music', clef: 'treble', keySignature: 'C', timeSignature: '4/4',
      voices: [
        { stemDirection: 'up', notes: [
          { keys: ['e/5'], duration: '8' }, { keys: ['f/5'], duration: '8' },
          { keys: ['g/5'], duration: 'q' }, { rest: true, duration: 'q' },
          { keys: ['a/5'], duration: 'q' },
        ] },
        { stemDirection: 'down', notes: [
          { keys: ['c/4'], duration: 'q' }, { keys: ['e/4'], duration: 'q' },
          { rest: true, duration: 'q' }, { keys: ['g/4'], duration: 'q' },
        ] },
      ],
    };
    const svg = await draw(spec);
    // Both voices contribute: 7 pitched notes across the two voices, each with
    // a stem (a collapsed / detached-stem failure would drop stems or notes).
    // This is the primary structural lock: the detached-stem / voice-collapse
    // failure modes both drop the stem count below 7.
    expect(svg.querySelectorAll('.vf-stem').length).toBe(7);
    // The staff draws content from both voices (>= the pitched-note count).
    // NB: an exact stavenote count is deliberately NOT asserted -- jsdom's
    // missing measureText makes rest-glyph rendering non-deterministic under
    // jsdom (it can drop a rest that the real SVG backend draws), so a strict
    // count here would be a jsdom artefact, not a source guarantee.  The 7-stem
    // lock above already proves both voices' pitched notes render with stems.
    expect(svg.querySelectorAll('.vf-stavenote').length).toBeGreaterThanOrEqual(7);
  });
});
