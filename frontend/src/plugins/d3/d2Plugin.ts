import { D3RenderPlugin } from '../../types/d3';
import { isDiagramDefinitionComplete } from '../../utils/diagramUtils';
import { extractDefinitionFromYAML } from '../../utils/diagramUtils';
import { escapeHtml } from '../../utils/htmlSanitize';

export interface D2Spec {
    type: 'd2';
    isStreaming?: boolean;
    forceRender?: boolean;
    definition: string;
    layout?: 'elk' | 'dagre' | 'tala';
}

const isD2Spec = (spec: any): spec is D2Spec => {
    return (
        typeof spec === 'object' &&
        spec !== null &&
        spec.type === 'd2' &&
        typeof spec.definition === 'string' &&
        spec.definition.trim().length > 0
    );
};

export interface D2NodeBox { x: number; y: number; width: number; height: number; }

// Trim an edge so it runs between the BORDERS of the source and target boxes
// (not centre-to-centre) and leaves a small gap at each end, so the arrowhead
// is painted OUTSIDE the target rectangle and stays visible regardless of
// node/edge paint order. Previously edges ran centre-to-centre with the
// arrowhead (marker refX inside the box) hidden under the target node, making
// every directed graph look undirected.
export function trimEdgeToNodes(
    source: D2NodeBox | undefined,
    target: D2NodeBox | undefined,
    gap: number = 6
): { x1: number; y1: number; x2: number; y2: number } {
    const sx = source ? source.x + source.width / 2 : 0;
    const sy = source ? source.y + source.height / 2 : 0;
    const tx = target ? target.x + target.width / 2 : 0;
    const ty = target ? target.y + target.height / 2 : 0;

    let x1 = sx, y1 = sy, x2 = tx, y2 = ty;

    const dx = tx - sx;
    const dy = ty - sy;
    if (dx === 0 && dy === 0) {
        return { x1, y1, x2, y2 };
    }

    // Point where the segment leaves the SOURCE box (+gap outward).
    if (source) {
        const hw = source.width / 2 + gap;
        const hh = source.height / 2 + gap;
        const scale = 1 / Math.max(Math.abs(dx) / hw, Math.abs(dy) / hh);
        if (isFinite(scale)) {
            x1 = sx + dx * scale;
            y1 = sy + dy * scale;
        }
    }

    // Point where the segment enters the TARGET box, pushed out by `gap` so
    // the arrowhead sits clear of the rectangle.
    if (target) {
        const hw = target.width / 2 + gap;
        const hh = target.height / 2 + gap;
        const scale = 1 / Math.max(Math.abs(dx) / hw, Math.abs(dy) / hh);
        if (isFinite(scale)) {
            x2 = tx - dx * scale;
            y2 = ty - dy * scale;
        }
    }

    return { x1, y1, x2, y2 };
}

// Compute the bounding rectangle of a container from the laid-out positions of
// its MEMBER nodes, walking the container/parent chain so arbitrarily deep
// nesting is covered. Returns null when the container encloses no laid-out
// node (previously Math.min/max over an empty children[] yielded
// Infinity/-Infinity, emitting hard "attribute x: Expected length, Infinity"
// SVG errors and silently dropping the container).
// Per-nesting-level inset (viewBox px) added to a container's padding for each
// level of containers nested INSIDE it, so an outer container draws a strictly
// larger rect than the containers it wraps. Without this, deeply-nested
// containers whose only members are the same leaf node all resolve to the
// SAME min/max bounds and paint as N mutually-indistinguishable overlapping
// rects — depth 0 and depth 11 looked identical (D-089). The step is small and
// the level count is capped so the outermost rect stays on-canvas: with the
// fallback origin at 100, max inset 12*6=72 keeps minX-(20+72) = 8 >= 0.
export const D2_NEST_STEP = 6;
export const D2_NEST_MAX_LEVELS = 12;

// How many levels of containers are nested INSIDE `containerId` (0 for a
// container that directly holds only leaf nodes / nothing nested). Used to grow
// an outer container's rect so nesting is visible (D-089). Cycle-guarded.
export function d2ContainerDescendantDepth(
    containerId: string,
    containers: Array<{ id: string; parent?: string | null }>
): number {
    const childrenOf = new Map<string, string[]>();
    for (const c of containers) {
        const p = (c && c.parent) ?? null;
        if (p) {
            if (!childrenOf.has(p)) childrenOf.set(p, []);
            childrenOf.get(p)!.push(c.id);
        }
    }
    const memo = new Map<string, number>();
    const onStack = new Set<string>();
    const depth = (id: string): number => {
        if (memo.has(id)) return memo.get(id)!;
        if (onStack.has(id)) return 0; // cycle guard
        onStack.add(id);
        let d = 0;
        for (const k of childrenOf.get(id) || []) d = Math.max(d, 1 + depth(k));
        onStack.delete(id);
        memo.set(id, d);
        return d;
    };
    return depth(containerId);
}

export function d2ContainerBounds(
    container: { id: string; children?: string[] },
    nodes: any[],
    containers: Array<{ id: string; parent?: string | null }>,
    pad: number = 20
): { x: number; y: number; width: number; height: number } | null {
    const byId = new Map(containers.map(c => [c.id, c]));
    const members = nodes.filter(n => {
        let cid: string | null = (n && n.container) ?? null;
        let guard = 0;
        while (cid && guard++ < 64) {
            if (cid === container.id) return true;
            cid = byId.get(cid)?.parent ?? null;
        }
        return false;
    });
    if (members.length === 0) {
        return null;
    }
    // Grow the pad by one step per level of containers nested inside this one,
    // so a parent's dashed rect strictly encloses its children's rects instead
    // of coinciding with them (D-089). Flat containers (depth 0) keep pad=20 —
    // byte-identical to before.
    const inset = Math.min(d2ContainerDescendantDepth(container.id, containers), D2_NEST_MAX_LEVELS) * D2_NEST_STEP;
    const effPad = pad + inset;
    const minX = Math.min(...members.map(n => n.x)) - effPad;
    const minY = Math.min(...members.map(n => n.y)) - effPad;
    const maxX = Math.max(...members.map(n => n.x + n.width)) + effPad;
    const maxY = Math.max(...members.map(n => n.y + n.height)) + effPad;
    return { x: minX, y: minY, width: maxX - minX, height: maxY - minY };
}

// Build the ELK label objects for a node. The label must NOT carry a
// layoutOptions {'elk.labelManager':'none'} entry: ELK's labelManager property
// expects a manager INSTANCE, and the string 'none' makes elk.layout() throw
// "Couldn't create new instance of property org.eclipse.elk.labelManager" on
// EVERY layout, forcing the plugin into its square-grid fallback. A bare
// {text} label lets the real layered layout run.
export function buildElkNodeLabels(node: any): Array<{ text: string }> {
    return node && node.label ? [{ text: node.label }] : [];
}

// ---------------------------------------------------------------------------
// Shared node sizing / label wrapping (G-13: D-086 / D-087 / D-088).
//
// The label font-size is a fixed pixel value expressed in *viewBox* units, so
// the SVG must be painted at its natural pixel size (see d2CanvasSize) rather
// than width:100%, otherwise the browser downscales the whole viewBox and the
// 12px text shrinks to a sub-pixel smear on large graphs (D-086). Long labels
// wrap into multiple tspan lines — calculateNodeHeight already reserved the
// room but the old renderer emitted a single flat <text> that ran straight out
// of the box (D-087). The fallback grid pitch is derived from the real max node
// width so a box can no longer overlap and be truncated by its neighbour
// (D-088, old nodeSpacing=150 < node width up to 200).
// ---------------------------------------------------------------------------
// Page/surface backgrounds the d2 SVG is painted onto, used to reason about
// node-fill / edge / text contrast in both themes.
export const D2_DARK_BG = '#1f1f1f';
export const D2_LIGHT_BG = '#ffffff';

export interface D2ThemeColors { node: string; nodeStroke: string; edge: string; text: string; }

// Theme-resolved palette. Light and dark are INDEPENDENT constants (resolved
// from the theme the renderer was given), so a dark-theme tweak can never
// regress light output.
//   D-094: dark node fill darkened #4361ee -> #303f9f (Indigo 700). White label
//          text on the fill rises 5.02:1 -> 8.98:1, so thin white strokes stop
//          smearing into a saturated fill at small effective sizes. The node
//          boundary is carried by the bright cyan stroke #4cc9f0 (8.57:1 vs the
//          #1f1f1f page), not the fill. Light fill #e3f2fd unchanged (black text
//          18.39:1).
//   D-095: dark edge desaturated magenta #f72585 -> neutral grey-blue #9aa4b2.
//          The saturated magenta was the brightest thing on the canvas and
//          out-shouted the nodes (figure/ground inversion); a neutral grey reads
//          as recessive structure, mirroring the light theme's grey #666666. On
//          the #1f1f1f page 4.36:1 -> 6.54:1 (still visible) and, crossing the
//          #303f9f node fill, 1.33:1 -> 3.56:1 (was invisible on contact). Light
//          edge #666666 unchanged.
export function d2ThemeColors(isDarkMode: boolean): D2ThemeColors {
    return {
        node: isDarkMode ? '#303f9f' : '#e3f2fd',
        nodeStroke: isDarkMode ? '#4cc9f0' : '#1976d2',
        edge: isDarkMode ? '#9aa4b2' : '#666666',
        text: isDarkMode ? '#ffffff' : '#000000',
    };
}

