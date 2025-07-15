import { D3RenderPlugin } from '../../types/d3';
import { isDiagramDefinitionComplete } from '../../utils/diagramUtils';
import { extractDefinitionFromYAML } from '../../utils/diagramUtils';
import ELK from 'elkjs';

export interface D2Spec {
    type: 'd2';
    isStreaming?: boolean;
    forceRender?: boolean;
    definition: string;
    layout?: 'elk' | 'dagre' | 'tala';
}

const isD2Spec = (spec: any): spec is D2Spec => {
    return (
        typeof spec === 'object' &&
        spec !== null &&
        spec.type === 'd2' &&
        typeof spec.definition === 'string' &&
        spec.definition.trim().length > 0
    );
};

// Enhanced D2 parser with better syntax support
// D2 parser and renderer
class D2Parser {
    private nodes: Map<string, any> = new Map();
    private edges: any[] = [];
    private containers: Map<string, any> = new Map();
    private currentContainer: string | null = null;
    private containerStack: string[] = [];

    constructor() {
        this.reset();
    }

    private reset() {
        this.nodes.clear();
        this.edges = [];
        this.containers.clear();
        this.containerStack = [];
        this.currentContainer = null;
    }

    parse(definition: string) {
        this.reset();
        
        const lines = definition.split('\n')
            .map(line => line.trim())
            .filter(line => line && !line.startsWith('#'));
        
        for (const line of lines) {
            this.parseLine(line);
        }

        return {
            nodes: Array.from(this.nodes.values()),
            edges: this.edges,
            containers: Array.from(this.containers.values())
        };
    }

    private parseLine(line: string) {
        // Handle container start/end
        if (line.endsWith('{')) {
            // Handle nested containers
            const containerName = line.replace('{', '').trim();
            if (this.currentContainer) {
                this.containerStack.push(this.currentContainer);
            }
            this.currentContainer = containerName;
            this.containers.set(containerName, {
                id: containerName,
                label: containerName,
                type: 'container',
                children: [],
                parent: this.containerStack.length > 0 ? this.containerStack[this.containerStack.length - 1] : null
            });
            return;
        }
        
        if (line === '}') {
            // Pop from container stack for nested containers
            this.currentContainer = this.containerStack.pop() || null;
            return;
        }

        // Handle connections (edges)
        if (line.includes('->') || line.includes('<->') || line.includes('<-')) {
            this.parseConnection(line);
        }
        // Handle node definitions with properties
        else if (line.includes(':') && (line.includes('{') || line.includes('}'))) {
            this.parseNodeWithProperties(line);
        }
        // Handle simple node definitions
        else if (line.includes(':')) {
            this.parseSimpleNode(line);
        }
        // Handle style definitions
        else if (line.includes('.') && line.includes(':')) {
            this.parseStyleDefinition(line);
        }
    }

    private parseConnection(line: string) {
        const connectionRegex = /([^-<>]+)(\s*<?-+>?\s*)([^-<>]+)(?:\s*:\s*(.+))?/;
        const match = line.match(connectionRegex);
        
        if (match) {
            let source = match[1].trim();
            const connector = match[2].trim();
            let target = match[3].trim();
            const label = match[4]?.trim();

            // Handle dotted paths (e.g., container.node)
            source = this.resolvePath(source);
            target = this.resolvePath(target);

            // Ensure nodes exist
            this.ensureNode(source);
            this.ensureNode(target);

            const edge = {
                source: this.normalizeNodeId(source),
                target: this.normalizeNodeId(target),
                label: label || '',
                bidirectional: connector.includes('<->'),
                reversed: connector.startsWith('<-') && !connector.includes('<->')
            };

            this.edges.push(edge);
        }
    }

    private parseSimpleNode(line: string) {
        const parts = line.split(':');
        if (parts.length >= 2) {
            const nodeId = parts[0].trim();
            const label = parts.slice(1).join(':').trim();

            this.nodes.set(nodeId, {
                id: this.normalizeNodeId(nodeId),
                label: label || nodeId,
                originalId: nodeId,
                container: this.currentContainer
            });

            // Add to current container if we're inside one
            if (this.currentContainer && this.containers.has(this.currentContainer)) {
                this.containers.get(this.currentContainer).children.push(nodeId);
            }
        }
    }

