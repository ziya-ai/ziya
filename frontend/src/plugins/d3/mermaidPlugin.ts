import { D3RenderPlugin } from '../../types/d3';
import initMermaidSupport, { enhancePacketDarkMode } from './mermaidEnhancer';
import { isDiagramDefinitionComplete } from '../../utils/diagramUtils';
import { extractDefinitionFromYAML } from '../../utils/diagramUtils';
import { getZoomScript } from '../../utils/popupScriptUtils';
import { enhanceSVGVisibility } from '../../utils/colorUtils';

// Add mermaid to window for TypeScript
declare global {
    interface Window {
        mermaid: any;
        __mermaidLoaded?: boolean;
        __mermaidLoading?: Promise<any>;
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
    MAX_FONT_SIZE: 18,      // Maximum font size in pixels
    MAX_SCALE: 3.0,         // Maximum scale factor
    MIN_SCALE: 0.3          // Minimum scale factor (for very large text)
};

// Global render queue to serialize Mermaid rendering and prevent conflicts
class MermaidRenderQueue {
    private queue: Array<() => Promise<any>> = [];
    private isProcessing = false;
    private pendingDiagrams = new Set<string>(); // Track diagrams being processed

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

        // Minimal Safari throttling - only for very large queues
        const isSafari = typeof navigator !== 'undefined' && /^((?!chrome|android).)*safari/i.test(navigator.userAgent);
        if (isSafari && this.queue.length > 10) {
            console.log('🍎 SAFARI-THROTTLE: Large queue detected, processing with delay');
            await new Promise(resolve => setTimeout(resolve, 50)); // Reduced from 200ms to 50ms
        }

        this.isProcessing = true;

        console.log(`🎯 RENDER-QUEUE: Processing item ${this.queue.length} remaining`);

        const renderFn = this.queue.shift()!;
        await renderFn();
        this.isProcessing = false;

        this.processQueue(); // Process next item
    }
}

const renderQueue = new MermaidRenderQueue();

/**
 * Lazy load mermaid library
 * Uses dynamic import with timeout, falls back to CDN if chunk loading fails
 */
async function loadMermaid(): Promise<any> {
    if (typeof window !== 'undefined' && window.__mermaidLoaded && window.mermaid) {
        return window.mermaid;
    }

    // If already loading, wait for it
    if (window.__mermaidLoading) {
        return await window.__mermaidLoading;
    }

    // Helper: Import with timeout protection
    const importWithTimeout = (moduleSpecifier: string, timeoutMs: number = 3000): Promise<any> => {
        return Promise.race([
            import(moduleSpecifier),
            new Promise((_, reject) =>
                setTimeout(() => reject(new Error(`Import timeout after ${timeoutMs}ms`)), timeoutMs)
            )
        ]);
    };

    // Helper: Load mermaid from CDN as fallback
    const loadFromCDN = (): Promise<any> => {
        return new Promise((resolve, reject) => {
            console.warn('⚠️ MERMAID-LOAD: Loading from CDN fallback');

            // Check if already loaded by CDN in a previous attempt
            if (window.mermaid && typeof window.mermaid.render === 'function') {
                console.log('✅ MERMAID-LOAD: Already available on window');
                return resolve({ default: window.mermaid });
            }

            const script = document.createElement('script');
            script.src = 'https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js';

            script.onload = () => {
                console.log('✅ MERMAID-LOAD: Loaded from CDN successfully');
                if (window.mermaid && typeof window.mermaid.render === 'function') {
                    // Wrap in module-like object to match import() structure
                    resolve({ default: window.mermaid });
                } else {
                    reject(new Error('Mermaid script loaded but window.mermaid not available'));
                }
            };

            script.onerror = (e) => {
                console.error('❌ MERMAID-LOAD: CDN fallback also failed:', e);
                reject(new Error('Failed to load Mermaid from CDN'));
            };

            document.head.appendChild(script);
        });
    };

    // Start loading with timeout protection and CDN fallback
    window.__mermaidLoading = importWithTimeout('mermaid', 3000)
        .catch(error => {
            console.error('❌ MERMAID-LOAD: Chunk import failed:', error.message);
            // Fall back to CDN
            return loadFromCDN();
        })
        .then(module => {
            console.log('✅ MERMAID-LOAD: Module loaded successfully');
            const mermaid = module.default;
            initMermaidSupport(mermaid);
            window.mermaid = mermaid;
            window.__mermaidLoaded = true;
            return mermaid;
        });

    return await window.__mermaidLoading;
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
            maxWidth: '100%',
            height: 'auto',
            minHeight: 'auto',
            overflow: 'hidden',
            // Safari-specific: ensure container can grow to accommodate scaled content
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center'
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
        // Lazy load mermaid library
        const mermaid = await loadMermaid();
        if (!mermaid) {
            throw new Error('Failed to load mermaid library');
        }

        // Skip queue for Safari to avoid delays - Mermaid can handle concurrent renders
        const isSafari = typeof navigator !== 'undefined' && /^((?!chrome|android).)*safari/i.test(navigator.userAgent);
        if (isSafari) {
            // Add Safari warning to Mermaid diagrams specifically
            console.warn('🍎 SAFARI-MERMAID: Rendering Mermaid diagram on Safari - compatibility issues expected');
            const result = await renderSingleDiagram(container, d3, spec, isDarkMode, mermaid);
            // Add a small notice that rendering may be degraded
            console.warn('🍎 SAFARI-MERMAID: Mermaid diagram rendered on Safari. Visual artifacts or performance issues may occur.');
            return result;
        } else {
            // Use render queue for other browsers to prevent conflicts
            return renderQueue.enqueue(async () => {
                return await renderSingleDiagram(container, d3, spec, isDarkMode, mermaid);
            });
        }
    }
};

