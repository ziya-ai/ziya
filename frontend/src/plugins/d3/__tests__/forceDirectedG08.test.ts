/**
 * G-08 — force-directed plugin: group-palette contrast + recycling, radius-aware
 * arrowheads, declared style options, and unresolved-endpoint recovery
 * (shared file: forceDirectedPlugin.ts).
 *
 * Defects covered:
 *   D-020  group palette entries are contrast-reconciled per-theme (dark-tuned
 *          entries no longer vanish on a light canvas) and recycled groups past
 *          the 10-entry table are de-collided (hue-rotated), not reused verbatim.
 *   D-022  the link arrowhead is a fixed pixel size (markerUnits=userSpaceOnUse)
 *          and sits at the target node's rim (segment shortened by radius),
 *          instead of scaling with stroke-width at a fixed refX=20.
 *   D-121  declared options are honoured: style.nodeColor (uniform fill) is read
 *          by the node-fill resolver, and link.color is applied per-datum.
 *   D-124  from/to & name aliases and array-index endpoints resolve instead of
 *          dropping every edge; unresolvable endpoints are counted (warned).
 *
 * Direction: every theme assertion pairs the newly-fixed theme against the theme
 * that was already correct, and every structural assertion checks a value the
 * pre-fix code did not produce (raw sub-floor palette entry, refX=20 / no
 * markerUnits, palette fallback ignoring style.nodeColor, dropped aliased edges).
 */
import { contrastRatio } from '../chartTheme';
import {
  forceDirectedPlugin,
  groupColor,
  rotateHue,
  resolveNodeFill,
  resolveLinkStroke,
  shortenToTarget,
  normalizeGraph,
  FORCE_LIGHT_BG,
  FORCE_DARK_BG,
} from '../forceDirectedPlugin';

// ── mock d3 that records .attr()/.text() calls without a DOM ──────────────────
function makeMockD3() {
  const record: any = { attrs: [] as Array<[string, any]>, texts: [] as Array<(d: any) => any> };
  const target: any = function () {};
  const proxy: any = new Proxy(target, {
    get(_t, prop) {
      if (prop === 'zoomIdentity') return proxy;
      const name = String(prop);
      return (...args: any[]) => {
        if (name === 'attr' && args.length >= 1) record.attrs.push([args[0], args[1]]);
        else if (name === 'text' && typeof args[0] === 'function') record.texts.push(args[0]);
        return proxy;
      };
    },
    apply() {
      return proxy;
    },
  });
  return { d3: proxy, record };
}

function runRender(spec: any, isDark = false) {
  const { d3, record } = makeMockD3();
  const cleanup = forceDirectedPlugin.render({} as any, d3, spec, isDark);
  if (typeof cleanup === 'function') cleanup();
  return record;
}

// ── D-020 group-palette contrast + recycling ─────────────────────────────────

describe('D-020 — group palette reconciled per-theme and de-collided past 10', () => {
  // Index 5 = #edc948 in DEFAULT_GROUP_COLORS: 1.61:1 on white, 10:1 on #212121.
  it('nudges a dark-tuned entry to clear 3:1 on the LIGHT canvas', () => {
    // Direction: the raw palette entry is BELOW the graphical floor on white,
    // which is exactly what the old getNodeColor returned verbatim.
    expect(contrastRatio('#edc948', FORCE_LIGHT_BG)).toBeLessThan(3);
    const c = groupColor(5, FORCE_LIGHT_BG);
    expect(c.toLowerCase()).not.toBe('#edc948');
    expect(contrastRatio(c, FORCE_LIGHT_BG)).toBeGreaterThanOrEqual(3);
  });

  it('leaves the same entry UNCHANGED on the dark canvas (per-theme, not a swap)', () => {
    const c = groupColor(5, FORCE_DARK_BG);
    expect(c.toLowerCase()).toBe('#edc948');
    expect(contrastRatio(c, FORCE_DARK_BG)).toBeGreaterThanOrEqual(3);
  });

  it('every palette index clears 3:1 in BOTH themes after reconciliation', () => {
    for (let i = 0; i < 10; i++) {
      expect(contrastRatio(groupColor(i, FORCE_LIGHT_BG), FORCE_LIGHT_BG)).toBeGreaterThanOrEqual(3);
      expect(contrastRatio(groupColor(i, FORCE_DARK_BG), FORCE_DARK_BG)).toBeGreaterThanOrEqual(3);
    }
  });

  it('recycled group 10 is DISTINCT from group 0 (was identical at %10)', () => {
    expect(groupColor(10, FORCE_LIGHT_BG)).not.toBe(groupColor(0, FORCE_LIGHT_BG));
    expect(groupColor(10, FORCE_DARK_BG)).not.toBe(groupColor(0, FORCE_DARK_BG));
    // rotateHue actually moves the colour
    expect(rotateHue('#4e79a7', 137).toLowerCase()).not.toBe('#4e79a7');
    // non-hex passes through
    expect(rotateHue('steelblue', 90)).toBe('steelblue');
  });
});

// ── D-022 radius-aware, stroke-independent arrowhead ─────────────────────────

