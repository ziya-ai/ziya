/**
 * DrawIO Renderer
 * 
 * Renders architecture shapes from the universal catalog as DrawIO XML.
 * Uses the mxGraph XML format compatible with diagrams.net
 */

import { 
    type ArchitectureShape, 
    COLOR_PALETTES,
    escapeXml,
} from '../architectureShapesCatalog';
import { iconRegistry } from '../iconRegistry';

export interface DrawIOShape {
    /** Shape ID from catalog (e.g., "aws_lambda") */
    shapeId: string;
    
    /** Label to display on the shape */
    label: string;
    
    /** X coordinate in pixels */
    x: number;
    
    /** Y coordinate in pixels */
    y: number;
    
    /** Optional width override (uses catalog default if not specified) */
    width?: number;
    
    /** Optional height override (uses catalog default if not specified) */
    height?: number;
    
    /** Optional custom styles to merge with default */
    customStyles?: Record<string, string | number>;
}

export interface DrawIOConnection {
    /** Index of source shape in shapes array */
    sourceIndex: number;
    
    /** Index of target shape in shapes array */
    targetIndex: number;
    
    /** Optional label for the connection */
    label?: string;
    
    /** Optional style overrides */
    style?: 'solid' | 'dashed' | 'dotted';
    
    /** Optional custom edge styles */
    customStyles?: Record<string, string | number>;
}

/**
 * Style object to DrawIO style string converter
 */
function styleToString(styleObj: Record<string, string | number | boolean>): string {
    const parts: string[] = [];
    
    for (const [key, value] of Object.entries(styleObj)) {
        if (value === true) {
            parts.push(key);
        } else if (value !== false && value !== null && value !== undefined) {
            parts.push(`${key}=${value}`);
        }
    }
    
    return parts.join(';');
}

/**
 * Standard connection points for AWS resource icons
 */
const STANDARD_CONNECTION_POINTS = '[[0,0,0],[0.25,0,0],[0.5,0,0],[0.75,0,0],[1,0,0],[0,1,0],[0.25,1,0],[0.5,1,0],[0.75,1,0],[1,1,0],[0,0.25,0],[0,0.5,0],[0,0.75,0],[1,0.25,0],[1,0.5,0],[1,0.75,0]]';

/**
 * Render a single shape as DrawIO XML
 */
export async function renderShapeAsDrawIO(
    catalogShape: ArchitectureShape,
    label: string,
    x: number,
    y: number,
    width?: number,
    height?: number,
    customStyles?: Record<string, string | number>
): { xml: string; cellId: string } {
    const cellId = `cell_${Math.random().toString(36).substr(2, 9)}`;
    const w = width || catalogShape.defaultSize?.width || 78;
    const h = height || catalogShape.defaultSize?.height || 78;
    
    const colorPalette = COLOR_PALETTES[catalogShape.color];
    
    // Check if this is an AWS resource icon (most common pattern)
    const drawioHint = catalogShape.renderHints.drawio;
    if (drawioHint?.shape === 'mxgraph.aws4.resourceIcon' && drawioHint.resIcon) {
        // Extract provider and icon ID from catalog shape
        const provider = catalogShape.provider?.id || 'aws';
        const iconId = drawioHint.resIcon.replace('mxgraph.aws4.', '');
        
        // Try to get icon as data URI
        const iconDataUri = await iconRegistry.getIconAsDataUri(provider, iconId);
        
        if (iconDataUri) {
            // Use image with icon
            const baseStyle = {
                'shape': 'image',
                'image': iconDataUri,
                'aspect': 'fixed',
                'verticalLabelPosition': 'bottom',
                'verticalAlign': 'top',
                'align': 'center',
                'html': 1,
                'fontSize': 12,
                'fontColor': colorPalette.text,
            };
            
            const style = styleToString({ ...baseStyle, ...(customStyles || {}) });
            
            const xml = `<mxCell id="${cellId}" value="${escapeXml(label)}" style="${style}" vertex="1" parent="1">
        <mxGeometry x="${x}" y="${y}" width="${w}" height="${h}" as="geometry" />
      </mxCell>`;
            
            return { xml, cellId };
        }
        
        // Fallback to colored box if icon not available
        console.warn(`Icon not available for ${provider}:${iconId}, using colored box`);
        const baseStyle = {
            'sketch': 0,
            'points': STANDARD_CONNECTION_POINTS,
            'outlineConnect': 0,
            'fontColor': colorPalette.text,
            'gradientColor': colorPalette.secondary,
            'gradientDirection': 'north',
            'fillColor': colorPalette.primary,
            'strokeColor': '#ffffff',
            'dashed': 0,
            'verticalLabelPosition': 'bottom',
            'verticalAlign': 'top',
            'align': 'center',
            'html': 1,
            'fontSize': 12,
            'fontStyle': 0,
            'aspect': 'fixed',
            'shape': drawioHint.shape,
            'resIcon': drawioHint.resIcon,
        };
        
        const style = styleToString({ ...baseStyle, ...(customStyles || {}) });
        
        const xml = `<mxCell id="${cellId}" value="${escapeXml(label)}" style="${style}" vertex="1" parent="1">
        <mxGeometry x="${x}" y="${y}" width="${w}" height="${h}" as="geometry" />
      </mxCell>`;
        
        return { xml, cellId };
    }
    
    // Generic shape (rectangle, ellipse, etc.)
    const baseGenericStyle: Record<string, string | number> = {
        'whiteSpace': 'wrap',
        'html': 1,
        'fillColor': colorPalette.secondary,
        'strokeColor': colorPalette.primary,
        'fontColor': colorPalette.text,
        'fontSize': 12,
    };
    
    // Add shape-specific properties
    if (drawioHint?.shape) {
        if (drawioHint.shape === 'rectangle') {
            // Default rectangle, no special properties needed
        } else if (drawioHint.shape === 'ellipse') {
            baseGenericStyle['ellipse'] = 1;
        } else if (drawioHint.shape === 'rhombus') {
            baseGenericStyle['rhombus'] = 1;
        } else if (drawioHint.shape === 'cylinder') {
            baseGenericStyle['shape'] = 'cylinder';
        } else if (drawioHint.shape === 'hexagon') {
            baseGenericStyle['shape'] = 'hexagon';
        } else if (drawioHint.shape === 'parallelogram') {
            baseGenericStyle['shape'] = 'parallelogram';
        } else if (drawioHint.shape === 'actor') {
            baseGenericStyle['shape'] = 'actor';
        } else if (drawioHint.shape === 'cloud') {
            baseGenericStyle['shape'] = 'cloud';
        } else {
            baseGenericStyle['shape'] = drawioHint.shape;
        }
    }
    
    const style = styleToString({ ...baseGenericStyle, ...(customStyles || {}) });
    
    const xml = `<mxCell id="${cellId}" value="${escapeXml(label)}" style="${style}" vertex="1" parent="1">
        <mxGeometry x="${x}" y="${y}" width="${w}" height="${h}" as="geometry" />
      </mxCell>`;
    
    return { xml, cellId };
}

