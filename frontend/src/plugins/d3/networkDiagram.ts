import { D3RenderPlugin, D3Node, D3Link, D3Style } from '../../types/d3';
import JSON5 from 'json5';
import { classifyColor, contrastRatio, compositeOver, isDarkBackground, ensureReadableFill, truncateLabel } from './chartTheme';

/** Canonical effective canvas surfaces for network contrast resolution.
 *  The network SVG paints no background, so foreground colours are resolved
 *  against the page/theme surface (light #ffffff, dark #1f1f1f). */
export const NETWORK_LIGHT_BG = '#ffffff';
export const NETWORK_DARK_BG = '#1f1f1f';

/**
 * Strip a leading/trailing markdown code fence from a `definition` payload so a
 * ```json ... ``` wrapper around otherwise-valid JSON parses (D-208 w4-04). The
 * old recovery bailed on the first char not being '{' and never saw the JSON
 * inside the fence. Pure/testable.
 */
export function stripNetworkFence(raw: string): string {
    let t = String(raw).trim();
    const matched = /^```[a-zA-Z0-9_-]*\s*\n?([\s\S]*?)\n?```$/.exec(t);
    if (matched) return matched[1].trim();
    t = t.replace(/^```[a-zA-Z0-9_-]*\s*/, '').replace(/```\s*$/, '');
    return t.trim();
}

/** Fold smart/curly quotes to ASCII (D-208 w4-05); JSON5 rejects U+201C/D/U+2018/9. */
export function normalizeNetworkSmartQuotes(raw: string): string {
    return String(raw)
        .replace(/[\u201C\u201D\u201E\u201F]/g, '"')
        .replace(/[\u2018\u2019\u201A\u201B]/g, "'");
}

/**
 * Replace object-member separator semicolons with commas OUTSIDE string
 * literals (D-208 w4-06: `{ "a": 1; "b": 2 }`). Scans char-by-char tracking
 * quote state (handling backslash escapes) so a ';' inside a string value is
 * left untouched — data is never silently corrupted. Pure/testable.
 */
export function replaceUnquotedSemicolons(body: string): string {
    let out = '';
    let inStr = false;
    let quote = '';
    let esc = false;
    for (let i = 0; i < body.length; i++) {
        const ch = body[i];
        if (inStr) {
            out += ch;
            if (esc) { esc = false; }
            else if (ch === '\\') { esc = true; }
            else if (ch === quote) { inStr = false; }
        } else if (ch === '"' || ch === "'") {
            inStr = true; quote = ch; out += ch;
        } else if (ch === ';') {
            out += ',';
        } else {
            out += ch;
        }
    }
    return out;
}

/**
 * Tolerant parse of a JSON-ish `definition` object string (D-208). Tries strict
 * `JSON.parse` first (fast path, byte-identical to the old behaviour), then
 * JSON5 (trailing commas, unquoted keys, single quotes, comments), then JSON5
 * with unquoted semicolons folded to commas — after stripping a markdown fence,
 * normalising smart quotes and slicing to the outermost {...} (so leading prose
 * / trailing `;` are ignored). Returns the parsed object, or `undefined` when
 * unrecoverable. Pure/DOM-free for unit testing.
 */
export function lenientParseNetworkObject(raw: any): any {
    if (typeof raw !== 'string') return undefined;
    const cleaned = normalizeNetworkSmartQuotes(stripNetworkFence(raw)).trim();
    if (!cleaned) return undefined;
    const first = cleaned.indexOf('{');
    const last = cleaned.lastIndexOf('}');
    if (first === -1 || last === -1 || last < first) return undefined;
    const body = cleaned.slice(first, last + 1);
    try {
        return JSON.parse(body);
    } catch (_e) { /* fall through */ }
    try {
        return JSON5.parse(body);
    } catch (_e2) { /* fall through */ }
    try {
        return JSON5.parse(replaceUnquotedSemicolons(body));
    } catch (_e3) {
        return undefined;
    }
}




export interface NetworkDiagramSpec {
    width: number;
    height: number;
    nodes: D3Node[];
    links: D3Link[];
    groups?: Array<{
        id: string;
        label: string;
        members: string[];
    }>;
    styles?: {
        [key: string]: D3Style;
    };
};

/**
 * Find the object that actually carries the graph — i.e. the first object with
 * an Array `nodes` field, searched: the object itself, the well-known envelope
 * keys (`data`/`graph`/`network`/`diagram`), then a depth-limited descent
 * (D-209). resolveNetworkSpec previously probed exactly two shapes (top-level
 * and `data`), so a graph wrapped one level deeper in a `graph` envelope
 * (network-w4-07) was never found: nodes stayed absent -> isNetworkDiagramSpec
 * false -> canHandle false -> registry finds no plugin -> the 30s empty-DOM
 * hang. Returns `undefined` when no descendant carries a `nodes` array, so a
 * genuinely node-less spec is still rejected (never hijacked). Pure/DOM-free.
 *
 * Exported for regression testing.
 */
export function findGraphContainer(obj: any, maxDepth = 4): any {
    const hasNodes = (o: any) => o && typeof o === 'object' && Array.isArray(o.nodes);
    if (hasNodes(obj)) return obj;
    if (typeof obj !== 'object' || obj === null || Array.isArray(obj)) return undefined;
    // Deterministic well-known envelopes first.
    for (const p of [obj.data, obj.graph, obj.network, obj.diagram]) {
        if (hasNodes(p)) return p;
    }
    if (maxDepth <= 0) return undefined;
    // Bounded generic descent: the first descendant object carrying `nodes`.
    for (const k of Object.keys(obj)) {
        const v = (obj as any)[k];
        if (v && typeof v === 'object' && !Array.isArray(v)) {
            const found = findGraphContainer(v, maxDepth - 1);
            if (found) return found;
        }
    }
    return undefined;
}

/**
 * Recover a structured network spec from the always-a-string `definition`
 * contract used by the render tool wrapper (app/mcp/tools/diagram_render.py),
 * which delivers `{ type: 'network', definition: '<json string>' }` and never
 * parses JSON or forwards structured fields.
 *
 * Mirrors resolveChordSpec (Issue 10) / the joint structured-recovery (Issue 2).
 * If the spec already carries structured nodes+edges/links, it is returned
 * unchanged. Otherwise, when `definition` is a JSON object string containing
 * nodes, it is parsed and nodes/edges/links/directed/width/height/groups/style
 * are lifted onto a shallow copy of the spec. Pure and DOM-free so it can be
 * unit-tested.
 *
 * Exported for regression testing.
 */
