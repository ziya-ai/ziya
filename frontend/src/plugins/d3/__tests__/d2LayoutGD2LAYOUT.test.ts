/**
 * G-D2-LAYOUT — consolidated regression guard for the six d2 layout/sizing
 * defects (shared file: frontend/src/plugins/d3/d2Plugin.ts).
 *
 * Every defect in this group was confirmed ALREADY REMEDIATED in the current
 * source during stage-2 triage; this suite pins the fixed behaviour of the
 * exported pure functions so a future regression is caught. All six are
 * kind:structural and THEME-INVARIANT — the geometry is byte-identical in
 * light and dark (no colour is resolved from the theme in any of these paths),
 * so each assertion is paired with the PRE-FIX value it would take against
 * unpatched code, i.e. each test fails if the fix is reverted.
 *
 *   D-046  Directed edges are trimmed to the node border so the arrowhead
 *          sits OUTSIDE the target box (old: centre-to-centre, head hidden
 *          under the target rect -> graph looked undirected).
 *   D-049  A container enclosing no laid-out member yields null bounds, never
 *          an Infinity rect (old: Math.min/max over empty children[] ->
 *          Infinity -> "attribute x: Expected length, Infinity" + dropped box).
 *   D-050  Container nesting is made visible: an outer container that wraps
 *          nested containers reports a positive descendant depth, driving the
 *          per-level inset so an outer rect is strictly larger than the inner
 *          (old: identical min/max bounds -> N indistinguishable plaid rects).
 *   D-055  d2CanvasSize paints at NATURAL pixel size (width == content extent,
 *          not a small fixed value), so the fixed-12px label text is not
 *          downscaled with the viewBox on large graphs.
 *   D-056  wrapLabel wraps multi-word labels and hard-breaks a single
 *          unbreakable token, and d2GridPitch >= widest node, so labels cannot
 *          overflow the box and boxes cannot overlap/truncate their neighbour.
 *   D-057  d2ResolveSvgSize honours an explicit requested width/height while
 *          keeping the content viewBox (preserveAspectRatio meet), so an
 *          over/under-sized request scales rather than dropping rows.
 */
import {
  trimEdgeToNodes,
  d2ContainerBounds,
  d2ContainerDescendantDepth,
  d2CanvasSize,
  wrapLabel,
  d2GridPitch,
  d2NodeWidth,
  d2ResolveSvgSize,
  D2_FONT_SIZE,
} from '../d2Plugin';

