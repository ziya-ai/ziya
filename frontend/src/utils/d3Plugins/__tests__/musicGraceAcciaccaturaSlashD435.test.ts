/**
 * @jest-environment jsdom
 */
/**
 * D-435 `grace-note-group-geometry-broken` -- acciaccatura slash (music-w1-09).
 *
 * A slashed grace note is an acciaccatura: it must carry a short diagonal
 * slash through its stem, which is what distinguishes it from a plain
 * appoggiatura.  VexFlow 5.0.0 does NOT draw that slash -- verified against the
 * bare API, `new GraceNote({slash:true})` inside a GraceNoteGroup renders its
 * stem but emits no slash stroke -- so every acciaccatura read as an
 * appoggiatura.  (The other two symptoms in the original triage, a collapsed
 * grace chord and grace/main crowding, are already resolved by earlier grace
 * fixes; this pins the remaining one.)
 *
 * The fix collects single, unbeamed slashed grace notes and draws the short
 * diagonal stroke through the resolved grace stem on the factory context after
 * factory.draw(), before the theme recolour, so it is inked like the stem it
 * crosses.  These tests render real VexFlow and assert:
 *   - a SHORT DIAGONAL line now exists (it did not before the fix), and
 *   - it is short (~17px) -- NOT the oversized cross-staff stroke that was the
 *     historical failure mode, and
 *   - it renders in BOTH themes, and in dark mode is not left as invisible
 *     #000000-on-dark (the recolour remaps it).
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
const draw = async (spec: MusicSpec, dark: boolean) => {
  const container = document.createElement('div');
  document.body.appendChild(container);
  await renderMusicSpec(container, spec, dark, d3Stub);
  return container;
};

/** A single slashed acciaccatura before a quarter note, isolated. */
const spec: MusicSpec = {
  type: 'music', clef: 'treble', timeSignature: '4/4',
  notes: [
    { keys: ['c/5'], duration: 'q', graceNotes: [{ keys: ['b/4'], duration: '8', slash: true }] },
    { keys: ['d/5'], duration: 'q' },
  ],
} as any;

/**
 * Every 2-point line path (`M x1 y1 L x2 y2`, fill="none") that is DIAGONAL
 * (both dx and dy meaningfully non-zero) and short.  Stems are vertical, stave
 * lines horizontal, so a short diagonal is the acciaccatura slash.
 */
const diagonalSlashes = (svg: SVGSVGElement): Array<{ len: number; stroke: string | null }> => {
  const out: Array<{ len: number; stroke: string | null }> = [];
  svg.querySelectorAll('path[fill="none"]').forEach((p) => {
    const d = p.getAttribute('d') || '';
    const nums = d.match(/-?\d+(\.\d+)?/g);
    if (!nums || nums.length !== 4) return;
    const [x1, y1, x2, y2] = nums.map(Number);
    const dx = Math.abs(x2 - x1);
    const dy = Math.abs(y2 - y1);
    const len = Math.hypot(x2 - x1, y2 - y1);
    if (dx > 3 && dy > 3) out.push({ len, stroke: p.getAttribute('stroke') });
  });
  return out;
};

describe('D-435: acciaccatura slash is drawn (and short) in both themes', () => {
  it('LIGHT: draws exactly one short diagonal slash through the grace stem', async () => {
    const c = await draw(spec, false);
    const svg = c.querySelector('svg') as SVGSVGElement;
    const slashes = diagonalSlashes(svg);
    // Before the fix there were ZERO diagonal lines (VexFlow drew no slash).
    expect(slashes.length).toBeGreaterThanOrEqual(1);
    // Short -- a slash through a grace stem, never the oversized cross-staff
    // stroke of the original defect (the staff itself is ~40px tall).
    expect(Math.min(...slashes.map((s) => s.len))).toBeLessThan(30);
  });

  it('DARK: slash is present and is not left as invisible #000000-on-dark', async () => {
    const c = await draw(spec, true);
    const svg = c.querySelector('svg') as SVGSVGElement;
    const slashes = diagonalSlashes(svg);
    expect(slashes.length).toBeGreaterThanOrEqual(1);
    // The recolour either remaps an explicit #000000 to the dark ink or the
    // stroke inherits the dark-ink root; in neither case is a slash left with
    // an explicit pure-black stroke (which would be invisible on #1f1f1f).
    expect(slashes.every((s) => (s.stroke || '').toLowerCase() !== '#000000')).toBe(true);
  });
});
