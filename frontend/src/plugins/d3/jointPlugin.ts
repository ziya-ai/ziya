import type { dia, shapes } from '@joint/core';

import { D3RenderPlugin } from '../../types/d3';
import { isDiagramDefinitionComplete } from '../../utils/diagramUtils';
import { extractDefinitionFromYAML } from '../../utils/diagramUtils';

export interface JointSpec {
    type: 'joint' | 'jointjs' | 'diagram';
    isStreaming?: boolean;
    forceRender?: boolean;
    definition?: string;
    elements?: JointElement[];
    connections?: JointLink[];
    layout?: string | {
        type: 'hierarchical' | 'force' | 'grid' | 'manual';
        options?: any;
    };
    theme?: 'light' | 'dark' | 'auto';
    width?: number;
    height?: number;
    shapeLibrary?: 'basic' | 'electrical' | 'network' | 'uml' | 'custom';
    interactive?: boolean;
    autoLayout?: boolean;
    grid?: boolean;
    snapToGrid?: boolean;
}

// Add missing JointPluginOptions interface
export interface JointPluginOptions {
    theme?: 'light' | 'dark';
    width?: number;
    height?: number;
    gridSize?: number;
    showGrid?: boolean;
    interactive?: boolean;
    onElementSelect?: (id: string, element: any) => void;
    onLinkSelect?: (id: string, link: any) => void;
    onElementEdit?: (id: string, element: any) => void;
    onElementMove?: (id: string, position: { x: number; y: number }) => void;
    onElementResize?: (id: string, size: { width: number; height: number }) => void;
    onLinkChange?: (id: string, link: any) => void;
    onCanvasClick?: () => void;
}

// Add D3Plugin interface if not already defined
export interface D3Plugin {
    name: string;
    priority: number;
    initialize: (container: HTMLElement, options?: any) => JointInstance;
}

export interface JointInstance {
    graph: any; // dia.Graph - using any to avoid eager import
    paper: any; // dia.Paper
    theme: 'light' | 'dark';
    addElement: (elementSpec: JointElement) => any; // dia.Element
    addLink: (linkSpec: JointLink) => any; // dia.Link
    updateElement: (id: string, updates: Partial<JointElement>) => void;
    updateLink: (id: string, updates: Partial<JointLink>) => void;
    removeElement: (id: string) => void;
    removeLink: (id: string) => void;
    clear: () => void;
    fitToContent: () => void;
    zoomIn: () => void;
    zoomOut: () => void;
    resetZoom: () => void;
    exportSVG: () => string;
    exportJSON: () => any;
    importJSON: (data: any) => void;
    getElements: () => any[]; // dia.Element[]
    getLinks: () => any[]; // dia.Link[]
    getElementById: (id: string) => any | null;
    getLinkById: (id: string) => any | null;
    setTheme: (theme: 'light' | 'dark') => void;
    enableInteraction: () => void;
    disableInteraction: () => void;
    highlightElement: (id: string, highlight?: boolean) => void;
    selectElement: (id: string) => void;
    getElementAt: (x: number, y: number) => any | null;
    addPort: (elementId: string, portSpec: Port) => void;
    removePort: (elementId: string, portId: string) => void;
    getElementPorts: (elementId: string) => any[];
}

export interface JointElement {
    id: string;
    shape?: string; // circle, rect, ellipse, diamond, hexagon, etc.
    category?: string; // For grouping and styling
    elementType?: string; // For specialized elements (switch, router, resistor, etc.)
    type?: string; // Shape type (rect, circle, etc.)
    position?: { x: number; y: number } | [number, number];
    size?: { width: number; height: number };
    attrs?: any;
    text?: string;
    label?: string;
    ports?: Port[];
    icon?: string; // For network/circuit elements
    value?: string; // For electrical elements
    group?: string;
}

interface JointLink {
    id: string;
    source: string | { id: string; port?: string; anchor?: { name: string }; connectionPoint?: { name: string } };
    target: string | { id: string; port?: string; anchor?: { name: string }; connectionPoint?: { name: string } };
    label?: string;
    labels?: any[];
    vertices?: { x: number; y: number }[];
    router?: 'orthogonal' | 'manhattan' | 'metro' | 'normal';
    connector?: 'rounded' | 'smooth' | 'jumpover' | 'normal';
    attrs?: any;
}

export interface Port {
    id: string;
    position?: string;
    type?: 'input' | 'output' | 'inout';
    label?: string;
    attrs?: any;
}

// Type guard to check if a spec is for Joint.js
const isJointSpec = (spec: any): spec is JointSpec => {
    return (
        typeof spec === 'object' &&
        spec !== null &&
        (spec.type === 'joint' || spec.type === 'jointjs' || spec.type === 'diagram') &&
        (typeof spec.definition === 'string' ||
            (spec.elements && typeof spec.elements === 'object'))
    );
};

// Enhanced shape registry with electrical and network components
const createShapeRegistry = () => {
    return {
        // Default fallback
        'default': (spec: JointElement, theme: 'light' | 'dark') => createEnhancedRectElement(spec, theme),

        // Enhanced basic shapes with better styling
        rect: (spec: JointElement, theme: 'light' | 'dark') => createEnhancedRectElement(spec, theme),
        square: (spec: JointElement, theme: 'light' | 'dark') => createEnhancedRectElement({ ...spec, size: { width: 80, height: 80 } }, theme),
        circle: (spec: JointElement, theme: 'light' | 'dark') => createEnhancedCircleElement(spec, theme),
        ellipse: (spec: JointElement, theme: 'light' | 'dark') => createEnhancedEllipseElement(spec, theme),
        diamond: (spec: JointElement, theme: 'light' | 'dark') => createEnhancedDiamondElement(spec, theme),
        hexagon: (spec: JointElement, theme: 'light' | 'dark') => createHexagonElement(spec, theme),
        cylinder: (spec: JointElement, theme: 'light' | 'dark') => createCylinderElement(spec, theme),
        actor: (spec: JointElement, theme: 'light' | 'dark') => createActorElement(spec, theme),

        // Process/workflow shapes
        process: (spec: JointElement, theme: 'light' | 'dark') => createEnhancedRectElement({ ...spec, size: spec.size || { width: 140, height: 60 } }, theme),
        decision: (spec: JointElement, theme: 'light' | 'dark') => createEnhancedDiamondElement(spec, theme),
        start: (spec: JointElement, theme: 'light' | 'dark') => createEnhancedCircleElement({ ...spec, size: spec.size || { width: 80, height: 80 } }, theme),
        end: (spec: JointElement, theme: 'light' | 'dark') => createEnhancedCircleElement({ ...spec, size: spec.size || { width: 80, height: 80 } }, theme),

        // Additional common shapes
        node: (spec: JointElement, theme: 'light' | 'dark') => createEnhancedCircleElement(spec, theme),
        box: (spec: JointElement, theme: 'light' | 'dark') => createEnhancedRectElement(spec, theme),
        oval: (spec: JointElement, theme: 'light' | 'dark') => createEnhancedEllipseElement(spec, theme),
        rhombus: (spec: JointElement, theme: 'light' | 'dark') => createEnhancedDiamondElement(spec, theme),

        // Aliases for common names
        rectangle: (spec: JointElement, theme: 'light' | 'dark') => createEnhancedRectElement(spec, theme),

        // Database shapes
        database: (spec: JointElement, theme: 'light' | 'dark') => createCylinderElement(spec, theme),
        storage: (spec: JointElement, theme: 'light' | 'dark') => createEnhancedRectElement(spec, theme),

        // Network components
        message: (spec: JointElement, theme: 'light' | 'dark') => createMessageElement(spec, theme),
        document: (spec: JointElement, theme: 'light' | 'dark') => createDocumentElement(spec, theme),

        // System shapes
        component: (spec: JointElement, theme: 'light' | 'dark') => createComponentElement(spec, theme),
        module: (spec: JointElement, theme: 'light' | 'dark') => createModuleElement(spec, theme),

        // UML shapes  
        class: (spec: JointElement, theme: 'light' | 'dark') => createEnhancedUMLElement(spec, 'class', theme),
        interface: (spec: JointElement, theme: 'light' | 'dark') => createEnhancedUMLElement(spec, 'interface', theme),
        package: (spec: JointElement, theme: 'light' | 'dark') => createEnhancedUMLElement(spec, 'package', theme),
        note: (spec: JointElement, theme: 'light' | 'dark') => createNoteElement(spec, theme),

        // Flowchart shapes
        subprocess: (spec: JointElement, theme: 'light' | 'dark') => createSubprocessElement(spec, theme),
        manual: (spec: JointElement, theme: 'light' | 'dark') => createManualElement(spec, theme),
        data: (spec: JointElement, theme: 'light' | 'dark') => createDataElement(spec, theme),

        // Network components
        router: (spec: JointElement, theme: 'light' | 'dark') => createNetworkElement(spec, 'router', theme),
        switch: (spec: JointElement, theme: 'light' | 'dark') => createNetworkElement(spec, 'switch', theme),
        server: (spec: JointElement, theme: 'light' | 'dark') => createNetworkElement(spec, 'server', theme),
        firewall: (spec: JointElement, theme: 'light' | 'dark') => createNetworkElement(spec, 'firewall', theme),
        cloud: (spec: JointElement, theme: 'light' | 'dark') => createNetworkElement(spec, 'cloud', theme),

        // Electrical components
        resistor: (spec: JointElement, theme: 'light' | 'dark') => createElectricalElement(spec, 'resistor', theme),
        capacitor: (spec: JointElement, theme: 'light' | 'dark') => createElectricalElement(spec, 'capacitor', theme),
        inductor: (spec: JointElement, theme: 'light' | 'dark') => createElectricalElement(spec, 'inductor', theme),
        battery: (spec: JointElement, theme: 'light' | 'dark') => createElectricalElement(spec, 'battery', theme),
        ground: (spec: JointElement, theme: 'light' | 'dark') => createElectricalElement(spec, 'ground', theme),
        voltage_source: (spec: JointElement, theme: 'light' | 'dark') => createElectricalElement(spec, 'voltage_source', theme),
        current_source: (spec: JointElement, theme: 'light' | 'dark') => createElectricalElement(spec, 'current_source', theme),
        diode: (spec: JointElement, theme: 'light' | 'dark') => createElectricalElement(spec, 'diode', theme),
        transistor: (spec: JointElement, theme: 'light' | 'dark') => createElectricalElement(spec, 'transistor', theme),

    };
};

// Create diamond-shaped element
const createEnhancedRectElement = (elementSpec: JointElement, theme: 'light' | 'dark') => {
    const position = Array.isArray(elementSpec.position) ?
        { x: elementSpec.position[0], y: elementSpec.position[1] } :
        elementSpec.position || { x: 0, y: 0 };
    const size = elementSpec.size || { width: 120, height: 80 };
    const text = elementSpec.text || elementSpec.label || elementSpec.id;

    // Access shapes from the global scope set by render()
    const { shapes } = (globalThis as any).__jointRuntimeDeps || {};
    if (!shapes) throw new Error('Joint.js not initialized');

    return new shapes.standard.Rectangle({
        id: elementSpec.id,
        position,
        size,
        attrs: {
            body: {
                fill: theme === 'dark' ? '#4c566a' : '#ffffff',
                stroke: theme === 'dark' ? '#88c0d0' : '#2c3e50',
                strokeWidth: 2,
                rx: 8,
                ry: 8,
                magnet: true,
                filter: theme === 'dark' ? 'drop-shadow(2px 2px 4px rgba(0,0,0,0.5))' : 'drop-shadow(2px 2px 4px rgba(0,0,0,0.2))'
            },
            label: {
                text: text,
                fill: theme === 'dark' ? '#eceff4' : '#2c3e50',
                fontSize: 14,
                fontFamily: 'Arial, sans-serif',
                fontWeight: 'bold',
                textAnchor: 'middle',
                textVerticalAnchor: 'middle'
            }
        }
    });
};

const createEnhancedCircleElement = (elementSpec: JointElement, theme: 'light' | 'dark') => {
    const position = Array.isArray(elementSpec.position) ?
        { x: elementSpec.position[0], y: elementSpec.position[1] } :
        elementSpec.position || { x: 0, y: 0 };
    const size = elementSpec.size || { width: 80, height: 80 };
    const text = elementSpec.text || elementSpec.label || elementSpec.id;

    // Access shapes from the global scope set by render()
    const { shapes } = (globalThis as any).__jointRuntimeDeps || {};
    if (!shapes) throw new Error('Joint.js not initialized');

    return new shapes.standard.Circle({
        id: elementSpec.id,
        position,
        size,
        attrs: {
            body: {
                fill: theme === 'dark' ? '#5e81ac' : '#3498db',
                stroke: theme === 'dark' ? '#88c0d0' : '#2980b9',
                strokeWidth: 3,
                magnet: true,
                filter: theme === 'dark' ? 'drop-shadow(2px 2px 6px rgba(0,0,0,0.4))' : 'drop-shadow(2px 2px 6px rgba(0,0,0,0.2))'
            },
            label: {
                text: text,
                fill: theme === 'dark' ? '#eceff4' : '#ffffff',
                fontSize: 13,
                fontFamily: 'Arial, sans-serif',
                fontWeight: 'bold',
                textAnchor: 'middle',
                textVerticalAnchor: 'middle'
            }
        }
    });
};

