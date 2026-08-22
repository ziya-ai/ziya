/**
 * Chord diagram plugin for D3 visualization.
 *
 * Handles specs with type "chord" or "chord-directed".  Renders a
 * circular chord layout showing flows between groups via d3.chord() /
 * d3.chordDirected() + d3.ribbon().
 *
 * Accepted spec shapes:
 *
 *   Links form (LLM-friendly, mirrors force-directed):
 *     {
 *       type: "chord",
 *       nodes: [{ id: "A", label?: "A", color?: "#abc" }, ...],
 *       links: [{ source: "A", target: "B", value: 10 }, ...],
 *       directed?: true,         // default true (uses chordDirected)
 *       style?: {...}
 *     }
 *
 *   Matrix form (direct d3 input):
 *     {
 *       type: "chord",
 *       matrix: [[0, 5, 2], [3, 0, 1], [4, 2, 0]],
 *       names?: ["A", "B", "C"],
 *       colors?: ["#abc", ...],
 *       directed?: true,
 *       style?: {...}
 *     }
 */
import { D3RenderPlugin } from '../../types/d3';

interface ChordNode {
  id: string;
  label?: string;
  color?: string;
}

interface ChordLink {
  source: string;
  target: string;
  value?: number;
}

interface ChordStyle {
  background?: string;
  ribbonOpacity?: number;
  hoverOpacity?: number;
  fadeOpacity?: number;
  labelColor?: string;
  fontSize?: number;
  arcStroke?: string;
}

/** Default categorical palette when no explicit colors are given. */
const DEFAULT_PALETTE = [
  '#4e79a7', '#f28e2b', '#e15759', '#76b7b2', '#59a14f',
  '#edc948', '#b07aa1', '#ff9da7', '#9c755f', '#bab0ac',
];

/**
 * Recover a structured chord spec from a `definition`-as-JSON-string wrapper.
 *
 * `render_diagram` (app/mcp/tools/diagram_render.py) always ships the real
 * chord JSON as a STRING under `spec.definition`, with only `type` on the
 * outer wrapper. The plugin's `isChordSpec`/`render` read `matrix`/`nodes`/
 * `links` off the top-level object, so a wrapped spec never matches any
 * plugin -> "No plugin found for spec: chord" -> retry-to-timeout, zero
 * output (Issue 23; same contract-mismatch class as joint#2 / network#11 /
 * music#17).
 *
 * This lifts the structured fields (matrix | nodes+links, plus optional
 * names/colors/directed/style/width/height) from the parsed definition onto
 * a shallow copy so downstream code sees the arrays it expects. If the spec
 * is already structured, or `definition` is absent / non-JSON / carries no
 * chord content, the spec is returned unchanged (guarded — never hijacks a
 * non-chord spec).
 *
 * Exported for regression testing.
 */
/**
 * Resolve a conflicting DUAL-SHAPE chord spec.
 *
 * A chord spec may legitimately arrive in EITHER the matrix form
 * (`matrix: number[][]`, direct d3 input) OR the links form
 * (`nodes` + `links`, the richer LLM-friendly NAMED form). Some inputs
 * (Issue 50) supply BOTH at once. The render path and `isChordSpec` both
 * test `Array.isArray(spec.matrix)` FIRST, so the matrix wins
 * unconditionally and the entire nodes/links structure — potentially dozens
 * of named nodes and links plus their colors/groups — is SILENTLY DROPPED,
 * leaving a diagram of bare numeric-index arcs (major silent data loss).
 *
 * The links form carries strictly MORE information than a bare numeric
 * matrix (named nodes, per-node colors, richer link structure), so when both
 * are present the links form is the safe choice: preferring it can at worst
 * re-derive an equivalent matrix, whereas preferring the matrix throws away
 * the named structure with no recovery. This helper DROPS the `matrix` field
 * when a NON-EMPTY links form (nodes.length > 0 AND links is an array)
 * co-exists with it, so downstream shape-selection consistently uses the
 * higher-information form.
 *
 * Guards (spec returned byte-identical, ref-equal): matrix-only specs,
 * links-only specs, specs whose nodes array is empty/absent, and non-object
 * specs are all left untouched — this resolves ONLY the genuine
 * both-present conflict, it is not a catch-all rewrite.
 *
 * Exported for regression testing.
 */
