import React, { useEffect, useRef, useState } from 'react';
import * as d3 from 'd3';
import { useTheme } from '../context/ThemeContext';
import { Spin } from 'antd';

interface D3RendererProps {
   spec: string;
   width?: number;
   height?: number;
}

interface BarData {
    label: string;
    value: number;
    group?: string;
}

interface LineData {
    date: string;
    value: number;
}

interface ScatterData {
    x: number;
    y: number;
    size?: number;
    color?: string;
    label?: string;
}

interface FunctionData {
    fn: string;
    domain: [number, number];
    samples?: number;
    label?: string;
}

interface SeriesData {
    name: string;
    values: LineData[];
    pattern?: 'solid' | 'dashed' | 'dotted';
    axis?: string;
    color?: string;
}

interface MultiAxisData {
    x: number[];
    series: {
        name: string;
        values: number[];
        axis: string;
        color: string;
    }[];
}

interface TimeSeriesData {
    series: {
        name: string;
        values: { date: string; value: number }[];
        axis?: string;
    }[];
}

type ChartType = 'bar' | 'line' | 'scatter' | 'function' | 'multiaxis' | 'bubble' | 'timeseries';

interface D3Spec {
   type: ChartType;
   data: BarData[] | LineData[] | ScatterData[] | FunctionData[] | MultiAxisData | TimeSeriesData;
   options?: {
       xDomain?: [number, number];
       yDomain?: [number, number];
       grid?: boolean;
       points?: boolean;
       multiScale?: boolean;
       step?: boolean;
       valueLabels?: boolean;
       smooth?: boolean;
       interactive?: boolean;
       animation?: boolean | {
           duration?: number;
           sequential?: boolean;
       };
       grouped?: boolean;
       cumulative?: boolean;
       tooltip?: boolean;
       legend?: boolean;
       axes?: {
           [key: string]: {
               label?: string;
               domain?: [number, number];
               scale?: 'linear' | 'log';
           };
       };
       yAxis?: {
           label?: string;
       };
       xAxis?: {
           label?: string;
       };
   };
   title?: string;
   xAxis?: {
       label?: string;
       format?: string;
   };
   yAxis?: {
       label?: string;
       format?: string;
   };
}

const ZOOM_CONFIG = {
    MIN_SCALE: 0.2,    // Maximum zoom out (5x smaller)
    MAX_SCALE: 5,      // Maximum zoom in (5x larger)
    ZOOM_SPEED: 0.5,   // Reduce zoom speed
    EXTENT_PADDING: 50 // Padding around the plot area in pixels
};

const factorial = (n: number): number => {
    if (n <= 1) return 1;
    return n * factorial(n - 1);
};

