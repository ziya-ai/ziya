/**
 * Regression test for Issue 11 (network renderer): two layered defects, the
 * same class as Issue 10 (chord) and Issue 2 (joint).
 *
 * 1. CONTRACT MISMATCH + FIELD-NAME MISMATCH (total outage): the render_diagram
 *    tool wrapper always packs the caller's payload into `spec.definition` as a
 *    JSON STRING and never hoists `nodes`/`edges` onto the top-level spec. The
 *    network plugin's `isNetworkDiagramSpec` read `spec.nodes`/`spec.links`
 *    directly, AND required `links` while the documented network format uses
 *    `edges`. So EVERY network spec via render_diagram was invisible to the
 *    plugin: `findPluginForSpec` returned undefined and the D3Renderer
 *    orchestrator retried to a ~30-35s timeout with zero output. Fix:
 *    `resolveNetworkSpec` recovers structured fields from a JSON `definition`
 *    and normalizes `edges` -> `links`.
 *
 * 2. DEGENERATE VALUES + DANGLING EDGES (NaN geometry / off-canvas / crashes):
 *    once selected, node `size` of 0/-25/1e9 and edge `weight` of -50/1e12, plus
 *    edges pointing at undeclared node ids, would produce bad radii/stroke widths
 *    or draw lines to unresolved endpoints. Fix: `sanitizeNetworkGraph` clamps
 *    size/weight to sane bounds and filters edges whose endpoints are absent.
 *
 * The test imports the REAL shipped module (no re-implementation) so it detects
 * drift and would FAIL against the pre-fix source (which had neither exported
 * helper and required `links`).
 */
import {
  resolveNetworkSpec,
  sanitizeNetworkGraph,
  networkDiagramPlugin,
  NETWORK_MAX_NODE_SIZE,
  NETWORK_MAX_LINK_WIDTH,
} from '../networkDiagram';

describe('Issue 11 — resolveNetworkSpec (definition-string contract + edges alias)', () => {
  it('recovers nodes/edges from a JSON `definition` string and normalizes edges -> links', () => {
    const wrapped = {
      type: 'network',
      definition: JSON.stringify({
        type: 'network',
        directed: true,
        width: 800,
        height: 600,
        nodes: [{ id: 'a' }, { id: 'b' }],
        edges: [{ source: 'a', target: 'b', weight: 2 }],
      }),
    };
    const resolved = resolveNetworkSpec(wrapped);
    expect(Array.isArray(resolved.nodes)).toBe(true);
    expect(resolved.nodes).toHaveLength(2);
    // `edges` must be surfaced as `links` for the render loop
    expect(Array.isArray(resolved.links)).toBe(true);
    expect(resolved.links[0]).toMatchObject({ source: 'a', target: 'b', weight: 2 });
    expect(resolved.directed).toBe(true);
    expect(resolved.width).toBe(800);
    expect(resolved.height).toBe(600);
  });

  it('the plugin canHandle ACCEPTS a wrapped network spec (the exact outage case)', () => {
    const wrapped = {
      type: 'network',
      definition: JSON.stringify({
        type: 'network',
        nodes: [{ id: 'a' }, { id: 'b' }],
        edges: [{ source: 'a', target: 'b' }],
      }),
    };
    expect(networkDiagramPlugin.canHandle(wrapped)).toBe(true);
  });

  it('canHandle accepts an already-structured spec that uses `edges` (no links key)', () => {
    const structured = {
      type: 'network',
      nodes: [{ id: 'a' }, { id: 'b' }],
      edges: [{ source: 'a', target: 'b' }],
    };
    expect(networkDiagramPlugin.canHandle(structured)).toBe(true);
  });

  it('canHandle still accepts the legacy `links` field (no regression)', () => {
    const legacy = {
      type: 'network',
      nodes: [{ id: 'a' }, { id: 'b' }],
      links: [{ source: 'a', target: 'b' }],
    };
    expect(networkDiagramPlugin.canHandle(legacy)).toBe(true);
  });

  // GUARD CASES — the widened predicate must STILL reject what it rejected before.
  it('canHandle REJECTS a non-network type even when wrapped', () => {
    const wrapped = {
      type: 'chord',
      definition: JSON.stringify({ type: 'chord', nodes: [{ id: 'a' }], edges: [] }),
    };
    expect(networkDiagramPlugin.canHandle(wrapped)).toBe(false);
  });

  it('canHandle REJECTS a network spec with no nodes/edges anywhere', () => {
    expect(networkDiagramPlugin.canHandle({ type: 'network' })).toBe(false);
  });

  it('resolveNetworkSpec leaves a non-JSON definition string untouched', () => {
    const spec = { type: 'network', definition: 'not json at all' };
    expect(resolveNetworkSpec(spec)).toBe(spec);
  });

  it('resolveNetworkSpec ignores a malformed JSON definition (returns input unchanged)', () => {
    const spec = { type: 'network', definition: '{ "nodes": [ oops' };
    expect(resolveNetworkSpec(spec)).toBe(spec);
  });

  it('resolveNetworkSpec is a no-op for a spec already carrying nodes+edges', () => {
    const spec = { type: 'network', nodes: [{ id: 'a' }], edges: [] };
    expect(resolveNetworkSpec(spec)).toBe(spec);
  });
});

