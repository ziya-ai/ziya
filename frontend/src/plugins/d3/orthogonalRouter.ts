/**
 * Orthogonal Connector Router
 * Based on algorithm by jose-mdz
 * Routes orthogonal paths between fixed rectangles with obstacle avoidance
 */

export interface Point {
    x: number;
    y: number;
}

export interface Rect {
    left: number;
    top: number;
    width: number;
    height: number;
}

export type Side = 'top' | 'right' | 'bottom' | 'left';

export interface ConnectorPoint {
    shape: Rect;
    side: Side;
    distance: number; // 0.0 to 1.0 along the side
}

export interface RoutingOptions {
    pointA: ConnectorPoint;
    pointB: ConnectorPoint;
    obstacles?: Rect[]; // Other shapes to avoid
    shapeMargin?: number;
    globalBounds?: Rect;
}

class Rectangle {
    constructor(
        readonly left: number,
        readonly top: number,
        readonly width: number,
        readonly height: number
    ) {}

    static fromRect(r: Rect): Rectangle {
        return new Rectangle(r.left, r.top, r.width, r.height);
    }

    static fromLTRB(left: number, top: number, right: number, bottom: number): Rectangle {
        return new Rectangle(left, top, right - left, bottom - top);
    }

    get right(): number { return this.left + this.width; }
    get bottom(): number { return this.top + this.height; }
    get center(): Point { return { x: this.left + this.width / 2, y: this.top + this.height / 2 }; }

    inflate(h: number, v: number): Rectangle {
        return Rectangle.fromLTRB(
            this.left - h,
            this.top - v,
            this.right + h,
            this.bottom + v
        );
    }

    contains(p: Point): boolean {
        return p.x >= this.left && p.x <= this.right && p.y >= this.top && p.y <= this.bottom;
    }

    intersects(r: Rectangle): boolean {
        return (r.left < this.right) && (this.left < r.right) &&
               (r.top < this.bottom) && (this.top < r.bottom);
    }
}

class PointNode {
    distance = Number.MAX_SAFE_INTEGER;
    shortestPath: PointNode[] = [];
    adjacentNodes: Map<PointNode, number> = new Map();

    constructor(public data: Point) {}
}

class PointGraph {
    private index: { [x: string]: { [y: string]: PointNode } } = {};

    add(p: Point) {
        const xs = p.x.toString(), ys = p.y.toString();
        if (!(xs in this.index)) this.index[xs] = {};
        if (!(ys in this.index[xs])) this.index[xs][ys] = new PointNode(p);
    }

    get(p: Point): PointNode | null {
        const xs = p.x.toString(), ys = p.y.toString();
        return (xs in this.index && ys in this.index[xs]) ? this.index[xs][ys] : null;
    }

    has(p: Point): boolean {
        return this.get(p) !== null;
    }

    connect(a: Point, b: Point) {
        const nodeA = this.get(a);
        const nodeB = this.get(b);
        if (!nodeA || !nodeB) throw new Error('Point not found');
        
        const dist = Math.sqrt(Math.pow(b.x - a.x, 2) + Math.pow(b.y - a.y, 2));
        nodeA.adjacentNodes.set(nodeB, dist);
    }

    private getLowestDistanceNode(unsettled: Set<PointNode>): PointNode {
        let lowest: PointNode | null = null;
        let lowestDist = Number.MAX_SAFE_INTEGER;
        for (const node of unsettled) {
            if (node.distance < lowestDist) {
                lowestDist = node.distance;
                lowest = node;
            }
        }
        return lowest!;
    }

    private directionOf(a: Point, b: Point): 'h' | 'v' | null {
        if (a.x === b.x) return 'v';
        if (a.y === b.y) return 'h';
        return null;
    }

