/**
 * Mermaid Edge Rerouter
 *
 * Post-render SVG processor that detects "skip edges" (edges connecting
 * non-adjacent nodes in a linear layout) and reroutes them with curved
 * arcs that bypass intermediate nodes instead of cutting through them.
 *
 * Problem: Mermaid's dagre layout draws feedback/control-loop edges as
 * straight lines through intermediate nodes, making diagrams hard to read.
 *
 * Solution: After render, identify crossing edges and replace their SVG
 * paths with cubic bezier curves that arc above or below the node row.
 */

interface NodeBox {
    id: string;
    x: number;
    y: number;
    width: number;
    height: number;
    centerX: number;
    centerY: number;
    rank: number;
}

interface EdgeInfo {
    element: SVGPathElement;
    sourceId: string;
    targetId: string;
    pathData: string;
}

/** Skip edge collected for batch sorting before arc generation. */
interface SkipEdgeEntry {
    edge: EdgeInfo;
    sourceNode: NodeBox;
    targetNode: NodeBox;
    intermediates: NodeBox[];
    skipDistance: number; // number of ranks skipped
}

export interface RerouteResult {
    totalEdges: number;
    reroutedEdges: number;
    skippedEdges: number;
    details: string[];
}

// Arc layout constants
const ARC_BASE_MARGIN = 30;   // minimum clearance from outermost node edge
const ARC_LAYER_SPACING = 25; // spacing between nested arc layers
const ARC_MIN_OFFSET = 30;    // absolute minimum offset even for single-skip arcs


function detectLayoutDirection(nodes: NodeBox[]): 'LR' | 'TB' {
    if (nodes.length < 2) return 'LR';
    const xs = nodes.map(n => n.centerX);
    const ys = nodes.map(n => n.centerY);
    const xSpread = Math.max(...xs) - Math.min(...xs);
    const ySpread = Math.max(...ys) - Math.min(...ys);
    return xSpread > ySpread ? 'LR' : 'TB';
}

function assignRanks(nodes: NodeBox[], direction: 'LR' | 'TB'): void {
    const tolerance = 30;
    const sorted = [...nodes].sort((a, b) =>
        direction === 'LR' ? a.centerX - b.centerX : a.centerY - b.centerY
    );
    let currentRank = 0;
    let lastPos = -Infinity;
    for (const node of sorted) {
        const pos = direction === 'LR' ? node.centerX : node.centerY;
        if (pos - lastPos > tolerance) {
            currentRank++;
            lastPos = pos;
        }
        node.rank = currentRank;
    }
}

function extractNodes(svgElement: SVGElement): NodeBox[] {
    const nodes: NodeBox[] = [];
    const nodeGroups = svgElement.querySelectorAll('.node');

    nodeGroups.forEach((group) => {
        const id = group.id || group.getAttribute('data-id') || '';
        if (!id) return;

        try {
            const bbox = (group as SVGGraphicsElement).getBBox();
            const ctm = (group as SVGGraphicsElement).getCTM();
            const svgCTM = svgElement.querySelector('g')?.getCTM();

            let x = bbox.x;
            let y = bbox.y;

            if (ctm && svgCTM) {
                const relCTM = svgCTM.inverse().multiply(ctm);
                x = relCTM.e + bbox.x;
                y = relCTM.f + bbox.y;
            }

            nodes.push({
                id: id.replace(/^flowchart-/, '').replace(/-\d+$/, ''),
                x, y,
                width: bbox.width,
                height: bbox.height,
                centerX: x + bbox.width / 2,
                centerY: y + bbox.height / 2,
                rank: 0
            });
        } catch (e) {
            console.debug(`Edge rerouter: could not get bbox for node ${id}`, e);
        }
    });

    return nodes;
}

