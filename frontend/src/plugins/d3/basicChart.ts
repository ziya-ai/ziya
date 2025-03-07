import * as d3 from 'd3';
import { D3RenderPlugin } from '../../types/d3';

export interface BasicChartSpec {
    type: 'bar' | 'line';
    data: Array<{
        label: string;
        value: number;
    }>;
    width?: number;
    height?: number;
    margin?: {
        top: number;
        right: number;
        bottom: number;
        left: number;
    };
}

const defaultMargin = { top: 20, right: 20, bottom: 30, left: 40 };

const isBasicChartSpec = (spec: any): spec is BasicChartSpec => {
    return (
        typeof spec === 'object' &&
        (spec.type === 'bar' || spec.type === 'line') &&
        Array.isArray(spec.data) &&
        spec.data.length > 0 &&
        spec.data.every((d: any) => 
            typeof d.label === 'string' && 
            typeof d.value === 'number'
        )
    );
};

export const basicChartPlugin: D3RenderPlugin = {
    name: 'basic-chart',
    priority: 1,
    canHandle: isBasicChartSpec,
    render: (container: HTMLElement, spec: BasicChartSpec) => {
        const width = spec.width || 600;
        const height = spec.height || 400;
        const margin = { ...defaultMargin, ...spec.margin };

        try {
            // Clear any existing content
            d3.select(container).selectAll('*').remove();
            
            // Create SVG
            const svg = d3.select(container)
                .append('svg')
                .attr('width', width)
                .attr('height', height)
                .attr('viewBox', [0, 0, width, height])
                .style('overflow', 'visible')
                .style('display', 'block');

            const g = svg.append('g')
                .attr('transform', `translate(${margin.left},${margin.top})`);

            // Create scales
            const x = d3.scaleBand()
                .domain(spec.data.map(d => d.label))
                .range([0, width - margin.left - margin.right])
                .padding(0.1);

            const y = d3.scaleLinear()
                .domain([0, d3.max(spec.data, d => d.value) || 0])
                .nice()
                .range([height - margin.top - margin.bottom, 0]);

            // Add X axis
            g.append('g')
                .attr('transform', `translate(0,${height - margin.top - margin.bottom})`)
                .call(d3.axisBottom(x))
                .selectAll('text')
                .style('text-anchor', 'middle');

            // Add Y axis
            g.append('g')
                .call(d3.axisLeft(y));

            if (spec.type === 'bar') {
                // Create bars
                g.selectAll('rect')
                    .data(spec.data)
                    .join('rect')
                    .attr('x', d => x(d.label) ?? 0)
                    .attr('y', d => y(d.value) ?? 0)
                    .attr('height', d => (y(0) ?? 0) - (y(d.value) ?? 0))
                    .attr('width', () => x.bandwidth())
                    .attr('fill', 'steelblue');
            } else if (spec.type === 'line') {
                // Create line
                const line = d3.line<{label: string; value: number}>()
                    .x(d => {
                        const xPos = x(d.label);
                        return xPos === undefined ? 0 : xPos + x.bandwidth() / 2;
                    })
                    .y(d => y(d.value) ?? 0)
                    .defined(d => x(d.label) !== undefined && y(d.value) !== undefined);

                g.append('path')
                    .datum(spec.data)
                    .attr('fill', 'none')
                    .attr('stroke', 'steelblue')
                    .attr('stroke-width', 1.5)
                    .attr('d', line);
            }
        } catch (error) {
            console.error('Error rendering basic chart:', error);
            throw error;
        }
    }
}