describe('G-D2-LAYOUT: d2 layout & sizing regression guards', () => {
  test('D-046: edge is trimmed so the arrowhead clears the target box', () => {
    const source = { id: 's', x: 0, y: 0, width: 80, height: 40 };
    const target = { id: 't', x: 400, y: 0, width: 80, height: 40 };
    const gap = 6;
    const geom = trimEdgeToNodes(source, target, gap);

    const targetCx = target.x + target.width / 2; // 440
    const targetLeftEdge = target.x;              // 400
    // FIX: the segment stops at the target's LEFT border pushed out by `gap`,
    // i.e. x2 == targetCx - (width/2 + gap) == 400 - 6 == 394, which is OUTSIDE
    // the box (<= left edge). PRE-FIX x2 == targetCx (440), buried in the rect.
    expect(geom.x2).toBeCloseTo(targetCx - (target.width / 2 + gap), 5);
    expect(geom.x2).toBeLessThanOrEqual(targetLeftEdge);
    expect(geom.x2).toBeLessThan(targetCx); // discriminates against centre-to-centre
  });

  test('D-049: an empty container yields null bounds, never an Infinity rect', () => {
    const containers = [{ id: 'empty', parent: null }];
    const laidOut: any[] = []; // no member node was laid out
    const bounds = d2ContainerBounds(containers[0] as any, laidOut, containers as any);
    // FIX: null (the render path filters it out). PRE-FIX: a bounds object whose
    // x/width are +/-Infinity, emitting an invalid SVG length attribute.
    expect(bounds).toBeNull();

    // A container WITH a laid-out member still returns finite, enclosing bounds.
    // Membership is resolved via node.container (walking the container.parent
    // chain), not node.parent.
    const withMember = [{ id: 'grp', parent: null }];
    const node = { id: 'n', container: 'grp', x: 100, y: 100, width: 80, height: 40 };
    const b2 = d2ContainerBounds(withMember[0] as any, [node] as any, withMember as any);
    expect(b2).not.toBeNull();
    expect(Number.isFinite(b2!.x)).toBe(true);
    expect(Number.isFinite(b2!.width)).toBe(true);
    expect(b2!.width).toBeGreaterThan(0);
  });

  test('D-050: nesting is visible — outer descendant depth > inner, driving distinct bounds', () => {
    // outer wraps inner; inner wraps a leaf.
    const containers = [
      { id: 'outer', parent: null },
      { id: 'inner', parent: 'outer' },
    ];
    const outerDepth = d2ContainerDescendantDepth('outer', containers as any);
    const innerDepth = d2ContainerDescendantDepth('inner', containers as any);
    // FIX (D-089): outer has one nested container inside it -> depth 1; inner
    // has none -> depth 0. PRE-FIX there was no depth notion at all and both
    // containers resolved to identical min/max bounds (plaid). depth>inner is
    // what grows the outer rect strictly larger than the inner.
    expect(outerDepth).toBeGreaterThan(innerDepth);
    expect(innerDepth).toBe(0);
    expect(outerDepth).toBeGreaterThanOrEqual(1);

    // And the inset actually enlarges the outer rect vs the inner for the same leaf.
    const leaf = { id: 'leaf', container: 'inner', x: 200, y: 200, width: 80, height: 40 };
    const innerB = d2ContainerBounds(containers[1] as any, [leaf] as any, containers as any);
    const outerB = d2ContainerBounds(containers[0] as any, [leaf] as any, containers as any);
    expect(innerB).not.toBeNull();
    expect(outerB).not.toBeNull();
    expect(outerB!.width).toBeGreaterThan(innerB!.width);
    expect(outerB!.height).toBeGreaterThan(innerB!.height);
  });

  test('D-055: d2CanvasSize is natural pixel size, not a downscaling fixed box', () => {
    // A wide graph: a node far to the right.
    const nodes = [
      { x: 0, y: 0, width: 80, height: 40 },
      { x: 3000, y: 0, width: 100, height: 40 },
    ];
    const size = d2CanvasSize(nodes);
    // FIX: width tracks the content extent (maxX+100 == 3200), so the 12px text
    // is drawn 1:1. PRE-FIX the whole viewBox was downscaled into width:100% of
    // a small host, shrinking 12px text sub-pixel. The natural width must be at
    // least the content's right edge.
    expect(size.width).toBeGreaterThanOrEqual(3100);
    expect(size.width).toBe(3200);
    expect(size.viewBox).toBe(`0 0 ${size.width} ${size.height}`);
    // Sanity: the fixed label size is a positive pixel value, not a ratio.
    expect(D2_FONT_SIZE).toBeGreaterThan(0);
  });

  test('D-056: labels wrap / hard-break and grid pitch cannot overlap boxes', () => {
    // Multi-word label wraps into >1 line at a narrow width.
    const multi = wrapLabel('the quick brown fox jumps over the lazy dog', 100);
    expect(multi.length).toBeGreaterThan(1);

    // A single 600-char unbreakable token is hard-broken into multiple lines,
    // none longer than the per-line char budget -> cannot run off the canvas.
    const longTok = 'x'.repeat(600);
    const broken = wrapLabel(longTok, 120);
    expect(broken.length).toBeGreaterThan(1);
    const maxChars = Math.max(4, Math.floor((120 - 16) / 8));
    for (const line of broken) expect(line.length).toBeLessThanOrEqual(maxChars);

    // Grid pitch is at least the widest node width (+gap) so neighbours never
    // truncate each other. PRE-FIX pitch was a fixed 150 < node width up to 240.
    const wideLabel = 'a-really-wide-node-label-that-hits-the-max-width';
    const w = d2NodeWidth(wideLabel);
    const pitch = d2GridPitch([{ label: wideLabel, width: w, height: 40 }]);
    expect(pitch.x).toBeGreaterThanOrEqual(w);
    expect(pitch.x).toBeGreaterThan(150);
  });

  test('D-057: explicit requested size is honoured, content viewBox preserved', () => {
    const canvas = { width: 900, height: 600, viewBox: '0 0 900 600' };

    // Over-sized extreme-aspect request (3000x300) is applied verbatim; the
    // viewBox stays at the content bounds so preserveAspectRatio scales the whole
    // graph in — PRE-FIX the request was ignored and rows were truncated.
    const big = d2ResolveSvgSize(canvas, 3000, 300);
    expect(big.width).toBe(3000);
    expect(big.height).toBe(300);
    expect(big.viewBox).toBe('0 0 900 600');

    // No request -> natural canvas size retained (D-086 preserved).
    const none = d2ResolveSvgSize(canvas);
    expect(none.width).toBe(900);
    expect(none.height).toBe(600);

    // Invalid/zero/negative requests are ignored (fall back to natural size).
    const bad = d2ResolveSvgSize(canvas, 0, -5);
    expect(bad.width).toBe(900);
    expect(bad.height).toBe(600);
  });
});