export const D2_FONT_SIZE = 12;
export const D2_CHAR_WIDTH = 8;
export const D2_MIN_NODE_WIDTH = 80;
export const D2_MAX_NODE_WIDTH = 240;
export const D2_LINE_HEIGHT = 16;

export function d2NodeWidth(text: string): number {
    const len = String(text ?? '').length;
    return Math.max(D2_MIN_NODE_WIDTH, Math.min(len * D2_CHAR_WIDTH + 30, D2_MAX_NODE_WIDTH));
}

// Greedily wrap `text` into lines that fit `maxWidthPx`, hard-breaking any
// single token longer than the line so a 600-char unbroken label cannot run
// off the canvas. Always returns at least one (possibly empty) line.
export function wrapLabel(text: string, maxWidthPx: number, charWidth: number = D2_CHAR_WIDTH): string[] {
    const maxChars = Math.max(4, Math.floor((maxWidthPx - 16) / charWidth));
    const words = String(text ?? '').split(/\s+/).filter(Boolean);
    if (words.length === 0) return [''];
    const lines: string[] = [];
    let cur = '';
    for (const w of words) {
        if (w.length > maxChars) {
            if (cur) { lines.push(cur); cur = ''; }
            let rest = w;
            while (rest.length > maxChars) { lines.push(rest.slice(0, maxChars)); rest = rest.slice(maxChars); }
            cur = rest;
            continue;
        }
        if (!cur) cur = w;
        else if (cur.length + 1 + w.length <= maxChars) cur += ' ' + w;
        else { lines.push(cur); cur = w; }
    }
    if (cur) lines.push(cur);
    return lines;
}

export function d2NodeHeight(text: string): number {
    const lines = wrapLabel(text, d2NodeWidth(text)).length;
    return Math.max(40, 12 + lines * D2_LINE_HEIGHT);
}

// sql_table layout constants (D-082): a header band plus one row per column.
export const D2_SQL_HEADER_HEIGHT = 26;
export const D2_SQL_ROW_HEIGHT = 20;

// The `field: type` text rows of a `shape: sql_table` node. Columns are stashed
// on node.attrs by the node-body parser (D-097); here they become the drawn
// rows. Empty for a non-sql_table node so callers can size unconditionally.
export function d2SqlColumns(node: any): string[] {
    if (!node || node.shape !== 'sql_table' || !node.attrs) return [];
    return Object.keys(node.attrs).map(k => `${k}: ${node.attrs[k]}`);
}

// Shape-aware node box size (D-082). Most shapes reuse the label-derived
// width/height; a circle is squared so its inscribed area holds the label, and
// a sql_table is grown to fit its title row plus one row per column so the
// columns are never drawn outside the box. Unknown shapes fall back to the
// plain label box, so a node with no shape is byte-identical to before.
export function d2NodeBoxSize(node: any): { width: number; height: number } {
    const label = String((node && (node.label ?? node.id)) ?? '');
    const shape = node && node.shape;
    if (shape === 'sql_table') {
        const rows = d2SqlColumns(node);
        const widest = Math.max(label.length, ...rows.map(r => r.length), 6);
        const width = Math.max(D2_MIN_NODE_WIDTH, Math.min(widest * D2_CHAR_WIDTH + 24, 320));
        const height = D2_SQL_HEADER_HEIGHT + Math.max(rows.length, 1) * D2_SQL_ROW_HEIGHT + 6;
        return { width, height };
    }
    const width = d2NodeWidth(label);
    const height = d2NodeHeight(label);
    if (shape === 'circle') {
        const d = Math.max(width, height);
        return { width: d, height: d };
    }
    return { width, height };
}

// Grid pitch (fallback layout) is at least the widest/tallest node plus a gap,
// so boxes never overlap and truncate each other (D-088).
export function d2GridPitch(nodes: any[]): { x: number; y: number } {
    const widths = nodes.map(n => n.width || d2NodeWidth(n.label || n.id));
    const heights = nodes.map(n => n.height || d2NodeHeight(n.label || n.id));
    const maxW = widths.length ? Math.max(...widths) : D2_MIN_NODE_WIDTH;
    const maxH = heights.length ? Math.max(...heights) : 40;
    return { x: Math.ceil(maxW) + 60, y: Math.ceil(maxH) + 60 };
}

// ---------------------------------------------------------------------------
// Topology-aware fallback layout (G-44: D-090 / D-091 / D-093).
//
// The fallback layout is used whenever ELK cannot run (e.g. elkjs unavailable
// in the headless renderer), which is the state these specs render in. The old
// simpleGridLayout placed nodes in DECLARATION order into a `cols=ceil(sqrt(n))`
// square with no regard for graph topology or container grouping, producing:
//   * a 120-node linear chain squashed into an 11x11 square crossed by long
//     diagonal wrap edges (D-091);
//   * container members scattered across the width so each dashed bounds rect
//     sliced through unrelated groups — a "plaid" (D-090);
//   * 260 edges drawn centre-to-centre over adjacency-blind positions, an
//     undifferentiated hairball (D-093).
// The helpers below make the fallback (a) order nodes by edge adjacency so
// connected nodes sit together, (b) pick a column count from the topology
// (a path-like graph gets a single row, not a square), and (c) pack each
// container's members into a compact DISJOINT block. These only affect the
// degraded fallback path; ELK's own output (the primary path) is untouched.
// ---------------------------------------------------------------------------

// Order nodes so that edge-connected nodes are adjacent in the sequence (BFS
// from indegree-0 roots in declaration order, then any leftovers). Turns an
// out-of-declaration-order chain a->b->c into [a,b,c] and clusters connected
// components, which is what lets a chain render as a readable row and cuts
// edge crossings for general graphs (D-091/D-093). Pure; returns a new array.
export function d2OrderByAdjacency(nodes: any[], edges: any[]): any[] {
    if (!nodes || nodes.length === 0) return nodes || [];
    const byId = new Map(nodes.map(n => [n.id, n]));
    const adj = new Map<string, string[]>();
    const indeg = new Map<string, number>();
    for (const n of nodes) { adj.set(n.id, []); indeg.set(n.id, 0); }
    for (const e of edges || []) {
        if (adj.has(e.source) && byId.has(e.target)) adj.get(e.source)!.push(e.target);
        if (indeg.has(e.target)) indeg.set(e.target, (indeg.get(e.target) || 0) + 1);
    }
    const visited = new Set<string>();
    const order: any[] = [];
    const bfs = (startId: string) => {
        const queue = [startId];
        while (queue.length) {
            const id = queue.shift()!;
            if (visited.has(id)) continue;
            visited.add(id);
            const node = byId.get(id);
            if (node) order.push(node);
            for (const t of adj.get(id) || []) if (!visited.has(t)) queue.push(t);
        }
    };
    // Seed from declaration-order roots (nothing points at them); fall back to
    // all nodes when the graph is a pure cycle (no indegree-0 node).
    const roots = nodes.filter(n => (indeg.get(n.id) || 0) === 0);
    for (const r of (roots.length ? roots : nodes)) if (!visited.has(r.id)) bfs(r.id);
    // Any node not reachable from a seed (isolated / only-incoming component).
    for (const n of nodes) if (!visited.has(n.id)) { visited.add(n.id); order.push(n); }
    return order;
}

// Column count for the fallback grid. A PATH-LIKE graph (every node has total
// degree <= 2 and roughly one edge per node) is laid out in a SINGLE ROW so the
// sequence reads left-to-right with short adjacent hops; anything else keeps the
// square ceil(sqrt(n)) so a dense graph does not become an unreadably wide strip
// (D-091). Pure.
export function d2GridCols(count: number, edges: any[]): number {
    if (count <= 1) return 1;
    const deg = new Map<string, number>();
    for (const e of edges || []) {
        deg.set(e.source, (deg.get(e.source) || 0) + 1);
        deg.set(e.target, (deg.get(e.target) || 0) + 1);
    }
    let maxDeg = 0;
    for (const v of deg.values()) if (v > maxDeg) maxDeg = v;
    const eCount = edges ? edges.length : 0;
    const pathLike = eCount >= count - 1 && eCount <= count && maxDeg <= 2;
    return pathLike ? count : (Math.ceil(Math.sqrt(count)) || 1);
}

