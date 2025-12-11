/**
 * Shared Graph Layout Engine
 * Provides automatic layout and edge routing using ELK (Eclipse Layout Kernel)
 * Can be used across all diagram types: DrawIO, Mermaid, Graphviz, custom D3
 */

import ELK from 'elkjs/lib/elk.bundled.js';

const elk = new ELK();

export interface LayoutNode {
    id: string;
    width: number;
    height: number;
    x?: number;
    y?: number;
    labels?: Array<{ text: string }>;
    properties?: Record<string, any>;
}

export interface LayoutEdge {
    id: string;
    source: string;
    target: string;
    labels?: Array<{ text: string }>;
    properties?: Record<string, any>;
}

export interface LayoutContainer {
    id: string;
    children: LayoutNode[];
    edges: LayoutEdge[];
    labels?: Array<{ text: string }>;
    properties?: Record<string, any>;
}

export interface LayoutOptions {
    algorithm?: 'layered' | 'force' | 'box' | 'stress';
    direction?: 'DOWN' | 'UP' | 'RIGHT' | 'LEFT';
    spacing?: {
        nodeNode?: number;
        edgeNode?: number;
        edgeEdge?: number;
    };
    edgeRouting?: 'ORTHOGONAL' | 'POLYLINE' | 'SPLINES';
    hierarchical?: boolean;
}

export interface LayoutResult {
    nodes: Map<string, { x: number; y: number; width: number; height: number }>;
    edges: Map<string, {
        sections: Array<{
            startPoint: { x: number; y: number };
            endPoint: { x: number; y: number };
            bendPoints?: Array<{ x: number; y: number }>;
        }>;
    }>;
    containers?: Map<string, { x: number; y: number; width: number; height: number }>;
}

/**
 * Convert a graph structure to ELK format
 */
function convertToELK(
    nodes: LayoutNode[],
    edges: LayoutEdge[],
    containers?: LayoutContainer[]
): any {
    const elkGraph: any = {
        id: 'root',
        layoutOptions: {},
        children: [],
        edges: []
    };

    // Add containers as children with their own nodes
    if (containers && containers.length > 0) {
        // Build set of node IDs that are already inside containers
        const containerNodeIds = new Set(containers.flatMap(c => c.children.map(n => n.id)));

        containers.forEach(container => {
            elkGraph.children.push({
                id: container.id,
                layoutOptions: {
                    'elk.padding': '[top=40,left=20,bottom=20,right=20]'
                },
                labels: container.labels,
                children: container.children.map(node => ({
                    id: node.id,
                    width: node.width,
                    height: node.height,
                    x: node.x,
                    y: node.y,
                    labels: node.labels,
                    properties: {
                        ...node.properties,
                        'org.eclipse.elk.position': `(${node.x},${node.y})`
                    }
                })),
                edges: container.edges.map(edge => ({
                    id: edge.id,
                    sources: [edge.source],
                    targets: [edge.target],
                    labels: edge.labels,
                    properties: edge.properties
                }))
            });
        });

        // CRITICAL FIX: Also add top-level nodes to the graph
        // These are nodes that aren't inside any container but may be referenced by cross-container edges
        // Only add nodes that are NOT already in a container
        nodes.filter(node => !containerNodeIds.has(node.id)).forEach(node => {
            elkGraph.children.push({
                id: node.id,
                width: node.width,
                height: node.height,
                x: node.x,
                y: node.y,
                labels: node.labels,
                properties: {
                    ...node.properties,
                    'org.eclipse.elk.position': `(${node.x},${node.y})`
                }
            });
        });
    } else {
        // Flat structure - add all nodes as direct children
        elkGraph.children = nodes.map(node => ({
            id: node.id,
            width: node.width,
            height: node.height,
            x: node.x,
            y: node.y,
            labels: node.labels,
            properties: {
                ...node.properties,
                'org.eclipse.elk.position': `(${node.x},${node.y})`
            }
        }));
    }

    // Add top-level edges (cross-container or graph-level)
    elkGraph.edges = edges.map(edge => ({
        id: edge.id,
        sources: [edge.source],
        targets: [edge.target],
        labels: edge.labels,
        properties: edge.properties
    }));

    return elkGraph;
}

