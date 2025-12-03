/**
 * Architecture Shapes Catalog - TypeScript Types
 * 
 * Type definitions for the universal architecture shapes catalog.
 * The actual catalog data comes from the backend MCP tools.
 * 
 * This provides type safety when working with shapes in renderers.
 */

export type ShapeCategory = 
    | 'compute' 
    | 'storage' 
    | 'database' 
    | 'networking' 
    | 'security'
    | 'integration'
    | 'analytics'
    | 'management'
    | 'container'
    | 'developer_tools'
    | 'ml'
    | 'iot'
    | 'application'
    | 'generic';

export type ColorPalette = 
    | 'orange'   // Compute
    | 'green'    // Storage
    | 'blue'     // Database
    | 'purple'   // Networking
    | 'red'      // Security
    | 'pink'     // Integration/Management
    | 'neutral'  // Generic shapes
    | 'gray';    // Other

/**
 * Tool-agnostic shape definition
 */
export interface ArchitectureShape {
    /** Unique identifier */
    id: string;
    
    /** Display name */
    name: string;
    
    /** Category for grouping */
    category: ShapeCategory;
    
    /** Color palette */
    color: ColorPalette;
    
    /** Human-readable description */
    description: string;
    
    /** Search keywords */
    keywords: string[];
    
    /** Default dimensions */
    defaultSize?: { width: number; height: number };
    
    /** Provider information */
    provider?: {
        id: string;
        name: string;
    };
    
    /** Tool-specific rendering hints */
    renderHints: {
        drawio?: {
            shape: string;
            resIcon: string;
        };
        mermaid?: {
            icon: string;
            style?: string;
        };
        graphviz?: {
            shape: string;
            style?: string;
        };
    };
}

/**
 * Color palette definitions
 */
export const COLOR_PALETTES: Record<ColorPalette, {
    name: string;
    primary: string;
    secondary: string;
    text: string;
}> = {
    orange: { name: 'Orange (Compute)', primary: '#D05C17', secondary: '#F78E04', text: '#232F3E' },
    green: { name: 'Green (Storage)', primary: '#759C3E', secondary: '#7AA116', text: '#232F3E' },
    blue: { name: 'Blue (Database)', primary: '#2E73B8', secondary: '#5294CF', text: '#232F3E' },
    purple: { name: 'Purple (Networking)', primary: '#5A30B5', secondary: '#945DF2', text: '#232F3E' },
    red: { name: 'Red (Security)', primary: '#C7131F', secondary: '#DD344C', text: '#232F3E' },
    pink: { name: 'Pink (Integration)', primary: '#BC1356', secondary: '#F34482', text: '#232F3E' },
    neutral: { name: 'Neutral Gray', primary: '#AAB7B8', secondary: '#F2F3F4', text: '#232F3E' },
    gray: { name: 'Dark Gray', primary: '#566573', secondary: '#AAB7B8', text: '#FFFFFF' },
};

/**
 * Helper to escape XML special characters
 */
export function escapeXml(str: string): string {
    return str
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&apos;');
}