// Grouping- and topology-aware fallback layout. Mutates node.x/node.y in place
// and returns { nodes, edges }. When any node carries a container, members of
// each immediate container are packed into a compact disjoint block so their
// bounds rect no longer cuts through other groups (D-090); otherwise nodes are
// adjacency-ordered into a topology-sized grid (D-091/D-093). `origin` keeps the
// leftmost/topmost node clear of the outermost container inset so nested rects
// stay on-canvas (see D2_NEST_STEP).
export function d2SimpleLayout(nodes: any[], edges: any[], origin: number = 100): { nodes: any[]; edges: any[] } {
    if (!nodes || nodes.length === 0) return { nodes: nodes || [], edges: edges || [] };

    // Size every node first so the pitch never lets boxes overlap (D-088).
    nodes.forEach(node => {
        const box = d2NodeBoxSize(node);
        node.width = box.width;
        node.height = box.height;
    });
    const pitch = d2GridPitch(nodes);

    const hasContainers = nodes.some(n => n && n.container);
    if (!hasContainers) {
        const order = d2OrderByAdjacency(nodes, edges);
        const cols = d2GridCols(order.length, edges);
        order.forEach((node, i) => {
            node.x = (i % cols) * pitch.x + origin;
            node.y = Math.floor(i / cols) * pitch.y + origin;
        });
        return { nodes, edges };
    }

    // Group by IMMEDIATE container, preserving adjacency order within/among
    // groups, then flow each group as its own compact sub-grid block. Blocks are
    // spaced by a full pitch (>= 2 * container pad) so dashed rects never touch.
    const ordered = d2OrderByAdjacency(nodes, edges);
    const groups = new Map<string, any[]>();
    const groupOrder: string[] = [];
    for (const n of ordered) {
        const key = n.container || '';
        if (!groups.has(key)) { groups.set(key, []); groupOrder.push(key); }
        groups.get(key)!.push(n);
    }
    const blocksPerRow = Math.max(1, Math.ceil(Math.sqrt(groupOrder.length)));
    const gap = pitch.x;
    const vgap = pitch.y;
    let cursorX = origin;
    let cursorY = origin;
    let rowMaxH = 0;
    let col = 0;
    for (const key of groupOrder) {
        const members = groups.get(key)!;
        const gcols = Math.max(1, Math.ceil(Math.sqrt(members.length)));
        const grows = Math.ceil(members.length / gcols);
        members.forEach((node, i) => {
            node.x = cursorX + (i % gcols) * pitch.x;
            node.y = cursorY + Math.floor(i / gcols) * pitch.y;
        });
        rowMaxH = Math.max(rowMaxH, grows * pitch.y);
        cursorX += gcols * pitch.x + gap;
        if (++col >= blocksPerRow) { col = 0; cursorX = origin; cursorY += rowMaxH + vgap; rowMaxH = 0; }
    }
    return { nodes, edges };
}

// The SVG is painted at natural pixel size so fixed-px label text is not
// downscaled with the viewBox on large graphs (D-086).
export function d2CanvasSize(nodes: any[]): { width: number; height: number; viewBox: string } {
    const maxX = nodes.length ? Math.max(...nodes.map(n => n.x + n.width)) : 0;
    const maxY = nodes.length ? Math.max(...nodes.map(n => n.y + n.height)) : 0;
    const width = Math.max(800, maxX + 100);
    const height = Math.max(400, maxY + 100);
    return { width, height, viewBox: `0 0 ${width} ${height}` };
}

export function stripD2Quotes(v: string): string {
    const s = String(v ?? '').trim();
    if (s.length >= 2 && ((s[0] === '"' && s[s.length - 1] === '"') || (s[0] === "'" && s[s.length - 1] === "'"))) {
        return s.slice(1, -1);
    }
    return s;
}

// Strip a trailing `# comment` from a D2 line. A `#` begins a comment only when
// it sits at the start of a token (line start or preceded by whitespace) and is
// NOT the lead of a bare hex colour value (`#f00` / `#ff0000` / `#ff0000ff`).
// Regions inside single/double quotes are respected so a `#` in a quoted label
// or a quoted `"#8b0000"` colour is never mistaken for a comment. Previously
// only a line that *started* with `#` was dropped, so a trailing
// `Build   # compiles sources` left "# compiles sources" inside the node label,
// overflowing the box past the grid pitch and clipping the neighbour (D-085).
export function stripInlineComment(line: string): string {
    const s = String(line ?? '');
    let inSingle = false;
    let inDouble = false;
    for (let i = 0; i < s.length; i++) {
        const ch = s[i];
        if (ch === "'" && !inDouble) { inSingle = !inSingle; continue; }
        if (ch === '"' && !inSingle) { inDouble = !inDouble; continue; }
        if (ch === '#' && !inSingle && !inDouble) {
            const atTokenStart = i === 0 || /\s/.test(s[i - 1]);
            if (!atTokenStart) continue;
            const rest = s.slice(i + 1);
            // A bare, unquoted hex colour written after a space is a VALUE, not
            // a comment (e.g. `fill: #ff0000`): keep it.
            if (/^[0-9a-fA-F]{3,8}(\s|$)/.test(rest) && /^[0-9a-fA-F]{3,8}$/.test(rest.trim())) continue;
            return s.slice(0, i);
        }
    }
    return s;
}

// Split `s` on any separator char in `seps`, but only at bracket depth 0 so a
// value like rgb(1,2,3) or [a,b] is not fractured. Segments are returned with
// surrounding whitespace intact (the caller trims); empty/whitespace-only
// segments (a trailing separator) are dropped.
export function splitTopLevelSeps(s: string, seps: string): string[] {
    const out: string[] = [];
    let depth = 0;
    let cur = '';
    for (const ch of String(s ?? '')) {
        if (ch === '(' || ch === '[' || ch === '{') { depth++; cur += ch; continue; }
        if (ch === ')' || ch === ']' || ch === '}') { depth = Math.max(0, depth - 1); cur += ch; continue; }
        if (depth === 0 && seps.includes(ch)) {
            if (cur.trim()) out.push(cur);
            cur = '';
            continue;
        }
        cur += ch;
    }
    if (cur.trim()) out.push(cur);
    return out;
}

// Split a single logical line on top-level `;` statement separators so
// `web -> api; api -> db; db -> cache` becomes three statements and
// `web: Web Server; api: API Service` becomes two nodes, instead of the old
// behaviour where the greedy connection regex swallowed `; api` into an
// endpoint and the simple-node parser folded the whole run into one label
// (4 nodes + 3 edges collapsed to 2 nodes + 0 edges) (D-100). A `;` is a
// separator only at bracket depth 0 and OUTSIDE single/double quotes, so a
// `;` inside an inline `{ shape: circle; fill: blue }` property block or a
// quoted label/colour is preserved. Always returns at least the input line
// (a line with no top-level `;` is returned unchanged).
export function splitD2Statements(line: string): string[] {
    const s = String(line ?? '');
    const out: string[] = [];
    let depth = 0;
    let inSingle = false;
    let inDouble = false;
    let cur = '';
    for (let i = 0; i < s.length; i++) {
        const ch = s[i];
        if (ch === "'" && !inDouble) { inSingle = !inSingle; cur += ch; continue; }
        if (ch === '"' && !inSingle) { inDouble = !inDouble; cur += ch; continue; }
        if (!inSingle && !inDouble) {
            if (ch === '(' || ch === '[' || ch === '{') { depth++; cur += ch; continue; }
            if (ch === ')' || ch === ']' || ch === '}') { depth = Math.max(0, depth - 1); cur += ch; continue; }
            if (ch === ';' && depth === 0) {
                if (cur.trim()) out.push(cur.trim());
                cur = '';
                continue;
            }
        }
        cur += ch;
    }
    if (cur.trim()) out.push(cur.trim());
    return out.length ? out : [s.trim()].filter(Boolean);
}

// Whether an author-supplied colour string is actually paintable, so a bad
// value falls back to the theme colour instead of erasing or blackening the
// geometry (D-098). The node/edge/text colour path DOES now read author
// styles (nodeFill/nodeStroke/nodeTextFill), but it passed the value verbatim,
// so `fill: transparent` made a node vanish, a zero-alpha rgba()/#rrggbb00 did
// the same, and an unresolvable token (`var(--x)`, `$token`, `currentColor`)
// fell to SVG-invalid black — visible-but-meaningless in light, invisible on
// the dark #303f9f node fill. Treated as UNUSABLE (→ theme fallback):
// empty/whitespace, transparent, none, currentColor, var(...)/$.../--... tokens,
// and any rgba()/hsla()/#rrggbb00 with a zero alpha. Everything else (hex,
// rgb(), hsl(), CSS names) is left verbatim so a valid author colour is
// byte-identical to before.
export function isUsableD2Color(v: any): boolean {
    if (typeof v !== 'string') return false;
    const s = v.trim().toLowerCase();
    if (!s) return false;
    if (s === 'transparent' || s === 'none' || s === 'currentcolor') return false;
    if (s.startsWith('var(') || s.startsWith('$') || s.startsWith('--')) return false;
    const fn = s.match(/^(?:rgba|hsla)\(([^)]*)\)$/);
    if (fn) {
        const parts = fn[1].split(',').map(p => p.trim());
        if (parts.length >= 4 && parseFloat(parts[3]) === 0) return false;
    }
    if (/^#[0-9a-f]{8}$/.test(s) && s.slice(7) === '00') return false;
    return true;
}