const createEnhancedEllipseElement = (elementSpec: JointElement, theme: 'light' | 'dark') => {
    const position = Array.isArray(elementSpec.position) ?
        { x: elementSpec.position[0], y: elementSpec.position[1] } :
        elementSpec.position || { x: 0, y: 0 };
    const size = elementSpec.size || { width: 140, height: 70 };
    const text = elementSpec.text || elementSpec.label || elementSpec.id;

    // Access shapes from the global scope set by render()
    const { shapes } = (globalThis as any).__jointRuntimeDeps || {};
    if (!shapes) throw new Error('Joint.js not initialized');

    return new shapes.standard.Ellipse({
        id: elementSpec.id,
        position,
        size,
        attrs: {
            body: {
                fill: theme === 'dark' ? '#bf616a' : '#e74c3c',
                stroke: theme === 'dark' ? '#d08770' : '#c0392b',
                strokeWidth: 2,
                magnet: true,
                filter: theme === 'dark' ? 'drop-shadow(2px 2px 4px rgba(0,0,0,0.5))' : 'drop-shadow(2px 2px 4px rgba(0,0,0,0.2))'
            },
            label: {
                text: text,
                fill: theme === 'dark' ? '#eceff4' : '#ffffff',
                fontSize: 13,
                fontFamily: 'Arial, sans-serif',
                fontWeight: 'bold',
                textAnchor: 'middle',
                textVerticalAnchor: 'middle'
            }
        }
    });
};

const createEnhancedDiamondElement = (elementSpec: JointElement, theme: 'light' | 'dark') => {
    const position = Array.isArray(elementSpec.position) ?
        { x: elementSpec.position[0], y: elementSpec.position[1] } :
        elementSpec.position || { x: 0, y: 0 };
    const size = elementSpec.size || { width: 120, height: 80 };
    const text = elementSpec.text || elementSpec.label || elementSpec.id;

    // Access shapes from the global scope set by render()
    const { shapes } = (globalThis as any).__jointRuntimeDeps || {};
    if (!shapes) throw new Error('Joint.js not initialized');

    return new shapes.standard.Polygon({
        id: elementSpec.id,
        position,
        size,
        attrs: {
            body: {
                fill: theme === 'dark' ? '#ebcb8b' : '#f39c12',
                stroke: theme === 'dark' ? '#d08770' : '#e67e22',
                strokeWidth: 2,
                refPoints: '0,10 10,0 20,10 10,20',
                magnet: true,
                filter: theme === 'dark' ? 'drop-shadow(2px 2px 4px rgba(0,0,0,0.5))' : 'drop-shadow(2px 2px 4px rgba(0,0,0,0.2))'
            },
            label: {
                text: text,
                fill: theme === 'dark' ? '#2e3440' : '#ffffff',
                fontSize: 12,
                fontFamily: 'Arial, sans-serif',
                fontWeight: 'bold',
                textAnchor: 'middle',
                textVerticalAnchor: 'middle'
            }
        }
    });
};

const createHexagonElement = (elementSpec: JointElement, theme: 'light' | 'dark') => {
    const position = Array.isArray(elementSpec.position) ?
        { x: elementSpec.position[0], y: elementSpec.position[1] } :
        elementSpec.position || { x: 0, y: 0 };
    const size = elementSpec.size || { width: 100, height: 86 };
    const text = elementSpec.text || elementSpec.label || elementSpec.id;

    // Access shapes from the global scope set by render()
    const { shapes } = (globalThis as any).__jointRuntimeDeps || {};
    if (!shapes) throw new Error('Joint.js not initialized');

    return new shapes.standard.Polygon({
        id: elementSpec.id,
        position,
        size,
        attrs: {
            body: {
                fill: theme === 'dark' ? '#a3be8c' : '#27ae60',
                stroke: theme === 'dark' ? '#8fbcbb' : '#229954',
                strokeWidth: 2,
                refPoints: '15,0 25,0 30,8.66 25,17.32 15,17.32 10,8.66',
                filter: 'drop-shadow(2px 2px 4px rgba(0,0,0,0.3))'
            },
            label: {
                text: text,
                fill: theme === 'dark' ? '#eceff4' : '#ffffff',
                fontSize: 12,
                fontFamily: 'Arial, sans-serif',
                fontWeight: 'bold',
                textAnchor: 'middle',
                textVerticalAnchor: 'middle'
            }
        }
    });
};

const createCylinderElement = (elementSpec: JointElement, theme: 'light' | 'dark') => {
    const position = Array.isArray(elementSpec.position) ?
        { x: elementSpec.position[0], y: elementSpec.position[1] } :
        elementSpec.position || { x: 0, y: 0 };
    const size = elementSpec.size || { width: 80, height: 100 };
    const text = elementSpec.text || elementSpec.label || elementSpec.id;

    // Access dia from the global scope set by render()
    const { dia } = (globalThis as any).__jointRuntimeDeps || {};
    if (!dia) throw new Error('Joint.js not initialized');

    return new dia.Element({
        id: elementSpec.id,
        position,
        size,
        markup: [
            { tagName: 'ellipse', selector: 'top' },
            { tagName: 'rect', selector: 'body' },
            { tagName: 'ellipse', selector: 'bottom' },
            { tagName: 'text', selector: 'label' }
        ],
        attrs: {
            top: {
                cx: size.width / 2,
                cy: 8,
                rx: size.width / 2 - 2,
                ry: 8,
                fill: theme === 'dark' ? '#5e81ac' : '#3498db',
                stroke: theme === 'dark' ? '#88c0d0' : '#2980b9',
                strokeWidth: 2
            },
            body: {
                x: 2,
                y: 8,
                width: size.width - 4,
                height: size.height - 16,
                fill: theme === 'dark' ? '#5e81ac' : '#3498db',
                stroke: theme === 'dark' ? '#88c0d0' : '#2980b9',
                strokeWidth: 2
            },
            bottom: {
                cx: size.width / 2,
                cy: size.height - 8,
                rx: size.width / 2 - 2,
                ry: 8,
                fill: theme === 'dark' ? '#4c566a' : '#2c3e50',
                stroke: theme === 'dark' ? '#88c0d0' : '#2980b9',
                strokeWidth: 2
            },
            label: {
                text: text,
                fill: theme === 'dark' ? '#eceff4' : '#ffffff',
                fontSize: 12,
                fontFamily: 'Arial, sans-serif',
                fontWeight: 'bold',
                textAnchor: 'middle',
                textVerticalAnchor: 'middle',
                x: size.width / 2,
                y: size.height / 2
            }
        }
    });
};

const createDiamondElement = (elementSpec: JointElement, theme: 'light' | 'dark') => {
    const position = Array.isArray(elementSpec.position) ?
        { x: elementSpec.position[0], y: elementSpec.position[1] } :
        elementSpec.position || { x: 0, y: 0 };
    const size = elementSpec.size || { width: 120, height: 80 };
    const text = elementSpec.text || elementSpec.label || elementSpec.id;

    // Access dia from the global scope set by render()
    const { dia } = (globalThis as any).__jointRuntimeDeps || {};
    if (!dia) throw new Error('Joint.js not initialized');

    const commonAttrs = {
        body: {
            fill: theme === 'dark' ? '#2f3349' : '#ffffff',
            stroke: theme === 'dark' ? '#4cc9f0' : '#333333',
            strokeWidth: 2
        },
        label: {
            text: text,
            fill: theme === 'dark' ? '#ffffff' : '#000000',
            fontSize: 12,
            fontFamily: 'Arial, sans-serif',
            textAnchor: 'middle',
            textVerticalAnchor: 'middle'
        }
    };

    // Create custom diamond shape using polygon
    const element = new dia.Element({
        id: elementSpec.id,
        position,
        size,
        markup: [{
            tagName: 'polygon',
            selector: 'body'
        }, {
            tagName: 'text',
            selector: 'label'
        }],
        attrs: {
            body: {
                ...commonAttrs.body,
                points: `${size.width / 2},0 ${size.width},${size.height / 2} ${size.width / 2},${size.height} 0,${size.height / 2}`
            },
            label: commonAttrs.label
        }
    });

    return element;
};

// Create network element with ports and specialized styling
const createNetworkElement = (elementSpec: JointElement, elementType: string, theme: 'light' | 'dark') => {
    const position = Array.isArray(elementSpec.position) ?
        { x: elementSpec.position[0], y: elementSpec.position[1] } :
        elementSpec.position || { x: 0, y: 0 };
    const size = elementSpec.size || getDefaultSizeForNetworkElement(elementType);
    const text = elementSpec.text || elementSpec.label || elementSpec.id;

    const networkAttrs = getNetworkElementAttrs(elementType, theme);
    const defaultPorts = getDefaultPortsForNetworkElement(elementType);

    // Access shapes from the global scope set by render()
    const { shapes } = (globalThis as any).__jointRuntimeDeps || {};
    if (!shapes) throw new Error('Joint.js not initialized');

    const element = new shapes.standard.Rectangle({
        id: elementSpec.id,
        position,
        size,
        attrs: {
            body: networkAttrs.body,
            label: {
                ...networkAttrs.label,
                text: text
            }
        }
    });

    // Add default ports
    defaultPorts.forEach(portSpec => {
        element.addPort(createPortFromSpec(portSpec, theme));
    });

    // Add custom ports if specified
    if (elementSpec.ports) {
        elementSpec.ports.forEach(portSpec => {
            element.addPort(createPortFromSpec(portSpec, theme));
        });
    }

    return element;
};

// Helper function to parse UML content from text
const parseUMLContent = (text: string) => {
    const lines = text.split('\n').map(line => line.trim()).filter(line => line);
    let name = 'Class';
    let attributes: string[] = [];
    let methods: string[] = [];

    let currentSection = 'name';

    for (const line of lines) {
        if (line === '---' || line === '===') {
            currentSection = currentSection === 'name' ? 'attributes' : 'methods';
            continue;
        }

        if (currentSection === 'name') {
            name = line;
        } else if (currentSection === 'attributes') {
            if (line.startsWith('+') || line.startsWith('-') || line.startsWith('#') || line.startsWith('~')) {
                attributes.push(line);
            } else {
                attributes.push(`+ ${line}`);
            }
        } else if (currentSection === 'methods') {
            if (line.startsWith('+') || line.startsWith('-') || line.startsWith('#') || line.startsWith('~')) {
                methods.push(line);
            } else {
                methods.push(`+ ${line}()`);
            }
        }
    }

    return {
        name,
        attributes,
        methods
    };
};

const createDocumentElement = (elementSpec: JointElement, theme: 'light' | 'dark') => {
    const position = Array.isArray(elementSpec.position) ?
        { x: elementSpec.position[0], y: elementSpec.position[1] } :
        elementSpec.position || { x: 0, y: 0 };
    const size = elementSpec.size || { width: 100, height: 80 };
    const text = elementSpec.text || elementSpec.label || elementSpec.id;

    // Access dia from the global scope set by render()
    const { dia } = (globalThis as any).__jointRuntimeDeps || {};
    if (!dia) throw new Error('Joint.js not initialized');

    return new dia.Element({
        id: elementSpec.id,
        position,
        size,
        markup: [
            { tagName: 'path', selector: 'body' },
            { tagName: 'text', selector: 'label' }
        ],
        attrs: {
            body: {
                d: `M 0 0 L ${size.width - 15} 0 L ${size.width} 15 L ${size.width} ${size.height} L 0 ${size.height} Z M ${size.width - 15} 0 L ${size.width - 15} 15 L ${size.width} 15`,
                fill: theme === 'dark' ? '#d08770' : '#f39c12',
                stroke: theme === 'dark' ? '#ebcb8b' : '#e67e22',
                strokeWidth: 2,
                filter: 'drop-shadow(2px 2px 4px rgba(0,0,0,0.3))'
            },
            label: {
                text: text,
                fill: theme === 'dark' ? '#2e3440' : '#ffffff',
                fontSize: 11,
                fontFamily: 'Arial, sans-serif',
                fontWeight: 'bold',
                textAnchor: 'middle',
                textVerticalAnchor: 'middle',
                x: size.width / 2,
                y: size.height / 2 + 5
            }
        }
    });
};

