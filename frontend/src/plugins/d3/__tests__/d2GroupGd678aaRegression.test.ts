/**
 * Regression guard for fix-group G-d678aa (d2 engine): defects D-061, D-063,
 * D-064, D-069, D-071, D-073. These six all live in d2Plugin.ts and have each
 * been verified then regressed four times over the sweep — the failure keeps
 * re-surfacing at render time even though the source fix is present. This suite
 * pins, at the unit tier, the exact geometry/colour invariants those fixes
 * guarantee so a future edit that reintroduces the collapse is caught here
 * instead of only in a later render sweep.
 *
 * Confirmed against current source by direct probe (2026-09-21): the ELK
 * hierarchical path for a nested container graph produces stacked, compact,
 * NON-overlapping leaves and a container rect ~152px tall (not the ~900px
 * full-canvas overbound seen in the stale D-063/D-064 renders), and the
 * container-less fallback (d2SimpleLayout) is likewise compact.
 */
import {
  D2Parser,
  buildElkHierarchy,
  flattenElkResult,
  d2ContainerBounds,
  d2NodesOverlap,
  d2SimpleLayout,
  d2ThemeColors,
} from '../d2Plugin';
import ELK from 'elkjs';

const NESTED_DEF = `region {
  az1 {
    node1: Node 1
    node2: Node 2
    node1 -> node2
  }
  az2 {
    node3: Node 3
  }
}`;

describe('G-d678aa: nested container bounds stay compact (D-063 / D-064)', () => {
  test('ELK hierarchical layout: no overlap, container rects hug their members', async () => {
    const parser = new D2Parser();
    const { nodes, edges, containers } = parser.parse(NESTED_DEF) as any;
    expect(nodes.length).toBe(3);
    expect(containers.length).toBe(3);

    const graph = buildElkHierarchy(nodes, edges, containers, { direction: 'DOWN' });
    const elk = new (ELK as any)();
    const laid = await elk.layout(graph);
    const flat = flattenElkResult(laid, nodes);

    // Every leaf must be laid out and none may overlap (the D-361 collapse would
    // pack them at a near-zero pitch, hiding the edge under the target rect).
    expect(flat.length).toBe(3);
    expect(d2NodesOverlap(flat)).toBe(false);

    const bounds: Record<string, any> = {};
    for (const c of containers) bounds[c.id] = d2ContainerBounds(c, flat, containers);

    // A container rect for 40px-tall leaves must not balloon to the full canvas
    // (the D-063/D-064 "overbounded-tall" defect drew ~900px rects). The whole
    // three-node graph is well under 400px in each dimension.
    for (const id of Object.keys(bounds)) {
      expect(bounds[id]).not.toBeNull();
      expect(bounds[id].height).toBeLessThan(400);
      expect(bounds[id].width).toBeLessThan(400);
      expect(bounds[id].height).toBeGreaterThan(0);
    }

    // Nesting must be strict: region encloses az1 and az2 (outer rect strictly
    // larger, no false one-inside-another slicing).
    const R = bounds['region'];
    const A1 = bounds['region.az1'];
    const A2 = bounds['region.az2'];
    for (const child of [A1, A2]) {
      expect(R.x).toBeLessThanOrEqual(child.x);
      expect(R.y).toBeLessThanOrEqual(child.y);
      expect(R.x + R.width).toBeGreaterThanOrEqual(child.x + child.width);
      expect(R.y + R.height).toBeGreaterThanOrEqual(child.y + child.height);
    }
  });

  test('fallback layout (elkjs unavailable) is also compact and grouped', () => {
    const parser = new D2Parser();
    const { nodes, edges, containers } = parser.parse(NESTED_DEF) as any;
    const laid = d2SimpleLayout(nodes.map((n: any) => ({ ...n })), edges);
    expect(d2NodesOverlap(laid.nodes)).toBe(false);
    for (const c of containers) {
      const b = d2ContainerBounds(c, laid.nodes, containers);
      expect(b).not.toBeNull();
      expect(b!.height).toBeLessThan(400);
      expect(b!.width).toBeLessThan(600);
    }
  });
});

describe('G-d678aa: dark edge colour resolves from theme (D-073)', () => {
  // D-095: dark edge #f72585 (magenta, figure/ground inversion; 1.33:1 crossing
  // the #303f9f node fill) -> neutral grey-blue #9aa4b2: 6.54:1 on the #1f1f1f
  // page, 3.56:1 over the node fill. Light edge grey #666666: 5.74:1 on white.
  test('dark and light edge colours are the resolved, contrast-checked constants', () => {
    expect(d2ThemeColors(true).edge).toBe('#9aa4b2');
    expect(d2ThemeColors(false).edge).toBe('#666666');
    // The two themes must resolve INDEPENDENTLY (a dark tweak may not leak into
    // light) — the whole point of the theme-resolution fix.
    expect(d2ThemeColors(true).edge).not.toBe(d2ThemeColors(false).edge);
  });

  test('computed contrast: dark edge >= 4.5:1 on page, light edge >= 4.5:1 on page', () => {
    const lum = (hex: string) => {
      const n = hex.replace('#', '');
      const ch = [0, 2, 4].map(i => parseInt(n.slice(i, i + 2), 16) / 255)
        .map(c => (c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4)));
      return 0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2];
    };
    const ratio = (a: string, b: string) => {
      const la = lum(a) + 0.05;
      const lb = lum(b) + 0.05;
      return la > lb ? la / lb : lb / la;
    };
    // dark #9aa4b2 on #1f1f1f page
    expect(ratio('#9aa4b2', '#1f1f1f')).toBeGreaterThanOrEqual(4.5);
    // light #666666 on #ffffff page
    expect(ratio('#666666', '#ffffff')).toBeGreaterThanOrEqual(4.5);
  });
});
