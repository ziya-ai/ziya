import mermaid from 'mermaid';
import { D3RenderPlugin } from '../../types/d3';
import initMermaidSupport from './mermaidEnhancer';
import { isDiagramDefinitionComplete } from '../../utils/diagramUtils';
import { extractDefinitionFromYAML } from '../../utils/diagramUtils';

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
    isMarkdownBlockClosed?: boolean;
    forceRender?: boolean;
    definition: string;
    theme?: 'default' | 'dark' | 'neutral' | 'forest'; // Optional theme override
}

// Type guard to check if a spec is for Mermaid
const isMermaidSpec = (spec: any): spec is MermaidSpec => {
    // Handle JSON-wrapped mermaid specs
    if (typeof spec === 'object' && spec !== null && spec.type === 'mermaid' && spec.definition) {
        return typeof spec.definition === 'string' && spec.definition.trim().length > 0;
    }
    
    // Handle direct mermaid spec objects
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

// Global render queue to serialize Mermaid rendering and prevent conflicts
class MermaidRenderQueue {
    private queue: Array<() => Promise<any>> = [];
    private isProcessing = false;

    async enqueue<T>(renderFn: () => Promise<T>): Promise<T> {
        return new Promise((resolve, reject) => {
            this.queue.push(async () => {
                try {
                    const result = await renderFn();
                    resolve(result);
                } catch (error) {
                    reject(error);
                }
            });

            this.processQueue();
        });
    }

    private async processQueue() {
        if (this.isProcessing || this.queue.length === 0) return;

        this.isProcessing = true;
        const renderFn = this.queue.shift()!;
        await renderFn();
        this.isProcessing = false;

        this.processQueue(); // Process next item
    }
}

const renderQueue = new MermaidRenderQueue();

// Initialize Mermaid support with preprocessing and error handling
initMermaidSupport(mermaid);

// Also ensure window.mermaid is enhanced if it exists
if (typeof window !== 'undefined' && window.mermaid) {
    initMermaidSupport(window.mermaid);
}

export const mermaidPlugin: D3RenderPlugin = {
    name: 'mermaid-renderer',
    priority: 5,
    sizingConfig: {
        sizingStrategy: 'auto-expand',
        needsDynamicHeight: true,
        needsOverflowVisible: true,
        observeResize: true,
        containerStyles: {
            width: '100%',
            height: 'auto',
            overflow: 'visible'
        }
    },

    canHandle: (spec: any): boolean => {
        // Handle JSON-wrapped mermaid specs like {"type": "mermaid", "definition": "..."}
        if (typeof spec === 'object' && spec !== null && spec.type === 'mermaid' && spec.definition) {
            return typeof spec.definition === 'string' && spec.definition.trim().length > 0;
        }
        
        // Handle direct mermaid spec objects
        if (isMermaidSpec(spec)) {
            return true;
        }
        
        return false;
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
        // Use render queue to serialize all Mermaid operations
        return renderQueue.enqueue(async () => {
            return await renderSingleDiagram(container, d3, spec, isDarkMode);
        });
    }
};

async function renderSingleDiagram(container: HTMLElement, d3: any, spec: MermaidSpec, isDarkMode: boolean): Promise<void> {
    console.log(`🎯 MERMAID SINGLE RENDER with spec:`, spec);
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

        // This allows the content to display as highlighted code during streaming
        if (spec.isStreaming && !spec.isMarkdownBlockClosed && !spec.forceRender) {
            console.log('Mermaid: Markdown block still open, letting content display as code');
            // Don't show a waiting message - let the markdown renderer show the code
            // Just remove the loading spinner and return
            try {
                container.innerHTML = '';
            } catch (e) {
                console.warn('Could not remove loading spinner:', e);
            }
            return; // Exit early - let markdown renderer handle the streaming content
        }

        // Only proceed with rendering when we have a complete definition
        if (!spec.definition || spec.definition.trim().length < 10) {
            console.log('Mermaid: Definition too short, waiting for more content');
            return; // Exit early and wait for complete definition
        }
        // Initialize mermaid with graph-specific settings

        // Extract actual content from YAML wrapper if present, but don't do other preprocessing
        // The enhanced render function will handle all preprocessing
        let rawDefinition: string;
        
        // Handle JSON-wrapped specs vs direct definition strings
        if (typeof spec === 'object' && spec.definition) {
            rawDefinition = extractDefinitionFromYAML(spec.definition, 'mermaid');
        } else if (typeof spec === 'string') {
            rawDefinition = extractDefinitionFromYAML(spec, 'mermaid');
        } else {
            throw new Error('Invalid mermaid spec: no definition found');
        }
        
        console.log('Raw definition (first 200 chars):', rawDefinition.substring(0, 200));

        // Detect diagram type
        const firstLine = rawDefinition.trim().split('\n')[0].toLowerCase();
        const diagramType = firstLine.replace(/^(\w+).*$/, '$1').toLowerCase();

        // Create a guaranteed unique ID that won't conflict with other diagrams
        const containerId = container.id || container.className || 'mermaid';
        const mermaidId = `${containerId}-${Date.now()}-${Math.random().toString(16).substring(2, 10)}`;

        mermaid.initialize({
            startOnLoad: false,
            theme: isDarkMode ? 'dark' : 'default',
            securityLevel: 'loose',
            fontFamily: '"Arial", sans-serif',
            fontSize: 14,
            themeVariables: isDarkMode ? {
                // High contrast dark theme
                primaryColor: '#88c0d0',
                primaryTextColor: '#000000', // Use black text by default
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
        console.log(`Attempting to render mermaid with ID: ${mermaidId}`);

        let svg: string;
        let renderError: Error | null = null;
        try {
            const result = await mermaid.render(mermaidId, rawDefinition);

            // Check if result is valid
            if (!result || typeof result !== 'object') {
                console.error('Invalid mermaid render result:', result);
                throw new Error('Mermaid render returned invalid result');
            }
            console.log('Mermaid render result:', result);
            svg = result.svg;

            // Check if we got a valid SVG
            if (!svg || svg.trim() === '') {
                console.error('Empty SVG returned from Mermaid render - likely parse error');
                console.error('Definition that failed (first 500 chars):', rawDefinition.substring(0, 500));

                // Check if there was a parsing error that Mermaid swallowed
                // This is a common issue where Mermaid returns empty result instead of throwing
                throw new Error('Mermaid parsing failed - empty SVG returned. This usually indicates syntax errors in the diagram definition.');
            }

            // Additional validation - check if SVG contains actual content
            if (svg.length < 100 || !svg.includes('<svg')) {
                console.error('SVG appears to be malformed or too short:', svg.substring(0, 200));
                throw new Error('Mermaid returned malformed SVG - likely parsing error');
            }

            renderSuccessful = true;
            console.log(`Mermaid render successful, got SVG of length: ${svg.length}`);

            // Check if the SVG contains an error message
            if (svg.includes('syntax error') || svg.includes('Parse error')) {
                throw new Error('Mermaid syntax error in diagram');
            }
        } catch (renderError) {
            console.error('Error rendering mermaid diagram:', renderError);

            // Store the error for better error reporting
            renderError = renderError instanceof Error ? renderError : new Error(String(renderError));

            // Log the specific error details
            if (renderError instanceof Error) {
                console.error('Error name:', renderError.name);
                console.error('Error message:', renderError.message);
                console.error('Error stack:', renderError.stack);
            }

            console.error('Failed definition (first 500 chars):', rawDefinition.substring(0, 500));
            console.error('Raw definition that failed:', rawDefinition.substring(0, 500));
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

        // Helper function to detect light backgrounds
        const isLightBackground = (color: string): boolean => {
            if (!color || color === 'none' || color === 'transparent') return false;
            
            // Parse color to RGB values
            let r = 0, g = 0, b = 0;
            
            // Handle hex format (#ff9999)
            const hexMatch = color.match(/^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i);
            if (hexMatch) {
                r = parseInt(hexMatch[1], 16);
                g = parseInt(hexMatch[2], 16);
                b = parseInt(hexMatch[3], 16);
            }
            // Handle rgb() format (rgb(255, 153, 153))
            else {
                const rgbMatch = color.match(/rgb\((\d+),\s*(\d+),\s*(\d+)\)/);
                if (rgbMatch) {
                    r = parseInt(rgbMatch[1]);
                    g = parseInt(rgbMatch[2]);
                    b = parseInt(rgbMatch[3]);
                } else {
                    return false;
                }
            }
            
            // Calculate relative luminance using proper sRGB formula
            const getLuminanceComponent = (colorValue: number) => {
                const normalized = colorValue / 255;
                return normalized <= 0.03928 
                    ? normalized / 12.92 
                    : Math.pow((normalized + 0.055) / 1.055, 2.4);
            };
            
            const rLum = getLuminanceComponent(r);
            const gLum = getLuminanceComponent(g);
            const bLum = getLuminanceComponent(b);
            
            const luminance = 0.2126 * rLum + 0.7152 * gLum + 0.0722 * bLum;
            
            
            // Use a threshold where anything above 0.4 luminance is considered light
            return luminance > 0.4;
        };
        
        // Enhanced function to improve text visibility in dark mode
        const fixTextVisibilityForClassDef = (svgElement: SVGElement) => {
            if (isDarkMode) {
                console.log('🔍 DEBUG: fixTextVisibilityForClassDef starting in dark mode');
            }
            console.log('🔍 FIXING TEXT VISIBILITY: Starting classDef text visibility fix');
            
            // Find all text elements
            const textElements = svgElement.querySelectorAll('text');
            console.log(`Found ${textElements.length} text elements to process`);
            
            textElements.forEach(textEl => {
                const textContent = textEl.textContent?.trim();
                if (!textContent) return;

                // Look for the parent node/cluster group
                let parentGroup = textEl.closest('g.node, g.cluster');
                if (!parentGroup) {
                    // Fallback: check if parent has any background shapes
                    parentGroup = textEl.parentElement;
                }

                if (parentGroup) {
                    // Find any background shape in this group
                    const backgroundShape = parentGroup.querySelector('rect, polygon, circle, path');
                    if (backgroundShape) {
                        const fill = backgroundShape.getAttribute('fill');
                        console.log(`Text "${textContent}" has background fill: ${fill}`);
                        const currentTextFill = textEl.getAttribute('fill');
                        console.log(`Text "${textContent}" current fill: ${currentTextFill}`);
                        
                        if (fill && isLightBackground(fill)) {
                            textEl.setAttribute('fill', '#000000');
                            (textEl as SVGElement).style.fill = '#000000';
                        }
                    }
                }
            });
        };
        
        // Enhanced function to improve text visibility in dark mode
        const enhanceDarkModeTextVisibility = (svgElement: SVGElement) => {
            if (isDarkMode) {
            }
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
                            console.log(`🔧 DEBUG: Using stroke color ${stroke} for text: "${textEl.textContent}"`);
                            textEl.setAttribute('fill', stroke);
                        } else if (fill && fill !== 'none') {
                            const currentFill = textEl.getAttribute('fill');
                            console.log(`🔧 DEBUG: Text "${textEl.textContent}" - current: ${currentFill}, background: ${fill}`);
                            // If no good stroke, derive optimal contrasting color from fill
                            const contrastColor = getTextContrastColor(fill);
                            console.log(`🔧 DEBUG: Calculated contrast color: ${contrastColor} for background: ${fill}`);
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
            });

            // Special handling for Gantt charts - fix text visibility with proper contrast
            const fixGanttTextContrast = (textEl: Element) => {
                const svgTextEl = textEl as SVGElement;
                
                // Find the background color by looking at parent elements or sibling shapes
                let backgroundColor = '#ffffff'; // Default to white
                
                // Check parent group for background rectangles
                const parentGroup = textEl.closest('g');
                if (parentGroup) {
                    const backgroundRect = parentGroup.querySelector('rect');
                    if (backgroundRect) {
                        const fill = backgroundRect.getAttribute('fill');
                        const computedFill = window.getComputedStyle(backgroundRect).fill;
                        backgroundColor = fill || computedFill || backgroundColor;
                    }
                }
                
                // Get optimal contrasting color
                const textColor = getOptimalTextColor(backgroundColor);
                
                svgTextEl.setAttribute('fill', textColor);
                svgTextEl.style.setProperty('fill', textColor, 'important');
            };
            
            // Apply contrast fixes to Gantt-specific elements
            svgElement.querySelectorAll('.section0, .section1, .section2, .section3').forEach(fixGanttTextContrast);
            svgElement.querySelectorAll('g.tick text, .taskText, .sectionTitle, .grid .tick text').forEach(fixGanttTextContrast);
            
            // Handle axis text and dates specifically
            svgElement.querySelectorAll('text').forEach(textEl => {
                if (textEl.textContent?.match(/\d{4}-\d{2}-\d{2}/) || textEl.closest('.grid')) {
                    fixGanttTextContrast(textEl);
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
                
                // Apply classDef text visibility fixes
                fixTextVisibilityForClassDef(svgElement);

                svgElement.querySelectorAll('path.path, path.messageText, .flowchart-link').forEach(el => {
                    el.setAttribute('stroke', '#88c0d0');
                    el.setAttribute('stroke-width', '1.5px');
                });

                // Additional fix: Force text color on nodes with light backgrounds
                svgElement.querySelectorAll('g.node text, g.cluster text').forEach(textEl => {
                    const parentGroup = textEl.closest('g.node, g.cluster');
                    if (parentGroup) {
                        const rect = parentGroup.querySelector('rect, polygon, circle');
                        if (rect) {
                            const fill = rect.getAttribute('fill');
                            if (fill && isLightBackground(fill)) {
                                textEl.setAttribute('fill', '#000000');
                                (textEl as SVGElement).style.fill = '#000000';
                            }
                        }
                    }
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
            requestAnimationFrame(() => {
                // Apply classDef text visibility fixes even in light mode
                fixTextVisibilityForClassDef(svgElement);
                // Apply custom styles from the diagram definition
                applyCustomStyles(svgElement);
            });
            applyCustomStyles(svgElement);
        }
        // CRITICAL: Add a delayed fix to ensure text visibility is applied after all other processing
        setTimeout(() => {
            console.log('🔍 DELAYED TEXT FIX: Applying final text visibility fixes');
            
            // SIMPLE APPROACH: Find all light-colored rectangles and fix text within them
            const allRects = svgElement.querySelectorAll('rect');
            console.log(`🔧 SIMPLE-FIX: Found ${allRects.length} rectangles to check`);
            
            allRects.forEach((rect, index) => {
                const fill = rect.getAttribute('fill');
                const computedFill = window.getComputedStyle(rect).fill;
                const actualColor = computedFill !== 'none' && computedFill !== 'rgb(0, 0, 0)' ? computedFill : fill;
                
                if (actualColor && isLightBackground(actualColor)) {
                    console.log(`🔧 SIMPLE-FIX: Found light background rect ${index}: ${actualColor}`);
                    
                    // Find the parent group and fix all text within it
                    const parentGroup = rect.closest('g');
                    if (parentGroup) {
                        const textElements = parentGroup.querySelectorAll('div, span');
                        console.log(`🔧 SIMPLE-FIX: Found ${textElements.length} text elements in this group`);
                        
                        textElements.forEach(textEl => {
                            console.log(`🔧 SIMPLE-FIX: Setting black text for "${textEl.textContent}" on light background ${actualColor}`);
                            (textEl as HTMLElement).style.setProperty('color', '#000000', 'important');
                        });
                    }
                }
            });
            
            const allTextElements = svgElement.querySelectorAll('text');
            allTextElements.forEach(textEl => {
                // Special handling for Gantt charts with proper contrast detection
                const isGanttText = textEl.closest('.grid') || 
                                  textEl.textContent?.match(/\d{4}-\d{2}-\d{2}/) ||
                                  ['section0', 'section1', 'section2', 'section3'].some(cls => 
                                      textEl.classList.contains(cls) || textEl.parentElement?.classList.contains(cls));
                
                if (isGanttText) {
                    // Find background and set appropriate contrast
                    let bgColor = '#ffffff';
                    const parentGroup = textEl.closest('g');
                    if (parentGroup) {
                        const rect = parentGroup.querySelector('rect');
                        bgColor = rect?.getAttribute('fill') || window.getComputedStyle(rect || textEl).backgroundColor || bgColor;
                    }
                    const contrastColor = getOptimalTextColor(bgColor);
                    textEl.setAttribute('fill', contrastColor);
                    (textEl as SVGElement).style.setProperty('fill', contrastColor, 'important');
                    return; // Skip further processing for Gantt text
                }
                
                const parentGroup = textEl.closest('g.node, g.cluster');
                if (parentGroup) {
                    const backgroundShape = parentGroup.querySelector('rect, polygon, circle, path');
                    if (backgroundShape) {
                        const fill = backgroundShape.getAttribute('fill');
                        if (fill && isLightBackground(fill)) {
                            console.log(`🔧 DELAYED FIX: Setting black text for light background ${fill}`);
                            textEl.setAttribute('fill', '#000000');
                            console.log(`🔧 DEBUG: Final text color set to black for "${textEl.textContent}" on ${fill}`);
                            (textEl as SVGElement).style.setProperty('fill', '#000000', 'important');
                        }
                    }
                }
            });
        }, 500);

        // Helper functions for contrast calculation
        const getColorLuminance = (color: string): number => {
            const rgb = color.match(/\d+/g);
            if (!rgb || rgb.length < 3) return 0.5;
            const [r, g, b] = rgb.map(x => {
                const val = parseInt(x) / 255;
                return val <= 0.03928 ? val / 12.92 : Math.pow((val + 0.055) / 1.055, 2.4);
            });
            return 0.2126 * r + 0.7152 * g + 0.0722 * b;
        };

        const calculateContrastRatio = (textColor: string, backgroundColor: string): number => {
            const textLum = getColorLuminance(textColor);
            const bgLum = getColorLuminance(backgroundColor);
            const lighter = Math.max(textLum, bgLum);
            const darker = Math.min(textLum, bgLum);
            return (lighter + 0.05) / (darker + 0.05);
        };

        // TEXT VISIBILITY FIX - Focus on foreignObject children only
        setTimeout(() => {
            console.log('🔍 TEXT VISIBILITY FIX: Starting analysis');
            const foreignObjects = container.querySelectorAll('foreignObject');
            console.log(`Found ${foreignObjects.length} foreignObject elements`);
            let fixCount = 0;
            let totalTextElements = 0;
            let elementsWithBackground = 0;

            foreignObjects.forEach((foreignObj, foreignIndex) => {
                const textElements = foreignObj.querySelectorAll('div, span');
                console.log(`ForeignObject ${foreignIndex}: contains ${textElements.length} text elements`);

                textElements.forEach((textEl) => {
                    const content = textEl.textContent?.trim();
                    if (!content) return;

                    totalTextElements++;
                    console.log(`Text element ${totalTextElements}: "${content}"`);

                    const computedStyle = window.getComputedStyle(textEl);
                    const textColor = computedStyle.color;
                    console.log(`  Text color: ${textColor}`);

                    // Find background color by walking up the DOM within this foreignObject
                    let backgroundColor: string | null = null;
                    let currentElement: Element | null = textEl;
                    let depth = 0;

                    while (currentElement && currentElement !== foreignObj) {
                        const elementStyle = window.getComputedStyle(currentElement);
                        const bgColor = elementStyle.backgroundColor;
                        console.log(`  Level ${depth}: element ${currentElement.tagName}, bg: ${bgColor}`);

                        if (bgColor && bgColor !== 'rgba(0, 0, 0, 0)' && bgColor !== 'transparent') {
                            backgroundColor = bgColor;
                            console.log(`  Found background: ${backgroundColor}`);
                            break;
                        }
                        currentElement = currentElement.parentElement;
                        depth++;
                    }

                    // Strategy 2: If no background found in DOM hierarchy, look for sibling SVG elements
                    if (!backgroundColor) {
                        console.log(`  No background in DOM hierarchy, checking SVG siblings`);

                        // Look at the parent group of this foreignObject
                        const parentGroup = foreignObj.parentElement;
                        if (parentGroup) {
                            console.log(`  Parent group: ${parentGroup.tagName}`);

                            // Look for rect, circle, polygon, path elements in the same group
                            const shapeElements = parentGroup.querySelectorAll('rect, circle, polygon, path, ellipse');
                            console.log(`  Found ${shapeElements.length} shape elements in parent group`);

                            for (const shape of shapeElements) {
                                const fill = shape.getAttribute('fill');
                                const computedFill = window.getComputedStyle(shape).fill;
                                console.log(`    Shape ${shape.tagName}: fill="${fill}" computed="${computedFill}"`);

                                // Use computed style instead of fill attribute for custom colors
                                if (computedFill && computedFill !== 'none' && computedFill !== 'transparent' && computedFill !== 'rgb(0, 0, 0)') {
                                    backgroundColor = computedFill;
                                    console.log(`  Found SVG background: ${backgroundColor}`);
                                    break;
                                } else if (fill && fill !== 'none' && fill !== 'transparent') {
                                    backgroundColor = fill;
                                    console.log(`  Found fallback SVG background: ${backgroundColor}`);
                                    break;
                                }
                            }
                        }
                    }

                    // Strategy 3: If still no background, search the entire SVG for ALL shapes
                    if (!backgroundColor) {
                        console.log(`  Still no background, searching entire SVG`);
                        const allShapes = container.querySelectorAll('rect, circle, polygon, path, ellipse');
                        console.log(`  Found ${allShapes.length} total shapes in SVG`);

                        // Log all shapes and their colors to see what we're missing
                        const allColors = new Set<string>();
                        allShapes.forEach((shape, i) => {
                            const fill = shape.getAttribute('fill');
                            const computedFill = window.getComputedStyle(shape).fill;
                            if (fill && fill !== 'none') allColors.add(fill);
                            if (computedFill && computedFill !== 'none' && computedFill !== 'rgb(0, 0, 0)') allColors.add(computedFill);

                            // Log first 10 shapes for debugging
                            if (i < 10) {
                                console.log(`    All shapes ${i}: ${shape.tagName} fill="${fill}" computed="${computedFill}"`);
                            }
                        });

                        console.log(`  All unique colors found:`, Array.from(allColors));

                        // For now, don't assign a background from this broad search
                        // We just want to see what colors are available
                    }

                    if (backgroundColor) {
                        elementsWithBackground++;
                        console.log(`  Background found: ${backgroundColor}`);
                        const isLight = isLightBackground(backgroundColor);
                        console.log(`  Is light background: ${isLight}`);

                        if (isLight) {
                            const contrastRatio = calculateContrastRatio(textColor, backgroundColor);
                            console.log(`  Contrast ratio: ${contrastRatio.toFixed(2)}`);

                            if (contrastRatio < 3.0) {
                                console.log(`  🔧 FIXING: "${content}" - poor contrast (${contrastRatio.toFixed(2)})`);
                                (textEl as HTMLElement).style.color = '#000000';
                                (textEl as HTMLElement).style.setProperty('color', '#000000', 'important');
                                fixCount++;
                            } else {
                                console.log(`  ✓ SKIPPING: "${content}" - good contrast (${contrastRatio.toFixed(2)})`);
                            }
                        }
                    } else {
                        console.log(`  No background found for: "${content}"`);
                    }

                    // Check if we need to fix this text
                });
            });
            console.log(`🔍 SUMMARY: Processed ${totalTextElements} text elements, ${elementsWithBackground} had backgrounds, fixed ${fixCount}`);
        }, 1000);

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
            svgElement.querySelectorAll('.node .label text, .cluster .label text').forEach(textEl => {
                // Find the background color of the parent node/cluster
                const parentGroup = textEl.closest('g.node, g.cluster');
                if (parentGroup) {
                    const backgroundShape = parentGroup.querySelector('rect, polygon, circle, path');
                    if (backgroundShape) {
                        const fill = backgroundShape.getAttribute('fill');
                        const contrastColor = fill ? getOptimalTextColor(fill) : (isDark ? '#ffffff' : '#000000');
                        textEl.setAttribute('fill', contrastColor);
                    }
                }
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
                    "><div style="
                        font-weight: bold;
                        color: ${isDarkMode ? '#58a6ff' : '#0366d6'};
                        margin-bottom: 12px;
                        font-size: 14px;
                    ">🧩 Mermaid Diagram Source:</div><code>${spec.definition}</code></pre>`;
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

        // Extract the first line to check for diagram type
        const lines = spec.definition.trim().split('\n');
        const firstLine = lines[0]?.trim() || '';
        const diagramType = firstLine.split(' ')[0];

        // Enhanced error analysis
        let errorTitle = 'Mermaid Rendering Error';
        let errorMessage = 'There was an error rendering the diagram.';

        // Analyze the error message for common patterns
        const errorMsg = error.message || '';

        if (errorMsg.includes('Parse error') || errorMsg.includes('Expecting')) {
            errorTitle = 'Mermaid Syntax Error';
            errorMessage = 'There is a syntax error in the Mermaid diagram definition. The diagram may have malformed connections or invalid characters.';
        } else if (errorMsg.includes('Lexical error') || errorMsg.includes('Unrecognized text')) {
            errorTitle = 'Mermaid Lexical Error';
            errorMessage = 'Mermaid encountered unrecognized text or invalid syntax in the diagram definition.';
        } else if (errorMsg.includes('empty SVG returned')) {
            // Check if this diagram type is actually supported
            try {
                const { detectSupportedDiagramTypes, normalizeDiagramType } = await import('./mermaidEnhancer');
                const supportedTypes = detectSupportedDiagramTypes(mermaid);
                const normalizedType = normalizeDiagramType(diagramType, mermaid);
                
                console.log('Type detection debug:', {
                    diagramType,
                    normalizedType,
                    supportedTypesSize: supportedTypes.size,
                    supportedTypes: Array.from(supportedTypes),
                    hasOriginal: supportedTypes.has(diagramType),
                    hasNormalized: supportedTypes.has(normalizedType)
                });
                
                // If detection failed (empty set), fall back to parsing error
                if (supportedTypes.size === 0) {
                    console.warn('Type detection returned empty set, falling back to parsing error');
                    errorTitle = 'Mermaid Parsing Error';
                    errorMessage = 'Mermaid parsing failed - this usually indicates syntax errors in the diagram definition.';
                } else if (diagramType && !supportedTypes.has(diagramType) && !supportedTypes.has(normalizedType)) {
                    errorTitle = 'Unsupported Diagram Type';
                    errorMessage = `The diagram type "${diagramType}" is not supported in the current version of Mermaid. This may be a beta feature that hasn't been released yet.`;
                } else {
                    errorTitle = 'Mermaid Parsing Error';
                    errorMessage = 'Mermaid parsing failed - this usually indicates syntax errors in the diagram definition.';
                }
            } catch (detectionError) {
                console.warn('Could not detect supported types:', detectionError);
                errorTitle = 'Mermaid Parsing Error';
                errorMessage = 'Mermaid parsing failed - this usually indicates syntax errors in the diagram definition.';
            }
        }

        if (!spec.isStreaming || spec.forceRender) {
                // First clear the container and add the error message
                container.innerHTML = `
                <div class="mermaid-error">
                    <strong>${errorTitle}:</strong>
                    <p>${errorMessage}</p>
                    <pre>${error.message || 'Unknown error'}</pre>
                    <details>
                        <summary>Show Definition</summary>
                        <pre><code>${spec.definition}</code></pre>
                    </details>
                </div>
                `;

                // Create buttons
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

    // Special handling for very light colors that appear in classDef
    const lightBlue = /^#e[0-9a-f]f[0-9a-f]fd$/i;  // Matches #e3f2fd and similar
    const lightGreen = /^#e[0-9a-f]f[0-9a-f]e[0-9a-f]$/i; // Matches #e8f5e8 and similar  
    const lightOrange = /^#fff[0-9a-f]e[0-9a-f]$/i; // Matches #fff3e0 and similar
    
    if (lightBlue.test(backgroundColor) || lightGreen.test(backgroundColor) || lightOrange.test(backgroundColor)) {
        return '#000000'; // Always use black on these very light backgrounds
    }
    
    // Handle yellow and yellow-ish colors
    if (rgb.r > 200 && rgb.g > 200 && rgb.b < 100) {
        return '#000000'; // Always use black on yellow/yellow-ish
    }

    // Handle beige/cream colors
    if (rgb.r > 220 && rgb.g > 200 && rgb.b > 150) {
        return '#000000'; // Always use black on beige/cream
    }

    // Calculate relative luminance and use conservative threshold
    const luminance = (0.299 * rgb.r + 0.587 * rgb.g + 0.114 * rgb.b) / 255;
    return luminance > 0.4 ? '#000000' : '#ffffff';
}

function isProblematicBackground(color: string): boolean {
    if (!color || color === 'none' || color === 'transparent') return false;

    // Normalize the color to uppercase and remove # if present

    let normalizedColor: string;

    // Handle RGB format: rgb(255, 245, 157) -> FFF59D
    if (color.startsWith('rgb(')) {
        const rgbMatch = color.match(/rgb\((\d+),\s*(\d+),\s*(\d+)\)/);
        if (rgbMatch) {
            const [, r, g, b] = rgbMatch;
            normalizedColor = [r, g, b]
                .map(x => parseInt(x).toString(16).padStart(2, '0'))
                .join('').toUpperCase();
        } else {
            return false;
        }
    } else {
        normalizedColor = color.toUpperCase();
    }

    if (normalizedColor.startsWith('#')) {
        normalizedColor = normalizedColor.substring(1);
    }

    // The exact list of problematic background colors you identified
    const problematicColors = [
        'FFEA2E', 'FFB50D', 'FFF58C', 'FFF59D', 'FFF0D9', 'E2F4E2', 'F0DDF3',
        'DBF2FE', 'FFF7DA', 'DDEFFD', 'FDC0C8', 'F5A9D1', 'D4EA8C', 
        'E3F2FD', 'E8F5E8', 'FFF3E0', // Add the specific colors from user's example
        'FFEB3B'
    ];

    const result = problematicColors.includes(normalizedColor);
    // Check if this color matches any of the problematic ones
    return result;
}
