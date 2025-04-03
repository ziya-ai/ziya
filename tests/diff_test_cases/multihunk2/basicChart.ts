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
    priority: 10, // Higher priority than network diagram
    canHandle: (spec: any) => spec.type === 'chart' && spec.chartType === 'bar',
    render: (container: HTMLElement, d3: any, spec: any) => {
        console.debug('Basic chart plugin rendering:', spec);

        try {
            // Clear any existing content
            d3.select(container).selectAll('*').remove();
            
            const margin = spec.options?.margin || defaultMargin;
            const width = (spec.options?.width || 600) - margin.left - margin.right;
            const height = (spec.options?.height || 400) - margin.top - margin.bottom;

            // Create SVG
            const svg = d3.select(container)
                .append('svg')
                .attr('width', width + margin.left + margin.right)
                .attr('height', height + margin.top + margin.bottom)
                .append('g')
                .attr('transform', `translate(${margin.left},${margin.top})`);

            // Create scales
            const x = d3.scaleBand()
                .range([0, width])
                .domain(spec.data.map((d: any) => d.label))
                .padding(0.1);

            const y = d3.scaleLinear()
                .range([height, 0])
                .domain([0, d3.max(spec.data, (d: any) => d.value)]);

            // Add X axis
            svg.append('g')
                .attr('transform', `translate(0,${height})`)
                .call(d3.axisBottom(x));

            // Add Y axis
            svg.append('g')
                .call(d3.axisLeft(y));

            // Add bars
            svg.selectAll('rect')
                .data(spec.data)
                .join('rect')
                .attr('x', (d: any) => x(d.label))
                .attr('y', (d: any) => y(d.value))
                .attr('width', x.bandwidth())
                .attr('height', (d: any) => height - y(d.value))
                .attr('fill', 'steelblue');
        } catch (error) {
            console.error('Basic chart render error:', error);
            throw error;
        }
    }
};
