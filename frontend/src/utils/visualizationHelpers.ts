import { VegaLiteSpec, D3Spec } from './types';

interface NetworkNode extends d3.SimulationNodeDatum {
    id: string;
    group?: number;
    x?: number;
    y?: number;
}

interface NetworkLink extends d3.SimulationLinkDatum<NetworkNode> {
    source: string | NetworkNode;
    target: string | NetworkNode;
    value?: number;
}

export const createNetworkGraph = (
    nodes: NetworkNode[],
    links: NetworkLink[],
    d3: typeof import('d3'), // D3 instance passed from component
    options: {
        width?: number;
        height?: number;
        nodeColor?: string;
        linkColor?: string;
        nodeSize?: number;
        directed?: boolean;
    }
): D3Spec => ({
    type: 'custom',
    renderer: 'd3',
    render: (container: SVGSVGElement, width: number, height: number, isDarkMode: boolean, d3: typeof import('d3')) => {
        // Create force simulation
        const simulation = d3.forceSimulation<NetworkNode>(nodes)
            .force('link', d3.forceLink<NetworkNode, NetworkLink>(links)
                .id((d) => d.id))
            .force('charge', d3.forceManyBody().strength(-100))
            .force('center', d3.forceCenter(width / 2, height / 2));

        const svg = d3.select(container);

        // Add arrow marker for directed graphs
        if (options.directed) {
            svg.append('defs').append('marker')
                .attr('id', 'arrowhead')
                .attr('viewBox', '-0 -5 10 10')
                .attr('refX', 20)
                .attr('refY', 0)
                .attr('orient', 'auto')
                .attr('markerWidth', 6)
                .attr('markerHeight', 6)
                .append('path')
                .attr('d', 'M 0,-5 L 10,0 L 0,5')
                .attr('fill', isDarkMode ? '#666' : '#999');
        }

        // Create links
        const link = svg.append('g')
            .selectAll<SVGLineElement, NetworkLink>('line')
            .data(links)
            .join('line')
            .attr('stroke', options.linkColor || (isDarkMode ? '#666' : '#999'))
            .attr('stroke-opacity', 0.6)
            .attr('stroke-width', d => Math.sqrt(d.value || 1))
            .attr('marker-end', options.directed ? 'url(#arrowhead)' : null);

        // Create nodes
        const node = svg.append('g')
            .selectAll<SVGCircleElement, NetworkNode>('circle')
            .data(nodes)
            .join('circle')
            .attr('r', options.nodeSize || 5)
            .attr('fill', options.nodeColor || (isDarkMode ? '#fff' : '#000'));

        // Add titles for tooltips
        node.append('title')
            .text(d => d.id);

        // Add drag behavior
        const drag = d3.drag<SVGCircleElement, NetworkNode>()
            .on('start', (event, d) => {
                if (!event.active) simulation.alphaTarget(0.3).restart();
                d.fx = event.x;
                d.fy = event.y;
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

        node.call(drag);

        // Update positions on each tick
        simulation.on('tick', () => {
            link
                .attr('x1', d => (d.source as NetworkNode).x ?? 0)
                .attr('y1', d => (d.source as NetworkNode).y ?? 0)
                .attr('x2', d => (d.target as NetworkNode).x ?? 0)
                .attr('y2', d => (d.target as NetworkNode).y ?? 0);

            node
                .attr('cx', d => d.x ?? 0)
                .attr('cy', d => d.y ?? 0);
        });

        return () => {
            simulation.stop();
        };
    }
});