    private calculateMinimumDistance(evalNode: PointNode, edgeWeight: number, srcNode: PointNode) {
        const srcDist = srcNode.distance;
        
        // Infer direction from previous path
        let comingDir: 'h' | 'v' | null = null;
        if (srcNode.shortestPath.length > 0) {
            const prev = srcNode.shortestPath[srcNode.shortestPath.length - 1];
            comingDir = this.directionOf(prev.data, srcNode.data);
        }
        
        const goingDir = this.directionOf(srcNode.data, evalNode.data);
        const changingDir = comingDir && goingDir && comingDir !== goingDir;
        
        // Penalize direction changes to prefer straight segments
        const extraWeight = changingDir ? Math.pow(edgeWeight + 1, 2) : 0;
        
        if (srcDist + edgeWeight + extraWeight < evalNode.distance) {
            evalNode.distance = srcDist + edgeWeight + extraWeight;
            evalNode.shortestPath = [...srcNode.shortestPath, srcNode];
        }
    }

    calculateShortestPath(source: PointNode): void {
        source.distance = 0;
        const settled = new Set<PointNode>();
        const unsettled = new Set<PointNode>([source]);

        while (unsettled.size > 0) {
            const current = this.getLowestDistanceNode(unsettled);
            unsettled.delete(current);

            for (const [adjacent, weight] of current.adjacentNodes) {
                if (!settled.has(adjacent)) {
                    this.calculateMinimumDistance(adjacent, weight, current);
                    unsettled.add(adjacent);
                }
            }
            settled.add(current);
        }
    }
}

function computeConnectionPoint(p: ConnectorPoint): Point {
    const b = Rectangle.fromRect(p.shape);
    switch (p.side) {
        case 'bottom': return { x: b.left + b.width * p.distance, y: b.bottom };
        case 'top': return { x: b.left + b.width * p.distance, y: b.top };
        case 'left': return { x: b.left, y: b.top + b.height * p.distance };
        case 'right': return { x: b.right, y: b.top + b.height * p.distance };
    }
}

/**
 * Route an orthogonal connector between two shapes
 */
