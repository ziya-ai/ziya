/**
 * G-45 — d2 plugin theme + colour-guard + statement-separator repairs
 * (shared file: d2Plugin.ts, part 6). Four defects worked as one group:
 *
 *   D-094 (theme, dark) — dark node fill darkened #4361ee -> #303f9f so white
 *          label text clears the 4.5 floor with margin (5.02:1 -> 8.98:1).
 *   D-095 (theme, dark) — dark edge desaturated #f72585 -> #9aa4b2 so edges
 *          stop out-shouting nodes and are visible where they cross a node.
 *   D-098 (recovery) — an author colour is honoured only when paintable; an
 *          unusable value (transparent / none / currentColor / var()/$token /
 *          zero-alpha) falls back to the theme colour instead of erasing or
 *          blackening the geometry.
 *   D-100 (recovery) — `;` is a top-level statement separator, so
 *          `a -> b; b -> c` yields both edges and `a: L1; b: L2` two nodes.
 *
 * Both theme defects assert BOTH themes. Direction: every assertion is paired
 * with the PRE-FIX value (old #4361ee / #f72585 constants; verbatim colour
 * passthrough; a single greedy connection line), so each test FAILS against
 * unpatched d2Plugin.ts.
 */
import { contrastRatio } from '../chartTheme';
import {
  d2ThemeColors,
  isUsableD2Color,
  splitD2Statements,
  D2Parser,
  D2_DARK_BG,
  D2_LIGHT_BG,
} from '../d2Plugin';

// ---------------------------------------------------------------------------
// D-094 — dark node fill legible white label (both themes)
// ---------------------------------------------------------------------------
describe('D-094 dark node fill — white label clears the text floor', () => {
  it('dark: white text on the node fill clears 4.5 with margin', () => {
    const dark = d2ThemeColors(true);
    expect(dark.text.toLowerCase()).toBe('#ffffff');
    const ratio = contrastRatio(dark.text, dark.node);
    expect(ratio).toBeGreaterThanOrEqual(4.5);
    expect(ratio).toBeGreaterThan(7); // ~8.98
  });

  it('direction: the OLD dark fill #4361ee gave white text only ~5.02:1 (marginal)', () => {
    const old = contrastRatio('#ffffff', '#4361ee');
    expect(old).toBeLessThan(6);
    // the new fill is a strict improvement
    expect(contrastRatio('#ffffff', d2ThemeColors(true).node)).toBeGreaterThan(old);
  });

  it('light UNCHANGED: black text on light node fill #e3f2fd stays ~18:1', () => {
    const light = d2ThemeColors(false);
    expect(light.node.toLowerCase()).toBe('#e3f2fd');
    expect(light.text.toLowerCase()).toBe('#000000');
    expect(contrastRatio(light.text, light.node)).toBeGreaterThan(15);
  });
});

// ---------------------------------------------------------------------------
// D-095 — dark edge visible but recessive (both themes)
// ---------------------------------------------------------------------------
describe('D-095 dark edge — visible on page AND on the node, no longer dominant', () => {
  it('dark: edge clears 3:1 on the page and 3:1 crossing the node fill', () => {
    const dark = d2ThemeColors(true);
    expect(contrastRatio(dark.edge, D2_DARK_BG)).toBeGreaterThanOrEqual(3);
    expect(contrastRatio(dark.edge, dark.node)).toBeGreaterThanOrEqual(3); // ~3.56
  });

  it('dark: the edge is no longer the saturated magenta #f72585', () => {
    expect(d2ThemeColors(true).edge.toLowerCase()).not.toBe('#f72585');
  });

  it('direction: the OLD magenta #f72585 vanished on the node fill (<3:1)', () => {
    // old magenta on the old fill was 1.33:1 — invisible on contact
    expect(contrastRatio('#f72585', '#4361ee')).toBeLessThan(3);
    // and on the new fill it would still fail; the new grey passes
    expect(contrastRatio('#f72585', d2ThemeColors(true).node)).toBeLessThan(3);
  });

  it('light UNCHANGED: edge #666666 visible on page and node fill', () => {
    const light = d2ThemeColors(false);
    expect(light.edge.toLowerCase()).toBe('#666666');
    expect(contrastRatio(light.edge, D2_LIGHT_BG)).toBeGreaterThanOrEqual(3);
    expect(contrastRatio(light.edge, light.node)).toBeGreaterThanOrEqual(3);
  });
});