export function resolveNetworkSpec(spec: any): any {
    if (typeof spec !== 'object' || spec === null) return spec;

    // Already structured? (accept `edges` as an alias for `links`)
    const hasNodes = Array.isArray(spec.nodes) || Array.isArray(spec.data?.nodes);
    const hasEdges = Array.isArray(spec.links) || Array.isArray(spec.edges)
        || Array.isArray(spec.data?.links) || Array.isArray(spec.data?.edges);
    if (hasNodes && hasEdges) return spec;

    // Only attempt recovery from a `definition` string.
    if (typeof spec.definition !== 'string' || spec.definition.trim() === '') return spec;

    // Tolerant recovery (D-208): strip a markdown fence, normalise smart quotes,
    // slice to the outermost {...}, then strict JSON -> JSON5 (trailing commas,
    // unquoted keys, single quotes, comments) -> JSON5 with unquoted semicolons
    // folded to commas. The old path bailed when the first char was not '{'
    // (killed a ```json fence around byte-valid JSON, w4-04) and used a lone
    // strict JSON.parse, so trailing commas / unquoted keys / single & smart
    // quotes / semicolon separators left the spec node-less -> isNetworkDiagramSpec
    // false -> canHandle false -> registry finds no plugin -> D3Renderer retries
    // to the 30s hard timeout with an EMPTY DOM. Still requires a nodes array
    // below, so it never hijacks a non-network spec.
    const parsed = lenientParseNetworkObject(spec.definition);
    if (typeof parsed !== 'object' || parsed === null) return spec;

    // Locate the graph container: top-level, a well-known envelope, or one level
    // deeper in a `graph` wrapper (D-209 network-w4-07). The old two-path probe
    // (`parsed.nodes` / `parsed.data.nodes`) never saw a `graph`-enveloped graph.
    const container = findGraphContainer(parsed) || parsed;
    const pNodes = Array.isArray(container.nodes);
    const pEdges = Array.isArray(container.links) || Array.isArray(container.edges);
    if (!pNodes) return spec;

    // Lift structured fields onto a shallow copy so the plugin's canHandle/render
    // see the arrays they expect. `edges` is normalized to `links`. Fields are
    // sourced from the located container (envelope-aware), falling back to the
    // parsed root so a top-level `style`/`styles`/`nodeStyle` beside a `graph`
    // envelope is still honoured.
    const resolved: any = { ...spec };
    resolved.nodes = container.nodes;
    resolved.links = container.links || container.edges;
    const pick = (k: string) => (container[k] !== undefined ? container[k] : parsed[k]);
    if (pick('directed') !== undefined) resolved.directed = pick('directed');
    if (pick('width') !== undefined) resolved.width = pick('width');
    if (pick('height') !== undefined) resolved.height = pick('height');
    if (pick('groups') !== undefined) resolved.groups = pick('groups');
    if (pick('style') !== undefined) resolved.style = pick('style');
    if (pick('styles') !== undefined) resolved.styles = pick('styles');
    if (pick('nodeStyle') !== undefined) resolved.nodeStyle = pick('nodeStyle');
    // pEdges may be false (nodes-only graph); links then defaults to [] below.
    if (!pEdges && !Array.isArray(resolved.links)) resolved.links = [];
    return resolved;
}

/**
 * Flatten the several styling dialects a model emits into the single flat
 * `style` object the render body consumes (D-213).
 *
 * The render previously read ONLY `resolved.style`, but `NetworkDiagramSpec`
 * also declares (and resolveNetworkSpec lifts) the plural keyed `styles`
 * dialect — `{ styles: { default: { labelColor, linkColor, fontSize, ... } } }`
 * (network-w4-14) — and models emit a `nodeStyle` alias for a global node fill.
 * Both were silently dropped, so authored colours fell to plugin defaults: in
 * light the default label/link were ghost marks (a fail), in dark the same
 * dark-tuned defaults looked fine (a pass) — the clean parity split the defect
 * was filed on. This merges:
 *   - `style` (singular) — highest precedence,
 *   - the `styles.default` entry (or the first object entry) UNDER it, so an
 *     explicit `style` field still wins,
 *   - `nodeStyle.fill`/`nodeStyle.color` -> a global `nodeFill` the render uses
 *     as the default node colour when no per-node/per-group colour applies.
 * Author colours are still contrast-reconciled downstream (resolveNetworkColors
 * for label/link, ensureReadableFill for node fill); this fix only ensures they
 * are READ. Pure/DOM-free. Exported for regression testing.
 */
export function resolveNetworkStyle(resolved: any): any {
    if (typeof resolved !== 'object' || resolved === null) return {};
    const base = (typeof resolved.style === 'object' && resolved.style !== null && !Array.isArray(resolved.style))
        ? resolved.style
        : {};
    let fromStyles: any = {};
    const styles = resolved.styles;
    if (typeof styles === 'object' && styles !== null && !Array.isArray(styles)) {
        const entry = (typeof styles.default === 'object' && styles.default !== null)
            ? styles.default
            : Object.values(styles).find((v: any) => v && typeof v === 'object' && !Array.isArray(v));
        if (entry && typeof entry === 'object') fromStyles = entry;
    }
    // `styles.default` under explicit `style` (style wins on conflict).
    const merged: any = { ...fromStyles, ...base };
    // `nodeStyle` global fill alias -> `nodeFill` (only when not already set).
    const ns = resolved.nodeStyle;
    if (typeof ns === 'object' && ns !== null && !Array.isArray(ns)) {
        const nf = ns.fill ?? ns.color;
        if (merged.nodeFill === undefined && nf !== undefined) merged.nodeFill = nf;
    }
    return merged;
}