describe('D-022 — arrowhead fixed-size and placed at the target rim', () => {
  it('shortenToTarget stops the segment radius+gap before the target centre', () => {
    const p = shortenToTarget(0, 0, 100, 0, 8, 4); // off = 12 -> ends at x=88
    expect(p.x).toBeCloseTo(88, 5);
    expect(p.y).toBeCloseTo(0, 5);
    // a longer target radius pulls the endpoint back further
    const q = shortenToTarget(0, 0, 100, 0, 30, 4); // off = 34 -> ends at x=66
    expect(q.x).toBeCloseTo(66, 5);
    // degenerate zero-length segment is safe
    expect(shortenToTarget(5, 5, 5, 5, 8)).toEqual({ x: 5, y: 5 });
  });

  it('render sets markerUnits=userSpaceOnUse and refX=10 (was strokeWidth / refX=20)', () => {
    const rec = runRender({
      type: 'force-directed',
      definition: JSON.stringify({
        nodes: [{ id: 'A' }, { id: 'B' }],
        links: [{ source: 'A', target: 'B' }],
      }),
    });
    expect(rec.attrs.some(([k, v]) => k === 'markerUnits' && v === 'userSpaceOnUse')).toBe(true);
    expect(rec.attrs.some(([k, v]) => k === 'refX' && v === 10)).toBe(true);
    // the old fixed refX=20 must be gone
    expect(rec.attrs.some(([k, v]) => k === 'refX' && v === 20)).toBe(false);
  });
});

// ── D-121 declared style options honoured ────────────────────────────────────

describe('D-121 — style.nodeColor and link.color are read', () => {
  it('resolveNodeFill uses the uniform style.nodeColor when a node has no colour', () => {
    for (const bg of [FORCE_LIGHT_BG, FORCE_DARK_BG]) {
      const fill = resolveNodeFill({ group: 0 }, { nodeColor: '#ff0000' }, bg);
      // Direction: the old resolver ignored style.nodeColor and fell back to the
      // group palette (group 0). The uniform colour must now win over that.
      expect(fill).not.toBe(groupColor(0, bg));
      // #ff0000 clears 3:1 on both surfaces, so it is preserved verbatim
      expect(fill.toLowerCase()).toBe('#ff0000');
    }
  });

  it('an explicit node.color still beats the uniform style.nodeColor', () => {
    const fill = resolveNodeFill({ color: '#4e79a7', group: 0 }, { nodeColor: '#ff0000' }, FORCE_LIGHT_BG);
    expect(fill.toLowerCase()).toBe('#4e79a7'); // node.color wins, and clears 3:1 on white
  });

  it('resolveLinkStroke applies a per-link colour, contrast-reconciled', () => {
    for (const bg of [FORCE_LIGHT_BG, FORCE_DARK_BG]) {
      const dflt = bg === FORCE_DARK_BG ? '#b0b0b0' : '#6b6b6b';
      const stroke = resolveLinkStroke({ color: '#e15759' }, bg, 0.9, dflt);
      // Direction: the old render set stroke once from the global default; a
      // per-link colour never reached the <line>. It must now differ from default
      // and be readable on the canvas.
      expect(stroke.toLowerCase()).not.toBe(dflt);
      expect(contrastRatio(stroke, bg)).toBeGreaterThanOrEqual(3);
      // absent per-link colour keeps the resolved default
      expect(resolveLinkStroke({}, bg, 0.9, dflt)).toBe(dflt);
    }
  });
});

// ── D-124 unresolved-endpoint recovery ───────────────────────────────────────

describe('D-124 — endpoint aliases / indices resolve instead of dropping edges', () => {
  it('maps name->id and from/to->source/target', () => {
    const rawLinks = [{ from: 'A', to: 'B' }];
    // Direction: the raw link carries NO source/target, so the old endpoint
    // filter (which read only source/target) dropped it entirely.
    expect((rawLinks[0] as any).source).toBeUndefined();

    const g = normalizeGraph([{ name: 'A' }, { name: 'B' }], rawLinks);
    expect(g.nodes[0].id).toBe('A');
    expect(g.links.length).toBe(1);
    expect(g.links[0].source).toBe('A');
    expect(g.links[0].target).toBe('B');
    expect(g.dropped).toBe(0);
  });

  it('resolves numeric array-index endpoints against the node order', () => {
    const g = normalizeGraph([{ id: 'A' }, { id: 'B' }], [{ source: 0, target: 1 }]);
    expect(g.links.length).toBe(1);
    expect(g.links[0].source).toBe('A');
    expect(g.links[0].target).toBe('B');
  });

  it('a real numeric id is preferred over index interpretation', () => {
    const g = normalizeGraph([{ id: '0' }, { id: '1' }], [{ source: '0', target: '1' }]);
    expect(g.links[0].source).toBe('0');
    expect(g.dropped).toBe(0);
  });

  it('counts (does not silently swallow) links whose endpoints never resolve', () => {
    const g = normalizeGraph([{ id: 'A' }], [{ source: 'A', target: 'ZZ' }]);
    expect(g.links.length).toBe(0);
    expect(g.dropped).toBe(1);
  });

  it('render recovers an aliased spec without throwing in BOTH themes', () => {
    const spec = {
      type: 'force-directed',
      definition: JSON.stringify({
        nodes: [{ name: 'A' }, { name: 'B' }],
        links: [{ from: 'A', to: 'B' }],
      }),
    };
    expect(() => runRender(spec, false)).not.toThrow();
    expect(() => runRender(spec, true)).not.toThrow();
  });
});
