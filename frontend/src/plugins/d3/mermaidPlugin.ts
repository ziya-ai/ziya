import mermaid from 'mermaid';
import { D3RenderPlugin } from '../../types/d3';
import { PictureOutlined, DownloadOutlined } from '@ant-design/icons';
import { Spin } from 'antd'; // For loading indicator
import initMermaidSupport from './mermaidEnhancer';
import { isDiagramDefinitionComplete } from '../../utils/diagramUtils';

// Add mermaid to window for TypeScript
declare global {
    interface Window {
        mermaid: typeof mermaid;
    }
}

// Define the specification for Mermaid diagrams
export interface MermaidSpec {
    type: 'mermaid';
    isStreaming?: boolean;
    forceRender?: boolean;
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

// Initialize Mermaid support with preprocessing and error handling
initMermaidSupport(mermaid);

export const mermaidPlugin: D3RenderPlugin = {
    name: 'mermaid-renderer',
    priority: 5,

    canHandle: (spec: any): boolean => {
        return isMermaidSpec(spec);
    },

    // Helper to check if a mermaid definition is complete
    isDefinitionComplete: (definition: string): boolean => {
        if (!definition || definition.trim().length === 0) return false;

        // Check for basic completeness indicators
        const lines = definition.trim().split('\n');
        if (lines.length < 2) return false;

        const firstLine = lines[0].trim().toLowerCase();

        // Check for specific diagram types
        if (firstLine.startsWith('graph') || firstLine.startsWith('flowchart')) {
            // For flowcharts, check for balanced braces
            const openBraces = definition.split('{').length - 1;
            const closeBraces = definition.split('}').length - 1;
            return openBraces === closeBraces && openBraces > 0;
        }

        // For other diagram types, check if there are at least a few lines
        // and the definition doesn't end with an incomplete code block
        return lines.length >= 3 && !definition.endsWith('```');
    },


    render: async (container: HTMLElement, d3: any, spec: MermaidSpec, isDarkMode: boolean): Promise<void> => {
        console.log(`🎯 MERMAID PLUGIN RENDER CALLED with spec:`, spec);
        console.log(`Mermaid plugin render called with spec type: ${spec.type}, definition length: ${spec.definition.length}`);
        console.log('📊 DIAGRAM PREVIEW:', spec.definition.substring(0, 100).replace(/\n/g, '\\n'));
        console.log('🔍 HAS HTML TAGS:', spec.definition.includes('<br'));

        let renderSuccessful = false;
        try {
            container.innerHTML = '';

            // Add loading spinner while mermaid renders
            const loadingSpinner = document.createElement('div');
            loadingSpinner.className = 'mermaid-loading-spinner';
            loadingSpinner.style.cssText = `
                display: flex;
                flex-direction: column;
                align-items: center;
                justify-content: center;
                padding: 2em;
                min-height: 150px;
                width: 100%;
            `;

            // Add spinner animation
            loadingSpinner.innerHTML = `
                <div style="
                    border: 4px solid rgba(0, 0, 0, 0.1);
                    border-top: 4px solid ${isDarkMode ? '#4cc9f0' : '#3498db'};
                    border-radius: 50%;
                    width: 40px;
                    height: 40px;
                    animation: mermaid-spin 1s linear infinite;
                    margin-bottom: 15px;
                "></div>
                <div style="font-family: system-ui, -apple-system, sans-serif; color: ${isDarkMode ? '#eceff4' : '#333333'};">
                    Rendering diagram...
                </div>
            `;

            container.appendChild(loadingSpinner);

            // If we're streaming and the definition is incomplete, show a waiting message
            if (spec.isStreaming && !spec.forceRender) {
                const isComplete = isDiagramDefinitionComplete(spec.definition, 'mermaid');
                const timestamp = Date.now();
                console.log(`[${timestamp}] Mermaid streaming check:`, {
                    isComplete,
                    definitionLength: spec.definition.length,
                    definitionPreview: spec.definition.substring(0, 50),
                    definitionEnd: spec.definition.substring(spec.definition.length - 20)
                });

                if (!isComplete) {
                    loadingSpinner.innerHTML = `
                        <div style="text-align: center; padding: 20px; background-color: ${isDarkMode ? '#1f1f1f' : '#f6f8fa'}; border: 1px dashed #ccc; border-radius: 4px;">
                            <p>Waiting for complete diagram definition...</p>
                        </div>
                    `;
                    return; // Exit early and wait for complete definition
                }
            }

            // Initialize mermaid with graph-specific settings

            // Pre-process the definition to fix common syntax issues
            let processedDefinition = spec.definition;

            console.log('Original definition (first 200 chars):', processedDefinition.substring(0, 200));

            // FIRST: Remove HTML tags that cause parsing issues
            processedDefinition = processedDefinition.replace(/<br\s*\/?>/gi, '\n');
            processedDefinition = processedDefinition.replace(/<\/br>/gi, '');
            processedDefinition = processedDefinition.replace(/<[^>]+>/g, '');

            // Detect diagram type
            const firstLine = processedDefinition.trim().split('\n')[0].toLowerCase();
            const diagramType = firstLine.replace(/^(\w+).*$/, '$1').toLowerCase();

            console.log('After HTML removal (first 200 chars):', processedDefinition.substring(0, 200));

            // Apply diagram-specific fixes
            if (diagramType === 'flowchart' || diagramType === 'graph' || firstLine.startsWith('flowchart ') || firstLine.startsWith('graph ')) {
                // CRITICAL: Fix parentheses and special characters in node labels
                // This must happen before other processing to prevent parsing errors
                processedDefinition = processedDefinition.replace(/(\w+)\[([^\]]*)\]/g, (match, nodeId, content) => {
                    // If content contains parentheses, slashes, or line breaks and isn't quoted, quote it
                    if (/[()\/\n<>]/.test(content) && !content.match(/^".*"$/)) {
                        // Don't double-escape already escaped quotes
                        const cleanContent = content.replace(/\\"/g, '"').replace(/"/g, '\\"');
                        return `${nodeId}["${cleanContent}"]`;
                    }
                    return match;
                });

                console.log('After parentheses fix (first 200 chars):', processedDefinition.substring(0, 200));

                // Fix subgraph class syntax
                processedDefinition = processedDefinition.replace(/class\s+(\w+)\s+subgraph-(\w+)/g, 'class $1 style_$2');
                processedDefinition = processedDefinition.replace(/classDef\s+subgraph-(\w+)/g, 'classDef style_$1');

                // Fix "Send DONE Marker" nodes
                processedDefinition = processedDefinition.replace(/\[Send\s+"DONE"\s+Marker\]/g, '[Send DONE Marker]');
                processedDefinition = processedDefinition.replace(/\[Send\s+\[DONE\]\s+Marker\]/g, '[Send DONE Marker]');

                // Fix SendDone nodes
                processedDefinition = processedDefinition.replace(/SendDone\[([^\]]+)\]/g, 'sendDoneNode["$1"]');

                // Fix end nodes that cause parsing errors - replace all 'end' node references
                processedDefinition = processedDefinition.replace(/\bend\b\s*\[/g, 'endNode[');
                processedDefinition = processedDefinition.replace(/-->\s*\bend\b/g, '--> endNode');
                processedDefinition = processedDefinition.replace(/\bend\b\s*-->/g, 'endNode -->');

                // Fix quoted text in node labels
                processedDefinition = processedDefinition.replace(/\[([^"\]]*)"([^"\]]*)"([^"\]]*)\]/g, (match, before, quoted, after) => {
                    return `[${before}${quoted}${after}]`;
                });
            }
            else if (diagramType === 'requirement') {
                // Fix requirement diagram syntax
                const lines = processedDefinition.split('\n');
                const result: string[] = [];

                for (let i = 0; i < lines.length; i++) {
                    let line = lines[i].trim();

                    // Fix ID format
                    if (line.match(/^\s*id:/i)) {
                        line = line.replace(/id:\s*([^,]+)/, 'id: "$1"');
                    }
                    console.log(`Rendering Mermaid diagram with ${spec.definition.length} chars`);

                    // Fix text format
                    if (line.match(/^\s*text:/i)) {
                        line = line.replace(/text:\s*([^,]+)/, 'text: "$1"');
                    }

                    result.push(line);
                }

                processedDefinition = result.join('\n');
            }
            else if (diagramType === 'xychart') {
                // Fix xychart array syntax
                processedDefinition = processedDefinition.replace(/\[(.*?)\]/g, '"[$1]"');
            }

            // Fix quoted text in node labels for all diagram types
            processedDefinition = processedDefinition.replace(/\[([^"\]]*)"([^"\]]*)"([^"\]]*)\]/g, (match, before, quoted, after) => {
                return `[${before}${quoted}${after}]`;
            });

            mermaid.initialize({
                startOnLoad: false,
                theme: isDarkMode ? 'dark' : 'default',
                securityLevel: 'loose',
                fontFamily: '"Arial", sans-serif',
                fontSize: 14,
                themeVariables: isDarkMode ? {
                    // High contrast dark theme
                    primaryColor: '#88c0d0',
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
            console.log(`Attempting to render mermaid with ID: ${mermaidId}`);

            let svg: string;
            try {
                const result = await mermaid.render(mermaidId, processedDefinition);
                
                // Check if result is valid
                if (!result || typeof result !== 'object') {
                    console.error('Invalid mermaid render result:', result);
                    throw new Error('Mermaid render returned invalid result');
                }
                console.log('Mermaid render result:', result);
                svg = result.svg;
                renderSuccessful = true;
                console.log(`Mermaid render successful, got SVG of length: ${svg.length}`);

                renderSuccessful = true;
                // Check if we got a valid SVG
                if (!svg || svg.trim() === '') {
                    console.error('Empty SVG returned from Mermaid render for definition:', processedDefinition.substring(0, 200));
                    throw new Error('Failed to get SVG element');
                }

                // Check if the SVG contains an error message
                if (svg.includes('syntax error') || svg.includes('Parse error')) {
                    throw new Error('Mermaid syntax error in diagram');
                }
            } catch (renderError) {
                console.error('Error rendering mermaid diagram:', renderError);
                
                // Log the specific error details
                if (renderError instanceof Error) {
                    console.error('Error name:', renderError.name);
                    console.error('Error message:', renderError.message);
                    console.error('Error stack:', renderError.stack);
                }
                
                console.error('Failed definition (first 500 chars):', processedDefinition.substring(0, 500));
                console.error('Processed definition that failed:', processedDefinition.substring(0, 500));
                throw renderError;
            }

            // Add the animation keyframes if they don't exist yet
            if (!document.querySelector('#mermaid-spinner-keyframes')) {
                const keyframes = document.createElement('style');
                keyframes.id = 'mermaid-spinner-keyframes';
                keyframes.textContent = `
                    @keyframes mermaid-spin {
                        0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); }
                    }
                `;
                document.head.appendChild(keyframes);
            }

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

            // Remove the loading spinner
            try {
                if (loadingSpinner && loadingSpinner.parentNode === container) {
                    container.removeChild(loadingSpinner);
                }
            } catch (e) {
                console.warn('Could not remove loading spinner (this is normal for multiple renders):', e instanceof Error ? e.message : String(e));
            }

            // Add wrapper to container
            container.appendChild(wrapper);

            if (!renderSuccessful) return;

            // Get the SVG element after it's in the DOM
            const svgElement = wrapper.querySelector('svg');
            if (!svgElement) {
                throw new Error('Failed to get SVG element after rendering');
            }

            // Helper function to apply custom styles from the diagram definition
            const applyCustomStyles = (svgElement: SVGElement) => {
                // Process all style directives in the SVG
                const styleElements = svgElement.querySelectorAll('style');
                styleElements.forEach(styleEl => {
                    // Extract class names and their style definitions
                    const styleText = styleEl.textContent || '';
                    const styleRules = styleText.match(/\.(\w+)\s*{([^}]*)}/g) || [];

                    styleRules.forEach(rule => {
                        // Apply these styles directly to the elements with matching classes
                        const classMatch = rule.match(/\.(\w+)\s*{/);
                        const styleMatch = rule.match(/{([^}]*)}/);

                        if (classMatch && styleMatch) {
                            const className = classMatch[1];
                            const styles = styleMatch[1].trim();

                            // Find elements with this class and apply styles directly
                            svgElement.querySelectorAll(`.${className}`).forEach(el => {
                                // Parse individual style properties
                                styles.split(';').forEach(style => {
                                    const [prop, value] = style.split(':').map(s => s.trim());
                                    if (prop && value) {
                                        (el as SVGElement).style.setProperty(prop, value);
                                    }
                                });
                            });
                        }
                    });
                });
            };

            // Enhanced function to improve text visibility in dark mode
            const enhanceDarkModeTextVisibility = (svgElement: SVGElement) => {
                // Get all text elements
                const textElements = svgElement.querySelectorAll('text');
                
                textElements.forEach(textEl => {
                    // Find the parent node to get its styling context
                    let parentNode = textEl.parentElement;
                    while (parentNode && !parentNode.classList.contains('node') && !parentNode.classList.contains('cluster')) {
                        parentNode = parentNode.parentElement;
                    }
                    
                    if (parentNode) {
                        // Look for shape elements (rect, circle, polygon, path) in the parent
                        const shapeElement = parentNode.querySelector('rect, circle, polygon, path');
                        if (shapeElement) {
                            const stroke = shapeElement.getAttribute('stroke');
                            const fill = shapeElement.getAttribute('fill');
                            
                            // If we have a stroke color, use it for text (it's usually darker/more saturated)
                            if (stroke && stroke !== 'none' && stroke !== '#333' && stroke !== '#333333') {
                                textEl.setAttribute('fill', stroke);
                            } else if (fill && fill !== 'none') {
                                // If no good stroke, derive optimal contrasting color from fill
                                const contrastColor = getTextContrastColor(fill);
                                textEl.setAttribute('fill', contrastColor);
                            } else {
                                // Fallback to high contrast color
                                textEl.setAttribute('fill', '#000000');
                            }
                        }
                    }
                });
                
                // Special handling for edge labels and other floating text
                svgElement.querySelectorAll('.edgeLabel text').forEach(textEl => {
                    const currentFill = textEl.getAttribute('fill');
                    // If text is white or very light, make it more visible
                    if (!currentFill || currentFill === '#ffffff' || currentFill === 'white' || currentFill === '#eceff4') {
                        textEl.setAttribute('fill', '#000000');
                    }
                });

                // Handle gantt chart and other diagram text that might be on colored backgrounds
                svgElement.querySelectorAll('text').forEach(textEl => {
                    const parentRect = textEl.closest('g')?.querySelector('rect');
                    if (parentRect) {
                        const bgColor = parentRect.getAttribute('fill');
                        if (bgColor && bgColor !== 'none') {
                            const optimalColor = getOptimalTextColor(bgColor);
                            textEl.setAttribute('fill', optimalColor);
                        }
                    }
                });
            };

            // Enhance dark theme visibility for specific elements
            if (isDarkMode && svgElement) {
                requestAnimationFrame(() => {
                    // Enhance specific elements that might still have poor contrast
                    svgElement.querySelectorAll('.edgePath path').forEach(el => {
                        el.setAttribute('stroke', '#88c0d0');
                        el.setAttribute('stroke-width', '1.5px');
                    });

                    // Fix for arrow markers in dark mode
                    svgElement.querySelectorAll('defs marker path').forEach(el => {
                        el.setAttribute('stroke', '#88c0d0');
                        el.setAttribute('fill', '#88c0d0');
                    });

                    // Fix for all SVG paths and lines
                    svgElement.querySelectorAll('line, path:not([fill])').forEach(el => {
                        el.setAttribute('stroke', '#88c0d0');
                        el.setAttribute('stroke-width', '1.5px');
                    });

                    // Apply enhanced text visibility improvements
                    enhanceDarkModeTextVisibility(svgElement);
                    
                    svgElement.querySelectorAll('path.path, path.messageText, .flowchart-link').forEach(el => {
                        el.setAttribute('stroke', '#88c0d0');
                        el.setAttribute('stroke-width', '1.5px');
                    });

                    svgElement.querySelectorAll('.node rect, .node circle, .node polygon, .node path').forEach(el => {
                        el.setAttribute('stroke', '#81a1c1');
                        el.setAttribute('fill', '#5e81ac');
                    });

                    svgElement.querySelectorAll('.cluster rect').forEach(el => {
                        el.setAttribute('stroke', '#81a1c1');
                        el.setAttribute('fill', '#4c566a');
                    });

                    // Apply custom styles from the diagram definition
                    applyCustomStyles(svgElement);
                });
            } else {
                // Even in light mode, apply custom styles
                applyCustomStyles(svgElement);
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
                const finalScale = Math.min(scale, SCALE_CONFIG.MAX_SCALE);
                svgElement.style.transform = `scale(${finalScale})`;
                svgElement.style.transformOrigin = 'center center';
                svgElement.style.width = '100%';
                svgElement.style.height = 'auto';

                // Override any existing transform scale in the SVG's style attribute
                const svgStyleAttr = svgElement.getAttribute('style') || '';
                if (svgStyleAttr.includes('transform: scale')) {
                    // Remove any transform scale from the style attribute
                    const newStyleAttr = svgStyleAttr.replace(/transform:\s*scale\([^)]+\);?/g, '');
                    svgElement.setAttribute('style', newStyleAttr);
                    // Re-apply our controlled scale
                    svgElement.style.transform = `scale(${finalScale})`;
                }
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
                        :root {
                            --bg-color: #f8f9fa;
                            --text-color: #212529;
                            --toolbar-bg: #f1f3f5;
                            --toolbar-border: #dee2e6;
                            --button-bg: #4361ee;
                            --button-hover: #3a0ca3;
                        }
                        
                        [data-theme="dark"] {
                            --bg-color: #212529;
                            --text-color: #f8f9fa;
                            --toolbar-bg: #343a40;
                            --toolbar-border: #495057;
                            --button-bg: #4361ee;
                            --button-hover: #5a72f0;
                        }
                        
                        body {
                            margin: 0;
                            padding: 0;
                            display: flex;
                            flex-direction: column;
                            height: 100vh;
                            background-color: var(--bg-color);
                            color: var(--text-color);
                            font-family: system-ui, -apple-system, sans-serif;
                            transition: background-color 0.3s ease, color 0.3s ease;
                        }
                        
                        .toolbar {
                            background-color: var(--toolbar-bg);
                            border-bottom: 1px solid var(--toolbar-border);
                            padding: 8px;
                            display: flex;
                            justify-content: space-between;
                            align-items: center;
                            transition: background-color 0.3s ease, border-color 0.3s ease;
                        }
                        
                        .toolbar button {
                            background-color: var(--button-bg);
                            color: white;
                            border: none;
                            border-radius: 4px;
                            padding: 6px 12px;
                            cursor: pointer;
                            margin-right: 8px;
                            font-size: 14px;
                            transition: background-color 0.3s ease;
                        }
                        
                        .toolbar button:hover {
                            background-color: var(--button-hover);
                        }
                        
                        .theme-toggle {
                            background-color: transparent;
                            border: 1px solid var(--text-color);
                            color: var(--text-color);
                            padding: 4px 8px;
                            font-size: 12px;
                        }
                        
                        .theme-toggle:hover {
                            background-color: var(--text-color);
                            color: var(--bg-color);
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
                            transition: all 0.3s ease;
                        }
                    </style>
                </head>
                <body data-theme="light">
                    <div class="toolbar">
                        <div>
                            <button onclick="zoomIn()">Zoom In</button>
                            <button onclick="zoomOut()">Zoom Out</button>
                            <button onclick="resetZoom()">Reset</button>
                            <button class="theme-toggle" onclick="toggleTheme()">🌙 Dark</button>
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
                        let isDarkMode = false;
                        
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
                        
                        function toggleTheme() {
                            isDarkMode = !isDarkMode;
                            const body = document.body;
                            const themeButton = document.querySelector('.theme-toggle');
                            
                            if (isDarkMode) {
                                body.setAttribute('data-theme', 'dark');
                                themeButton.textContent = '☀️ Light';
                            } else {
                                body.setAttribute('data-theme', 'light');
                                themeButton.textContent = '🌙 Dark';
                            }
                            
                            // Re-render Mermaid diagram with new theme
                            reRenderMermaidDiagram();
                        }
                        
                        function reRenderMermaidDiagram() {
                            const svgContainer = document.getElementById('svg-container');
                            const currentSvg = svgContainer.querySelector('svg');
                            
                            if (!currentSvg) return;
                            
                            // Apply Mermaid-specific theme styling
                            applyMermaidTheme(currentSvg, isDarkMode);
                        }
                        
                        function applyMermaidTheme(svgElement, isDark) {
                            const darkTheme = {
                                primaryColor: '#88c0d0',
                                primaryTextColor: '#ffffff',
                                primaryBorderColor: '#88c0d0',
                                lineColor: '#88c0d0',
                                secondaryColor: '#5e81ac',
                                tertiaryColor: '#2e3440',
                                textColor: '#eceff4',
                                mainBkg: '#3b4252',
                                secondBkg: '#434c5e',
                                nodeBorder: '#88c0d0',
                                edgeLabelBackground: '#4c566a',
                                altBackground: '#2e3440',
                                nodeBkg: '#3b4252',
                                clusterBkg: '#2e3440'
                            };
                            
                            const lightTheme = {
                                primaryColor: '#1890ff',
                                primaryTextColor: '#000000',
                                primaryBorderColor: '#1890ff',
                                lineColor: '#333333',
                                secondaryColor: '#f0f0f0',
                                tertiaryColor: '#ffffff',
                                textColor: '#333333',
                                mainBkg: '#ffffff',
                                secondBkg: '#f8f9fa',
                                nodeBorder: '#cccccc',
                                edgeLabelBackground: '#ffffff',
                                altBackground: '#f5f5f5',
                                nodeBkg: '#ffffff',
                                clusterBkg: '#f8f9fa'
                            };
                            
                            const colors = isDark ? darkTheme : lightTheme;
                            
                            // Apply theme to Mermaid elements
                            svgElement.querySelectorAll('.edgePath path').forEach(el => {
                                el.setAttribute('stroke', colors.lineColor);
                                el.setAttribute('stroke-width', '1.5px');
                            });
                            
                            svgElement.querySelectorAll('defs marker path').forEach(el => {
                                el.setAttribute('stroke', colors.lineColor);
                                el.setAttribute('fill', colors.lineColor);
                            });
                            
                            svgElement.querySelectorAll('.node rect, .node circle, .node polygon, .node path').forEach(el => {
                                el.setAttribute('stroke', colors.nodeBorder);
                                el.setAttribute('fill', colors.nodeBkg);
                            });
                            
                            svgElement.querySelectorAll('.cluster rect').forEach(el => {
                                el.setAttribute('stroke', colors.nodeBorder);
                                el.setAttribute('fill', colors.clusterBkg);
                            });
                            
                            // Text styling - node labels should contrast with node background
                            svgElement.querySelectorAll('.node .label text, .cluster .label text').forEach(el => {
                                el.setAttribute('fill', isDark ? '#000000' : '#333333');
                            });
                            
                            // Edge labels and other text should use theme text color
                            svgElement.querySelectorAll('.edgeLabel text, text:not(.node .label text):not(.cluster .label text)').forEach(el => {
                                el.setAttribute('fill', colors.textColor);
                            });
                            
                            // Flow chart links
                            svgElement.querySelectorAll('.flowchart-link, path.path, path.messageText').forEach(el => {
                                el.setAttribute('stroke', colors.lineColor);
                                el.setAttribute('stroke-width', '1.5px');
                            });
                        }
                        
                        function downloadSvg() {
                            const svgData = new XMLSerializer().serializeToString(svg);
                            const svgBlob = new Blob([svgData], {type: 'image/svg+xml'});
                            const url = URL.createObjectURL(svgBlob);
                            
                            const link = document.createElement('a');
                            link.href = url;
                            link.download = 'mermaid-diagram-${Date.now()}.svg';
                            document.body.appendChild(link);
                            link.click();
                            document.body.removeChild(link);
                            
                            setTimeout(() => URL.revokeObjectURL(url), 1000);
                        }
                        
                        // Initialize theme based on system preference
                        if (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) {
                            toggleTheme();
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

            // Remove any loading spinner if it exists
            const spinner = container.querySelector('.mermaid-loading-spinner');
            if (spinner) {
                container.removeChild(spinner);
            }

            // Clear container before showing error
            if (container.innerHTML.includes('Rendering diagram')) {
                container.innerHTML = '';
            }

            if (!spec.isStreaming || spec.forceRender) container.innerHTML = `
                <div class="mermaid-error">
                    <strong>Mermaid Error:</strong>
                    <p>There was an error rendering the diagram. This is often due to syntax issues in the Mermaid definition.</p>
                    <pre>${error.message || 'Unknown error'}</pre>
                    <details>
                        <summary>Show Definition</summary>
                        <pre><code>${spec.definition}</code></pre>
                    </details>
                </div>
            `;

            // Add view source button
            const viewSourceButton = document.createElement('button');
            viewSourceButton.innerHTML = '📝 View Source';
            viewSourceButton.className = 'diagram-action-button mermaid-source-button';
            viewSourceButton.style.cssText = `
                background-color: #4361ee;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 8px 16px;
                margin: 10px 5px;
                cursor: pointer;
                display: inline-block;
            `;

            // Add retry button
            const retryButton = document.createElement('button');
            retryButton.innerHTML = '🔄 Retry Rendering';
            retryButton.className = 'diagram-action-button mermaid-retry-button';
            retryButton.style.cssText = `
                background-color: #4361ee;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 8px 16px;
                margin: 10px auto;
                cursor: pointer;
                display: block;
            `;

            // Create button container
            const buttonContainer = document.createElement('div');
            buttonContainer.style.textAlign = 'center';
            buttonContainer.appendChild(viewSourceButton);
            buttonContainer.appendChild(retryButton);
            container.appendChild(buttonContainer);

            // Add event listeners
            retryButton.onclick = () => mermaidPlugin.render(container, d3, spec, isDarkMode);
            viewSourceButton.onclick = () => {
                // Toggle between error view and source view
                const errorView = container.querySelector('.mermaid-error');
                if (errorView) {
                    // Create source view
                    container.innerHTML = `<pre style="
                        background-color: ${isDarkMode ? '#1f1f1f' : '#f6f8fa'};
                        padding: 16px;
                        border-radius: 4px;
                        overflow: auto;
                        color: ${isDarkMode ? '#e6e6e6' : '#24292e'};
                    "><code>${spec.definition}</code></pre>`;

                    // Add back button
                    const backButton = document.createElement('button');
                    backButton.innerHTML = '⬅️ Back to Error';
                    backButton.className = 'diagram-action-button';
                    backButton.style.cssText = `
                        background-color: #4361ee;
                        color: white;
                        border: none;
                        border-radius: 4px;
                        padding: 8px 16px;
                        margin: 10px auto;
                        cursor: pointer;
                        display: block;
                    `;
                    backButton.onclick = () => mermaidPlugin.render(container, d3, spec, isDarkMode);
                    container.appendChild(backButton);
                }
            };
        }
    }
};

/**
 * Get a contrasting text color based on background color
 * @param backgroundColor - The background color (hex) 
 * @returns - A contrasting color for text
 */
function getTextContrastColor(backgroundColor: string): string {
    // Convert hex to RGB
    const hexToRgb = (hex: string) => {
        const result = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex);
        return result ? {
            r: parseInt(result[1], 16),
            g: parseInt(result[2], 16),
            b: parseInt(result[3], 16)
        } : null;
    };

    const rgb = hexToRgb(backgroundColor);
    if (!rgb) return '#000000';

    // Special handling for yellow and yellow-ish colors
    // Yellow has high luminance but white text on yellow is terrible
    if (rgb.r > 200 && rgb.g > 200 && rgb.b < 100) {
        return '#000000'; // Always use black on yellow/yellow-ish
    }

    // Special handling for beige/cream colors (high R, G, moderate B)
    if (rgb.r > 220 && rgb.g > 200 && rgb.b > 150) {
        return '#000000'; // Always use black on beige/cream
    }

    // Calculate relative luminance using proper sRGB formula
    const luminance = (0.299 * rgb.r + 0.587 * rgb.g + 0.114 * rgb.b) / 255;
    
    // Use a more conservative threshold - prefer black text unless background is quite dark
    return luminance > 0.4 ? '#000000' : '#ffffff';
}

/**
 * Get optimal text color based on background color with special handling for problematic colors
 * @param backgroundColor - The background color (hex)
 * @returns - The best contrasting text color
 */
function getOptimalTextColor(backgroundColor: string): string {
    // Convert hex to RGB
    const hexToRgb = (hex: string) => {
        const result = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex);
        return result ? {
            r: parseInt(result[1], 16),
            g: parseInt(result[2], 16),
            b: parseInt(result[3], 16)
        } : null;
    };

    const rgb = hexToRgb(backgroundColor);
    if (!rgb) return '#000000';

    // Special handling for yellow and yellow-ish colors
    if (rgb.r > 200 && rgb.g > 200 && rgb.b < 100) {
        return '#000000'; // Always use black on yellow/yellow-ish
    }

    // Special handling for beige/cream colors
    if (rgb.r > 220 && rgb.g > 200 && rgb.b > 150) {
        return '#000000'; // Always use black on beige/cream
    }

    // Calculate relative luminance and use conservative threshold
    const luminance = (0.299 * rgb.r + 0.587 * rgb.g + 0.114 * rgb.b) / 255;
    return luminance > 0.4 ? '#000000' : '#ffffff';
}
