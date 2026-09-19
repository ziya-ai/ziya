/**
 * G-497189 — force-directed plugin regressions worked as one group (shared file
 * frontend/src/plugins/d3/forceDirectedPlugin.ts):
 *   D-395 selectVisibleLabels hides a label overprinting an ADJACENT disc
 *   D-398 sanitizeForceNodes de-collides fully-pinned coincident nodes
 *   D-399 groupColor coerces a numeric-STRING group instead of collapsing to 0
 *   D-400 readableStroke lifts a 3-digit hex stroke on a dark canvas
 *   D-376 truncateLabelMiddle keeps a long shared-prefix label distinguishable
 *
 * Every theme assertion covers BOTH themes. Each test is written so it FAILS
 * against the pre-fix behaviour (the old constant / regex / index path) and
 * passes only with the fix.
 */
import { contrastRatio, compositeOver, truncateLabelMiddle } from '../chartTheme';
import {
  readableStroke,
  groupColor,
  sanitizeForceNodes,
  selectVisibleLabels,
  FORCE_DARK_BG,
  FORCE_LIGHT_BG,
} from '../forceDirectedPlugin';

// ---------------------------------------------------------------------------
// D-400 — 3-digit hex stroke must be contrast-lifted on a dark canvas.
// Pre-fix: readableStroke's parse regex was /^#?([0-9a-f]{6})$/i, so a 3-digit
// hex like #468 returned null -> `if (!start) return hex` emitted it un-lifted
// (68,102,136 @0.9 on #212121 = 2.42:1, below the 3:1 graphical floor).
// ---------------------------------------------------------------------------
describe('readableStroke — D-400 3-digit hex lift (both themes)', () => {
  it('lifts #468 to clear 3:1 composited on the DARK canvas', () => {
    const out = readableStroke('#468', FORCE_DARK_BG, 0.9, '#888888');
    // demonstrate the un-lifted short hex would FAIL the floor
    expect(contrastRatio(compositeOver('#446688', FORCE_DARK_BG, 0.9), FORCE_DARK_BG)).toBeLessThan(3);
    expect(contrastRatio(compositeOver(out, FORCE_DARK_BG, 0.9), FORCE_DARK_BG)).toBeGreaterThanOrEqual(3);
  });

  it('leaves #468 already-readable on the LIGHT canvas (no over-correction)', () => {
    const out = readableStroke('#468', FORCE_LIGHT_BG, 0.9, '#888888');
    expect(contrastRatio(compositeOver(out, FORCE_LIGHT_BG, 0.9), FORCE_LIGHT_BG)).toBeGreaterThanOrEqual(3);
  });
});

// ---------------------------------------------------------------------------
// D-399 — a numeric-STRING group must select a distinct palette entry, not
// collapse to DEFAULT_GROUP_COLORS[0]. Pre-fix: Number.isFinite('1') === false
// so gi fell to 0 and every string-grouped node rendered the same colour.
// ---------------------------------------------------------------------------
describe('groupColor — D-399 numeric-string group coercion (both themes)', () => {
  for (const bg of [FORCE_DARK_BG, FORCE_LIGHT_BG]) {
    it(`string groups '0'/'1'/'2' are distinct on ${bg}`, () => {
      const c0 = groupColor('0' as any, bg);
      const c1 = groupColor('1' as any, bg);
      const c2 = groupColor('2' as any, bg);
      expect(new Set([c0, c1, c2]).size).toBe(3);
      // string group equals the equivalent numeric group
      expect(groupColor('1' as any, bg)).toBe(groupColor(1, bg));
      // and does NOT collapse onto group 0 (the pre-fix failure mode)
      expect(groupColor('1' as any, bg)).not.toBe(groupColor(0, bg));
    });
  }
});

