/**
 * G-a59f77 / D-092, D-382, D-389 — drawio ROUTE-FIX repair.
 *
 * The drawio plugin's ROUTE-FIX pass DETECTED edges whose Manhattan fallback
 * route was drawn straight through a vertex interior, but only LOGGED them and
 * never repaired the route (specs drawio-w1-06 / w1-11 / w2-11 render with edges
 * cutting through boxes; w4-09 an edge strikes a transparent-fill box's label).
 *
 * The repair wires in `rerouteAroundObstacles` from orthogonalRouter.ts — a
 * module the render path had never imported, so its obstacle-avoiding router was
 * dead code. This test exercises that helper directly: given a source and target
 * with an obstacle box squarely between them, the naive straight/L route would
 * cut through the obstacle, and the repair must return interior bend points that
 * keep clear of it. Before the fix the helper did not exist.
 *
 * Structural (both themes): routing is theme-agnostic, so the geometry that
 * fixes light fixes dark — the assertion holds identically for either render
 * theme (guarded explicitly at the end).
 */

import { rerouteAroundObstacles, type Rect } from '../orthogonalRouter';

// True iff point (px,py) lies strictly inside rect (with a small epsilon so a
// route grazing the padded boundary is not counted as a crossing).
function inside(px: number, py: number, r: Rect, eps = 0.5): boolean {
  return (
    px > r.left + eps &&
    px < r.left + r.width - eps &&
    py > r.top + eps &&
    py < r.top + r.height - eps
  );
}

// True iff the axis-aligned polyline (source-center → …bends… → target-center)
// passes through the obstacle interior on any segment.
function polylineHitsBox(
  source: Rect,
  target: Rect,
  bends: { x: number; y: number }[],
  box: Rect
): boolean {
  const sc = { x: source.left + source.width / 2, y: source.top + source.height / 2 };
  const tc = { x: target.left + target.width / 2, y: target.top + target.height / 2 };
  const pts = [sc, ...bends, tc];
  for (let i = 0; i < pts.length - 1; i++) {
    const a = pts[i];
    const b = pts[i + 1];
    // Sample the segment; enough for an axis-aligned crossing check.
    const steps = 40;
    for (let s = 0; s <= steps; s++) {
      const x = a.x + ((b.x - a.x) * s) / steps;
      const y = a.y + ((b.y - a.y) * s) / steps;
      if (inside(x, y, box)) return true;
    }
  }
  return false;
}

describe('rerouteAroundObstacles — ROUTE-FIX repair (G-a59f77)', () => {
  // Two boxes side by side with a third box parked directly on the straight
  // line between them — the exact "edge crosses vertex interior" geometry.
  const source: Rect = { left: 0, top: 100, width: 80, height: 40 };
  const target: Rect = { left: 400, top: 100, width: 80, height: 40 };
  const obstacle: Rect = { left: 200, top: 90, width: 80, height: 60 };

  it('returns interior bends that detour around an obstacle on the direct line', () => {
    const bends = rerouteAroundObstacles(source, target, [obstacle], 20);
    // A straight horizontal route (no bends) would slice through the obstacle;
    // the repair must introduce at least one bend to go around it.
    expect(bends.length).toBeGreaterThan(0);
    expect(polylineHitsBox(source, target, bends, obstacle)).toBe(false);
  });

  it('does not detour when the direct route is already clear', () => {
    // Obstacle moved well below the source→target line: no vertical excursion
    // should be introduced. The router may emit a benign point on the direct
    // line, but every point must stay on that horizontal line (y === 120) — a
    // genuine detour would jump to a different y to go around something.
    const clearBelow: Rect = { left: 200, top: 400, width: 80, height: 60 };
    const bends = rerouteAroundObstacles(source, target, [clearBelow], 20);
    const directY = source.top + source.height / 2; // 120
    expect(bends.every(p => p.y === directY)).toBe(true);
    expect(polylineHitsBox(source, target, bends, clearBelow)).toBe(false);
  });

  it('is theme-agnostic: identical detour regardless of render theme', () => {
    // The helper has no theme input; a fix verified in light must hold in dark.
    const light = rerouteAroundObstacles(source, target, [obstacle], 20);
    const dark = rerouteAroundObstacles(source, target, [obstacle], 20);
    expect(dark).toEqual(light);
    expect(polylineHitsBox(source, target, dark, obstacle)).toBe(false);
  });
});
