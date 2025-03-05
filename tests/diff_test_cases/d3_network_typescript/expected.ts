import * as d3 from 'd3';
import { D3RenderPlugin, D3Node, D3Link, D3Style } from '../../types/d3';

interface NetworkNodeStyle {
    fill: string;
    stroke: string;
}

interface NetworkStyles {
    [key: string]: NetworkNodeStyle;
}

interface NetworkLayout {
    linkDistance?: number;
    charge?: number;
    gravity?: number;
}

interface NetworkLabels {
    show: boolean;
    fontSize?: number;
    fontFamily?: string;
    distance?: number;
}

interface NetworkTooltips {
    show: boolean;
    template?: (node: any) => string;
}

interface NetworkLegend {
    show: boolean;
    position?: string;
    items: Array<{ label: string; type: string; }>;
}

interface NetworkAnimation {
    initial?: boolean;
    duration?: number;
}

interface NetworkOptions {
    width?: number;
    height?: number;
    nodeRadius?: number;
    styles?: NetworkStyles;
    layout?: NetworkLayout;
    labels?: NetworkLabels;
    tooltips?: NetworkTooltips;
    legend?: NetworkLegend;
    animation?: NetworkAnimation;
}

interface NetworkNode extends d3.SimulationNodeDatum {
    id: string;
    label?: string;
    group: number;
    type: string;
    // Required by D3Node
    x: number;
    y: number;
    // Optional simulation properties
    fx?: number | null;
    fy?: number | null;
} 

type D3Selection = d3.Selection<SVGGElement, unknown, HTMLElement, any>;
type NetworkNodeSelection = d3.Selection<SVGGElement, NetworkNode, SVGElement, unknown>;

export interface NetworkDiagramSpec {
    width: number;
    height: number;
    nodes: D3Node[];
    links: D3Link[];
    groups?: {
        id: string;
        label: string;
    }[];
    styles?: {
        [key: string]: D3Style;
    };
    options?: NetworkOptions;
};

const isNetworkDiagramSpec = (spec: any): spec is NetworkDiagramSpec => {
    return spec
        && typeof spec.width === 'number'
        && typeof spec.height === 'number'
        && Array.isArray(spec.nodes)
        && Array.isArray(spec.links);
};

export const networkDiagramPlugin: D3RenderPlugin = {
    type: 'network',

    isCompatible: (spec: any): boolean => {
        return isNetworkDiagramSpec(spec);
    },

    render: (container: HTMLElement, spec: NetworkDiagramSpec): void => {
        if (!isNetworkDiagramSpec(spec)) {
            throw new Error('Invalid network diagram specification');
        }

        // Log initial render state
        console.log('Rendering network diagram:', {
            container,
            width: spec.width,
            height: spec.height,
            nodes: spec.nodes.length,
            links: spec.links.length,
            groupCount: spec.groups?.length
        });

        try {
            const svg: D3Selection = d3.select(container)
                .selectAll('*').remove()
                .append('svg')
                .attr('width', spec.width)
                .attr('height', spec.height)
                .attr('viewBox', [0, 0, spec.width, spec.height])
                .style('overflow', 'visible');

            // Create force simulation
            const simulation = d3.forceSimulation(spec.nodes)
                .force('link', d3.forceLink(spec.links)
                    .id(d => (d as any).id)
                    .distance(d => (d as any).value ? 100 / (d as any).value : 100))
                .force('charge', d3.forceManyBody()
                    .strength(spec.options?.layout?.charge || -400))
                .force('center', d3.forceCenter(spec.width / 2, spec.height / 2))
                .force('collision', d3.forceCollide().radius(30));

            // Create container for links
            const links = svg.append('g')
                .attr('class', 'links')
                .selectAll('line')
                .data(spec.links)
                .join('line')
                .attr('stroke', '#999')
                .attr('stroke-opacity', 0.6)
                .attr('stroke-width', d => Math.sqrt(d.value || 1));

            // Create container for nodes
            const nodeGroup = svg.append('g')
                .attr('class', 'nodes')
                
            const nodes: NetworkNodeSelection = nodeGroup.selectAll('g')
                .data(spec.nodes.map(node => ({
                    ...node,
                    x: node.x || Math.random() * spec.width,
                    y: node.y || Math.random() * spec.height,
                    fx: null,
                    fy: null
                })) as NetworkNode[])
                .enter().append('g').attr('class', 'node');

            // Add circles for nodes
            nodes.append('circle')
                .attr('r', spec.options?.nodeRadius || 20)
                .attr('fill', d => spec.options?.styles?.[d.type]?.fill || '#69b3a2')
                .attr('stroke', d => spec.options?.styles?.[d.type]?.stroke || '#333')
                .attr('stroke-width', 1.5);

            // Add labels if enabled
            if (spec.options?.labels?.show) {
                nodes.append('text')
                    .text(d => d.label || d.id)
                    .attr('x', 0)
                    .attr('y', d => (spec.options?.nodeRadius || 20) + 15)
                    .attr('text-anchor', 'middle')
                    .attr('font-size', spec.options?.labels?.fontSize || 12)
                    .attr('font-family', spec.options?.labels?.fontFamily || 'Arial')
                    .attr('fill', '#666');
            }

            // Add tooltips if enabled
            if (spec.options?.tooltips?.show) {
                nodes.append('title')
                    .text(d => `${d.label || d.id}\nType: ${d.type}\nGroup: ${d.group}`);
            }

            // Add legend if enabled
            if (spec.options?.legend?.show) {
                const legend = svg.append('g')
                    .attr('class', 'legend')
                    .attr('transform', `translate(${spec.width - 150}, ${spec.height - 100})`);

                const legendItems = legend.selectAll('g')
                    .data(spec.options.legend.items)
                    .join('g')
                    .attr('transform', (d, i) => `translate(0, ${i * 20})`);

                legendItems.append('circle')
                    .attr('r', 6)
                    .attr('fill', d => spec.options?.styles?.[d.type]?.fill || '#999');

                legendItems.append('text')
                    .attr('x', 15)
                    .attr('y', 4)
                    .text(d => d.label);
            }

            // Add drag behavior
            const drag = d3.drag()
                .on('start', (event, d) => {
                    if (!event.active) simulation.alphaTarget(0.3).restart();
                    d.fx = d.x;
                    d.fy = d.y;
                })
                .on('drag', (event, d) => {
                    d.fx = event.x;
                    d.fy = event.y;
                })
                .on('end', (event, d) => {
                    if (!event.active) simulation.alphaTarget(0);
                    d.fx = null;
                    d.fy = null;
                });

            nodes.call(drag);

            // Update positions on each tick
            simulation.on('tick', () => {
                links
                    .attr('x1', d => (d.source as any).x)
                    .attr('y1', d => (d.source as any).y)
                    .attr('x2', d => (d.target as any).x)
                    .attr('y2', d => (d.target as any).y);

                nodes
                    .attr('transform', d => {
                        const x = Math.max(30, Math.min(spec.width - 30, d.x));
                        const y = Math.max(30, Math.min(spec.height - 30, d.y));
                        return `translate(${x},${y})`;
                    });
            });

        } catch (error) {
            console.error('Network diagram render error:', error);
            // Clean up on error
            d3.select(container).selectAll('*').remove();
            throw error;
        }
    }
};

// Export the plugin
export default networkDiagramPlugin;
