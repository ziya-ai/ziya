import { D3RenderPlugin } from '../../types/d3';
import {
    resolveChartColors,
    ensureReadableFill,
    planBandLabels,
    truncateLabel,
    type BandLabelPlan,
} from './chartTheme';

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

// ── schema recovery helpers (D-013) ──────────────────────────────────────────

/**
 * Unwrap a data payload that a model commonly nests one level:
 * `{data:{values:[...]}}`, `{data:{data:[...]}}`, `{rows:[...]}`, `{items:[...]}`.
 * Returns [] for anything that is not (or does not contain) an array so callers
 * can treat "no rows" uniformly instead of NaN-ing out.
 */
export function unwrapChartData(raw: any): any[] {
    if (Array.isArray(raw)) return raw;
    if (raw && typeof raw === 'object') {
        for (const k of ['values', 'data', 'rows', 'items']) {
            if (Array.isArray(raw[k])) return raw[k];
        }
    }
    return [];
}

/**
 * Map foreign field spellings onto the canonical {label, value} a band
 * (categorical) chart consumes: name/category/key/x -> label, y/count/amount/val
 * -> value. Existing {label, value} rows pass through unchanged (label stays a
 * string, value stays a number). Other fields (e.g. `color`) are preserved.
 */
export function aliasBandRow(d: any): any {
    if (d == null || typeof d !== 'object') return d;
    const label = d.label ?? d.name ?? d.category ?? d.key ?? d.x ?? '';
    const rawValue = d.value ?? d.y ?? d.count ?? d.amount ?? d.val;
    return { ...d, label: String(label), value: typeof rawValue === 'number' ? rawValue : Number(rawValue) };
}

/**
 * Keys that mark a spec as belonging to another engine (vega/vega-lite, force,
 * chord, network, music, ...). basic-chart is the highest-priority plugin
 * (tried first), so the typeless-recovery branch of canHandle MUST decline any
 * spec carrying one of these, or it would hijack another engine's render.
 */
const FOREIGN_SPEC_KEYS = ['mark', 'encoding', '$schema', 'nodes', 'links', 'edges', 'matrix', 'notes', 'traces', 'elements', 'cells', 'layout'];

/** True when `rows` look like category/value pairs (in any supported dialect). */
function looksLikeCategoryValueRows(rows: any[]): boolean {
    if (!rows.length) return false;
    const d0 = rows[0];
    if (!d0 || typeof d0 !== 'object' || Array.isArray(d0)) return false;
    const hasLabel = ['label', 'name', 'category', 'key'].some(k => d0[k] !== undefined);
    const hasValue = ['value', 'y', 'count', 'amount', 'val'].some(k => {
        const v = d0[k];
        return typeof v === 'number' || (typeof v === 'string' && v.trim() !== '' && !isNaN(Number(v)));
    });
    return hasLabel && hasValue;
}

/**
 * Radius range for continuous (scatter/bubble) markers (D-009).
 *  - plain scatter (no numeric `size` on any row) -> a fixed small radius, so
 *    sizeless points are dots rather than mutually-occluding r=40 blobs.
 *  - bubble (rows carry `size`) -> an area/count-aware max so N points share the
 *    plot without fully occluding one another, still clamped to a sane [6,40].
 */
export function radiusRange(hasSize: boolean, plotW: number, plotH: number, n: number): { min: number; max: number } {
    if (!hasSize) return { min: 5, max: 5 };
    const areaPerPoint = Math.max(1, plotW * plotH) / Math.max(1, n);
    const max = Math.max(6, Math.min(40, Math.sqrt(areaPerPoint / Math.PI) * 0.6));
    const min = Math.max(2, Math.min(6, max * 0.15));
    return { min, max };
}

/** Apply the fitting plan (thinning / rotation / truncation) to a band x-axis <g>. */
function applyBandAxis(axisG: any, labels: string[], plan: BandLabelPlan, axisColor: string) {
    // Colour the axis for the active theme (domain path + tick lines + text).
    axisG.style('color', axisColor);
    axisG.selectAll('.domain').style('stroke', axisColor);
    axisG.selectAll('.tick line').style('stroke', axisColor);

    const texts = axisG.selectAll('.tick text');
    texts.style('fill', axisColor);

    // Truncate long labels with an ellipsis (keeps the leading, most-distinctive
    // characters) and expose the full value as a <title> for hover.
    texts.text((d: any) => truncateLabel(String(d), plan.maxChars));

    if (plan.rotate) {
        texts
            .attr('transform', 'rotate(-45)')
            .attr('text-anchor', 'end')
            .attr('dx', '-0.6em')
            .attr('dy', '0.15em');
    }

    // Thin ticks: hide the label on every non-kept tick so dense categories stop
    // over-printing into a smear (the tick line itself is left in place).
    if (plan.keepEvery > 1) {
        axisG.selectAll('.tick text')
            .filter((_d: any, i: number) => i % plan.keepEvery !== 0)
            .style('display', 'none');
    }
}