const createComponentElement = (elementSpec: JointElement, theme: 'light' | 'dark') => {
    const position = Array.isArray(elementSpec.position) ?
        { x: elementSpec.position[0], y: elementSpec.position[1] } :
        elementSpec.position || { x: 0, y: 0 };
    const size = elementSpec.size || { width: 120, height: 80 };
    const text = elementSpec.text || elementSpec.label || elementSpec.id;

    // Access dia from the global scope set by render()
    const { dia } = (globalThis as any).__jointRuntimeDeps || {};
    if (!dia) throw new Error('Joint.js not initialized');

    return new dia.Element({
        id: elementSpec.id,
        position,
        size,
        markup: [
            { tagName: 'rect', selector: 'body' },
            { tagName: 'rect', selector: 'tab1' },
            { tagName: 'rect', selector: 'tab2' },
            { tagName: 'text', selector: 'label' }
        ],
        attrs: {
            body: {
                x: 0, y: 10, width: size.width, height: size.height - 10,
                fill: theme === 'dark' ? '#5e81ac' : '#3498db',
                stroke: theme === 'dark' ? '#88c0d0' : '#2980b9',
                strokeWidth: 2,
                rx: 5,
                filter: 'drop-shadow(2px 2px 4px rgba(0,0,0,0.3))'
            },
            tab1: {
                x: 10, y: 0, width: 20, height: 15,
                fill: theme === 'dark' ? '#81a1c1' : '#5dade2',
                stroke: theme === 'dark' ? '#88c0d0' : '#2980b9',
                strokeWidth: 1,
                rx: 3
            },
            tab2: {
                x: 35, y: 0, width: 20, height: 15,
                fill: theme === 'dark' ? '#81a1c1' : '#5dade2',
                stroke: theme === 'dark' ? '#88c0d0' : '#2980b9',
                strokeWidth: 1,
                rx: 3
            },
            label: {
                text: text,
                fill: theme === 'dark' ? '#eceff4' : '#ffffff',
                fontSize: 12,
                fontFamily: 'Arial, sans-serif',
                fontWeight: 'bold',
                textAnchor: 'middle',
                textVerticalAnchor: 'middle',
                x: size.width / 2,
                y: size.height / 2 + 5
            }
        }
    });
};

const createStartEndElement = (elementSpec: JointElement, theme: 'light' | 'dark', type: 'start' | 'end') => {
    const position = Array.isArray(elementSpec.position) ?
        { x: elementSpec.position[0], y: elementSpec.position[1] } :
        elementSpec.position || { x: 0, y: 0 };
    const size = elementSpec.size || { width: 80, height: 40 };
    const text = elementSpec.text || elementSpec.label || elementSpec.id;
    const color = type === 'start' ?
        (theme === 'dark' ? '#a3be8c' : '#27ae60') :
        (theme === 'dark' ? '#bf616a' : '#e74c3c');

    // Access shapes from the global scope set by render()
    const { shapes } = (globalThis as any).__jointRuntimeDeps || {};
    if (!shapes) throw new Error('Joint.js not initialized');

    return new shapes.standard.Ellipse({
        id: elementSpec.id,
        position,
        size,
        attrs: {
            body: {
                fill: color,
                stroke: theme === 'dark' ? '#eceff4' : '#2c3e50',
                strokeWidth: 3,
                filter: 'drop-shadow(2px 2px 4px rgba(0,0,0,0.4))'
            },
            label: {
                text: text,
                fill: theme === 'dark' ? '#eceff4' : '#ffffff',
                fontSize: 12,
                fontFamily: 'Arial, sans-serif',
                fontWeight: 'bold',
                textAnchor: 'middle',
                textVerticalAnchor: 'middle'
            }
        }
    });
};

const createProcessElement = (elementSpec: JointElement, theme: 'light' | 'dark') => {
    const position = Array.isArray(elementSpec.position) ?
        { x: elementSpec.position[0], y: elementSpec.position[1] } :
        elementSpec.position || { x: 0, y: 0 };
    const size = elementSpec.size || { width: 140, height: 60 };
    const text = elementSpec.text || elementSpec.label || elementSpec.id;

    // Access shapes from the global scope set by render()
    const { shapes } = (globalThis as any).__jointRuntimeDeps || {};
    if (!shapes) throw new Error('Joint.js not initialized');

    return new shapes.standard.Rectangle({
        id: elementSpec.id,
        position,
        size,
        attrs: {
            body: {
                fill: theme === 'dark' ? '#81a1c1' : '#3498db',
                stroke: theme === 'dark' ? '#88c0d0' : '#2980b9',
                strokeWidth: 2,
                rx: 10,
                ry: 10,
                filter: 'drop-shadow(3px 3px 6px rgba(0,0,0,0.3))'
            },
            label: {
                text: text,
                fill: theme === 'dark' ? '#eceff4' : '#ffffff',
                fontSize: 13,
                fontFamily: 'Arial, sans-serif',
                fontWeight: 'bold',
                textAnchor: 'middle',
                textVerticalAnchor: 'middle'
            }
        }
    });
};

// Create electrical element with specialized symbols
const createElectricalElement = (elementSpec: JointElement, elementType: string, theme: 'light' | 'dark') => {
    const position = Array.isArray(elementSpec.position) ?
        { x: elementSpec.position[0], y: elementSpec.position[1] } :
        elementSpec.position || { x: 0, y: 0 };
    const size = elementSpec.size || getDefaultSizeForElectricalElement(elementType);
    const value = elementSpec.value || getDefaultValueForElement(elementType);
    const label = elementSpec.text || elementSpec.label || elementSpec.id + (value ? ` (${value})` : '');

    const electricalAttrs = getEnhancedElectricalAttrs(elementType, theme, size, label);
    const markup = getEnhancedElectricalMarkup(elementType);
    const defaultPorts = getDefaultPortsForElectricalElement(elementType);
    
    // Access dia from the global scope set by render()
    const { dia } = (globalThis as any).__jointRuntimeDeps || {};
    if (!dia) throw new Error('Joint.js not initialized');

    const element = new dia.Element({
        id: elementSpec.id,
        position,
        size,
        markup: markup,
        attrs: electricalAttrs
    });

    // Add default ports
    defaultPorts.forEach(portSpec => {
        element.addPort(createPortFromSpec(portSpec, theme));
    });

    return element;
};

// Enhanced electrical element markup with proper symbols
const getEnhancedElectricalMarkup = (elementType: string) => {
    const markups: { [key: string]: any[] } = {
        resistor: [
            { tagName: 'path', selector: 'symbol' },
            { tagName: 'text', selector: 'label' }
        ],
        capacitor: [
            { tagName: 'line', selector: 'plate1' },
            { tagName: 'line', selector: 'plate2' },
            { tagName: 'text', selector: 'label' }
        ],
        inductor: [
            { tagName: 'path', selector: 'coil' },
            { tagName: 'text', selector: 'label' }
        ],
        battery: [
            { tagName: 'line', selector: 'positive' },
            { tagName: 'line', selector: 'negative' },
            { tagName: 'text', selector: 'polarityPos' },
            { tagName: 'text', selector: 'polarityNeg' },
            { tagName: 'text', selector: 'label' }
        ],
        ground: [
            { tagName: 'path', selector: 'symbol' },
            { tagName: 'text', selector: 'label' }
        ],
        diode: [
            { tagName: 'path', selector: 'triangle' },
            { tagName: 'line', selector: 'cathode' },
            { tagName: 'text', selector: 'label' }
        ],
        voltage_source: [
            { tagName: 'circle', selector: 'body' },
            { tagName: 'text', selector: 'polarityPos' },
            { tagName: 'text', selector: 'polarityNeg' },
            { tagName: 'text', selector: 'label' }
        ],
        current_source: [
            { tagName: 'circle', selector: 'body' },
            { tagName: 'path', selector: 'arrow' },
            { tagName: 'text', selector: 'label' }
        ],
        transistor: [
            { tagName: 'circle', selector: 'body' },
            { tagName: 'line', selector: 'baseLead' },
            { tagName: 'line', selector: 'base' },
            { tagName: 'line', selector: 'collector' },
            { tagName: 'line', selector: 'emitter' },
            { tagName: 'path', selector: 'arrow' },
            { tagName: 'text', selector: 'label' }
        ]
    };

    return markups[elementType] || [
        { tagName: 'rect', selector: 'body' },
        { tagName: 'text', selector: 'label' }
    ];
};

// Enhanced electrical element attributes with proper symbols
const getEnhancedElectricalAttrs = (elementType: string, theme: 'light' | 'dark', size: { width: number; height: number }, label: string) => {
    const strokeColor = theme === 'dark' ? '#ffffff' : '#000000';
    const textColor = theme === 'dark' ? '#ffffff' : '#000000';

    const commonLabel = {
        text: label,
        fill: textColor,
        fontSize: 11,
        fontFamily: 'Arial, sans-serif',
        textAnchor: 'middle',
        textVerticalAnchor: 'top',
        x: size.width / 2,
        y: size.height + 5
    };

    const attrs: { [key: string]: any } = {
        resistor: {
            symbol: {
                d: `M 0,${size.height / 2} L ${size.width * 0.2},${size.height / 2} L ${size.width * 0.25},${size.height * 0.2} L ${size.width * 0.35},${size.height * 0.8} L ${size.width * 0.45},${size.height * 0.2} L ${size.width * 0.55},${size.height * 0.8} L ${size.width * 0.65},${size.height * 0.2} L ${size.width * 0.75},${size.height * 0.8} L ${size.width * 0.8},${size.height / 2} L ${size.width},${size.height / 2}`,
                fill: 'none',
                stroke: strokeColor,
                strokeWidth: 2
            },
            label: commonLabel
        },
        capacitor: {
            plate1: {
                x1: size.width * 0.45,
                y1: size.height * 0.2,
                x2: size.width * 0.45,
                y2: size.height * 0.8,
                stroke: strokeColor,
                strokeWidth: 3
            },
            plate2: {
                x1: size.width * 0.55,
                y1: size.height * 0.2,
                x2: size.width * 0.55,
                y2: size.height * 0.8,
                stroke: strokeColor,
                strokeWidth: 3
            },
            label: commonLabel
        },
        battery: {
            positive: {
                x1: size.width * 0.4,
                y1: size.height * 0.2,
                x2: size.width * 0.4,
                y2: size.height * 0.8,
                stroke: strokeColor,
                strokeWidth: 4
            },
            negative: {
                x1: size.width * 0.6,
                y1: size.height * 0.3,
                x2: size.width * 0.6,
                y2: size.height * 0.7,
                stroke: strokeColor,
                strokeWidth: 2
            },
            polarityPos: {
                text: '+',
                fill: textColor,
                fontSize: 14,
                fontWeight: 'bold',
                textAnchor: 'middle',
                x: size.width * 0.4,
                y: size.height * 0.1
            },
            polarityNeg: {
                text: '−',
                fill: textColor,
                fontSize: 14,
                fontWeight: 'bold',
                textAnchor: 'middle',
                x: size.width * 0.6,
                y: size.height * 0.1
            },
            label: commonLabel
        },
        ground: {
            symbol: {
                d: `M ${size.width / 2},0 L ${size.width / 2},${size.height * 0.6} M ${size.width * 0.2},${size.height * 0.6} L ${size.width * 0.8},${size.height * 0.6} M ${size.width * 0.3},${size.height * 0.75} L ${size.width * 0.7},${size.height * 0.75} M ${size.width * 0.4},${size.height * 0.9} L ${size.width * 0.6},${size.height * 0.9}`,
                fill: 'none',
                stroke: strokeColor,
                strokeWidth: 2
            },
            label: commonLabel
        },
        diode: {
            triangle: {
                d: `M ${size.width * 0.3},${size.height * 0.3} L ${size.width * 0.7},${size.height / 2} L ${size.width * 0.3},${size.height * 0.7} Z`,
                fill: 'transparent',
                stroke: strokeColor,
                strokeWidth: 2
            },
            cathode: {
                x1: size.width * 0.7,
                y1: size.height * 0.3,
                x2: size.width * 0.7,
                y2: size.height * 0.7,
                stroke: strokeColor,
                strokeWidth: 2
            },
            label: commonLabel
        },
        inductor: {
            coil: {
                d: `M 0,${size.height / 2} L ${size.width * 0.15},${size.height / 2} ` +
                   `Q ${size.width * 0.2},${size.height * 0.1} ${size.width * 0.25},${size.height / 2} ` +
                   `Q ${size.width * 0.3},${size.height * 0.9} ${size.width * 0.35},${size.height / 2} ` +
                   `Q ${size.width * 0.4},${size.height * 0.1} ${size.width * 0.45},${size.height / 2} ` +
                   `Q ${size.width * 0.5},${size.height * 0.9} ${size.width * 0.55},${size.height / 2} ` +
                   `Q ${size.width * 0.6},${size.height * 0.1} ${size.width * 0.65},${size.height / 2} ` +
                   `Q ${size.width * 0.7},${size.height * 0.9} ${size.width * 0.75},${size.height / 2} ` +
                   `L ${size.width},${size.height / 2}`,
                fill: 'none',
                stroke: strokeColor,
                strokeWidth: 2
            },
            label: commonLabel
        },
        voltage_source: {
            body: {
                cx: size.width / 2,
                cy: size.height / 2,
                r: Math.min(size.width, size.height) / 2 - 2,
                fill: 'transparent',
                stroke: strokeColor,
                strokeWidth: 2
            },
            polarityPos: {
                text: '+',
                fill: textColor,
                fontSize: 16,
                fontWeight: 'bold',
                textAnchor: 'middle',
                x: size.width / 2 - size.width * 0.15,
                y: size.height / 2 + 5
            },
            polarityNeg: {
                text: '−',
                fill: textColor,
                fontSize: 16,
                fontWeight: 'bold',
                textAnchor: 'middle',
                x: size.width / 2 + size.width * 0.15,
                y: size.height / 2 + 5
            },
            label: commonLabel
        },
        current_source: {
            body: {
                cx: size.width / 2,
                cy: size.height / 2,
                r: Math.min(size.width, size.height) / 2 - 2,
                fill: 'transparent',
                stroke: strokeColor,
                strokeWidth: 2
            },
            arrow: {
                d: `M ${size.width / 2},${size.height * 0.3} L ${size.width / 2},${size.height * 0.7} M ${size.width / 2},${size.height * 0.7} L ${size.width * 0.4},${size.height * 0.6} M ${size.width / 2},${size.height * 0.7} L ${size.width * 0.6},${size.height * 0.6}`,
                fill: 'none',
                stroke: strokeColor,
                strokeWidth: 2
            },
            label: commonLabel
        },
        transistor: {
            body: {
                cx: size.width / 2,
                cy: size.height / 2,
                r: Math.min(size.width, size.height) / 2.5,
                fill: 'transparent',
                stroke: strokeColor,
                strokeWidth: 1.5
            },
            baseLead: {
                x1: 0,
                y1: size.height / 2,
                x2: size.width * 0.35,
                y2: size.height / 2,
                stroke: strokeColor,
                strokeWidth: 2
            },
            base: {
                x1: size.width * 0.35,
                y1: size.height * 0.3,
                x2: size.width * 0.35,
                y2: size.height * 0.7,
                stroke: strokeColor,
                strokeWidth: 3
            },
            collector: {
                x1: size.width * 0.35,
                y1: size.height * 0.35,
                x2: size.width,
                y2: size.height * 0.15,
                stroke: strokeColor,
                strokeWidth: 2
            },
            emitter: {
                x1: size.width * 0.35,
                y1: size.height * 0.65,
                x2: size.width,
                y2: size.height * 0.85,
                stroke: strokeColor,
                strokeWidth: 2
            },
            arrow: {
                d: `M ${size.width * 0.55},${size.height * 0.72} L ${size.width * 0.65},${size.height * 0.8} L ${size.width * 0.6},${size.height * 0.68} Z`,
                fill: strokeColor,
                stroke: strokeColor,
                strokeWidth: 1
            },
            label: commonLabel
        }
    };

    return attrs[elementType] || {
        body: {
            fill: 'transparent',
            stroke: strokeColor,
            strokeWidth: 2,
            width: size.width,
            height: size.height
        },
        label: commonLabel
    };
};

