/**
 * G-16470a — regression guard for the six D2 defects (D-059..D-064) that were
 * verified in the 2026-09-04 sweep and reappeared together in run d2c18548.
 *
 * Shared root cause (NOT six independent regressions): the flat (container-less)
 * ELK layout path sized non-sql_table leaves with
 * `elk.nodeSize.constraints: 'NODE_LABELS'` and no minimum. Under the headless
 * renderer (no font metrics) that resolves the bare {text} label to a 0x0 node,
 * so ELK packs point-sized boxes at the same origin. Every d2 spec then fails at
 * once, which is why all six defects flipped to `regression` on the same fresh
 * evidence run (2f254c3a) even though their individual fixes — arrowhead marker
 * gating (D-062), edge-label parse (D-061), container bounds (D-063/D-064) and
 * per-fill label contrast (D-059) — were all still present in source: the
 * collapse masked them. The fix (buildElkFlatChild, shared with G-09b125) pins
 * every leaf to its measured box via MINIMUM_SIZE, matching the hierarchy path.
 *
 * This test asserts the DEFINING symptom of the group — D-060,
 * "arrowheads-hidden-under-node": trimEdgeToNodes can only push the arrowhead
 * clear of the target rectangle if the target has a real, non-degenerate box.
 * With the flat-child sizing fix the trimmed endpoint sits OUTSIDE the target
 * (>= half its real width from the centre); under the pre-fix 0x0 collapse the
 * endpoint lands within `gap` of the node centre, hidden beneath the rect.
 *
 * Geometry only — theme-invariant, so one axis covers both light and dark.
 */
import { buildElkFlatChild, d2NodeBoxSize, trimEdgeToNodes } from '../d2Plugin';

describe('G-16470a flat-layout node sizing keeps arrowheads visible (D-059..D-064)', () => {
  const source = { id: 'a', label: 'Service A', shape: 'rectangle' };
  const target = { id: 'b', label: 'Service B', shape: 'rectangle' };

  it('buildElkFlatChild gives the target a real, non-degenerate box (not the 0x0 collapse)', () => {
    const child = buildElkFlatChild(target);
    // PRE-FIX: NODE_LABELS with no minimum -> 0x0 under headless render.
    expect(child.width).toBeGreaterThan(0);
    expect(child.height).toBeGreaterThan(0);
    expect(child.width).toBe(d2NodeBoxSize(target).width);
    expect(child.height).toBe(d2NodeBoxSize(target).height);
  });

  it('the trimmed arrowhead endpoint clears the target rectangle when the box is real', () => {
    const gap = 6;
    const sBox = buildElkFlatChild(source);
    const tBox = buildElkFlatChild(target);
    // Two nodes separated horizontally, laid out with their pinned sizes.
    const src = { x: 0, y: 0, width: sBox.width, height: sBox.height };
    const tgt = { x: 400, y: 0, width: tBox.width, height: tBox.height };

    const { x2, y2 } = trimEdgeToNodes(src as any, tgt as any, gap);
    const tCenterX = tgt.x + tgt.width / 2;
    const tCenterY = tgt.y + tgt.height / 2;
    const distFromCenter = Math.hypot(x2 - tCenterX, y2 - tCenterY);

    // Arrowhead must sit outside a rect of the node's real width — i.e. at least
    // half the box width from the centre. This can ONLY hold if the box is real.
    expect(distFromCenter).toBeGreaterThanOrEqual(tgt.width / 2);
  });

  it('documents the pre-fix failure: a 0x0 target hides the arrowhead at the node centre', () => {
    const gap = 6;
    const src = { x: 0, y: 0, width: 120, height: 40 };
    // Simulate the NODE_LABELS collapse: target packed to point size.
    const degenerate = { x: 400, y: 0, width: 0, height: 0 };

    const { x2, y2 } = trimEdgeToNodes(src as any, degenerate as any, gap);
    const tCenterX = degenerate.x;
    const tCenterY = degenerate.y;
    const distFromCenter = Math.hypot(x2 - tCenterX, y2 - tCenterY);

    // With no box, the endpoint stays within ~`gap` of the centre — far inside
    // where the node's real rectangle would be (half-width ~51px for this
    // label). The arrowhead is painted under the (overlapping) node and never
    // seen. This is the collapse the MINIMUM_SIZE fix prevents; contrast it with
    // the real-box clearance asserted above.
    const realHalfWidth = d2NodeBoxSize(target).width / 2;
    expect(distFromCenter).toBeLessThan(realHalfWidth);
    expect(distFromCenter).toBeLessThan(gap * 2);
  });
});
