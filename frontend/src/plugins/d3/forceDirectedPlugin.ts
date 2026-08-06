/**
 * Force-directed graph plugin for D3 visualization.
 *
 * Handles specs with type "force-directed" or "force".  Uses d3-force
 * simulation for automatic layout of nodes connected by links.
 *
 * Accepted spec shapes:
 *   { type: "force-directed", data: { nodes: [...], links: [...] }, style?: {...} }
 *   { type: "force", nodes: [...], links: [...], style?: {...} }
 */
import { D3RenderPlugin } from '../../types/d3';

interface ForceNode {
  id: string;
  group?: number;
  size?: number;
  color?: string;
  label?: string;
  // d3-force adds these at runtime
  x?: number;
  y?: number;
  fx?: number | null;
  fy?: number | null;
}

interface ForceLink {
  source: string | ForceNode;
  target: string | ForceNode;
  value?: number;
  color?: string;
}

interface ForceStyle {
  background?: string;
  nodeColors?: Record<string, string>;
  nodeColor?: string;
  linkColor?: string;
  linkOpacity?: number;
  labelColor?: string;
  fontSize?: number;
}

/** Default palette for node groups when no explicit colors are given. */
const DEFAULT_GROUP_COLORS = [
  '#4e79a7', '#f28e2b', '#e15759', '#76b7b2', '#59a14f',
  '#edc948', '#b07aa1', '#ff9da7', '#9c755f', '#bab0ac',
];

/** Absolute clamp for a node's rendered radius (px). */
export const FORCE_MAX_NODE_RADIUS = 200;

/**
 * Coerce an arbitrary value to a finite number, or return `undefined` when it
 * cannot be (non-numeric string, NaN, +/-Infinity, null, object). Numeric
 * strings ("400", "3.5") are accepted; "NaN"/"Infinity"/"-Infinity" and
 * anything else become `undefined`.
 */
export function toFiniteOrUndefined(v: any): number | undefined {
  if (typeof v === 'number') return Number.isFinite(v) ? v : undefined;
  if (typeof v === 'string') {
    const trimmed = v.trim();
    if (trimmed === '') return undefined;
    const n = Number(trimmed);
    return Number.isFinite(n) ? n : undefined;
  }
  return undefined;
}

/**
 * Sanitize force-directed nodes so no non-finite value can reach the d3-force
 * simulation. This is the whole-class guard for the "total render hang" family
 * (ledger Issue 25): a node whose fixed-position pin (`fx`/`fy`) is a non-finite
 * value — e.g. the JSON strings "Infinity"/"-Infinity"/"NaN", or a raw
 * Infinity/NaN — poisons d3's quadtree. `d3.forceManyBody` builds a quadtree via
 * `cover()`, which DOUBLES its extent in a `while` loop until it contains every
 * point; an Infinity coordinate can never be covered, so the loop spins forever
 * and the simulation never emits a frame (30s timeout, zero output). A NaN pin
 * silently corrupts every node position to NaN instead.
 *
 * Rules (pure, no DOM):
 *   - `fx`/`fy`: kept only if they coerce to a finite number; otherwise the pin
 *     is DROPPED (property removed) so the node participates as a free node.
 *   - `size`/`radius`: coerced to a finite number, forced non-negative, and
 *     clamped to FORCE_MAX_NODE_RADIUS; non-finite/absent left untouched so the
 *     render path applies its own default.
 *   - all other fields (id, group, label, color, ...) are preserved verbatim.
 *
 * Returns NEW node objects (does not mutate the input).
 */
export function sanitizeForceNodes<T extends Record<string, any>>(nodes: T[]): T[] {
  if (!Array.isArray(nodes)) return [];
  return nodes.map((raw) => {
    const n: Record<string, any> = { ...raw };

    // Fixed-position pins: drop any non-finite pin outright.
    if ('fx' in n) {
      const fx = toFiniteOrUndefined(n.fx);
      if (fx === undefined) delete n.fx;
      else n.fx = fx;
    }
    if ('fy' in n) {
      const fy = toFiniteOrUndefined(n.fy);
      if (fy === undefined) delete n.fy;
      else n.fy = fy;
    }

    // Radius-ish numeric fields: clamp to a sane finite range when present.
    for (const key of ['size', 'radius'] as const) {
      if (key in n) {
        const val = toFiniteOrUndefined(n[key]);
        if (val === undefined) {
          delete n[key]; // let the render path fall back to its default
        } else {
          n[key] = Math.min(FORCE_MAX_NODE_RADIUS, Math.max(0, val));
        }
      }
    }

    return n as T;
  });
}