/**
 * Normalize `nodes` / `links` supplied as a KEYED OBJECT MAP into the array
 * form the links path expects.
 *
 * The links form is documented as `nodes: [{id}]` / `links: [{source,target}]`,
 * but LLMs (Issue 50) routinely key `nodes` by id:
 *   `nodes: { "Alpha": { color }, "Beta": { color }, ... }`
 * Every downstream consumer (`isChordSpec`, `buildMatrix`, the render path)
 * calls `Array.isArray(nodes)`; for an object map that is FALSE, so:
 *   - if a `matrix` is ALSO present, the matrix short-circuits and the named
 *     graph is silently dropped (Issue 50 headline), and
 *   - if no matrix is present, `isChordSpec` returns false -> "No compatible
 *     plugin found" -> silent retry-to-timeout, zero output.
 * Either way the entire named graph is lost.
 *
 * This converts an object-map `nodes` to `[{ id: <key>, ...value }]` (the map
 * KEY is the node identity that links reference, so it is forced as `id`),
 * and an object-map `links` to its `Object.values`. Arrays are returned
 * REF-EQUAL (untouched) so array-form specs are behavior-identical; this is a
 * shape-normalization gap fix, not a catch-all. Also normalizes the
 * `data.nodes` / `data.links` nesting.
 *
 * Exported for regression testing.
 */
export function normalizeChordCollections(spec: any): any {
  if (typeof spec !== 'object' || spec === null) return spec;

  const isPlainMap = (v: any): boolean =>
    v !== null && typeof v === 'object' && !Array.isArray(v);

  const nodesToArray = (nodes: any): any[] =>
    Object.keys(nodes).map((key) => {
      const val = nodes[key];
      return isPlainMap(val) ? { ...val, id: key } : { id: key };
    });

  const linksToArray = (links: any): any[] => Object.values(links);

  let changed = false;
  const next: any = { ...spec };

  if (isPlainMap(spec.nodes)) { next.nodes = nodesToArray(spec.nodes); changed = true; }
  if (isPlainMap(spec.links)) { next.links = linksToArray(spec.links); changed = true; }

  if (isPlainMap(spec.data)) {
    const d = spec.data;
    if (isPlainMap(d.nodes) || isPlainMap(d.links)) {
      const nd: any = { ...d };
      if (isPlainMap(d.nodes)) nd.nodes = nodesToArray(d.nodes);
      if (isPlainMap(d.links)) nd.links = linksToArray(d.links);
      next.data = nd;
      changed = true;
    }
  }

  return changed ? next : spec;
}

export function resolveChordShapeConflict(spec: any): any {
  if (typeof spec !== 'object' || spec === null) return spec;
  const hasMatrix = Array.isArray(spec.matrix) && spec.matrix.length > 0
    && Array.isArray(spec.matrix[0]);
  if (!hasMatrix) return spec;
  const nodes = spec.nodes || spec.data?.nodes;
  const links = spec.links || spec.data?.links;
  const hasLinksForm = Array.isArray(nodes) && nodes.length > 0
    && Array.isArray(links);
  if (!hasLinksForm) return spec;
  // Both present -> prefer the richer links form; drop the conflicting matrix
  // so the render path takes the nodes/links branch instead of short-circuiting
  // on the matrix.
  const { matrix, ...rest } = spec;
  return rest;
}

