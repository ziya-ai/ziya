/**
 * Graphviz Renderer
 * 
 * Renders architecture shapes from the universal catalog as Graphviz DOT format.
 * Suitable for complex network diagrams and system architectures.
 */
import { 
    type ArchitectureShape, 
    type ColorPalette,
    COLOR_PALETTES,
} from '../architectureShapesCatalog';
import { iconRegistry } from '../iconRegistry';

export interface GraphvizShape {
    /** Shape ID from catalog */
    shapeId: string;
    
    /** Label to display */
    label: string;
    
    /** Optional custom node ID (auto-generated if not provided) */
    nodeId?: string;
}

export interface GraphvizConnection {
    /** Index of source shape in shapes array */
    sourceIndex: number;
    
    /** Index of target shape in shapes array */
    targetIndex: number;
    
    /** Optional label for the edge */
    label?: string;
    
    /** Optional style */
    style?: 'solid' | 'dashed' | 'dotted' | 'bold';
}

/**
 * Get Graphviz color for shape
 */
function getGraphvizColor(colorPalette: ColorPalette): { fill: string; stroke: string; font: string } {
    const palette = COLOR_PALETTES[colorPalette];
    
    return {
        fill: palette.secondary,
        stroke: palette.primary,
        font: palette.text,
    };
}

/**
 * Generate Graphviz DOT from catalog shapes
 * 
 * @param shapes - Array of shapes to render
 * @param connections - Array of connections between shapes
 * @param shapeCatalog - Map of shape ID to ArchitectureShape definition
 * @param title - Diagram title
 * @param rankdir - Graph direction (LR, TB, RL, BT)
 * @returns Complete Graphviz DOT string
 */
export async function generateGraphvizFromCatalog(
    shapes: GraphvizShape[],
    connections: GraphvizConnection[],
    shapeCatalog: Record<string, ArchitectureShape>,
    title: string = 'Architecture',
    rankdir: 'LR' | 'TB' | 'RL' | 'BT' = 'LR'
): string {
    const lines: string[] = [];
    
    // Graph header
    lines.push('digraph {');
    lines.push(`    label="${title}";`);
    lines.push(`    rankdir=${rankdir};`);
    lines.push('    node [fontname="Arial", fontsize=12];');
    lines.push('    edge [fontname="Arial", fontsize=10];');
    lines.push('');
    
    // Generate node IDs
    const nodeIds = shapes.map((shape, idx) => shape.nodeId || `node${idx}`);
    
    // Render nodes
    for (let idx = 0; idx < shapes.length; idx++) {
        const shape = shapes[idx];
        const catalogShape = shapeCatalog[shape.shapeId];
        if (!catalogShape) {
            console.warn(`Shape not found in catalog: ${shape.shapeId}`);
            continue;
        }
        
        const nodeId = nodeIds[idx];
        const colors = getGraphvizColor(catalogShape.color);
        
        // Try to get icon URL
        const provider = catalogShape.provider?.id || 'aws';
        const drawioHint = catalogShape.renderHints.drawio;
        let iconUrl: string | null = null;
        
        if (drawioHint?.resIcon) {
            const iconId = drawioHint.resIcon.replace('mxgraph.aws4.', '');
            iconUrl = await iconRegistry.getIconAsBlobUrl(provider, iconId);
        }
        
        // Get Graphviz shape and style from render hints
        const graphvizHint = catalogShape.renderHints.graphviz;
        const graphvizShape = graphvizHint?.shape || 'box';
        const graphvizStyle = graphvizHint?.style || 'filled';
        
        // Build node definition
        lines.push(`    ${nodeId} [`);
        lines.push(`        label="${shape.label}"`);
        
        // Add image if available
        if (iconUrl) {
            lines.push(`        image="${iconUrl}"`);
            lines.push(`        imagescale=true`);
            lines.push(`        labelloc=b`);  // Label below image
        }
        
        lines.push(`        shape=${graphvizShape}`);
        lines.push(`        style="${graphvizStyle}"`);
        lines.push(`        fillcolor="${colors.fill}"`);
        lines.push(`        fontcolor="${colors.font}"`);
        lines.push(`        color="${colors.stroke}"`);
        lines.push(`    ];`);
    }
    
    lines.push('');
    
    // Render edges
    connections.forEach(conn => {
        if (conn.sourceIndex < 0 || conn.sourceIndex >= nodeIds.length ||
            conn.targetIndex < 0 || conn.targetIndex >= nodeIds.length) {
            console.warn('Invalid connection:', conn);
            return;
        }
        
        const source = nodeIds[conn.sourceIndex];
        const target = nodeIds[conn.targetIndex];
        
        // Build edge attributes
        const attrs: string[] = [];
        
        if (conn.label) {
            attrs.push(`label="${conn.label}"`);
        }
        
        if (conn.style === 'dashed') {
            attrs.push('style=dashed');
        } else if (conn.style === 'dotted') {
            attrs.push('style=dotted');
        } else if (conn.style === 'bold') {
            attrs.push('style=bold');
            attrs.push('penwidth=2');
        }
        
        const attrStr = attrs.length > 0 ? ` [${attrs.join(', ')}]` : '';
        lines.push(`    ${source} -> ${target}${attrStr};`);
    });
    
    lines.push('}');
    
    return lines.join('\n');
}