    private parseNodeWithProperties(line: string) {
        // Handle node properties like: node: { shape: circle; fill: blue }
        const match = line.match(/([^:]+):\s*\{([^}]+)\}/);
        if (match) {
            const nodeId = match[1].trim();
            const properties = match[2].trim();

            const node = this.nodes.get(nodeId) || {
                id: this.normalizeNodeId(nodeId),
                label: nodeId,
                originalId: nodeId,
                container: this.currentContainer
            };

            // Parse properties
            const props = this.parseProperties(properties);
            Object.assign(node, props);

            this.nodes.set(nodeId, node);

            // Add to current container if we're inside one
            if (this.currentContainer && this.containers.has(this.currentContainer)) {
                this.containers.get(this.currentContainer).children.push(nodeId);
            }
        }
    }

    private parseStyleDefinition(line: string) {
        // Handle style definitions like: *.shape: circle
        const match = line.match(/([^:]+):\s*(.+)/);
        if (match) {
            const selector = match[1].trim();
            const value = match[2].trim();
            
            // Apply styles to matching nodes
            if (selector.startsWith('*.')) {
                const property = selector.substring(2);
                // Apply to all nodes - this is a simplified implementation
                // In a full implementation, you'd store these styles and apply them during rendering
            }
        }
    }

    private parseProperties(propString: string): any {
        const props: any = {};
        const pairs = propString.split(';');

        for (const pair of pairs) {
            const [key, value] = pair.split(':').map(s => s.trim());
            if (key && value) {
                props[key] = value;
            }
        }

        return props;
    }

    private resolvePath(path: string): string {
        // Handle dotted paths like container.node
        if (path.includes('.')) {
            const parts = path.split('.');
            // For now, just use the last part as the node ID
            // In a full implementation, you'd handle the hierarchy properly
            return parts[parts.length - 1];
        }
        return path;
    }

    private ensureNode(nodeId: string) {
        if (!this.nodes.has(nodeId)) {
            this.nodes.set(nodeId, {
                id: this.normalizeNodeId(nodeId),
                label: nodeId,
                originalId: nodeId,
                container: this.currentContainer
            });
        }
    }

    private normalizeNodeId(id: string): string {
        return id.replace(/[^a-zA-Z0-9]/g, '_');
    }
}

// Full ELK layout engine integration
class ELKLayoutEngine {
    private elk: any;

    constructor() {
        this.elk = new ELK();
    }

    async layout(nodes: any[], edges: any[], options: any = {}) {
        if (nodes.length === 0) {
            return { nodes: [], edges: [] };
        }

        // Create ELK graph structure
        const elkGraph = {
            id: 'root',
            layoutOptions: {
                'elk.algorithm': options.algorithm || 'layered',
                'elk.direction': options.direction || 'DOWN',
                'elk.spacing.nodeNode': options.nodeSpacing || '50',
                'elk.layered.spacing.nodeNodeBetweenLayers': options.layerSpacing || '50',
                'elk.spacing.edgeNode': '30',
                'elk.spacing.edgeEdge': '15',
                'elk.layered.crossingMinimization.strategy': 'LAYER_SWEEP',
                'elk.layered.nodePlacement.strategy': 'BRANDES_KOEPF',
                'elk.layered.cycleBreaking.strategy': 'GREEDY',
                'elk.insideSelfLoops.activate': 'true',
                ...options
            },
            children: nodes.map(node => ({
                id: node.id,
                width: this.calculateNodeWidth(node.label || node.id),
                height: this.calculateNodeHeight(node.label || node.id),
                labels: node.label ? [{
                    text: node.label,
                    layoutOptions: {
                        'elk.labelManager': 'none'
                    }
                }] : [],
                layoutOptions: {
                    'elk.nodeSize.constraints': 'NODE_LABELS',
                    'elk.nodeSize.options': 'DEFAULT_MINIMUM_SIZE COMPUTE_PADDING',
                    'elk.padding': '[top=10,left=15,bottom=10,right=15]'
                }
            })),
            edges: edges.map(edge => ({
                id: `${edge.source}_${edge.target}`,
                sources: [edge.source],
                targets: [edge.target],
                labels: edge.label ? [{
                    text: edge.label,
                    layoutOptions: {
                        'elk.edgeLabels.placement': 'CENTER'
                    }
                }] : [],
                layoutOptions: {
                    'elk.edge.type': edge.bidirectional ? 'UNDIRECTED' : 'DIRECTED'
                }
            }))
        };

        try {
            // Use ELK to compute the layout
            const layoutedGraph = await this.elk.layout(elkGraph);

            // Transform ELK result back to our format
            const layoutedNodes = layoutedGraph.children?.map((elkNode: any) => {
                const originalNode = nodes.find(n => n.id === elkNode.id);
                return {
                    ...originalNode,
                    x: elkNode.x || 0,
                    y: elkNode.y || 0,
                    width: elkNode.width || 100,
                    height: elkNode.height || 50
                };
            }) || [];

            return { nodes: layoutedNodes, edges };
        } catch (error) {
            console.warn('ELK layout failed, falling back to simple layout:', error);
            return this.simpleGridLayout(nodes, edges);
        }
    }

