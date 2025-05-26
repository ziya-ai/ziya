import * as Viz from '@viz-js/viz';
import { D3RenderPlugin } from '../../types/d3';
import { isDiagramDefinitionComplete } from '../../utils/diagramUtils';

export interface GraphvizSpec {
    type: 'graphviz';
    isStreaming?: boolean;
    forceRender?: boolean;
    definition: string;
}

const isGraphvizSpec = (spec: any): spec is GraphvizSpec => {
    return (
        typeof spec === 'object' &&
        spec !== null &&
        spec.type === 'graphviz' &&
        typeof spec.definition === 'string' &&
        spec.definition.trim().length > 0
    );
};

// Store the current theme for each container to detect changes
const containerThemes = new WeakMap<HTMLElement, boolean>();

export const graphvizPlugin: D3RenderPlugin = {
    name: 'graphviz-renderer',
    priority: 5,
    canHandle: isGraphvizSpec,

    // Helper to check if a graphviz definition is complete
    isDefinitionComplete: (definition: string): boolean => {
        if (!definition || definition.trim().length === 0) return false;

        // Check for balanced braces which is a good indicator of completeness
        const openBraces = definition.split('{').length - 1;
        const closeBraces = definition.split('}').length - 1;

        // A complete definition should have balanced braces and end with a closing brace
        return openBraces === closeBraces && openBraces > 0 && definition.includes('}');
    },

    render: async (container: HTMLElement, d3: any, spec: GraphvizSpec, isDarkMode: boolean) => {
        try {
            const hasExistingContent = container.querySelector('svg') !== null;
            // Show loading spinner immediately
            container.innerHTML = `
                <div style="
                    display: flex;
                    flex-direction: column;
                    align-items: center;
                    justify-content: center;
                    padding: 2em;
                    width: 100%;
                    height: 100%;
                    min-height: 150px;
                ">
                    <div style="
                        border: 4px solid rgba(0, 0, 0, 0.1);
                        border-top: 4px solid ${isDarkMode ? '#4cc9f0' : '#3498db'};
                        border-radius: 50%;
                        width: 40px;
                        height: 40px;
                        animation: graphviz-spin 1s linear infinite;
                        margin-bottom: 15px;
                    "></div>
                    <div style="
                        font-family: system-ui, -apple-system, sans-serif;
                        color: ${isDarkMode ? '#eceff4' : '#333333'};
                    ">Rendering Visualization...</div>
                </div>
                <style>
                    @keyframes graphviz-spin {
                        0% { transform: rotate(0deg); }
                        100% { transform: rotate(360deg); }
                    }
                </style>
            `;

            // If we're streaming and the definition is incomplete, show a waiting message
            if (spec.isStreaming && !spec.forceRender) {
                const isComplete = isDiagramDefinitionComplete(spec.definition, 'graphviz');
                const timestamp = Date.now();
                console.log(`[${timestamp}] Graphviz streaming check:`, {
                    isComplete,
                    definitionLength: spec.definition.length,
                    definitionPreview: spec.definition.substring(0, 50),
                    definitionEnd: spec.definition.substring(spec.definition.length - 20)
                });

                if (!isComplete) {
                    container.innerHTML = `
                        <div style="text-align: center; padding: 20px; background-color: ${isDarkMode ? '#1f1f1f' : '#f6f8fa'}; border: 1px dashed #ccc; border-radius: 4px;">
                            <p>Waiting for complete Graphviz definition...</p>
                        </div>
                    `;
                    return; // Exit early and wait for complete definition
                }

                // If we already have content and we're streaming, don't show errors
                if (hasExistingContent && spec.isStreaming) {
                    return; // Keep existing content during streaming if definition is incomplete
                }
            }
            console.log(`Rendering Graphviz diagram with ${spec.definition.length} chars`);

            // Store the current theme for this container
            containerThemes.set(container, isDarkMode);

            // Enhanced theme colors with better contrast
            const themeColors = {
                light: {
                    text: '#333333',                // Darker text for better contrast
                    stroke: '#555555',              // Darker stroke
                    nodeFill: '#f5f5f5',            // Light gray node fill
                    nodeBorder: '#999999',          // Medium gray node border
                    edgeColor: '#333333',           // Dark edge color for better visibility
                    background: 'transparent',
                    labelText: '#333333',           // Dark label text
                    clusterBg: '#f0f0f0',           // Cluster background
                    clusterBorder: '#cccccc'        // Cluster border
                },
                dark: {
                    // Bright, happy colors for dark mode
                    text: '#ffffff',                // White text for high contrast
                    stroke: '#00b7ff',              // Bright cyan stroke
                    nodeFill: '#2a3990',            // Rich blue node fill
                    nodeBorder: '#4cc9f0',          // Bright blue node border
                    edgeColor: '#f72585',           // Vibrant pink edge color for high visibility
                    background: 'transparent',
                    labelText: '#ffffff',           // White text for labels
                    clusterBg: '#1a1a2e',           // Dark cluster background
                    clusterBorder: '#4cc9f0',       // Bright cluster border

                    // Alternative node colors for variety
                    nodeColors: [
                        '#4361ee',                  // Royal blue
                        '#3a0ca3',                  // Deep purple
                        '#7209b7',                  // Vibrant purple
                        '#f72585',                  // Hot pink
                        '#4cc9f0',                  // Bright cyan
                        '#06d6a0',                  // Mint green
                        '#118ab2',                  // Teal
                    ]
                }
            };

            const colors = isDarkMode ? themeColors.dark : themeColors.light;

            const vizInstance = await Viz.instance();

            // Add theme attributes to dot with more styling options
            let themedDot = spec.definition;

            // Only add theme attributes if the graph has a proper structure
            if (spec.definition.match(/^(\s*(?:di)?graph\s+[^{]*{)/)) {
                // Set default text color based on page mode
                const defaultTextColor = isDarkMode ? '#ffffff' : '#000000';

                themedDot = spec.definition.replace(
                    /^(\s*(?:di)?graph\s+[^{]*{)/,
                    `$1
                    bgcolor="transparent";
                    node [color="${colors.nodeBorder}", fontcolor="${defaultTextColor}", style="filled", fillcolor="${colors.nodeFill}", penwidth=1.5];
                    edge [color="${colors.edgeColor}", fontcolor="${defaultTextColor}", penwidth=1.5];
                    graph [fontcolor="${defaultTextColor}", color="${colors.clusterBorder}", fontname="Arial"];`
                );

                // Handle graph label if present
                const labelMatch = spec.definition.match(/\s+label\s*=\s*"([^"]+)"/);
                if (labelMatch) {
                    const originalLabel = labelMatch[1];
                    themedDot = themedDot.replace(
                        /\s+label\s*=\s*"([^"]+)"/,
                        ` label=<<font color="${defaultTextColor}">${originalLabel}</font>>`
                    );
                }
            }

            const element = await vizInstance.renderSVGElement(themedDot);

            // Apply theme to SVG elements with more specific styling
            const elements = element.getElementsByTagName('*');

            // For dark mode, prepare to assign different colors to nodes
            let nodeIndex = 0;
            const nodeColors = isDarkMode ? themeColors.dark.nodeColors : [];

            // First pass: Apply colors to nodes and collect background colors
            const nodeBackgroundColors = new Map(); // Map to store node background colors
            const clusterBackgroundColors = new Map(); // Map to store cluster background colors

            // First identify all clusters and their background colors
            for (let i = 0; i < elements.length; i++) {
                const el = elements[i];

                // Identify cluster backgrounds
                if (el.tagName === 'polygon' && el.parentElement && el.parentElement.classList.contains('cluster')) {
                    const originalFill = el.getAttribute('fill');
                    if (originalFill) {
                        clusterBackgroundColors.set(el.parentElement, originalFill);

                        // In dark mode, override light cluster backgrounds
                        if (isDarkMode) {
                            // Check if this is a light color that needs to be darkened
                            if (isLightColor(originalFill)) {
                                // Use a darker color based on the original hue
                                const darkColor = getDarkVersionOfColor(originalFill);
                                el.setAttribute('fill', darkColor);
                                el.setAttribute('stroke', colors.clusterBorder);

                                // Store the fact that we changed this color
                                el.setAttribute('data-original-fill', originalFill);
                                el.setAttribute('data-darkened', 'true');
                            }
                        }
                    }
                }
            }

            // Then process nodes
            for (let i = 0; i < elements.length; i++) {
                const el = elements[i];

                if (el.tagName === 'ellipse' || el.tagName === 'polygon') {
                    // Node shapes
                    if (el.getAttribute('fill') !== 'none') {
                        // Store the original fill color before we modify it
                        const originalFill = el.getAttribute('fill');
                        if (originalFill) {
                            // Store the element and its original fill color
                            nodeBackgroundColors.set(el, originalFill);
                        }

                        // In dark mode, handle node colors
                        if (isDarkMode) {
                            // Check if this is a light color that needs to be darkened
                            if (originalFill && isLightColor(originalFill)) {
                                // For white or very light colors, use our node colors
                                if (originalFill.toLowerCase() === '#ffffff' ||
                                    originalFill.toLowerCase() === 'white' ||
                                    getBrightness(originalFill) > 0.9) {

                                    if (nodeColors.length > 0) {
                                        const colorIndex = nodeIndex % nodeColors.length;
                                        el.setAttribute('fill', nodeColors[colorIndex]);

                                        // Store the fact that we changed this color
                                        el.setAttribute('data-original-fill', originalFill);
                                        el.setAttribute('data-darkened', 'true');

                                        nodeIndex++;
                                    } else {
                                        el.setAttribute('fill', colors.nodeFill);

                                        // Store the fact that we changed this color
                                        el.setAttribute('data-original-fill', originalFill);
                                        el.setAttribute('data-darkened', 'true');
                                    }
                                } else {
                                    // For other light colors, darken them
                                    const darkColor = getDarkVersionOfColor(originalFill);
                                    el.setAttribute('fill', darkColor);

                                    // Store the fact that we changed this color
                                    el.setAttribute('data-original-fill', originalFill);
                                    el.setAttribute('data-darkened', 'true');
                                }
                            }

                            // Set border color
                            el.setAttribute('stroke', colors.nodeBorder);
                            el.setAttribute('stroke-width', '1.5');
                        }
                    }
                }
            }

            // Second pass: Apply text colors based on their parent node colors
            // and ensure edges are visible against backgrounds
            for (let i = 0; i < elements.length; i++) {
                const el = elements[i];

                if (el.tagName === 'text') {
                    // Default text color based on page mode
                    // Use the opposite of the page background for maximum visibility
                    const defaultTextColor = isDarkMode ? '#ffffff' : '#000000';
                    el.setAttribute('fill', defaultTextColor);

                    // For node labels, ensure text is readable against node background
                    const parent = el.parentElement;
                    if (parent) {
                        // Find the node shape element (ellipse, polygon) in the parent
                        const nodeShape = parent.querySelector('ellipse, polygon');

                        if (nodeShape) {
                            // Get the current fill color of the node
                            const currentFill = nodeShape.getAttribute('fill');

                            // If we have a background color, set text color based on its brightness
                            if (currentFill) {
                                const isLightBackground = isLightColor(currentFill);
                                el.setAttribute('fill', isLightBackground ? '#000000' : '#ffffff');
                            }

                            // Add debug attributes
                            el.setAttribute('data-bg-color', currentFill || 'unknown');
                            el.setAttribute('data-is-light', isLightColor(currentFill || '') ? 'true' : 'false');
                        } else if (parent.classList.contains('cluster')) {
                            // This is a cluster label
                            // Find the cluster background
                            const clusterBg = parent.querySelector('polygon');
                            if (clusterBg) {
                                const bgColor = clusterBg.getAttribute('fill');
                                // If we have a background color, set text color based on its brightness
                                if (bgColor) {
                                    const isLightBackground = isLightColor(bgColor);
                                    el.setAttribute('fill', isLightBackground ? '#000000' : '#ffffff');
                                }
                            }
                        } else if (parent.classList.contains('edge')) {
                            // This is an edge label - use the default text color based on page mode
                            // This ensures edge labels are visible against the page background
                            el.setAttribute('fill', defaultTextColor);
                        }
                    }

                    // Special case for graph labels (which might not have a direct node parent)
                    if (parent && parent.classList.contains('graph')) {
                        // Graph labels should match the default text color for the page mode
                        el.setAttribute('fill', defaultTextColor);
                    }
                } else if (el.tagName === 'path') {
                    // Edge paths
                    if (!el.getAttribute('fill') || el.getAttribute('fill') === 'none') {
                        // Make sure edges are visible with high contrast color
                        el.setAttribute('stroke', colors.edgeColor);
                        el.setAttribute('stroke-width', '1.5');
                    }
                } else if (el.tagName === 'polygon' && el.classList.contains('arrow')) {
                    // This is an arrowhead
                    el.setAttribute('fill', colors.edgeColor);
                    el.setAttribute('stroke', colors.edgeColor);
                }
            }

            // Clear container and append SVG
            container.innerHTML = '';

            // Create wrapper div similar to mermaid plugin
            const wrapper = document.createElement('div');
            wrapper.className = 'graphviz-wrapper';
            wrapper.style.cssText = `
                width: 100%;
                max-width: 100%;
                overflow: auto;
                padding: 1em;
                display: flex;
                justify-content: center;
            `;

            // Add the SVG to the wrapper
            wrapper.appendChild(element);

            // Add wrapper to container
            container.appendChild(wrapper);

            // Add action buttons container
            const actionsContainer = document.createElement('div');
            actionsContainer.className = 'diagram-actions';

            // Add Open button
            const openButton = document.createElement('button');
            openButton.innerHTML = '↗️ Open';
            openButton.className = 'diagram-action-button graphviz-open-button';
            openButton.onclick = () => {
                // Get the SVG dimensions
                const svgGraphics = element as unknown as SVGGraphicsElement;
                let width = 600;
                let height = 400;

                try {
                    // Try to get the bounding box
                    const bbox = svgGraphics.getBBox();
                    width = Math.max(bbox.width + 50, 400); // Add padding, minimum 400px
                    height = Math.max(bbox.height + 100, 300); // Add padding, minimum 300px
                } catch (e) {
                    console.warn('Could not get SVG dimensions, using defaults', e);
                }

                // Create a new SVG with proper XML declaration and doctype
                const svgData = new XMLSerializer().serializeToString(element);

                // Create an HTML document that will display the SVG responsively
                const htmlContent = `
                <!DOCTYPE html>
                <html>
                <head>
                    <meta charset="UTF-8">
                    <title>Graphviz Diagram</title>
                    <style>
                        body {
                            margin: 0;
                            padding: 0;
                            display: flex;
                            flex-direction: column;
                            height: 100vh;
                            background-color: #f8f9fa;
                            font-family: system-ui, -apple-system, sans-serif;
                        }
                        .toolbar {
                            background-color: #f1f3f5;
                            border-bottom: 1px solid #dee2e6;
                            padding: 8px;
                            display: flex;
                            justify-content: space-between;
                            align-items: center;
                        }
                        .toolbar button {
                            background-color: #4361ee;
                            color: white;
                            border: none;
                            border-radius: 4px;
                            padding: 6px 12px;
                            cursor: pointer;
                            margin-right: 8px;
                            font-size: 14px;
                        }
                        .toolbar button:hover {
                            background-color: #3a0ca3;
                        }
                        .container {
                            flex: 1;
                            display: flex;
                            justify-content: center;
                            align-items: center;
                            overflow: auto;
                            padding: 20px;
                        }
                        svg {
                            max-width: 100%;
                            max-height: 100%;
                            height: auto;
                            width: auto;
                        }
                        @media (prefers-color-scheme: dark) {
                            body {
                                background-color: #212529;
                                color: #f8f9fa;
                            }
                            .toolbar {
                                background-color: #343a40;
                                border-bottom: 1px solid #495057;
                            }
                        }
                    </style>
                </head>
                <body>
                    <div class="toolbar">
                        <div>
                            <button onclick="zoomIn()">Zoom In</button>
                            <button onclick="zoomOut()">Zoom Out</button>
                            <button onclick="resetZoom()">Reset</button>
                        </div>
                        <div>
                            <button onclick="downloadSvg()">Download SVG</button>
                        </div>
                    </div>
                    <div class="container" id="svg-container">
                        ${svgData}
                    </div>
                    <script>
                        const svg = document.querySelector('svg');
                        let currentScale = 1;
                        
                        // Make sure SVG is responsive
                        svg.setAttribute('width', '100%');
                        svg.setAttribute('height', '100%');
                        svg.style.maxWidth = '100%';
                        svg.style.maxHeight = '100%';
                        svg.setAttribute('preserveAspectRatio', 'xMidYMid meet');
                        
                        function zoomIn() {
                            currentScale *= 1.2;
                            svg.style.transform = \`scale(\${currentScale})\`;
                        }
                        
                        function zoomOut() {
                            currentScale /= 1.2;
                            svg.style.transform = \`scale(\${currentScale})\`;
                        }
                        
                        function resetZoom() {
                            currentScale = 1;
                            svg.style.transform = 'scale(1)';
                        }
                        
                        function downloadSvg() {
                            const svgData = new XMLSerializer().serializeToString(svg);
                            const svgBlob = new Blob([svgData], {type: 'image/svg+xml'});
                            const url = URL.createObjectURL(svgBlob);
                            
                            const link = document.createElement('a');
                            link.href = url;
                            link.download = 'graphviz-diagram.svg';
                            document.body.appendChild(link);
                            link.click();
                            document.body.removeChild(link);
                            
                            setTimeout(() => URL.revokeObjectURL(url), 1000);
                        }
                    </script>
                </body>
                </html>
                `;

                // Create a blob with the HTML content
                const blob = new Blob([htmlContent], { type: 'text/html' });
                const url = URL.createObjectURL(blob);

                // Open in a new window with specific dimensions
                const popupWindow = window.open(
                    url,
                    'GraphvizDiagram',
                    `width=${width},height=${height},resizable=yes,scrollbars=yes,status=no,toolbar=no,menubar=no,location=no`
                );

                // Focus the new window
                if (popupWindow) {
                    popupWindow.focus();
                }

                // Clean up the URL object after a delay
                setTimeout(() => URL.revokeObjectURL(url), 10000);
            };
            actionsContainer.appendChild(openButton);

            // Add Save button
            const saveButton = document.createElement('button');
            saveButton.innerHTML = '💾 Save';
            saveButton.className = 'diagram-action-button graphviz-save-button';
            saveButton.onclick = () => {
                // Create a new SVG with proper XML declaration and doctype
                const svgData = new XMLSerializer().serializeToString(element);

                // Create a properly formatted SVG document with XML declaration
                const svgDoc = `<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" "http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd">
${svgData}`;

                // Create a blob with the SVG content
                const blob = new Blob([svgDoc], { type: 'image/svg+xml' });
                const url = URL.createObjectURL(blob);

                // Create a download link
                const link = document.createElement('a');
                link.href = url;
                link.download = `graphviz-diagram-${Date.now()}.svg`;
                document.body.appendChild(link);
                link.click();
                document.body.removeChild(link);

                // Clean up the URL object after a delay
                setTimeout(() => URL.revokeObjectURL(url), 1000);
            };
            actionsContainer.appendChild(saveButton);

            // Add Source button with toggle functionality like in mermaid plugin
            let showingSource = false;
            const originalContent = wrapper.innerHTML;
            const sourceButton = document.createElement('button');
            sourceButton.innerHTML = showingSource ? '🎨 View' : '📝 Source';
            sourceButton.className = 'diagram-action-button graphviz-source-button';
            sourceButton.onclick = () => {
                showingSource = !showingSource;
                sourceButton.innerHTML = showingSource ? '🎨 View' : '📝 Source';

                if (showingSource) {
                    wrapper.innerHTML = `<pre style="
                        background-color: ${isDarkMode ? '#1f1f1f' : '#f6f8fa'};
                        padding: 16px;
                        border-radius: 4px;
                        overflow: auto;
                        color: ${isDarkMode ? '#e6e6e6' : '#24292e'};
                    "><code>${spec.definition}</code></pre>`;
                } else {
                    wrapper.innerHTML = originalContent;
                }
            };
            actionsContainer.appendChild(sourceButton);

            // Add actions container before the wrapper
            container.insertBefore(actionsContainer, wrapper);

            // Add a theme button to manually re-render with the opposite theme
            const themeButton = document.createElement('button');
            themeButton.innerHTML = isDarkMode ? '☀️ Light' : '🌙 Dark';
            themeButton.className = 'diagram-action-button graphviz-theme-button';
            themeButton.onclick = () => {
                // Re-render with the opposite theme
                graphvizPlugin.render(container, d3, spec, !isDarkMode);
            };
            actionsContainer.appendChild(themeButton);
        } catch (error) {
            console.error('Graphviz rendering error:', error);

            // Only show error if we're not streaming or if we have no existing content
            if (!spec.isStreaming || !container.querySelector('svg')) {
                container.innerHTML = `
                <div class="graphviz-error">
                    <strong>Graphviz Error:</strong>
                    <pre>${error instanceof Error ? error.message : 'Unknown error'}</pre>
                    <details>
                        <summary>Show Definition</summary>
                        <pre><code>${spec.definition}</code></pre>
                    </details>
                </div>
            `;
            }
        }
    }
};

// Helper function to determine if a color is light or dark
function isLightColor(color: string): boolean {
    // Default to assuming it's a dark color if we can't parse it
    if (!color || color === 'transparent' || color === 'none') {
        return false;
    }

    // Handle specific named colors that we know are light
    const lightNamedColors = [
        'white', 'lightblue', 'lightgreen', 'lightgrey', 'lightgray', 'pink',
        '#aed6f1', '#d4e6f1', '#d5f5e3', '#f5f5f5', '#e6e6e6', '#f0f0f0',
        '#ffffff', '#f8f9fa', '#e9ecef', '#dee2e6', '#ced4da', '#adb5bd'
    ];

    // Case-insensitive check for light named colors
    if (lightNamedColors.some(c => c.toLowerCase() === color.toLowerCase())) {
        return true;
    }

    // Get brightness value
    const brightness = getBrightness(color);

    // More conservative threshold - if in doubt, assume it's light
    // This ensures we don't put white text on ambiguous backgrounds
    return brightness > 0.5;
}

// Helper function to calculate brightness of a color
function getBrightness(color: string): number {
    // Convert hex or named colors to RGB
    let r, g, b;

    if (color.startsWith('#')) {
        // Handle hex colors
        const hex = color.substring(1);
        if (hex.length === 3) {
            r = parseInt(hex[0] + hex[0], 16);
            g = parseInt(hex[1] + hex[1], 16);
            b = parseInt(hex[2] + hex[2], 16);
        } else {
            r = parseInt(hex.substring(0, 2), 16);
            g = parseInt(hex.substring(2, 4), 16);
            b = parseInt(hex.substring(4, 6), 16);
        }
    } else if (color.startsWith('rgb')) {
        // Handle rgb/rgba colors
        const match = color.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)(?:,\s*[\d.]+)?\)/);
        if (match) {
            r = parseInt(match[1], 10);
            g = parseInt(match[2], 10);
            b = parseInt(match[3], 10);
        } else {
            // Can't parse, assume dark
            return 0;
        }
    } else {
        // Can't parse, assume dark
        return 0;
    }

    // Calculate perceived brightness using the formula:
    // (0.299*R + 0.587*G + 0.114*B)
    return (0.299 * r + 0.587 * g + 0.114 * b) / 255;
}

