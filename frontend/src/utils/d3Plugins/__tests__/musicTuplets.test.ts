/**
 * @jest-environment jsdom
 *
 * Tuplets (triplets, quintuplets, ...).  A tuplet both draws the "3" bracket
 * AND rescales the spanned notes' tick values so the group occupies the right
 * beat time -- without the rescale the three eighths of a triplet fill 3/8 of
 * a bar instead of a quarter and the formatter's spacing is wrong.
 *
 * A tuplet needs the real VexFlow Tuplet primitive, so these are render smoke
 * tests: the spec must render to an SVG (with the expected noteheads) without
 * hanging.  They assert the positive outcome the tuplet path produces and
 * would fail -- by timing out or by a thrown error -- if the tick rescale or
 * span-resolution regressed.
 */

// Polyfill structuredClone for jest's jsdom environment: vexflow 5.0.0 uses
// it in metrics.getFontInfo, and jest's jsdom global does not expose it on
// Node 20 (a plain-data font-metrics clone, so JSON round-trip suffices).
if (typeof (globalThis as any).structuredClone !== 'function') {
  (globalThis as any).structuredClone = (v: any) =>
    (v === undefined ? undefined : JSON.parse(JSON.stringify(v)));
}

import { renderMusicSpec, type MusicSpec } from '../musicPlugin';

const makeChain = () => {
  const chain: any = {};
  for (const m of ['attr', 'style', 'text', 'append', 'classed', 'html']) chain[m] = () => chain;
  return chain;
};
const d3Stub = { select: () => makeChain() };
const draw = async (spec: MusicSpec) => {
  const container = document.createElement('div');
  document.body.appendChild(container);
  await renderMusicSpec(container, spec, false, d3Stub);
  return container;
};
const noteHeads = (c: HTMLElement) => c.querySelectorAll('g.vf-stavenote').length;

describe('tuplets', () => {
  it('renders an eighth-note triplet to an SVG with three noteheads', async () => {
    const c = await draw({
      type: 'music',
      timeSignature: '2/4',
      notes: [
        { keys: ['c/5'], duration: '8' },
        { keys: ['d/5'], duration: '8' },
        { keys: ['e/5'], duration: '8' },
        { keys: ['f/5'], duration: 'q' },
      ],
      tuplets: [{ from: 0, to: 2 }], // triplet over the three eighths
    });
    expect(c.querySelector('svg')).not.toBeNull();
    expect(noteHeads(c)).toBe(4);
  });

  it('renders a quintuplet (num 5 in the space of 4) without hanging', async () => {
    const c = await draw({
      type: 'music',
      notes: [
        { keys: ['c/5'], duration: '16' },
        { keys: ['d/5'], duration: '16' },
        { keys: ['e/5'], duration: '16' },
        { keys: ['f/5'], duration: '16' },
        { keys: ['g/5'], duration: '16' },
      ],
      tuplets: [{ from: 0, to: 4, num: 5, inSpaceOf: 4, ratioed: true }],
    });
    expect(c.querySelector('svg')).not.toBeNull();
    expect(noteHeads(c)).toBe(5);
  });

  it('renders a tuplet placed below the staff', async () => {
    const c = await draw({
      type: 'music',
      notes: [
        { keys: ['c/4'], duration: '8' },
        { keys: ['d/4'], duration: '8' },
        { keys: ['e/4'], duration: '8' },
      ],
      tuplets: [{ from: 0, to: 2, position: 'below', bracketed: true }],
    });
    expect(c.querySelector('svg')).not.toBeNull();
  });
});
