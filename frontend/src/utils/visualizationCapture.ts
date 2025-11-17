/**
 * Visualization Capture Utility
 * 
 * Captures rendered visualizations (SVG, Canvas) from the DOM
 * for embedding in exported conversations.
 */

export interface CapturedVisualization {
    type: 'svg' | 'canvas' | 'image';
    dataUri: string;
    sourceCode?: string;
    width?: number;
    height?: number;
    vizType?: string; // 'mermaid', 'graphviz', 'd3', 'joint', etc.
    index: number; // Position in conversation
}

/**
 * Capture all visualizations from the current conversation
 */
export async function captureAllVisualizations(): Promise<CapturedVisualization[]> {
    const captured: CapturedVisualization[] = [];

    // Find all D3 renderer containers
    const d3Containers = document.querySelectorAll('.d3-container, .vega-lite-container');

    for (let i = 0; i < d3Containers.length; i++) {
        const container = d3Containers[i] as HTMLElement;

        try {
            // Determine visualization type from container classes or data attributes
            const vizType = determineVizType(container);

            // Try to find the source code (usually in a sibling or parent element)
            const sourceCode = findSourceCode(container);

            // Capture the rendered output
            const viz = await captureVisualization(container, vizType, sourceCode, i);

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
    index: number
): Promise<CapturedVisualization | null> {
    // Try to find SVG first (most common for D3/Mermaid/Graphviz)
    const svg = container.querySelector('svg');
    if (svg) {
        return captureSVG(svg, vizType, sourceCode, index);
    }

    // Try canvas (less common but possible)
    const canvas = container.querySelector('canvas');
    if (canvas) {
        return captureCanvas(canvas as HTMLCanvasElement, vizType, sourceCode, index);
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
    index: number
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
        index
    };
}

/**
 * Capture canvas and convert to data URI
 */
function captureCanvas(
    canvas: HTMLCanvasElement,
    vizType: string,
    sourceCode: string | null,
    index: number
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
        index
    };
}

/**
 * Determine visualization type from container
 */
function determineVizType(container: HTMLElement): string {
    // Check data attributes
    const vizType = container.getAttribute('data-visualization-type');
    if (vizType) return vizType;

    // Check class names
    const classList = container.className;
    if (classList.includes('mermaid')) return 'mermaid';
    if (classList.includes('graphviz')) return 'graphviz';
    if (classList.includes('vega-lite')) return 'vega-lite';
    if (classList.includes('joint')) return 'joint';
    if (classList.includes('d2')) return 'd2';

    // Check for plugin-specific containers
    if (classList.includes('mermaid-renderer-container')) return 'mermaid';
    if (classList.includes('graphviz-renderer-container')) return 'graphviz';
    if (classList.includes('joint-renderer-container')) return 'joint';

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
