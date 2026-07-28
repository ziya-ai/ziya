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

/**
 * Coerce a raw network graph into safe, renderable arrays:
 *  - accepts `edges` as an alias for `links`
 *  - clamps degenerate node `size` (non-finite / <=0 -> default; > cap -> cap)
 *  - filters dangling edges whose source/target id is absent from `nodes`
 *    (an unresolved endpoint would otherwise draw a line to (0,0) or crash a
 *    force lookup — mirrors the Issue-3 forceLink dangling-edge filter)
 *  - clamps degenerate link `weight` used for stroke-width
 * Returns new arrays; does not mutate input. Pure/DOM-free for unit testing.
 *
 * Exported for regression testing.
 */
export function sanitizeNetworkGraph(rawNodes: any[], rawLinks: any[]): { nodes: any[]; links: any[] } {
    const nodes = (Array.isArray(rawNodes) ? rawNodes : []).map((n: any) => {
        const out = { ...n };
        const size = Number(n?.size);
        if (!Number.isFinite(size) || size <= 0) {
            out.size = undefined; // fall back to the render default (10)
        } else if (size > NETWORK_MAX_NODE_SIZE) {
            out.size = NETWORK_MAX_NODE_SIZE;
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
        nodes.every((n: any) => typeof n.id === 'string') &&
        links.length >= 0 &&
        links.every((l: any) => typeof l.source === 'string' && typeof l.target === 'string')
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
        const { nodes: safeNodes, links: safeLinks } = sanitizeNetworkGraph(rawNodes, rawLinks);
        const width = (resolved as any).width || 600;
        const height = (resolved as any).height || 400;

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