export const basicChartPlugin: D3RenderPlugin = {
    name: 'basic-chart',
    priority: 10, // Higher priority than network diagram
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
    canHandle: (spec: any) => {
        if (spec === null || typeof spec !== 'object') return false;
        if (spec.type === 'bar' || spec.type === 'line' || spec.type === 'scatter' || spec.type === 'bubble') {
            return true;
        }
        // D-013: recover a typeless {data:[{label,value}]} (or aliased dialect)
        // spec, which otherwise matches no plugin and hangs to a 30s timeout.
        // Guarded so it never hijacks another engine's spec: `type` must be
        // absent, no foreign discriminator key may be present, and the rows must
        // look like category/value pairs.
        if (spec.type === undefined || spec.type === null) {
            if (FOREIGN_SPEC_KEYS.some(k => spec[k] !== undefined)) return false;
            return looksLikeCategoryValueRows(unwrapChartData(spec.data));
        }
        return false;
    },
    render: (container: HTMLElement, d3: any, spec: any, isDarkMode: boolean = false) => {
        console.debug('Basic chart plugin rendering:', spec);

        try {
            // Clear any existing content
            d3.select(container).selectAll('*').remove();

            // D-013: unwrap a nested data payload and default a typeless spec to a
            // bar chart so aliased / wrapped schemas render instead of NaN-ing out.
            const rawData: any[] = unwrapChartData(spec.data);
            const effectiveType: string =
                spec.type === 'bar' || spec.type === 'line' || spec.type === 'scatter' || spec.type === 'bubble'
                    ? spec.type
                    : 'bar';
            const style = spec.style || {};
            // Theme-derived colours (D-011): every default is resolved from the
            // active theme so axes/labels/markers are legible in both light and
            // dark; caller style.* still wins when supplied.
            const colors = resolveChartColors(isDarkMode, style);

            const isContinuous =
                effectiveType === 'bubble' ||
                (effectiveType === 'scatter' && rawData.length > 0 && rawData[0] && rawData[0].x !== undefined);

            // Band charts consume {label, value}; alias foreign field spellings
            // (name/category/x -> label, y/count -> value) before scale building
            // (D-013). Continuous charts keep their x/y/size rows untouched.
            const data: any[] = isContinuous ? rawData : rawData.map(aliasBandRow);

            const margin = { ...(spec.margin || defaultMargin) };

            // Band (categorical) charts: plan label fitting up-front so we can
            // reserve bottom margin before the plot height is fixed (D-007).
            let bandPlan: BandLabelPlan | null = null;
            if (!isContinuous) {
                const provWidth = (spec.width || 600) - margin.left - margin.right;
                bandPlan = planBandLabels(
                    data.map((d: any) => String(d?.label ?? '')),
                    provWidth,
                    colors.fontSize,
                    margin.bottom,
                );
                if (bandPlan.reservedBottom > margin.bottom) {
                    margin.bottom = bandPlan.reservedBottom;
                }
            } else {
                // Continuous charts place a label ABOVE each marker (y - r - 4);
                // reserve top headroom = largest marker radius + one label line so
                // the highest/largest bubble's label is not shaved at the SVG top
                // edge (D-010). Combined with the per-label clamp below.
                const hasLabels = data.some((d: any) => d && d.label);
                if (hasLabels) {
                    const provW = (spec.width || 600) - margin.left - margin.right;
                    const provH = (spec.height || 400) - margin.top - margin.bottom;
                    const hasSizeProv = data.some((d: any) => typeof d?.size === 'number' && isFinite(d.size));
                    const estMax = radiusRange(hasSizeProv, provW, provH, data.length).max;
                    const headroom = Math.ceil(estMax + colors.fontSize + 6);
                    if (headroom > margin.top) margin.top = headroom;
                }
            }

            const width = (spec.width || 600) - margin.left - margin.right;
            const height = (spec.height || 400) - margin.top - margin.bottom;

            // Create SVG
            const svg = d3.select(container)
                .append('svg')
                .attr('width', width + margin.left + margin.right)
                .attr('height', height + margin.top + margin.bottom)
                .append('g')
                .attr('transform', `translate(${margin.left},${margin.top})`)

            // Bubble charts use continuous x/y scales with size-mapped radii.
            // Data format: { x: number, y: number, size: number, label?: string }
            // Scatter charts with x/y data use the same continuous layout.
            if (isContinuous) {
                if (style.background) {
                    svg.append('rect')
                        .attr('x', -margin.left).attr('y', -margin.top)
                        .attr('width', width + margin.left + margin.right)
                        .attr('height', height + margin.top + margin.bottom)
                        .attr('fill', style.background);
                }

                // D-009: fixed small radius for a sizeless scatter; an area/count
                // -aware range for a bubble chart (never the old absolute [4,40]).
                const hasSize = data.some((d: any) => typeof d?.size === 'number' && isFinite(d.size));
                const { min: minRadius, max: maxRadius } = radiusRange(hasSize, width, height, data.length);
                const radiusOf = (d: any) => {
                    if (!hasSize) return minRadius;
                    return rScale(typeof d.size === 'number' ? d.size : 0);
                };

                const xExtent = d3.extent(data, (d: any) => d.x) as [number, number];
                const yExtent = d3.extent(data, (d: any) => d.y) as [number, number];
                const xSpan = (xExtent[1] - xExtent[0]) || 1;
                const ySpan = (yExtent[1] - yExtent[0]) || 1;
                // D-009: radius-aware domain padding — reserve `maxRadius` worth of
                // data-space on each edge so the largest marker at an extreme does
                // not spill past the axes.
                const xPad = xSpan * 0.1 + maxRadius * (xSpan / Math.max(1, width));
                const yPad = ySpan * 0.1 + maxRadius * (ySpan / Math.max(1, height));

                const x = d3.scaleLinear()
                    .domain([xExtent[0] - xPad, xExtent[1] + xPad])
                    .range([0, width]);
                const y = d3.scaleLinear()
                    .domain([yExtent[0] - yPad, yExtent[1] + yPad])
                    .range([height, 0]);
                const maxSize = d3.max(data, (d: any) => d.size) || 1;
                const rScale = d3.scaleSqrt().domain([0, maxSize]).range([minRadius, maxRadius]);

                svg.append('g').attr('transform', `translate(0,${height})`)
                    .call(d3.axisBottom(x))
                    .style('color', colors.axis)
                    .selectAll('text').style('fill', colors.axis);
                svg.append('g')
                    .call(d3.axisLeft(y))
                    .style('color', colors.axis)
                    .selectAll('text').style('fill', colors.axis);

                svg.selectAll('circle')
                    .data(data)
                    .join('circle')
                    .attr('cx', (d: any) => x(d.x))
                    .attr('cy', (d: any) => y(d.y))
                    .attr('r', (d: any) => radiusOf(d))
                    // Validate/clamp caller colour against the active surface (D-012).
                    .attr('fill', (d: any) => ensureReadableFill(d.color || style.pointColor, colors.bg, colors.seriesFallback))
                    .attr('opacity', 0.8)
                    .attr('stroke', colors.markerStroke)
                    .attr('stroke-width', 1);

                // D-010: clamp the label baseline so it never rises above the SVG
                // top edge. The <g> is translated down by margin.top, so the SVG
                // top is at group-y = -margin.top; keeping y >= fontSize-margin.top
                // leaves the whole glyph visible.
                const minLabelY = colors.fontSize - margin.top;
                svg.selectAll('.bubble-label')
                    .data(data.filter((d: any) => d.label))
                    .join('text')
                    .attr('class', 'bubble-label')
                    .attr('x', (d: any) => x(d.x))
                    .attr('y', (d: any) => Math.max(minLabelY, y(d.y) - radiusOf(d) - 4))
                    .attr('text-anchor', 'middle')
                    .attr('fill', colors.label)
                    .attr('font-size', colors.fontSize)
                    .text((d: any) => d.label);

                return;
            }

            // Create scales
            const x = d3.scaleBand()
                .range([0, width])
                .domain(data.map((d: any) => d.label))
                .padding(0.1);

            const y = d3.scaleLinear()
                .range([height, 0])
                .domain([0, d3.max(data, (d: any) => d.value)]);

            // Add X axis, then fit its category labels (thin/rotate/truncate) and
            // colour it for the active theme.
            const xAxisG = svg.append('g')
                .attr('transform', `translate(0,${height})`)
                .call(d3.axisBottom(x));
            applyBandAxis(xAxisG, data.map((d: any) => String(d?.label ?? '')), bandPlan!, colors.axis);

            // Add Y axis
            svg.append('g')
                .call(d3.axisLeft(y))
                .style('color', colors.axis)
                .selectAll('text').style('fill', colors.axis);

            if (effectiveType === 'bar') {
                // Add bars
                svg.selectAll('rect')
                    .data(data)
                    .join('rect')
                    .attr('x', (d: any) => x(d.label))
                    .attr('y', (d: any) => y(d.value))
                    .attr('width', x.bandwidth())
                    .attr('height', (d: any) => height - y(d.value))
                    .attr('fill', (d: any) => ensureReadableFill(d.color, colors.bg, colors.seriesFallback));
            } else if (effectiveType === 'line' || effectiveType === 'scatter') {
                // Create line generator
                const line = d3.line()
                    .x((d: any) => x(d.label) + x.bandwidth() / 2)
                    .y((d: any) => y(d.value));

                if (effectiveType === 'line') {
                    // Add line
                    svg.append('path')
                        .datum(data)
                        .attr('fill', 'none')
                        .attr('stroke', ensureReadableFill(style.lineColor, colors.bg, colors.seriesFallback))
                        .attr('stroke-width', 2)
                        .attr('d', line);
                }

                // Add points
                svg.selectAll('circle')
                    .data(data)
                    .join('circle')
                    .attr('cx', (d: any) => x(d.label) + x.bandwidth() / 2)
                    .attr('cy', (d: any) => y(d.value))
                    .attr('r', 4)
                    .attr('fill', (d: any) => ensureReadableFill(d.color, colors.bg, colors.seriesFallback))
                    .attr('stroke', colors.markerStroke)
                    .attr('stroke-width', 1);
            }

        } catch (error) {
            console.error('Basic chart render error:', error);
            throw error;
        }
    }
};