function isForceDirectedSpec(spec: any): boolean {
  if (typeof spec !== 'object' || spec === null) return false;

  // "d3" is a renderer-FAMILY name, not a concrete diagram type. A spec of
  // { type: "d3", layout: "force-directed"|"force", nodes, links } must map
  // onto this concrete plugin — otherwise no plugin's canHandle matches,
  // findPluginForSpec returns undefined, and the D3Renderer orchestrator
  // retries to a ~35s timeout (silent data loss / hang). See ledger Issue 3.
  const type = spec.type;
  const layout = spec.layout;
  const isForceType = type === 'force-directed' || type === 'force';
  const isD3Family =
    type === 'd3' && (layout === 'force-directed' || layout === 'force');
  if (!isForceType && !isD3Family) return false;

  // Nodes/links can be at top level or nested under data
  const nodes = spec.nodes || spec.data?.nodes;
  const links = spec.links || spec.data?.links;

  return Array.isArray(nodes) && Array.isArray(links) && nodes.length > 0;
}

export const forceDirectedPlugin: D3RenderPlugin = {
  name: 'force-directed',
  priority: 5,
  sizingConfig: {
    sizingStrategy: 'fixed',
    needsDynamicHeight: false,
    needsOverflowVisible: false,
    observeResize: false,
    containerStyles: {
      overflow: 'hidden',
    },
  },

  canHandle: isForceDirectedSpec,

  render: (container: HTMLElement, d3: any, spec: any, isDarkMode: boolean): (() => void) => {
    // Normalize spec: extract nodes/links from either location, then sanitize
    // node geometry. sanitizeForceNodes drops any non-finite fx/fy pin (the JSON
    // strings "Infinity"/"NaN", raw Infinity/NaN) — such a pin poisons d3's
    // forceManyBody quadtree cover() into an infinite doubling loop, hanging the
    // whole render to timeout with zero output. See ledger Issue 25.
    const nodes: ForceNode[] = sanitizeForceNodes(
      (spec.nodes || spec.data?.nodes || []).map((n: any) => ({ ...n }))
    );
    const rawLinks: ForceLink[] = (spec.links || spec.data?.links || []).map((l: any) => ({ ...l }));

    // Sanitize links against the actual node set. d3.forceLink().id() throws an
    // uncaught "node not found" error when a link's source/target id does not
    // resolve to a node — a single dangling reference aborts the entire render
    // (silent hang to timeout). Drop links whose endpoints are not present so
    // the whole class of malformed/dangling-edge specs renders the valid subset
    // instead of failing. See ledger Issue 3.
    const nodeIds = new Set<string>(
      nodes.map((n) => (n && n.id != null ? String(n.id) : '')).filter((id) => id !== '')
    );
    const endpointId = (e: string | ForceNode): string =>
      typeof e === 'object' && e !== null ? String((e as ForceNode).id) : String(e);
    const links: ForceLink[] = rawLinks.filter(
      (l) => nodeIds.has(endpointId(l.source)) && nodeIds.has(endpointId(l.target))
    );

    const style: ForceStyle = spec.style || {};

    const width = spec.width || 700;
    const height = spec.height || 500;
    const bg = style.background || (isDarkMode ? '#1a1a2e' : '#ffffff');
    const linkColor = style.linkColor || (isDarkMode ? '#555555' : '#999999');
    const linkOpacity = style.linkOpacity ?? 0.6;
    const labelColor = style.labelColor || (isDarkMode ? '#cccccc' : '#333333');
    const fontSize = style.fontSize || 10;
    const nodeColors: Record<string, string> = style.nodeColors || {};

    /** Resolve a node's fill color from style.nodeColors, node.color, or palette. */
    const getNodeColor = (d: ForceNode): string => {
      if (d.color) return d.color;
      const groupKey = String(d.group ?? 0);
      if (nodeColors[groupKey]) return nodeColors[groupKey];
      const groupIndex = (d.group ?? 0) % DEFAULT_GROUP_COLORS.length;
      return DEFAULT_GROUP_COLORS[groupIndex];
    };

    // Clear container
    d3.select(container).selectAll('*').remove();

    const svg = d3.select(container)
      .append('svg')
      .attr('width', width)
      .attr('height', height)
      .attr('viewBox', [0, 0, width, height])
      .style('background', bg)
      .style('border-radius', '6px');

    // Zoom group — all rendered content goes inside
    const g = svg.append('g');

    // Zoom / pan behaviour
    const zoom = d3.zoom()
      .scaleExtent([0.2, 5])
      .on('zoom', (event: any) => g.attr('transform', event.transform));
    svg.call(zoom);

    // Arrow marker for directed edges
    svg.append('defs').append('marker')
      .attr('id', 'fd-arrow')
      .attr('viewBox', '0 -5 10 10')
      .attr('refX', 20)
      .attr('refY', 0)
      .attr('markerWidth', 6)
      .attr('markerHeight', 6)
      .attr('orient', 'auto')
      .append('path')
      .attr('d', 'M0,-5L10,0L0,5')
      .attr('fill', linkColor);

    // Force simulation
    const simulation = d3.forceSimulation(nodes)
      .force('link', d3.forceLink(links).id((d: ForceNode) => d.id).distance(80))
      .force('charge', d3.forceManyBody().strength(-200))
      .force('center', d3.forceCenter(width / 2, height / 2))
      .force('collision', d3.forceCollide().radius((d: ForceNode) => (d.size || 8) + 4));

    // Links
    const link = g.append('g')
      .attr('stroke', linkColor)
      .attr('stroke-opacity', linkOpacity)
      .selectAll('line')
      .data(links)
      .join('line')
      .attr('stroke-width', (d: any) => Math.sqrt(d.value || 1))
      .attr('marker-end', 'url(#fd-arrow)');

    // Node groups (circle + label)
    const node = g.append('g')
      .selectAll('g')
      .data(nodes)
      .join('g')
      .call(drag(simulation, d3));

    // Circles
    node.append('circle')
      .attr('r', (d: ForceNode) => d.size || 8)
      .attr('fill', getNodeColor)
      .attr('stroke', (d: ForceNode) => {
        // Slightly lighter/darker stroke for definition
        const fill = getNodeColor(d);
        return d3.color(fill)?.brighter(0.5)?.toString() || '#ffffff44';
      })
      .attr('stroke-width', 1.5);

    // Labels
    node.append('text')
      .text((d: ForceNode) => d.label || d.id)
      .attr('x', (d: ForceNode) => (d.size || 8) + 4)
      .attr('y', 3)
      .attr('fill', labelColor)
      .attr('font-size', `${fontSize}px`)
      .attr('font-family', 'system-ui, -apple-system, sans-serif')
      .attr('pointer-events', 'none');

    // Tooltip on hover
    node.append('title')
      .text((d: ForceNode) => d.label || d.id);

    // Tick handler — update positions every simulation step
    simulation.on('tick', () => {
      link
        .attr('x1', (d: any) => d.source.x)
        .attr('y1', (d: any) => d.source.y)
        .attr('x2', (d: any) => d.target.x)
        .attr('y2', (d: any) => d.target.y);

      node.attr('transform', (d: any) => `translate(${d.x},${d.y})`);
    });

    // Warm up the simulation so the initial render isn't a messy blob
    // (300 ticks ≈ the default alphaMin threshold)
    simulation.alpha(1).restart();
    for (let i = 0; i < 300; i++) simulation.tick();
    // Trigger a final render with settled positions
    simulation.alpha(0.01).restart();

    // Cleanup function — stop simulation when component unmounts
    return () => {
      simulation.stop();
    };
  },
};

/**
 * D3 drag behaviour for force-directed nodes.
 *
 * On drag start the node is pinned (fx/fy set) so it doesn't float
 * away.  On drag end it's un-pinned so the simulation can settle.
 */
function drag(simulation: any, d3: any) {
  function dragstarted(event: any) {
    if (!event.active) simulation.alphaTarget(0.3).restart();
    event.subject.fx = event.subject.x;
    event.subject.fy = event.subject.y;
  }

  function dragged(event: any) {
    event.subject.fx = event.x;
    event.subject.fy = event.y;
  }

  function dragended(event: any) {
    if (!event.active) simulation.alphaTarget(0);
    event.subject.fx = null;
    event.subject.fy = null;
  }

  return d3.drag()
    .on('start', dragstarted)
    .on('drag', dragged)
    .on('end', dragended);
}
