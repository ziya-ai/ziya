import { D3RenderPlugin, D3Node, D3Link, D3Style } from '../../types/d3';

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
};

const isNetworkDiagramSpec = (spec: any): spec is NetworkDiagramSpec => {
    return (
        typeof spec === 'object' &&
        spec.type === 'network' && // Check for network type
        Array.isArray(spec.nodes) &&
        Array.isArray(spec.links) &&
        spec.nodes.length >= 0 &&
        spec.nodes.every((n: any) => typeof n.id === 'string') &&
        spec.links.length >= 0 &&
        spec.links.every((l: any) => typeof l.source === 'string' && typeof l.target === 'string')
    );
};

export const networkDiagramPlugin: D3RenderPlugin = {
    name: 'network-diagram',
    priority: 1,
    sizingConfig: {
        sizingStrategy: 'responsive',
        needsDynamicHeight: false,
        needsOverflowVisible: false,
        observeResize: false,
        containerStyles: {
            height: '400px',
            overflow: 'auto'
        }
    },
    canHandle: isNetworkDiagramSpec,
    render: (container: HTMLElement, d3: any, spec: any) => {
        console.debug('Network diagram plugin rendering:', { spec });

        if (!isNetworkDiagramSpec(spec)) {
            throw new Error('Invalid network diagram specification');
        }

        console.debug('Network diagram render:', {
            nodeCount: spec.nodes.length,
            linkCount: spec.links.length,
            groupCount: spec.groups?.length
        });
        try {
            const svg = d3.select(container)
                .selectAll('*').remove()  // Clear existing content
                .append('svg')
                .attr('width', spec.width)
                .attr('height', spec.height)
                .attr('viewBox', [0, 0, spec.width, spec.height]);
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
            // Draw links first (rest of the rendering code remains the same)
            // ... (previous link rendering code)
            // Draw nodes
            // ... (previous node rendering code)
        } catch (error) {
            console.error('Network diagram render error:', error);
            // Clean up on error
            d3.select(container).selectAll('*').remove();
            throw error;
        }
    }
};