/**
 * Calculate optimal port side for a node based on edge direction
 */
function getOptimalPortSide(sourceNode: LayoutNode, targetNode: LayoutNode): { source: string, target: string } {
    const dx = (targetNode.x || 0) - (sourceNode.x || 0);
    const dy = (targetNode.y || 0) - (sourceNode.y || 0);
    
    const absDx = Math.abs(dx);
    const absDy = Math.abs(dy);
    
    // CRITICAL: Choose port sides based on which direction has MORE distance
    // If dy is bigger, it's a vertical edge -> use NORTH/SOUTH
    // If dx is bigger, it's a horizontal edge -> use EAST/WEST
    // This is simple and correct!
    const isVertical = absDy >= absDx;
    
    if (isVertical) {
        // Vertical edge - use top/bottom ports
        return dy > 0 
            ? { source: 'SOUTH', target: 'NORTH' }  // Source bottom -> Target top
            : { source: 'NORTH', target: 'SOUTH' }; // Source top -> Target bottom
    } else {
        // Horizontal edge - use left/right ports
        return dx > 0
            ? { source: 'EAST', target: 'WEST' }    // Source right -> Target left
            : { source: 'WEST', target: 'EAST' };   // Source left -> Target right
    }
}

/**
 * Run ELK layout algorithm on a graph
 */
export async function runLayout(
    nodes: LayoutNode[],
    edges: LayoutEdge[],
    options: LayoutOptions = {},
    containers?: LayoutContainer[]
): Promise<LayoutResult> {
    console.log('🎯 ELK: Starting layout computation', {
        nodes: nodes.length,
        edges: edges.length,
        containers: containers?.length || 0,
        options
    });

    // First pass: create node map for position lookups
    const nodeMap = new Map<string, LayoutNode>();
    nodes.forEach(n => nodeMap.set(n.id, n));
    containers?.forEach(c => c.children.forEach(n => nodeMap.set(n.id, n)));

    // Build ELK graph with port constraints based on actual edge directions
    const elkGraph = convertToELK(nodes, edges, containers);

    // Configure ELK global options
    const algorithm = options.algorithm || 'layered';
    const direction = options.direction || 'DOWN';
    const edgeRouting = options.edgeRouting || 'ORTHOGONAL';

    elkGraph.layoutOptions = {
        'elk.algorithm': algorithm,
        'elk.direction': direction,
        'elk.edgeRouting': edgeRouting,

        // Spacing configuration
        'elk.spacing.nodeNode': options.spacing?.nodeNode?.toString() || '80',
        'elk.layered.spacing.nodeNodeBetweenLayers': options.spacing?.nodeNode?.toString() || '100',
        'elk.layered.spacing.edgeNodeBetweenLayers': options.spacing?.edgeNode?.toString() || '40',
        'elk.spacing.edgeEdge': options.spacing?.edgeEdge?.toString() || '15',

        // Layout quality settings
        'elk.layered.nodePlacement.strategy': 'NETWORK_SIMPLEX',
        'elk.layered.thoroughness': '7',
        'elk.interactiveLayout': 'true',
        'elk.hierarchyHandling': 'INCLUDE_CHILDREN',
        'elk.separateConnectedComponents': 'false',
        'elk.layered.unnecessaryBendpoints': 'true',
        'elk.layered.spacing.edgeEdgeBetweenLayers': '20',
        'elk.layered.edgeRouting.sloppySplineRouting': 'false',
        
        // Let ELK freely choose port sides based on layout
        'elk.portConstraints': 'FREE'
    };

    // Add port constraints to each edge based on layout direction
    elkGraph.edges = elkGraph.edges.map((edge: any) => {
        const sourceNode = nodeMap.get(edge.sources[0]);
        const targetNode = nodeMap.get(edge.targets[0]);
        
        // Don't add port constraints - let ELK choose based on layout
        return edge;
    });

    try {
        const layouted = await elk.layout(elkGraph);

        console.log('✅ ELK: Layout completed successfully');
        console.log('📐 ELK: Output structure sample:', {
            hasChildren: !!layouted.children,
            childCount: layouted.children?.length || 0,
            hasEdges: !!layouted.edges,
            edgeCount: layouted.edges?.length || 0,
            firstChild: layouted.children?.[0] ? {
                id: layouted.children[0].id,
                x: layouted.children[0].x,
                y: layouted.children[0].y,
                width: layouted.children[0].width,
                height: layouted.children[0].height,
                hasChildren: !!layouted.children[0].children,
                childCount: layouted.children[0].children?.length || 0
            } : null
        });

        // Extract results
        const result: LayoutResult = {
            nodes: new Map(),
            edges: new Map(),
            containers: new Map()
        };

        // Process containers and their children
        if (layouted.children) {
            layouted.children.forEach((child: any) => {
                if (child.children) {
                    // This is a container
                    result.containers?.set(child.id, {
                        x: child.x || 0,
                        y: child.y || 0,
                        width: child.width || 0,
                        height: child.height || 0
                    });

                    // Process nodes within container (relative to container)
                    child.children.forEach((node: any) => {
                        result.nodes.set(node.id, {
                            x: node.x || 0,  // Keep relative coordinates
                            y: node.y || 0,
                            width: node.width || 0,
                            height: node.height || 0
                        });
                    });

                    // Process edges within container
                    if (child.edges) {
                        child.edges.forEach((edge: any) => {
                            result.edges.set(edge.id, {
                                sections: edge.sections || []
                            });
                        });
                    }
                } else {
                    // Regular top-level node
                    result.nodes.set(child.id, {
                        x: child.x || 0,
                        y: child.y || 0,
                        width: child.width || 0,
                        height: child.height || 0
                    });
                }
            });
        }

        // Process top-level edges with routing information
        if (layouted.edges) {
            layouted.edges.forEach((edge: any) => {
                result.edges.set(edge.id, {
                    sections: edge.sections || []
                });
            });
        }

        console.log('📐 ELK: Extracted layout results', {
            nodesPositioned: result.nodes.size,
            edgesRouted: result.edges.size,
            containersResized: result.containers?.size || 0
        });

        // Log sample results for debugging
        const sampleNodeId = Array.from(result.nodes.keys())[0];
        if (sampleNodeId) {
            console.log('📐 ELK: Sample node position:', sampleNodeId, result.nodes.get(sampleNodeId));
        }

        return result;
    } catch (error) {
        console.error('❌ ELK layout failed:', error);
        throw error;
    }
}