export function resolveChordSpec(spec: any): any {
  if (typeof spec !== 'object' || spec === null) return spec;

  // Normalize object-map `nodes`/`links` -> array form first (Issue 50), then
  // resolve a conflicting dual-shape (both `matrix` AND `nodes`+`links`) so
  // downstream shape-selection uses the higher-information links form rather
  // than silently dropping it. Order matters: the conflict resolver requires
  // an ARRAY links form, which the normalization guarantees.
  spec = normalizeChordCollections(spec);
  spec = resolveChordShapeConflict(spec);

  // Already structured (matrix form OR links form)?
  const hasMatrix = Array.isArray(spec.matrix) && spec.matrix.length > 0
    && Array.isArray(spec.matrix[0]);
  const hasNodes = Array.isArray(spec.nodes) || Array.isArray(spec.data?.nodes);
  const hasLinks = Array.isArray(spec.links) || Array.isArray(spec.data?.links);
  if (hasMatrix || (hasNodes && hasLinks)) return spec;

  // Only attempt recovery from a JSON-object `definition` string.
  if (typeof spec.definition !== 'string' || spec.definition.trim() === '') return spec;
  if (spec.definition.trimStart()[0] !== '{') return spec;

  let parsed: any;
  try {
    parsed = JSON.parse(spec.definition);
  } catch (_e) {
    return spec;
  }
  if (typeof parsed !== 'object' || parsed === null) return spec;

  // Same object-map normalization + dual-shape conflict resolution for a
  // wrapped/parsed definition.
  parsed = normalizeChordCollections(parsed);
  parsed = resolveChordShapeConflict(parsed);

  const pMatrix = Array.isArray(parsed.matrix) && parsed.matrix.length > 0
    && Array.isArray(parsed.matrix[0]);
  const pNodes = Array.isArray(parsed.nodes) || Array.isArray(parsed.data?.nodes);
  const pLinks = Array.isArray(parsed.links) || Array.isArray(parsed.data?.links);
  // Requires genuine chord content: a matrix, or nodes (+links, defaulted below).
  if (!pMatrix && !pNodes) return spec;

  const resolved: any = { ...spec };
  if (pMatrix) {
    resolved.matrix = parsed.matrix;
    if (parsed.names !== undefined) resolved.names = parsed.names;
    if (parsed.colors !== undefined) resolved.colors = parsed.colors;
  } else {
    resolved.nodes = parsed.nodes || parsed.data?.nodes;
    resolved.links = parsed.links || parsed.data?.links
      || (Array.isArray(parsed.edges) ? parsed.edges : []);
  }
  if (parsed.directed !== undefined) resolved.directed = parsed.directed;
  if (parsed.width !== undefined) resolved.width = parsed.width;
  if (parsed.height !== undefined) resolved.height = parsed.height;
  if (parsed.style !== undefined) resolved.style = parsed.style;
  return resolved;
}

/**
 * Coerce an arbitrary link `value` (or matrix cell) to a finite, non-negative
 * number suitable for d3.chord().
 *
 * d3.chord() computes arc angles from a running sum of the matrix; a single
 * NaN (from a string like `"not-a-number"` or a value d3 can't add), Infinity,
 * or a negative flow poisons that sum and every downstream arc/ribbon path
 * becomes `MNaN,NaN` -> the entire diagram silently vanishes (Issue 10 matrix
 * form). This maps NaN/Infinity/-Infinity/negative -> 0 and keeps every finite
 * non-negative magnitude (including huge 1e15 and tiny 1e-300, which d3 handles
 * proportionally). `null`/`undefined` fall back to the caller's default.
 *
 * Exported for regression testing.
 */
export function coerceFlowValue(raw: any, fallback: number = 1): number {
  const v = Number(raw ?? fallback);
  return Number.isFinite(v) && v >= 0 ? v : 0;
}

function isChordSpec(spec: any): boolean {
  const resolved = resolveChordSpec(spec);
  if (typeof resolved !== 'object' || resolved === null) return false;
  const type = resolved.type;
  if (type !== 'chord' && type !== 'chord-directed') return false;

  // Matrix form
  if (Array.isArray(resolved.matrix) && resolved.matrix.length > 0
      && Array.isArray(resolved.matrix[0])) {
    return true;
  }

  // Links form (also accepts data.nodes / data.links)
  const nodes = resolved.nodes || resolved.data?.nodes;
  const links = resolved.links || resolved.data?.links;
  return Array.isArray(nodes) && Array.isArray(links) && nodes.length > 0;
}

/**
 * Normalize a links-form `nodes` array to `ChordNode` objects.
 *
 * The links form is documented to accept `nodes: [{ id, label?, color? }]`,
 * but LLMs (and this stress corpus, Issue 38) routinely pass `nodes` as a
 * flat array of plain STRINGS: `["Alpha", "Beta", "Gamma"]`. Downstream,
 * `buildMatrix` indexes by `node.id` and the render path maps `node.label`/
 * `node.color`; for a string node every one of those is `undefined`, so the
 * index map collapses to a single `undefined -> i` entry, EVERY link's
 * `idx.get(link.source)` returns undefined, every link is skipped, the matrix
 * is all-zero, and d3.chord() emits a blank canvas — total, silent data loss.
 *
 * This coerces each entry to a `{ id }` object:
 *   - a string       -> { id: string }
 *   - a number/bool   -> { id: String(value) }  (defensive; JSON shorthand)
 *   - an object with a usable id/label/name/key -> { id, label?, color? }
 *
 * Already-object nodes with an `id` are preserved (spread) so object-form
 * specs are behavior-identical — this is a normalization gap fix, not a
 * catch-all rewrite. Entries with no derivable id (null/undefined/empty
 * object) fall back to their positional index as the id so they still occupy
 * an arc slot rather than corrupting the index map.
 *
 * Exported for regression testing.
 */