/**
 * Generate Graphviz with subgraphs (clusters)
 * Useful for grouping components (e.g., VPCs, regions)
 */
export interface GraphvizCluster {
    /** Cluster label */
    label: string;
    
    /** Indices of shapes that belong to this cluster */
    shapeIndices: number[];
    
    /** Optional styling */
    style?: {
        fillcolor?: string;
        style?: string;
        penwidth?: number;
    };
}

export function generateGraphvizWithClusters(
    shapes: GraphvizShape[],
    connections: GraphvizConnection[],
    clusters: GraphvizCluster[],
    shapeCatalog: Record<string, ArchitectureShape>,
    title: string = 'Architecture',
    rankdir: 'LR' | 'TB' | 'RL' | 'BT' = 'LR'
): string {
    const lines: string[] = [];
    
    lines.push('digraph {');
    lines.push(`    label="${title}";`);
    lines.push(`    rankdir=${rankdir};`);
    lines.push('    compound=true;');  // Allow edges to/from clusters
    lines.push('    node [fontname="Arial", fontsize=12];');
    lines.push('    edge [fontname="Arial", fontsize=10];');
    lines.push('');
    
    const nodeIds = shapes.map((shape, idx) => shape.nodeId || `node${idx}`);
    
    // Create clusters
    clusters.forEach((cluster, clusterIdx) => {
        lines.push(`    subgraph cluster_${clusterIdx} {`);
        lines.push(`        label="${cluster.label}";`);
        
        if (cluster.style) {
            if (cluster.style.fillcolor) lines.push(`        fillcolor="${cluster.style.fillcolor}";`);
            if (cluster.style.style) lines.push(`        style="${cluster.style.style}";`);
            if (cluster.style.penwidth) lines.push(`        penwidth=${cluster.style.penwidth};`);
        }
        
        lines.push('');
        
        // Add nodes in this cluster
        cluster.shapeIndices.forEach(idx => {
            const shape = shapes[idx];
            const catalogShape = shapeCatalog[shape.shapeId];
            if (!catalogShape) return;
            
            const nodeId = nodeIds[idx];
            const colors = getGraphvizColor(catalogShape.color);
            const graphvizHint = catalogShape.renderHints.graphviz;
            
            lines.push(`        ${nodeId} [`);
            lines.push(`            label="${shape.label}"`);
            lines.push(`            shape=${graphvizHint?.shape || 'box'}`);
            lines.push(`            style="${graphvizHint?.style || 'filled'}"`);
            lines.push(`            fillcolor="${colors.fill}"`);
            lines.push(`        ];`);
        });
        
        lines.push('    }');
        lines.push('');
    });
    
    // Add shapes not in any cluster
    const clusteredIndices = new Set(clusters.flatMap(c => c.shapeIndices));
    shapes.forEach((shape, idx) => {
        if (clusteredIndices.has(idx)) return;
        
        const catalogShape = shapeCatalog[shape.shapeId];
        if (!catalogShape) return;
        
        const nodeId = nodeIds[idx];
        const colors = getGraphvizColor(catalogShape.color);
        const graphvizHint = catalogShape.renderHints.graphviz;
        
        lines.push(`    ${nodeId} [`);
        lines.push(`        label="${shape.label}"`);
        lines.push(`        shape=${graphvizHint?.shape || 'box'}`);
        lines.push(`        style="${graphvizHint?.style || 'filled'}"`);
        lines.push(`        fillcolor="${colors.fill}"`);
        lines.push(`    ];`);
    });
    
    lines.push('');
    
    // Render connections (same as before)
    connections.forEach(conn => {
        if (conn.sourceIndex < 0 || conn.sourceIndex >= nodeIds.length ||
            conn.targetIndex < 0 || conn.targetIndex >= nodeIds.length) {
            return;
        }
        
        const source = nodeIds[conn.sourceIndex];
        const target = nodeIds[conn.targetIndex];
        const attrs: string[] = [];
        
        if (conn.label) attrs.push(`label="${conn.label}"`);
        if (conn.style === 'dashed') attrs.push('style=dashed');
        else if (conn.style === 'dotted') attrs.push('style=dotted');
        else if (conn.style === 'bold') attrs.push('style=bold', 'penwidth=2');
        
        const attrStr = attrs.length > 0 ? ` [${attrs.join(', ')}]` : '';
        lines.push(`    ${source} -> ${target}${attrStr};`);
    });
    
    lines.push('}');
    
    return lines.join('\n');
}