/** Max node radius; guards huge/degenerate `size` from blowing out the viewBox. */
export const NETWORK_MAX_NODE_SIZE = 1000;
/** Max link stroke width; guards huge/negative `weight`. */
export const NETWORK_MAX_LINK_WIDTH = 40;
/** Default node radius when `size` is missing or degenerate. */
export const NETWORK_DEFAULT_NODE_SIZE = 10;
/**
 * Fraction of the smaller canvas dimension a single node radius may occupy.
 * A node radius must stay a small fraction of the canvas, otherwise a huge
 * (but finite) clamped size still paints a circle that covers the whole
 * viewport — the "flat teal rectangle" total-data-loss anomaly (Issue 21):
 * `NETWORK_MAX_NODE_SIZE` (1000) is >> a 600x400 canvas, so a clamped hub
 * (1e12 -> 1000) drew a radius-1000 circle over everything, hiding all other
 * nodes/edges. The cap must therefore be derived from the canvas, not a
 * standalone constant.
 */
export const NETWORK_NODE_SIZE_CANVAS_FRACTION = 0.15;

/**
 * Max characters of a node label rendered verbatim before single-char ellipsis
 * truncation (D-199). Node labels are centred <text> with no width measurement
 * or wrapping, so a ~135-char id at 12px is ~800px wide and overruns a 620px
 * viewBox at BOTH ends (clipping the trailing index digit that distinguishes
 * nodes) while any two same-row nodes double-expose. Truncation is the cheap,
 * geometry-preserving remedy; the full label is kept in a <title> child.
 */
export const NETWORK_MAX_LABEL_CHARS = 28;

/**
 * On-screen legibility floor (px) for a node/group label after the responsive
 * downscale (D-201). Labels rendered below ~7px effective are an unreadable
 * smudge (w2-02 passes at 6.7px); 8px gives headroom.
 */
export const NETWORK_MIN_EFFECTIVE_FONT_PX = 8;
/**
 * Reference on-screen display box the responsive container renders the SVG into.
 * The plugin's `sizingConfig` pins the displayed height at 400px; the width is
 * container-driven but bounded by a typical chat-message column, so a 700x400
 * reference lets us estimate the `displaySize / viewBoxSize` downscale headlessly
 * (the browser applies `preserveAspectRatio=meet`, i.e. the MIN of the two ratios).
 */
export const NETWORK_REF_DISPLAY_WIDTH = 700;
export const NETWORK_REF_DISPLAY_HEIGHT = 400;

/**
 * Compute the nominal `font-size` (in viewBox units) to apply to labels so the
 * ON-SCREEN size clears the legibility floor after the responsive downscale
 * (D-201). Two failure modes share one clamp:
 *  - a large viewBox is downscaled (w2-07: a 3000px-wide viewBox shrinks 12px to
 *    ~2.8px on a ~700px column), so the nominal size is boosted by 1/downscale;
 *  - a tiny nominal size on a non-downscaled canvas renders at a true sub-legible
 *    size (w2-14: fontSize 4 @ 600px), so the floor applies directly.
 * `nominal >= floor / downscale` covers both. The downscale estimate is a
 * heuristic (true display width is unknown headlessly); it is never < the
 * caller's requested size, so it only ever ENLARGES an at-risk label and leaves
 * a comfortable one unchanged. Pure/DOM-free. Exported for regression testing.
 */
export function effectiveNetworkFontSize(requested: any, width?: number, height?: number): number {
    const req = Number.isFinite(Number(requested)) && Number(requested) > 0 ? Number(requested) : 12;
    const w = Number.isFinite(Number(width)) && Number(width) > 0 ? Number(width) : NETWORK_REF_DISPLAY_WIDTH;
    const h = Number.isFinite(Number(height)) && Number(height) > 0 ? Number(height) : NETWORK_REF_DISPLAY_HEIGHT;
    const downscale = Math.min(1, NETWORK_REF_DISPLAY_WIDTH / w, NETWORK_REF_DISPLAY_HEIGHT / h);
    return Math.max(req, NETWORK_MIN_EFFECTIVE_FONT_PX / downscale);
}

/**
 * Derive a group's dashed bounding rect from the CURRENT positions of its
 * member nodes (D-202).
 *
 * WHY: the old renderer positioned every group with a hardcoded
 * `d.id === 'modem_board' ? 180/350 : 680/200` ternary at fixed y=50 h=500 that
 * ignored `members` entirely, so every id that was not 'modem_board' drew at the
 * IDENTICAL x=680 w=200 box (caption at x=700 y=80) — 30 declared groups
 * superimposed into one dashed box and 30 captions into one illegible blob
 * (w2-13), and the fixed y=50..550 rect was truncated by the 400px container.
 * This computes the min/max of the member node centres (± their radius) and pads,
 * so each group frames its OWN members. Returns `null` when no member resolves to
 * a positioned node (the caller then skips that group's rect rather than drawing
 * a bogus one). Pure/DOM-free. Exported for regression testing.
 */
export function computeGroupRect(
    members: any[],
    nodeById: Map<string, any>,
    padding = 16,
    defaultRadius: number = NETWORK_DEFAULT_NODE_SIZE,
): { x: number; y: number; width: number; height: number } | null {
    if (!Array.isArray(members) || members.length === 0 || !(nodeById instanceof Map)) return null;
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    let found = 0;
    for (const m of members) {
        const n = nodeById.get(String(m));
        if (!n || typeof n !== 'object') continue;
        const x = Number(n.x), y = Number(n.y);
        if (!Number.isFinite(x) || !Number.isFinite(y)) continue;
        const r = Number.isFinite(Number(n.size)) && Number(n.size) > 0 ? Number(n.size) : defaultRadius;
        minX = Math.min(minX, x - r); minY = Math.min(minY, y - r);
        maxX = Math.max(maxX, x + r); maxY = Math.max(maxY, y + r);
        found++;
    }
    if (found === 0) return null;
    return {
        x: minX - padding,
        y: minY - padding,
        width: (maxX - minX) + padding * 2,
        height: (maxY - minY) + padding * 2,
    };
}

/**
 * Map the obvious model-emitted field aliases onto the canonical
 * `id`/`source`/`target` names so a graph is not rejected wholesale for a
 * single non-canonical row (D-210). A node lacking a valid `id` adopts the
 * first valid `name`/`key`/`nodeId`; a link lacking a valid `source`/`target`
 * adopts the first valid `from`/`src` / `to`/`dst`/`dest`. Rows that still have
 * no usable endpoint are left as-is and dropped later by
 * `sanitizeNetworkGraph`; nothing is invented. Does not mutate input (returns
 * shallow copies of changed rows). Pure/DOM-free.
 *
 * Exported for regression testing.
 */