// Create UML element with proper compartments
const createUMLElement = (elementSpec: JointElement, elementType: string, theme: 'light' | 'dark') => {
    const position = Array.isArray(elementSpec.position) ?
        { x: elementSpec.position[0], y: elementSpec.position[1] } :
        elementSpec.position || { x: 0, y: 0 };
    const size = elementSpec.size || { width: 160, height: 120 };
    const text = elementSpec.text || elementSpec.label || elementSpec.id;

    // Parse UML content (methods, properties)
    const umlContent = parseUMLContent(text);

    // Access shapes from the global scope set by render()
    const { shapes } = (globalThis as any).__jointRuntimeDeps || {};
    if (!shapes) throw new Error('Joint.js not initialized');

    // Create UML class using standard rectangle with custom markup
    const element = new shapes.standard.Rectangle({
        id: elementSpec.id,
        position,
        size,
        markup: [
            { tagName: 'rect', selector: 'body' },
            { tagName: 'rect', selector: 'header' },
            { tagName: 'text', selector: 'headerText' },
            { tagName: 'rect', selector: 'attributes' },
            { tagName: 'text', selector: 'attributesText' },
            { tagName: 'rect', selector: 'methods' },
            { tagName: 'text', selector: 'methodsText' }
        ],
        attrs: {
            body: {
                fill: theme === 'dark' ? '#2f3349' : '#ffffff',
                stroke: theme === 'dark' ? '#4cc9f0' : '#333333',
                strokeWidth: 2,
                width: size.width,
                height: size.height
            },
            header: {
                fill: theme === 'dark' ? '#3b4252' : '#f0f0f0',
                stroke: theme === 'dark' ? '#4cc9f0' : '#333333',
                width: size.width,
                height: size.height / 3,
                y: 0
            },
            headerText: {
                text: umlContent.name,
                fill: theme === 'dark' ? '#ffffff' : '#000000',
                fontSize: 14,
                fontWeight: 'bold',
                textAnchor: 'middle',
                textVerticalAnchor: 'middle',
                x: size.width / 2,
                y: size.height / 6
            }
        }
    });

    return element;
};
// Parse simplified Joint.js syntax
const parseJointDefinition = (definition: string): { elements: JointElement[]; connections: JointLink[] } => {
    const lines = definition.split('\n').map(line => line.trim()).filter(line => line && !line.startsWith('//') && !line.startsWith('#') && !line.startsWith('```') && !line.startsWith('type:') && !line.startsWith('definition:') && line !== '|');
    const elements: JointElement[] = [];
    const links: JointLink[] = [];
    let currentSection = 'elements';

    for (const line of lines) {
        if (line.toLowerCase().includes('elements:')) {
            currentSection = 'elements';
            continue;
        }
        if (line.toLowerCase().includes('links:') || line.toLowerCase().includes('connections:')) {
            currentSection = 'links';
            continue;
        }

        if (currentSection === 'elements') {
            // Parse element: id [type] "label" @(x,y) size(w,h)
            // Enhanced regex to handle simpler formats: "A: Label" or just "A"
            const elementMatch = line.match(/^(\w+)(?:\s*\[(\w+)\])?(?:\s*"([^"]*)")?(?:\s*@\((\d+),\s*(\d+)\))?(?:\s*size\((\d+),\s*(\d+)\))?/) ||
                line.match(/^(\w+):\s*"?([^"]*)"?$/) ||
                line.match(/^(\w+)$/);

            if (elementMatch) {
                const [, id, type, label, x, y, w, h] = elementMatch;
                elements.push({
                    id,
                    type: type || 'rect',
                    position: x && y ?
                        [parseInt(x), parseInt(y)] :
                        [
                            (elements.length % 4) * 180 + 80,
                            Math.floor(elements.length / 4) * 120 + 60
                        ],
                    size: w && h ?
                        { width: parseInt(w), height: parseInt(h) } :
                        { width: 120, height: 80 },
                    text: label || id
                });
            } else if (line.includes(':') && !line.includes('->')) {
                // Simple format: id: "label"
                const simpleMatch = line.match(/^(\w+):\s*"?([^"]*)"?$/);
                if (simpleMatch) {
                    const [, id, label] = simpleMatch;
                    elements.push({
                        id,
                        type: 'rect',
                        position: [
                            (elements.length % 4) * 180 + 80,
                            Math.floor(elements.length / 4) * 120 + 60
                        ],
                        size: { width: 120, height: 80 },
                        text: label || id
                    });
                }
            }
        } else if (currentSection === 'links') {
            // Parse link: source -> target "label"
            const linkMatch = line.match(/^(\w+)\s*->\s*(\w+)(?:\s*"([^"]*)")?/);
            if (linkMatch) {
                const [, source, target, label] = linkMatch;
                links.push({
                    id: `${source}-${target}`,
                    source,
                    target,
                    label: label
                });
            }
        }
    }

    // If no elements were parsed, create a simple test case
    if (elements.length === 0 && definition.trim()) {
        console.log('No elements parsed from definition, creating default test elements');
        elements.push({
            id: 'A', type: 'rect', position: [100, 100],
            size: { width: 120, height: 80 }, text: 'Element A'
        });
        elements.push({
            id: 'B', type: 'circle', position: [300, 100],
            size: { width: 80, height: 80 }, text: 'Element B'
        });
        links.push({ id: 'A-B', source: 'A', target: 'B', label: 'connection' });
    }

    return { elements, connections: links };
};

// Create Joint.js elements from specification
const createElement = (elementSpec: JointElement, theme: 'light' | 'dark') => {
    const position = Array.isArray(elementSpec.position) ?
        { x: elementSpec.position[0], y: elementSpec.position[1] } :
        elementSpec.position || { x: 0, y: 0 };
    const size = elementSpec.size || { width: 120, height: 80 };
    const text = elementSpec.text || elementSpec.id;

    // Add validation
    if (!elementSpec.id) {
        console.error('Element missing required id:', elementSpec);
        throw new Error('Element must have an id');
    }

    console.log(`Creating element ${elementSpec.id}:`, { position, size, text, type: elementSpec.type });

    const commonAttrs = {
        body: {
            fill: theme === 'dark' ? '#2f3349' : '#ffffff',
            stroke: theme === 'dark' ? '#4cc9f0' : '#2c3e50',
            strokeWidth: 3,
            rx: 8,
            ry: 8,
            filter: theme === 'dark' ? 'drop-shadow(2px 2px 6px rgba(0,0,0,0.4))' : 'drop-shadow(2px 2px 6px rgba(0,0,0,0.2))'
        },
        label: {
            text: text,
            fill: theme === 'dark' ? '#eceff4' : '#2c3e50',
            fontSize: 13,
            fontFamily: 'Arial, sans-serif',
            fontWeight: 'bold',
            textAnchor: 'middle',
        textVerticalAnchor: 'middle'
        }
    };
    
    // Access shapes and dia from the global scope set by render()
    const { shapes, dia } = (globalThis as any).__jointRuntimeDeps || {};
    if (!shapes || !dia) throw new Error('Joint.js not initialized');

    let element: dia.Element;
    switch (elementSpec.type || 'rect') {
        case 'circle':
            element = new shapes.standard.Circle({
                id: elementSpec.id,
                position,
                size,
                attrs: {
                    body: {
                        ...commonAttrs.body,
                        fill: theme === 'dark' ? '#5e81ac' : '#3498db',
                        stroke: theme === 'dark' ? '#88c0d0' : '#2980b9'
                    },
                    label: {
                        ...commonAttrs.label,
                        fill: theme === 'dark' ? '#eceff4' : '#ffffff'
                    }
                }
            });
            break;
        case 'ellipse':
            element = new shapes.standard.Ellipse({
                id: elementSpec.id,
                position,
                size,
                attrs: {
                    body: {
                        ...commonAttrs.body,
                        fill: theme === 'dark' ? '#bf616a' : '#e74c3c',
                        stroke: theme === 'dark' ? '#d08770' : '#c0392b'
                    },
                    label: {
                        ...commonAttrs.label,
                        fill: theme === 'dark' ? '#eceff4' : '#ffffff'
                    }
                }
            });
            break;
        case 'cylinder':
            // Use Cylinder shape if available, fallback to Rectangle
            element = new shapes.standard.Ellipse({
                id: elementSpec.id,
                position,
                size,
                attrs: {
                    body: {
                        ...commonAttrs.body,
                        fill: theme === 'dark' ? '#a3be8c' : '#2ecc71',
                        stroke: theme === 'dark' ? '#8fbcbb' : '#27ae60'
                    },
                    label: {
                        ...commonAttrs.label,
                        fill: theme === 'dark' ? '#2e3440' : '#ffffff',
                        fontSize: 11
                    }
                }
            });
            break;
        case 'diamond':
            // Create proper diamond using Polygon
            element = new shapes.standard.Polygon({
                id: elementSpec.id,
                position,
                size,
                attrs: {
                    body: {
                        ...commonAttrs.body,
                        fill: theme === 'dark' ? '#ebcb8b' : '#f39c12',
                        stroke: theme === 'dark' ? '#d08770' : '#e67e22',
                        refPoints: '0,10 10,0 20,10 10,20'
                    },
                    label: {
                        ...commonAttrs.label,
                        fill: theme === 'dark' ? '#2e3440' : '#ffffff',
                        fontSize: 12
                    }
                }
            });
            break;
        default: // 'rect' or any other type
            element = new shapes.standard.Rectangle({
                id: elementSpec.id,
                position,
                size,
                attrs: {
                    body: {
                        ...commonAttrs.body,
                        fill: theme === 'dark' ? '#4c566a' : '#ffffff'
                    },
                    label: commonAttrs.label
                }
            });
    }

    console.log(`Created element:`, element);
    return element;
};