export function normalizeChordNodes(nodes: any[]): ChordNode[] {
  if (!Array.isArray(nodes)) return [];
  return nodes.map((node, i): ChordNode => {
    if (typeof node === 'string') {
      return { id: node };
    }
    if (typeof node === 'number' || typeof node === 'boolean') {
      return { id: String(node) };
    }
    if (node !== null && typeof node === 'object') {
      // Prefer an explicit id; fall back to common aliases, then index.
      const rawId = node.id ?? node.name ?? node.key ?? node.label;
      const id = rawId === undefined || rawId === null || rawId === ''
        ? String(i)
        : String(rawId);
      const out: ChordNode = { ...node, id };
      if (node.label !== undefined) out.label = String(node.label);
      if (node.color !== undefined) out.color = node.color;
      return out;
    }
    // null / undefined / other -> positional placeholder so the arc slot
    // is preserved and the index map stays 1:1 with node order.
    return { id: String(i) };
  });
}

/**
 * Build an N×N flow matrix from nodes + links.  Node order is preserved
 * (it determines arc placement around the circle).  Missing source/target
 * IDs are silently skipped — the diagram renders what it can.
 */
function buildMatrix(nodes: ChordNode[], links: ChordLink[]): number[][] {
  const n = nodes.length;
  const idx = new Map<string, number>();
  nodes.forEach((node, i) => idx.set(node.id, i));
  const matrix: number[][] = Array.from({ length: n }, () => new Array(n).fill(0));
  for (const link of links) {
    const s = idx.get(link.source);
    const t = idx.get(link.target);
    if (s === undefined || t === undefined) continue;
    matrix[s][t] += coerceFlowValue(link.value, 1);
  }
  return matrix;
}

/** Coerce every cell of a matrix-form input to a finite non-negative number. */
function sanitizeMatrix(matrix: any[][]): number[][] {
  return matrix.map(row =>
    Array.isArray(row) ? row.map(cell => coerceFlowValue(cell, 0)) : []);
}

