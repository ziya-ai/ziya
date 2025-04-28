import React, { useState, useEffect, useRef, useMemo, CSSProperties, useCallback } from 'react';
import { useTheme } from '../context/ThemeContext';
import { Spin, Modal } from 'antd';
import vegaEmbed from 'vega-embed';
import * as d3 from 'd3';
import { D3RenderPlugin } from '../types/d3';
import { d3RenderPlugins } from '../plugins/d3/registry';
import { isDiagramDefinitionComplete } from '../utils/diagramUtils';

type RenderType = 'auto' | 'vega-lite' | 'd3';

interface D3RendererProps {
    spec: any;
    width?: number;
    height?: number;
    containerId?: string;
    type?: RenderType;
    onLoad?: () => void;
    onError?: (error: Error) => void;
    isStreaming?: boolean;
    isMarkdownBlockClosed?: boolean;
    forceRender?: boolean;
    config?: any;
}

function findPlugin(spec: any): D3RenderPlugin | undefined {
    // First check for explicit type
    if (spec.visualizationType) {
        const exactMatch = d3RenderPlugins.find(p => p.name === spec.visualizationType);
        if (exactMatch) return exactMatch;
    }
    // Then check all plugins in priority order
    const matchingPlugins = d3RenderPlugins
        .filter(p => p.canHandle(spec))
        .sort((a, b) => b.priority - a.priority);
    if (matchingPlugins.length > 0) {
        console.debug(`Found ${matchingPlugins.length} matching plugins:`,
            matchingPlugins.map(p => `${p.name} (priority: ${p.priority})`));
        return matchingPlugins[0];
    }
}

// Helper function to estimate diagram size based on content
function estimateDiagramSize(spec: any, plugin?: D3RenderPlugin): { width: number; height: number } {
    // Default fallback sizes
    const defaults = { width: 600, height: 400 };

    if (!spec || typeof spec !== 'object') return defaults;

    // If explicit dimensions are provided, use them
    if (spec.width && spec.height) {
        return { width: spec.width, height: spec.height };
    }

    // Plugin-specific size estimation
    if (plugin?.name === 'mermaid-renderer' && spec.definition) {
        const lines = spec.definition.split('\n').length;
        const avgCharsPerLine = spec.definition.length / lines;

        // Rough estimation based on content complexity
        const estimatedWidth = Math.min(Math.max(avgCharsPerLine * 8, 400), 1200);
        const estimatedHeight = Math.min(Math.max(lines * 25, 200), 800);

        return { width: estimatedWidth, height: estimatedHeight };
    }

    return defaults;
}

