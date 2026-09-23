/**
 * G-941688 — force-directed fit clips COUNTER-SCALED content at the canvas edge.
 *
 * Shared file: frontend/src/plugins/d3/forceDirectedPlugin.ts. Defects worked:
 *   D-394 extreme-aspect / oversize canvas — nodes & labels clipped at the edge
 *         (w2-08 "canvas-cropped-nodes-clipped", w2-10 oversize).
 *   D-376 long shared-prefix labels on a d3 force graph clipped at the right
 *         edge (w2-13) — the truncation collapse was already fixed; the residual
 *         is the label-edge clip.
 *
 * Root cause (confirmed against the code, differs from the triage hypothesis):
 * the base fit — computeFitTransform(forceFitPoints(nodes, radiusOf, fontSize)) —
 * frames the graph using the BASE node radii and the AUTHORED font. But the
 * render path then ENLARGES the drawn discs (effectiveNodeRadius) and labels
 * (effectiveLabelFontSize) in user space so their ON-SCREEN size clears the
 * legibility floors, and at a small fit.k that enlargement (~floor/k) pushes the
 * drawn content PAST the framed extent, so it is clipped at the canvas edge.
 * computeCounterScaledFit refines the fit once using the DRAWN radius + applied
 * font so the enlarged content is contained; it is a no-op at k≈1.
 *
 * Each assertion FAILS against the base fit (the pre-fix path) and passes with
 * computeCounterScaledFit.
 */
import {
  computeFitTransform,
  computeCounterScaledFit,
  forceFitPoints,
  effectiveNodeRadius,
  effectiveLabelFontSize,
  labelRightExtent,
  resolveNodeFill,
  FORCE_DARK_BG,
  FORCE_LIGHT_BG,
  type FitTransform,
} from '../forceDirectedPlugin';

// On-screen right edge of the furthest DRAWN label end, given a fit transform.
// Mirrors the render path: labels are drawn at the counter-scaled font/radius
// for the fit's own k, inside the zoom group scaled by fit.k.
function maxDrawnRightEdgeOnScreen(
  nodes: Array<{ x: number; y: number; label?: string; id?: string }>,
  radiusOf: (n: any) => number,
  fontSize: number,
  fit: FitTransform,
): number {
  const appliedFont = effectiveLabelFontSize(fontSize, fit.k);
  let maxEdge = -Infinity;
  for (const n of nodes) {
    const drawR = effectiveNodeRadius(radiusOf(n), fit.k);
    const chars = Math.min(String(n.label ?? n.id ?? '').length, 24);
    const labelEndX = n.x + labelRightExtent(chars, drawR, appliedFont);
    const onScreen = fit.x + fit.k * labelEndX;
    if (onScreen > maxEdge) maxEdge = onScreen;
  }
  return maxEdge;
}

describe('computeCounterScaledFit — D-394/D-376 contain counter-scaled content', () => {
  const WIDTH = 760;
  const HEIGHT = 520;
  const radiusOf = () => 8;
  const fontSize = 10;

  // A settled layout with a large horizontal extent so the base fit resolves to
  // a small k — exactly the regime where the on-screen floors enlarge labels
  // enough to overflow the frame. 24-char labels mirror the arn-style w2-13 set.
  const label = 'arn:aws:ecs:task-definition/x';
  const nodes = [
    { id: 'a', x: 0, y: 260, label },
    { id: 'b', x: 1000, y: 260, label },
    { id: 'c', x: 2000, y: 260, label },
  ];

  it('the BASE fit clips the drawn label past the right edge (the bug)', () => {
    const base = computeFitTransform(forceFitPoints(nodes, radiusOf, fontSize), WIDTH, HEIGHT);
    const edge = maxDrawnRightEdgeOnScreen(nodes, radiusOf, fontSize, base);
    // Drawn (counter-scaled) label runs off the right of the canvas.
    expect(edge).toBeGreaterThan(WIDTH);
  });

  it('computeCounterScaledFit contains the drawn label within the canvas', () => {
    const refined = computeCounterScaledFit(nodes, radiusOf, fontSize, WIDTH, HEIGHT);
    const edge = maxDrawnRightEdgeOnScreen(nodes, radiusOf, fontSize, refined);
    expect(edge).toBeLessThanOrEqual(WIDTH + 0.5);
  });

  it('refinement tightens (never enlarges) the scale relative to the base fit', () => {
    const base = computeFitTransform(forceFitPoints(nodes, radiusOf, fontSize), WIDTH, HEIGHT);
    const refined = computeCounterScaledFit(nodes, radiusOf, fontSize, WIDTH, HEIGHT);
    expect(refined.k).toBeLessThanOrEqual(base.k + 1e-9);
    // and it actually moved (this scenario is genuinely counter-scaled)
    expect(refined.k).toBeLessThan(base.k);
  });

  it('is a strict no-op for a compact graph (k clamped, no enlargement)', () => {
    // A tiny extent → base fit clamps to the 2x ceiling; the floors do not
    // enlarge discs or labels, so the refined fit equals the base fit exactly.
    const compact = [
      { id: 'a', x: 380, y: 260, label: 'a' },
      { id: 'b', x: 400, y: 270, label: 'b' },
    ];
    const base = computeFitTransform(forceFitPoints(compact, radiusOf, fontSize), WIDTH, HEIGHT);
    const refined = computeCounterScaledFit(compact, radiusOf, fontSize, WIDTH, HEIGHT);
    expect(refined.k).toBe(base.k);
    expect(refined.x).toBe(base.x);
    expect(refined.y).toBe(base.y);
  });
});

// ---------------------------------------------------------------------------
// D-399 — a numeric-STRING node.group (the w4-06 recovery spec quotes every
// number, group included) must resolve to a DISTINCT palette fill at the render
// entry point resolveNodeFill, not collapse to DEFAULT_GROUP_COLORS[0]. The
// wave-4 preprocessor coerces size/value/width/etc. but NOT node.group, so a
// string group reaches the plugin verbatim; groupColor coerces it. This guards
// the whole render-path colour resolution (not just groupColor) in BOTH themes,
// against a regression to Number.isFinite('1') === false collapsing every disc.
// ---------------------------------------------------------------------------
describe('resolveNodeFill — D-399 string-group render path stays distinct', () => {
  // The exact w4-06 node set: 6 nodes, string groups "0"/"1"/"2".
  const w406 = [
    { group: '0' as any }, { group: '1' as any }, { group: '2' as any },
    { group: '2' as any }, { group: '1' as any }, { group: '0' as any },
  ];
  for (const bg of [FORCE_DARK_BG, FORCE_LIGHT_BG]) {
    it(`resolves 3 distinct group fills on ${bg}`, () => {
      const fills = w406.map((d) => resolveNodeFill(d, {}, bg));
      // 3 groups → 3 distinct colours (pre-fix: all collapse to palette[0]).
      expect(new Set(fills).size).toBe(3);
      // same-group nodes share a fill; different-group nodes differ.
      expect(fills[0]).toBe(fills[5]); // both group "0"
      expect(fills[2]).toBe(fills[3]); // both group "2"
      expect(fills[0]).not.toBe(fills[1]);
      expect(fills[1]).not.toBe(fills[2]);
    });
  }
});