// Enhanced link creation with better routing and styling
const createEnhancedLink = (linkSpec: JointLink, theme: 'light' | 'dark') => {
    // Configure source/target with proper anchor and connection points
    const sourceConfig = typeof linkSpec.source === 'string'
        ? { id: linkSpec.source, anchor: { name: 'modelCenter' }, connectionPoint: { name: 'boundary' } }
        : {
            ...linkSpec.source,
            anchor: linkSpec.source.anchor || { name: 'modelCenter' },
            connectionPoint: linkSpec.source.connectionPoint || { name: 'boundary' }
        };

    const targetConfig = typeof linkSpec.target === 'string'
        ? { id: linkSpec.target, anchor: { name: 'modelCenter' }, connectionPoint: { name: 'boundary' } }
        : {
            ...linkSpec.target,
            anchor: linkSpec.target.anchor || { name: 'modelCenter' },
            connectionPoint: linkSpec.target.connectionPoint || { name: 'boundary' }
        };

    // Access shapes from the global scope set by render()
    const { shapes } = (globalThis as any).__jointRuntimeDeps || {};
    if (!shapes) throw new Error('Joint.js not initialized');

    const link = new shapes.standard.Link({
        id: linkSpec.id,
        source: sourceConfig,
        target: targetConfig,
        router: {
            name: linkSpec.router || 'normal',
            args: { padding: 20 }
        },
        connector: {
            name: linkSpec.connector || 'rounded',
            args: { radius: 15 }
        },
        vertices: linkSpec.vertices || [],
        defaultRouter: { name: 'normal' },
        attrs: {
            line: {
                stroke: theme === 'dark' ? '#88c0d0' : '#34495e',
                strokeWidth: 3,
                strokeLinecap: 'round',
                strokeLinejoin: 'round',
                strokeDasharray: linkSpec.attrs?.line?.strokeDasharray || '0',
                filter: 'drop-shadow(2px 2px 4px rgba(0,0,0,0.2))',
                targetMarker: {
                    type: 'path',
                    d: 'M 14 -7 0 0 14 7 z',
                    fill: theme === 'dark' ? '#88c0d0' : '#34495e',
                    stroke: theme === 'dark' ? '#88c0d0' : '#34495e',
                    strokeWidth: 2
                }
            },
            wrapper: {
                strokeWidth: 10,
                stroke: 'transparent'
            }
        }
    });

    // Add label if specified
    if (linkSpec.label) {
        link.appendLabel({
            position: 0.5,
            attrs: {
                rect: {
                    fill: theme === 'dark' ? '#3b4252' : '#ffffff',
                    stroke: theme === 'dark' ? '#4c566a' : '#bdc3c7',
                    strokeWidth: 1,
                    rx: 6,
                    ry: 6,
                    width: 'calc(w + 16)',
                    height: 'calc(h + 8)',
                    x: 'calc(x - 8)',
                    y: 'calc(y - 4)'
                },
                text: {
                    text: linkSpec.label,
                    fill: theme === 'dark' ? '#eceff4' : '#2c3e50',
                    fontSize: 12,
                    fontFamily: 'Arial, sans-serif',
                    fontWeight: 'bold',
                    textAnchor: 'middle',
                    textVerticalAnchor: 'middle'
                }
            }
        });
    }

    return link;
};

// Override the original createLink to use the enhanced version
const createLink = (linkSpec: JointLink, theme: 'light' | 'dark') => {
    // Prepare source and target with proper anchor points for better connections
    const sourceConfig = typeof linkSpec.source === 'string'
        ? { id: linkSpec.source, anchor: { name: 'modelCenter' } }
        : { ...linkSpec.source, anchor: { name: 'modelCenter' } };

    const targetConfig = typeof linkSpec.target === 'string'
        ? { id: linkSpec.target, anchor: { name: 'modelCenter' } }
        : { ...linkSpec.target, anchor: { name: 'modelCenter' } };

    // Access shapes from the global scope set by render()
    const { shapes } = (globalThis as any).__jointRuntimeDeps || {};
    if (!shapes) throw new Error('Joint.js not initialized');

    const link = new shapes.standard.Link({
        id: linkSpec.id,
        source: sourceConfig,
        target: targetConfig,
        router: {
            name: linkSpec.router || 'normal',
            args: { padding: 10 }
        },
        connector: {
            name: linkSpec.connector || 'rounded',
            args: { radius: 15 }
        },
        vertices: linkSpec.vertices || [],
        attrs: {
            line: {
                stroke: theme === 'dark' ? '#88c0d0' : '#34495e',
                strokeWidth: 3,
                strokeLinecap: 'round',
                strokeLinejoin: 'round',
                strokeDasharray: linkSpec.attrs?.line?.strokeDasharray || '0',
                filter: 'drop-shadow(2px 2px 4px rgba(0,0,0,0.2))',
                targetMarker: {
                    type: 'path',
                    d: 'M 14 -7 0 0 14 7 z',
                    fill: theme === 'dark' ? '#88c0d0' : '#34495e',
                    stroke: theme === 'dark' ? '#88c0d0' : '#34495e',
                    strokeWidth: 2
                }
            },
            wrapper: {
                strokeWidth: 10,
                stroke: 'transparent'
            }
        }
    });

    // Add label if specified
    if (linkSpec.label) {
        link.appendLabel({
            position: 0.5,
            attrs: {
                rect: {
                    fill: theme === 'dark' ? '#3b4252' : '#ffffff',
                    stroke: theme === 'dark' ? '#4c566a' : '#bdc3c7',
                    strokeWidth: 1,
                    rx: 6,
                    ry: 6,
                    width: 'calc(w + 16)',
                    height: 'calc(h + 8)',
                    x: 'calc(x - 8)',
                    y: 'calc(y - 4)'
                },
                text: {
                    text: linkSpec.label,
                    fill: theme === 'dark' ? '#eceff4' : '#2c3e50',
                    fontSize: 12,
                    fontFamily: 'Arial, sans-serif',
                    fontWeight: 'bold',
                    textAnchor: 'middle',
                    textVerticalAnchor: 'middle'
                }
            }
        });
    }

    return link;
};