// Helper function to get a dark version of a color
function getDarkVersionOfColor(color: string): string {
    // For named colors, map to dark equivalents
    const colorMap: Record<string, string> = {
        'white': '#2e3440',
        'lightblue': '#5e81ac',
        'lightgreen': '#8fbcbb',
        'lightgrey': '#4c566a',
        'lightgray': '#4c566a',
        'pink': '#b48ead'
    };

    // Check if we have a direct mapping
    if (colorMap[color.toLowerCase()]) {
        return colorMap[color.toLowerCase()];
    }

    // Otherwise, try to darken the color
    try {
        let r, g, b;

        if (color.startsWith('#')) {
            // Handle hex colors
            const hex = color.substring(1);
            if (hex.length === 3) {
                r = parseInt(hex[0] + hex[0], 16);
                g = parseInt(hex[1] + hex[1], 16);
                b = parseInt(hex[2] + hex[2], 16);
            } else {
                r = parseInt(hex.substring(0, 2), 16);
                g = parseInt(hex.substring(2, 4), 16);
                b = parseInt(hex.substring(4, 6), 16);
            }
        } else if (color.startsWith('rgb')) {
            // Handle rgb/rgba colors
            const match = color.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)(?:,\s*[\d.]+)?\)/);
            if (match) {
                r = parseInt(match[1], 10);
                g = parseInt(match[2], 10);
                b = parseInt(match[3], 10);
            } else {
                return '#2e3440'; // Default dark color
            }
        } else {
            return '#2e3440'; // Default dark color
        }

        // Darken the color by reducing each component by 60%
        r = Math.max(Math.floor(r * 0.4), 0);
        g = Math.max(Math.floor(g * 0.4), 0);
        b = Math.max(Math.floor(b * 0.4), 0);

        // Convert back to hex
        return `#${r.toString(16).padStart(2, '0')}${g.toString(16).padStart(2, '0')}${b.toString(16).padStart(2, '0')}`;
    } catch (e) {
        return '#2e3440'; // Default dark color if parsing fails
    }
}