    private calculateNodeWidth(text: string): number {
        // Estimate width based on text length with minimum and maximum bounds
        const baseWidth = 80;
        const charWidth = 8;
        const padding = 30;
        return Math.max(baseWidth, Math.min(text.length * charWidth + padding, 200));
    }

    private calculateNodeHeight(text: string): number {
        // Calculate height based on text wrapping
        const baseHeight = 40;
        const lineHeight = 16;
        const maxWidth = 180;
        const estimatedLines = Math.ceil((text.length * 8) / maxWidth);
        return Math.max(baseHeight, baseHeight + (estimatedLines - 1) * lineHeight);
    }

    private simpleGridLayout(nodes: any[], edges: any[]) {
        // Fallback layout when ELK fails
        const cols = Math.ceil(Math.sqrt(nodes.length));
        const nodeSpacing = 150;

        nodes.forEach((node, index) => {
            const row = Math.floor(index / cols);
            const col = index % cols;
            node.x = col * nodeSpacing + 100;
            node.y = row * nodeSpacing + 100;
            node.width = this.calculateNodeWidth(node.label || node.id);
            node.height = this.calculateNodeHeight(node.label || node.id);
        });

        return { nodes, edges };
    }
}

export const d2Plugin: D3RenderPlugin = {
    name: 'd2-renderer',
    priority: 6,
    canHandle: isD2Spec,

    isDefinitionComplete: (definition: string): boolean => {
        if (!definition || definition.trim().length === 0) return false;

        // Check for basic D2 syntax patterns
        const lines = definition.trim().split('\n').filter(line => line.trim());
        if (lines.length === 0) return false;

        // Look for connections or node definitions
        const hasConnections = lines.some(line =>
            line.includes('->') || line.includes('<->') || line.includes('<-')
        );
        const hasNodes = lines.some(line => line.includes(':'));

        return hasConnections || hasNodes;
    },

    render: async (container: HTMLElement, d3: any, spec: D2Spec, isDarkMode: boolean) => {
        try {
            // Check if streaming and incomplete
            if (spec.isStreaming && !spec.forceRender) {
                const isComplete = d2Plugin.isDefinitionComplete!(spec.definition);
                if (!isComplete) {
                    container.innerHTML = `
                        <div style="text-align: center; padding: 20px; background-color: ${isDarkMode ? '#1f1f1f' : '#f6f8fa'}; border: 1px dashed #ccc; border-radius: 4px;">
                            <p>Waiting for complete D2 definition...</p>
                        </div>
                    `;
                    return;
                }
            }

            // Parse D2 definition
            const extractedDefinition = extractDefinitionFromYAML(spec.definition, 'd2');
            const parser = new D2Parser();
            const { nodes, edges, containers } = parser.parse(extractedDefinition);

            if (nodes.length === 0) {
                container.innerHTML = `
                    <div style="text-align: center; padding: 20px; color: ${isDarkMode ? '#ff6b6b' : '#d63031'};">
                        <p>No nodes found in D2 definition</p>
                    </div>
                `;
                return;
            }

            // Apply layout
            const layoutEngine = new ELKLayoutEngine();
            // Configure layout options based on diagram complexity
            const layoutOptions = {
                algorithm: spec.layout || 'layered',
                direction: containers.length > 0 ? 'DOWN' : 'RIGHT',
                nodeSpacing: '60',
                layerSpacing: '80'
            };

            const layoutResult = await layoutEngine.layout(nodes, edges, layoutOptions);

            // Render with D3
            container.innerHTML = '';
        const svg = d3.select(container)
            .append('svg')
            .attr('width', '100%')
            .attr('height', () => {
                // Calculate height based on layout
                const maxY = Math.max(...layoutResult.nodes.map(n => n.y + n.height));
                return Math.max(400, maxY + 100) + 'px';
            })
            .attr('viewBox', () => {
                const maxX = Math.max(...layoutResult.nodes.map(n => n.x + n.width));
                const maxY = Math.max(...layoutResult.nodes.map(n => n.y + n.height));
                return `0 0 ${Math.max(800, maxX + 100)} ${Math.max(400, maxY + 100)}`;
            });

            // Theme colors
    const colors = {
        node: isDarkMode ? '#4361ee' : '#e3f2fd',
        nodeStroke: isDarkMode ? '#4cc9f0' : '#1976d2',
        edge: isDarkMode ? '#f72585' : '#666666',
        text: isDarkMode ? '#ffffff' : '#000000'
    };

            // Render containers first (as background rectangles)
    if (containers.length > 0) {
        svg.selectAll('.container')
            .data(containers)
            .enter()
            .append('rect')
            .attr('class', 'container')
            .attr('x', d => Math.min(...d.children.map(childId => {
                    const node = layoutResult.nodes.find(n => n.originalId === childId);
                return node ? node.x - 20 : 0;
            })))
            .attr('y', d => Math.min(...d.children.map(childId => {
                    const node = layoutResult.nodes.find(n => n.originalId === childId);
                return node ? node.y - 20 : 0;
            })))
            .attr('width', d => Math.max(...d.children.map(childId => {
                    const node = layoutResult.nodes.find(n => n.originalId === childId);
                return node ? node.x + node.width + 40 : 100;
            })) - Math.min(...d.children.map(childId => {
                    const node = layoutResult.nodes.find(n => n.originalId === childId);
                return node ? node.x - 20 : 0;
            })))
            .attr('height', d => Math.max(...d.children.map(childId => {
                    const node = layoutResult.nodes.find(n => n.originalId === childId);
                return node ? node.y + node.height + 40 : 50;
            })) - Math.min(...d.children.map(childId => {
                    const node = layoutResult.nodes.find(n => n.originalId === childId);
                return node ? node.y - 20 : 0;
            })))
            .attr('fill', 'none')
            .attr('stroke', isDarkMode ? '#4cc9f0' : '#1976d2')
            .attr('stroke-width', 2)
            .attr('stroke-dasharray', '5,5')
            .attr('rx', 10);
    }

            // Add arrowhead marker
            svg.append('defs')
                .append('marker')
                .attr('id', 'arrowhead')
                .attr('viewBox', '0 -5 10 10')
                .attr('refX', 8)
                .attr('refY', 0)
                .attr('markerWidth', 6)
                .attr('markerHeight', 6)
                .attr('orient', 'auto')
                .append('path')
                .attr('d', 'M0,-5L10,0L0,5')
                .attr('fill', colors.edge);

            // Render edges
            svg.selectAll('.edge')
    .data(layoutResult.edges)
    .enter()
    .append('line')
    .attr('class', 'edge')
    .attr('x1', d => {
        const sourceNode = layoutResult.nodes.find(n => n.id === d.source);
        return sourceNode ? sourceNode.x + sourceNode.width / 2 : 0;
    })
    .attr('y1', d => {
        const sourceNode = layoutResult.nodes.find(n => n.id === d.source);
        return sourceNode ? sourceNode.y + sourceNode.height / 2 : 0;
    })
    .attr('x2', d => {
        const targetNode = layoutResult.nodes.find(n => n.id === d.target);
        return targetNode ? targetNode.x + targetNode.width / 2 : 0;
    })
    .attr('y2', d => {
        const targetNode = layoutResult.nodes.find(n => n.id === d.target);
        return targetNode ? targetNode.y + targetNode.height / 2 : 0;
    })
    .attr('stroke', colors.edge)
    .attr('stroke-width', 2)
    .attr('marker-end', 'url(#arrowhead)');

            // Render nodes
const nodeGroups = svg.selectAll('.node')
    .data(layoutResult.nodes)
    .enter()
    .append('g')
    .attr('class', 'node')
    .attr('transform', d => `translate(${d.x}, ${d.y})`);

nodeGroups.append('rect')
    .attr('width', d => d.width)
    .attr('height', d => d.height)
    .attr('fill', colors.node)
    .attr('stroke', colors.nodeStroke)
    .attr('stroke-width', 2)
    .attr('rx', 5);

nodeGroups.append('text')
    .attr('x', d => d.width / 2)
    .attr('y', d => d.height / 2)
    .attr('text-anchor', 'middle')
    .attr('dominant-baseline', 'middle')
    .attr('fill', colors.text)
    .attr('font-family', 'Arial, sans-serif')
    .attr('font-size', '12px')
    .text(d => d.label);

            // Add edge labels if they exist
            svg.selectAll('.edge-label')
                .data(layoutResult.edges.filter(d => d.label))
                .enter()
                .append('text')
                .attr('class', 'edge-label')
                .attr('x', d => {
                    const sourceNode = layoutResult.nodes.find(n => n.id === d.source);
                    const targetNode = layoutResult.nodes.find(n => n.id === d.target);
                    if (sourceNode && targetNode) {
                        return (sourceNode.x + sourceNode.width / 2 + targetNode.x + targetNode.width / 2) / 2;
                    }
                    return 0;
                })
                .attr('y', d => {
                    const sourceNode = layoutResult.nodes.find(n => n.id === d.source);
                    const targetNode = layoutResult.nodes.find(n => n.id === d.target);
                    if (sourceNode && targetNode) {
                        return (sourceNode.y + sourceNode.height / 2 + targetNode.y + targetNode.height / 2) / 2;
                    }
                    return 0;
                })
                .attr('text-anchor', 'middle')
                .attr('dominant-baseline', 'middle')
                .attr('fill', colors.text)
                .attr('font-family', 'Arial, sans-serif')
                .attr('font-size', '10px')
                .attr('background', isDarkMode ? '#1f1f1f' : '#ffffff')
                .text(d => d.label);

        } catch (error) {
            console.error('D2 rendering error:', error);
            container.innerHTML = `
                <div style="
                    padding: 20px;
                    background-color: ${isDarkMode ? '#2a1f1f' : '#fff2f0'};
                    border: 1px solid ${isDarkMode ? '#a61d24' : '#ffa39e'};
                    border-radius: 6px;
                    color: ${isDarkMode ? '#ff7875' : '#cf1322'};
                ">
                    <strong>D2 Rendering Error:</strong>
                    <pre style="margin: 10px 0; white-space: pre-wrap;">${error instanceof Error ? error.message : 'Unknown error'}</pre>
                    <details style="margin-top: 10px;">
                        <summary style="cursor: pointer; font-weight: bold;">Show D2 Definition</summary>
                        <pre style="
                            margin: 10px 0;
                            padding: 10px;
                            background-color: ${isDarkMode ? '#1f1f1f' : '#f6f8fa'};
                            border-radius: 4px;
                            overflow-x: auto;
                            white-space: pre-wrap;
                        "><code>${spec.definition}</code></pre>
                    </details>
                </div>
            `;
        }
    }
};