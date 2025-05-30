import { select, Selection } from 'd3';
import { ChartOptions } from '../types';

export class D3Chart {
  private svg: Selection<SVGSVGElement, unknown, HTMLElement, any>;
  private container: HTMLElement;
  private options: ChartOptions;

  constructor(container: HTMLElement, options: ChartOptions) {
    this.container = container;
    this.options = options;
    this.initialize();
  }

  private initialize(): void {
    // Create the SVG element
    this.svg = select(this.container)
      .append('svg')
      .attr('width', this.options.width)
      .attr('height', this.options.height)
      .attr('viewBox', [0, 0, this.options.width, this.options.height])
      .style('overflow', 'visible');
    
    // Add a background
    this.svg.append('rect')
      .attr('width', '100%')
      .attr('height', '100%')
      .attr('fill', this.options.backgroundColor || '#f9f9f9');
  }

  public update(data: any[]): void {
    // Update the chart with new data
    const circles = this.svg.selectAll('circle')
      .data(data);
    
    // Enter new elements
    circles.enter()
      .append('circle')
      .attr('r', 5)
      .attr('cx', d => d.x)
      .attr('cy', d => d.y)
      .attr('fill', this.options.pointColor || 'steelblue');
    
    // Update existing elements
    circles
      .attr('cx', d => d.x)
      .attr('cy', d => d.y);
    
    // Remove old elements
    circles.exit().remove();
  }

  public resize(width: number, height: number): void {
    // Update the SVG dimensions
    this.svg
      .attr('width', width)
      .attr('height', height)
      .attr('viewBox', [0, 0, width, height]);
    
    this.options.width = width;
    this.options.height = height;
  }
}