export const jointPlugin: D3RenderPlugin = {
    name: 'joint-renderer',
    priority: 6, // Higher than basic charts, lower than mermaid/graphviz
    sizingConfig: {
        sizingStrategy: 'auto-expand',
        needsDynamicHeight: true,
        needsOverflowVisible: true,
        observeResize: false,
        minWidth: undefined,
        minHeight: undefined,
        containerStyles: {
            width: '100%',
            height: 'auto',
            maxHeight: 'none',
            overflow: 'visible'
        }
    },

    canHandle: (spec: any): boolean => {
        return isJointSpec(spec);
    },

    // Helper to check if a joint definition is complete
    isDefinitionComplete: (definition: string): boolean => {
        if (!definition || definition.trim().length === 0) return false;

        // Check if we have at least one element definition
        const lines = definition.trim().split('\n');
        return lines.length >= 2 && lines.some(line =>
            line.includes('elements:') || line.match(/\w+\s*(?:\[\w+\])?/)
        );
    },

    render: async (container: HTMLElement, d3: any, spec: JointSpec, isDarkMode: boolean): Promise<void> => {
        console.log('Joint.js plugin render called with spec:', spec);

        // Lazy load Joint.js libraries
        const [jointCore, jointLayout] = await Promise.all([
            import('@joint/core'),
            import('@joint/layout-directed-graph')
        ]);
        const { dia, shapes, anchors, connectionPoints, routers, connectors } = jointCore;
        const { DirectedGraph } = jointLayout;

        // Make runtime dependencies available to helper functions
        (globalThis as any).__jointRuntimeDeps = {
            dia, shapes, anchors, connectionPoints, routers, connectors
        };

        try {
            // Clear container and any existing Joint.js instances
            const existingPaper = (container as any)._jointPaper;
            if (existingPaper) {
                existingPaper.remove();
                delete (container as any)._jointPaper;
            }
            container.innerHTML = '';

            // Ensure container uses full width from parent
            container.style.width = '100%';
            container.style.maxWidth = '100%';

            // Show loading spinner

            // Show loading spinner
            const loadingSpinner = document.createElement('div');
            loadingSpinner.className = 'joint-loading-spinner';
            loadingSpinner.style.cssText = `
                display: flex;
                flex-direction: column;
                align-items: center;
                justify-content: center;
                padding: 2em;
                min-height: 200px;
                width: 100%;
            `;
            loadingSpinner.innerHTML = `
                <div style="
                    border: 4px solid rgba(0, 0, 0, 0.1);
                    border-top: 4px solid ${isDarkMode ? '#4cc9f0' : '#3498db'};
                    border-radius: 50%;
                    width: 40px;
                    height: 40px;
                    animation: joint-spin 1s linear infinite;
                    margin-bottom: 15px;
                "></div>
                <div style="font-family: system-ui, -apple-system, sans-serif; color: ${isDarkMode ? '#eceff4' : '#333333'};">
                    Rendering diagram...
                </div>
            `;
            container.appendChild(loadingSpinner);

            // Add spinner animation
            if (!document.querySelector('#joint-spinner-keyframes')) {
                const keyframes = document.createElement('style');
                keyframes.id = 'joint-spinner-keyframes';
                keyframes.textContent = `
                    @keyframes joint-spin {
                        0% { transform: rotate(0deg); }
                        100% { transform: rotate(360deg); }
                    }
                `;
                document.head.appendChild(keyframes);
            }

            // If we're streaming and the definition is incomplete, show a waiting message
            if (spec.isStreaming && !spec.forceRender) {
                const definition = spec.definition || '';
                const isComplete = jointPlugin.isDefinitionComplete!(definition);

                if (!isComplete) {
                    container.innerHTML = `
                        <div style="text-align: center; padding: 20px; background-color: ${isDarkMode ? '#1f1f1f' : '#f6f8fa'}; border: 1px dashed #ccc; border-radius: 4px;">
                            <p>Waiting for complete Joint.js diagram definition...</p>
                            <button onclick="this.parentElement.style.display='none'; this.dispatchEvent(new CustomEvent('forceRender', { bubbles: true }))" 
                                style="background-color: #4361ee; color: white; border: none; padding: 8px 16px; border-radius: 4px; cursor: pointer; margin-top: 10px;">
                                🔄 Force Render
                            </button>
                        </div>
                    `;
                    return;
                }
            }

            // Parse the specification
            let elements: JointElement[], connections: JointLink[];
            if (spec.definition) {
                const definition = extractDefinitionFromYAML(spec.definition, 'joint');
                const parsed = parseJointDefinition(definition);
                elements = parsed.elements;
                connections = parsed.connections;

                console.log('Parsed from definition:', {
                    elements: elements.length,
                    connections: connections.length
                });
            } else if (spec.elements) {
                // Handle object format
                elements = Object.keys(spec.elements).map(id => ({
                    ...{ type: 'rect' }, // Default type
                    id,
                    ...spec.elements![id]
                }));
                connections = spec.connections || [];

                console.log('Parsed from object format:', {
                    elements: elements.length,
                    elementIds: elements.map(e => e.id),
                    elementTypes: elements.map(e => e.type || 'undefined'),
                    connections: connections.length
                });
            } else {
                throw new Error('No elements or definition provided');
            }

            console.log('Parsed elements:', elements);
            console.log('Parsed connections:', connections);

            if (elements.length === 0) {
                throw new Error('No elements found in specification');
            }

            const theme = spec.theme === 'auto' ? (isDarkMode ? 'dark' : 'light') :
                (spec.theme || (isDarkMode ? 'dark' : 'light'));

            // Calculate container dimensions - walk up to find a rendered parent with actual dimensions
            const parentContainer = container.parentElement;
            let parentRect = parentContainer?.getBoundingClientRect();

            // If parent has no width (not laid out), walk up further
            let searchParent = parentContainer;
            while (searchParent && parentRect && parentRect.width === 0) {
                searchParent = searchParent.parentElement;
                parentRect = searchParent?.getBoundingClientRect();
            }

            // Use parent width if available, otherwise use viewport-relative default
            const availableWidth = (parentRect && parentRect.width > 0) ? parentRect.width : window.innerWidth * 0.8;
            const availableHeight = (parentRect && parentRect.height > 0) ? parentRect.height : 400;

            console.log('Joint.js sizing:', {
                container: container.getBoundingClientRect(),
                parentRect,
                availableWidth,
                availableHeight,
                windowWidth: window.innerWidth
            });

            const width = spec.width || Math.max(availableWidth - 40, 400);
            const height = spec.height || Math.max(availableHeight - 40, 300);

            // Create Joint.js graph and paper
            const graph = new dia.Graph({}, {
                cellNamespace: shapes
            });

            console.log('Creating Joint.js paper with dimensions:', { width, height });

            const paper = new dia.Paper({
                el: container,
                width,
                height,
                gridSize: spec.grid !== false ? 10 : 1,
                drawGrid: spec.grid !== false,
                model: graph,
                cellViewNamespace: shapes,
                anchorNamespace: anchors,
                connectionPointNamespace: connectionPoints,
                routerNamespace: routers,
                connectorNamespace: connectors,
                interactive: spec.interactive !== false,
                snapLinks: { radius: 30 },
                linkPinning: false,
                defaultAnchor: { name: 'modelCenter' },
                defaultConnectionPoint: { name: 'boundary' },
                defaultRouter: { name: 'normal' },
                defaultLink: () => new shapes.standard.Link(),
                defaultConnector: { name: 'rounded', args: { radius: 15 } },
                background: {
                    color: theme === 'dark' ? '#1f1f1f' : '#ffffff'
                },
            });

            // Store paper reference for cleanup
            (container as any)._jointPaper = paper;

            // Add ResizeObserver to handle container width changes
            const resizeObserver = new ResizeObserver((entries) => {
                for (const entry of entries) {
                    const { width: newWidth } = entry.contentRect;

                    // Only update width - height is controlled by content
                    const currentDimensions = paper.getComputedSize();
                    if (newWidth > 0 && newWidth !== currentDimensions.width && Math.abs(currentDimensions.width - newWidth) > 5) {

                        console.log('Container width changed, updating paper width:', {
                            from: currentDimensions.width,
                            to: newWidth
                        });

                        // Get content bounds to maintain proper height
                        const bbox = graph.getBBox();
                        if (bbox) {
                            const padding = 40;
                            const contentHeight = bbox.height + padding * 2;

                            // Update paper width, maintain content-based height
                            paper.setDimensions(newWidth, Math.max(contentHeight, 300));

                            // Update container and parent heights to match
                            container.style.height = `${Math.max(contentHeight, 300)}px`;
                            container.style.minHeight = `${Math.max(contentHeight, 300)}px`;

                            // Also update parent d3-container if it exists
                            const parentContainer = container.parentElement;
                            if (parentContainer?.classList.contains('d3-container')) {
                                parentContainer.style.height = 'auto';
                                parentContainer.style.minHeight = `${Math.max(contentHeight, 300)}px`;
                            }

                            // Reposition content to center
                            paper.translate(padding - bbox.x, padding - bbox.y);

                            // Update SVG viewBox
                            const svg = container.querySelector('svg');
                            if (svg) {
                                svg.setAttribute('viewBox', `0 0 ${newWidth} ${Math.max(contentHeight, 300)}`);
                            }
                        }
                    }
                }
            });

            resizeObserver.observe(container);

            // Store observer for cleanup
            (container as any)._resizeObserver = resizeObserver;

            // Force the paper container to use full width
            const paperEl = container.querySelector('.joint-paper') as HTMLElement;
            if (paperEl) {
                paperEl.style.width = '100%';
                paperEl.style.height = 'auto';
                paperEl.style.minHeight = 'unset';
                paperEl.style.maxHeight = 'none';
                console.log('Forced paper element to full width');
            }

            // Remove loading spinner
            if (loadingSpinner && loadingSpinner.parentNode === container) {
                container.removeChild(loadingSpinner);
            }

            // Create and add elements
            const jointElements: dia.Element[] = [];
            let elementIndex = 0;
            const gridCols = Math.min(Math.ceil(Math.sqrt(elements.length)), 4); // Cap at 4 columns
            const elementSpacing = Math.min((width - 100) / gridCols, 150); // Leave margins
            const totalGridWidth = (gridCols - 1) * elementSpacing;
            const startX = Math.max(60, (width - totalGridWidth) / 2);
            const startY = 80;

            // Create shape registry for enhanced element creation
            const shapeRegistry = createShapeRegistry();

            console.log('🔧 JOINT-DEBUG: Starting element creation');
            console.log('🔧 JOINT-DEBUG: Shape registry keys:', Object.keys(shapeRegistry));

            elements.forEach(elementSpec => {
                try {
                    // Use shape registry for enhanced shapes
                    const shapeType = elementSpec.type || elementSpec.shape || 'rect';

                    console.log(`🔧 JOINT-DEBUG: Processing element ${elementSpec.id}:`, {
                        type: elementSpec.type,
                        shape: elementSpec.shape,
                        shapeType: shapeType,
                        hasCreator: !!shapeRegistry[shapeType]
                    });

                    const shapeCreator = shapeRegistry[shapeType];
                    if (!shapeCreator) {
                        console.warn(`🔧 JOINT-DEBUG: No shape creator found for "${shapeType}", using rect as fallback`);
                        const fallbackCreator = shapeRegistry['rect'];
                        if (!fallbackCreator) {
                            throw new Error(`No shape creator for "${shapeType}" and no rect fallback available`);
                        }
                    }

                    const actualCreator = shapeRegistry[shapeType] || shapeRegistry['rect'];

                    // Ensure element has required properties
                    let defaultPosition = elementSpec.position;

                    // Only auto-position if position is completely missing or clearly invalid
                    const needsAutoPosition = !defaultPosition ||
                        (Array.isArray(defaultPosition) && (defaultPosition[0] < 0 || defaultPosition[1] < 0)) ||
                        (typeof defaultPosition === 'object' && !Array.isArray(defaultPosition) &&
                            ('x' in defaultPosition && 'y' in defaultPosition && (defaultPosition.x < 0 || defaultPosition.y < 0)));

                    if (needsAutoPosition) {
                        // Use grid layout for better default positioning
                        const col = elementIndex % gridCols;
                        const row = Math.floor(elementIndex / gridCols);
                        defaultPosition = [startX + col * elementSpacing, startY + row * 120];
                        console.log(`🔧 JOINT-DEBUG: Auto-positioning element ${elementSpec.id} at grid (${col}, ${row}) -> (${defaultPosition[0]}, ${defaultPosition[1]})`);
                    }

                    const elementWithDefaults = {
                        ...elementSpec,
                        position: defaultPosition,
                        size: elementSpec.size || { width: 120, height: 80 }
                    };

                    const element = actualCreator(elementWithDefaults, theme);
                    if (element) {
                        jointElements.push(element);
                        graph.addCell(element);
                        console.log(`🔧 JOINT-DEBUG: ✓ Created element ${elementSpec.id}`);
                    }
                } catch (error) {
                    console.warn(`Failed to create element ${elementSpec.id}:`, error);
                } finally {
                    elementIndex++;
                }
            });

            console.log(`Created ${jointElements.length} elements out of ${elements.length} specified`);

            if (jointElements.length === 0) {
                throw new Error('No elements were successfully created');
            }

            // Create and add links
            const jointLinks: dia.Link[] = [];
            connections.forEach(linkSpec => {
                try {
                    // Validate that source and target elements exist
                    const sourceId = typeof linkSpec.source === 'string' ? linkSpec.source : linkSpec.source.id;
                    const targetId = typeof linkSpec.target === 'string' ? linkSpec.target : linkSpec.target.id;

                    const sourceExists = jointElements.some(el => el.id === sourceId);
                    const targetExists = jointElements.some(el => el.id === targetId);

                    if (!sourceExists) {
                        console.warn(`Link source "${sourceId}" not found in created elements`);
                        return;
                    }
                    if (!targetExists) {
                        console.warn(`Link target "${targetId}" not found in created elements`);
                        return;
                    }

                    const link = createEnhancedLink(linkSpec, theme);
                    if (link) {
                        jointLinks.push(link);
                        graph.addCell(link);
                        console.log(`Created link: ${linkSpec.id}`, link);
                    }
                } catch (error) {
                    console.warn(`Failed to create link ${linkSpec.id}:`, error);
                }
            });

            console.log(`Created ${jointLinks.length} links out of ${connections.length} specified`);

            // Apply auto-layout if enabled
            if (spec.autoLayout !== false && jointElements.length > 1) {
                console.log('Applying DirectedGraph layout to Joint.js diagram');

                try {
                    DirectedGraph.layout(graph, {
                        nodeSep: 50,
                        edgeSep: 80,
                        rankSep: 100,
                        marginX: 30,
                        marginY: 30,
                        rankDir: 'TB', // Top to bottom
                        resizeClusters: true,
                        clusterPadding: { top: 40, left: 10, right: 10, bottom: 10 }
                    });
                    console.log('DirectedGraph layout applied successfully');
                } catch (layoutError) {
                    console.warn('Auto-layout failed, using manual positioning:', layoutError);
                }
            }

            // Fit content to paper after layout - ensure all content is visible
            const fitContentToPaper = () => {
                // Get the actual content bounds
                const bbox = graph.getBBox();
                console.log('Graph bounding box:', bbox);

                if (bbox && bbox.width > 0 && bbox.height > 0) {
                    const padding = 40;
                    const contentWidth = bbox.width + padding * 2;
                    const contentHeight = bbox.height + padding * 2;

                    // Get current container size for responsive width
                    const containerRect = container.getBoundingClientRect();
                    const containerWidth = containerRect.width > 0 ? containerRect.width : width;

                    // Width: use container width, but ensure it fits content
                    const finalWidth = Math.max(contentWidth, containerWidth);

                    // Height: ALWAYS grow to fit content (don't cap at original height)
                    const finalHeight = contentHeight;

                    paper.setDimensions(finalWidth, finalHeight);

                    // Update container height to match paper
                    container.style.height = `${finalHeight}px`;
                    container.style.minHeight = `${finalHeight}px`;
                    container.style.maxHeight = 'none';

                    // Propagate height to parent containers
                    const parentContainer = container.parentElement;
                    if (parentContainer?.classList.contains('d3-container')) {
                        parentContainer.style.height = `${finalHeight}px`;
                        parentContainer.style.minHeight = `${finalHeight}px`;
                        parentContainer.style.maxHeight = 'none';
                    }

                    // Update grandparent if it exists (outer wrapper)
                    const grandparentContainer = parentContainer?.parentElement;
                    if (grandparentContainer?.classList.contains('d3-container')) {
                        grandparentContainer.style.height = `${finalHeight}px`;
                        grandparentContainer.style.minHeight = `${finalHeight}px`;
                        grandparentContainer.style.maxHeight = 'none';
                    }

                    // Also update max-height to allow growth
                    container.style.maxHeight = 'none';

                    // Center the content
                    paper.translate(padding - bbox.x, padding - bbox.y);
                    console.log('Paper dimensions updated to fit content');

                    // Force SVG to scale properly
                    const svg = container.querySelector('svg');
                    if (svg) {
                        svg.style.width = '100%';
                        svg.style.height = '100%';
                        svg.style.maxWidth = '100%';
                        svg.style.maxHeight = 'none'; // Allow vertical growth
                        svg.setAttribute('viewBox', `0 0 ${finalWidth} ${finalHeight}`);
                        svg.setAttribute('preserveAspectRatio', 'xMidYMid meet');
                        console.log('SVG viewBox set to:', finalWidth, finalHeight);
                    }
                }
            };

            // Fit content after layout completes
            setTimeout(fitContentToPaper, 300);

            // Add interaction handlers
            paper.on('element:pointerclick', (elementView: dia.ElementView) => {
                console.log('Element clicked:', elementView.model.id);
                // Add selection highlighting
                graph.getElements().forEach(el => {
                    const view = paper.findViewByModel(el);
                    if (view) view.unhighlight();
                });
                elementView.highlight();
            });

            paper.on('link:pointerclick', (linkView: dia.LinkView) => {
                console.log('Link clicked:', linkView.model.id);
            });

            // Add context menu for right-click
            paper.on('element:contextmenu', (elementView: dia.ElementView, evt: dia.Event) => {
                evt.preventDefault();
                console.log('Element right-clicked:', elementView.model.id);
                // Could add context menu here
            });

            // Add action buttons
            const actionsContainer = document.createElement('div');
            actionsContainer.className = 'diagram-actions';
            actionsContainer.style.cssText = `
                position: absolute;
                top: 10px;
                right: 10px;
                z-index: 1000;
                display: flex;
                gap: 5px;
            `;

            // Fit to content button
            const fitButton = document.createElement('button');
            fitButton.innerHTML = '🔍 Fit';
            fitButton.className = 'diagram-action-button';
            fitButton.style.cssText = `
                background-color: #4361ee;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 6px 12px;
                cursor: pointer;
                font-size: 12px;
                margin: 2px;
            `;
            fitButton.onclick = () => {
                paper.scaleContentToFit({ padding: 20, minScale: 0.5, maxScale: 1.5 });

                // Also update container height after fitting
                setTimeout(() => {
                    const bbox = graph.getBBox();
                    if (bbox) {
                        container.style.height = `${bbox.height + 80}px`;
                    }
                }, 100);
            };
            actionsContainer.appendChild(fitButton);

            // Make container relative for absolute positioning  
            container.style.position = 'relative';
            container.appendChild(actionsContainer);

            console.log('Joint.js diagram rendered successfully');

        } catch (error) {
            console.error('Joint.js rendering error:', error);

            // Remove loading spinner if it exists
            const spinner = container.querySelector('.joint-loading-spinner') as HTMLElement;
            if (spinner && spinner.parentNode === container) {
                container.removeChild(spinner);
            }

            container.innerHTML = `
                <div class="joint-error" style="
                    padding: 16px;
                    margin: 16px 0;
                    border-radius: 6px;
                    background-color: ${isDarkMode ? '#2a1f1f' : '#fff2f0'};
                    border: 1px solid ${isDarkMode ? '#a61d24' : '#ffa39e'};
                    color: ${isDarkMode ? '#ff7875' : '#cf1322'};
                ">
                    <strong>Joint.js Rendering Error:</strong>
                    <p>${error instanceof Error ? error.message : 'Unknown error'}</p>
                    <details>
                        <summary>Show Definition</summary>
                    <pre><code>${spec.definition || JSON.stringify(spec, null, 2)}</code></pre>
                </details>
            </div>
            `;
        }
    }
};