describe('Issue 11 — sanitizeNetworkGraph (degenerate values + dangling edges)', () => {
  it('clamps degenerate node sizes and drops dangling edges', () => {
    const nodes = [
      { id: 'core', size: 60 },
      { id: 'zero', size: 0 },
      { id: 'neg', size: -25 },
      { id: 'huge', size: 1000000000 },
    ];
    const links = [
      { source: 'core', target: 'zero', weight: 1 },
      { source: 'core', target: 'huge', weight: 1e12 },
      { source: 'core', target: 'neg', weight: -50 },
      { source: 'ghost', target: 'core' }, // dangling source
      { source: 'core', target: 'ghost2' }, // dangling target
    ];
    const { nodes: sn, links: sl } = sanitizeNetworkGraph(nodes, links);

    // sizes: valid kept, 0/-25 -> undefined (fall back to default), 1e9 -> cap
    expect(sn.find(n => n.id === 'core')!.size).toBe(60);
    expect(sn.find(n => n.id === 'zero')!.size).toBeUndefined();
    expect(sn.find(n => n.id === 'neg')!.size).toBeUndefined();
    expect(sn.find(n => n.id === 'huge')!.size).toBe(NETWORK_MAX_NODE_SIZE);

    // dangling edges removed; only the 3 with both endpoints present survive
    expect(sl).toHaveLength(3);
    expect(sl.some(l => l.source === 'ghost' || l.target === 'ghost2')).toBe(false);

    // weights: huge clamped, negative -> undefined (default), normal kept
    expect(sl.find(l => l.target === 'huge')!.weight).toBe(NETWORK_MAX_LINK_WIDTH);
    expect(sl.find(l => l.target === 'neg')!.weight).toBeUndefined();
    expect(sl.find(l => l.target === 'zero')!.weight).toBe(1);
  });

  it('is pure — does not mutate input arrays/objects', () => {
    const nodes = [{ id: 'a', size: 1e9 }];
    const links = [{ source: 'a', target: 'a', weight: 1e12 }];
    sanitizeNetworkGraph(nodes, links);
    expect(nodes[0].size).toBe(1e9); // original untouched
    expect(links[0].weight).toBe(1e12);
  });

  it('tolerates non-array input', () => {
    const { nodes, links } = sanitizeNetworkGraph(undefined as any, null as any);
    expect(nodes).toEqual([]);
    expect(links).toEqual([]);
  });
});

/**
 * Regression test for Issue 21 (network renderer): CANVAS-RELATIVE NODE-SIZE CAP.
 *
 * ANOMALY: an adversarial spec with node `size` 1e12 (hub) and 1e300
 * (precise_b) rendered as a SINGLE FLAT TEAL RECTANGLE — total data loss. The
 * Issue-11 `sanitizeNetworkGraph` DID clamp size, but to the standalone
 * constant `NETWORK_MAX_NODE_SIZE` (1000). On a 600x400 canvas a radius-1000
 * circle (default fill #69b3a2) covers the ENTIRE viewport, hiding every other
 * node and edge. The clamp bound was unrelated to canvas size, so a huge finite
 * value still produced total occlusion.
 *
 * FIX: `networkNodeSizeCap(width,height)` derives a per-node radius cap from the
 * smaller canvas dimension (NETWORK_NODE_SIZE_CANVAS_FRACTION), and
 * `sanitizeNetworkGraph` accepts that cap as its 3rd argument. A single node can
 * no longer paint over the whole canvas.
 *
 * Imports the REAL shipped module; would FAIL against pre-fix source (which had
 * no `networkNodeSizeCap` export and no cap parameter — the 2-arg call always
 * clamped to 1000).
 */