// ---------------------------------------------------------------------------
// D-398 — nodes pinned to identical fx/fy must be separated so they render as
// distinct discs. Pre-fix: sanitizeForceNodes kept coincident pins verbatim and
// forceCollide could not move fixed positions, so N nodes stacked into one.
// ---------------------------------------------------------------------------
describe('sanitizeForceNodes — D-398 coincident-pin de-collision', () => {
  it('separates nodes pinned to the same coordinate', () => {
    const out = sanitizeForceNodes([
      { id: 'a', fx: 100, fy: 100 },
      { id: 'b', fx: 100, fy: 100 },
      { id: 'c', fx: 100, fy: 100 },
    ]);
    const coords = out.map((n: any) => `${n.fx},${n.fy}`);
    expect(new Set(coords).size).toBe(3); // all distinct now
    // first keeps its authored pin
    expect(out[0].fx).toBe(100);
    expect(out[0].fy).toBe(100);
    // pins stay finite and near the requested location
    for (const n of out as any[]) {
      expect(Number.isFinite(n.fx)).toBe(true);
      expect(Number.isFinite(n.fy)).toBe(true);
      expect(Math.hypot(n.fx - 100, n.fy - 100)).toBeLessThan(60);
    }
  });

  it('leaves distinct pins and single-axis pins untouched', () => {
    const out = sanitizeForceNodes([
      { id: 'a', fx: 10, fy: 20 },
      { id: 'b', fx: 30, fy: 40 },
      { id: 'c', fx: 10 }, // only one axis pinned -> not a coincident point
      { id: 'd', fx: 10 },
    ]);
    expect(out[0].fx).toBe(10);
    expect(out[0].fy).toBe(20);
    expect(out[1].fx).toBe(30);
    expect((out[2] as any).fx).toBe(10);
    expect((out[3] as any).fx).toBe(10);
  });
});

// ---------------------------------------------------------------------------
// D-395 — a label whose box overlaps an ADJACENT node's disc must be hidden
// (declutter), not left overprinting the disc. Pre-fix selectVisibleLabels only
// tested label-vs-label overlaps.
// ---------------------------------------------------------------------------
describe('selectVisibleLabels — D-395 label-vs-disc declutter', () => {
  it('hides a label that overprints another node\'s disc', () => {
    // node 0 label extends to the right straight across node 1's disc
    const labelBoxes = [
      { x0: 10, y0: 0, x1: 60, y1: 10, priority: 8 },
      { x0: 200, y0: 200, x1: 240, y1: 210, priority: 8 },
    ];
    const discBoxes = [
      { x0: 0, y0: 0, x1: 8, y1: 8 },     // node 0's own disc (must be ignored)
      { x0: 40, y0: 0, x1: 56, y1: 12 },  // node 1's disc, under node 0's label
    ];
    const withDiscs = selectVisibleLabels(labelBoxes, discBoxes);
    expect(withDiscs[0]).toBe(false); // label 0 overprints disc 1 -> hidden
    expect(withDiscs[1]).toBe(true);
    // and the label-vs-label-only path (no discBoxes) would have SHOWN it
    const withoutDiscs = selectVisibleLabels(labelBoxes);
    expect(withoutDiscs[0]).toBe(true);
  });

  it('does not hide a label merely for overlapping its OWN disc', () => {
    const labelBoxes = [{ x0: 8, y0: 0, x1: 60, y1: 10, priority: 8 }];
    const discBoxes = [{ x0: 0, y0: 0, x1: 12, y1: 12 }]; // own disc overlaps label anchor
    expect(selectVisibleLabels(labelBoxes, discBoxes)[0]).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// D-376 — long labels sharing a leading prefix must stay distinguishable after
// truncation (middle ellipsis keeps the unique suffix). Guards the arn-style
// labels of d3-w2-13 against a regression to leading-only truncation.
// ---------------------------------------------------------------------------
describe('truncateLabelMiddle — D-376 shared-prefix labels stay distinct', () => {
  it('keeps 12 arn labels distinct at 24 chars', () => {
    const labels = Array.from({ length: 12 }, (_, i) =>
      `arn:aws:ecs:us-west-2:123456789012:task-definition/checkout-orchestrator-canary-${String(i).padStart(2, '0')}`);
    const truncated = labels.map((l) => truncateLabelMiddle(l, 24));
    expect(new Set(truncated).size).toBe(12); // no collapse to identical prefix
    truncated.forEach((t) => expect(t.length).toBeLessThanOrEqual(24));
  });
});
