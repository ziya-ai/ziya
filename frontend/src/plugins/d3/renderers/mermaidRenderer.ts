/**
 * Mermaid Renderer
 * 
 * Renders architecture shapes from the universal catalog as Mermaid diagrams.
 * Supports flowcharts and architecture diagrams.
 */

import { 
    type ArchitectureShape,
    COLOR_PALETTES,
} from '../architectureShapesCatalog';
import { iconRegistry } from '../iconRegistry';

export interface MermaidShape {
    /** Shape ID from catalog */
    shapeId: string;
    
    /** Label to display */
    label: string;
    
    /** Optional custom node ID (auto-generated if not provided) */
    nodeId?: string;
}

export interface MermaidConnection {
    /** Index of source shape in shapes array */
    sourceIndex: number;
    
    /** Index of target shape in shapes array */
    targetIndex: number;
    
    /** Optional label for the connection */
    label?: string;
    
    /** Connection style */
    style?: 'solid' | 'dashed' | 'dotted' | 'thick';
}

/**
 * Get Mermaid node syntax with icon support
 */
async function getMermaidNodeSyntax(shape: ArchitectureShape, label: string, nodeId: string): Promise<string> {
    // Try to get icon URL for embedding in Mermaid
    const provider = shape.provider?.id || 'aws';
    const drawioHint = shape.renderHints.drawio;
    
    if (drawioHint?.resIcon) {
        const iconId = drawioHint.resIcon.replace('mxgraph.aws4.', '');
        const blobUrl = await iconRegistry.getIconAsBlobUrl(provider, iconId);
        
        if (blobUrl) {
            // Mermaid supports image nodes (limited browser support)
            return `${nodeId}[<img src='${blobUrl}' width='32'/> ${label}]`;
        }
    }
    
    const drawioShape = shape.renderHints.drawio?.shape;
    
    // For shapes with custom icons, use icon syntax
    const mermaidIcon = shape.renderHints.mermaid?.icon;
    if (mermaidIcon && mermaidIcon.startsWith('aws-')) {
        // AWS architecture diagram syntax (Mermaid v10+)
        return `${nodeId}[["${label}"]]`;
    } else if (mermaidIcon && (mermaidIcon.length === 1 || mermaidIcon.length === 2)) {
        // Emoji icon
        return `${nodeId}["${mermaidIcon} ${label}"]`;
    }
    
    // Map DrawIO shapes to Mermaid node shapes
    // Mermaid shape syntax:
    // [] = rectangle
    // [()] = stadium (rounded rectangle)
    // {{}} = diamond
    // [([])] = cylindrical
    // (()) = circle
    // [/\] = trapezoid
    // [\] = inverted trapezoid
    
    if (drawioShape === 'rhombus' || shape.id.includes('diamond')) {
        return `${nodeId}{{"${label}"}}`;
    } else if (drawioShape === 'cylinder' || shape.category === 'database') {
        return `${nodeId}[("${label}")]`;
    } else if (drawioShape === 'ellipse' || shape.id.includes('ellipse')) {
        return `${nodeId}(("${label}"))`;
    } else if (shape.id.includes('rounded')) {
        return `${nodeId}(["${label}"])`;
    } else if (drawioShape === 'hexagon') {
        return `${nodeId}{{"${label}"}}`;
    } else if (drawioShape === 'parallelogram') {
        return `${nodeId}[/"${label}"/]`;
    } else if (drawioShape === 'actor') {
        return `${nodeId}(["👤 ${label}"])`;
    } else if (drawioShape === 'cloud') {
        return `${nodeId}(["☁️ ${label}"])`;
    }
    
    // Default: rectangle
    return `${nodeId}["${label}"]`;
}

/**
 * Generate Mermaid diagram from catalog shapes
 * 
 * @param shapes - Array of shapes to render
 * @param connections - Array of connections between shapes
 * @param shapeCatalog - Map of shape ID to ArchitectureShape definition
 * @param diagramType - Type of Mermaid diagram (flowchart or graph)
 * @param direction - Flow direction (LR=left-to-right, TB=top-to-bottom, etc.)
 * @returns Complete Mermaid diagram string
 */
export function generateMermaidFromCatalog(
    shapes: MermaidShape[],
    connections: MermaidConnection[],
    shapeCatalog: Record<string, ArchitectureShape>,
    diagramType: 'graph' | 'flowchart' = 'flowchart',
    direction: 'LR' | 'TB' | 'RL' | 'BT' = 'LR'
): string {
    const lines: string[] = [];
    
    // Header
    lines.push(`${diagramType} ${direction}`);
    lines.push('');
    
    // Generate node IDs
    const nodeIds = shapes.map((shape, idx) => shape.nodeId || `node${idx}`);
    
    // Render shapes as nodes
    shapes.forEach((shape, idx) => {
        const catalogShape = shapeCatalog[shape.shapeId];
        if (!catalogShape) {
            console.warn(`Shape not found in catalog: ${shape.shapeId}`);
            return;
        }
        
        const nodeId = nodeIds[idx];
        const nodeSyntax = getMermaidNodeSyntax(catalogShape, shape.label, nodeId);
        
        // Add CSS class for styling
        lines.push(`    ${nodeSyntax}:::${catalogShape.category}`);
    });
    
    lines.push('');
    
    // Render connections
    connections.forEach(conn => {
        if (conn.sourceIndex < 0 || conn.sourceIndex >= nodeIds.length ||
            conn.targetIndex < 0 || conn.targetIndex >= nodeIds.length) {
            console.warn('Invalid connection:', conn);
            return;
        }
        
        const sourceId = nodeIds[conn.sourceIndex];
        const targetId = nodeIds[conn.targetIndex];
        
        // Choose arrow style
        let arrow = '-->';
        if (conn.style === 'dashed') {
            arrow = '-.->';
        } else if (conn.style === 'dotted') {
            arrow = '-..->';
        } else if (conn.style === 'thick') {
            arrow = '==>';
        }
        
        if (conn.label) {
            lines.push(`    ${sourceId} ${arrow}|${conn.label}| ${targetId}`);
        } else {
            lines.push(`    ${sourceId} ${arrow} ${targetId}`);
        }
    });
    
    lines.push('');
    
    // Add style classes (using AWS color palette)
    lines.push('    classDef compute fill:#F78E04,stroke:#D05C17,color:#232F3E');
    lines.push('    classDef container fill:#F78E04,stroke:#D05C17,color:#232F3E');
    lines.push('    classDef storage fill:#7AA116,stroke:#759C3E,color:#232F3E');
    lines.push('    classDef database fill:#5294CF,stroke:#2E73B8,color:#232F3E');
    lines.push('    classDef networking fill:#945DF2,stroke:#5A30B5,color:#fff');
    lines.push('    classDef security fill:#DD344C,stroke:#C7131F,color:#fff');
    lines.push('    classDef integration fill:#F34482,stroke:#BC1356,color:#fff');
    lines.push('    classDef analytics fill:#F58536,stroke:#D86613,color:#232F3E');
    lines.push('    classDef management fill:#F34482,stroke:#BC1356,color:#fff');
    lines.push('    classDef developer_tools fill:#5294CF,stroke:#2E73B8,color:#232F3E');
    lines.push('    classDef ml fill:#7AA116,stroke:#759C3E,color:#232F3E');
    lines.push('    classDef iot fill:#7AA116,stroke:#759C3E,color:#232F3E');
    lines.push('    classDef application fill:#F34482,stroke:#BC1356,color:#fff');
    lines.push('    classDef generic fill:#dae8fc,stroke:#6c8ebf,color:#232F3E');
    
    return lines.join('\n');
}