export function normalizeNetworkAliases(
    rawNodes: any[],
    rawLinks: any[]
): { nodes: any[]; links: any[] } {
    const nodes = (Array.isArray(rawNodes) ? rawNodes : []).map((n: any) => {
        if (!n || typeof n !== 'object') return n;
        if (isValidNetworkId(n.id)) return n;
        for (const k of ['name', 'key', 'nodeId']) {
            if (isValidNetworkId(n[k])) return { ...n, id: n[k] };
        }
        return n;
    });
    const links = (Array.isArray(rawLinks) ? rawLinks : []).map((l: any) => {
        if (!l || typeof l !== 'object') return l;
        let out = l;
        if (!isValidNetworkId(l.source)) {
            for (const k of ['from', 'src', 'start']) {
                if (isValidNetworkId(l[k])) { out = { ...out, source: l[k] }; break; }
            }
        }
        if (!isValidNetworkId(out.target)) {
            for (const k of ['to', 'dst', 'dest']) {
                if (isValidNetworkId(l[k])) { out = { ...out, target: l[k] }; break; }
            }
        }
        return out;
    });
    return { nodes, links };
}

/**
 * Compute the effective per-node radius cap for a given canvas.
 *
 * Returns a value that is BOTH <= `NETWORK_MAX_NODE_SIZE` (the absolute guard)
 * and <= a small fraction of the smaller canvas dimension, so a single node can
 * never cover the whole viewport. Falls back to a sane floor when the canvas is
 * missing/degenerate. Pure/DOM-free for unit testing.
 *
 * Exported for regression testing.
 */
export function networkNodeSizeCap(width?: number, height?: number): number {
    const w = Number.isFinite(Number(width)) && Number(width) > 0 ? Number(width) : 600;
    const h = Number.isFinite(Number(height)) && Number(height) > 0 ? Number(height) : 400;
    const canvasCap = Math.min(w, h) * NETWORK_NODE_SIZE_CANVAS_FRACTION;
    // Never below the default radius, never above the absolute guard.
    return Math.max(NETWORK_DEFAULT_NODE_SIZE, Math.min(NETWORK_MAX_NODE_SIZE, canvasCap));
}

/**
 * Coerce a raw network graph into safe, renderable arrays:
 *  - accepts `edges` as an alias for `links`
 *  - clamps degenerate node `size` (non-finite / <=0 -> default; > cap -> cap)
 *  - filters dangling edges whose source/target id is absent from `nodes`
 *    (an unresolved endpoint would otherwise draw a line to (0,0) or crash a
 *    force lookup — mirrors the Issue-3 forceLink dangling-edge filter)
 *  - clamps degenerate link `weight` used for stroke-width
 * `maxNodeSize` is the effective node-radius cap (defaults to the absolute
 * guard, but callers should pass a canvas-relative cap from
 * `networkNodeSizeCap()` so a huge finite size cannot cover the viewport).
 * Returns new arrays; does not mutate input. Pure/DOM-free for unit testing.
 *
 * Exported for regression testing.
 */
export function sanitizeNetworkGraph(
    rawNodes: any[],
    rawLinks: any[],
    maxNodeSize: number = NETWORK_MAX_NODE_SIZE
): { nodes: any[]; links: any[] } {
    // Guard the cap itself: non-finite/<=0 collapses to the absolute guard.
    const nodeCap = Number.isFinite(maxNodeSize) && maxNodeSize > 0
        ? Math.min(maxNodeSize, NETWORK_MAX_NODE_SIZE)
        : NETWORK_MAX_NODE_SIZE;
    const nodes = (Array.isArray(rawNodes) ? rawNodes : []).map((n: any) => {
        const out = { ...n };
        const size = Number(n?.size);
        if (!Number.isFinite(size) || size <= 0) {
            out.size = undefined; // fall back to the render default (10)
        } else if (size > nodeCap) {
            out.size = nodeCap;
        } else {
            out.size = size;
        }
        return out;
    });

    // Endpoint-type reconciliation (D-211): a node `id` may be a JSON number
    // while a link endpoint is the string form of the same value (or the
    // reverse). Strict `Set` membership keeps `5 !== "5"`, so every such edge
    // would be silently discarded and the plugin would report SUCCESS with all
    // edges gone. Key the lookup by `String(id)` and CANONICALISE each surviving
    // endpoint back to the matching node's actual id value, so both the render's
    // strict `n.id === l.source` line lookup and d3-forceLink's `nodeById.get`
    // resolve regardless of the original JSON type slip.
    const idByString = new Map<string, any>();
    for (const n of nodes) idByString.set(String(n.id), n.id);
    const links = (Array.isArray(rawLinks) ? rawLinks : [])
        .filter((l: any) => l && idByString.has(String(l.source)) && idByString.has(String(l.target)))
        .map((l: any) => {
            const out = { ...l };
            out.source = idByString.get(String(l.source));
            out.target = idByString.get(String(l.target));
            const w = Number(l?.weight);
            if (!Number.isFinite(w) || w <= 0) {
                out.weight = undefined; // fall back to the render default (1)
            } else if (w > NETWORK_MAX_LINK_WIDTH) {
                out.weight = NETWORK_MAX_LINK_WIDTH;
            } else {
                out.weight = w;
            }
            return out;
        });

    return { nodes, links };
}

/**
 * Clamp every node's simulated position so its full circle stays inside the
 * visible viewport `[0,width] x [0,height]`.
 *
 * WHY (Issue 31): the force layout uses `forceManyBody(-200)` repulsion with no
 * bounding force. Nodes that end up with NO surviving link (their only edges
 * were dangling ghost refs filtered by `sanitizeNetworkGraph`, or they were
 * declared isolated) are pushed away from the charged cluster with nothing to
 * pull them back, so they land far outside `[0,w]x[0,h]` and are silently
 * CLIPPED by the SVG viewBox — total, invisible data loss (dropped
 * isolated/leaf/tree_child_b nodes). Separately, a large-but-clamped hub whose
 * centre sits at y < radius gets its top clipped off the canvas edge.
 *
 * A post-simulation position clamp fixes the WHOLE class: any node — connected,
 * disconnected, or ejected to +/-Infinity/NaN by a degenerate force input — is
 * pinned back so its entire radius is visible. `radiusOf` mirrors the render's
 * radius default (`d.size || 10`). Mutates node x/y in place (they are the
 * simulation's own working objects) and also returns the array for testing.
 * A non-finite coordinate (NaN/Infinity from a poisoned tick) is recentered.
 * Pure/DOM-free for unit testing.
 *
 * Exported for regression testing.
 */