import {
  networkNodeSizeCap,
  NETWORK_NODE_SIZE_CANVAS_FRACTION,
  NETWORK_DEFAULT_NODE_SIZE,
} from '../networkDiagram';

describe('Issue 21 — networkNodeSizeCap (canvas-relative node radius cap)', () => {
  it('caps radius to a fraction of the smaller canvas dimension', () => {
    // 600x400 -> min 400 * 0.15 = 60
    expect(networkNodeSizeCap(600, 400)).toBeCloseTo(400 * NETWORK_NODE_SIZE_CANVAS_FRACTION, 6);
    // portrait: 400x600 -> min 400 * 0.15 = 60 (uses the SMALLER dimension)
    expect(networkNodeSizeCap(400, 600)).toBeCloseTo(400 * NETWORK_NODE_SIZE_CANVAS_FRACTION, 6);
  });

  it('the cap is far smaller than the canvas, so a capped node cannot cover it', () => {
    const w = 600, h = 400;
    const cap = networkNodeSizeCap(w, h);
    // A radius-`cap` circle has diameter 2*cap; that must stay well under the
    // smaller canvas dimension (this is the invariant the anomaly violated:
    // 2*1000 = 2000 >> 400).
    expect(2 * cap).toBeLessThan(Math.min(w, h));
  });

  it('never returns below the default node radius, never above the absolute guard', () => {
    // tiny canvas -> floored at the default radius, not 0
    expect(networkNodeSizeCap(10, 10)).toBe(NETWORK_DEFAULT_NODE_SIZE);
    // giant canvas -> still capped by the absolute guard (1000)
    expect(networkNodeSizeCap(1e9, 1e9)).toBe(NETWORK_MAX_NODE_SIZE);
  });

  it('falls back to sane defaults for missing/degenerate canvas dimensions', () => {
    // default canvas 600x400 -> 60
    expect(networkNodeSizeCap(undefined, undefined)).toBeCloseTo(60, 6);
    expect(networkNodeSizeCap(0, -5)).toBeCloseTo(60, 6);
    expect(networkNodeSizeCap(NaN, Infinity)).toBeCloseTo(60, 6);
  });

  it('sanitizeNetworkGraph clamps huge size to the CANVAS cap, reproducing the fix', () => {
    const cap = networkNodeSizeCap(600, 400); // 60
    const { nodes } = sanitizeNetworkGraph(
      [
        { id: 'hub', size: 1e12 },
        { id: 'precise_b', size: 1e300 },
        { id: 'normal', size: 12 },
      ],
      [],
      cap
    );
    // hub + precise_b clamped to the canvas cap (60), NOT the old 1000
    expect(nodes.find(n => n.id === 'hub')!.size).toBe(cap);
    expect(nodes.find(n => n.id === 'precise_b')!.size).toBe(cap);
    expect(nodes.find(n => n.id === 'hub')!.size).toBeLessThan(NETWORK_MAX_NODE_SIZE);
    // in-bounds size is left untouched (cap did not become a catch-all)
    expect(nodes.find(n => n.id === 'normal')!.size).toBe(12);
  });

  it('GUARD: without the canvas cap arg it still clamps to the absolute guard (back-compat)', () => {
    // Pre-fix behavior preserved for callers that pass no cap: 1e12 -> 1000.
    const { nodes } = sanitizeNetworkGraph([{ id: 'hub', size: 1e12 }], []);
    expect(nodes[0].size).toBe(NETWORK_MAX_NODE_SIZE);
  });

  it('GUARD: a degenerate cap (<=0 / non-finite) collapses to the absolute guard, never 0', () => {
    // A bad cap must NOT clamp every node to 0 (which would be a new blank-render bug).
    const { nodes } = sanitizeNetworkGraph([{ id: 'hub', size: 1e12 }], [], 0);
    expect(nodes[0].size).toBe(NETWORK_MAX_NODE_SIZE);
    const r2 = sanitizeNetworkGraph([{ id: 'hub', size: 1e12 }], [], NaN);
    expect(r2.nodes[0].size).toBe(NETWORK_MAX_NODE_SIZE);
  });
});
