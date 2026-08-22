import { D3RenderPlugin, D3Node, D3Link, D3Style } from '../../types/d3';




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

    const pNodes = Array.isArray(parsed.nodes) || Array.isArray(parsed.data?.nodes);
    const pEdges = Array.isArray(parsed.links) || Array.isArray(parsed.edges)
        || Array.isArray(parsed.data?.links) || Array.isArray(parsed.data?.edges);
    if (!pNodes) return spec;

    // Lift structured fields onto a shallow copy so the plugin's canHandle/render
    // see the arrays they expect. `edges` is normalized to `links`.
    const resolved: any = { ...spec };
    resolved.nodes = parsed.nodes || parsed.data?.nodes;
    resolved.links = parsed.links || parsed.edges || parsed.data?.links || parsed.data?.edges;
    if (parsed.directed !== undefined) resolved.directed = parsed.directed;
    if (parsed.width !== undefined) resolved.width = parsed.width;
    if (parsed.height !== undefined) resolved.height = parsed.height;
    if (parsed.groups !== undefined) resolved.groups = parsed.groups;
    if (parsed.style !== undefined) resolved.style = parsed.style;
    if (parsed.styles !== undefined) resolved.styles = parsed.styles;
    // pEdges may be false (nodes-only graph); links then defaults to [] below.
    if (!pEdges && !Array.isArray(resolved.links)) resolved.links = [];
    return resolved;
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

    const ids = new Set(nodes.map((n: any) => n.id));
    const links = (Array.isArray(rawLinks) ? rawLinks : [])
        .filter((l: any) => l && ids.has(l.source) && ids.has(l.target))
        .map((l: any) => {
            const out = { ...l };
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

const isNetworkDiagramSpec = (spec: any): spec is NetworkDiagramSpec => {
    const resolved = resolveNetworkSpec(spec);
    const nodes = resolved?.nodes || resolved?.data?.nodes;
    const links = resolved?.links || resolved?.edges || resolved?.data?.links || resolved?.data?.edges;
    return (
        typeof resolved === 'object' &&
        resolved !== null &&
        resolved.type === 'network' && // Check for network type
        Array.isArray(nodes) &&
        Array.isArray(links) &&
        nodes.length >= 0 &&
        nodes.every((n: any) => n != null && isValidNetworkId(n.id)) &&
        links.length >= 0 &&
        links.every((l: any) => l != null && isValidNetworkId(l.source) && isValidNetworkId(l.target))
    );
};

export const networkDiagramPlugin: D3RenderPlugin = {
    name: 'network-diagram',
    priority: 1,
    sizingConfig: {
        sizingStrategy: 'responsive',
        needsDynamicHeight: false,
        needsOverflowVisible: false,
        observeResize: false,
        containerStyles: {
            height: '400px',
            overflow: 'auto'
        }
    },
    canHandle: isNetworkDiagramSpec,
    render: (container: HTMLElement, d3: any, spec: any) => {
        console.debug('Network diagram plugin rendering:', { spec });

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
        const { nodes: safeNodes, links: safeLinks } =
            sanitizeNetworkGraph(rawNodes, rawLinks, networkNodeSizeCap(width, height));

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
            // Create board containers if groups exist
            if (groups?.length) {
                const boards = svg.selectAll('.board')
                    .data(groups)
                    .enter()
                    .append('g')
                    .attr('class', 'board')
                    .attr('transform', 'translate(0,0)');
                boards.append('rect')
                    .attr('x', d => d.id === 'modem_board' ? 180 : 680)
                    .attr('y', 50)
                    .attr('width', d => d.id === 'modem_board' ? 350 : 200)
                    .attr('height', 500)
                    .attr('fill', 'none')
                    .attr('stroke', '#666')
                    .attr('stroke-dasharray', '5,5');
                boards.append('text')
                    .attr('x', d => d.id === 'modem_board' ? 200 : 700)
                    .attr('y', 80)
                    .text(d => d.label)
                    .attr('fill', '#666');
            }

            const style = (resolved as any).style || {};

            // If nodes lack explicit x/y coordinates, run a force simulation so a
            // bare nodes+links graph (the LLM-friendly form) gets a usable layout
            // instead of collapsing every node onto (0,0).
            const needsLayout = safeNodes.some((n: any) => n.x === undefined || n.y === undefined);
            if (needsLayout && typeof d3.forceSimulation === 'function') {
                const sim = d3.forceSimulation(safeNodes)
                    .force('link', d3.forceLink(safeLinks).id((n: any) => n.id).distance(80))
                    .force('charge', d3.forceManyBody().strength(-200))
                    .force('center', d3.forceCenter(width / 2, height / 2))
                    .stop();
                const ticks = Math.min(300, Math.max(50, safeNodes.length * 4));
                for (let i = 0; i < ticks; i++) sim.tick();
            }

            // Clamp every node inside the viewport so disconnected/ejected nodes
            // (repelled off-canvas with no link to pull them back) and a large
            // hub whose radius overhangs the edge are never silently clipped by
            // the SVG viewBox (Issue 31: catastrophic silent data loss).
            clampNodePositionsToViewport(safeNodes, width, height);

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
                .attr('stroke', (l: any) => style.linkColor || '#999')
                .attr('stroke-opacity', (l: any) => style.linkOpacity ?? 0.6)
                .attr('stroke-width', (l: any) => l.weight || 1);

            // Draw nodes
            const nodeColors = style.nodeColors || {};
            const nodeGroups = svg.selectAll('.node')
                .data(safeNodes)
                .enter()
                .append('g')
                .attr('class', 'node')
                .attr('transform', (d: any) => `translate(${d.x ?? 0},${d.y ?? 0})`);

            nodeGroups.append('circle')
                .attr('r', (d: any) => d.size || 10)
                .attr('fill', (d: any) => nodeColors[(d as any).group] || d.color || '#69b3a2')
                .attr('stroke', '#fff')
                .attr('stroke-width', 1.5);

            nodeGroups.append('text')
                .attr('dy', (d: any) => -(d.size || 10) - 5)
                .attr('text-anchor', 'middle')
                .attr('fill', style.labelColor || '#ccc')
                .attr('font-size', style.fontSize || 12)
                .text((d: any) => d.label || d.id);
        } catch (error) {
            console.error('Network diagram render error:', error);
            // Clean up on error
            d3.select(container).selectAll('*').remove();
            throw error;
        }
    }
};