export function clampNodePositionsToViewport(
    nodes: any[],
    width: number,
    height: number,
    defaultRadius: number = NETWORK_DEFAULT_NODE_SIZE
): any[] {
    if (!Array.isArray(nodes)) return [];
    const w = Number.isFinite(width) && width > 0 ? width : 600;
    const h = Number.isFinite(height) && height > 0 ? height : 400;
    for (const n of nodes) {
        if (!n || typeof n !== 'object') continue;
        const r = Number.isFinite(Number(n.size)) && Number(n.size) > 0
            ? Number(n.size)
            : defaultRadius;
        // A node larger than half the canvas cannot fully fit; still keep its
        // centre inside so it is at least partially and centrally visible.
        const rx = Math.min(r, w / 2);
        const ry = Math.min(r, h / 2);
        let x = Number(n.x);
        let y = Number(n.y);
        if (!Number.isFinite(x)) x = w / 2;
        if (!Number.isFinite(y)) y = h / 2;
        n.x = Math.max(rx, Math.min(w - rx, x));
        n.y = Math.max(ry, Math.min(h - ry, y));
    }
    return nodes;
}


/**
 * A network node `id` / link `source`/`target` endpoint is valid if it is a
 * non-empty string OR a finite number. JSON permits numeric ids, d3-force
 * resolves them, and `sanitizeNetworkGraph`'s `Set`-based dangling-link filter
 * keeps `5 !== "5"` distinct (strict equality), so numeric endpoints are a
 * legitimate, semantically-safe input shape.
 *
 * WHY (Issue 47): `isNetworkDiagramSpec` gated `canHandle` with
 * `typeof id === 'string'`. A single numeric id (`5`, `1e15`) made `.every(...)`
 * return false -> canHandle false -> registry finds no plugin -> D3Renderer
 * retries to the 30s timeout with zero output (the same silent-hang signature
 * as Issue 43). Widening to accept finite numbers fixes the whole class.
 *
 * The guard stays STRICT: NaN/Infinity, objects, arrays, null, undefined,
 * booleans and empty strings are still rejected (so it is not a catch-all —
 * a malformed graph is still declined at detection rather than crashing the
 * force layout downstream).
 *
 * Pure/DOM-free. Exported for regression testing.
 */
export function isValidNetworkId(v: any): boolean {
    if (typeof v === 'string') return v.length > 0;
    if (typeof v === 'number') return Number.isFinite(v);
    return false;
}

/**
 * Above this node count a force layout that lacks authored coordinates is
 * abandoned in favour of a deterministic grid (D-197). d3-force with
 * `forceManyBody(-200)` and a 300-tick cap ejects large un-anchored graphs
 * off-canvas; `clampNodePositionsToViewport` then pins every ejected node to the
 * viewport edge, converting off-canvas clipping into on-canvas COINCIDENCE — a
 * solid perimeter band of stacked circles with an empty interior (40% hidden at
 * 200 nodes, 76% at 250). Element count is not the ceiling (300 nodes with
 * authored x/y render perfectly); the *layout* is, so above the threshold a
 * grid guarantees every node occupies a distinct, visible cell.
 */
export const NETWORK_FORCE_LAYOUT_MAX_NODES = 80;

/**
 * Deterministically place nodes on a grid that fills `[0,width]x[0,height]`,
 * mutating each node's x/y in place (they are the render's own working objects)
 * and returning the array for testing. Columns are chosen to approximate the
 * canvas aspect ratio so cells stay squarish. Every node lands in its own cell
 * centre, so no two nodes coincide and none is ejected off-canvas — the fix for
 * the large-graph perimeter-pileup / empty-interior anomaly (D-197). Pure/DOM-free.
 *
 * Exported for regression testing.
 */
export function computeGridLayout(nodes: any[], width: number, height: number): any[] {
    if (!Array.isArray(nodes)) return [];
    const w = Number.isFinite(width) && width > 0 ? width : 600;
    const h = Number.isFinite(height) && height > 0 ? height : 400;
    const n = nodes.length;
    if (n === 0) return nodes;
    const cols = Math.max(1, Math.ceil(Math.sqrt(n * (w / h))));
    const rows = Math.max(1, Math.ceil(n / cols));
    const cellW = w / cols;
    const cellH = h / rows;
    for (let i = 0; i < n; i++) {
        const node = nodes[i];
        if (!node || typeof node !== 'object') continue;
        const c = i % cols;
        const r = Math.floor(i / cols);
        node.x = (c + 0.5) * cellW;
        node.y = (r + 0.5) * cellH;
    }
    return nodes;
}

export interface NetworkColors {
    /** Effective canvas the foreground is resolved against. */
    effectiveBg: string;
    /** Whether that canvas is dark. */
    darkCanvas: boolean;
    /** Node/link text colour, >= 4.5:1 against effectiveBg. */
    labelColor: string;
    /** Link stroke colour, whose composite at `linkOpacity` clears 3:1. */
    linkColor: string;
    /** Link stroke opacity actually used. */
    linkOpacity: number;
}

function parseHexRgb(hex: string): [number, number, number] | null {
    let h = String(hex).trim().replace(/^#/, '');
    if (h.length === 3) h = h.split('').map(c => c + c).join('');
    if (h.length !== 6 || /[^0-9a-fA-F]/.test(h)) return null;
    return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)];
}
function rgbToHexStr(rgb: [number, number, number]): string {
    return '#' + rgb.map(v => Math.max(0, Math.min(255, Math.round(v))).toString(16).padStart(2, '0')).join('');
}

/**
 * Resolve a link stroke whose ON-SCREEN composite (over `bgHex` at `opacity`)
 * clears `minRatio`:1. A caller stroke below the floor — including a
 * dark-tuned colour ghosting on the dark canvas at low opacity (D-203 w1-06:
 * #4a4a4a@0.85 -> 1.69:1) — is nudged toward the canvas-opposite until the
 * COMPOSITE (not the nominal colour) clears the floor. Named / unresolvable
 * colours use `fallback`. Pure/testable.
 */
