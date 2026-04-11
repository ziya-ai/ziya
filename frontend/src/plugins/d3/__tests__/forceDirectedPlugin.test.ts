/**
 * Tests for the force-directed graph plugin.
 *
 * Since the plugin uses d3 DOM manipulation, these tests focus on the
 * canHandle type guard and spec normalization logic.  Full render tests
 * would require jsdom with SVG support (or Playwright integration tests).
 */

import { parseD3Spec } from '../../../utils/d3SpecParser';

// ── Replicate the type guard from forceDirectedPlugin.ts ─────────────

function isForceDirectedSpec(spec: any): boolean {
  if (typeof spec !== 'object' || spec === null) return false;

  const type = spec.type;
  if (type !== 'force-directed' && type !== 'force') return false;

  const nodes = spec.nodes || spec.data?.nodes;
  const links = spec.links || spec.data?.links;

  return Array.isArray(nodes) && Array.isArray(links) && nodes.length > 0;
}

// ── canHandle tests ──────────────────────────────────────────────────

describe('isForceDirectedSpec (canHandle)', () => {
  it('accepts type "force-directed" with data.nodes/data.links', () => {
    const spec = {
      type: 'force-directed',
      data: {
        nodes: [{ id: 'A' }, { id: 'B' }],
        links: [{ source: 'A', target: 'B' }],
      },
    };
    expect(isForceDirectedSpec(spec)).toBe(true);
  });

  it('accepts type "force" with top-level nodes/links', () => {
    const spec = {
      type: 'force',
      nodes: [{ id: 'X' }],
      links: [{ source: 'X', target: 'X' }],
    };
    expect(isForceDirectedSpec(spec)).toBe(true);
  });

  it('rejects spec with wrong type', () => {
    expect(isForceDirectedSpec({
      type: 'network',
      nodes: [{ id: 'A' }],
      links: [],
    })).toBe(false);
  });

  it('rejects spec with empty nodes', () => {
    expect(isForceDirectedSpec({
      type: 'force-directed',
      data: { nodes: [], links: [] },
    })).toBe(false);
  });

  it('rejects spec with missing links', () => {
    expect(isForceDirectedSpec({
      type: 'force-directed',
      data: { nodes: [{ id: 'A' }] },
    })).toBe(false);
  });

  it('rejects null', () => {
    expect(isForceDirectedSpec(null)).toBe(false);
  });

  it('rejects undefined', () => {
    expect(isForceDirectedSpec(undefined)).toBe(false);
  });

  it('rejects string', () => {
    expect(isForceDirectedSpec('force-directed')).toBe(false);
  });

  it('rejects vega-lite spec (no false positive)', () => {
    expect(isForceDirectedSpec({
      $schema: 'https://vega.github.io/schema/vega-lite/v5.json',
      data: { values: [{ x: 1, y: 2 }] },
      mark: 'point',
    })).toBe(false);
  });

  it('rejects bar chart spec (no false positive)', () => {
    expect(isForceDirectedSpec({
      type: 'bar',
      data: [{ label: 'A', value: 10 }],
    })).toBe(false);
  });
});

// ── Integration: parseD3Spec → canHandle pipeline ────────────────────

describe('parseD3Spec → canHandle pipeline', () => {
  it('parses JS expression and matches force-directed plugin', () => {
    const raw = '({ type: "force-directed", data: { nodes: [{ id: "A" }], links: [{ source: "A", target: "A" }] } })';
    const parsed = parseD3Spec(raw);
    expect(parsed).not.toBeNull();
    expect(isForceDirectedSpec(parsed)).toBe(true);
  });

  it('parses JSON and matches force-directed plugin', () => {
    const raw = '{ "type": "force-directed", "data": { "nodes": [{ "id": "A" }], "links": [{ "source": "A", "target": "A" }] } }';
    const parsed = parseD3Spec(raw);
    expect(parsed).not.toBeNull();
    expect(isForceDirectedSpec(parsed)).toBe(true);
  });

  it('unparseable string does not match', () => {
    const parsed = parseD3Spec('this is not a spec');
    expect(parsed).toBeNull();
    expect(isForceDirectedSpec({
      type: 'bar',
      data: [{ label: 'A', value: 10 }],
    })).toBe(false);
  });
});

