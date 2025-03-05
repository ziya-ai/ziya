import { BaseType, Selection } from 'd3';
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
            const svg = d3.select(container)
                .append('svg')
                .attr('width', spec.width)
                .attr('height', spec.height || 400)
                .attr('viewBox', [0, 0, spec.width || 600, spec.height || 400])
                .style('overflow', 'visible')
                .style('display', 'block');
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
            
            // Draw links
            const links = svg.append('g')
                .attr('class', 'links')
                .selectAll('line')
                .data(spec.links)
                .enter()
                .append('line')
                .attr('stroke', d => d.color || '#999')
                .attr('stroke-width', 1.5)
                .attr('stroke-dasharray', d => d.dashed ? '5,5' : null)
                .attr('x1', d => {
                    const source = spec.nodes.find(n => n.id === d.source);
                    return source ? source.x : 0;
                })
                .attr('y1', d => {
                    const source = spec.nodes.find(n => n.id === d.source);
                    return source ? source.y : 0;
                })
                .attr('x2', d => {
                    const target = spec.nodes.find(n => n.id === d.target);
                    return target ? target.x : 0;
                })
                .attr('y2', d => {
                    const target = spec.nodes.find(n => n.id === d.target);
                    return target ? target.y : 0;
                });
                
            // Draw nodes
            const nodes = svg.append('g')
                .attr('class', 'nodes')
                .selectAll('circle')
                .data(spec.nodes)
                .enter()
                .append('circle')
                .attr('r', 5)
                .attr('cx', d => d.x)
                .attr('cy', d => d.y)
                .attr('fill', d => d.group ? spec.styles?.[d.group]?.fill || '#69b3a2' : '#69b3a2')
                .attr('stroke', d => d.group ? spec.styles?.[d.group]?.stroke || '#333' : '#333')
                .attr('stroke-width', 1.5);
        } catch (error) {
            console.error('Network diagram render error:', error);
            // Clean up on error
            d3.select(container).selectAll('*').remove();
            throw error;
        }
    }
};
