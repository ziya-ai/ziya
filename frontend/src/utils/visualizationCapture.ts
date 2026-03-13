/**
 * Visualization Capture Utility
 * 
 * Captures rendered visualizations (SVG, Canvas) from the DOM
 * for embedding in exported conversations.
 */

import { VISUALIZATION_TYPES } from '../constants/visualizationTypes';

export interface CapturedVisualization {
    type: 'svg' | 'canvas' | 'image';
    dataUri: string;
    sourceCode?: string;
    width?: number;
    height?: number;
    vizType?: string; // 'mermaid', 'graphviz', 'd3', 'joint', etc.
    sourceHash?: string; // Content fingerprint for matching to code blocks
}

/**
 * Capture all visualizations from the current conversation
 */
export async function captureAllVisualizations(): Promise<CapturedVisualization[]> {
    const captured: CapturedVisualization[] = [];

    // Find only top-level D3 renderer containers.  D3Renderer nests two
    // .d3-container divs; selecting only outermost avoids capturing each
    // diagram twice.
    const d3Containers = document.querySelectorAll('.d3-container:not(.d3-container .d3-container)');

    for (let i = 0; i < d3Containers.length; i++) {
        const container = d3Containers[i] as HTMLElement;

        try {
            // Determine visualization type from container classes or data attributes
            const vizType = determineVizType(container);

            // Try to find the source code (usually in a sibling or parent element)
            const sourceCode = findSourceCode(container);

            // Read the content fingerprint stamped by D3Renderer so the
            // backend can match this capture to the right code block
            // regardless of message filtering / round trimming.
            const sourceHash = container.getAttribute('data-viz-source-hash') || undefined;

            const viz = await captureVisualization(container, vizType, sourceCode, sourceHash);

            if (viz) {
                captured.push(viz);
            }
        } catch (error) {
            console.error('Failed to capture visualization:', error);
        }
    }

    console.log(`📸 Captured ${captured.length} visualizations`);
    return captured;
}

/**
 * Capture a single visualization element
 */
async function captureVisualization(
    container: HTMLElement,
    vizType: string,
    sourceCode: string | null,
    sourceHash: string | undefined
): Promise<CapturedVisualization | null> {
    // Try to find SVG first (most common for D3/Mermaid/Graphviz)
    const svg = container.querySelector('svg');
    if (svg) {
        return captureSVG(svg, vizType, sourceCode, sourceHash);
    }

    // Try canvas (less common but possible)
    const canvas = container.querySelector('canvas');
    if (canvas) {
        return captureCanvas(canvas as HTMLCanvasElement, vizType, sourceCode, sourceHash);
    }

    // No renderable content found
    return null;
}

/**
 * Capture SVG and convert to data URI
 */
function captureSVG(
    svg: SVGElement,
    vizType: string,
    sourceCode: string | null,
    sourceHash: string | undefined
): CapturedVisualization {
    // Clone the SVG to avoid modifying the original
    const clone = svg.cloneNode(true) as SVGElement;

    // Cast to SVGSVGElement for dimension access
    const svgElement = clone as unknown as SVGSVGElement;

    // Ensure the SVG has proper dimensions
    if (!clone.hasAttribute('width')) {
        try {
            const bbox = (svg as unknown as SVGGraphicsElement).getBBox();
            clone.setAttribute('width', String(bbox.width));
            clone.setAttribute('height', String(bbox.height));
        } catch (e) {
            // Fallback dimensions if getBBox fails
            clone.setAttribute('width', '600');
            clone.setAttribute('height', '400');
        }
    }

    // Add XML namespace if not present
    if (!clone.hasAttribute('xmlns')) {
        clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
    }

    // Serialize the SVG
    const serializer = new XMLSerializer();
    const svgString = serializer.serializeToString(clone);

    // Convert to base64 data URI
    const base64 = btoa(unescape(encodeURIComponent(svgString)));
    const dataUri = `data:image/svg+xml;base64,${base64}`;

    // Get dimensions with fallback
    const width = svgElement.width?.baseVal?.value ||
        parseInt(clone.getAttribute('width') || '600', 10);
    const height = svgElement.height?.baseVal?.value ||
        parseInt(clone.getAttribute('height') || '400', 10);

    return {
        type: 'svg',
        dataUri,
        sourceCode: sourceCode || undefined,
        width,
        height,
        vizType,
        sourceHash
    };
}

/**
 * Capture canvas and convert to data URI
 */
function captureCanvas(
    canvas: HTMLCanvasElement,
    vizType: string,
    sourceCode: string | null,
    sourceHash: string | undefined
): CapturedVisualization {
    // Convert canvas to PNG data URI
    const dataUri = canvas.toDataURL('image/png');

    return {
        type: 'canvas',
        dataUri,
        sourceCode: sourceCode ?? undefined,
        width: canvas.width,
        height: canvas.height,
        vizType,
        sourceHash
    };
}

/**
 * Determine visualization type from container
 */
function determineVizType(container: HTMLElement): string {
    // Check data attributes
    const vizType = container.getAttribute('data-visualization-type');
    if (vizType) return vizType;

    const classList = container.className;

    // Match against the canonical visualization type list — checks both
    // bare class names (e.g. "mermaid") and renderer-container suffixed
    // names (e.g. "mermaid-renderer-container").
    for (const vt of VISUALIZATION_TYPES) {
        if (classList.includes(vt) || classList.includes(`${vt}-renderer-container`)) {
            return vt;
        }
    }

    return 'd3'; // Default fallback
}

/**
 * Find source code for a visualization
 */
function findSourceCode(container: HTMLElement): string | null {
    // Look for code blocks near this visualization
    // Strategy: Find the nearest preceding code block with viz type

    let current = container.parentElement;
    let depth = 0;

    while (current && depth < 5) {
        // Look for code blocks in siblings or parent
        const codeBlocks = current.querySelectorAll('pre code');

        for (const code of Array.from(codeBlocks)) {
            const codeText = code.textContent;
            if (codeText && (
                codeText.includes('graph') ||
                codeText.includes('flowchart') ||
                codeText.includes('digraph') ||
                codeText.includes('"mark"') || // Vega-Lite
                codeText.includes('$schema')
            )) {
                return codeText;
            }
        }

        current = current.parentElement;
        depth++;
    }

    return null;
}