async function renderSingleDiagram(container: HTMLElement, d3: any, spec: MermaidSpec, isDarkMode: boolean, mermaid: any): Promise<void> {
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
        if (!spec.isMarkdownBlockClosed && !spec.forceRender) {
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
            let def = spec.definition;

            // Handle double-wrapped JSON definitions
            if (typeof def === 'string' && def.trim().startsWith('{')) {
                try {
                    const parsed = JSON.parse(def);
                    if (parsed.type === 'mermaid' && parsed.definition) {
                        rawDefinition = parsed.definition;
                        console.log('Extracted definition from double-wrapped JSON');
                    } else {
                        rawDefinition = extractDefinitionFromYAML(def, 'mermaid');
                        console.log('Used YAML extraction from string definition');
                    }
                } catch {
                    rawDefinition = extractDefinitionFromYAML(def, 'mermaid');
                    console.log('JSON parse failed, using YAML extraction');
                }
            } else {
                rawDefinition = extractDefinitionFromYAML(def, 'mermaid');
            }
        } else if (typeof spec === 'string') {
            rawDefinition = extractDefinitionFromYAML(spec, 'mermaid');
        } else {
            throw new Error('Invalid mermaid spec: no definition found');
        }

        // CRITICAL DEBUG: Log what we're about to send to Mermaid
        console.log('🔧 MERMAID-DEBUG: About to render with rawDefinition:', {
            type: typeof rawDefinition,
            length: rawDefinition?.length || 0,
            firstChar: rawDefinition?.charAt(0) || 'N/A',
            first50: rawDefinition?.substring(0, 50) || 'N/A',
            startsWithGantt: rawDefinition?.trim().startsWith('gantt') || false
        });

        console.log('Raw definition (first 200 chars):', rawDefinition.substring(0, 200));

        // Detect diagram type
        const lines = rawDefinition.trim().split('\n');
        let firstLine = lines[0]?.trim() || '';

        // Skip YAML frontmatter if present
        if (firstLine === '---' && lines.length > 2) {
            firstLine = lines.find((line, idx) => idx > 0 && line.trim() && line.trim() !== '---')?.trim() || '';
        }
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

        // CRITICAL: Remove old wrapper before adding new one to prevent duplicate diagrams
        const oldWrapper = container.querySelector('.mermaid-wrapper');
        if (oldWrapper) {
            console.log('🧹 REMOVING old wrapper before adding new one');
            container.removeChild(oldWrapper);
        }

        // Add wrapper to container
        container.appendChild(wrapper);

        // Get the SVG element after it's in the DOM
        const svgElement = wrapper.querySelector('svg');
        if (!svgElement) {
            throw new Error('Failed to get SVG element after rendering');
        }


        if (!renderSuccessful) return;

        // UNIVERSAL FIX: Apply centralized visibility enhancement
        setTimeout(() => {
            const result = enhanceSVGVisibility(svgElement, isDarkMode, { debug: true });
            console.log(`✅ Mermaid visibility enhanced:`, result);
            console.log(`🎯 Detected diagram type: "${diagramType}"`);
        }, 300);

        // Apply unified responsive scaling for all browsers
        applyUnifiedResponsiveScaling(container, svgElement, isDarkMode);

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
                        svg.style.maxWidth = '100%');
                        svg.style.maxHeight = '100%';
                        svg.setAttribute('preserveAspectRatio', 'xMidYMid meet');
                        ${getZoomScript()}
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
        let cachedSVG: string;
        let cachedScale: string;
        let cachedWrapperHeight: string;

        // Cache initial state
        cachedSVG = wrapper.innerHTML;
        cachedScale = svgElement.style.transform;
        cachedWrapperHeight = wrapper.style.minHeight;

        const sourceButton = document.createElement('button');
        sourceButton.innerHTML = showingSource ? '🎨 View' : '📝 Source';
        sourceButton.className = 'diagram-action-button mermaid-source-button';
        sourceButton.onclick = () => {
            showingSource = !showingSource;
            sourceButton.innerHTML = showingSource ? '🎨 View' : '📝 Source';

            if (showingSource) {
                // Cache current state before showing source
                cachedSVG = wrapper.innerHTML;
                cachedScale = svgElement.style.transform;
                cachedWrapperHeight = wrapper.style.minHeight;

                wrapper.innerHTML = `<pre style="
                        background-color: ${isDarkMode ? '#1f1f1f' : '#f6f8fa'};
                        padding: 16px;
                        border-radius: 4px;
                        overflow: auto;
                        color: ${isDarkMode ? '#e6e6e6' : '#24292e'};
                    "><code>${spec.definition}</code></pre>`;
            } else {
                // Restore cached state
                wrapper.innerHTML = cachedSVG;

                // Reapply the scaling that was applied before
                const restoredSVG = wrapper.querySelector('svg');
                if (restoredSVG && cachedScale) {
                    (restoredSVG as SVGElement).style.transform = cachedScale;
                }
                if (cachedWrapperHeight) {
                    wrapper.style.minHeight = cachedWrapperHeight;
                }
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

// Unified responsive scaling that works across all browsers including Safari
/**
 * Find the maximum EFFECTIVE font size as actually rendered on screen
 * CRITICAL: Returns the maximum DECLARED font size (we'll apply viewBox scaling separately)
 */
function findMaxDeclaredFontSize(svgElement: SVGElement): number {
    const textElements = svgElement.querySelectorAll('text, tspan');
    const foreignObjectContainers = svgElement.querySelectorAll('foreignObject');
    let maxFontSize = 0;

    // Measure SVG text elements - get DECLARED size from computedStyle
    textElements.forEach(textEl => {
        const text = textEl.textContent?.trim();
        if (!text) return;

        const computedStyle = window.getComputedStyle(textEl);
        const declaredSize = parseFloat(computedStyle.fontSize || '0');

        if (declaredSize > maxFontSize) {
            maxFontSize = declaredSize;
        }
    });

    // Also check foreignObject elements
    foreignObjectContainers.forEach((fo) => {
        const textEls = fo.querySelectorAll('span, div');
        textEls.forEach(el => {
            const text = el.textContent?.trim();
            if (!text) return;

            const computedStyle = window.getComputedStyle(el);
            const declaredSize = parseFloat(computedStyle.fontSize || '0');

            if (declaredSize > maxFontSize) {
                maxFontSize = declaredSize;
            }
        });
    });

    console.log(`📏 FONT-DETECTION: Max declared font size: ${maxFontSize.toFixed(1)}px`);
    return maxFontSize;
}

/**
 * Scale diagram to achieve target EFFECTIVE font size (as rendered on screen)
 * Key insight: effective font = declared font × (svg width / viewBox width)
 * So we set svg width = viewBox width × (target font / declared font)
 */
function applyEffectiveFontScaling(svgElement: SVGElement, mermaidWrapper: HTMLElement, diagramType: string): void {
    const viewBox = svgElement.getAttribute('viewBox');
    if (!viewBox) {
        console.log('🎯 EFFECTIVE-SCALE: No viewBox found, skipping');
        return;
    }

    const [, , vbW, vbH] = viewBox.split(' ').map(Number);

    // Find max DECLARED font size
    const maxDeclaredFont = findMaxDeclaredFontSize(svgElement);

    if (maxDeclaredFont === 0) {
        console.log('🎯 EFFECTIVE-SCALE: No text found, skipping');
        return;
    }

    console.log(`🎯 EFFECTIVE-SCALE: Type="${diagramType}", Declared font=${maxDeclaredFont.toFixed(1)}px, ViewBox=${vbW.toFixed(0)}×${vbH.toFixed(0)}`);

    // Calculate viewBox scale needed for target font size
    // effective font = declared font × viewBox scale
    // target font = declared font × target viewBox scale
    // target viewBox scale = target font / declared font
    const targetViewBoxScale = SCALE_CONFIG.TARGET_FONT_SIZE / maxDeclaredFont;

    // New SVG dimensions
    let newWidth = vbW * targetViewBoxScale;
    let newHeight = vbH * targetViewBoxScale;

    // Clamp to reasonable sizes
    const maxWidth = 900;
    const minWidth = 100;
    if (newWidth > maxWidth) {
        const ratio = maxWidth / newWidth;
        newWidth = maxWidth;
        newHeight = newHeight * ratio;
    } else if (newWidth < minWidth) {
        const ratio = minWidth / newWidth;
        newWidth = minWidth;
        newHeight = newHeight * ratio;
    }

    // Remove default width attribute and apply calculated dimensions
    svgElement.removeAttribute('width');
    svgElement.removeAttribute('height');
    svgElement.style.setProperty('width', `${newWidth}px`, 'important');
    svgElement.style.setProperty('height', `${newHeight}px`, 'important');
    svgElement.style.setProperty('max-width', 'none', 'important');
    svgElement.style.setProperty('max-height', 'none', 'important');
    svgElement.style.setProperty('transform', 'none', 'important');

    // Set wrapper to match content
    mermaidWrapper.style.setProperty('min-height', `${newHeight + 20}px`, 'important');
    mermaidWrapper.style.setProperty('height', 'auto', 'important');
    mermaidWrapper.style.setProperty('max-height', 'none', 'important');
    mermaidWrapper.style.setProperty('width', '100%', 'important');
    mermaidWrapper.style.setProperty('display', 'flex', 'important');
    mermaidWrapper.style.setProperty('justify-content', 'center', 'important');
    mermaidWrapper.style.setProperty('align-items', 'flex-start', 'important');
    mermaidWrapper.style.setProperty('overflow', 'visible', 'important');
    mermaidWrapper.style.setProperty('padding', '10px', 'important');

    // Fix parent d3-container
    const d3Container = mermaidWrapper.closest('.d3-container');
    if (d3Container) {
        (d3Container as HTMLElement).style.setProperty('height', 'auto', 'important');
        (d3Container as HTMLElement).style.setProperty('min-height', 'auto', 'important');
        const outer = d3Container.parentElement?.closest('.d3-container');
        if (outer) {
            (outer as HTMLElement).style.setProperty('height', 'auto', 'important');
            (outer as HTMLElement).style.setProperty('min-height', 'auto', 'important');
        }
    }

    const finalScale = newWidth / vbW;
    const finalEffectiveFont = maxDeclaredFont * finalScale;
    console.log(`🎯 EFFECTIVE-SCALE: ${vbW.toFixed(0)}×${vbH.toFixed(0)} → ${newWidth.toFixed(0)}×${newHeight.toFixed(0)} (scale: ${finalScale.toFixed(3)}, effective font: ${finalEffectiveFont.toFixed(1)}px)`);
}

function applyUnifiedResponsiveScaling(
    container: HTMLElement,
    svgElement: SVGElement,
    isDarkMode: boolean,
    diagramType: string = 'unknown'
) {
    console.log('🎯 UNIFIED-SCALING: Applying responsive scaling for all browsers');

    const isSafari = /^((?!chrome|android).)*safari/i.test(navigator.userAgent);
    const mermaidWrapper = container.querySelector('.mermaid-wrapper') as HTMLElement;

    if (!mermaidWrapper) {
        console.warn('No mermaid wrapper found, cannot apply responsive scaling');
        return;
    }

    // MINIMAL approach: Just ensure proper viewBox and preserve Mermaid's layout
    const viewBox = svgElement.getAttribute('viewBox');
    if (viewBox) {
        console.log('🎯 UNIFIED-SCALING: SVG has viewBox:', viewBox);
        // Ensure proper responsive attributes without breaking positioning
        svgElement.setAttribute('preserveAspectRatio', 'xMidYMid meet');
    } else {
        console.warn('🎯 UNIFIED-SCALING: No viewBox found, this may cause positioning issues');
        // Don't add a viewBox if Mermaid didn't create one - this can break positioning
    }

    // For Safari, apply minimal scaling if the diagram is too small
    if (isSafari) {
        // Apply effective font-based scaling
        setTimeout(() => {
            applyEffectiveFontScaling(svgElement, mermaidWrapper, diagramType);
        }, 250);

        setTimeout(() => {
            const svgRect = svgElement.getBoundingClientRect();
            const containerRect = container.getBoundingClientRect();

            console.log('🎯 SAFARI-SIZE-CHECK:', {
                svgWidth: svgRect.width,
                svgHeight: svgRect.height,
                containerWidth: containerRect.width,
                currentTransform: svgElement.style.transform
            });

            // Only scale if the diagram is significantly smaller than the container
            if (svgRect.width > 0 && containerRect.width > 0 &&
                svgRect.width < containerRect.width * 0.6) {
                const targetScale = Math.min(containerRect.width * 0.9 / svgRect.width, 4.0);
                svgElement.style.transform = `scale(${targetScale})`;
                svgElement.style.transformOrigin = 'center center';
                console.log(`🎯 SAFARI-SCALE: Applied scaling ${targetScale}x (${svgRect.width}px → ${svgRect.width * targetScale}px)`);

                // Adjust wrapper to accommodate scaled content
                mermaidWrapper.style.minHeight = `${svgRect.height * targetScale + 40}px`;
                mermaidWrapper.style.minWidth = `${svgRect.width * targetScale}px`;
                mermaidWrapper.style.width = 'auto'; // Let it expand to fit scaled content
            } else {
                console.log('🎯 SAFARI-SCALE: No scaling needed, diagram size looks good');
            }
        }, 200); // Give Mermaid time to finish positioning
    } else {
        // For non-Safari browsers, apply effective font-based scaling
        setTimeout(() => {
            console.log(`🎯 SCALE: Starting effective font scaling for ${diagramType}`);
            applyEffectiveFontScaling(svgElement, mermaidWrapper, diagramType);
        }, 500);
    }
    // Configure wrapper for responsive behavior without breaking Mermaid's positioning
    mermaidWrapper.style.width = '100%';
    mermaidWrapper.style.maxWidth = '100%';
    mermaidWrapper.style.overflow = 'auto'; // Changed from 'visible' to 'auto' to handle large scaled content
    mermaidWrapper.style.display = 'flex';
    mermaidWrapper.style.justifyContent = 'center';
    mermaidWrapper.style.alignItems = 'flex-start'; // Changed from 'center' to preserve top alignment
    mermaidWrapper.style.padding = '1em';

    console.log('🎯 UNIFIED-SCALING: Responsive configuration applied', isSafari ? '(Safari)' : '(Other)');
}