function extractEdges(svgElement: SVGElement): EdgeInfo[] {
    const edges: EdgeInfo[] = [];
    const edgeGroups = svgElement.querySelectorAll('.edgePath');

    edgeGroups.forEach((group) => {
        const path = group.querySelector('path') as SVGPathElement;
        if (!path) return;

        const classList = Array.from(group.classList);
        let sourceId = '';
        let targetId = '';

        for (const cls of classList) {
            if (cls.startsWith('LS-')) sourceId = cls.substring(3);
            else if (cls.startsWith('LE-')) targetId = cls.substring(3);
        }

        if (!sourceId) sourceId = group.getAttribute('data-source') || '';
        if (!targetId) targetId = group.getAttribute('data-target') || '';

        if (!sourceId && group.id) {
            const match = group.id.match(/L-(.+)-(.+)/);
            if (match) { sourceId = match[1]; targetId = match[2]; }
        }

        sourceId = sourceId.replace(/^flowchart-/, '').replace(/-\d+$/, '');
        targetId = targetId.replace(/^flowchart-/, '').replace(/-\d+$/, '');

        if (sourceId && targetId) {
            edges.push({
                element: path,
                sourceId, targetId,
                pathData: path.getAttribute('d') || ''
            });
        }
    });

    return edges;
}

function pathIntersectsNode(pathBBox: DOMRect, node: NodeBox, margin: number = 5): boolean {
    return (
        pathBBox.x < node.x + node.width + margin &&
        pathBBox.x + pathBBox.width > node.x - margin &&
        pathBBox.y < node.y + node.height + margin &&
        pathBBox.y + pathBBox.height > node.y - margin
    );
}

function findIntermediateNodes(
    sourceNode: NodeBox,
    targetNode: NodeBox,
    allNodes: NodeBox[]
): NodeBox[] {
    const minRank = Math.min(sourceNode.rank, targetNode.rank);
    const maxRank = Math.max(sourceNode.rank, targetNode.rank);
    if (maxRank - minRank <= 1) return [];
    return allNodes.filter(n =>
        n.id !== sourceNode.id &&
        n.id !== targetNode.id &&
        n.rank > minRank &&
        n.rank < maxRank
    );
}

function generateArcPath(
    source: NodeBox,
    target: NodeBox,
    intermediates: NodeBox[],
    direction: 'LR' | 'TB',
    arcAbove: boolean,
    /** Nesting ordinal: 0 = innermost (shortest skip), higher = outermost */
    ordinal: number = 0
): string {
    const relevantNodes = [source, ...intermediates, target];
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (const n of relevantNodes) {
        minX = Math.min(minX, n.x);
        minY = Math.min(minY, n.y);
        maxX = Math.max(maxX, n.x + n.width);
        maxY = Math.max(maxY, n.y + n.height);
    }

    // Ordinal-based layered offset: higher ordinal = farther from nodes
    const arcOffset = Math.max(ARC_MIN_OFFSET, ARC_BASE_MARGIN + ordinal * ARC_LAYER_SPACING);

    if (direction === 'LR') {
        const startX = source.x + source.width;
        const startY = source.centerY;
        const endX = target.x;
        const endY = target.centerY;

        const arcY = arcAbove
            ? minY - arcOffset
            : maxY + arcMargin + arcScale;

        const cp1X = startX + (endX - startX) * 0.25;
        const cp2X = startX + (endX - startX) * 0.75;

        return `M ${startX} ${startY} C ${cp1X} ${arcY}, ${cp2X} ${arcY}, ${endX} ${endY}`;
    } else {
        const startX = source.centerX;
        const startY = source.y + source.height;
        const endX = target.centerX;
        const endY = target.y;

        const arcX = arcAbove
            ? minX - arcOffset
            : maxX + arcMargin + arcScale;

        const cp1Y = startY + (endY - startY) * 0.25;
        const cp2Y = startY + (endY - startY) * 0.75;

        return `M ${startX} ${startY} C ${arcX} ${cp1Y}, ${arcX} ${cp2Y}, ${endX} ${endY}`;
    }
}

/**
 * Reroute skip edges in a Mermaid SVG to arc around intermediate nodes.
 * Call after Mermaid render completes and SVG is in the DOM.
 */