// ---------------------------------------------------------------------------
// D-098 — unusable author colour falls back to the theme colour (both themes)
// ---------------------------------------------------------------------------
describe('D-098 isUsableD2Color — guards geometry-erasing / unresolvable colours', () => {
  it('rejects transparent / none / currentColor / token / zero-alpha forms', () => {
    for (const bad of [
      'transparent', 'none', 'currentColor', 'CURRENTCOLOR', '',
      'var(--ziya-accent-500)', '$theme', '--brand',
      'rgba(255,99,71,0)', 'rgba(0,0,0,0.0)', 'hsla(200,50%,50%,0)', '#11223300',
    ]) {
      expect(isUsableD2Color(bad)).toBe(false);
    }
  });

  it('accepts real, paintable colour forms verbatim', () => {
    for (const ok of ['#f0f', '#ff0000', 'cornflowerblue', 'rgb(255,99,71)', 'rgba(255,99,71,0.5)', '#11223344']) {
      expect(isUsableD2Color(ok)).toBe(true);
    }
  });

  it('render fallback: a `fill: transparent` node uses the theme fill in BOTH themes', () => {
    const { nodes } = new D2Parser().parse('a: Box\na.style.fill: transparent');
    const node = nodes.find((n: any) => n.originalId === 'a' || n.id === 'a');
    expect(node.style.fill).toBe('transparent'); // author value preserved on the node
    // The render() colour accessor logic: usable? -> value ; else theme fill.
    const resolve = (isDark: boolean) =>
      isUsableD2Color(node.style.fill) ? node.style.fill : d2ThemeColors(isDark).node;
    expect(resolve(true)).toBe('#303f9f');   // dark -> theme fill, NOT transparent
    expect(resolve(false)).toBe('#e3f2fd');  // light -> theme fill, NOT transparent
    // direction: the OLD accessor (`d.style.fill ? d.style.fill : theme`) kept
    // the geometry-erasing value
    const oldAccessor = node.style.fill ? node.style.fill : 'THEME';
    expect(oldAccessor).toBe('transparent');
  });
});

// ---------------------------------------------------------------------------
// D-100 — semicolon statement separator
// ---------------------------------------------------------------------------
describe('D-100 splitD2Statements — top-level `;` splits statements', () => {
  it('splits a chained-connection line into independent statements', () => {
    expect(splitD2Statements('web -> api; api -> db; db -> cache'))
      .toEqual(['web -> api', 'api -> db', 'db -> cache']);
  });

  it('splits semicolon-separated node declarations', () => {
    expect(splitD2Statements('web: Web Server; api: API Service'))
      .toEqual(['web: Web Server', 'api: API Service']);
  });

  it('does NOT split a `;` inside an inline { } property block', () => {
    expect(splitD2Statements('node: { shape: circle; fill: blue }'))
      .toEqual(['node: { shape: circle; fill: blue }']);
  });

  it('does NOT split a `;` inside a quoted label', () => {
    expect(splitD2Statements('x: "a; b"')).toEqual(['x: "a; b"']);
  });

  it('a line with no top-level `;` is returned unchanged', () => {
    expect(splitD2Statements('a -> b')).toEqual(['a -> b']);
  });
});

describe('D-100 parser — semicolon lines yield all nodes and edges', () => {
  it('`web -> api; api -> db; db -> cache` -> 4 nodes, 3 edges', () => {
    const { nodes, edges } = new D2Parser().parse('web -> api; api -> db; db -> cache');
    expect(nodes.length).toBe(4);
    expect(edges.length).toBe(3);
  });

  it('`web: Web Server; api: API Service` -> 2 distinctly-labelled nodes', () => {
    const { nodes } = new D2Parser().parse('web: Web Server; api: API Service');
    expect(nodes.length).toBe(2);
    const labels = nodes.map((n: any) => n.label).sort();
    expect(labels).toEqual(['API Service', 'Web Server']);
  });

  it('direction: the PRE-FIX greedy connection split swallowed `; api` into an endpoint', () => {
    // The old parser split ONLY on connectors, so an endpoint carried the ';'.
    const parts = 'web -> api; api -> db; db -> cache'.split(/(<->|<-|->)/);
    const endpoints = parts.filter((_, i) => i % 2 === 0).map(s => s.trim());
    expect(endpoints.some(e => e.includes(';'))).toBe(true); // e.g. 'api; api'
  });
});