// True when the definition is a JSON graph payload rather than D2 source. D2
// never begins the whole document with a bare `{`/`[` carrying quoted `"key":`
// pairs, whereas a copied JSON graph does. Detecting this lets the renderer
// emit an honest "looks like JSON, not D2" message instead of confidently
// drawing two boxes both labelled '[' (the old behaviour, because `"nodes": [`
// and `"edges": [` contain ':' and fell through to the simple-node parser)
// which then triggered Infinity-rect errors (D-099). A strict JSON.parse is
// tried first; the heuristic covers near-JSON with trailing commas that
// JSON.parse rejects (e.g. d2-w4-02).
export function looksLikeJson(def: string): boolean {
    const s = String(def ?? '').trim();
    if (!s) return false;
    if (!(s.startsWith('{') || s.startsWith('['))) return false;
    try {
        const parsed = JSON.parse(s);
        if (parsed && typeof parsed === 'object') return true;
    } catch {
        // fall through to the heuristic (trailing commas etc.)
    }
    // Must end like a JSON container and contain at least one quoted-key colon
    // pair — this is what distinguishes a JSON blob from a d2 `container { }`.
    if (!/[\]}]\s*$/.test(s)) return false;
    return /"[^"]+"\s*:/.test(s);
}

// Normalise typographic/smart quotes to their ASCII equivalents so a label a
// prose-trained model wrote with curly quotes behaves exactly like the ASCII
// form (D-101). U+201C/U+201D -> '"', U+2018/U+2019 -> "'". Previously the
// curly glyphs passed straight through: stripD2Quotes only recognises ASCII
// '"'/"'" as delimiters, so `web: “Web Server”` kept the visible curly quotes
// in the label where real D2 would strip them, and `web -> api: “hands off”`
// carried the glyphs into the edge label. Em-dash / en-dash are left alone —
// they are legitimate label glyphs, not delimiters. Pure and idempotent.
export function normalizeD2SmartQuotes(s: string): string {
    return String(s ?? '')
        .replace(/[\u201C\u201D\u201E\u201F\u2033\u2036]/g, '"')
        .replace(/[\u2018\u2019\u201A\u201B\u2032\u2035]/g, "'");
}

// True when the definition is Mermaid flowchart source mis-typed as d2 rather
// than D2 syntax. Mermaid uses bracket/brace/stadium node labels (`A[Web
// Server]`, `B{API Gateway}`, `C[(Database)]`) and multi-dash / dotted / thick
// edge operators (`-->`, `-.->`, `==>`), none of which exist in D2 (D2 edges
// are a single-dash `->` and D2 never wraps a label in `[...]`). Detecting this
// lets the renderer say "looks like Mermaid, not D2" instead of drawing each
// node twice (once bracketed, once bare) plus a blank box from the `-.->`
// endpoint (the old behaviour, D-102). Conservative: fires only on an explicit
// Mermaid diagram-type header, or on the co-occurrence of a Mermaid arrow AND a
// bare `id[`/`id{` bracket-label token (a `:`-prefixed d2 `x: { ... }` node
// body has no word char immediately before the brace, so it never matches).
export function looksLikeMermaid(def: string): boolean {
    const s = String(def ?? '').trim();
    if (!s) return false;
    const firstLine = (s.split('\n').find(l => l.trim()) || '').trim();
    if (/^(graph|flowchart)\s+(TB|TD|BT|RL|LR)\b/i.test(firstLine)) return true;
    if (/^(sequenceDiagram|classDiagram|stateDiagram(-v2)?|erDiagram|gantt|pie|journey|gitGraph|mindmap|timeline|quadrantChart)\b/.test(firstLine)) return true;
    // Mermaid-specific edge operator: two-or-more dashes, dotted, or thick.
    const mermaidArrow = /--+>|-\.-*>|==+>|--[xo]\b/.test(s);
    // A bare `id[` or `id{` bracket-label token (word char immediately before
    // the bracket — d2's `x: {` has `: ` before the brace, so it never matches).
    const bracketLabel = /[A-Za-z0-9_]+[[{][^\]}]*[\]}]/.test(s);
    return mermaidArrow && bracketLabel;
}

// Resolve the SVG's rendered pixel size. When the caller passes an explicit
// width/height (render_diagram size, plumbed onto the spec), honour it and keep
// the viewBox at the CONTENT bounds so the graph scales to fit that box
// (preserveAspectRatio meet -> nothing is dropped or clipped, unlike the old
// fixed max(400,maxY+100) that silently truncated rows on an under/over-sized
// request, D-092). With no request we keep the natural pixel size so fixed-px
// label text is not downscaled on large graphs (D-086 preserved).
export function d2ResolveSvgSize(
    canvas: { width: number; height: number; viewBox: string },
    reqWidth?: number,
    reqHeight?: number
): { width: number; height: number; viewBox: string } {
    const ok = (n: any): n is number => typeof n === 'number' && isFinite(n) && n > 0;
    return {
        width: ok(reqWidth) ? reqWidth : canvas.width,
        height: ok(reqHeight) ? reqHeight : canvas.height,
        viewBox: canvas.viewBox,
    };
}

type D2Frame =
    | { kind: 'container'; id: string }
    | { kind: 'node'; id: string }
    | { kind: 'style'; targetKind: 'node' | 'class'; targetId: string }
    | { kind: 'classes' }
    | { kind: 'classdef'; name: string };

// Enhanced D2 parser with better syntax support
// D2 parser and renderer
export class D2Parser {
    private nodes: Map<string, any> = new Map();
    private edges: any[] = [];
    private containers: Map<string, any> = new Map();
    private classes: Map<string, any> = new Map();
    private frames: D2Frame[] = [];
    private direction: string | null = null;

    constructor() {
        this.reset();
    }

    private reset() {
        this.nodes.clear();
        this.edges = [];
        this.containers.clear();
        this.classes.clear();
        this.frames = [];
        this.direction = null;
    }

    parse(definition: string) {
        this.reset();

        // Fold typographic/smart quotes to ASCII up front (D-101) so a curly
        // label behaves exactly like the ASCII form for delimiter stripping.
        definition = normalizeD2SmartQuotes(definition);

        // Strip trailing `# comment` from each line BEFORE trimming so a
        // comment tail cannot leak into a node label / edge (D-085); the
        // existing full-line comment filter still drops `# ...` lines.
        // Strip trailing comments, then split each physical line on top-level
        // `;` separators so semicolon-delimited statements become independent
        // lines (D-100). `;` inside an inline `{ ... }` block or a quoted
        // string is preserved by splitD2Statements.
        const lines: string[] = [];
        for (const raw of definition.split('\n')) {
            const stripped = stripInlineComment(raw).trim();
            if (!stripped || stripped.startsWith('#')) continue;
            for (const stmt of splitD2Statements(stripped)) {
                const t = stmt.trim();
                if (t && !t.startsWith('#')) lines.push(t);
            }
        }

        for (const line of lines) {
            this.parseLine(line);
        }

        return {
            nodes: Array.from(this.nodes.values()),
            edges: this.edges,
            containers: Array.from(this.containers.values()),
            direction: this.direction
        };
    }

    // The nearest enclosing plain container (for node.container assignment and
    // container.children bookkeeping). Style/node/class frames are transparent
    // to container nesting.
    private currentContainerId(): string | null {
        for (let i = this.frames.length - 1; i >= 0; i--) {
            const f = this.frames[i];
            if (f.kind === 'container') return f.id;
        }
        return null;
    }

    private topFrame(): D2Frame | undefined {
        return this.frames[this.frames.length - 1];
    }

    private parseLine(line: string) {
        // Block start / end.
        if (line.endsWith('{')) {
            this.openBlock(line.slice(0, -1).trim());
            return;
        }
        if (line === '}') {
            this.frames.pop();
            return;
        }

        const top = this.topFrame();

        // Bare "key: value" inside a `style { }` block applies to the target
        // node/class instead of becoming a phantom node (D-083).
        if (top && top.kind === 'style') {
            this.applyStyleKV(top.targetKind, top.targetId, line);
            return;
        }

        // Inside a class definition: "style.key: value" (or bare "key: value").
        if (top && top.kind === 'classdef') {
            this.applyClassLine(top.name, line);
            return;
        }

        // Bare lines inside an `id: Label { ... }` node body apply to THAT node
        // (shape / label / width / height / style.* / inline `style: { ... }`)
        // instead of leaking as phantom nodes — the old code fell through to
        // parseSimpleNode/parseNodeWithProperties and turned `shape: sql_table`,
        // `id: int`, `style: { fill: red }` etc. into boxes, discarding the
        // node's own label (D-097). Connections inside a node body are honoured.
        if (top && top.kind === 'node') {
            if (line.includes('->') || line.includes('<-')) {
                this.parseConnection(line);
                return;
            }
            this.applyNodeBodyLine(top.id, line);
            return;
        }

        // Connections (edges).
        if (line.includes('->') || line.includes('<->') || line.includes('<-')) {
            this.parseConnection(line);
            return;
        }

        // Top-level `direction: <up|down|left|right>` is a layout keyword, not
        // a node — the old code fell through to parseSimpleNode and drew a box
        // labelled 'right' (D-084). Only honoured outside any block frame.
        if (!top) {
            const dirMatch = line.match(/^direction\s*:\s*(up|down|left|right)\s*$/i);
            if (dirMatch) {
                this.direction = dirMatch[1].toLowerCase();
                return;
            }
        }

        // Dotted style path outside a block: `X.style.key: value` — must be
        // handled BEFORE the simple-node branch (which used to turn the whole
        // line into a node labelled with the colour value) (D-083).
        if (this.tryParseDottedStyle(line)) {
            return;
        }

        // Node with inline `{ ... }` properties.
        if (line.includes(':') && line.includes('{') && line.includes('}')) {
            this.parseNodeWithProperties(line);
            return;
        }

        // Simple node definition.
        if (line.includes(':')) {
            this.parseSimpleNode(line);
            return;
        }

        // Bare node id (no colon).
        if (line.length > 0) {
            this.ensureNode(line);
        }
    }