export function readableLinkStroke(
    input: any,
    bgHex: string,
    opacity: number,
    fallback: string,
    minRatio = 3,
): string {
    const c = classifyColor(input);
    const base = c && c.hex ? c.hex : fallback;
    const rgb = parseHexRgb(base) || parseHexRgb(fallback)!;
    if (contrastRatio(compositeOver(base, bgHex, opacity), bgHex) >= minRatio) return base;
    const target: [number, number, number] = isDarkBackground(bgHex) ? [255, 255, 255] : [0, 0, 0];
    for (let t = 0.15; t <= 1.0001; t += 0.15) {
        const cand = rgbToHexStr([
            rgb[0] + (target[0] - rgb[0]) * t,
            rgb[1] + (target[1] - rgb[1]) * t,
            rgb[2] + (target[2] - rgb[2]) * t,
        ]);
        if (contrastRatio(compositeOver(cand, bgHex, opacity), bgHex) >= minRatio) return cand;
    }
    return rgbToHexStr(target);
}

/**
 * Resolve theme-aware label and link colours from the EFFECTIVE canvas (D-203).
 *
 * The old renderer used two static literals — `style.labelColor || '#ccc'` and
 * `style.linkColor || '#999'` — with no per-theme resolution. #ccc measures
 * 1.61:1 on the light canvas (invisible) and an authored dark literal (#000,
 * #111) fails on dark; no single literal clears 4.5:1 against both #ffffff and
 * #1f1f1f. Defaults are therefore chosen from the resolved background luminance
 * (never a blind constant swap) and a caller colour is reconciled toward the
 * WCAG floor against that same surface. Pure/testable.
 */
export function resolveNetworkColors(isDarkMode: boolean, style: any = {}): NetworkColors {
    const bgClass = classifyColor(style?.background);
    const effectiveBg = bgClass && bgClass.hex ? bgClass.hex : (isDarkMode ? NETWORK_DARK_BG : NETWORK_LIGHT_BG);
    const darkCanvas = isDarkBackground(effectiveBg);
    // Labels: 4.5 text floor.
    const defaultLabel = darkCanvas ? '#e0e0e0' : '#333333';
    const labelColor = style?.labelColor
        ? (classifyColor(style.labelColor)
            ? readableTextColor(style.labelColor, effectiveBg, defaultLabel)
            : defaultLabel)
        : defaultLabel;
    // Links: 3 graphical floor on the composite. Raise the default opacity to
    // 0.9 only when the caller supplies neither colour nor opacity (the old 0.6
    // default composited a #999 stroke to 1.78:1 light / 2.96:1 dark — ghost
    // hairlines). An explicit caller opacity is always honoured; the stroke then
    // compensates so the composite still clears the floor.
    const defaultLink = darkCanvas ? '#cfcfcf' : '#666666';
    const linkOpacity = typeof style?.linkOpacity === 'number'
        ? style.linkOpacity
        : (style?.linkColor ? 0.6 : 0.9);
    const linkColor = readableLinkStroke(style?.linkColor || defaultLink, effectiveBg, linkOpacity, defaultLink);
    // D-204: at a low caller opacity even the canvas-opposite stroke picked by
    // readableLinkStroke cannot clear the 3:1 graphical floor (a #999 default at
    // 0.35 composites to ~1.38:1 — the whole topology becomes ghost lines), and
    // opacity is then the only remaining lever. Raise it toward 1 until the
    // composite clears the floor. The caller's opacity is honoured as a floor of
    // intent (never lowered) but is not allowed to erase the edges.
    let effOpacity = linkOpacity;
    if (contrastRatio(compositeOver(linkColor, effectiveBg, effOpacity), effectiveBg) < 3) {
        for (let o = effOpacity + 0.05; o <= 1.0001; o += 0.05) {
            effOpacity = Math.min(1, o);
            if (contrastRatio(compositeOver(linkColor, effectiveBg, effOpacity), effectiveBg) >= 3) break;
        }
    }
    return { effectiveBg, darkCanvas, labelColor, linkColor, linkOpacity: effOpacity };
}

/** Nudge a text colour to >= 4.5:1 against `bgHex` (named/unresolvable -> fallback). */
function readableTextColor(input: any, bgHex: string, fallback: string): string {
    const c = classifyColor(input);
    if (!c) return fallback;
    if (c.named) return c.named;
    const hex = c.hex!;
    if (contrastRatio(hex, bgHex) >= 4.5) return hex;
    const rgb = parseHexRgb(hex)!;
    const target: [number, number, number] = isDarkBackground(bgHex) ? [255, 255, 255] : [0, 0, 0];
    for (let t = 0.2; t <= 1.0001; t += 0.2) {
        const cand = rgbToHexStr([
            rgb[0] + (target[0] - rgb[0]) * t,
            rgb[1] + (target[1] - rgb[1]) * t,
            rgb[2] + (target[2] - rgb[2]) * t,
        ]);
        if (contrastRatio(cand, bgHex) >= 4.5) return cand;
    }
    return fallback;
}

const isNetworkDiagramSpec = (spec: any): spec is NetworkDiagramSpec => {
    const resolved = resolveNetworkSpec(spec);
    const rawNodes = resolved?.nodes || resolved?.data?.nodes;
    const rawLinks = resolved?.links || resolved?.edges || resolved?.data?.links || resolved?.data?.edges;
    if (
        typeof resolved !== 'object' ||
        resolved === null ||
        resolved.type !== 'network' || // network-type discriminator (prevents hijack)
        !Array.isArray(rawNodes) ||
        !Array.isArray(rawLinks)
    ) {
        return false;
    }
    // Tolerant detection (D-210): a SINGLE malformed row — a missing endpoint,
    // an aliased field (`to`/`from`), or a node carrying `name` instead of `id`
    // — must NOT reject the whole graph (the old `.every(...)` did, so canHandle
    // returned false -> registry finds no plugin -> D3Renderer retries to the 30s
    // timeout with an EMPTY DOM). Map the obvious aliases, then require only that
    // at least one node is renderable; `sanitizeNetworkGraph` drops the still-
    // unusable rows at render. The `type === 'network'` gate above already keeps
    // this from claiming a non-network spec.
    const { nodes } = normalizeNetworkAliases(rawNodes, rawLinks);
    return nodes.length === 0 || nodes.some((n: any) => n != null && isValidNetworkId(n.id));
};

