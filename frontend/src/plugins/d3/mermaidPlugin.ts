import mermaid from 'mermaid';
import { D3RenderPlugin } from '../../types/d3';
import { PictureOutlined, DownloadOutlined } from '@ant-design/icons';
import { Spin } from 'antd'; // For loading indicator

// Define the specification for Mermaid diagrams
export interface MermaidSpec {
    type: 'mermaid';
    definition: string;
    theme?: 'default' | 'dark' | 'neutral' | 'forest'; // Optional theme override
}

// Type guard to check if a spec is for Mermaid
const isMermaidSpec = (spec: any): spec is MermaidSpec => {
    return (
        typeof spec === 'object' &&
        spec !== null &&
        spec.type === 'mermaid' &&
        typeof spec.definition === 'string' &&
        spec.definition.trim().length > 0
    );
};

const SCALE_CONFIG = {
    TARGET_FONT_SIZE: 14,   // Target font size in pixels
    MIN_FONT_SIZE: 12,      // Minimum font size in pixels
    MAX_SCALE: 1.0         // Maximum scale (natural size)
};

export const mermaidPlugin: D3RenderPlugin = {
    name: 'mermaid-renderer',
    priority: 5,

    canHandle: (spec: any): boolean => {
        return isMermaidSpec(spec);
    },

    render: async (container: HTMLElement, d3: any, spec: MermaidSpec, isDarkMode: boolean): Promise<void> => {
        try {
            container.innerHTML = '';

            // Initialize mermaid with graph-specific settings
            mermaid.initialize({
                startOnLoad: false,
                theme: isDarkMode ? 'dark' : 'default',
                securityLevel: 'loose',
                fontFamily: '"Arial", sans-serif',
                fontSize: 14,
                themeVariables: isDarkMode ? {
                    // High contrast dark theme
                    primaryColor: '#81a1c1',
                    primaryTextColor: '#ffffff',
                    primaryBorderColor: '#88c0d0',
                    lineColor: '#88c0d0',
                    secondaryColor: '#5e81ac',
                    tertiaryColor: '#2e3440',
                    
                    // Text colors
                    textColor: '#eceff4',
                    loopTextColor: '#eceff4',
                    
                    // Node colors
                    mainBkg: '#3b4252',
                    secondBkg: '#434c5e',
                    nodeBorder: '#88c0d0',
                    
                    // Edge colors
                    edgeLabelBackground: '#4c566a',
                    
                    // Contrast colors
                    altBackground: '#2e3440',
                    
                    // Flowchart specific
                    nodeBkg: '#3b4252',
                    clusterBkg: '#2e3440',
                    titleColor: '#88c0d0',
                    
                    // Class diagram specific
                    classText: '#ffffff',
                    
                    // State diagram specific
                    labelColor: '#ffffff',
                    
                    // Sequence diagram specific
                    actorBkg: '#4c566a',
                    actorBorder: '#88c0d0',
                    activationBkg: '#5e81ac',
                    
                    // Gantt chart specific
                    sectionBkgColor: '#3b4252',
                    altSectionBkgColor: '#434c5e',
                    gridColor: '#eceff4',
                    todayLineColor: '#88c0d0'
                } : {},
                flowchart: {
                    htmlLabels: true,
                    curve: 'basis',
                    padding: 15,
                    nodeSpacing: 50,
                    rankSpacing: 50,
                    diagramPadding: 8,
                },
                sequence: {
                    diagramMarginX: 50,
                    diagramMarginY: 30,
                    actorMargin: 50,
                    width: 150,
                    height: 65,
                    boxMargin: 10,
                    boxTextMargin: 5,
                    noteMargin: 10,
                    messageMargin: 35,
                    mirrorActors: true,
                    bottomMarginAdj: 1,
                    useMaxWidth: true,
                },
                gantt: {
                    titleTopMargin: 25,
                    barHeight: 20,
                    barGap: 4,
                    topPadding: 50,
                    leftPadding: 75,
                    gridLineStartPadding: 35,
                    fontSize: 11,
                    sectionFontSize: 11,
                    numberSectionStyles: 4,
                    axisFormat: '%Y-%m-%d',
                    topAxis: false,
                },
            });

            // Render the diagram
            const mermaidId = `mermaid-${Date.now()}-${Math.random().toString(16).substring(2)}`;
            const { svg } = await mermaid.render(mermaidId, spec.definition);

            // Create wrapper div
            const wrapper = document.createElement('div');
            wrapper.className = 'mermaid-wrapper';
            wrapper.style.cssText = `
                width: 100%;
                max-width: 100%;
                overflow: auto;
                padding: 1em;
                display: flex;
                justify-content: center;
            `;
            wrapper.innerHTML = svg;

            // Add wrapper to container
            container.appendChild(wrapper);

            // Get the SVG element after it's in the DOM
            const svgElement = wrapper.querySelector('svg');
            if (!svgElement) {
                throw new Error('Failed to get SVG element');
            }

            // Enhance dark theme visibility for specific elements
            if (isDarkMode && svgElement) {
                requestAnimationFrame(() => {
                    // Enhance specific elements that might still have poor contrast
                    svgElement.querySelectorAll('.edgePath path').forEach(el => {
                        el.setAttribute('stroke', '#88c0d0');
                        el.setAttribute('stroke-width', '1.5');
                    });
                    
                    // Fix for arrow markers in dark mode
                    svgElement.querySelectorAll('defs marker path').forEach(el => {
                        el.setAttribute('stroke', '#88c0d0');
                        el.setAttribute('fill', '#88c0d0');
                    });
                    
                    // Fix for all SVG paths and lines
                    svgElement.querySelectorAll('line, path:not([fill])').forEach(el => {
                        el.setAttribute('stroke', '#88c0d0');
                        el.setAttribute('stroke-width', '1.5');
                    });
                    
                    // Text on darker backgrounds should be black for contrast
                    svgElement.querySelectorAll('.node .label text, .cluster .label text').forEach(el => {
                        el.setAttribute('fill', '#000000');
                    });
                    
                    // Text on lighter backgrounds should be white for contrast
                    svgElement.querySelectorAll('.edgeLabel text, text:not(.node .label text):not(.cluster .label text)').forEach(el => {
                        el.setAttribute('fill', '#eceff4');
                    });

                    svgElement.querySelectorAll('path.path, path.messageText, .flowchart-link').forEach(el => {
                        el.setAttribute('stroke', '#88c0d0');
                        el.setAttribute('stroke-width', '1.5');
                    });
                    
                    svgElement.querySelectorAll('.node rect, .node circle, .node polygon, .node path').forEach(el => {
                        el.setAttribute('stroke', '#81a1c1');
                        el.setAttribute('fill', '#5e81ac');
                    });
                    
                    svgElement.querySelectorAll('.cluster rect').forEach(el => {
                        el.setAttribute('stroke', '#81a1c1');
                        el.setAttribute('fill', '#4c566a');
                    });
                });
            }

            // Wait for next frame to ensure SVG is rendered
            requestAnimationFrame(() => {
                // Find all text elements
                const textElements = svgElement.querySelectorAll('text');
                if (textElements.length === 0) return;
                // Get the computed font size of the first text element
                const computedStyle = window.getComputedStyle(textElements[0]);
                const currentFontSize = parseFloat(computedStyle.fontSize);

                // Calculate scale based on target font size
                const scale = SCALE_CONFIG.TARGET_FONT_SIZE / currentFontSize;

                // Apply transform scale to the SVG
                svgElement.style.transform = `scale(${scale})`;
                svgElement.style.transformOrigin = 'center';
                svgElement.style.width = '100%';
                svgElement.style.height = 'auto';
            });

            // Add action buttons
            const actionsContainer = document.createElement('div');
            actionsContainer.className = 'diagram-actions';

            // Add Open button
            const openButton = document.createElement('button');
            openButton.innerHTML = '↗️ Open';
            openButton.className = 'diagram-action-button mermaid-open-button';
            openButton.onclick = () => {
                // Get the SVG element
                const svgElement = wrapper.querySelector('svg');
                if (!svgElement) return;
                
                // Get the SVG dimensions
                const svgGraphics = svgElement as unknown as SVGGraphicsElement;
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
                const svgData = new XMLSerializer().serializeToString(svgElement);
                
                // Create an HTML document that will display the SVG responsively
                const htmlContent = `
                <!DOCTYPE html>
                <html>
                <head>
                    <meta charset="UTF-8">
                    <title>Mermaid Diagram</title>
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
                            link.download = 'mermaid-diagram.svg';
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
                    'MermaidDiagram', 
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
            saveButton.className = 'diagram-action-button mermaid-save-button';
            saveButton.onclick = () => {
                // Get the SVG element
                const svgElement = wrapper.querySelector('svg');
                if (!svgElement) return;
                
                // Create a new SVG with proper XML declaration and doctype
                const svgData = new XMLSerializer().serializeToString(svgElement);
                
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
                link.download = `mermaid-diagram-${Date.now()}.svg`;
                document.body.appendChild(link);
                link.click();
                document.body.removeChild(link);
                
                // Clean up the URL object after a delay
                setTimeout(() => URL.revokeObjectURL(url), 1000);
            };
            actionsContainer.appendChild(saveButton);

            // Add Source button
            let showingSource = false;
            const originalContent = wrapper.innerHTML;
            const sourceButton = document.createElement('button');
            sourceButton.innerHTML = showingSource ? '🎨 View' : '📝 Source';
            sourceButton.className = 'diagram-action-button mermaid-source-button';
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

            // Add actions container
            container.insertBefore(actionsContainer, wrapper);

        } catch (error: any) {
            console.error('Mermaid rendering error:', error);
            container.innerHTML = `
                <div class="mermaid-error">
                    <strong>Mermaid Error:</strong>
                    <pre>${error.message || 'Unknown error'}</pre>
                    <details>
                        <summary>Show Definition</summary>
                        <pre><code>${spec.definition}</code></pre>
                    </details>
                </div>
            `;
        }
    }
};