// Helper functions for network elements
const getDefaultSizeForNetworkElement = (elementType: string) => {
    const sizes = {
        router: { width: 80, height: 60 },
        switch: { width: 100, height: 40 },
        server: { width: 60, height: 80 },
        firewall: { width: 80, height: 80 },
        cloud: { width: 120, height: 80 }
    };
    return sizes[elementType as keyof typeof sizes] || { width: 80, height: 60 };
};

const getNetworkElementAttrs = (elementType: string, theme: 'light' | 'dark') => {
    return {
        body: {
            fill: theme === 'dark' ? '#2f3349' : '#ffffff',
            stroke: theme === 'dark' ? '#4cc9f0' : '#333333',
            strokeWidth: 2,
            rx: elementType === 'cloud' ? 15 : 5,
            ry: elementType === 'cloud' ? 15 : 5
        },
        label: {
            fill: theme === 'dark' ? '#ffffff' : '#000000',
            fontSize: 11,
            fontFamily: 'Arial, sans-serif',
            textAnchor: 'middle',
            textVerticalAnchor: 'middle'
        }
    };
};

const getDefaultPortsForNetworkElement = (elementType: string) => {
    const portConfigs = {
        router: [
            { id: 'wan', position: 'top', type: 'input' as const },
            { id: 'lan1', position: 'bottom', type: 'output' as const },
            { id: 'lan2', position: 'left', type: 'output' as const },
            { id: 'lan3', position: 'right', type: 'output' as const }
        ],
        switch: [
            { id: 'port1', position: 'left', type: 'inout' as const },
            { id: 'port2', position: 'left', type: 'inout' as const },
            { id: 'port3', position: 'right', type: 'inout' as const },
            { id: 'port4', position: 'right', type: 'inout' as const }
        ],
        server: [
            { id: 'network', position: 'top', type: 'input' as const },
            { id: 'storage', position: 'bottom', type: 'output' as const }
        ],
        firewall: [
            { id: 'external', position: 'left', type: 'input' as const },
            { id: 'internal', position: 'right', type: 'output' as const }
        ],
        cloud: [
            { id: 'connection', position: 'bottom', type: 'inout' as const }
        ]
    };

    return portConfigs[elementType as keyof typeof portConfigs] || [];
};

// Helper functions for electrical elements
const getDefaultSizeForElectricalElement = (elementType: string): { width: number; height: number } => {
    const sizes = {
        resistor: { width: 60, height: 20 },
        capacitor: { width: 40, height: 40 },
        inductor: { width: 60, height: 30 },
        battery: { width: 40, height: 60 },
        ground: { width: 40, height: 30 },
        voltage_source: { width: 40, height: 40 },
        current_source: { width: 40, height: 40 },
        diode: { width: 30, height: 30 },
        transistor: { width: 50, height: 40 }
    };
    return sizes[elementType as keyof typeof sizes] || { width: 40, height: 40 };
};

const getDefaultValueForElement = (elementType: string): string => {
    const defaultValues = {
        resistor: '1kΩ',
        capacitor: '100µF',
        inductor: '1mH',
        battery: '9V',
        voltage_source: '5V',
        current_source: '1A'
    };
    return defaultValues[elementType as keyof typeof defaultValues] || '';
};

const getElectricalElementAttrs = (elementType: string, theme: 'light' | 'dark') => {
    const baseColor = theme === 'dark' ? '#ffffff' : '#000000';
    const fillColor = theme === 'dark' ? 'transparent' : 'transparent';

    const commonAttrs = {
        body: {
            fill: fillColor,
            stroke: baseColor,
            strokeWidth: 2
        }
    };

    return commonAttrs;
};

const getElectricalElementMarkup = (elementType: string) => {
    // Simple markup for now - can be enhanced with proper electrical symbols
    const markups = {
        resistor: [
            { tagName: 'rect', selector: 'body' },
            { tagName: 'text', selector: 'label' }
        ],
        capacitor: [
            { tagName: 'rect', selector: 'body' },
            { tagName: 'line', selector: 'plate1' },
            { tagName: 'line', selector: 'plate2' },
            { tagName: 'text', selector: 'label' }
        ],
        battery: [
            { tagName: 'rect', selector: 'body' },
            { tagName: 'line', selector: 'positive' },
            { tagName: 'line', selector: 'negative' },
            { tagName: 'text', selector: 'label' }
        ]
    };

    return markups[elementType as keyof typeof markups] || [
        { tagName: 'rect', selector: 'body' },
        { tagName: 'text', selector: 'label' }
    ];
};

const getDefaultPortsForElectricalElement = (elementType: string) => {
    const portConfigs = {
        resistor: [
            { id: 'terminal1', position: 'left', type: 'inout' as const },
            { id: 'terminal2', position: 'right', type: 'inout' as const }
        ],
        capacitor: [
            { id: 'positive', position: 'top', type: 'inout' as const },
            { id: 'negative', position: 'bottom', type: 'inout' as const }
        ],
        battery: [
            { id: 'positive', position: 'top', type: 'output' as const },
            { id: 'negative', position: 'bottom', type: 'input' as const }
        ],
        diode: [
            { id: 'anode', position: 'left', type: 'input' as const },
            { id: 'cathode', position: 'right', type: 'output' as const }
        ],
        transistor: [
            { id: 'base', position: 'left', type: 'input' as const },
            { id: 'collector', position: 'top', type: 'output' as const },
            { id: 'emitter', position: 'bottom', type: 'output' as const }
        ]
    };

    return portConfigs[elementType as keyof typeof portConfigs] || [
        { id: 'port1', position: 'left', type: 'inout' },
        { id: 'port2', position: 'right', type: 'inout' }
    ];
};

// Convert our Port interface to Joint.js port format
const createJointPort = (portSpec: Port, theme: 'light' | 'dark') => {
    const portPosition = getPortPosition(portSpec.position || 'top');

    return {
        id: portSpec.id,
        group: portSpec.type || 'default',
        args: portPosition,
        markup: [{
            tagName: 'circle',
            selector: 'portBody'
        }],
        attrs: {
            portBody: {
                fill: theme === 'dark' ? '#4cc9f0' : '#333333',
                stroke: theme === 'dark' ? '#ffffff' : '#000000',
                strokeWidth: 1,
                r: 4,
                magnet: true
            }
        },
        label: {
            position: { name: 'outside' },
            markup: [{ tagName: 'text', selector: 'label' }],
            attrs: {
                label: {
                    text: portSpec.label || '',
                    fill: theme === 'dark' ? '#ffffff' : '#000000',
                    fontSize: 10,
                    textAnchor: 'middle'
                }
            }
        }
    };
};

const createPortFromSpec = (portSpec: Port, theme: 'light' | 'dark') => {
    const portPosition = getPortPosition(portSpec.position || 'top');

    return {
        id: portSpec.id,
        group: portSpec.type || 'default',
        args: portPosition,
        markup: [{
            tagName: 'circle',
            selector: 'portBody'
        }],
        attrs: {
            portBody: {
                fill: theme === 'dark' ? '#4cc9f0' : '#333333',
                stroke: theme === 'dark' ? '#ffffff' : '#000000',
                strokeWidth: 1,
                r: 4,
                magnet: true
            }
        }
    };
};

const getPortPosition = (position: string) => {
    const positions = {
        top: { x: '50%', y: '0%' },
        bottom: { x: '50%', y: '100%' },
        left: { x: '0%', y: '50%' },
        right: { x: '100%', y: '50%' }
    };
    return positions[position as keyof typeof positions] || { x: '50%', y: '50%' };
};



const createDatabaseElement = (elementSpec: JointElement, theme: 'light' | 'dark') => {
    const position = Array.isArray(elementSpec.position) ?
        { x: elementSpec.position[0], y: elementSpec.position[1] } :
        elementSpec.position || { x: 0, y: 0 };
    const size = elementSpec.size || { width: 80, height: 100 };
    const text = elementSpec.text || elementSpec.label || elementSpec.id;

    // Access dia from the global scope set by render()
    const { dia } = (globalThis as any).__jointRuntimeDeps || {};
    if (!dia) throw new Error('Joint.js not initialized');

    return new dia.Element({
        id: elementSpec.id,
        position,
        size,
        markup: [
            { tagName: 'ellipse', selector: 'top' },
            { tagName: 'rect', selector: 'body' },
            { tagName: 'ellipse', selector: 'bottom' },
            { tagName: 'ellipse', selector: 'bottomShadow' },
            { tagName: 'text', selector: 'label' }
        ],
        attrs: {
            top: {
                cx: size.width / 2, cy: 10, rx: size.width / 2 - 2, ry: 10,
                fill: theme === 'dark' ? '#a3be8c' : '#2ecc71',
                stroke: theme === 'dark' ? '#8fbcbb' : '#27ae60',
                strokeWidth: 2
            },
            body: {
                x: 2, y: 10, width: size.width - 4, height: size.height - 20,
                fill: theme === 'dark' ? '#a3be8c' : '#2ecc71',
                stroke: theme === 'dark' ? '#8fbcbb' : '#27ae60',
                strokeWidth: 2
            },
            bottom: {
                cx: size.width / 2, cy: size.height - 10, rx: size.width / 2 - 2, ry: 10,
                fill: theme === 'dark' ? '#8fbcbb' : '#27ae60',
                stroke: theme === 'dark' ? '#8fbcbb' : '#27ae60',
                strokeWidth: 2
            },
            bottomShadow: {
                cx: size.width / 2, cy: size.height - 8, rx: size.width / 2 - 4, ry: 6,
                fill: theme === 'dark' ? '#4c566a' : '#229954',
                opacity: 0.7
            },
            label: {
                text: text,
                fill: theme === 'dark' ? '#2e3440' : '#ffffff',
                fontSize: 11,
                fontFamily: 'Arial, sans-serif',
                fontWeight: 'bold',
                textAnchor: 'middle',
                textVerticalAnchor: 'middle',
                x: size.width / 2,
                y: size.height / 2
            }
        }
    });
};

const createStorageElement = (elementSpec: JointElement, theme: 'light' | 'dark') => {
    const position = Array.isArray(elementSpec.position) ?
        { x: elementSpec.position[0], y: elementSpec.position[1] } :
        elementSpec.position || { x: 0, y: 0 };
    const size = elementSpec.size || { width: 100, height: 80 };
    const text = elementSpec.text || elementSpec.label || elementSpec.id;

    // Access shapes from the global scope set by render()
    const { shapes } = (globalThis as any).__jointRuntimeDeps || {};
    if (!shapes) throw new Error('Joint.js not initialized');

    return new shapes.standard.Rectangle({
        id: elementSpec.id,
        position,
        size,
        attrs: {
            body: {
                fill: theme === 'dark' ? '#b48ead' : '#9b59b6',
                stroke: theme === 'dark' ? '#d08770' : '#8e44ad',
                strokeWidth: 2,
                rx: 10,
                ry: 10,
                filter: 'drop-shadow(2px 2px 4px rgba(0,0,0,0.3))'
            },
            label: {
                text: text,
                fill: theme === 'dark' ? '#eceff4' : '#ffffff',
                fontSize: 13,
                fontFamily: 'Arial, sans-serif',
                fontWeight: 'bold',
                textAnchor: 'middle',
                textVerticalAnchor: 'middle'
            }
        }
    });
};

const createMessageElement = (elementSpec: JointElement, theme: 'light' | 'dark') => {
    const position = Array.isArray(elementSpec.position) ?
        { x: elementSpec.position[0], y: elementSpec.position[1] } :
        elementSpec.position || { x: 0, y: 0 };
    const size = elementSpec.size || { width: 120, height: 60 };
    const text = elementSpec.text || elementSpec.label || elementSpec.id;

    // Access dia from the global scope set by render()
    const { dia } = (globalThis as any).__jointRuntimeDeps || {};
    if (!dia) throw new Error('Joint.js not initialized');

    return new dia.Element({
        id: elementSpec.id,
        position,
        size,
        markup: [
            { tagName: 'rect', selector: 'body' },
            { tagName: 'path', selector: 'flap' },
            { tagName: 'text', selector: 'label' }
        ],
        attrs: {
            body: {
                x: 0, y: 0, width: size.width, height: size.height,
                fill: theme === 'dark' ? '#ebcb8b' : '#f39c12',
                stroke: theme === 'dark' ? '#d08770' : '#e67e22',
                strokeWidth: 2,
                rx: 5,
                ry: 5
            },
            flap: {
                d: `M 0,0 L ${size.width / 2},${size.height / 3} L ${size.width},0`,
                fill: 'none',
                stroke: theme === 'dark' ? '#d08770' : '#e67e22',
                strokeWidth: 2
            },
            label: {
                text: text,
                fill: theme === 'dark' ? '#2e3440' : '#ffffff',
                fontSize: 12,
                fontFamily: 'Arial, sans-serif',
                fontWeight: 'bold',
                textAnchor: 'middle',
                textVerticalAnchor: 'middle',
                x: size.width / 2,
                y: size.height / 2 + 5
            }
        }
    });
};