    private openBlock(headRaw: string) {
        let head = headRaw;
        let trailingColon = false;
        if (head.endsWith(':')) { head = head.slice(0, -1).trim(); trailingColon = true; }
        const top = this.topFrame();

        // `classes { }` block.
        if (head === 'classes' && (!top || top.kind !== 'classdef')) {
            this.frames.push({ kind: 'classes' });
            return;
        }

        // A class definition `name { }` inside a `classes { }` block.
        if (top && top.kind === 'classes') {
            if (!this.classes.has(head)) this.classes.set(head, { name: head, style: {} });
            this.frames.push({ kind: 'classdef', name: head });
            return;
        }

        // `style { }` block -> styles for the nearest node or class definition,
        // never a container (D-083: this used to lose the parent's label and
        // turn each style value into a child node).
        if (head === 'style') {
            for (let i = this.frames.length - 1; i >= 0; i--) {
                const f = this.frames[i];
                if (f.kind === 'node') { this.frames.push({ kind: 'style', targetKind: 'node', targetId: f.id }); return; }
                if (f.kind === 'classdef') { this.frames.push({ kind: 'style', targetKind: 'class', targetId: f.name }); return; }
            }
            // Orphan style block: swallow it so its keys never become nodes.
            this.frames.push({ kind: 'style', targetKind: 'node', targetId: '' });
            return;
        }

        // `X.style { }` block.
        const dotStyle = head.match(/^(.+)\.style$/);
        if (dotStyle) {
            const id = dotStyle[1].trim();
            this.ensureNode(id);
            this.frames.push({ kind: 'style', targetKind: 'node', targetId: id });
            return;
        }

        // `id: Label {` -> node with a body block (the body typically holds a
        // nested `style { }`).
        if (head.includes(':')) {
            const idx = head.indexOf(':');
            const nodeId = head.slice(0, idx).trim();
            const label = head.slice(idx + 1).trim();
            this.upsertNode(nodeId, label || nodeId);
            this.frames.push({ kind: 'node', id: nodeId });
            return;
        }

        // `X: {` (trailing-colon head, no inline label) is a NODE body with the
        // default label, NOT a container — this is how a
        // `users: { shape: sql_table ... }` table node is written. The old code
        // stripped the ':' and treated it as a container, shredding the body's
        // attribute lines into phantom column nodes and leaving the table
        // anonymous (D-082). Class/style/labelled-node blocks are handled above,
        // so this only fires for a real node body. A plain `X {` (no colon)
        // remains a container.
        if (trailingColon && head && !head.includes(':')) {
            this.upsertNode(head, this.nodes.get(head)?.label || head);
            this.frames.push({ kind: 'node', id: head });
            return;
        }

        // Plain container (unchanged behaviour: nested containers keep parent
        // pointers).
        const parent = this.currentContainerId();
        if (!this.containers.has(head)) {
            this.containers.set(head, {
                id: head,
                label: head,
                type: 'container',
                children: [],
                parent
            });
        }
        this.frames.push({ kind: 'container', id: head });
    }

    private tryParseDottedStyle(line: string): boolean {
        const m = line.match(/^(.+?)\.style\.([A-Za-z0-9_-]+)\s*:\s*(.+)$/);
        if (!m) return false;
        const nodeId = m[1].trim();
        const key = m[2].trim();
        const val = stripD2Quotes(m[3]);
        this.setNodeStyle(nodeId, key, val);
        return true;
    }

    private applyStyleKV(targetKind: 'node' | 'class', targetId: string, line: string) {
        const idx = line.indexOf(':');
        if (idx < 0) return;
        let key = line.slice(0, idx).trim();
        const val = stripD2Quotes(line.slice(idx + 1));
        if (key.startsWith('style.')) key = key.slice(6);
        if (!key || key === 'style') return;
        if (targetKind === 'class') {
            const c = this.classes.get(targetId) || { name: targetId, style: {} };
            c.style[key] = val;
            this.classes.set(targetId, c);
        } else if (targetId) {
            this.setNodeStyle(targetId, key, val);
        }
    }

    private applyClassLine(name: string, line: string) {
        const idx = line.indexOf(':');
        if (idx < 0) return;
        let key = line.slice(0, idx).trim();
        const val = stripD2Quotes(line.slice(idx + 1));
        if (key.startsWith('style.')) key = key.slice(6);
        if (!key || key === 'style') return;
        const c = this.classes.get(name) || { name, style: {} };
        c.style[key] = val;
        this.classes.set(name, c);
    }

    // A `key: value` line directly inside an `id: Label { ... }` node body.
    // Everything is applied to `nodeId`; nothing here ever creates a new node,
    // which is the whole point of D-097 (attribute bodies used to leak as
    // phantom nodes and steal the box label). Recognised keys:
    //   shape / label            -> node attribute
    //   width / height           -> numeric node dimension
    //   style.<k> / inline style: { ... }  -> node style
    //   anything else (near, tooltip, icon, sql-table columns, ...) -> stashed
    //                              on node.attrs so it is preserved but inert.
    private applyNodeBodyLine(nodeId: string, line: string) {
        const idx = line.indexOf(':');
        if (idx < 0) return; // a bare token in a node body is not a phantom node
        const key = line.slice(0, idx).trim();
        const rest = line.slice(idx + 1).trim();

        // Inline style object: `style: { fill: red, stroke: navy, }`.
        const brace = rest.match(/^\{([\s\S]*)\}$/);
        if (key === 'style' && brace) {
            const props = this.parseProperties(brace[1]);
            for (const [k, v] of Object.entries(props)) this.setNodeStyle(nodeId, k, v as string);
            return;
        }

        const val = stripD2Quotes(rest);
        if (key.startsWith('style.')) { this.setNodeStyle(nodeId, key.slice(6), val); return; }

        const node = this.nodes.get(nodeId);
        if (!node) return;
        if (key === 'shape' || key === 'label') { (node as any)[key] = val; return; }
        if (key === 'width' || key === 'height') {
            const num = parseFloat(val);
            if (isFinite(num) && num > 0) (node as any)[key] = num;
            return;
        }
        node.attrs = node.attrs || {};
        node.attrs[key] = val;
    }

    private setNodeStyle(nodeId: string, key: string, val: string) {
        this.ensureNode(nodeId);
        const node = this.nodes.get(nodeId);
        node.style = node.style || {};
        node.style[key] = val;
    }

    private upsertNode(nodeId: string, label: string) {
        const existing = this.nodes.get(nodeId);
        if (existing) {
            if (label) existing.label = label;
            return existing;
        }
        const node = {
            id: this.normalizeNodeId(nodeId),
            label: label || nodeId,
            originalId: nodeId,
            container: this.currentContainerId()
        };
        this.nodes.set(nodeId, node);
        const cid = this.currentContainerId();
        if (cid && this.containers.has(cid)) {
            this.containers.get(cid).children.push(nodeId);
        }
        return node;
    }

    private parseConnection(line: string) {
        // A connection may CHAIN: `a -> b -> c` must yield BOTH a->b and b->c,
        // not a single edge to a phantom node "b -> c" (the old single-match
        // parser matched only the first connector and swallowed the rest into
        // the target endpoint, dropping every downstream node) (D-084).
        //
        // Split into endpoints and connectors at top level. String.split with a
        // capturing group keeps the connector tokens, so the result alternates
        // endpoint, connector, endpoint, ... Alternatives are ordered
        // longest-first so '<->' wins over its '<-'/'->' prefixes.
        const parts = line.split(/(<->|<-|->)/);
        if (parts.length < 3) {
            return;
        }

        // A trailing ": label" belongs to the LAST endpoint; it applies to
        // every connection in the chain (matching d2's chained-label
        // semantics). Splitting on the first ':' keeps `a -> b: x` -> label 'x'
        // and two nodes a,b (D-078 regression guard).
        let label = '';
        const lastIdx = parts.length - 1;
        const colonIdx = parts[lastIdx].indexOf(':');
        if (colonIdx >= 0) {
            label = parts[lastIdx].slice(colonIdx + 1).trim();
            parts[lastIdx] = parts[lastIdx].slice(0, colonIdx);
        }

        const endpoints: string[] = [];
        const connectors: string[] = [];
        for (let i = 0; i < parts.length; i++) {
            if (i % 2 === 0) endpoints.push(parts[i].trim());
            else connectors.push(parts[i]);
        }

        // Any empty endpoint (e.g. a dangling connector) makes the whole line
        // ill-formed — emit nothing rather than a phantom edge.
        if (endpoints.some(e => !e)) {
            return;
        }

        // Ensure every node in the chain exists.
        const resolved = endpoints.map(e => this.resolvePath(e));
        for (const id of resolved) this.ensureNode(id);

        for (let i = 0; i < connectors.length; i++) {
            const connector = connectors[i];
            this.edges.push({
                source: this.normalizeNodeId(resolved[i]),
                target: this.normalizeNodeId(resolved[i + 1]),
                label: label,
                bidirectional: connector === '<->',
                reversed: connector === '<-'
            });
        }
    }