export const D3Renderer: React.FC<D3RendererProps> = ({ spec, width = 600, height = 400 }) => {
    const svgRef = useRef<SVGSVGElement>(null);
    const { isDarkMode } = useTheme();
    const [error, setError] = useState<string | null>(null);
    const [isLoading, setIsLoading] = useState(true);

    const lightTheme = {
        background: '#ffffff',
        text: '#000000',
        axis: '#666666',
        gridMajor: '#e0e0e0',
        gridMinor: '#f0f0f0',
        highlight: '#1890ff'
    };

    const darkTheme = {
        background: 'transparent',
        text: '#ffffff',
        axis: '#888888',
        gridMajor: '#303030',
        gridMinor: '#252525',
        highlight: '#177ddc'
    };

    const theme = isDarkMode ? darkTheme : lightTheme;

    const evaluateExpression = (expr: string, x: number): number => {
        try {
            const context = {
                x,
                Math,
                pow: Math.pow,
                sin: Math.sin,
                cos: Math.cos,
                exp: Math.exp,
                abs: Math.abs,
                PI: Math.PI,
                factorial
            };

            const processedExpr = expr
                .replace(/(\d+|\w+(?:\.\w+)*)\s*\*\*\s*(\d+|\w+(?:\.\w+)*)/g, 
                    (_, base, exp) => `pow(${base}, ${exp})`);

            const fn = new Function(...Object.keys(context), `return ${processedExpr}`);
            return fn(...Object.values(context));
        } catch (e) {
            console.error('Error evaluating expression:', e);
            return NaN;
        }
    };

    const generateFunctionPoints = (
        fnString: string,
        domain: [number, number],
        samples: number = 200
    ): ScatterData[] => {
        const points: ScatterData[] = [];
        const [min, max] = domain;
        const step = (max - min) / (samples - 1);

        for (let i = 0; i < samples; i++) {
            const x = min + (step * i);
            const y = evaluateExpression(fnString, x);
            if (!isNaN(y) && isFinite(y)) {
                points.push({ x, y });
            }
        }

        return points;
    };

useEffect(() => {
        const renderVisualization = async () => {
            if (!svgRef.current) return;

            try {
                setIsLoading(true);
                setError(null);

		// Debug logging
                console.debug('Rendering D3 visualization with spec:', {
                    rawSpec: spec,
                    parsedSpec: typeof spec === 'string' ? JSON.parse(spec) : spec
                });

                const vizSpec: D3Spec = typeof spec === 'string' ? JSON.parse(spec) : spec;

		if (!vizSpec?.data) {
                    throw new Error('Invalid or missing data in specification');
                }

		// Clear existing content
                d3.select(svgRef.current).selectAll('*').remove();

                const margin = { top: 50, right: 40, bottom: 60, left: 60 };
                const innerWidth = width - margin.left - margin.right;
                const innerHeight = height - margin.top - margin.bottom;

                const svg = d3.select(svgRef.current)
                    .attr('width', width)
                    .attr('height', height);

                const g = svg.append('g')
                    .attr('transform', `translate(${margin.left},${margin.top})`);

                if (vizSpec.title) {
                    svg.append('text')
                        .attr('x', width / 2)
                        .attr('y', 25)
                        .attr('text-anchor', 'middle')
                        .style('font-size', '16px')
                        .style('fill', theme.text)
                        .text(vizSpec.title);
                }

                // Add zoom behavior with limits
                if (vizSpec.options?.interactive !== false) {
                    const zoom = d3.zoom<SVGSVGElement, unknown>()
                        .scaleExtent([ZOOM_CONFIG.MIN_SCALE, ZOOM_CONFIG.MAX_SCALE])
                        .extent([[0, 0], [width, height]])
                        .translateExtent([
                            [-ZOOM_CONFIG.EXTENT_PADDING, -ZOOM_CONFIG.EXTENT_PADDING],
                            [width + ZOOM_CONFIG.EXTENT_PADDING, height + ZOOM_CONFIG.EXTENT_PADDING]
                        ])
                        .on('zoom', (event) => {
                            g.attr('transform', event.transform);
                        });

                    svg.call(zoom)
                        .on('wheel.zoom', (event) => {
                            if (!event.ctrlKey && !event.metaKey) return;
                            event.preventDefault();
                            const delta = event.deltaY * ZOOM_CONFIG.ZOOM_SPEED;
                            zoom.scaleBy(svg, Math.pow(0.995, delta));
                        });
                }

                switch (vizSpec.type) {
                    case 'bar':
                        await renderBarChart(g, vizSpec, innerWidth, innerHeight, theme);
                        break;
                    case 'line':
                        await renderLineChart(g, vizSpec, innerWidth, innerHeight, theme);
                        break;
                    case 'scatter':
                    case 'bubble':
                        await renderScatterPlot(g, vizSpec, innerWidth, innerHeight, theme);
                        break;
                    case 'function':
                        await renderFunctionPlot(g, vizSpec, innerWidth, innerHeight, theme);
                        break;
                    case 'multiaxis':
                        await renderMultiAxisChart(g, vizSpec, innerWidth, innerHeight, theme);
                        break;
                    case 'timeseries':
                        await renderTimeSeriesChart(g, vizSpec, innerWidth, innerHeight, theme);
                        break;
                    default:
                        throw new Error(`Unsupported visualization type: ${vizSpec.type}`);
                }

            } catch (err) {
                setError(err instanceof Error ? err.message : 'Error rendering visualization');
                console.error('D3 rendering error:', err);
            } finally {
                setIsLoading(false);
            }
        };

        renderVisualization();
    }, [spec, width, height, theme]);

    const createTooltip = () => {
        return d3.select('body').append('div')
            .attr('class', 'd3-tooltip')
            .style('position', 'absolute')
            .style('visibility', 'hidden')
            .style('background-color', theme.background)
            .style('border', `1px solid ${theme.axis}`)
            .style('padding', '5px')
            .style('border-radius', '3px')
            .style('color', theme.text)
            .style('pointer-events', 'none')
            .style('z-index', '1000');
    };

    const renderBarChart = async (
        g: d3.Selection<SVGGElement, unknown, null, undefined>,
        vizSpec: D3Spec,
        width: number,
        height: number,
        theme: any
    ) => {

        const data = vizSpec.data as BarData[];
        const grouped = vizSpec.options?.grouped;

	// Validate data structure
        if (!data || !Array.isArray(data) || !data.every(d => d.label && typeof d.value === 'number')) {
            throw new Error('Invalid bar chart data structure');
        }

        let x: d3.ScaleBand<string>;
        let groupX: d3.ScaleBand<string> | null = null;

        if (grouped) {
            const groups = Array.from(new Set(data.map(d => d.group || '')));
            const labels = Array.from(new Set(data.map(d => d.label)));

            x = d3.scaleBand()
                .domain(groups)
                .range([0, width])
                .padding(0.1);

            groupX = d3.scaleBand()
                .domain(labels)
                .range([0, x.bandwidth()])
                .padding(0.05);
        } else {
            x = d3.scaleBand()
                .domain(data.map(d => d.label))
                .range([0, width])
                .padding(0.1);
        }

        const y = d3.scaleLinear()
            .domain([0, d3.max(data, d => d.value) || 0])
            .range([height, 0]);

        // Add grid
        if (vizSpec.options?.grid !== false) {
            g.append('g')
                .attr('class', 'grid')
                .attr('opacity', 0.1)
                .call(g => {
                    d3.axisLeft(y)
                        .tickSize(-width)
                        .tickFormat(() => '')(g);
                });
        }

        const tooltip = createTooltip();

        // Add bars
        if (grouped) {
            const groups = Array.from(new Set(data.map(d => d.group || '')));
            const colorScale = d3.scaleOrdinal(d3.schemeCategory10)
                .domain(groups);

            groups.forEach((group, i) => {
                const groupData = data.filter(d => d.group === group);
                g.selectAll(`.bar-${i}`)
                    .data(groupData)
                    .enter()
                    .append('rect')
                    .attr('class', `bar-${i}`)
                    .attr('x', d => (x(d.group || '') || 0) + (groupX!(d.label) || 0))
                    .attr('y', d => y(d.value))
                    .attr('width', groupX!.bandwidth())
                    .attr('height', d => height - y(d.value))
                    .attr('fill', colorScale(group))
                    .on('mouseover', (event, d) => {
                        const color = d3.color(colorScale(group))?.brighter(0.5)?.toString() || theme.highlight;
                        d3.select(event.currentTarget)
                            .transition()
                            .duration(200)
                            .attr('fill', color);
                        
                        tooltip.html(`${d.label} (${group}): ${d.value}`)
                            .style('visibility', 'visible');
                    })
                    .on('mousemove', (event) => {
                        tooltip.style('top', (event.pageY - 10) + 'px')
                            .style('left', (event.pageX + 10) + 'px');
                    })
                    .on('mouseout', (event) => {
                        d3.select(event.currentTarget)
                            .transition()
                            .duration(200)
                            .attr('fill', colorScale(group));
                        tooltip.style('visibility', 'hidden');
                    });
            });
        } else {
            g.selectAll('rect')
                .data(data)
                .enter()
                .append('rect')
                .attr('x', d => x(d.label) || 0)
                .attr('y', d => y(d.value))
                .attr('width', x.bandwidth())
                .attr('height', d => height - y(d.value))
                .attr('fill', theme.highlight)
                .on('mouseover', (event, d) => {
                    const color = d3.color(theme.highlight)?.brighter(0.5)?.toString() || theme.highlight;
                    d3.select(event.currentTarget)
                        .transition()
                        .duration(200)
                        .attr('fill', color);
                    
                    tooltip.html(`${d.label}: ${d.value}`)
                        .style('visibility', 'visible');
                })
                .on('mousemove', (event) => {
                    tooltip.style('top', (event.pageY - 10) + 'px')
                        .style('left', (event.pageX + 10) + 'px');
                })
                .on('mouseout', (event) => {
                    d3.select(event.currentTarget)
                        .transition()
                        .duration(200)
                        .attr('fill', theme.highlight);
                    tooltip.style('visibility', 'hidden');
                });
        }
       // Add axes
        g.append('g')
            .attr('transform', `translate(0,${height})`)
            .call(d3.axisBottom(x))
            .selectAll('text')
            .attr('fill', theme.text);

        g.append('g')
            .call(d3.axisLeft(y))
            .selectAll('text')
            .attr('fill', theme.text);

        // Add axis labels if provided
        if (vizSpec.xAxis?.label) {
            g.append('text')
                .attr('x', width / 2)
                .attr('y', height + 40)
                .attr('text-anchor', 'middle')
                .style('fill', theme.text)
                .text(vizSpec.xAxis.label);
        }

        if (vizSpec.yAxis?.label) {
            g.append('text')
                .attr('transform', 'rotate(-90)')
                .attr('x', -height / 2)
                .attr('y', -40)
                .attr('text-anchor', 'middle')
                .style('fill', theme.text)
                .text(vizSpec.yAxis.label);
        }
    };

    const renderLineChart = async (
        g: d3.Selection<SVGGElement, unknown, null, undefined>,
        vizSpec: D3Spec,
        width: number,
        height: number,
        theme: any
    ) => {
	// Validate data structure for line chart
	const isMultiSeriesData = (data: any): data is { series: SeriesData[] } => {
                return data && 
                       typeof data === 'object' && 
                       'series' in data && 
                       Array.isArray(data.series);
        };

	const isSingleSeriesData = (data: any): data is LineData[] => {
	    if (!Array.isArray(data)) return false;

	    // Now TypeScript knows data is an array
	    return data.length > 0 &&
		   data.every(d => typeof d === 'object' && d !== null &&
                'date' in d && 'value' in d &&
                typeof d.value === 'number'
            );
        };

	// Handle both single and multi-series data
        let allSeries: SeriesData[] = [];
        if (isMultiSeriesData(vizSpec.data)) {
            allSeries = vizSpec.data.series;
        } else if (isSingleSeriesData(vizSpec.data)) {
            allSeries = [{
                name: 'Value',
                values: vizSpec.data,
                pattern: 'solid'
            }];
        } else {
            console.error('Invalid data structure:', {
		dataType: typeof vizSpec.data,
		isArray: Array.isArray(vizSpec.data),
		sample: vizSpec.data
	    });
	    throw new Error('Invalid line chart data structure. Expected either an array of data points or a multi-series object.');
        }

        console.debug('Line chart series:', {
                    seriesCount: allSeries.length,
                    sampleSeries: allSeries[0]?.name,
                    multiScale: vizSpec.options?.multiScale
        });

        const data = vizSpec.data as LineData[];

        // Parse dates properly from YYYY-M format
        const parseDate = (dateStr: string) => {
            if (!dateStr) return new Date();
            const [year, month] = dateStr.split('-').map(Number);
            return new Date(year, month - 1);
        };

	// Get all values to determine domains
        const allDates = allSeries.flatMap(s => s.values.map(d => parseDate(d.date)));
        const allValues = allSeries.flatMap(s => s.values.map(d => d.value));

        const x = d3.scaleTime()
            .domain(d3.extent(allDates) as [Date, Date])
	    .range([0, width]);

	// Create scales for each series if multiScale is enabled
	const yScales = new Map<string, d3.ScaleLinear<number, number>>();
	if (vizSpec.options?.multiScale) {
	    allSeries.forEach((series, i) => {
		const values = series.values.map(d => d.value);
		yScales.set(series.name, d3.scaleLinear()
		    .domain([0, d3.max(values) || 0])
		    .range([height, 0])
		    .nice());
	    });
	} else {
	    const y = d3.scaleLinear()
		.domain([0, d3.max(allValues) || 0])
		.range([height, 0])
		.nice();
	    allSeries.forEach(series => yScales.set(series.name, y));
	}

        // Add grid using the first y-scale
        if (vizSpec.options?.grid !== false) {
            const primaryScale = yScales.values().next().value;
            g.append('g')
                .attr('class', 'grid')
                .attr('opacity', 0.1)
                .call(g => {
                    d3.axisLeft(primaryScale)
                        .tickSize(-width)
                        .tickFormat(() => '')(g);
                });
        }

	// Create color scale for multiple series
	const colorScale = d3.scaleOrdinal(d3.schemeCategory10)
	    .domain(allSeries.map(s => s.name));

	// Create line generators for each series
	const createLine = (series: SeriesData) => {
	    const line = d3.line<LineData>()
		.x(d => x(parseDate(d.date)))
		.y(d => yScales.get(series.name)!(d.value));

	    if (vizSpec.options?.step) {
		line.curve(d3.curveStepAfter);
	    } else if (vizSpec.options?.smooth) {
		line.curve(d3.curveMonotoneX);
	    } else {
		// Default to linear interpolation
		line.curve(d3.curveLinear);
	    }

	    return line;
	};

	// Configure x-axis with proper date formatting
        const xAxis = d3.axisBottom(x)
               .ticks(data.length)
               .tickFormat(d => d3.timeFormat('%Y-%m')(d as Date));

        const tooltip = createTooltip();

	// Draw lines for each series
	allSeries.forEach((series, i) => {
	    const color = series.color || colorScale(series.name);
	    const line = createLine(series);

	    const path = g.append('path')
		.datum(series.values)
		.attr('fill', 'none')
		.attr('stroke', color)
		.attr('stroke-width', 2)
		.attr('stroke-dasharray', series.pattern === 'dashed' ? '5,5' :
					series.pattern === 'dotted' ? '2,2' : 'none')
		.attr('d', line);

	    // Add line animation
	    if (vizSpec.options?.animation !== false) {
		const totalLength = path.node()?.getTotalLength() || 0;
		path.attr('stroke-dasharray', `${totalLength} ${totalLength}`)
		    .attr('stroke-dashoffset', totalLength)
		    .transition()
		    .duration(2000)
		    .attr('stroke-dashoffset', 0);
	    }

	    // Add value labels if enabled
	    if (vizSpec.options?.valueLabels) {
		g.selectAll(`.value-label-${i}`)
		    .data(series.values)
		    .enter()
		    .append('text')
		    .attr('class', `value-label-${i}`)
		    .attr('x', d => x(parseDate(d.date)))
		    .attr('y', d => yScales.get(series.name)!(d.value) - 10)
		    .attr('text-anchor', 'middle')
		    .attr('fill', color)
		    .style('font-size', '12px')
		    .style('opacity', 0)
		    .text(d => d.value.toFixed(1))
		    .style('opacity', 1);
	    }

	    // Add points if enabled
	    if (vizSpec.options?.points) {
		g.selectAll(`.points-${i}`)
		    .data(series.values as LineData[])
		    .enter()
		    .append('circle')
		    .attr('class', `points-${i}`)
		    .attr('cx', d => x(parseDate(d.date)))
		    .attr('cy', d => yScales.get(series.name)!(d.value))
		    .attr('r', 4)
		    .attr('fill', color)
		    .attr('stroke', theme.background)
		    .attr('stroke-width', 2)
		    .on('mouseover', (event, d) => {
			const brighterColor = d3.color(color)?.brighter(0.5)?.toString() || color;
			d3.select(event.currentTarget)
			    .transition()
			    .duration(200)
			    .attr('r', 6)
			    .attr('fill', brighterColor);

			const date = d3.timeFormat('%Y-%m')(parseDate(d.date));
			tooltip.html(`${series.name}: ${d.value}<br/>${date}`)
			    .style('visibility', 'visible');
		    })
		    .on('mousemove', (event) => {
                        tooltip.style('top', (event.pageY - 10) + 'px')
                            .style('left', (event.pageX + 10) + 'px');
                    })
                    .on('mouseout', (event) => {
			d3.select(event.currentTarget)
			    .transition()
			    .duration(200)
			    .attr('r', 4)
			    .attr('fill', color);
			tooltip.style('visibility', 'hidden');
                    });
	    }
	});

	// Add legend if enabled
	if (vizSpec.options?.legend) {
	    const legend = g.append('g')
		.attr('class', 'legend')
		.attr('transform', `translate(${width + 10}, 0)`);

	    allSeries.forEach((series, i) => {
		const legendItem = legend.append('g')
		    .attr('transform', `translate(0, ${i * 20})`);

		legendItem.append('line')
		    .attr('x1', 0)
		    .attr('x2', 20)
		    .attr('y1', 10)
		    .attr('y2', 10)
		    .attr('stroke', series.color || colorScale(series.name))
		    .attr('stroke-width', 2)
		    .attr('stroke-dasharray', series.pattern === 'dashed' ? '5,5' :
					    series.pattern === 'dotted' ? '2,2' : 'none');

		legendItem.append('text')
		    .attr('x', 25)
		    .attr('y', 10)
		    .attr('dy', '0.35em')
		    .style('fill', theme.text)
		    .text(series.name);
	    });
	}

        // Add axes
        g.append('g')
            .attr('transform', `translate(0,${height})`)
	    .call(xAxis)
            .selectAll('text')
            .attr('fill', theme.text);

	// Add y-axes
	if (vizSpec.options?.multiScale) {
	    Array.from(yScales.entries()).forEach(([name, scale], i) => {
		const axis = i === 0 ? 
		    d3.axisLeft(scale) : 
		    d3.axisRight(scale);
		    
		const axisGroup = g.append('g')
		    .call(axis);
		    
		if (i > 0) {
		    axisGroup.attr('transform', `translate(${width},0)`);
		}
		
		axisGroup.selectAll('text')
		    .attr('fill', theme.text);
	    });
	} else {
	    g.append('g')
		.call(d3.axisLeft(yScales.values().next().value))
		.selectAll('text')
		.attr('fill', theme.text);
	}

        // Add axis labels
        if (vizSpec.xAxis?.label) {
            g.append('text')
                .attr('x', width / 2)
                .attr('y', height + 40)
                .attr('text-anchor', 'middle')
                .style('fill', theme.text)
                .text(vizSpec.xAxis.label);
        }

        if (vizSpec.yAxis?.label) {
            g.append('text')
                .attr('transform', 'rotate(-90)')
                .attr('x', -height / 2)
                .attr('y', -40)
                .attr('text-anchor', 'middle')
                .style('fill', theme.text)
                .text(vizSpec.yAxis.label);
        }
    };
   const renderScatterPlot = async (
        g: d3.Selection<SVGGElement, unknown, null, undefined>,
        vizSpec: D3Spec,
        width: number,
        height: number,
        theme: any
    ) => {
        const data = vizSpec.data as ScatterData[];
        
        const x = d3.scaleLinear()
            .domain(vizSpec.options?.xDomain || [
                d3.min(data, d => d.x) || 0,
                d3.max(data, d => d.x) || 0
            ])
            .range([0, width]);

        const y = d3.scaleLinear()
            .domain(vizSpec.options?.yDomain || [
                d3.min(data, d => d.y) || 0,
                d3.max(data, d => d.y) || 0
            ])
            .range([height, 0]);

        // Add grid
        if (vizSpec.options?.grid !== false) {
            g.append('g')
                .attr('class', 'grid')
                .attr('opacity', 0.1)
                .call(g => {
                    d3.axisLeft(y)
                        .tickSize(-width)
                        .tickFormat(() => '')(g);
                });
        }

        const tooltip = createTooltip();

        // Create size scale if bubble chart
        const sizeScale = d3.scaleLinear()
            .domain([
                d3.min(data, d => d.size || 5) || 5,
                d3.max(data, d => d.size || 5) || 5
            ])
            .range([5, 20]);

        // Create color scale if colors provided
        const colorScale = d3.scaleOrdinal(d3.schemeCategory10)
            .domain(data.map(d => d.color || theme.highlight));

        // Add dots
        g.selectAll('circle')
            .data(data)
            .enter()
            .append('circle')
            .attr('cx', d => x(d.x))
            .attr('cy', d => y(d.y))
            .attr('r', d => sizeScale(d.size || 5))
            .attr('fill', d => d.color || theme.highlight)
            .attr('opacity', 0.7)
            .on('mouseover', (event, d) => {
                const color = d3.color(d.color || theme.highlight)?.brighter(0.5)?.toString() || theme.highlight;
                d3.select(event.currentTarget)
                    .transition()
                    .duration(200)
                    .attr('r', sizeScale(d.size || 5) * 1.5)
                    .attr('fill', color)
                    .attr('opacity', 1);
                
                tooltip.html(`${d.label || ''} (${d.x}, ${d.y})${d.size ? ` Size: ${d.size}` : ''}`)
                    .style('visibility', 'visible');
            })
            .on('mousemove', (event) => {
                tooltip.style('top', (event.pageY - 10) + 'px')
                    .style('left', (event.pageX + 10) + 'px');
            })
            .on('mouseout', (event, d) => {
                d3.select(event.currentTarget)
                    .transition()
                    .duration(200)
                    .attr('r', sizeScale(d.size || 5))
                    .attr('fill', d.color || theme.highlight)
                    .attr('opacity', 0.7);
                tooltip.style('visibility', 'hidden');
            });

        // Add axes
        g.append('g')
            .attr('transform', `translate(0,${height})`)
            .call(d3.axisBottom(x))
            .selectAll('text')
            .attr('fill', theme.text);

        g.append('g')
            .call(d3.axisLeft(y))
            .selectAll('text')
            .attr('fill', theme.text);

        // Add axis labels
        if (vizSpec.xAxis?.label) {
            g.append('text')
                .attr('x', width / 2)
                .attr('y', height + 40)
                .attr('text-anchor', 'middle')
                .style('fill', theme.text)
                .text(vizSpec.xAxis.label);
        }

        if (vizSpec.yAxis?.label) {
            g.append('text')
                .attr('transform', 'rotate(-90)')
                .attr('x', -height / 2)
                .attr('y', -40)
                .attr('text-anchor', 'middle')
                .style('fill', theme.text)
                .text(vizSpec.yAxis.label);
        }
    };

    const renderFunctionPlot = async (
        g: d3.Selection<SVGGElement, unknown, null, undefined>,
        vizSpec: D3Spec,
        width: number,
        height: number,
        theme: any
    ) => {
        const functions = vizSpec.data as FunctionData[];
        const functionArray = Array.isArray(functions) ? functions : [functions];
        
        // Generate points for all functions to determine domain
        const allPoints: ScatterData[] = [];
        functionArray.forEach(fn => {
            const points = generateFunctionPoints(
                fn.fn,
                fn.domain,
                fn.samples || 200
            );
            allPoints.push(...points);
        });

        const x = d3.scaleLinear()
            .domain(vizSpec.options?.xDomain || [
                d3.min(allPoints, d => d.x) || -10,
                d3.max(allPoints, d => d.x) || 10
            ])
            .range([0, width]);

        const y = d3.scaleLinear()
            .domain(vizSpec.options?.yDomain || [
                d3.min(allPoints, d => d.y) || -10,
                d3.max(allPoints, d => d.y) || 10
            ])
            .range([height, 0]);
      // Add grid
        if (vizSpec.options?.grid !== false) {
            g.append('g')
                .attr('class', 'grid')
                .attr('opacity', 0.1)
                .call(g => {
                    d3.axisLeft(y)
                        .tickSize(-width)
                        .tickFormat(() => '')(g);
                });
        }

        // Add x-axis line
        g.append('line')
            .attr('x1', 0)
            .attr('y1', y(0))
            .attr('x2', width)
            .attr('y2', y(0))
            .attr('stroke', theme.axis)
            .attr('stroke-width', 1);

        // Add y-axis line
        g.append('line')
            .attr('x1', x(0))
            .attr('y1', 0)
            .attr('x2', x(0))
            .attr('y2', height)
            .attr('stroke', theme.axis)
            .attr('stroke-width', 1);

        const line = d3.line<ScatterData>()
            .x(d => x(d.x))
            .y(d => y(d.y))
            .defined(d => !isNaN(d.x) && !isNaN(d.y) && isFinite(d.x) && isFinite(d.y));

        // Create color scale for multiple functions
        const colorScale = d3.scaleOrdinal<string>()
            .domain(functionArray.map((_, i) => i.toString()))
            .range(d3.schemeCategory10);

        const tooltip = createTooltip();

        functionArray.forEach((fn, index) => {
            const points = generateFunctionPoints(fn.fn, fn.domain, fn.samples || 200);
	    // Filter out invalid points before drawing
	    const validPoints = points.filter((p) => {
                return !isNaN(p.x) && !isNaN(p.y) && isFinite(p.x) && isFinite(p.y);
            });
            const color = colorScale(index.toString());

            const path = g.append('path')
                .datum(validPoints)
                .attr('fill', 'none')
                .attr('stroke', color)
                .attr('stroke-width', 2)
                .attr('d', line);

            if (vizSpec.options?.animation !== false) {
                const totalLength = path.node()?.getTotalLength() || 0;
                path.attr('stroke-dasharray', `${totalLength} ${totalLength}`)
                    .attr('stroke-dashoffset', totalLength)
                    .transition()
                    .duration(2000)
                    .attr('stroke-dashoffset', 0);
            }

            // Add function label if provided
            if (fn.label && validPoints.length > 0) {
                const lastPoint = points[points.length - 1];
                g.append('text')
                    .attr('x', x(lastPoint.x))
                    .attr('y', y(lastPoint.y))
                    .attr('dx', '0.5em')
                    .attr('dy', '0.5em')
                    .style('fill', color)
                    .text(fn.label);
            }

            // Add hover effects for function lines
            const hoverLine = g.append('path')
                .attr('fill', 'none')
                .attr('stroke', 'transparent')
                .attr('stroke-width', 10)
                .attr('d', line(validPoints))
                .on('mouseover', () => {
                    path.attr('stroke-width', 3)
                        .attr('stroke', d3.color(color)?.brighter(0.5)?.toString() || color);
                    tooltip.html(fn.label || fn.fn)
                        .style('visibility', 'visible');
                })
                .on('mousemove', (event) => {
                    const [mouseX] = d3.pointer(event);
                    const xValue = x.invert(mouseX);
                    const yValue = evaluateExpression(fn.fn, xValue);
                    tooltip.html(`${fn.label || fn.fn}<br/>x: ${xValue.toFixed(2)}<br/>y: ${yValue.toFixed(2)}`)
                        .style('top', (event.pageY - 10) + 'px')
                        .style('left', (event.pageX + 10) + 'px');
                })
                .on('mouseout', () => {
                    path.attr('stroke-width', 2)
                        .attr('stroke', color);
                    tooltip.style('visibility', 'hidden');
                });
        });

        // Add axes
        g.append('g')
            .attr('transform', `translate(0,${height})`)
            .call(d3.axisBottom(x))
            .selectAll('text')
            .attr('fill', theme.text);

        g.append('g')
            .call(d3.axisLeft(y))
            .selectAll('text')
            .attr('fill', theme.text);

        // Add axis labels
        if (vizSpec.xAxis?.label) {
            g.append('text')
                .attr('x', width / 2)
                .attr('y', height + 40)
                .attr('text-anchor', 'middle')
                .style('fill', theme.text)
                .text(vizSpec.xAxis.label);
        }

        if (vizSpec.yAxis?.label) {
            g.append('text')
                .attr('transform', 'rotate(-90)')
                .attr('x', -height / 2)
                .attr('y', -40)
                .attr('text-anchor', 'middle')
                .style('fill', theme.text)
                .text(vizSpec.yAxis.label);
        }
    };

    const renderMultiAxisChart = async (
        g: d3.Selection<SVGGElement, unknown, null, undefined>,
        vizSpec: D3Spec,
        width: number,
        height: number,
        theme: any
    ) => {
        const data = vizSpec.data as MultiAxisData;
        const axes = vizSpec.options?.axes || {};

	// Validate data structure
        if (!data.x || !Array.isArray(data.x) || !data.series || !Array.isArray(data.series)) {
            throw new Error('Invalid multi-axis data structure');
        }

        // Create scales for each axis
        const x = d3.scaleLinear()
            .domain([0, data.x.length - 1])
            .range([0, width]);

        const yScales = new Map<string, d3.ScaleLinear<number, number>>();
        data.series.forEach(series => {
            const axisConfig = axes[series.axis] || {};
            yScales.set(series.axis, d3.scaleLinear()
                .domain(axisConfig.domain || [0, d3.max(series.values) || 0])
                .range([height, 0]));
        });
        // Add grid for primary axis
        if (vizSpec.options?.grid !== false) {
            const primaryScale = yScales.get(data.series[0].axis);
            if (primaryScale) {
                g.append('g')
                    .attr('class', 'grid')
                    .attr('opacity', 0.1)
                    .call(g => {
                        d3.axisLeft(primaryScale)
                            .tickSize(-width)
                            .tickFormat(() => '')(g);
                    });
            }
        }

        const line = d3.line<number>()
            .x((_, i) => x(i))
            .y(d => d);

        const tooltip = createTooltip();

        // Draw lines for each series
        data.series.forEach((series, index) => {
            const yScale = yScales.get(series.axis);
            if (!yScale) return;

            const scaledValues = series.values.map(v => yScale(v));
            
            const path = g.append('path')
                .datum(scaledValues)
                .attr('fill', 'none')
                .attr('stroke', series.color)
                .attr('stroke-width', 2)
                .attr('d', line);

            // Add animation if enabled
            if (vizSpec.options?.animation !== false) {
                const totalLength = path.node()?.getTotalLength() || 0;
                path.attr('stroke-dasharray', `${totalLength} ${totalLength}`)
                    .attr('stroke-dashoffset', totalLength)
                    .transition()
                    .duration(2000)
                    .attr('stroke-dashoffset', 0);
            }

            // Add dots with hover effects
            g.selectAll(`.dots-${index}`)
                .data(series.values)
                .enter()
                .append('circle')
                .attr('class', `dots-${index}`)
                .attr('cx', (_, i) => x(i))
                .attr('cy', d => yScale(d))
                .attr('r', 4)
                .attr('fill', series.color)
                .on('mouseover', (event, d) => {
                    const color = d3.color(series.color)?.brighter(0.5)?.toString() || series.color;
                    d3.select(event.currentTarget)
                        .transition()
                        .duration(200)
                        .attr('r', 6)
                        .attr('fill', color);
                    
                    tooltip.html(`${series.name}: ${d}`)
                        .style('visibility', 'visible');
                })
                .on('mousemove', (event) => {
                    tooltip.style('top', (event.pageY - 10) + 'px')
                        .style('left', (event.pageX + 10) + 'px');
                })
                .on('mouseout', (event) => {
                    d3.select(event.currentTarget)
                        .transition()
                        .duration(200)
                        .attr('r', 4)
                        .attr('fill', series.color);
                    tooltip.style('visibility', 'hidden');
                });

            // Add axis for this series
            let axisElement;
            if (index === 0) {
                axisElement = g.append('g').call(d3.axisLeft(yScale));
            } else {
                axisElement = g.append('g').call(d3.axisRight(yScale));
            }

            // Position secondary axes on the right
            if (index > 0) {
                axisElement.attr('transform', `translate(${width},0)`);
            }

            // Style axis text
            axisElement.selectAll('text')
                .attr('fill', series.color);

            // Add axis label if provided
            const axisConfig = axes[series.axis];
            if (axisConfig?.label) {
                g.append('text')
                    .attr('transform', `rotate(-90)`)
                    .attr('x', -height / 2)
                    .attr('y', index === 0 ? -40 : width + 40)
                    .attr('text-anchor', 'middle')
                    .style('fill', series.color)
                    .text(axisConfig.label);
            }
        });

        // Add x-axis
        g.append('g')
            .attr('transform', `translate(0,${height})`)
            .call(d3.axisBottom(x))
            .selectAll('text')
            .attr('fill', theme.text);

        // Add legend
        const legend = g.append('g')
            .attr('class', 'legend')
            .attr('transform', `translate(${width + 60}, 0)`);

        data.series.forEach((series, i) => {
            const legendItem = legend.append('g')
                .attr('transform', `translate(0, ${i * 20})`);

            legendItem.append('line')
                .attr('x1', 0)
                .attr('x2', 20)
                .attr('y1', 10)
                .attr('y2', 10)
                .attr('stroke', series.color)
                .attr('stroke-width', 2);

            legendItem.append('text')
                .attr('x', 25)
                .attr('y', 10)
                .attr('dy', '0.35em')
                .style('fill', series.color)
                .text(series.name);
        });
    };

    const renderTimeSeriesChart = async (
        g: d3.Selection<SVGGElement, unknown, null, undefined>,
        vizSpec: D3Spec,
        width: number,
        height: number,
        theme: any
    ) => {
        const data = vizSpec.data as TimeSeriesData;

        // Find overall date range
        const allDates = data.series.flatMap(s => s.values.map(v => new Date(v.date)));
        const xDomain = [d3.min(allDates) || new Date(), d3.max(allDates) || new Date()];

        const x = d3.scaleTime()
            .domain(xDomain)
            .range([0, width]);

        // Create scales for each axis
        const yScales = new Map<string, d3.ScaleLinear<number, number>>();
        data.series.forEach(series => {
            const axisKey = series.axis || 'default';
            if (!yScales.has(axisKey)) {
                const values = series.values.map(v => v.value);
                yScales.set(axisKey, d3.scaleLinear()
                    .domain([0, d3.max(values) || 0])
                    .range([height, 0]));
            }
        });

        // Add grid
        if (vizSpec.options?.grid !== false) {
            const primaryScale = yScales.get('default') || yScales.values().next().value;
            g.append('g')
                .attr('class', 'grid')
                .attr('opacity', 0.1)
                .call(g => {
                    d3.axisLeft(primaryScale)
                        .tickSize(-width)
                        .tickFormat(() => '')(g);
                });
        }

        const tooltip = createTooltip();
        const colorScale = d3.scaleOrdinal(d3.schemeCategory10);

        // Draw lines for each series
        data.series.forEach((series, index) => {
            const yScale = yScales.get(series.axis || 'default');
            if (!yScale) return;

            const line = d3.line<{date: string; value: number}>()
                .x(d => x(new Date(d.date)))
                .y(d => yScale(d.value));

            const color = colorScale(index.toString());

            const path = g.append('path')
                .datum(series.values)
                .attr('fill', 'none')
                .attr('stroke', color)
                .attr('d', line);

            // Add animation
            if (vizSpec.options?.animation !== false) {
                const totalLength = path.node()?.getTotalLength() || 0;
                path.attr('stroke-dasharray', `${totalLength} ${totalLength}`)
                    .attr('stroke-dashoffset', totalLength)
                    .transition()
                    .duration(2000)
                    .attr('stroke-dashoffset', 0);
            }

            // Add dots with hover effects
            g.selectAll(`.dots-${index}`)
                .data(series.values)
                .enter()
                .append('circle')
                .attr('class', `dots-${index}`)
                .attr('cx', d => x(new Date(d.date)))
                .attr('cy', d => yScale(d.value))
                .attr('r', 4)
                .attr('fill', color)
                .on('mouseover', (event, d) => {
                    const brighterColor = d3.color(color)?.brighter(0.5)?.toString() || color;
                    d3.select(event.currentTarget)
                        .transition()
                        .duration(200)
                        .attr('r', 6)
                        .attr('fill', brighterColor);
                    
                    const date = new Date(d.date).toLocaleDateString();
                    tooltip.html(`${series.name}<br/>${date}: ${d.value}`)
                        .style('visibility', 'visible');
                })
                .on('mousemove', (event) => {
                    tooltip.style('top', (event.pageY - 10) + 'px')
                        .style('left', (event.pageX + 10) + 'px');
                })
                .on('mouseout', (event) => {
                    d3.select(event.currentTarget)
                        .transition()
                        .duration(200)
                        .attr('r', 4)
                        .attr('fill', color);
                    tooltip.style('visibility', 'hidden');
                });
        });

        // Add axes
        g.append('g')
            .attr('transform', `translate(0,${height})`)
            .call(d3.axisBottom(x))
            .selectAll('text')
            .attr('fill', theme.text);

        // Add primary y-axis
        const primaryScale = yScales.get('default') || yScales.values().next().value;
        g.append('g')
            .call(d3.axisLeft(primaryScale))
            .selectAll('text')
            .attr('fill', theme.text);

        // Add secondary y-axes if needed
        let secondaryAxisOffset = width + 40;
        yScales.forEach((scale, key) => {
            if (key !== 'default') {
                g.append('g')
                    .attr('transform', `translate(${secondaryAxisOffset},0)`)
                    .call(d3.axisRight(scale))
                    .selectAll('text')
                    .attr('fill', theme.text);
                secondaryAxisOffset += 40;
            }
        });

        // Add axis labels
        if (vizSpec.xAxis?.label) {
            g.append('text')
                .attr('x', width / 2)
                .attr('y', height + 40)
                .attr('text-anchor', 'middle')
                .style('fill', theme.text)
                .text(vizSpec.xAxis.label);
        }

        if (vizSpec.yAxis?.label) {
            g.append('text')
                .attr('transform', 'rotate(-90)')
                .attr('x', -height / 2)
                .attr('y', -40)
                .attr('text-anchor', 'middle')
                .style('fill', theme.text)
                .text(vizSpec.yAxis.label);
        }
    };

    if (error) {
        return (
            <div className="d3-error" style={{
                padding: '1em',
                margin: '1em 0',
                backgroundColor: isDarkMode ? '#2a1f1f' : '#fff1f0',
                border: `1px solid ${isDarkMode ? '#a61d24' : '#ffa39e'}`,
                borderRadius: '6px'
            }}>
                <p>Error rendering D3 visualization: {error}</p>
                <pre><code>{spec}</code></pre>
            </div>
        );
    }

    return (
        <div className="d3-container" style={{ position: 'relative' }}>
            {isLoading && (
                <div style={{ 
                    position: 'absolute', 
                    top: '50%', 
                    left: '50%', 
                    transform: 'translate(-50%, -50%)',
                    zIndex: 1000
                }}>
                    <Spin tip="Rendering visualization..." />
                </div>
            )}
            <svg
                ref={svgRef}
                style={{ width: '100%', height: 'auto', maxWidth: width }}
            />
        </div>
    );
};

export default D3Renderer;