// ── Spec normalization tests ─────────────────────────────────────────
// The plugin normalizes nodes/links from either nesting level.

describe('spec normalization', () => {
  /** Replicate the normalization logic from the render function. */
  function normalize(spec: any) {
    const nodes = (spec.nodes || spec.data?.nodes || []).map((n: any) => ({ ...n }));
    const links = (spec.links || spec.data?.links || []).map((l: any) => ({ ...l }));
    const style = spec.style || {};
    return { nodes, links, style };
  }

  it('extracts nodes/links from data property', () => {
    const spec = {
      type: 'force-directed',
      data: {
        nodes: [{ id: 'A', group: 1 }, { id: 'B', group: 2 }],
        links: [{ source: 'A', target: 'B', value: 5 }],
      },
      style: { background: '#000' },
    };
    const { nodes, links, style } = normalize(spec);
    expect(nodes).toHaveLength(2);
    expect(links).toHaveLength(1);
    expect(style.background).toBe('#000');
  });

  it('extracts nodes/links from top level', () => {
    const spec = {
      type: 'force',
      nodes: [{ id: 'X' }],
      links: [{ source: 'X', target: 'X' }],
    };
    const { nodes, links } = normalize(spec);
    expect(nodes).toHaveLength(1);
    expect(links).toHaveLength(1);
  });

  it('does not mutate original spec', () => {
    const original = { id: 'A', group: 1 };
    const spec = {
      type: 'force-directed',
      data: { nodes: [original], links: [] },
    };
    const { nodes } = normalize(spec);
    // Modify the copy — original should be untouched
    nodes[0].group = 99;
    expect(original.group).toBe(1);
  });

  it('defaults style to empty object', () => {
    const spec = {
      type: 'force-directed',
      data: { nodes: [{ id: 'A' }], links: [] },
    };
    const { style } = normalize(spec);
    expect(style).toEqual({});
  });
});

// ── Color resolution tests ───────────────────────────────────────────

describe('getNodeColor logic', () => {
  const DEFAULT_GROUP_COLORS = [
    '#4e79a7', '#f28e2b', '#e15759', '#76b7b2', '#59a14f',
    '#edc948', '#b07aa1', '#ff9da7', '#9c755f', '#bab0ac',
  ];

  function getNodeColor(d: any, nodeColors: Record<string, string>): string {
    if (d.color) return d.color;
    const groupKey = String(d.group ?? 0);
    if (nodeColors[groupKey]) return nodeColors[groupKey];
    const groupIndex = (d.group ?? 0) % DEFAULT_GROUP_COLORS.length;
    return DEFAULT_GROUP_COLORS[groupIndex];
  }

  it('uses explicit node.color first', () => {
    expect(getNodeColor({ id: 'A', color: '#ff0000', group: 1 }, { '1': '#00ff00' })).toBe('#ff0000');
  });

  it('uses nodeColors map when no explicit color', () => {
    expect(getNodeColor({ id: 'A', group: 3 }, { '3': '#123456' })).toBe('#123456');
  });

  it('falls back to palette by group index', () => {
    expect(getNodeColor({ id: 'A', group: 0 }, {})).toBe('#4e79a7');
    expect(getNodeColor({ id: 'B', group: 1 }, {})).toBe('#f28e2b');
  });

  it('wraps around palette for high group numbers', () => {
    expect(getNodeColor({ id: 'A', group: 10 }, {})).toBe(DEFAULT_GROUP_COLORS[0]);
  });

  it('defaults groupless nodes to group 0', () => {
    expect(getNodeColor({ id: 'A' }, {})).toBe(DEFAULT_GROUP_COLORS[0]);
  });
});