export function rerouteSkipEdges(svgElement: SVGElement): RerouteResult {
    const result: RerouteResult = {
        totalEdges: 0,
        reroutedEdges: 0,
        skippedEdges: 0,
        details: []
    };

    const nodes = extractNodes(svgElement);
    if (nodes.length < 3) {
        result.details.push('Too few nodes for skip-edge detection');
        return result;
    }

    const direction = detectLayoutDirection(nodes);
    assignRanks(nodes, direction);

    const nodeMap = new Map<string, NodeBox>();
    for (const node of nodes) {
        nodeMap.set(node.id, node);
    }

    result.details.push(
        `Layout: ${direction}, ${nodes.length} nodes, ` +
        `ranks: ${new Set(nodes.map(n => n.rank)).size}`
    );

    const edges = extractEdges(svgElement);
    result.totalEdges = edges.length;

    if (edges.length === 0) {
        result.details.push('No edges found with source/target info');
        return result;
    }

    const skipEdges: SkipEdgeEntry[] = [];

    for (const edge of edges) {
        const sourceNode = nodeMap.get(edge.sourceId);
        const targetNode = nodeMap.get(edge.targetId);

        if (!sourceNode || !targetNode) {
            result.skippedEdges++;
            continue;
        }

        const intermediates = findIntermediateNodes(sourceNode, targetNode, nodes);

        const skipDistance = Math.abs(sourceNode.rank - targetNode.rank);

        if (intermediates.length === 0) {
            // Adjacent edge: only reroute if path actually crosses a node
            try {
                const pathBBox = edge.element.getBBox();
                const crossesNode = nodes.some(n =>
                    n.id !== edge.sourceId &&
                    n.id !== edge.targetId &&
                    pathIntersectsNode(
                        new DOMRect(pathBBox.x, pathBBox.y, pathBBox.width, pathBBox.height),
                        n
                    )
                );
                if (!crossesNode) {
                    result.skippedEdges++;
                    continue;
                }
            } catch {
                result.skippedEdges++;
                continue;
            }
        }

        skipEdges.push({ edge, sourceNode, targetNode, intermediates, skipDistance });
    }

    if (skipEdges.length === 0) {
        result.details.push('No skip edges detected');
        return result;
    }

    // Sort by skip distance ascending so shortest gets lowest ordinal (innermost arc)
    skipEdges.sort((a, b) => a.skipDistance - b.skipDistance);

    // Distribute edges to above/below sides, alternating assignment.
    // Assign the first to above, second to below, etc.
    const aboveEdges: SkipEdgeEntry[] = [];
    const belowEdges: SkipEdgeEntry[] = [];
    for (let i = 0; i < skipEdges.length; i++) {
        if (i % 2 === 0) {
            aboveEdges.push(skipEdges[i]);
        } else {
            belowEdges.push(skipEdges[i]);
        }
    }

    // Within each side, sort by skip distance ascending (shortest = innermost = ordinal 0)
    aboveEdges.sort((a, b) => a.skipDistance - b.skipDistance);
    belowEdges.sort((a, b) => a.skipDistance - b.skipDistance);

    // Generate arcs with ordinal-based nesting
    const renderSide = (entries: SkipEdgeEntry[], arcAbove: boolean) => {
        for (let ordinal = 0; ordinal < entries.length; ordinal++) {
            const entry = entries[ordinal];
            const newPath = generateArcPath(
                entry.sourceNode, entry.targetNode, entry.intermediates,
                direction, arcAbove, ordinal
            );

            entry.edge.element.setAttribute('d', newPath);
            entry.edge.element.style.strokeLinejoin = 'round';
            entry.edge.element.style.strokeLinecap = 'round';

            result.reroutedEdges++;
            result.details.push(
                `Rerouted: ${entry.edge.sourceId} → ${entry.edge.targetId} ` +
                `(skip ${entry.intermediates.length} nodes, ` +
                `rank-dist ${entry.skipDistance}, ` +
                `arc ${arcAbove ? 'above' : 'below'} ordinal ${ordinal})`
            );
        }
    };

    renderSide(aboveEdges, true);
    renderSide(belowEdges, false);

    return result;
}

/**
 * Check if skip-edge rerouting would be beneficial for this SVG.
 */
export function shouldRerouteEdges(svgElement: SVGElement): boolean {
    const nodes = svgElement.querySelectorAll('.node');
    const edges = svgElement.querySelectorAll('.edgePath');
    return nodes.length >= 4 && edges.length >= nodes.length;
}
