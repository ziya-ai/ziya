import { dia, shapes, util } from 'jointjs';
import { D3RenderPlugin } from '../../types/d3';
import { isDiagramDefinitionComplete } from '../../utils/diagramUtils';

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
}

interface JointElement {
    id: string;
    type?: string;
    position?: { x: number; y: number } | [number, number];
    size?: { width: number; height: number };
    attrs?: any;
    text?: string;
    ports?: any[];
}

interface JointLink {
    id: string;
    source: string | { id: string; port?: string };
    target: string | { id: string; port?: string };
    label?: string;
    labels?: any[];
    vertices?: { x: number; y: number }[];
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

// Parse simplified Joint.js syntax
const parseJointDefinition = (definition: string): { elements: JointElement[]; connections: JointLink[] } => {
    const lines = definition.split('\n').map(line => line.trim()).filter(line => line && !line.startsWith('//') && !line.startsWith('#') && !line.startsWith('```'));
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
            const elementMatch = line.match(/^(\w+)(?:\s*\[(\w+)\])?(?:\s*"([^"]*)")?(?:\s*@\((\d+),\s*(\d+)\))?(?:\s*size\((\d+),\s*(\d+)\))?/);
            if (elementMatch) {
                const [, id, type, label, x, y, w, h] = elementMatch;
                elements.push({
                    id,
                    type: type || 'rect',
                    position: x && y ?
                        { x: parseInt(x), y: parseInt(y) } :
                        {
                            x: (elements.length % 4) * 180 + 80,
                            y: Math.floor(elements.length / 4) * 120 + 60
                        },
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
                        position: {
                            x: (elements.length % 4) * 180 + 80,
                            y: Math.floor(elements.length / 4) * 120 + 60
                        },
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

    return { elements, connections: links };
};

// Create Joint.js elements from specification
const createElement = (elementSpec: JointElement, theme: 'light' | 'dark') => {
    const position = Array.isArray(elementSpec.position) ?
        { x: elementSpec.position[0], y: elementSpec.position[1] } :
        elementSpec.position || { x: 0, y: 0 };
    const size = elementSpec.size || { width: 120, height: 80 };
    const text = elementSpec.text || elementSpec.id;

    const commonAttrs = {
        body: {
            fill: theme === 'dark' ? '#2f3349' : '#ffffff',
            stroke: theme === 'dark' ? '#4cc9f0' : '#333333',
            strokeWidth: 2,
            rx: 5,
            ry: 5
        },
        label: {
            text: text,
            fill: theme === 'dark' ? '#ffffff' : '#000000',
            fontSize: 14,
            fontFamily: 'Arial, sans-serif',
            textAnchor: 'middle',
            textVerticalAnchor: 'middle'
        }
    };

    let element: dia.Element;
    switch (elementSpec.type || 'rect') {
        case 'circle':
            element = new shapes.basic.Circle({
                id: elementSpec.id,
                position,
                size,
                attrs: {
                    ...commonAttrs,
                    circle: commonAttrs.body
                }
            });
            break;
        case 'ellipse':
            element = new shapes.basic.Ellipse({
                id: elementSpec.id,
                position,
                size,
                attrs: {
                    ...commonAttrs,
                    ellipse: commonAttrs.body
                }
            });
            break;
        case 'cylinder':
            // Cylinder doesn't exist in basic shapes, use Ellipse as fallback
            element = new shapes.basic.Ellipse({
                id: elementSpec.id,
                position,
                size,
                attrs: {
                    ...commonAttrs,
                    ellipse: commonAttrs.body
                }
            });
            break;
        case 'diamond':
            // Use Rect with custom styling for diamond shape
            element = new shapes.basic.Rect({
                id: elementSpec.id,
                position,
                size,
                attrs: commonAttrs
            });
            break;
        default: // 'rect' or any other type
            element = new shapes.basic.Rect({
                id: elementSpec.id,
                position,
                size,
                attrs: commonAttrs
            });
    }

    // Add ports if specified
    if (elementSpec.ports) {
        elementSpec.ports.forEach(port => {
            element.addPort(port);
        });
    }

    return element;
};

// Create Joint.js links from specification
const createLink = (linkSpec: JointLink, theme: 'light' | 'dark') => {
    const linkAttrs = {
        line: {
            stroke: theme === 'dark' ? '#88c0d0' : '#333333',
            strokeWidth: 2,
            targetMarker: {
                type: 'path',
                d: 'M 10 -5 0 0 10 5 z',
                fill: theme === 'dark' ? '#88c0d0' : '#333333'
            }
        },
        wrapper: {
            strokeWidth: 10,
            strokeOpacity: 0
        }
    };

    const link = new dia.Link({
        id: linkSpec.id,
        source: { id: typeof linkSpec.source === 'string' ? linkSpec.source : linkSpec.source.id },
        target: { id: typeof linkSpec.target === 'string' ? linkSpec.target : linkSpec.target.id },
        attrs: linkAttrs
    });

    // Add label if specified
    if (linkSpec.label) {
        link.label(0, {
            position: 0.5,
            attrs: {
                text: {
                    text: linkSpec.label,
                    fill: theme === 'dark' ? '#ffffff' : '#000000',
                    fontSize: 12,
                    fontFamily: 'Arial, sans-serif'
                }
            }
        });
    }

    return link;
};

export const jointPlugin: D3RenderPlugin = {
    name: 'joint-renderer',
    priority: 6, // Higher than basic charts, lower than mermaid/graphviz

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
        console.log('Joint.js plugin render called');

        try {
            // Clear container
            container.innerHTML = '';

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
                const isComplete = isDiagramDefinitionComplete(definition, 'joint');

                if (!isComplete) {
                    loadingSpinner.innerHTML = `
                        <div style="text-align: center; padding: 20px; background-color: ${isDarkMode ? '#1f1f1f' : '#f6f8fa'}; border: 1px dashed #ccc; border-radius: 4px;">
                            <p>Waiting for complete Joint.js diagram definition...</p>
                        </div>
                    `;
                    return;
                }
            }

            // Parse the specification
            let elements: JointElement[], connections: JointLink[];
            if (spec.definition) {
                const parsed = parseJointDefinition(spec.definition);
                elements = parsed.elements;
                connections = parsed.connections;
            } else if (spec.elements) {
                // Handle object format
                elements = Object.keys(spec.elements).map(id => ({
                    id,
                    ...spec.elements![id]
                }));
                connections = spec.connections || [];
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
            const width = spec.width || 800;
            const height = spec.height || 600;

            // Create Joint.js graph and paper
            const graph = new dia.Graph();

            // Remove loading spinner before creating paper
            if (loadingSpinner && loadingSpinner.parentNode === container) {
                container.removeChild(loadingSpinner);
            }

            const paper = new dia.Paper({
                el: container,
                width,
                height,
                gridSize: 10,
                model: graph,
                background: {
                    color: theme === 'dark' ? '#1f1f1f' : '#ffffff'
                }
            } as any);

            // Create Joint.js elements and links
            const jointElements = elements.map(elementSpec => createElement(elementSpec, theme));
            const jointLinks = connections.map(linkSpec => createLink(linkSpec, theme));

            // Add to graph
            graph.addCells(jointElements);
            graph.addCells(jointLinks);

            // Fit content to paper
            paper.scaleContentToFit({ padding: 20 });

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
                    <strong>Joint.js Error:</strong>
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
