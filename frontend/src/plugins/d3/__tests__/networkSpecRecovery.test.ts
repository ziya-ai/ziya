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