export const networkDiagramPlugin: D3RenderPlugin = {
    name: 'network-diagram',
    priority: 1,
    sizingConfig: {
        sizingStrategy: 'responsive',
        // D-052: the container previously pinned `height: '400px'` with
        // `needsDynamicHeight: false`, so D3Renderer forced the host box to a
        // fixed 400px (it sets `container.style.height = needsDynamicHeight
        // ? 'auto' : '<h>px'`). A taller authored/responsive SVG (e.g. height
        // 3000) then rendered at natural size inside that 400px box and every
        // node below 400px was silently cropped (visible fraction ~= 400/height:
        // 17% lost at height 600, 87% at height 3000) — the render still reported
        // success. Matching every other D3 engine (chord/vega/plotly/graphviz),
        // `needsDynamicHeight: true` lets the container height follow the SVG
        // content (D3Renderer applies 'auto' + maxHeight 'none'), so the full
        // graph is shown; the responsive viewBox still scales to the column
        // width and the D-201 font-floor keeps downscaled labels legible. No
        // hardcoded sub-viewport height is pinned.
        needsDynamicHeight: true,
        needsOverflowVisible: false,
        observeResize: false,
        containerStyles: {
            overflow: 'auto'
        }
    },
    canHandle: isNetworkDiagramSpec,
    render: (container: HTMLElement, d3: any, spec: any, isDarkMode: boolean = false) => {
        console.debug('Network diagram plugin rendering:', { spec, isDarkMode });

        // Recover structured fields from a JSON `definition` string and accept
        // `edges` as an alias for `links` before validating.
        const resolved = resolveNetworkSpec(spec);
        if (!isNetworkDiagramSpec(resolved)) {
            throw new Error('Invalid network diagram specification');
        }

        const rawNodes = (resolved as any).nodes || (resolved as any).data?.nodes || [];
        const rawLinks = (resolved as any).links || (resolved as any).edges
            || (resolved as any).data?.links || (resolved as any).data?.edges || [];
        const width = (resolved as any).width || 600;
        const height = (resolved as any).height || 400;
        // Cap node radius relative to the canvas so a single huge (but finite,
        // post-clamp) node cannot paint a circle over the whole viewport
        // (Issue 21: hub 1e12 -> radius-1000 circle filled a 600x400 canvas).
        // Map obvious field aliases (`name`->id, `to`/`from`->target/source)
        // before sanitizing so a partially non-canonical graph renders its good
        // rows instead of being rejected wholesale (D-210).
        const { nodes: aliasNodes, links: aliasLinks } = normalizeNetworkAliases(rawNodes, rawLinks);
        const { nodes: safeNodes, links: safeLinks } =
            sanitizeNetworkGraph(aliasNodes, aliasLinks, networkNodeSizeCap(width, height));

        console.debug('Network diagram render:', {
            nodeCount: safeNodes.length,
            linkCount: safeLinks.length,
            groupCount: (resolved as any).groups?.length
        });
        try {
            // Clear existing content first, then build new SVG
            d3.select(container).selectAll('*').remove();

            const svg = d3.select(container)
                .append('svg')
                .attr('width', width)
                .attr('height', height)
                .attr('viewBox', [0, 0, width, height]);

            const groups = (resolved as any).groups;

            // Flatten the `style` / plural `styles` / `nodeStyle` dialects into
            // one flat style object so an author who used the deprecated plural
            // keyed form or a `nodeStyle` alias is honoured instead of dropped to
            // defaults (D-213).
            const style = resolveNetworkStyle(resolved);
            // Theme-resolve label + link colours from the effective canvas so the
            // default label is not the invisible #ccc on light / an authored dark
            // label is not lost on dark, and default edges are not ghost hairlines
            // (D-203). Never a blind constant swap — see resolveNetworkColors.
            const netColors = resolveNetworkColors(isDarkMode, style);
            // Clamp the label font to an on-screen legibility floor accounting for
            // the responsive viewBox downscale (D-201): a large viewBox shrinks a
            // 12px label below legibility and a tiny nominal size renders sub-pixel.
            const fontSizePx = effectiveNetworkFontSize(style.fontSize, width, height);

            // If nodes lack explicit x/y coordinates, run a force simulation so a
            // bare nodes+links graph (the LLM-friendly form) gets a usable layout
            // instead of collapsing every node onto (0,0).
            const needsLayout = safeNodes.some((n: any) => n.x === undefined || n.y === undefined);
            if (needsLayout && safeNodes.length > NETWORK_FORCE_LAYOUT_MAX_NODES) {
                // Large un-anchored graph: a force layout ejects nodes off-canvas
                // and the viewport clamp then stacks them into a perimeter band
                // with an empty interior (D-197). A deterministic grid guarantees
                // every node a distinct, visible cell.
                computeGridLayout(safeNodes, width, height);
            } else if (needsLayout && typeof d3.forceSimulation === 'function') {
                // Charge magnitude is scaled DOWN as the node count grows so a
                // moderately large graph is not repelled off-canvas (and then
                // clamped into coincidence); forceCollide keeps circles from
                // overlapping/stacking even after the clamp (D-197).
                const chargeStrength = -200 * Math.min(1, 40 / Math.max(1, safeNodes.length));
                const radiusOf = (n: any) =>
                    (Number.isFinite(Number(n?.size)) && Number(n.size) > 0
                        ? Number(n.size)
                        : NETWORK_DEFAULT_NODE_SIZE) + 4;
                const sim = d3.forceSimulation(safeNodes)
                    .force('link', d3.forceLink(safeLinks).id((n: any) => n.id).distance(80))
                    .force('charge', d3.forceManyBody().strength(chargeStrength))
                    .force('center', d3.forceCenter(width / 2, height / 2))
                    .force('collide', d3.forceCollide().radius(radiusOf).iterations(2))
                    .stop();
                const ticks = Math.min(300, Math.max(50, safeNodes.length * 4));
                for (let i = 0; i < ticks; i++) sim.tick();
            }

            // Clamp every node inside the viewport so disconnected/ejected nodes
            // (repelled off-canvas with no link to pull them back) and a large
            // hub whose radius overhangs the edge are never silently clipped by
            // the SVG viewBox (Issue 31: catastrophic silent data loss).
            clampNodePositionsToViewport(safeNodes, width, height);

            // Draw group containers AFTER node positions are known so each group's
            // dashed rect is derived from its OWN members' positions (D-202), not a
            // hardcoded per-id ternary that stacked every non-'modem_board' group
            // into one box. Drawn before links/nodes so it sits behind them. Rect
            // stroke + caption use the theme-resolved label colour so the boundary
            // is not the old #666 (invisible on the dark surface).
            if (groups?.length) {
                const nodeById = new Map<string, any>();
                for (const n of safeNodes) nodeById.set(String(n.id), n);
                const groupRects = groups
                    .map((g: any) => ({ group: g, rect: computeGroupRect(g?.members, nodeById) }))
                    .filter((gr: any) => gr.rect !== null);
                const boards = svg.selectAll('.board')
                    .data(groupRects)
                    .enter()
                    .append('g')
                    .attr('class', 'board');
                boards.append('rect')
                    .attr('x', (gr: any) => gr.rect.x)
                    .attr('y', (gr: any) => gr.rect.y)
                    .attr('width', (gr: any) => gr.rect.width)
                    .attr('height', (gr: any) => gr.rect.height)
                    .attr('fill', 'none')
                    .attr('stroke', netColors.labelColor)
                    .attr('stroke-dasharray', '5,5');
                boards.append('text')
                    .attr('x', (gr: any) => gr.rect.x + 4)
                    .attr('y', (gr: any) => gr.rect.y + fontSizePx + 2)
                    .text((gr: any) => String(gr.group.label ?? gr.group.id ?? ''))
                    .attr('fill', netColors.labelColor)
                    .attr('font-size', fontSizePx);
            }

            // Draw links
            svg.selectAll('.link')
                .data(safeLinks)
                .enter()
                .append('line')
                .attr('class', 'link')
                .attr('x1', (l: any) => {
                    const n = typeof l.source === 'object' ? l.source : safeNodes.find((n: any) => n.id === l.source);
                    return n?.x ?? 0;
                })
                .attr('y1', (l: any) => {
                    const n = typeof l.source === 'object' ? l.source : safeNodes.find((n: any) => n.id === l.source);
                    return n?.y ?? 0;
                })
                .attr('x2', (l: any) => {
                    const n = typeof l.target === 'object' ? l.target : safeNodes.find((n: any) => n.id === l.target);
                    return n?.x ?? 0;
                })
                .attr('y2', (l: any) => {
                    const n = typeof l.target === 'object' ? l.target : safeNodes.find((n: any) => n.id === l.target);
                    return n?.y ?? 0;
                })
                .attr('stroke', () => netColors.linkColor)
                .attr('stroke-opacity', () => netColors.linkOpacity)
                .attr('stroke-width', (l: any) => l.weight || 1);

            // Draw nodes
            const nodeColors = style.nodeColors || {};
            // Validate/substitute node fills (D-212): a colour that is structurally
            // legal but semantically empty must not land on the background. Node
            // 'transparent' (fill == bg, 1.00:1) erases the circle; an unresolvable
            // token — var(--ziya-node-accent), bare 'theme.node.fill' — is rejected
            // by the browser and falls back to the CSS INITIAL value (fill:black),
            // turning nodes black (and invisible on dark). classifyColor returns
            // null for both classes; we then substitute a theme-readable default.
            // A valid author colour (hex / rgb / named) is honoured verbatim.
            // Default node fill honours a global `nodeStyle.fill` (D-213 -> style.nodeFill)
            // when supplied, else the plugin teal — both contrast-reconciled to the
            // effective canvas.
            const defaultNodeFill = ensureReadableFill(
                classifyColor(style.nodeFill) ? style.nodeFill : '#69b3a2',
                netColors.effectiveBg, '#69b3a2', 3);
            // Contrast-validate the per-node/per-group fill against the active
            // background (D-206): a pale categorical palette entry (e.g. #edc948
            // = 1.61:1 on white) applied verbatim is an invisible disc on light
            // that the white node outline cannot rescue. ensureReadableFill nudges
            // a below-floor hex toward the surface-opposite until it clears 3:1
            // (dark-tuned entries already clear it and pass through unchanged, so
            // no dark regression); transparent/token/absent -> defaultNodeFill;
            // a valid, sufficiently-contrasting author colour is honoured verbatim.
            const resolveNodeFill = (d: any): string =>
                ensureReadableFill(nodeColors[(d as any).group] ?? d.color,
                    netColors.effectiveBg, defaultNodeFill, 3);
            const nodeGroups = svg.selectAll('.node')
                .data(safeNodes)
                .enter()
                .append('g')
                .attr('class', 'node')
                .attr('transform', (d: any) => `translate(${d.x ?? 0},${d.y ?? 0})`);

            nodeGroups.append('circle')
                .attr('r', (d: any) => d.size || 10)
                .attr('fill', (d: any) => resolveNodeFill(d))
                .attr('stroke', '#fff')
                .attr('stroke-width', 1.5);

            const haloWidth = Math.max(2, fontSizePx * 0.18);
            const labelNodes = nodeGroups.append('text')
                .attr('dy', (d: any) => -(d.size || 10) - 5)
                .attr('text-anchor', 'middle')
                .attr('fill', () => netColors.labelColor)
                .attr('font-size', fontSizePx)
                // Paint a canvas-coloured halo UNDER the glyph fill so a label
                // stays legible where it overlaps a neighbouring circle or the
                // edge fan at high node count (D-200). paint-order:stroke draws
                // the stroke first, so the fill is never eaten by its own halo.
                .attr('stroke', () => netColors.effectiveBg)
                .attr('stroke-width', haloWidth)
                .attr('stroke-linejoin', 'round')
                .attr('paint-order', 'stroke')
                .text((d: any) => truncateLabel(String(d.label ?? d.id), NETWORK_MAX_LABEL_CHARS));
            // Keep the full label reachable on hover / for assistive tech; the
            // visible <text> is ellipsis-truncated so a ~135-char id no longer
            // overruns the viewBox at both ends or double-exposes onto a
            // same-row neighbour (D-199).
            labelNodes.append('title').text((d: any) => String(d.label ?? d.id));
        } catch (error) {
            console.error('Network diagram render error:', error);
            // Clean up on error
            d3.select(container).selectAll('*').remove();
            throw error;
        }
    }
};