    private parseSimpleNode(line: string) {
        const parts = line.split(':');
        if (parts.length >= 2) {
            const nodeId = parts[0].trim();
            const label = parts.slice(1).join(':').trim();
            this.upsertNode(nodeId, label || nodeId);
        }
    }

    private parseNodeWithProperties(line: string) {
        // Handle node properties like: `node: { shape: circle; fill: blue }`
        // and `auth: Auth {class: service}` (label between the colon and brace).
        const match = line.match(/^([^:{]+):\s*([^{]*?)\s*\{([^}]*)\}\s*$/);
        if (!match) {
            return;
        }
        const nodeId = match[1].trim();
        const label = match[2].trim();
        const propsStr = match[3].trim();

        const node = this.upsertNode(nodeId, label || (this.nodes.get(nodeId)?.label) || nodeId);

        const props = this.parseProperties(propsStr);

        // A `class:` reference seeds the node's style from the class; explicit
        // inline style keys then override the class values.
        if (props.class) {
            const cls = this.classes.get(props.class);
            if (cls && cls.style) {
                node.style = { ...(cls.style), ...(node.style || {}) };
            }
        }
        for (const [k, v] of Object.entries(props)) {
            if (k === 'class') continue;
            if (k === 'shape' || k === 'label') {
                (node as any)[k] = v;
            } else {
                node.style = node.style || {};
                node.style[k] = v as string;
            }
        }
    }

    private parseProperties(propString: string): any {
        const props: any = {};
        // Split on both ';' and top-level ',' so comma-separated inline styles
        // (`fill: red, stroke: navy,`) and their trailing commas are tolerated
        // (D-097 / recovery). Splitting is paren/bracket/brace-aware so a value
        // like rgb(1,2,3) is not fractured; empty segments (a trailing
        // separator) are skipped.
        const pairs = splitTopLevelSeps(propString, ';,');

        for (const pair of pairs) {
            const idx = pair.indexOf(':');
            if (idx < 0) continue;
            const key = pair.slice(0, idx).trim();
            const value = stripD2Quotes(pair.slice(idx + 1));
            if (key) {
                props[key] = value;
            }
        }

        return props;
    }

    private resolvePath(path: string): string {
        // Handle dotted paths like container.node
        if (path.includes('.')) {
            const parts = path.split('.');
            // For now, just use the last part as the node ID
            // In a full implementation, you'd handle the hierarchy properly
            return parts[parts.length - 1];
        }
        return path;
    }

    private ensureNode(nodeId: string) {
        if (!this.nodes.has(nodeId)) {
            this.nodes.set(nodeId, {
                id: this.normalizeNodeId(nodeId),
                label: nodeId,
                originalId: nodeId,
                container: this.currentContainerId()
            });
        }
    }

    private normalizeNodeId(id: string): string {
        return id.replace(/[^a-zA-Z0-9]/g, '_');
    }
}

// Full ELK layout engine integration
class ELKLayoutEngine {
    private elk: any | null = null;

    async initialize() {
        if (!this.elk) {
            const ELK = (await import('elkjs')).default;
            this.elk = new ELK();
        }
    }

    async layout(nodes: any[], edges: any[], options: any = {}) {
        if (nodes.length === 0) {
            return { nodes: [], edges: [] };
        }

        await this.initialize();
        
        // Create ELK graph structure
        const elkGraph = {
            id: 'root',
            layoutOptions: {
                'elk.algorithm': options.algorithm || 'layered',
                'elk.direction': options.direction || 'DOWN',
                'elk.spacing.nodeNode': options.nodeSpacing || '50',
                'elk.layered.spacing.nodeNodeBetweenLayers': options.layerSpacing || '50',
                'elk.spacing.edgeNode': '30',
                'elk.spacing.edgeEdge': '15',
                'elk.layered.crossingMinimization.strategy': 'LAYER_SWEEP',
                'elk.layered.nodePlacement.strategy': 'BRANDES_KOEPF',
                'elk.layered.cycleBreaking.strategy': 'GREEDY',
                'elk.insideSelfLoops.activate': 'true',
                ...options
            },
            children: nodes.map(node => {
                // Shape-aware size so a sql_table reserves room for its column
                // rows and a circle is squared (D-082). A sql_table is sized by
                // MINIMUM_SIZE (not NODE_LABELS) so ELK does not shrink it back
                // to just the title label.
                const box = d2NodeBoxSize(node);
                const isTable = node.shape === 'sql_table';
                return {
                    id: node.id,
                    width: box.width,
                    height: box.height,
                    labels: buildElkNodeLabels(node),
                    layoutOptions: {
                        'elk.nodeSize.constraints': isTable ? 'MINIMUM_SIZE' : 'NODE_LABELS',
                        'elk.nodeSize.minimum': isTable ? `(${box.width},${box.height})` : undefined,
                        'elk.nodeSize.options': 'DEFAULT_MINIMUM_SIZE COMPUTE_PADDING',
                        'elk.padding': '[top=10,left=15,bottom=10,right=15]'
                    }
                };
            }),
            edges: edges.map(edge => ({
                id: `${edge.source}_${edge.target}`,
                sources: [edge.source],
                targets: [edge.target],
                labels: edge.label ? [{
                    text: edge.label,
                    layoutOptions: {
                        'elk.edgeLabels.placement': 'CENTER'
                    }
                }] : [],
                layoutOptions: {
                    'elk.edge.type': edge.bidirectional ? 'UNDIRECTED' : 'DIRECTED'
                }
            }))
        };

        try {
            // Use ELK to compute the layout
            const layoutedGraph = await this.elk.layout(elkGraph);

            // Transform ELK result back to our format
            const layoutedNodes = layoutedGraph.children?.map((elkNode: any) => {
                const originalNode = nodes.find(n => n.id === elkNode.id);
                return {
                    ...originalNode,
                    x: elkNode.x || 0,
                    y: elkNode.y || 0,
                    width: elkNode.width || 100,
                    height: elkNode.height || 50
                };
            }) || [];

            return { nodes: layoutedNodes, edges };
        } catch (error) {
            console.warn('ELK layout failed, falling back to simple layout:', error);
            return this.simpleGridLayout(nodes, edges);
        }
    }

    private calculateNodeWidth(text: string): number {
        return d2NodeWidth(text);
    }

    private calculateNodeHeight(text: string): number {
        return d2NodeHeight(text);
    }