export const chordPlugin: D3RenderPlugin = {
  name: 'chord-renderer',
  priority: 5,
  sizingConfig: {
    sizingStrategy: 'fixed',
    needsDynamicHeight: false,
    needsOverflowVisible: false,
    observeResize: false,
    containerStyles: { overflow: 'hidden' },
  },

  canHandle: isChordSpec,

  render: (container: HTMLElement, d3: any, rawSpec: any, isDarkMode: boolean): (() => void) => {
    // Recover a structured spec from a definition-as-JSON-string wrapper.
    const spec = resolveChordSpec(rawSpec);
    const style: ChordStyle = spec.style || {};
    const width = spec.width || 600;
    const height = spec.height || 600;
    const bg = style.background || (isDarkMode ? '#1a1a2e' : '#ffffff');
    const labelColor = style.labelColor || (isDarkMode ? '#e0e0e0' : '#333333');
    const fontSize = style.fontSize || 11;
    const ribbonOpacity = style.ribbonOpacity ?? 0.7;
    const hoverOpacity = style.hoverOpacity ?? 0.95;
    const fadeOpacity = style.fadeOpacity ?? 0.1;
    const arcStroke = style.arcStroke || (isDarkMode ? '#0d0d1a' : '#ffffff');

    // Resolve names, colors, and the flow matrix from either input shape.
    let matrix: number[][];
    let names: string[];
    let colors: string[];

    if (Array.isArray(spec.matrix)) {
      matrix = sanitizeMatrix(spec.matrix);
      const n = matrix.length;
      names = Array.isArray(spec.names) && spec.names.length === n
        ? spec.names.map((s: any) => String(s))
        : Array.from({ length: n }, (_, i) => String(i));
      colors = Array.isArray(spec.colors) && spec.colors.length === n
        ? spec.colors
        : names.map((_, i) => DEFAULT_PALETTE[i % DEFAULT_PALETTE.length]);
    } else {
      const nodes: ChordNode[] = normalizeChordNodes(spec.nodes || spec.data?.nodes || []);
      const links: ChordLink[] = (spec.links || spec.data?.links || []).map((l: any) => ({ ...l }));
      matrix = buildMatrix(nodes, links);
      names = nodes.map(node => node.label || node.id);
      colors = nodes.map((node, i) =>
        node.color || DEFAULT_PALETTE[i % DEFAULT_PALETTE.length]);
    }

    // Default to directed (matches the user's chordDirected expectation
    // and is the more common case for flow diagrams).  Pass directed:false
    // for symmetric chord layouts.
    const directed = spec.directed !== false;
    const chordLayout = directed ? d3.chordDirected() : d3.chord();
    chordLayout.padAngle(0.05).sortSubgroups(d3.descending);
    if (directed) chordLayout.sortChords(d3.descending);

    const chords = chordLayout(matrix);

    // Sizing — leave room around the circle for labels.
    const outerRadius = Math.min(width, height) * 0.5 - 60;
    const innerRadius = outerRadius - 18;

    // Clear container
    d3.select(container).selectAll('*').remove();

    const svg = d3.select(container)
      .append('svg')
      .attr('width', width)
      .attr('height', height)
      .attr('viewBox', [-width / 2, -height / 2, width, height])
      .style('background', bg)
      .style('border-radius', '6px');

    const arc = d3.arc().innerRadius(innerRadius).outerRadius(outerRadius);
    const ribbon = directed
      ? d3.ribbonArrow().radius(innerRadius - 1).padAngle(1 / innerRadius)
      : d3.ribbon().radius(innerRadius - 1);

    // Group arcs (the outer ring segments)
    const group = svg.append('g')
      .selectAll('g')
      .data(chords.groups)
      .join('g');

    group.append('path')
      .attr('fill', (d: any) => colors[d.index])
      .attr('stroke', arcStroke)
      .attr('stroke-width', 1)
      .attr('d', arc as any);

    // Group labels — placed just outside the arc, rotated to be readable.
    group.append('text')
      .each((d: any) => { d.angle = (d.startAngle + d.endAngle) / 2; })
      .attr('dy', '0.35em')
      .attr('transform', (d: any) =>
        `rotate(${(d.angle * 180 / Math.PI - 90)}) `
        + `translate(${outerRadius + 8}) `
        + `${d.angle > Math.PI ? 'rotate(180)' : ''}`)
      .attr('text-anchor', (d: any) => d.angle > Math.PI ? 'end' : null)
      .attr('fill', labelColor)
      .attr('font-size', `${fontSize}px`)
      .attr('font-family', 'system-ui, -apple-system, sans-serif')
      .text((d: any) => names[d.index]);

    // Tooltip on the arc itself (totals in/out).
    group.append('title').text((d: any) => {
      const outgoing = matrix[d.index].reduce((a, b) => a + b, 0);
      const incoming = matrix.reduce((sum, row) => sum + row[d.index], 0);
      return `${names[d.index]}\nout: ${outgoing}\nin: ${incoming}`;
    });

    // Ribbons (the chords themselves)
    const ribbons = svg.append('g')
      .attr('fill-opacity', ribbonOpacity)
      .selectAll('path')
      .data(chords)
      .join('path')
      .attr('d', ribbon as any)
      .attr('fill', (d: any) => colors[d.target.index])
      .attr('stroke', arcStroke)
      .attr('stroke-width', 0.5);

    ribbons.append('title').text((d: any) =>
      `${names[d.source.index]} → ${names[d.target.index]}: ${d.source.value}`
      + (d.source.value !== d.target.value
        ? `\n${names[d.target.index]} → ${names[d.source.index]}: ${d.target.value}`
        : ''));

    // Hover behaviour — fade ribbons not connected to the hovered group.
    group.on('mouseover', function (this: any, _evt: any, hovered: any) {
      ribbons.attr('fill-opacity', (d: any) =>
        d.source.index === hovered.index || d.target.index === hovered.index
          ? hoverOpacity
          : fadeOpacity);
    }).on('mouseout', function () {
      ribbons.attr('fill-opacity', ribbonOpacity);
    });

    // No simulation to clean up — return a no-op so the host's
    // cleanup contract is honoured.
    return () => { /* nothing to tear down */ };
  },
};
