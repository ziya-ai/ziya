/**
 * @jest-environment jsdom
 */
/**
 * D-434 `tuplet-position-inverted` regression (music-w1-06).
 *
 * VexFlow's `Beam.generateBeams` runs a trailing pass over every tuplet that
 * touches a beamed note and unconditionally calls
 * `tuplet.setTupletLocation(stemDown ? -1 : 1)`, DISCARDING the `location` the
 * Tuplet was constructed with.  Because the music plugin builds tuplets before
 * generating the auto-beams, that pass silently overwrote the author's
 * placement:
 *   - a beamed triplet with an explicit `position:"below"` (stems up) printed
 *     its number ABOVE, and
 *   - a beamed triplet with the plugin's documented default ("above", stems
 *     down) printed its number BELOW.
 * i.e. every beamed tuplet number was flipped to the stem/beam side.
 *
 * The fix re-applies each tuplet's requested location AFTER beam construction
 * (musicPlugin.ts, just before factory.draw()).  These tests render real
 * VexFlow to an SVG and assert the tuplet number lands on the requested side
 * of the noteheads.  Without the fix they FAIL (the beam pass inverts both).
 *
 * The check is structural, so it holds in BOTH themes -- number placement is a
 * geometry decision the theme's recolour does not touch -- and the two cases
 * are asserted independently.
 */

// vexflow 5.0.0 uses structuredClone in metrics.getFontInfo; jsdom on Node 20
// does not expose it.  A plain-data font-metrics clone -> JSON round-trip.
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

/** y of the tuplet number (its <text> element's y attribute). */
const tupletNumberY = (svg: SVGSVGElement): number => {
  const t = svg.querySelector('g.vf-tuplet text');
  expect(t).not.toBeNull();
  return parseFloat(t!.getAttribute('y') || 'NaN');
};

/**
 * min / max y across every notehead in the score.  VexFlow 5 renders SMuFL
 * noteheads as <text> glyphs (not <path>), so the notehead y is the text
 * element's `y` attribute.
 */
const noteheadYExtent = (svg: SVGSVGElement): { min: number; max: number } => {
  const ys: number[] = [];
  svg.querySelectorAll('g.vf-notehead text').forEach((t) => {
    const y = t.getAttribute('y');
    if (y != null && y !== '') ys.push(parseFloat(y));
  });
  expect(ys.length).toBeGreaterThan(0);
  return { min: Math.min(...ys), max: Math.max(...ys) };
};

describe('D-434: beamed tuplet honours its requested number placement', () => {
  it('places a default (above) beamed triplet number ABOVE the noteheads', async () => {
    const c = await draw({
      type: 'music', clef: 'treble', timeSignature: '2/4', autoBeam: true,
      notes: [
        { keys: ['c/5'], duration: '8' },
        { keys: ['d/5'], duration: '8' },
        { keys: ['e/5'], duration: '8' },
        { keys: ['f/5'], duration: 'q' },
      ],
      tuplets: [{ from: 0, to: 2 }], // no position -> documented default "above"
    } as any);
    const svg = c.querySelector('svg') as SVGSVGElement;
    const numY = tupletNumberY(svg);
    const heads = noteheadYExtent(svg);
    // Smaller y == higher on the page.  "Above" means the number sits above the
    // topmost notehead.  Before the fix the beam pass forced it BELOW (numY was
    // the largest y in the score), so this comparison is what regressed.
    expect(numY).toBeLessThan(heads.min);
  });

  it('places an explicit position:"below" beamed triplet number BELOW the noteheads', async () => {
    const c = await draw({
      type: 'music', clef: 'treble', timeSignature: '2/4', autoBeam: true,
      notes: [
        { keys: ['g/4'], duration: '8' },
        { keys: ['a/4'], duration: '8' },
        { keys: ['b/4'], duration: '8' },
        { keys: ['c/5'], duration: 'q' },
      ],
      tuplets: [{ from: 0, to: 2, position: 'below' }],
    } as any);
    const svg = c.querySelector('svg') as SVGSVGElement;
    const numY = tupletNumberY(svg);
    const heads = noteheadYExtent(svg);
    // "Below" means the number sits below the bottommost notehead.  Before the
    // fix the beam pass (stems up -> location 1) forced it ABOVE instead.
    expect(numY).toBeGreaterThan(heads.max);
  });
});