    private simpleGridLayout(nodes: any[], edges: any[]) {
        // Fallback layout when ELK fails. Delegates to the shared, grouping- and
        // topology-aware d2SimpleLayout: nodes are sized first (pitch >= widest
        // node so boxes never overlap, D-088), then adjacency-ordered and either
        // packed into per-container blocks (D-090) or placed in a topology-sized
        // grid — a path-like graph gets a single row instead of a square crossed
        // by diagonal wrap edges (D-091/D-093).
        return d2SimpleLayout(nodes, edges);
    }
}

export const d2Plugin: D3RenderPlugin = {
    name: 'd2-renderer',
    priority: 6,
    sizingConfig: {
        sizingStrategy: 'auto-expand',
        needsDynamicHeight: true,
        needsOverflowVisible: true,
        observeResize: true,
        containerStyles: {
            width: '100%',
            height: 'auto',
            overflow: 'visible'
        }
    },
    canHandle: isD2Spec,

    isDefinitionComplete: (definition: string): boolean => {
        if (!definition || definition.trim().length === 0) return false;

        // Check for basic D2 syntax patterns
        const lines = definition.trim().split('\n').filter(line => line.trim());
        if (lines.length === 0) return false;

        // Look for connections or node definitions
        const hasConnections = lines.some(line =>
            line.includes('->') || line.includes('<->') || line.includes('<-')
        );
        const hasNodes = lines.some(line => line.includes(':'));

        return hasConnections || hasNodes;
    },

    render: async (container: HTMLElement, d3: any, spec: D2Spec, isDarkMode: boolean) => {
        try {
            // Check if streaming and incomplete
            if (spec.isStreaming && !spec.forceRender) {
                const isComplete = d2Plugin.isDefinitionComplete!(spec.definition);
                if (!isComplete) {
                    container.innerHTML = `
                        <div style="text-align: center; padding: 20px; background-color: ${isDarkMode ? '#1f1f1f' : '#f6f8fa'}; border: 1px dashed #ccc; border-radius: 4px;">
                            <p>Waiting for complete D2 definition...</p>
                        </div>
                    `;
                    return;
                }
            }

            // Parse D2 definition
            const extractedDefinition = extractDefinitionFromYAML(spec.definition, 'd2');

            // A JSON graph payload is not D2 source. The old parser accepted
            // `"nodes": [` / `"edges": [` (they contain ':') as simple nodes,
            // drawing two boxes both labelled '[' and then throwing
            // Infinity-rect errors. Detect it and say so honestly (D-099).
            if (looksLikeJson(extractedDefinition)) {
                container.innerHTML = `
                    <div style="
                        padding: 20px;
                        background-color: ${isDarkMode ? '#2a1f1f' : '#fff2f0'};
                        border: 1px solid ${isDarkMode ? '#a61d24' : '#ffa39e'};
                        border-radius: 6px;
                        color: ${isDarkMode ? '#ff7875' : '#cf1322'};
                    ">
                        <strong>This looks like a JSON graph payload, not D2 syntax.</strong>
                        <p style="margin: 10px 0;">D2 uses lines like <code>a -&gt; b</code> and
                        <code>id: Label</code>, not a JSON object. Convert the graph to D2, or
                        select a renderer that accepts JSON.</p>
                    </div>
                `;
                return;
            }

            // Mermaid flowchart source mis-typed as d2 (bracket/brace labels +
            // `-->`/`-.->` arrows). The old parser folded each node into two
            // boxes plus a blank endpoint; say so honestly instead (D-102).
            if (looksLikeMermaid(extractedDefinition)) {
                container.innerHTML = `
                    <div style="
                        padding: 20px;
                        background-color: ${isDarkMode ? '#2a1f1f' : '#fff2f0'};
                        border: 1px solid ${isDarkMode ? '#a61d24' : '#ffa39e'};
                        border-radius: 6px;
                        color: ${isDarkMode ? '#ff7875' : '#cf1322'};
                    ">
                        <strong>This looks like Mermaid, not D2 syntax.</strong>
                        <p style="margin: 10px 0;">D2 connects nodes with <code>a -&gt; b</code>
                        and labels them <code>id: Label</code>; it does not use Mermaid's
                        <code>A[Label]</code>/<code>B{Label}</code> node shapes or
                        <code>--&gt;</code>/<code>-.-&gt;</code> arrows. Set the diagram type to
                        <code>mermaid</code>, or rewrite the graph in D2.</p>
                    </div>
                `;
                return;
            }

            const parser = new D2Parser();
            const { nodes, edges, containers, direction: parsedDirection } = parser.parse(extractedDefinition);

            if (nodes.length === 0) {
                container.innerHTML = `
                    <div style="text-align: center; padding: 20px; color: ${isDarkMode ? '#ff6b6b' : '#d63031'};">
                        <p>No nodes found in D2 definition</p>
                    </div>
                `;
                return;
            }

            // Apply layout
            const layoutEngine = new ELKLayoutEngine();
            // Configure layout options based on diagram complexity
            // A `direction:` keyword (D-084) overrides the default flow; else
            // fall back to DOWN when containers are present, RIGHT otherwise.
            const DIR_MAP: Record<string, string> = { up: 'UP', down: 'DOWN', left: 'LEFT', right: 'RIGHT' };
            const layoutOptions = {
                algorithm: spec.layout || 'layered',
                direction: parsedDirection ? (DIR_MAP[parsedDirection] || 'DOWN') : (containers.length > 0 ? 'DOWN' : 'RIGHT'),
                nodeSpacing: '60',
                layerSpacing: '80'
            };

            const layoutResult = await layoutEngine.layout(nodes, edges, layoutOptions);

            // Grow sql_table boxes to fit their column rows regardless of the
            // size the layout engine returned, so columns are never drawn
            // outside the box (D-082). Boxes grow from their top-left origin.
            layoutResult.nodes.forEach((n: any) => {
                if (n && n.shape === 'sql_table') {
                    const box = d2NodeBoxSize(n);
                    n.width = Math.max(n.width || 0, box.width);
                    n.height = Math.max(n.height || 0, box.height);
                }
            });

            // Render with D3. The SVG is sized at its natural pixel size (not
            // width:100%) so the fixed-px label text is not downscaled with the
            // viewBox on large graphs (D-086); the auto-expand / overflow:visible
            // container scrolls rather than shrinking the content.
            container.innerHTML = '';
        const canvas = d2CanvasSize(layoutResult.nodes);
        // Honour an explicit requested size (render_diagram width/height plumbed
        // onto the spec) by scaling the content to that box via the viewBox,
        // rather than silently truncating rows or downscaling text (D-092).
        const svgSize = d2ResolveSvgSize(canvas, (spec as any).width, (spec as any).height);
        const svg = d3.select(container)
            .append('svg')
            .attr('width', svgSize.width)
            .attr('height', svgSize.height)
            .attr('viewBox', svgSize.viewBox)
            .attr('preserveAspectRatio', 'xMinYMin meet');

            // Theme colors — resolved per-theme (D-094/D-095); see d2ThemeColors.
    const colors = d2ThemeColors(isDarkMode);

    // Author-supplied styles (parsed from `style { }` / `X.style.k: v` /
    // class references) are honoured when present, else the theme default is
    // used — nodes without a style are byte-identical to before (D-083).
    // An author colour is honoured only when it is actually paintable; an
    // unusable value (transparent / none / currentColor / var(...)/$token /
    // zero-alpha rgba()/#rrggbb00) falls back to the theme colour instead of
    // erasing the node (transparent) or blackening it (invalid token) — the
    // fallback is the theme FILL, never `none`, so geometry can never be
    // erased by a caller colour in either theme (D-098). A valid author colour
    // is passed through verbatim, so a styled node is byte-identical to before.
    const nodeFill = (d: any) => (d.style && isUsableD2Color(d.style.fill)) ? d.style.fill : colors.node;
    const nodeStroke = (d: any) => (d.style && isUsableD2Color(d.style.stroke)) ? d.style.stroke : colors.nodeStroke;
    const nodeStrokeWidth = (d: any) => (d.style && d.style['stroke-width']) ? d.style['stroke-width'] : 2;
    const nodeTextFill = (d: any) => {
        const c = d.style && (d.style['font-color'] || d.style.color);
        return isUsableD2Color(c) ? c : colors.text;
    };

            // Render containers first (as background rectangles). Bounds are
            // computed from the laid-out MEMBER nodes (walking the nesting
            // chain), and any container with no laid-out member is skipped so
            // an empty children[] can never produce an Infinity rect.
    if (containers.length > 0) {
        const containerRects = containers
            .map((c: any) => ({ container: c, bounds: d2ContainerBounds(c, layoutResult.nodes, containers) }))
            .filter((entry: any) => entry.bounds !== null);

        svg.selectAll('.container')
            .data(containerRects)
            .enter()
            .append('rect')
            .attr('class', 'container')
            .attr('x', (d: any) => d.bounds.x)
            .attr('y', (d: any) => d.bounds.y)
            .attr('width', (d: any) => d.bounds.width)
            .attr('height', (d: any) => d.bounds.height)
            .attr('fill', 'none')
            .attr('stroke', isDarkMode ? '#4cc9f0' : '#1976d2')
            .attr('stroke-width', 2)
            .attr('stroke-dasharray', '5,5')
            .attr('rx', 10);

        // Container label: the group name is drawn at the top-left of its
        // bounds so groups are no longer anonymous (D-080). The colour is the
        // theme-resolved text colour (colors.text), so it is legible on both
        // the light and dark page background — structural defect, but the label
        // must read in both themes.
        svg.selectAll('.container-label')
            .data(containerRects)
            .enter()
            .append('text')
            .attr('class', 'container-label')
            .attr('x', (d: any) => d.bounds.x + 8)
            .attr('y', (d: any) => d.bounds.y + 16)
            .attr('fill', colors.text)
            .attr('font-family', 'Arial, sans-serif')
            .attr('font-size', `${D2_FONT_SIZE}px`)
            .attr('font-weight', 'bold')
            .text((d: any) => d.container.label || d.container.id);
    }

            // Arrowhead markers. refX=10 puts the path apex (x=10) at the line
            // endpoint, which trimEdgeToNodes() places just OUTSIDE the target
            // box so the head is always visible (the old refX=8 marker sat at
            // the target centre, hidden under the node rect). A start marker
            // (auto-start-reverse) draws reverse / bidirectional heads.
            const defs = svg.append('defs');
            defs.append('marker')
                .attr('id', 'arrowhead')
                .attr('viewBox', '0 -5 10 10')
                .attr('refX', 10)
                .attr('refY', 0)
                .attr('markerWidth', 8)
                .attr('markerHeight', 8)
                .attr('orient', 'auto')
                .append('path')
                .attr('d', 'M0,-5L10,0L0,5')
                .attr('fill', colors.edge);
            defs.append('marker')
                .attr('id', 'arrowhead-start')
                .attr('viewBox', '0 -5 10 10')
                .attr('refX', 10)
                .attr('refY', 0)
                .attr('markerWidth', 8)
                .attr('markerHeight', 8)
                .attr('orient', 'auto-start-reverse')
                .append('path')
                .attr('d', 'M0,-5L10,0L0,5')
                .attr('fill', colors.edge);

            // Render edges border-to-border (trimmed via trimEdgeToNodes) so
            // arrowheads clear the target box; direction-aware markers
            // distinguish '->', '<-' and '<->'.
            const edgeGeom = (d: any) => trimEdgeToNodes(
                layoutResult.nodes.find(n => n.id === d.source),
                layoutResult.nodes.find(n => n.id === d.target)
            );
            svg.selectAll('.edge')
    .data(layoutResult.edges)
    .enter()
    .append('line')
    .attr('class', 'edge')
    .attr('x1', (d: any) => edgeGeom(d).x1)
    .attr('y1', (d: any) => edgeGeom(d).y1)
    .attr('x2', (d: any) => edgeGeom(d).x2)
    .attr('y2', (d: any) => edgeGeom(d).y2)
    .attr('stroke', colors.edge)
    .attr('stroke-width', 2)
    .attr('marker-end', (d: any) => (d.bidirectional || !d.reversed) ? 'url(#arrowhead)' : null)
    .attr('marker-start', (d: any) => (d.bidirectional || d.reversed) ? 'url(#arrowhead-start)' : null);

            // Render nodes
const nodeGroups = svg.selectAll('.node')
    .data(layoutResult.nodes)
    .enter()
    .append('g')
    .attr('class', 'node')
    .attr('transform', d => `translate(${d.x}, ${d.y})`);

// Node shape dispatch (D-082): the parsed `node.shape` is honoured instead of
// always drawing a rounded rect. Unknown shapes fall back to the rect so no
// existing spec regresses. A sql_table draws a titled box with one text row per
// column (the columns are stashed on node.attrs by the node-body parser). The
// label is wrapped into tspan lines and stays inside the box (D-087 preserved).
nodeGroups.each(function (this: any, d: any) {
    const g = d3.select(this);
    const w = d.width;
    const h = d.height;
    const fill = nodeFill(d);
    const stroke = nodeStroke(d);
    const sw = nodeStrokeWidth(d);
    const textFill = nodeTextFill(d);
    const shape = (d.shape || 'rectangle');

    const drawWrappedLabel = () => {
        const text = g.append('text')
            .attr('x', w / 2)
            .attr('y', h / 2)
            .attr('text-anchor', 'middle')
            .attr('dominant-baseline', 'middle')
            .attr('fill', textFill)
            .attr('font-family', 'Arial, sans-serif')
            .attr('font-size', `${D2_FONT_SIZE}px`);
        const lines = wrapLabel(d.label, w);
        const startDy = -((lines.length - 1) / 2) * D2_LINE_HEIGHT;
        lines.forEach((ln: string, i: number) => {
            text.append('tspan')
                .attr('x', w / 2)
                .attr('dy', i === 0 ? startDy : D2_LINE_HEIGHT)
                .text(ln);
        });
    };

    if (shape === 'sql_table') {
        g.append('rect')
            .attr('width', w).attr('height', h)
            .attr('fill', fill).attr('stroke', stroke).attr('stroke-width', sw).attr('rx', 4);
        g.append('line')
            .attr('x1', 0).attr('y1', D2_SQL_HEADER_HEIGHT)
            .attr('x2', w).attr('y2', D2_SQL_HEADER_HEIGHT)
            .attr('stroke', stroke).attr('stroke-width', 1);
        g.append('text')
            .attr('x', w / 2).attr('y', D2_SQL_HEADER_HEIGHT / 2)
            .attr('text-anchor', 'middle').attr('dominant-baseline', 'middle')
            .attr('fill', textFill).attr('font-weight', 'bold')
            .attr('font-family', 'Arial, sans-serif').attr('font-size', `${D2_FONT_SIZE}px`)
            .text(d.label);
        d2SqlColumns(d).forEach((row: string, i: number) => {
            g.append('text')
                .attr('x', 8)
                .attr('y', D2_SQL_HEADER_HEIGHT + i * D2_SQL_ROW_HEIGHT + D2_SQL_ROW_HEIGHT / 2)
                .attr('dominant-baseline', 'middle')
                .attr('fill', textFill).attr('font-family', 'Arial, sans-serif')
                .attr('font-size', '11px')
                .text(row);
        });
        return;
    }

    if (shape === 'circle' || shape === 'oval' || shape === 'ellipse') {
        g.append('ellipse')
            .attr('cx', w / 2).attr('cy', h / 2)
            .attr('rx', w / 2).attr('ry', h / 2)
            .attr('fill', fill).attr('stroke', stroke).attr('stroke-width', sw);
    } else if (shape === 'diamond' || shape === 'rhombus') {
        g.append('polygon')
            .attr('points', `${w / 2},0 ${w},${h / 2} ${w / 2},${h} 0,${h / 2}`)
            .attr('fill', fill).attr('stroke', stroke).attr('stroke-width', sw);
    } else if (shape === 'hexagon') {
        const inset = Math.min(20, w / 4);
        g.append('polygon')
            .attr('points', `${inset},0 ${w - inset},0 ${w},${h / 2} ${w - inset},${h} ${inset},${h} 0,${h / 2}`)
            .attr('fill', fill).attr('stroke', stroke).attr('stroke-width', sw);
    } else if (shape === 'cylinder' || shape === 'stored_data') {
        const ry = Math.min(10, h / 6);
        g.append('path')
            .attr('d', `M0,${ry} L0,${h - ry} A ${w / 2},${ry} 0 0 0 ${w},${h - ry} L ${w},${ry}`)
            .attr('fill', fill).attr('stroke', stroke).attr('stroke-width', sw);
        g.append('ellipse')
            .attr('cx', w / 2).attr('cy', ry).attr('rx', w / 2).attr('ry', ry)
            .attr('fill', fill).attr('stroke', stroke).attr('stroke-width', sw);
    } else if (shape === 'queue') {
        g.append('rect')
            .attr('width', w).attr('height', h).attr('rx', Math.min(h / 2, 20))
            .attr('fill', fill).attr('stroke', stroke).attr('stroke-width', sw);
        g.append('line')
            .attr('x1', w - 12).attr('y1', 0).attr('x2', w - 12).attr('y2', h)
            .attr('stroke', stroke).attr('stroke-width', 1);
    } else {
        g.append('rect')
            .attr('width', w).attr('height', h)
            .attr('fill', fill).attr('stroke', stroke).attr('stroke-width', sw).attr('rx', 5);
    }
    drawWrappedLabel();
});

            // Add edge labels if they exist
            svg.selectAll('.edge-label')
                .data(layoutResult.edges.filter(d => d.label))
                .enter()
                .append('text')
                .attr('class', 'edge-label')
                .attr('x', d => {
                    const sourceNode = layoutResult.nodes.find(n => n.id === d.source);
                    const targetNode = layoutResult.nodes.find(n => n.id === d.target);
                    if (sourceNode && targetNode) {
                        return (sourceNode.x + sourceNode.width / 2 + targetNode.x + targetNode.width / 2) / 2;
                    }
                    return 0;
                })
                .attr('y', d => {
                    const sourceNode = layoutResult.nodes.find(n => n.id === d.source);
                    const targetNode = layoutResult.nodes.find(n => n.id === d.target);
                    if (sourceNode && targetNode) {
                        return (sourceNode.y + sourceNode.height / 2 + targetNode.y + targetNode.height / 2) / 2;
                    }
                    return 0;
                })
                .attr('text-anchor', 'middle')
                .attr('dominant-baseline', 'middle')
                .attr('fill', colors.text)
                .attr('font-family', 'Arial, sans-serif')
                .attr('font-size', '10px')
                .attr('background', isDarkMode ? '#1f1f1f' : '#ffffff')
                .text(d => d.label);

        } catch (error) {
            console.error('D2 rendering error:', error);
            container.innerHTML = `
                <div style="
                    padding: 20px;
                    background-color: ${isDarkMode ? '#2a1f1f' : '#fff2f0'};
                    border: 1px solid ${isDarkMode ? '#a61d24' : '#ffa39e'};
                    border-radius: 6px;
                    color: ${isDarkMode ? '#ff7875' : '#cf1322'};
                ">
                    <strong>D2 Rendering Error:</strong>
                    <pre style="margin: 10px 0; white-space: pre-wrap;">${escapeHtml(error instanceof Error ? error.message : 'Unknown error')}</pre>
                    <details style="margin-top: 10px;">
                        <summary style="cursor: pointer; font-weight: bold;">Show D2 Definition</summary>
                        <pre style="
                            margin: 10px 0;
                            padding: 10px;
                            background-color: ${isDarkMode ? '#1f1f1f' : '#f6f8fa'};
                            border-radius: 4px;
                            overflow-x: auto;
                            white-space: pre-wrap;
                        "><code>${escapeHtml(spec.definition ?? '')}</code></pre>
                    </details>
                </div>
            `;
        }
    }
};