const createModuleElement = (elementSpec: JointElement, theme: 'light' | 'dark') => {
    const position = Array.isArray(elementSpec.position) ?
        { x: elementSpec.position[0], y: elementSpec.position[1] } :
        elementSpec.position || { x: 0, y: 0 };
    const size = elementSpec.size || { width: 120, height: 80 };
    const text = elementSpec.text || elementSpec.label || elementSpec.id;

    // Access shapes from the global scope set by render()
    const { shapes } = (globalThis as any).__jointRuntimeDeps || {};
    if (!shapes) throw new Error('Joint.js not initialized');

    return new shapes.standard.Rectangle({
        id: elementSpec.id,
        position,
        size,
        attrs: {
            body: {
                fill: theme === 'dark' ? '#5e81ac' : '#3498db',
                stroke: theme === 'dark' ? '#81a1c1' : '#2980b9',
                strokeWidth: 3,
                strokeDasharray: '10,5',
                rx: 8,
                ry: 8,
                filter: 'drop-shadow(2px 2px 4px rgba(0,0,0,0.3))'
            },
            label: {
                text: text,
                fill: theme === 'dark' ? '#eceff4' : '#ffffff',
                fontSize: 13,
                fontFamily: 'Arial, sans-serif',
                fontWeight: 'bold',
                textAnchor: 'middle',
                textVerticalAnchor: 'middle'
            }
        }
    });
};

const createEnhancedUMLElement = (elementSpec: JointElement, umlType: 'class' | 'interface' | 'package', theme: 'light' | 'dark') => {
    const position = Array.isArray(elementSpec.position) ?
        { x: elementSpec.position[0], y: elementSpec.position[1] } :
        elementSpec.position || { x: 0, y: 0 };
    const size = elementSpec.size || { width: 160, height: 120 };
    const text = elementSpec.text || elementSpec.label || elementSpec.id;

    const colors = {
        class: { fill: theme === 'dark' ? '#4c566a' : '#ffffff', stroke: theme === 'dark' ? '#88c0d0' : '#2c3e50' },
        interface: { fill: theme === 'dark' ? '#5e81ac' : '#e8f4fd', stroke: theme === 'dark' ? '#81a1c1' : '#3498db' },
        package: { fill: theme === 'dark' ? '#a3be8c' : '#e8f5e8', stroke: theme === 'dark' ? '#8fbcbb' : '#27ae60' }
    };

    // Access shapes from the global scope set by render()
    const { shapes } = (globalThis as any).__jointRuntimeDeps || {};
    if (!shapes) throw new Error('Joint.js not initialized');

    return new shapes.standard.Rectangle({
        id: elementSpec.id,
        position,
        size,
        attrs: {
            body: {
                fill: colors[umlType].fill,
                stroke: colors[umlType].stroke,
                strokeWidth: 2,
                rx: 5,
                ry: 5,
                filter: 'drop-shadow(2px 2px 4px rgba(0,0,0,0.2))'
            },
            label: {
                text: umlType === 'interface' ? `<<interface>>\n${text}` : text,
                fill: theme === 'dark' ? '#eceff4' : '#2c3e50',
                fontSize: 12,
                fontFamily: 'Arial, sans-serif',
                fontWeight: 'bold',
                textAnchor: 'middle',
                textVerticalAnchor: 'top',
                y: 15
            }
        }
    });
};


const createNoteElement = (elementSpec: JointElement, theme: 'light' | 'dark') => {
    const position = Array.isArray(elementSpec.position) ?
        { x: elementSpec.position[0], y: elementSpec.position[1] } :
        elementSpec.position || { x: 0, y: 0 };
    const size = elementSpec.size || { width: 100, height: 80 };
    const text = elementSpec.text || elementSpec.label || elementSpec.id;

    // Access dia from the global scope set by render()
    const { dia } = (globalThis as any).__jointRuntimeDeps || {};
    if (!dia) throw new Error('Joint.js not initialized');

    return new dia.Element({
        id: elementSpec.id,
        position,
        size,
        markup: [
            { tagName: 'path', selector: 'body' },
            { tagName: 'text', selector: 'label' }
        ],
        attrs: {
            body: {
                d: `M 0,0 L ${size.width - 15},0 L ${size.width},15 L ${size.width},${size.height} L 0,${size.height} Z M ${size.width - 15},0 L ${size.width - 15},15 L ${size.width},15`,
                fill: theme === 'dark' ? '#ebcb8b' : '#fff3cd',
                stroke: theme === 'dark' ? '#d08770' : '#ffc107',
                strokeWidth: 2
            },
            label: {
                text: text,
                fill: theme === 'dark' ? '#2e3440' : '#856404',
                fontSize: 11,
                fontFamily: 'Arial, sans-serif',
                fontWeight: 'normal',
                textAnchor: 'middle',
                textVerticalAnchor: 'middle',
                x: size.width / 2 - 7,
                y: size.height / 2
            }
        }
    });
};


const createDataElement = (elementSpec: JointElement, theme: 'light' | 'dark') => {
    const position = Array.isArray(elementSpec.position) ?
        { x: elementSpec.position[0], y: elementSpec.position[1] } :
        elementSpec.position || { x: 0, y: 0 };
    const size = elementSpec.size || { width: 120, height: 60 };
    const text = elementSpec.text || elementSpec.label || elementSpec.id;

    // Access dia from the global scope set by render()
    const { dia } = (globalThis as any).__jointRuntimeDeps || {};
    if (!dia) throw new Error('Joint.js not initialized');

    return new dia.Element({
        id: elementSpec.id,
        position,
        size,
        markup: [
            { tagName: 'path', selector: 'body' },
            { tagName: 'text', selector: 'label' }
        ],
        attrs: {
            body: {
                d: `M 15,0 L ${size.width},0 L ${size.width - 15},${size.height} L 0,${size.height} Z`,
                fill: theme === 'dark' ? '#b48ead' : '#9b59b6',
                stroke: theme === 'dark' ? '#d08770' : '#8e44ad',
                strokeWidth: 2
            },
            label: {
                text: text,
                fill: theme === 'dark' ? '#eceff4' : '#ffffff',
                fontSize: 12,
                fontFamily: 'Arial, sans-serif',
                fontWeight: 'bold',
                textAnchor: 'middle',
                textVerticalAnchor: 'middle',
                x: size.width / 2,
                y: size.height / 2
            }
        }
    });
};

const createSubprocessElement = (elementSpec: JointElement, theme: 'light' | 'dark') => {
    const position = Array.isArray(elementSpec.position) ?
        { x: elementSpec.position[0], y: elementSpec.position[1] } :
        elementSpec.position || { x: 0, y: 0 };
    const size = elementSpec.size || { width: 120, height: 60 };
    const text = elementSpec.text || elementSpec.label || elementSpec.id;

    // Access dia from the global scope set by render()
    const { dia } = (globalThis as any).__jointRuntimeDeps || {};
    if (!dia) throw new Error('Joint.js not initialized');

    return new dia.Element({
        id: elementSpec.id,
        position,
        size,
        markup: [
            { tagName: 'rect', selector: 'body' },
            { tagName: 'rect', selector: 'plus1' },
            { tagName: 'rect', selector: 'plus2' },
            { tagName: 'text', selector: 'label' }
        ],
        attrs: {
            body: {
                x: 0, y: 0, width: size.width, height: size.height,
                fill: theme === 'dark' ? '#5e81ac' : '#3498db',
                stroke: theme === 'dark' ? '#81a1c1' : '#2980b9',
                strokeWidth: 2,
                rx: 5,
                ry: 5
            },
            plus1: {
                x: size.width / 2 - 8, y: size.height / 2 - 2,
                width: 16, height: 4,
                fill: theme === 'dark' ? '#eceff4' : '#ffffff'
            },
            plus2: {
                x: size.width / 2 - 2, y: size.height / 2 - 8,
                width: 4, height: 16,
                fill: theme === 'dark' ? '#eceff4' : '#ffffff'
            },
            label: {
                text: text,
                fill: theme === 'dark' ? '#eceff4' : '#ffffff',
                fontSize: 12,
                fontFamily: 'Arial, sans-serif',
                fontWeight: 'bold',
                textAnchor: 'middle',
                textVerticalAnchor: 'top',
                x: size.width / 2,
                y: 10
            }
        }
    });
};

const createManualElement = (elementSpec: JointElement, theme: 'light' | 'dark') => {
    const position = Array.isArray(elementSpec.position) ?
        { x: elementSpec.position[0], y: elementSpec.position[1] } :
        elementSpec.position || { x: 0, y: 0 };
    const size = elementSpec.size || { width: 120, height: 80 };
    const text = elementSpec.text || elementSpec.label || elementSpec.id;

    // Access dia from the global scope set by render()
    const { dia } = (globalThis as any).__jointRuntimeDeps || {};
    if (!dia) throw new Error('Joint.js not initialized');

    return new dia.Element({
        id: elementSpec.id,
        position,
        size,
        markup: [
            { tagName: 'path', selector: 'body' },
            { tagName: 'text', selector: 'label' }
        ],
        attrs: {
            body: {
                d: `M 0,15 Q 30,0 60,15 Q 90,0 120,15 L 120,80 L 0,80 Z`,
                fill: theme === 'dark' ? '#d08770' : '#e67e22',
                stroke: theme === 'dark' ? '#bf616a' : '#d35400',
                strokeWidth: 2
            },
            label: {
                text: text,
                fill: theme === 'dark' ? '#eceff4' : '#ffffff',
                fontSize: 12,
                fontFamily: 'Arial, sans-serif',
                fontWeight: 'bold',
                textAnchor: 'middle',
                textVerticalAnchor: 'middle',
                x: size.width / 2,
                y: size.height / 2 + 5
            }
        }
    });
};

// Add missing shape creation functions that are referenced in the existing code
const createActorElement = (elementSpec: JointElement, theme: 'light' | 'dark') => {
    const position = Array.isArray(elementSpec.position) ?
        { x: elementSpec.position[0], y: elementSpec.position[1] } :
        elementSpec.position || { x: 0, y: 0 };
    const size = elementSpec.size || { width: 60, height: 100 };
    const text = elementSpec.text || elementSpec.label || elementSpec.id;

    // Access dia from the global scope set by render()
    const { dia } = (globalThis as any).__jointRuntimeDeps || {};
    if (!dia) throw new Error('Joint.js not initialized');

    return new dia.Element({
        id: elementSpec.id,
        position,
        size,
        markup: [
            { tagName: 'circle', selector: 'head' },
            { tagName: 'line', selector: 'body' },
            { tagName: 'line', selector: 'leftArm' },
            { tagName: 'line', selector: 'rightArm' },
            { tagName: 'line', selector: 'leftLeg' },
            { tagName: 'line', selector: 'rightLeg' },
            { tagName: 'text', selector: 'label' }
        ],
        attrs: {
            head: {
                cx: size.width / 2, cy: 15, r: 10,
                fill: theme === 'dark' ? '#d08770' : '#f39c12',
                stroke: theme === 'dark' ? '#bf616a' : '#e67e22',
                strokeWidth: 2
            },
            body: { x1: size.width / 2, y1: 25, x2: size.width / 2, y2: 60, stroke: theme === 'dark' ? '#eceff4' : '#2c3e50', strokeWidth: 3 },
            leftArm: { x1: size.width / 2, y1: 35, x2: size.width / 2 - 15, y2: 50, stroke: theme === 'dark' ? '#eceff4' : '#2c3e50', strokeWidth: 3 },
            rightArm: { x1: size.width / 2, y1: 35, x2: size.width / 2 + 15, y2: 50, stroke: theme === 'dark' ? '#eceff4' : '#2c3e50', strokeWidth: 3 },
            leftLeg: { x1: size.width / 2, y1: 60, x2: size.width / 2 - 15, y2: 85, stroke: theme === 'dark' ? '#eceff4' : '#2c3e50', strokeWidth: 3 },
            rightLeg: { x1: size.width / 2, y1: 60, x2: size.width / 2 + 15, y2: 85, stroke: theme === 'dark' ? '#eceff4' : '#2c3e50', strokeWidth: 3 },
            label: {
                text: text,
                fill: theme === 'dark' ? '#eceff4' : '#2c3e50',
                fontSize: 10,
                fontFamily: 'Arial, sans-serif',
                fontWeight: 'bold',
                textAnchor: 'middle',
                x: size.width / 2,
                y: size.height - 5
            }
        }
    });
};

const createLogicGate = (elementSpec: JointElement, gateType: string, theme: 'light' | 'dark') => {
    // Fallback to enhanced rectangle for logic gates
    return createEnhancedRectElement({
        ...elementSpec,
        text: `${gateType.toUpperCase()} Gate`
    }, theme);
};

const createCustomElement = (elementSpec: JointElement, theme: 'light' | 'dark') => {
    // Fallback to enhanced rectangle for custom elements
    return createEnhancedRectElement(elementSpec, theme);
};
