/**
 * findElementBackground must measure a foreignObject label against a shape
 * that is actually behind it.
 *
 * Strategy 0 climbs up to four groups from the label's foreignObject and took
 * the first filled shape it found.  For a mermaid edge label that walk leaves
 * the label (g.label -> g.edgeLabel -> g.edgeLabels) and reaches g.root, whose
 * first shape is a node anywhere in the diagram: in a dagre state diagram, the
 * solid #333333 start circle.  The label was judged to sit on near-black and
 * recoloured white, on its real light-grey label background (reported
 * 2026-10-28, MAC-move state diagram under dagre).
 *
 * A candidate now has to cover the label's centre.  Under jsdom (no layout,
 * every rect is zero) the old behaviour is kept, which the last test pins.
 */
import { findElementBackground } from '../colorUtils';

type R = { left: number; top: number; width: number; height: number };
let rects: Record<string, R> = {};
const zero: R = { left: 0, top: 0, width: 0, height: 0 };

beforeEach(() => {
  rects = {};
  jest.spyOn(Element.prototype, 'getBoundingClientRect').mockImplementation(function (this: Element) {
    const r = rects[this.id] || zero;
    return { ...r, x: r.left, y: r.top, right: r.left + r.width, bottom: r.top + r.height, toJSON() {} } as any;
  });
  jest.spyOn(console, 'log').mockImplementation(() => {});
});
afterEach(() => jest.restoreAllMocks());

const mount = () => {
  document.body.innerHTML =
    '<svg xmlns="http://www.w3.org/2000/svg"><g class="root">' +
    '<g class="nodes">' +
    '<g class="node"><circle id="start" fill="#333333" r="7"></circle></g>' +
    '<g class="node"><rect id="box" fill="#ECECFF" width="136" height="40"></rect>' +
    '<g class="label"><foreignObject width="120" height="24"><div xmlns="http://www.w3.org/1999/xhtml">' +
    '<span><p id="inner">idle</p></span></div></foreignObject></g></g>' +
    '</g>' +
    '<g class="edgeLabels"><g class="edgeLabel"><g class="label">' +
    '<foreignObject width="100" height="24"><div xmlns="http://www.w3.org/1999/xhtml">' +
    '<span class="edgeLabel"><p id="lbl" style="background-color: rgba(232, 232, 232, 0.8)">MAC-move</p></span>' +
    '</div></foreignObject></g></g></g>' +
    '</g></svg>';
  return {
    lbl: document.getElementById('lbl')!,
    inner: document.getElementById('inner')!,
  };
};

describe('findElementBackground: foreignObject labels', () => {
  it('does not take a distant node as an edge label background', () => {
    const { lbl } = mount();
    rects.start = { left: 0, top: 0, width: 14, height: 14 };
    rects.box = { left: 400, top: 400, width: 136, height: 40 };
    rects.lbl = { left: 200, top: 200, width: 100, height: 24 };
    const bg = findElementBackground(lbl, '#ffffff');
    expect(bg).not.toBe('#333333');
    // The label's own background, which mermaid styles on the <p>.
    expect(bg.replace(/\s/g, '')).toBe('rgba(232,232,232,0.8)');
  });

  it('still uses a shape that is behind the label', () => {
    const { inner } = mount();
    rects.start = { left: 0, top: 0, width: 14, height: 14 };
    rects.box = { left: 400, top: 400, width: 136, height: 40 };
    rects.inner = { left: 410, top: 408, width: 120, height: 24 };
    expect(findElementBackground(inner, '#ffffff')).toBe('#ECECFF');
  });

  it('keeps the old first-shape behaviour when there is no layout (jsdom)', () => {
    const { lbl } = mount();
    expect(findElementBackground(lbl, '#ffffff')).toBe('#333333');
  });
});