/**
 * Generate a connection between two shapes
 */
function renderConnectionAsDrawIO(
    sourceId: string,
    targetId: string,
    label: string = '',
    style: 'solid' | 'dashed' | 'dotted' = 'solid',
    customStyles?: Record<string, string | number>
): string {
    const connId = `edge_${Math.random().toString(36).substr(2, 9)}`;
    
    const baseStyle: Record<string, string | number> = {
        'edgeStyle': 'orthogonalEdgeStyle',
        'rounded': 0,
        'html': 1,
        'jettySize': 'auto',
        'orthogonalLoop': 1,
        'strokeColor': '#232F3E',
        'endArrow': 'classic',
        'endSize': 6,
    };
    
    // Apply style variations
    if (style === 'dashed') {
        baseStyle['dashed'] = 1;
        baseStyle['dashPattern'] = '3 3';
    } else if (style === 'dotted') {
        baseStyle['dashed'] = 1;
        baseStyle['dashPattern'] = '1 2';
    }
    
    const styleStr = styleToString({ ...baseStyle, ...(customStyles || {}) });
    
    return `<mxCell id="${connId}" value="${escapeXml(label)}" style="${styleStr}" edge="1" parent="1" source="${sourceId}" target="${targetId}">
        <mxGeometry relative="1" as="geometry" />
      </mxCell>`;
}

/**
 * Generate complete DrawIO diagram XML from catalog shapes
 * 
 * @param shapes - Array of shapes to render (uses catalog shape IDs)
 * @param connections - Array of connections between shapes
 * @param shapeCatalog - Map of shape ID to ArchitectureShape definition (from MCP tool)
 * @param title - Diagram title
 * @returns Complete DrawIO XML string
 */
export async function generateDrawIOFromCatalog(
    shapes: DrawIOShape[],
    connections: DrawIOConnection[],
    shapeCatalog: Record<string, ArchitectureShape>,
    title: string = 'Architecture Diagram'
): string {
    const renderedShapes: Array<{ xml: string; cellId: string }> = [];
    
    // Render each shape
    for (const shape of shapes) {
        const catalogShape = shapeCatalog[shape.shapeId];
        if (!catalogShape) {
            console.warn(`Shape not found in catalog: ${shape.shapeId}`);
            continue;
        }
        
        const rendered = await renderShapeAsDrawIO(
            catalogShape,
            shape.label,
            shape.x,
            shape.y,
            shape.width,
            shape.height,
            shape.customStyles
        );
        
        renderedShapes.push(rendered);
    }
    
    // Render connections
    const connectionXmls: string[] = [];
    for (const conn of connections) {
        if (conn.sourceIndex < 0 || conn.sourceIndex >= renderedShapes.length ||
            conn.targetIndex < 0 || conn.targetIndex >= renderedShapes.length) {
            console.warn('Invalid connection:', conn);
            continue;
        }
        
        const sourceCell = renderedShapes[conn.sourceIndex];
        const targetCell = renderedShapes[conn.targetIndex];
        
        const connXml = renderConnectionAsDrawIO(
            sourceCell.cellId,
            targetCell.cellId,
            conn.label || '',
            conn.style || 'solid',
            conn.customStyles
        );
        
        connectionXmls.push(connXml);
    }
    
    // Combine all cells
    const allCells = renderedShapes.map(s => s.xml).concat(connectionXmls).join('\n');
    
    // Generate complete DrawIO XML document
    return `<?xml version="1.0" encoding="UTF-8"?>
<mxfile host="ziya" modified="${new Date().toISOString()}" version="1.0">
  <diagram name="${escapeXml(title)}">
    <mxGraphModel dx="1200" dy="800" grid="1" gridSize="10" guides="1" tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" pageWidth="850" pageHeight="1100">
      <root>
        <mxCell id="0" />
        <mxCell id="1" parent="0" />
${allCells}
      </root>
    </mxGraphModel>
  </diagram>
</mxfile>`;
}