/**
 * Apply ELK layout results to maxGraph instance
 */
export function applyLayoutToMaxGraph(
    graph: any,
    cellMap: Map<string, any>,
    layoutResult: LayoutResult
): void {
    console.log('📐 Applying ELK layout results to maxGraph');
    console.log('📐 Input summary:', {
        cellMapSize: cellMap.size,
        nodesToPosition: layoutResult.nodes.size,
        containersToResize: layoutResult.containers?.size || 0,
        edgesToRoute: layoutResult.edges.size
    });

    // Validate graph parameter
    if (!graph || typeof graph.getModel !== 'function') {
        console.error('❌ Invalid graph object passed to applyLayoutToMaxGraph:', graph);
        throw new Error('Invalid graph object - missing getModel() method');
    }

    const model = graph.getModel();
    if (!model) {
        throw new Error('Failed to get model from graph');
    }

    model.beginUpdate();

    try {
        // Apply container positions and sizes first
        let containersUpdated = 0;
        layoutResult.containers?.forEach((position, containerId) => {
            const cell = cellMap.get(containerId);
            if (cell) {
                const geometry = cell.getGeometry();
                if (geometry) {
                    const oldGeom = {
                        x: geometry.x,
                        y: geometry.y,
                        width: geometry.width,
                        height: geometry.height
                    };

                    const newGeom = geometry.clone();
                    newGeom.x = position.x;
                    newGeom.y = position.y;
                    newGeom.width = position.width;
                    newGeom.height = position.height;
                    cell.setGeometry(newGeom);
                    containersUpdated++;

                    console.log(`  ✅ Container ${containerId}:`, {
                        before: oldGeom,
                        after: { x: position.x, y: position.y, width: position.width, height: position.height }
                    });
                }
            } else {
                console.warn(`  ⚠️ Container ${containerId} not found in cellMap`);
            }
        });

        // Apply node positions (these are relative to their containers)
        let nodesUpdated = 0;
        layoutResult.nodes.forEach((position, nodeId) => {
            const cell = cellMap.get(nodeId);
            if (cell && !cell.isEdge()) {
                const geometry = cell.getGeometry();
                if (geometry) {
                    // ELK gives us absolute positions, but we need relative-to-parent positions
                    // Calculate the parent's absolute position to convert back
                    let parentAbsX = 0;
                    let parentAbsY = 0;

                    let parent = cell.getParent();
                    while (parent && parent.getId() !== '0' && parent.getId() !== '1') {
                        const parentGeom = parent.getGeometry();
                        if (parentGeom) {
                            parentAbsX += parentGeom.x;
                            parentAbsY += parentGeom.y;
                        }
                        parent = parent.getParent();
                    }

                    // Convert ELK's absolute position back to relative
                    const relativeX = position.x - parentAbsX;
                    const relativeY = position.y - parentAbsY;

                    const oldX = geometry.x;
                    const oldY = geometry.y;

                    const newGeom = geometry.clone();
                    newGeom.x = relativeX;
                    newGeom.y = relativeY;
                    // Keep original width/height (ELK preserves node sizes)
                    cell.setGeometry(newGeom);
                    nodesUpdated++;

                    if (Math.abs(oldX - position.x) > 1 || Math.abs(oldY - position.y) > 1) {
                        console.log(`  ✅ Node ${nodeId} moved:`, {
                            from: { x: oldX.toFixed(1), y: oldY.toFixed(1) },
                            to: { x: position.x.toFixed(1), y: position.y.toFixed(1) },
                            delta: { dx: (position.x - oldX).toFixed(1), dy: (position.y - oldY).toFixed(1) }
                        });
                    }
                }
            } else if (!cell) {
                console.warn(`  ⚠️ Node ${nodeId} not found in cellMap`);
            }
        });

        // Apply edge routing (waypoints/bend points from ELK)
        let edgesUpdated = 0;
        layoutResult.edges.forEach((edgeInfo, edgeId) => {
            const cell = cellMap.get(edgeId);
            if (cell && cell.isEdge()) {
                const geometry = cell.getGeometry();
                if (geometry && edgeInfo.sections && edgeInfo.sections.length > 0) {
                    const section = edgeInfo.sections[0];
                    const newGeom = geometry.clone();

                    // Build waypoints array from ELK's routing
                    const waypoints: Array<{ x: number; y: number }> = [];

                    // Add start point
                    waypoints.push({
                        x: section.startPoint.x,
                        y: section.startPoint.y
                    });

                    // Add bend points (these create the orthogonal path)
                    if (section.bendPoints && section.bendPoints.length > 0) {
                        section.bendPoints.forEach((bp: any) => {
                            waypoints.push({ x: bp.x, y: bp.y });
                        });
                    }

                    // Add end point
                    waypoints.push({
                        x: section.endPoint.x,
                        y: section.endPoint.y
                    });

                    // Apply waypoints to geometry
                    newGeom.points = waypoints;
                    cell.setGeometry(newGeom);
                    edgesUpdated++;

                    console.log(`  ✅ Edge ${edgeId} routed:`, {
                        waypointCount: waypoints.length,
                        waypoints: waypoints.map(wp => `(\${wp.x.toFixed(1)},\${wp.y.toFixed(1)})`).join(' → ')
                    });
                } else {
                    console.warn(`  ⚠️ Edge ${edgeId} has no routing sections`);
                }
            } else if (!cell) {
                console.warn(`  ⚠️ Edge ${edgeId} not found in cellMap`);
            }
        });

        console.log('✅ Layout application complete:', {
            containersUpdated,
            nodesUpdated,
            edgesUpdated,
            totalExpected: {
                containers: layoutResult.containers?.size || 0,
                nodes: layoutResult.nodes.size,
                edges: layoutResult.edges.size
            }
        });
    } finally {
        model.endUpdate();
    }
}
