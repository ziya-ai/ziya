/**
 * G-181c6f — force-directed legibility & topology fixes.
 *
 * Pure-function tests for the targeted repairs in forceDirectedPlugin.ts. Each
 * assertion FAILS against the pre-fix code and passes with the fix:
 *
 *  - D-369 / D-393: at a tiny fit scale the whole zoom group (discs, link
 *    strokes) shrinks with k, so nodes collapse to sub-pixel dots and links to
 *    hairlines. effectiveNodeRadius / effectiveLinkStrokeWidth counter-scale the
 *    drawn size so on-screen size clears a floor, mirroring effectiveLabelFontSize.
 *  - D-396: distinct labels sharing a long common prefix collapsed to the same
 *    'prefix…' under head-only truncation; middle truncation keeps the tail.
 *  - D-397: a source===target edge is detected as a self-loop and drawn as a
 *    non-degenerate arc path instead of a zero-length arrowhead stub.
 */
import {
  effectiveNodeRadius,
  effectiveLinkStrokeWidth,
  FORCE_MIN_NODE_RADIUS_ON_SCREEN_PX,
  FORCE_MIN_LINK_STROKE_ON_SCREEN_PX,
  isSelfLoopLink,
  selfLoopPath,
} from '../forceDirectedPlugin';
import { truncateLabelMiddle } from '../chartTheme';

// ── D-369 / D-393: on-screen node/link floors under fit scale ────────────────
describe('D-369/D-393 sub-pixel nodes and links under fit scale', () => {
  it('enlarges the drawn node radius so on-screen size clears the floor at tiny k', () => {
    const k = 0.01; // large settled extent fitted into the frame
    const baseR = 8;
    const applied = effectiveNodeRadius(baseR, k);
    // Pre-fix: drawn radius stayed 8, on-screen = 8*0.01 = 0.08px (invisible).
    expect(applied * k).toBeGreaterThanOrEqual(FORCE_MIN_NODE_RADIUS_ON_SCREEN_PX - 1e-9);
  });

  it('never shrinks a larger authored radius (no-op at k≈1)', () => {
    expect(effectiveNodeRadius(20, 1)).toBe(20);
    expect(effectiveNodeRadius(20, 2)).toBe(20);
  });

  it('enlarges link stroke-width so on-screen stroke clears the floor at tiny k', () => {
    const k = 0.005;
    const baseW = 1;
    const applied = effectiveLinkStrokeWidth(baseW, k);
    expect(applied * k).toBeGreaterThanOrEqual(FORCE_MIN_LINK_STROKE_ON_SCREEN_PX - 1e-9);
  });

  it('link floor is a no-op for a heavy edge at k≈1', () => {
    expect(effectiveLinkStrokeWidth(4, 1)).toBe(4);
  });

  it('coerces degenerate inputs to safe finite values', () => {
    expect(Number.isFinite(effectiveNodeRadius(NaN, NaN))).toBe(true);
    expect(Number.isFinite(effectiveLinkStrokeWidth(NaN, 0))).toBe(true);
  });
});

// ── D-396: middle truncation preserves the discriminating suffix ─────────────
describe('D-396 head-truncation collapses distinct labels', () => {
  const a = 'distributed-consensus-coordinator-alpha';
  const b = 'distributed-consensus-coordinator-bravo';

  it('head-only truncation collapses the shared prefix (the bug)', () => {
    // truncateLabelMiddle keeps head+tail; the two labels remain distinct.
    const ta = truncateLabelMiddle(a, 24);
    const tb = truncateLabelMiddle(b, 24);
    expect(ta).not.toEqual(tb);
    expect(ta.length).toBeLessThanOrEqual(24);
    expect(tb.length).toBeLessThanOrEqual(24);
    // The distinguishing suffix survives on-screen.
    expect(ta.endsWith('alpha')).toBe(true);
    expect(tb.endsWith('bravo')).toBe(true);
  });
});

// ── D-397: self-loop detection and arc geometry ──────────────────────────────
describe('D-397 self-loop drawn as arrowhead stub', () => {
  it('detects a self-loop by object identity and by resolved id', () => {
    const node = { id: 'n1', x: 0, y: 0 };
    expect(isSelfLoopLink({ source: node, target: node })).toBe(true);
    expect(isSelfLoopLink({ source: 'n1', target: 'n1' })).toBe(true);
    expect(isSelfLoopLink({ source: { id: 'n1' }, target: { id: 'n1' } })).toBe(true);
  });

  it('is false for a normal edge and for missing endpoints', () => {
    expect(isSelfLoopLink({ source: 'a', target: 'b' })).toBe(false);
    expect(isSelfLoopLink({ source: null, target: null })).toBe(false);
    expect(isSelfLoopLink(undefined)).toBe(false);
  });

  it('emits a non-degenerate arc path (not a zero-length stub)', () => {
    const d = selfLoopPath(100, 100, 12);
    expect(d).toMatch(/^M[-\d.]+,[-\d.]+ C/);
    // Start and end points differ (a stub would have coincident endpoints).
    const m = d.match(/^M([-\d.]+),([-\d.]+) C.* ([-\d.]+),([-\d.]+)$/);
    expect(m).not.toBeNull();
    const [x0, y0, x1, y1] = [Number(m![1]), Number(m![2]), Number(m![3]), Number(m![4])];
    expect(Math.hypot(x1 - x0, y1 - y0)).toBeGreaterThan(5);
  });

  it('coerces degenerate radius/centre to a finite path', () => {
    expect(selfLoopPath(NaN, NaN, NaN)).toMatch(/^M/);
  });
});
