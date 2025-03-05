import * as d3 from 'd3';
import { D3RenderPlugin, D3Node, D3Link, D3Style } from '../../types/d3'; 

interface NetworkNodeStyle {
    fill: string;
    stroke: string;
}

interface NetworkNode extends d3.SimulationNodeDatum, D3Node {
    id: string;
    label?: string;
    group: number;
    type: string;
    x?: number;
    y?: number;
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
 
type D3Selection = d3.Selection<d3.BaseType, unknown, HTMLElement, any>;
type NetworkNodeSelection = d3.Selection<SVGGElement, NetworkNode, HTMLElement, any>;

export interface NetworkDiagramSpec {
    width: number;
    height: number;
    nodes: D3Node[];
    links: D3Link[];
    groups?: Array<{
        id: string;
        label: string;
        members: string[];
    }>;
    styles?: {
        [key: string]: D3Style;
    };
    options?: NetworkOptions;
};

const isNetworkDiagramSpec = (spec: any): spec is NetworkDiagramSpec => {
    return (
        typeof spec === 'object' &&
        !spec.render && // Don't handle specs with direct render functions
        spec.type === 'network' &&
        Array.isArray(spec.nodes) &&
        Array.isArray(spec.links) &&
        spec.nodes.length > 0 &&
        spec.links.length > 0 &&
        spec.nodes.every((n: any) => typeof n.id === 'string' && n.id) &&
        spec.links.every((l: any) => typeof l.source === 'string' && typeof l.target === 'string')
    );
};

export const networkDiagramPlugin: D3RenderPlugin = {
    name: 'network-diagram',
    priority: 1,
    canHandle: isNetworkDiagramSpec,
    render: (container: HTMLElement, d3: any, spec: any) => {
        console.debug('Network diagram plugin rendering:', spec);
        if (!isNetworkDiagramSpec(spec)) {
            throw new Error('Invalid network diagram specification');
        }

        function isNetworkDiagramSpec(spec: any): spec is NetworkDiagramSpec {
            return (
                typeof spec === 'object' &&
                spec.type === 'network' &&
                Array.isArray(spec.nodes) &&
                Array.isArray(spec.links) &&
                spec.nodes.every((n: any) => typeof n.id === 'string') &&
                spec.links.every((l: any) => typeof l.source === 'string' && typeof l.target === 'string')
            );
        }
        console.debug('Network diagram render:', {
            nodeCount: spec.nodes.length,
            linkCount: spec.links.length,
            groupCount: spec.groups?.length
        });
        try {
            const svg: D3Selection = d3.select(container)
                .selectAll('*').remove()
                .append('svg')
                .attr('width', spec.width)
                .attr('viewBox', [0, 0, spec.width, spec.height])
                .style('overflow', 'visible');
            // Create board containers if groups exist
            if (spec.groups?.length) {
                const boards = svg.selectAll('.board')
                    .data(spec.groups)
                    .enter()
                    .append('g')
                    .attr('class', 'board')
                    .attr('transform', 'translate(0,0)');
                boards.append('rect')
                    .attr('x', d => d.id === 'modem_board' ? 180 : 680)
                    .attr('y', 50)
                    .attr('width', d => d.id === 'modem_board' ? 350 : 200)
                    .attr('height', 500)
                    .attr('fill', 'none')
                    .attr('stroke', '#666')
                    .attr('stroke-dasharray', '5,5');
                boards.append('text')
                    .attr('x', d => d.id === 'modem_board' ? 200 : 700)
                    .attr('y', 80)
                    .text(d => d.label)
                    .attr('fill', '#666');
            }
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
                .enter()
                .append('line')
                .attr('stroke', '#999')
                .attr('stroke-opacity', 0.6)
                .attr('stroke-width', d => Math.sqrt(d.value || 1));

            // Create container for nodes
            const nodeGroup = svg.append('g')
                .attr('class', 'nodes')

            const nodes: NetworkNodeSelection = nodeGroup.selectAll('g')
                .data(spec.nodes)
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
                    .attr('dy', d => (spec.options?.nodeRadius || 20) + 8)
                    .attr('text-anchor', 'middle')
                    .attr('dominant-baseline', 'middle')
                    .attr('font-size', spec.options?.labels?.fontSize || 12)
                    .attr('font-family', spec.options?.labels?.fontFamily || 'Arial')
                    .attr('pointer-events', 'none')
                    .style('user-select', 'none')
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
                    .attr('transform', `translate(${spec.width - 120}, 20)`); // Position in top-right

                // Add legend background
                legend.append('rect')
                    .attr('width', 110)
                    .attr('height', (spec.options.legend.items.length * 25) + 10)
                    .attr('fill', 'white')
                    .attr('stroke', '#ccc')
                    .attr('rx', 5)
                    .attr('ry', 5)
                    .attr('opacity', 0.9);

                const legendItems = legend.selectAll('g')
                    .data(spec.options.legend.items || [])
                    .enter().append('g')
                    .attr('transform', (d, i) => `translate(0, ${i * 20})`);

                legendItems.append('circle')
                    .attr('r', 6)
                    .attr('fill', d => spec.options?.styles?.[d.type]?.fill || '#999')
                    .attr('stroke', d => spec.options?.styles?.[d.type]?.stroke || '#666');

                legendItems.append('text')
                    .attr('x', 15)
                    .attr('y', 5)
                    .attr('dominant-baseline', 'middle')
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
                    .attr('transform', (d: NetworkNode) => {
                        const x = Math.max(30, Math.min(spec.width - 30, d.x || 0));
                        const y = Math.max(30, Math.min(spec.height - 30, d.y || 0));
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
