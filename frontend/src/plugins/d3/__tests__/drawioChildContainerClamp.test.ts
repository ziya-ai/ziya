import { clampChildToContainerBounds } from '../drawioPlugin';

/**
 * Issue 49 regression: a child cell whose parent is a container with its own
 * geometry uses PARENT-RELATIVE coordinates. A child placed grossly outside that
 * container (e.g. local (500,900) inside a 60×60 group) makes maxGraph auto-expand
 * the container into a giant obstacle; the Manhattan A* edge router then builds its
 * grid over the ballooned box and hangs to the 30s cap with ZERO output whenever any
 * edge is present.
 *
 * clampChildToContainerBounds() must pull the child ORIGIN of GROSS overflowers back
 * into the parent [0,w]×[0,h] box, while leaving in-bounds / slightly-over children
 * and canvas-absolute (layer-parented) cells byte-identical.
 *
 * Non-vacuous: this export did not exist before the fix, so the import fails against
 * pre-fix code. The guard cases pin BOTH directions — the fix must not clamp things
 * it should leave alone (otherwise it becomes a catch-all).
 */
describe('clampChildToContainerBounds (Issue 49 — child overflowing its container)', () => {
  const child = (x: number, y: number, parent = 'grp', w = 40, h = 40) =>
    `<mxCell id="c" style="rounded=1;" vertex="1" parent="${parent}"><mxGeometry x="${x}" y="${y}" width="${w}" height="${h}" as="geometry"/></mxCell>`;
  const group = (w = 60, h = 60) =>
    `<mxCell id="grp" style="group;container=1;" vertex="1" parent="1"><mxGeometry x="40" y="220" width="${w}" height="${h}" as="geometry"/></mxCell>`;
  const wrap = (inner: string) =>
    `<mxGraphModel><root><mxCell id="0"/><mxCell id="1" parent="0"/>${inner}</root></mxGraphModel>`;

  const childX = (out: string): number =>
    parseFloat(out.match(/id="c"[\s\S]*?<mxGeometry\b[^>]*?\bx="([^"]*)"/)![1]);
  const childY = (out: string): number =>
    parseFloat(out.match(/id="c"[\s\S]*?<mxGeometry\b[^>]*?\by="([^"]*)"/)![1]);

  // ---- fires on gross overflow (both directions clamped into the parent box) ----
  it('clamps a grossly-overflowing child origin into the parent box', () => {
    const out = clampChildToContainerBounds(wrap(group(60, 60) + child(500, 900)));
    expect(childX(out)).toBeGreaterThanOrEqual(0);
    expect(childX(out)).toBeLessThanOrEqual(60);
    expect(childY(out)).toBeGreaterThanOrEqual(0);
    expect(childY(out)).toBeLessThanOrEqual(60);
    // specifically pulled to the far edge (min(w, x) = 60, min(h, y) = 60)
    expect(childX(out)).toBe(60);
    expect(childY(out)).toBe(60);
  });

  it('clamps a NEGATIVE gross overflow up to 0', () => {
    const out = clampChildToContainerBounds(wrap(group(60, 60) + child(-500, -900)));
    expect(childX(out)).toBe(0);
    expect(childY(out)).toBe(0);
  });

  it('clamps only the axis that overflows (x gross, y in-bounds)', () => {
    const out = clampChildToContainerBounds(wrap(group(60, 60) + child(500, 20)));
    expect(childX(out)).toBe(60);
    expect(childY(out)).toBe(20); // y within box, untouched
  });

  // ---------------------------- guards (untouched) -----------------------------
  it('leaves an in-bounds child byte-identical', () => {
    const xml = wrap(group(60, 60) + child(10, 10));
    expect(clampChildToContainerBounds(xml)).toBe(xml);
  });

  it('leaves a SLIGHTLY-over child (within factor) byte-identical', () => {
    // 80 < 60*3 = 180 → not gross → untouched
    const xml = wrap(group(60, 60) + child(80, 80));
    expect(clampChildToContainerBounds(xml)).toBe(xml);
  });

  it('does NOT clamp a child of a layer / cell "1" (canvas-absolute coords)', () => {
    const xml = wrap(child(5000, 9000, '1'));
    expect(clampChildToContainerBounds(xml)).toBe(xml);
  });

  it('does NOT clamp a child of a bare layer cell that has no geometry', () => {
    const layer = '<mxCell id="layer0" value="L" style="" parent="0"/>';
    const xml = wrap(layer + child(5000, 9000, 'layer0'));
    expect(clampChildToContainerBounds(xml)).toBe(xml);
  });

  it('does NOT clamp a relative (edge-label) child geometry', () => {
    const rel =
      '<mxCell id="c" style="edgeLabel;" vertex="1" parent="grp"><mxGeometry x="500" y="900" relative="1" as="geometry"/></mxCell>';
    const xml = wrap(group(60, 60) + rel);
    expect(clampChildToContainerBounds(xml)).toBe(xml);
  });

  it('does NOT clamp when the container has a degenerate (zero/negative) size', () => {
    const xml = wrap(group(0, 0) + child(500, 900));
    expect(clampChildToContainerBounds(xml)).toBe(xml);
  });

  it('returns byte-identical when nothing overflows', () => {
    const xml = wrap(group(60, 60) + child(5, 5));
    expect(clampChildToContainerBounds(xml)).toBe(xml);
  });

  it('is idempotent (a second pass changes nothing)', () => {
    const once = clampChildToContainerBounds(wrap(group(60, 60) + child(500, 900)));
    expect(clampChildToContainerBounds(once)).toBe(once);
  });

  // ------- end-to-end: the exact iteration-49 adversarial group/child pair -------
  it('clamps the real Issue-49 grp_overflow / grp_child_overflow pair', () => {
    const xml = wrap(
      '<mxCell id="grp_overflow" value="" style="group;container=1;" vertex="1" connectable="0" parent="1"><mxGeometry x="40" y="220" width="60" height="60" as="geometry"/></mxCell>' +
      '<mxCell id="grp_child_overflow" value="Child" style="rounded=1;whiteSpace=wrap;" vertex="1" parent="grp_overflow"><mxGeometry x="500" y="900" width="120" height="60" as="geometry"/></mxCell>'
    );
    const out = clampChildToContainerBounds(xml);
    const cx = parseFloat(out.match(/id="grp_child_overflow"[\s\S]*?\bx="([^"]*)"/)![1]);
    const cy = parseFloat(out.match(/id="grp_child_overflow"[\s\S]*?\by="([^"]*)"/)![1]);
    expect(cx).toBeLessThanOrEqual(60);
    expect(cy).toBeLessThanOrEqual(60);
    // the container geometry itself (id grp_overflow) is left alone
    expect(out).toContain('width="60" height="60"');
  });
});