export const D3Renderer: React.FC<D3RendererProps> = ({
    spec,
    width = 600,
    height = 400,
    containerId,
    type = 'auto',
    onLoad,
    onError,
    isStreaming = false,
    isMarkdownBlockClosed = true,
    config = {}
}) => {
    const { isDarkMode } = useTheme();
    const vegaContainerRef = useRef<HTMLDivElement>(null);
    const d3ContainerRef = useRef<HTMLDivElement>(null);
    const vegaViewRef = useRef<any>(null);
    const [isLoading, setIsLoading] = useState(true);
    const [renderError, setRenderError] = useState<string | null>(null);
    const [displayWaitingMessage, setDisplayWaitingMessage] = useState<boolean>(false);
    const [hasAttemptedRender, setHasAttemptedRender] = useState<boolean>(false);
    const [errorDetails, setErrorDetails] = useState<string[]>([]);
    const cleanupRef = useRef<(() => void) | null>(null);
    const simulationRef = useRef<any>(null);
    const [isSourceModalVisible, setIsSourceModalVisible] = useState(false);
    const renderIdRef = useRef<number>(0);
    const mounted = useRef(true);
    const lastSpecRef = useRef<any>(null);
    const streamingContentRef = useRef<string | null>(null);
    const lastUsedPluginRef = useRef<D3RenderPlugin | null>(null);
    const lastValidSpecRef = useRef<any>(null);
    const hasSuccessfulRenderRef = useRef<boolean>(false);

    // New state for size reservation and rendering control
    const [reservedSize, setReservedSize] = useState<{ width: number; height: number } | null>(null);
    const [renderingStarted, setRenderingStarted] = useState<boolean>(false);

    // Store the spec in a ref to avoid unnecessary re-renders
    useEffect(() => { lastSpecRef.current = spec; }, [spec]);

    // Estimate and reserve size early
    useEffect(() => {
        if (spec && !reservedSize) {
            const plugin = findPlugin(spec);
            const estimated = estimateDiagramSize(spec, plugin);
            setReservedSize(estimated);
            console.debug('Reserved size for diagram:', estimated);
        }
    }, [spec, reservedSize]);

    // First useEffect for cleanup
    useEffect(() => {
        return () => {
            mounted.current = false;
            console.debug('D3Renderer cleanup triggered');
            if (vegaViewRef.current) {
                try {
                    vegaViewRef.current.finalize();
                    vegaViewRef.current = null;
                } catch (e) { /* ignore cleanup errors */ }
                console.debug('Vega view finalized');
            }
            // Clear D3 container
            if (d3ContainerRef.current) {
                // Stop any running force simulation
                if (simulationRef.current) {
                    simulationRef.current.stop();
                    simulationRef.current = null;
                }
                // Remove all D3 event listeners
                d3.select(d3ContainerRef.current)
                    .selectAll('*')
                    .on('.', null);

                d3ContainerRef.current.innerHTML = '';
                if (cleanupRef.current) {
                    cleanupRef.current();
                    cleanupRef.current = null;
                }
            }
        };
    }, []);

    // Initialize visualization with useCallback for better performance and dependency tracking
    const initializeVisualization = useCallback(async () => {
        // Once we start rendering, hide the spinner permanently
        if (!renderingStarted) {
            setRenderingStarted(true);
        }

        if ((!hasAttemptedRender || !isStreaming) && !hasSuccessfulRenderRef.current) {
            setIsLoading(true);
        }
        try {
            if (!spec) {
                throw new Error('No specification provided');
                return;
            }

            let parsed: any;
            let specLines: string[];

            // Determine if we should attempt to render or show waiting message
            let attemptRender = true;
            let localShouldShowWaitingPlaceholder = false;

            try {
                if (typeof spec === 'string') {
                    console.debug('Parsing string spec:', spec.substring(0, 100) + '...');
                    // Clean up the spec string
                    specLines = spec
                        .replace(/\r\n/g, '\n')
                        .split('\n')
                        .map(line => line.trim())
                        .filter(line => !line.trim().startsWith('//') && line.trim() !== '');

                    // Check if we have a complete code block
                    if (!specLines.length) {
                        setIsLoading(true);
                        return; // Exit early if code block is incomplete
                    }

                    const cleanSpec = specLines.join('\n').replace(/\/\*[\s\S]*?\*\//g, '');
                    parsed = JSON.parse(cleanSpec);
                } else {
                    console.debug('Using object spec directly');
                    parsed = spec;
                }
            } catch (parseError) {
                // During streaming, this could be an incomplete JSON
                if (isStreaming && !isMarkdownBlockClosed) {
                    streamingContentRef.current = typeof spec === 'string' ? spec : null;
                    localShouldShowWaitingPlaceholder = true;
                    attemptRender = false;
                    if (isLoading && !hasSuccessfulRenderRef.current) setIsLoading(false);
                    setDisplayWaitingMessage(true);
                    return;
                } else {
                    setIsLoading(true); // Keep loading while we wait for complete spec
                    console.debug('Waiting for complete spec:', parseError);
                    return;
                }
            }
                
                // Log the parsed spec for debugging
                console.debug('D3Renderer: Successfully parsed spec:', {
                    type: parsed.type,
                    renderer: parsed.renderer
                });

            // If we have a parsed spec, determine if it's complete enough to render
            if (parsed) {
                const specType = parsed.type || '';

                // For streaming content, check if the definition is complete enough
                if (isStreaming && !isMarkdownBlockClosed) {
                    const plugin = findPlugin(parsed);

                    // If we have a plugin with isDefinitionComplete method, use it
                    if (plugin?.isDefinitionComplete && typeof parsed.definition === 'string') {
                        const isComplete = plugin.isDefinitionComplete(parsed.definition);
                        console.debug(`Checking if ${plugin.name} definition is complete:`, isComplete);

                        if (!isComplete) {
                            localShouldShowWaitingPlaceholder = true;
                            attemptRender = false;
                        }
                    } else if (specType === 'mermaid' || specType === 'graphviz') {
                        // Use the generic isDiagramDefinitionComplete utility
                        const isComplete = isDiagramDefinitionComplete(
                            parsed.definition || '',
                            specType
                        );

                        if (!isComplete) {
                            localShouldShowWaitingPlaceholder = true;
                            attemptRender = false;
                        }
                    }
                }

                // If we're going to attempt rendering, store this as our last valid spec
                if (attemptRender) {
                    lastValidSpecRef.current = parsed;
                }
            }

            // Update the waiting message state
            setDisplayWaitingMessage(localShouldShowWaitingPlaceholder);

            // If we're not attempting to render, exit early
            if (!attemptRender) {
                return;
            } else {
                // Mark that we've attempted a render
                setHasAttemptedRender(true);
            }

            // Special handling for Mermaid diagrams
            if (parsed.type === 'mermaid') {
                // Pre-process the definition to fix common syntax issues
                if (typeof parsed.definition === 'string') {

                    // Detect diagram type
                    const firstLine = parsed.definition.trim().split('\n')[0].toLowerCase();
                    const diagramType = firstLine.replace(/^(\w+).*$/, '$1').toLowerCase();

                    // Apply diagram-specific fixes
                    if (diagramType === 'flowchart' || diagramType === 'graph' || firstLine.startsWith('flowchart ') || firstLine.startsWith('graph ')) {
                        // Fix subgraph class syntax
                        parsed.definition = parsed.definition.replace(/class\s+(\w+)\s+subgraph-(\w+)/g, 'class $1 style_$2');
                        parsed.definition = parsed.definition.replace(/classDef\s+subgraph-(\w+)/g, 'classDef style_$1');

                        // Fix "Send DONE Marker" nodes
                        parsed.definition = parsed.definition.replace(/\[Send\s+"DONE"\s+Marker\]/g, '[Send DONE Marker]');
                        parsed.definition = parsed.definition.replace(/\[Send\s+\[DONE\]\s+Marker\]/g, '[Send DONE Marker]');

                        // Fix SendDone nodes
                        parsed.definition = parsed.definition.replace(/SendDone\[([^\]]+)\]/g, 'sendDoneNode["$1"]');

                        // Fix end nodes that cause parsing errors - replace all 'end' node references
                        parsed.definition = parsed.definition.replace(/\bend\b\s*\[/g, 'endNode[');
                        parsed.definition = parsed.definition.replace(/-->\s*\bend\b/g, '--> endNode');
                        parsed.definition = parsed.definition.replace(/\bend\b\s*-->/g, 'endNode -->');

                        // Convert flowchart to graph LR if needed for better compatibility
                        if (parsed.definition.startsWith('flowchart ')) {
                            parsed.definition = parsed.definition.replace(/^flowchart\s+/m, 'graph ');
                        }
                    }

                    else if (diagramType === 'requirement') {
                        // Fix requirement diagram syntax
                        const lines = parsed.definition.split('\n');
                        const result: string[] = [];

                        for (let i = 0; i < lines.length; i++) {
                            let line = lines[i].trim();

                            // Fix ID format
                            if (line.match(/^\s*id:/i)) {
                                line = line.replace(/id:\s*([^,]+)/, 'id: "$1"');
                            }

                            // Fix text format
                            if (line.match(/^\s*text:/i)) {
                                line = line.replace(/text:\s*([^,]+)/, 'text: "$1"');
                            }

                            result.push(line);
                        }

                        parsed.definition = result.join('\n');
                    }
                    else if (diagramType === 'xychart') {
                        // Fix xychart array syntax
                        parsed.definition = parsed.definition.replace(/\[(.*?)\]/g, '"[$1]"');
                    }

                    // Fix quoted text in node labels
                    parsed.definition = parsed.definition.replace(/\[([^"\]]*)"([^"\]]*)"([^"\]]*)\]/g, (match, before, quoted, after) => {
                        // Replace with simple text without quotes to avoid parsing issues
                        return `[${before}${quoted}${after}]`;
                    });

                    // Fix end nodes that cause parsing errors - replace all 'end' node references
                    parsed.definition = parsed.definition.replace(/\bend\b\s*\[/g, 'endNode[');
                    parsed.definition = parsed.definition.replace(/-->\s*\bend\b/g, '--> endNode');
                    parsed.definition = parsed.definition.replace(/\bend\b\s*-->/g, 'endNode -->');

                    // Fix nodes with square brackets in their text
                    parsed.definition = parsed.definition.replace(/\[([^\]]*\[[^\]]*\][^\]]*)\]/g, (match, content) => {
                        // Replace inner square brackets with parentheses
                        return `["${content.replace(/\[/g, '(').replace(/\]/g, ')')}"]`;
                    });

                    // Convert flowchart to graph LR if needed for better compatibility
                    if (parsed.definition.startsWith('flowchart ')) {
                        parsed.definition = parsed.definition.replace(/^flowchart\s+/m, 'graph ');
                    }

                    console.debug('Pre-processed Mermaid definition for better compatibility');
                }
            }

            // Log the parsed spec for debugging
            console.debug('D3Renderer: Successfully parsed spec:', {
                type: parsed.type,
                renderer: parsed.renderer
            });

            // Only proceed with rendering if we have a valid parsed spec
            if (!parsed) return;

            if (type === 'd3' || parsed.renderer === 'd3' || typeof parsed.render === 'function') {
                const container = d3ContainerRef.current;
                if (!container) return;

                if (renderIdRef.current !== renderIdRef.current) {
                    console.debug(`Skipping stale render #${renderIdRef.current}`);
                    return;
                }

                // Handle Vega-Lite rendering
                const container = vegaContainerRef.current;
                if (!container) return;

                const vegaSpec = {
                    $schema: 'https://vega.github.io/schema/vega-lite/v5.json',
                    width: width || 'container',
                    height: height || 300,
                    mark: parsed.type || 'point',
                    data: {
                        values: Array.isArray(parsed.data) ? parsed.data : [parsed.data]
                    },
                    encoding: parsed.encoding || {
                        x: { field: 'x', type: 'quantitative' },
                        y: { field: 'y', type: 'quantitative' }
                    },
                    ...parsed
                };

                if (vegaViewRef.current) {
                    vegaViewRef.current.finalize();
                    vegaViewRef.current = null;
                }

                console.debug('Rendering Vega spec:', vegaSpec);
                const result = await vegaEmbed(container, vegaSpec, {
                    actions: false,
                    theme: isDarkMode ? 'dark' : 'excel',
                    renderer: 'canvas'
                });

                // Check if this is a Graphviz or Mermaid plugin
                const plugin = findPlugin(parsed);
                lastUsedPluginRef.current = plugin || null;
                const isGraphvizOrMermaid = plugin?.name === 'graphviz-renderer' || plugin?.name === 'mermaid-renderer';

                // For Graphviz or Mermaid, override the container style to be more flexible
                if (isGraphvizOrMermaid) {
                    container.style.width = '100%';
                    container.style.height = 'auto';
                    container.style.minHeight = 'unset';
                    container.style.overflow = 'visible';
                }

                // Create temporary container for safe rendering
                const tempContainer = document.createElement('div');
                tempContainer.style.width = '100%';
                tempContainer.style.height = '100%';

                let renderSuccessful = false;

                try {
                    // Clear any existing content
                    if (container.firstChild) {
                        container.removeChild(container.firstChild);
                    }

                    // Initialize D3 selection for the container
                    const d3Container = d3.select(tempContainer);

                    if (typeof parsed.render === 'function') {
                        const result = parsed.render.call(parsed, tempContainer, d3);
                        renderSuccessful = true;

                        // If result is a function, use it as cleanup
                        if (typeof result === 'function') {
                            cleanupRef.current = result;
                        }
                    } else if (parsed.type === 'network') {
                        const plugin = findPlugin(parsed);
                        if (plugin) {
                            plugin.render(tempContainer, d3, {
                                ...parsed,
                                width: width || 600,
                                height: height || 400,
                                isStreaming: isStreaming && !isMarkdownBlockClosed,
                                forceRender: attemptRender
                            }, isDarkMode);
                            renderSuccessful = true;
                        }
                    } else {
                        const plugin = findPlugin(parsed);
                        if (!plugin) {
                            throw new Error('No render function or compatible plugin found');
                        }

                        console.debug('Using plugin:', plugin.name);
                        plugin.render(tempContainer, d3, {
                            ...parsed,
                            isStreaming: isStreaming && !isMarkdownBlockClosed,
                            forceRender: attemptRender
                        }, isDarkMode);
                        renderSuccessful = true;
                        if (mounted.current) setRenderError(null);
                    }

                    // Only replace main container content if render was successful
                    if (renderSuccessful) {
                        container.innerHTML = '';
                        container.appendChild(tempContainer);
                    }
                } catch (renderError) {
                    console.error('D3 render error:', renderError);
                    if (mounted.current) {
                        setRenderError(renderError instanceof Error ? renderError.message : 'Render failed');
                        setErrorDetails([renderError instanceof Error ? renderError.message : 'Unknown error']);
                    }
                }

                if (!renderSuccessful) {
                    throw new Error('Render did not complete successfully');
                }

                // Add retry button for Mermaid diagrams if there was an error
                if (renderError && (plugin?.name === 'mermaid-renderer')) {
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
                    retryButton.onclick = () => initializeVisualization();
                    container.appendChild(retryButton);
                }

                if (mounted.current) setIsLoading(false);
                setRenderError(null);
                onLoad?.();
                hasSuccessfulRenderRef.current = true;
                return;
            }
        };

            // Handle Vega-Lite rendering
            const container = vegaContainerRef.current;
            if (!container) return;

            const vegaSpec = {
                $schema: 'https://vega.github.io/schema/vega-lite/v5.json',
                width: width || 'container',
                height: height || 300,
                mark: parsed.type || 'point',
                data: {
                    values: Array.isArray(parsed.data) ? parsed.data : [parsed.data]
                },
                encoding: parsed.encoding || {
                    x: { field: 'x', type: 'quantitative' },
                    y: { field: 'y', type: 'quantitative' }
                },
                ...parsed
            };

            if (vegaViewRef.current) {
                vegaViewRef.current.finalize();
                vegaViewRef.current = null;
            }

            console.debug('Rendering Vega spec:', vegaSpec);
            const result = await vegaEmbed(container, vegaSpec, {
                actions: false,
                theme: isDarkMode ? 'dark' : 'excel',
                renderer: 'canvas'
            });

            if (!mounted.current) {
                result.view.finalize();
                return;
            }

            vegaViewRef.current = result.view;
            setIsLoading(false);
            hasSuccessfulRenderRef.current = true;
            onLoad?.();

        } catch (error: any) {
            // During streaming, suppress most errors unless the markdown block is closed
            if (isStreaming && !isMarkdownBlockClosed) {
                console.debug('Suppressing streaming error:', error.message);
                // Keep the waiting message visible
                if (!hasSuccessfulRenderRef.current) {
                    setDisplayWaitingMessage(true);
                }
                // Don't update error state during streaming
            } else {
                console.error('Visualization error:', error);
                if (mounted.current) {
                    setRenderError(error.message);
                    setIsLoading(false);
                }
            }
        }
    }, [spec, type, width, height, isDarkMode, isStreaming, isMarkdownBlockClosed, config, onLoad, onError, isLoading, hasAttemptedRender, mounted, renderingStarted]);

    // Main rendering useEffect
    useEffect(() => {
        // Skip re-rendering if we already have a successful render and the markdown block is closed
        if (hasSuccessfulRenderRef.current && isMarkdownBlockClosed) return;
        const currentRender = ++renderIdRef.current;
        console.debug(`Starting render #${currentRender}`);
        initializeVisualization();
    }, [initializeVisualization]);

    // Add a specific effect for theme changes to force re-rendering
    useEffect(() => {
        if (lastSpecRef.current) {
            console.log('Theme changed, re-rendering visualization');
            initializeVisualization();
        }
    }, [isDarkMode, initializeVisualization]);

    const isD3Render = useMemo(() => {
        const plugin = typeof spec === 'object' && spec !== null ? findPlugin(spec) : undefined;
        return type === 'd3' || (typeof spec === 'object' && (spec?.renderer === 'd3' || !!plugin));
    }, [type, spec]);

    // Determine if it's specifically a Mermaid render
    const isMermaidRender = useMemo(() => {
        const plugin = typeof spec === 'object' && spec !== null ? findPlugin(spec) : undefined;
        return plugin?.name === 'mermaid-renderer';
    }, [spec]);

    // Determine if it's specifically a Graphviz render
    const isGraphvizRender = useMemo(() => {
        const plugin = typeof spec === 'object' && spec !== null ? findPlugin(spec) : undefined;
        return plugin?.name === 'graphviz-renderer';
    }, [spec]);

    // Add a specific effect for theme changes to force re-rendering of Mermaid and Graphviz diagrams
    useEffect(() => {
        // Only run this effect when theme changes and we have a Mermaid or Graphviz diagram
        if ((isMermaidRender || isGraphvizRender) && d3ContainerRef.current) {
            console.debug(`Theme changed for ${isMermaidRender ? 'Mermaid' : 'Graphviz'} diagram, re-rendering`);

            // For Mermaid, apply post-render fixes
            if (isMermaidRender) {
                const svgElement = d3ContainerRef.current.querySelector('svg');
                if (svgElement) {
                    if (isDarkMode) {
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

                        // Node and cluster styling
                        svgElement.querySelectorAll('.node rect, .node circle, .node polygon, .node path').forEach(el => {
                            el.setAttribute('stroke', '#81a1c1');
                            el.setAttribute('fill', '#5e81ac');
                        });

                        svgElement.querySelectorAll('.cluster rect').forEach(el => {
                            el.setAttribute('stroke', '#81a1c1');
                            el.setAttribute('fill', '#4c566a');
                        });
                    }
                }
            }

            // For Graphviz, trigger a complete re-render
            if (isGraphvizRender && typeof spec === 'object') {
                // Find the theme button and click it to trigger a re-render
                const themeButton = d3ContainerRef.current.querySelector('.graphviz-theme-button');
                if (themeButton) {
                    (themeButton as HTMLButtonElement).click();
                } else {
                    // If no theme button, force a re-render by triggering a new render cycle
                    // This is a simpler approach that avoids referencing initializeVisualization
                    renderIdRef.current++; // Increment render ID to force a new render

                    // Force re-render by updating a state
                    setIsLoading(true);
                    setTimeout(() => {
                        if (mounted.current) {
                            setIsLoading(false);
                        }
                    }, 10);
                }
            }
        }
    }, [isDarkMode, isMermaidRender, isGraphvizRender, spec]);

    // Determine if it's specifically a Graphviz render
    const isGraphvizRender = useMemo(() => {
        const plugin = typeof spec === 'object' && spec !== null ? findPlugin(spec) : undefined;
        return plugin?.name === 'graphviz-renderer';
    }, [spec]);

    // Determine if it's specifically a Vega-Lite render
    const isVegaLiteRender = useMemo(() => {
        const plugin = typeof spec === 'object' && spec !== null ? findPlugin(spec) : undefined;
        return plugin?.name === 'vega-lite-renderer';
    }, [spec]);

    // Add a specific effect for theme changes to force re-rendering of Mermaid and Graphviz diagrams
    useEffect(() => {
        // Only run this effect when theme changes and we have a Mermaid or Graphviz diagram
        if ((isMermaidRender || isGraphvizRender) && d3ContainerRef.current) {
            console.debug(`Theme changed for ${isMermaidRender ? 'Mermaid' : 'Graphviz'} diagram, re-rendering`);

            // For Mermaid, apply post-render fixes
            if (isMermaidRender) {
                const svgElement = d3ContainerRef.current.querySelector('svg');
                if (svgElement) {
                    if (isDarkMode) {
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

                        // Node and cluster styling
                        svgElement.querySelectorAll('.node rect, .node circle, .node polygon, .node path').forEach(el => {
                            el.setAttribute('stroke', '#81a1c1');
                            el.setAttribute('fill', '#5e81ac');
                        });

                        svgElement.querySelectorAll('.cluster rect').forEach(el => {
                            el.setAttribute('stroke', '#81a1c1');
                            el.setAttribute('fill', '#4c566a');
                        });
                    }
                }
            }

            // For Graphviz, trigger a complete re-render
            if (isGraphvizRender && typeof spec === 'object') {
                // Find the theme button and click it to trigger a re-render
                const themeButton = d3ContainerRef.current.querySelector('.graphviz-theme-button');
                if (themeButton) {
                    (themeButton as HTMLButtonElement).click();
                } else {
                    // If no theme button, force a re-render by triggering a new render cycle
                    // This is a simpler approach that avoids referencing initializeVisualization
                    renderIdRef.current++; // Increment render ID to force a new render

                    // Force re-render by updating a state
                    setIsLoading(true);
                    setTimeout(() => {
                        if (mounted.current) {
                            setIsLoading(false);
                        }
                    }, 10);
                }
            }
        }
    }, [isDarkMode, isMermaidRender, isGraphvizRender, spec]);

    const containerStyles = useMemo(() =>
        isMermaidRender ? {
            height: 'auto !important',
            minHeight: 'unset',
            overflow: 'visible'
        } : {
            height: height || '400px',
            overflow: 'auto'
        }, [isMermaidRender, height]);

    const outerContainerStyle: CSSProperties = {
        position: 'relative',
        width: '100%',
        maxWidth: isVegaLiteRender ? '100%' : undefined,
        height: 'auto',
        display: 'block',
        overflow: 'visible',
        margin: 'lem 0',
        padding: 0,
        boxSizing: 'border-box',
        // Reserve space based on estimated size to prevent layout shifts
        ...(isVegaLiteRender ? {
            // Specific styles for Vega-Lite
            resize: 'horizontal' as const,
        } : {}),
        ...(reservedSize && !hasSuccessfulRenderRef.current ? {
            minHeight: `${reservedSize.height}px`,
            minWidth: `${Math.min(reservedSize.width, 800)}px` // Cap width to prevent horizontal overflow
        } : {})
    };

    // Function to open visualization in a popout window
    const openInPopout = (svg: SVGElement, title: string = 'Visualization') => {
        if (!svg) return;

        // Get the SVG dimensions - need to cast to SVGGraphicsElement to access getBBox
        const svgGraphics = svg as unknown as SVGGraphicsElement;
        let width = 600;
        let height = 400;

        try {
            // Try to get the bounding box
            const bbox = svgGraphics.getBBox();
            width = Math.max(bbox.width + 50, 400); // Add padding, minimum 400px
            height = Math.max(bbox.height + 100, 300); // Add padding, minimum 300px
        } catch (e) {
            console.warn('Could not get SVG dimensions, using defaults', e);
            // Use default dimensions if getBBox fails
        }

        // Get SVG data
        const svgData = new XMLSerializer().serializeToString(svg);

        // Create an HTML document that will display the SVG responsively
        const htmlContent = `
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <title>${title}</title>
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
                    link.download = '${title.toLowerCase().replace(/[^a-z0-9]/g, '-')}-${Date.now()}.svg';
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
            'D3Visualization',
            `width=${width},height=${height},resizable=yes,scrollbars=yes,status=no,toolbar=no,menubar=no,location=no`
        );

        // Focus the new window
        if (popupWindow) {
            popupWindow.focus();
        }

        // Clean up the URL object after a delay
        setTimeout(() => URL.revokeObjectURL(url), 10000);
    };

    // Function to save SVG
    const saveSvg = (svg: SVGElement, filename: string = `visualization-${Date.now()}.svg`) => {
        if (!svg) return;

        // Create a new SVG with proper XML declaration and doctype
        const svgData = new XMLSerializer().serializeToString(svg);

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
        link.download = filename;
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);

        // Clean up the URL object after a delay
        setTimeout(() => URL.revokeObjectURL(url), 1000);
    };

    // Add source modal component
    const SourceModal = () => (
        <Modal
            title="Visualization Source"
            open={isSourceModalVisible}
            onCancel={() => setIsSourceModalVisible(false)}
            footer={null}
            width={800}
        >
            <pre style={{
                backgroundColor: isDarkMode ? '#1f1f1f' : '#f6f8fa',
                padding: '16px',
                borderRadius: '4px',
                overflow: 'auto',
                maxHeight: '60vh',
                color: isDarkMode ? '#e6e6e6' : '#24292e'
            }}>
                <code>{typeof spec === 'string' ? spec : JSON.stringify(spec, null, 2)}</code>
            </pre>
        </Modal>
    );

    // Add action buttons container
    const ActionButtons = () => (
        <div className="diagram-actions">
            <button
                className="diagram-action-button"
                onClick={() => {
                    const container = d3ContainerRef.current;
                    if (container) {
                        const svg = container.querySelector('svg');
                        if (svg) {
                            // Use the new openInPopout function
                            const title = isGraphvizRender ? 'Graphviz Diagram' :
                                isMermaidRender ? 'Mermaid Diagram' :
                                    'D3 Visualization';
                            openInPopout(svg, title);
                        }
                    }
                }}
            >
                ↗️ Open
            </button>
            <button
                className="diagram-action-button"
                onClick={() => {
                    const container = d3ContainerRef.current;
                    if (container) {
                        const svg = container.querySelector('svg');
                        if (svg) {
                            // Use the new saveSvg function
                            const filename = isGraphvizRender ? `graphviz-diagram-${Date.now()}.svg` :
                                isMermaidRender ? `mermaid-diagram-${Date.now()}.svg` :
                                    `visualization-${Date.now()}.svg`;
                            saveSvg(svg, filename);
                        }
                    }
                }}
            >
                💾 Save
            </button>
            <button
                className="diagram-action-button"
                onClick={() => setIsSourceModalVisible(true)}
            >
                📝 Source
            </button>
        </div>
    );

    return (
        <div
            id={containerId || 'd3-container'}
            style={outerContainerStyle}
            className={`d3-container ${isStreaming ? 'streaming' : ''}`}
            data-render-id={renderIdRef.current}
            data-visualization-type={typeof spec === 'object' && spec?.type ? spec.type : 'unknown'}
        >
            {isStreaming && !isMarkdownBlockClosed && !hasSuccessfulRenderRef.current ? (
                <div style={{ textAlign: 'center', padding: '20px', backgroundColor: isDarkMode ? '#1f1f1f' : '#f6f8fa', border: '1px dashed #ccc', borderRadius: '4px' }}>
                    <p>Waiting for complete {typeof spec === 'object' && spec?.type ? spec.type : 'diagram'} definition...</p>
                </div>
            ) : (
                <>
                    {displayWaitingMessage && (
                        <div style={{ textAlign: 'center', padding: '20px', backgroundColor: isDarkMode ? '#1f1f1f' : '#f6f8fa', border: '1px dashed #ccc', borderRadius: '4px' }}>
                            <p>Waiting for complete diagram definition...</p>
                        </div>
                    )}
                </>
            )}

            {isD3Render ? (
                <div
                    ref={d3ContainerRef}
                    className={`d3-container ${isMermaidRender ? 'mermaid-container' : ''}`}
                    style={{
                        ...containerStyles,
                        height: isVegaLiteRender ? 'auto' : containerStyles.height,
                        overflow: isVegaLiteRender ? 'visible' : 'auto',
                        display: 'flex',
                        flexDirection: 'column',
                        alignItems: 'center',
                        width: '100%',
                        position: 'relative',
                        boxSizing: 'border-box'
                    }}
                />
            ) : (
                <div
                    ref={vegaContainerRef}
                    id="vega-container"
                    className="vega-lite-container"
                    style={{
                        width: '100%',
                        maxWidth: '100%',
                        display: !isD3Render ? 'block' : 'none',
                        position: 'relative',
                        height: isVegaLiteRender ? 'auto' : (height || '100%')
                    }}
                >
                    {(isLoading || !spec) && !renderingStarted && (
                        <div style={{
                            position: 'absolute',
                            top: 0,
                            left: 0,
                            right: 0,
                            bottom: 0,
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'center',
                            backgroundColor: 'rgba(0, 0, 0, 0.1)'
                        }}>
                            <Spin size="large" />
                            <div style={{ marginTop: '10px', color: isDarkMode ? '#ffffff' : '#000000' }}>
                                Preparing visualization...
                            </div>
                        </div>
                    )}
                    {renderError && !isStreaming && isMarkdownBlockClosed && (
                        <pre style={{
                            padding: '16px',
                            margin: '8px',
                            backgroundColor: isDarkMode ? '#2a1215' : '#fff1f0',
                            border: `1px solid ${isDarkMode ? '#5c2223' : '#ffa39e'}`,
                            borderRadius: '4px',
                            color: isDarkMode ? '#ff4d4f' : '#cf1322',
                            whiteSpace: 'pre-wrap',
                            wordWrap: 'break-word',
                            maxHeight: '200px',
                            overflowY: 'auto',
                            fontSize: '14px',
                            lineHeight: '1.5',
                            fontFamily: 'monospace'
                        }}>
                            <strong>Error:</strong>
                            {errorDetails.map((line, i) => <div key={i}>{line}</div>)}
                        </pre>
                    )}
                </div>
            )}
            <Modal
                title="Visualization Source"
                open={isSourceModalVisible}
                onCancel={() => setIsSourceModalVisible(false)}
                footer={null}
                width={800}
            >
                <pre style={{
                    backgroundColor: isDarkMode ? '#1f1f1f' : '#f6f8fa',
                    padding: '16px',
                    borderRadius: '4px',
                    overflow: 'auto',
                    maxHeight: '60vh',
                    color: isDarkMode ? '#e6e6e6' : '#24292e'
                }}>
                    <code>{typeof spec === 'string' ? spec : JSON.stringify(spec, null, 2)}</code>
                </pre>
            </Modal>
            <Modal
                title="Visualization Source"
                open={isSourceModalVisible}
                onCancel={() => setIsSourceModalVisible(false)}
                footer={null}
                width={800}
            >
                <pre style={{
                    backgroundColor: isDarkMode ? '#1f1f1f' : '#f6f8fa',
                    padding: '16px',
                    borderRadius: '4px',
                    overflow: 'auto',
                    maxHeight: '60vh',
                    color: isDarkMode ? '#e6e6e6' : '#24292e'
                }}>
                    <code>{typeof spec === 'string' ? spec : JSON.stringify(spec, null, 2)}</code>
                </pre>
            </Modal>
        </div>
    );
};