export function routeOrthogonalConnector(options: RoutingOptions): Point[] {
    const margin = options.shapeMargin ?? 20;
    
    // Compute actual connection points
    const ptA = computeConnectionPoint(options.pointA);
    const ptB = computeConnectionPoint(options.pointB);
    
    // Inflate shapes by margin to create routing boundaries
    const shapeA = Rectangle.fromRect(options.pointA.shape).inflate(margin, margin);
    const shapeB = Rectangle.fromRect(options.pointB.shape).inflate(margin, margin);
    
    // Create obstacles list. `obstacles` (source + target + externals) seeds the
    // routing GRID (rulers), so the grid still has lines hugging every box.
    // But the SOURCE and TARGET boxes must NOT block grid edges or exclude grid
    // nodes — the connection points sit on their inflated perimeters, and if the
    // box is treated as a blocker the endpoint's own neighbours get cut, leaving
    // it isolated so Dijkstra finds no path and the router falls back to a naive
    // straight line straight through the real obstacle (D-092/D-382/D-389). Only
    // EXTERNAL obstacles (`blockers`) are allowed to block. (Bug fix: previously
    // shapeA/shapeB were in the blocking set, which is why this router produced a
    // straight crossing route and had been left unused.)
    const obstacles = [shapeA, shapeB];
    const blockers: Rectangle[] = [];
    if (options.obstacles) {
        const inflatedExternals = options.obstacles.map(r => Rectangle.fromRect(r).inflate(margin, margin));
        obstacles.push(...inflatedExternals);
        blockers.push(...inflatedExternals);
    }
    
    // Create grid of horizontal and vertical rulers around obstacles
    const hRulers = new Set<number>();
    const vRulers = new Set<number>();
    
    // Add rulers for connection points
    hRulers.add(ptA.y);
    hRulers.add(ptB.y);
    vRulers.add(ptA.x);
    vRulers.add(ptB.x);
    
    // Add rulers for all obstacle boundaries
    obstacles.forEach(obs => {
        hRulers.add(obs.top);
        hRulers.add(obs.bottom);
        vRulers.add(obs.left);
        vRulers.add(obs.right);
    });
    
    // Add global bounds rulers if specified
    if (options.globalBounds) {
        const gb = Rectangle.fromRect(options.globalBounds);
        hRulers.add(gb.top);
        hRulers.add(gb.bottom);
        vRulers.add(gb.left);
        vRulers.add(gb.right);
    }
    
    const hRulerArray = Array.from(hRulers).sort((a, b) => a - b);
    const vRulerArray = Array.from(vRulers).sort((a, b) => a - b);
    
    // Build graph of intersection points
    const graph = new PointGraph();
    const spots: Point[] = [];
    
    // Create nodes at all grid intersections
    for (const x of vRulerArray) {
        for (const y of hRulerArray) {
            const pt = { x, y };
            
            // Skip points STRICTLY inside EXTERNAL obstacles (except connection
            // points). The test must be strict (interior only): nodes sitting ON
            // the inflated obstacle boundary are the routing CORRIDOR around it —
            // excluding them (inclusive contains) severs the perimeter path and
            // leaves the endpoints unreachable, so Dijkstra fails and the caller
            // draws a straight line through the box. Source/target boxes never
            // exclude nodes at all.
            let insideObstacle = false;
            const strictlyInside = (obs: Rectangle, q: Point) =>
                q.x > obs.left && q.x < obs.right && q.y > obs.top && q.y < obs.bottom;
            for (const obs of blockers) {
                if (strictlyInside(obs, pt)) {
                    // Allow if it's a connection point
                    if (!(Math.abs(pt.x - ptA.x) < 0.1 && Math.abs(pt.y - ptA.y) < 0.1) &&
                        !(Math.abs(pt.x - ptB.x) < 0.1 && Math.abs(pt.y - ptB.y) < 0.1)) {
                        insideObstacle = true;
                        break;
                    }
                }
            }
            
            if (!insideObstacle) {
                graph.add(pt);
                spots.push(pt);
            }
        }
    }
    
    // Connect adjacent nodes horizontally and vertically
    for (let i = 0; i < vRulerArray.length; i++) {
        for (let j = 0; j < hRulerArray.length - 1; j++) {
            const ptA = { x: vRulerArray[i], y: hRulerArray[j] };
            const ptB = { x: vRulerArray[i], y: hRulerArray[j + 1] };
            
            if (graph.has(ptA) && graph.has(ptB)) {
                // Check if line crosses any obstacle
                let crosses = false;
                for (const obs of blockers) {
                    const line = Rectangle.fromLTRB(
                        Math.min(ptA.x, ptB.x),
                        Math.min(ptA.y, ptB.y),
                        Math.max(ptA.x, ptB.x),
                        Math.max(ptA.y, ptB.y)
                    );
                    if (obs.intersects(line)) {
                        crosses = true;
                        break;
                    }
                }
                
                if (!crosses) {
                    graph.connect(ptA, ptB);
                    graph.connect(ptB, ptA);
                }
            }
        }
    }
    
    // Connect horizontally
    for (let j = 0; j < hRulerArray.length; j++) {
        for (let i = 0; i < vRulerArray.length - 1; i++) {
            const ptA = { x: vRulerArray[i], y: hRulerArray[j] };
            const ptB = { x: vRulerArray[i + 1], y: hRulerArray[j] };
            
            if (graph.has(ptA) && graph.has(ptB)) {
                let crosses = false;
                for (const obs of blockers) {
                    const line = Rectangle.fromLTRB(
                        Math.min(ptA.x, ptB.x),
                        Math.min(ptA.y, ptB.y),
                        Math.max(ptA.x, ptB.x),
                        Math.max(ptA.y, ptB.y)
                    );
                    if (obs.intersects(line)) {
                        crosses = true;
                        break;
                    }
                }
                
                if (!crosses) {
                    graph.connect(ptA, ptB);
                    graph.connect(ptB, ptA);
                }
            }
        }
    }
    
    // Find shortest path from A to B
    const sourceNode = graph.get(ptA);
    const targetNode = graph.get(ptB);
    
    if (!sourceNode || !targetNode) {
        console.warn('Could not find source or target in routing graph');
        return [ptA, ptB];
    }
    
    graph.calculateShortestPath(sourceNode);
    
    // Build path from shortest path
    const path: Point[] = [ptA];
    for (const node of targetNode.shortestPath) {
        path.push(node.data);
    }
    path.push(ptB);
    
    // Simplify path by removing collinear points
    const simplified: Point[] = [path[0]];
    for (let i = 1; i < path.length - 1; i++) {
        const prev = path[i - 1];
        const curr = path[i];
        const next = path[i + 1];
        
        // Keep point if it changes direction
        const dir1 = curr.x === prev.x ? 'v' : 'h';
        const dir2 = next.x === curr.x ? 'v' : 'h';
        
        if (dir1 !== dir2) {
            simplified.push(curr);
        }
    }
    simplified.push(path[path.length - 1]);
    
    return simplified;
}

/**
 * Determine optimal connection side for a source-target pair
 */
/**
 * D-092 / D-382 / D-389 — ROUTE-FIX repair helper.
 *
 * When the drawio plugin's ROUTE-FIX detector finds an edge whose Manhattan
 * fallback route was drawn straight through a vertex interior (or across a
 * transparent-fill box's label), it needs an obstacle-avoiding orthogonal route
 * to replace it. This is the missing "repair" the plugin previously only logged
 * ("ROUTE-FIX DETECT logs the crossing but does not repair it"): it picks the
 * optimal connection sides and routes around every obstacle, then returns only
 * the INTERIOR bend points so maxGraph's SegmentConnector still anchors to the
 * live cell perimeters (endpoints are dropped — the terminals own those).
 *
 * Returns [] when no bend is needed (a clear straight/L route already clears the
 * obstacles), so the caller only rewrites geometry.points when a genuine detour
 * is required. Pure (no maxGraph/DOM), so it is unit-testable in isolation.
 */
export function rerouteAroundObstacles(
    source: Rect,
    target: Rect,
    obstacles: Rect[],
    shapeMargin = 20
): Point[] {
    const { sourceSide, targetSide } = getOptimalSide(source, target);
    let route: Point[];
    try {
        route = routeOrthogonalConnector({
            pointA: { shape: source, side: sourceSide, distance: 0.5 },
            pointB: { shape: target, side: targetSide, distance: 0.5 },
            obstacles,
            shapeMargin,
        });
    } catch {
        return [];
    }
    // Drop the two perimeter endpoints; keep interior bends only. SegmentConnector
    // re-derives the perimeter anchor points from the live source/target cells.
    return route.length > 2 ? route.slice(1, -1) : [];
}

export function getOptimalSide(source: Rect, target: Rect): { sourceSide: Side; targetSide: Side } {
    const srcCenter = {
        x: source.left + source.width / 2,
        y: source.top + source.height / 2
    };
    const tgtCenter = {
        x: target.left + target.width / 2,
        y: target.top + target.height / 2
    };
    
    const dx = tgtCenter.x - srcCenter.x;
    const dy = tgtCenter.y - srcCenter.y;
    
    let sourceSide: Side, targetSide: Side;
    
    if (Math.abs(dx) > Math.abs(dy)) {
        // Horizontal flow
        sourceSide = dx > 0 ? 'right' : 'left';
        targetSide = dx > 0 ? 'left' : 'right';
    } else {
        // Vertical flow
        sourceSide = dy > 0 ? 'bottom' : 'top';
        targetSide = dy > 0 ? 'top' : 'bottom';
    }
    
    return { sourceSide, targetSide };
}
