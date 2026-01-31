import React, { useState, useEffect, useRef, useMemo, CSSProperties, useCallback } from 'react';
import { useTheme } from '../context/ThemeContext';
import { Spin, Modal } from 'antd';
import { D3RenderPlugin } from '../types/d3';
import { findPluginForSpec, loadPlugin } from '../plugins/d3/registry';
import { isDiagramDefinitionComplete } from '../utils/diagramUtils';
import { ContainerSizingManager } from '../utils/containerSizing';
import { isSafari } from '../utils/browserUtils';

type RenderType = 'auto' | 'vega-lite' | 'd3';

// Global cache to survive component remounting
const globalRenderCache = new Map<string, {
    rendered: boolean;
    timestamp: number;
}>();

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

// Helper function to sanitize specs by removing null/undefined values
function sanitizeSpec(obj: any): any {
    if (obj === undefined) {
        return undefined;
    }
    if (obj === null) {
        return null;
    }
    if (Array.isArray(obj)) {
        return obj.map(sanitizeSpec).filter(v => v !== undefined);
    }
    if (typeof obj === 'object') {
        const newObj: any = {};
        for (const key in obj) {
            if (Object.prototype.hasOwnProperty.call(obj, key)) {
                const value = sanitizeSpec(obj[key]);
                if (value !== undefined) {
                    newObj[key] = value;
                }
            }
        }
        return newObj;
    }
    return obj;
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
    forceRender = false,
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
    const [showRawContent, setShowRawContent] = useState(true); // Start with raw content visible
    const [hasAttemptedRender, setHasAttemptedRender] = useState<boolean>(false);
    const [errorDetails, setErrorDetails] = useState<string[]>([]);
    const cleanupRef = useRef<(() => void) | null>(null);
    const simulationRef = useRef<any>(null);
    const [vegaEmbed, setVegaEmbed] = useState<any>(null);
    const [d3, setD3] = useState<any>(null);
    const [plugin, setPlugin] = useState<D3RenderPlugin | null>(null);

    const [isSourceModalVisible, setIsSourceModalVisible] = useState(false);
    const renderIdRef = useRef<number>(0);
    const mounted = useRef(true);
    const isRenderingRef = useRef(false);
    const isLoadingPluginRef = useRef(false);
    const lastSpecRef = useRef<any>(null);
    const specHashRef = useRef<string>('');
    const streamingContentRef = useRef<string | null>(null);
    const lastUsedPluginRef = useRef<D3RenderPlugin | null>(null);
    const lastValidSpecRef = useRef<any>(null);
    const [renderingStarted, setRenderingStarted] = useState<boolean>(false);
    const hasSuccessfulRenderRef = useRef<boolean>(false);
    const lastThemeRef = useRef<boolean>(isDarkMode);
    const [hasSuccessfulRender, setHasSuccessfulRender] = useState<boolean>(false);
    const isDarkModeRef = useRef<boolean>(isDarkMode);

    // Generate a stable cache key for this spec
    const cacheKey = useMemo(() => {
        if (typeof spec === 'string') {
            return spec;
        } else if (spec?.definition) {
            return spec.definition;
        } else {
            return JSON.stringify(spec);
        }
    }, [spec]);

    // New state for size reservation and rendering control
    const cleanupFunctionsRef = useRef<(() => void)[]>([]);
    const [reservedSize, setReservedSize] = useState<{ width: number; height: number } | null>(null);
    const sizingManagerRef = useRef<ContainerSizingManager | null>(null);

    // Store the spec in a ref to avoid unnecessary re-renders
    useEffect(() => { lastSpecRef.current = spec; }, [spec]);

    // Keep isDarkMode ref in sync
    useEffect(() => {
        isDarkModeRef.current = isDarkMode;
    }, [isDarkMode]);

    // Get raw content for display during streaming
    const getRawContent = useCallback(() => {
        if (typeof spec === 'string') {
            return spec;
        } else if (spec?.definition) {
            return spec.definition;
        } else if (typeof spec === 'object') {
            return JSON.stringify(spec, null, 2);
        }
        return '';
    }, [spec]);

    const rawContent = getRawContent();

    // Size reservation removed - will be handled after plugin loads

    // Control when to show raw content vs rendered visualization
    useEffect(() => {
        console.log('🔍 RENDERER: showRawContent effect triggered', {
            hasSuccessfulRender: hasSuccessfulRenderRef.current,
            isMarkdownBlockClosed,
            isStreaming,
            hasPlugin: !!plugin,
            pluginName: plugin?.name,
            hasD3: !!d3,
            isLoading,
            currentShowRawContent: showRawContent,
            renderingStarted
        });

        // If we've already had a successful render and the block is closed, always keep showing the rendered version
        if (hasSuccessfulRenderRef.current && isMarkdownBlockClosed) {
            console.log('🔍 RENDERER: Keeping rendered version visible (successful render + closed block)');
            setShowRawContent(false);
            return;
        }

        // Show raw content if:
        // 1. This diagram is being streamed AND we don't have a successful render yet
        // 2. Plugin is not loaded yet
        // 3. Markdown block is not closed
        // 4. d3 is not loaded yet
        if ((isStreaming && !hasSuccessfulRenderRef.current) ||
            !plugin ||
            !d3 ||
            !isMarkdownBlockClosed ||
            isLoading) {
            console.log('🔍 RENDERER: Setting showRawContent=true');
            setShowRawContent(true);
        } else {
            console.log('🔍 RENDERER: Setting showRawContent=false');
            setShowRawContent(false);
        }
    }, [isStreaming, isMarkdownBlockClosed, hasSuccessfulRender, plugin, d3, isLoading]);
    // Comprehensive cleanup on unmount
    useEffect(() => {
        return () => {
            // Clean up old cache entries (> 30 seconds old) to prevent memory leak
            const now = Date.now();
            for (const [key, entry] of globalRenderCache.entries()) {
                if (now - entry.timestamp > 30000) {
                    globalRenderCache.delete(key);
                }
            }

            // Clean up sizing manager
            if (sizingManagerRef.current) {
                sizingManagerRef.current.cleanup();
                sizingManagerRef.current = null;
            }
            mounted.current = false;
            console.debug('D3Renderer cleanup triggered');

            // Execute all cleanup functions
            cleanupFunctionsRef.current.forEach(cleanup => {
                try {
                    cleanup();
                } catch (e) {
                    console.warn('Error during cleanup:', e);
                }
            });
            cleanupFunctionsRef.current = [];

            // Clean up Vega view
            try {
                if (vegaViewRef.current) {
                    vegaViewRef.current.finalize();
                    vegaViewRef.current = null;
                }
            } catch (e) {
                console.warn('Error finalizing Vega view:', e);
            }

            // Clean up D3 and simulations
            try {
                if (simulationRef.current) {
                    simulationRef.current.stop();
                    simulationRef.current = null;
                }
                if (d3ContainerRef.current) {
                    d3?.select(d3ContainerRef.current).selectAll('*').on('.', null);
                    d3ContainerRef.current.innerHTML = '';
                }
            } catch (e) {
                console.warn('Error cleaning up D3:', e);
            }
        };
    }, []);

    // NEW: Listen for Vega-Lite plugin completion events
    useEffect(() => {
        const container = vegaContainerRef.current || d3ContainerRef.current;
        if (!container) return;

        const handleVegaRenderComplete = (event: Event) => {
            const customEvent = event as CustomEvent;
            console.log('🎉 VEGA-COMPLETE: Received vega-render-complete event', customEvent.detail);
            
            // Update component state to reflect successful render
            if (mounted.current && customEvent.detail?.success) {
                console.log('🎉 VEGA-COMPLETE: Setting component state for successful render');
                setIsLoading(false);
                setRenderError(null);
                hasSuccessfulRenderRef.current = true;
                setHasSuccessfulRender(true); // NEW: Trigger state update for effects
                setShowRawContent(false);
                onLoad?.();

                // Update global cache
                globalRenderCache.set(cacheKey, {
                    rendered: true,
                    timestamp: Date.now()
                });
            }
        };

        container.addEventListener('vega-render-complete', handleVegaRenderComplete);
        return () => container.removeEventListener('vega-render-complete', handleVegaRenderComplete);
    }, []);

    // Initialize visualization with useCallback for better performance and dependency tracking
    const initializeVisualization = useCallback(async (forceRender = false): Promise<void> => {
        if (!mounted.current) return;

        // Check global cache to avoid duplicate work across component instances
        const cacheEntry = globalRenderCache.get(cacheKey);
        const wasCachedRecently = cacheEntry && (Date.now() - cacheEntry.timestamp) < 5000;

        if (wasCachedRecently && !forceRender && cacheEntry.rendered) {
            console.log('🔧 D3RENDERER: Skipping - already rendered by another instance within 5s');
            hasSuccessfulRenderRef.current = true;
            setIsLoading(false);
            setShowRawContent(false);  // Critical: hide raw content since we already rendered
            return;
        }

        // If we already have a successful render and not forcing, skip
        if (hasSuccessfulRenderRef.current && !forceRender) {
            console.log('🔧 D3RENDERER: Skipping render - already successfully rendered and not forcing');
            return;
        }

        // Prevent concurrent plugin loading
        if (isLoadingPluginRef.current) {
            console.log('🔧 D3RENDERER: Plugin already loading, skipping duplicate call');
            return;
        }

        console.log('🔧 D3RENDERER: initializeVisualization called, isRendering:', isRenderingRef.current, {
            hasPlugin: !!plugin,
            hasD3: !!d3,
            specType: spec?.type
        });

        // Track loaded values for immediate use
        let loadedPlugin: D3RenderPlugin | undefined = undefined;
        let loadedD3: any = undefined;

        // Load the appropriate plugin for this spec
        if (!plugin && spec) {
            isLoadingPluginRef.current = true;
            console.log('🔧 D3RENDERER: Loading plugin for spec:', spec.type);
            loadedPlugin = await findPluginForSpec(spec);
            isLoadingPluginRef.current = false;
            if (!loadedPlugin) {
                console.error('🔧 D3RENDERER: No compatible plugin found for spec:', spec);
                setRenderError('No compatible plugin found for this visualization');
                setIsLoading(false);
                return;
            }
            console.log('🔧 D3RENDERER: Plugin loaded:', loadedPlugin.name);
            setPlugin(loadedPlugin);
        }
        
        // Load d3 separately if needed (not tied to plugin loading)
        if (!d3 && !loadedD3) {
            console.log('🔧 D3RENDERER: Loading d3 module');
            loadedD3 = await import('d3');
            setD3(loadedD3);
        }

        // Get current values (either from state or just loaded)
        const currentPlugin = loadedPlugin || plugin;
        const currentD3 = loadedD3 || d3;

        // Early exit if we still don't have plugin or d3
        if (!currentPlugin || !currentD3) {
            console.log('🔧 D3RENDERER: Waiting for plugin and d3 to load...', { hasPlugin: !!plugin, hasD3: !!d3 });
            return;
        }
        
        // CRITICAL: Once we have both plugin and d3, don't reload them
        // This prevents the 19 redundant "Plugin loaded" messages during streaming
        if (!plugin) setPlugin(currentPlugin);
        if (!d3) setD3(currentD3);

        // Once we start rendering, hide the spinner permanently
        if (!renderingStarted) {
            setRenderingStarted(true);
        }

        // Don't show loading state if we're showing raw content
        // But DO show it briefly while loading plugin
        if (!plugin || !d3) {
            setIsLoading(true);
        } else if ((!hasAttemptedRender || !isStreaming) && !hasSuccessfulRenderRef.current && !showRawContent) {
            setIsLoading(true);
        }

        console.log('🔧 D3RENDERER: Starting render with plugin:', currentPlugin.name);

        // Prevent concurrent renders that cause loops in Safari
        // But check AFTER async plugin loading completes
        if (isRenderingRef.current && !forceRender) {
            console.log('Skipping concurrent render to prevent Safari loop');
            return;
        }

        console.log('🔧 D3RENDERER: Setting isRendering=true');
        isRenderingRef.current = true;
        try {
            // Clear previous cleanup functions
            cleanupFunctionsRef.current.forEach(cleanup => cleanup());
            cleanupFunctionsRef.current = [];

            if (!spec) {
                throw new Error('No specification provided');
            }

            let parsed: any;
            let specLines: string[];

            // Determine if we should attempt to render or show waiting message
            let attemptRender = !isStreaming || isMarkdownBlockClosed || forceRender;
            let localShouldShowWaitingPlaceholder = isStreaming && !isMarkdownBlockClosed && !forceRender;

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
                    if (isLoading && !hasSuccessfulRenderRef.current) setIsLoading(false);
                    setDisplayWaitingMessage(true);
                    return;
                } else {
                    setIsLoading(true); // Keep loading while we wait for complete spec
                    console.debug('Waiting for complete spec:', parseError);
                    return;
                }
            }

            // If we have a parsed spec, determine if it's complete enough to render
            if (parsed) {
                const specType = parsed.type || '';

                // For streaming content, check if the definition is complete enough
                if (isStreaming && !isMarkdownBlockClosed) {
                    // If we have a plugin with isDefinitionComplete method, use it on the original spec string
                    if (plugin?.isDefinitionComplete && typeof spec === 'string') {
                        const isComplete = plugin.isDefinitionComplete(spec);
                        console.debug(`Checking if ${plugin.name} definition is complete:`, isComplete);

                        if (!isComplete) {
                            attemptRender = false;
                        }
                    } else if (specType === 'mermaid' || specType === 'graphviz') {
                        // Use the generic isDiagramDefinitionComplete utility
                        const isComplete = isDiagramDefinitionComplete(
                            parsed.definition || '',
                            specType
                        );

                        if (!isComplete) {
                            attemptRender = false;
                        }
                    }
                }

                // If we're going to attempt rendering, store this as our last valid spec
                if (attemptRender) {
                    lastValidSpecRef.current = parsed;
                }
            }

            // Update the waiting message state based on streaming and render attempt
            setDisplayWaitingMessage(localShouldShowWaitingPlaceholder);

            // If we're not attempting to render, exit early
            if (!attemptRender) {
                return;
            } else {
                // Mark that we've attempted a render
                setHasAttemptedRender(true);
            }

            // Log the parsed spec for debugging
            console.debug('D3Renderer: Successfully parsed spec:', {
                type: parsed.type,
                renderer: parsed.renderer,
                pluginName: plugin?.name
            });

            // Only proceed with rendering if we have a valid parsed spec
            if (currentPlugin && (type === 'd3' || parsed.renderer === 'd3' || typeof parsed.render === 'function')) {
                const container = d3ContainerRef.current;
                if (!container || !currentPlugin) return;

                console.log('🔧 D3RENDERER: About to render with plugin:', currentPlugin.name);

                // Check if this is a Graphviz or Mermaid plugin
                lastUsedPluginRef.current = currentPlugin || null;
                // Initialize sizing manager if we have a plugin with sizing config
                if (currentPlugin?.sizingConfig && !sizingManagerRef.current) {
                    sizingManagerRef.current = new ContainerSizingManager();
                    sizingManagerRef.current.applySizingConfig(container, currentPlugin.sizingConfig, isDarkModeRef.current);
                    cleanupFunctionsRef.current.push(() => {
                        sizingManagerRef.current?.cleanup();
                    });
                }

                // Cleanup existing simulation
                if (simulationRef.current) {
                    try {
                        simulationRef.current.stop();
                        simulationRef.current = null;
                    } catch (error) {
                        console.warn('Error cleaning up simulation:', error);
                    }
                    simulationRef.current = null;
                }

                // Set container dimensions - but allow height to grow for joint-renderer and drawio-renderer
                const isJointRenderer = plugin?.name === 'joint-renderer';
                const isDrawioRenderer = currentPlugin?.name === 'drawio-renderer';

                // Also check if container will contain error content
                const willHaveError = renderError !== null;

                container.style.width = (isJointRenderer || isDrawioRenderer) ? '100%' : `${width}px`;
                // Allow auto height for joint, drawio, and error states
                container.style.height = (isJointRenderer || isDrawioRenderer || willHaveError) ? 'auto' : `${height}px`;
                container.style.minHeight = isJointRenderer ? '400px' : 'unset';
                container.style.maxHeight = (isJointRenderer || willHaveError) ? 'none' : 'unset';
                container.style.position = 'relative';
                // Use visible/auto overflow for joint, drawio, and errors
                container.style.overflow = (isJointRenderer || isDrawioRenderer || willHaveError) ? 'visible' : 'hidden';
                const isGraphvizOrMermaid = currentPlugin?.name === 'graphviz-renderer' || currentPlugin?.name === 'mermaid-renderer';
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
                tempContainer.style.height = 'auto';

                let renderSuccessful = false;

                try {
                    // Clear any existing content
                    if (container.firstChild) {
                        container.removeChild(container.firstChild);
                    }

                    const sanitizedParsed = sanitizeSpec(parsed);
                    if (typeof parsed.render === 'function') {
                        const result = sanitizedParsed.render.call(sanitizedParsed, tempContainer, d3);
                        renderSuccessful = true;

                        // If result is a function, use it as cleanup
                        if (typeof result === 'function') {
                            cleanupRef.current = result;
                            cleanupFunctionsRef.current.push(result);
                        }

                        // Register simulation cleanup if it exists
                        if (simulationRef.current) {
                            cleanupFunctionsRef.current.push(() => {
                                if (simulationRef.current) {
                                    simulationRef.current.stop();
                                    simulationRef.current = null;
                                }
                            });
                        }
                    } else if (currentPlugin) {
                        console.log('🔧 D3RENDERER: Calling plugin.render for:', currentPlugin.name);

                        // Add timeout for plugin render to prevent infinite hang
                        const renderPromise = Promise.race([
                            currentPlugin.render(tempContainer, currentD3, {
                                ...sanitizedParsed,
                                width: width || 600,
                                height: height || 400,
                                isStreaming: isStreaming,
                                isMarkdownBlockClosed: isMarkdownBlockClosed,
                                forceRender: forceRender,
                            }, isDarkModeRef.current),
                            new Promise((_, reject) =>
                                setTimeout(() => reject(new Error('Plugin render timeout after 10s')), 10000)
                            )
                        ]);

                        console.log('🔧 D3RENDERER: Awaiting plugin render...');
                        await renderPromise;
                        console.log('🔧 D3RENDERER: Plugin render completed');

                        renderSuccessful = true;
                    } else {
                        throw new Error('No render function or compatible plugin found');
                    }
                } catch (renderError) {
                    console.error('D3 render error:', renderError);
                    if (mounted.current) {
                        const errorMessage = renderError instanceof Error ? renderError.message : 'Render failed';
                        setRenderError(errorMessage);
                        setErrorDetails([errorMessage]);

                        // If it was a timeout, show helpful message
                        if (errorMessage.includes('timeout')) {
                            setErrorDetails([
                                'Rendering timed out - this diagram may be too complex or malformed',
                                'Try simplifying the diagram or check the definition for errors'
                            ]);
                        }
                    }
                }

                // Only replace main container content if render was successful
                if (renderSuccessful) {
                    container.innerHTML = '';
                    container.appendChild(tempContainer);
                }

                // Add retry button for Mermaid diagrams if there was an error
                if (renderError && (currentPlugin?.name === 'mermaid-renderer')) {
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
                console.log('🔍 RENDERER: Render successful, setting hasSuccessfulRenderRef=true');
                onLoad?.();
                hasSuccessfulRenderRef.current = true;

                // Update global cache
                globalRenderCache.set(cacheKey, {
                    rendered: true,
                    timestamp: Date.now()
                });
                return;
            }
        } catch (error: any) {
            if ((isStreaming && !isMarkdownBlockClosed) || !currentPlugin) {
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
        } finally {
            console.log('🔧 D3RENDERER: Setting isRendering=false');
            isRenderingRef.current = false;
        }
    }, []);

    // Separate effect to trigger re-render when plugin/d3 loads
    // This effect runs ONLY when plugin or d3 changes from null to loaded
    useEffect(() => {
        // Don't trigger if we already have a successful render and spec hasn't changed
        // This prevents ~10 redundant trigger attempts after initial success
        if (hasSuccessfulRenderRef.current) {
            console.log('🔧 D3RENDERER: Plugin/d3 available but already rendered, skipping');
            return;
        }

        if (plugin && d3 && spec) {
            console.log('🔧 D3RENDERER: Plugin and d3 available, triggering render');
            initializeVisualization(forceRender);
        }
    }, [plugin, d3]); // Removed spec, initializeVisualization, forceRender to prevent redundant triggers
    // Note: spec changes are already handled by the main rendering useEffect below
    // This effect only needs to trigger when dependencies become available

    // Main rendering useEffect with stable dependencies
    useEffect(() => {
        if (!mounted.current) return;

        // Create a stable hash that excludes timestamp and streaming flags
        const specHash = typeof spec === 'string' ? spec :
            [
                spec?.definition || '',
                spec?.type || '',
                spec?.width || '',
                spec?.height || ''
            ].join('|');

        // Skip if spec hasn't changed AND we already have a successful render
        if (specHash === specHashRef.current && !forceRender && hasSuccessfulRenderRef.current) {
            console.debug('Skipping render - spec unchanged and already successfully rendered');
            return;
        }

        // Update hash even if we don't render yet (to track changes)
        specHashRef.current = specHash;

        // Trigger rendering immediately when markdown block closes, even during streaming
        const shouldRender = (
            !hasSuccessfulRenderRef.current ||
            isMarkdownBlockClosed ||  // Render as soon as block closes, don't wait for streaming to end
            forceRender
        ) && spec && (typeof spec === 'string' ? spec.trim().length > 0 : true);

        if (shouldRender) {
            const currentRender = ++renderIdRef.current;
            console.debug(`Starting render #${currentRender}, isStreaming: ${isStreaming}, blockClosed: ${isMarkdownBlockClosed}, hasPlugin: ${!!plugin}, hasSuccessfulRender: ${hasSuccessfulRenderRef.current}`);
            initializeVisualization(forceRender);
        }
    }, [spec, isStreaming, isMarkdownBlockClosed, forceRender, initializeVisualization]);
    // Separate effect for theme changes to avoid circular dependencies
    useEffect(() => {
        // Show Safari-specific warning for diagram rendering issues
        if (isSafari() && (renderError || !hasSuccessfulRenderRef.current)) {
            console.warn('🍎 SAFARI-COMPAT: Safari detected with rendering issues. Consider showing browser upgrade notice.');
        }

        // Detect Safari for theme change handling
        const safariDetected = isSafari();

        // For Safari users, add an additional notice about potential issues
        if (safariDetected && renderError) {
            console.warn('🍎 SAFARI-NOTICE: Diagram rendering failed on Safari. This is a known compatibility issue.');
        }

        console.log('🎨 THEME-EFFECT: Theme effect triggered', {
            isDarkMode,
            lastTheme: lastThemeRef.current,
            themeChanged: isDarkMode !== lastThemeRef.current,
            hasSpec: !!lastSpecRef.current,
            hasSuccessfulRender: hasSuccessfulRenderRef.current,
            renderingStarted
        });

        // Only re-render for theme changes if we've already had a successful render
        // and the theme actually changed from the last render
        if (lastSpecRef.current && hasSuccessfulRenderRef.current && renderingStarted &&
            isDarkMode !== lastThemeRef.current) {
            console.log('Theme changed, re-rendering visualization');
            lastThemeRef.current = isDarkMode; // Update to track the new theme

            // Use a longer debounce for Safari to prevent render loops
            const debounceTime = safariDetected ? 1000 : 100; // Even longer for Safari

            const themeChangeTimer = setTimeout(() => {
                // Extra safety check for Safari
                if (isSafari() && isRenderingRef.current) {
                    console.log('Safari: Skipping theme change render due to concurrent operation');
                    return;
                }

                try {
                    initializeVisualization(true); // Force render on theme change
                } catch (error) {
                    console.error('Theme change render failed:', error);
                }
            }, debounceTime);

            return () => clearTimeout(themeChangeTimer);
        }
    }, [isDarkMode]); // Remove initializeVisualization dependency to break circular dependency

    const isD3Render = useMemo(() => {
        return type === 'd3' || (typeof spec === 'object' && (spec?.renderer === 'd3' || !!plugin));
    }, [type, spec]);

    // Get container styles from plugin config or use defaults
    const containerStyles = useMemo(() => {
        if (plugin?.sizingConfig?.containerStyles) {
            const baseStyles = plugin.sizingConfig.containerStyles;

            // Apply sizing strategy overrides
            if (plugin.sizingConfig.sizingStrategy === 'auto-expand') {
                return {
                    ...baseStyles,
                    height: 'auto',
                    maxHeight: 'none',
                    minHeight: 'auto',
                    overflow: plugin.sizingConfig.needsOverflowVisible ? 'visible' : (baseStyles.overflow || 'auto')
                };
            } else if (plugin.sizingConfig.sizingStrategy === 'responsive') {
                return {
                    ...baseStyles,
                    width: '100%',
                    maxWidth: '100%'
                };
            }

            // For plugins that need responsive width, don't use the width prop
            if (plugin?.name === 'drawio-renderer') {
                return {
                    ...baseStyles,
                    width: '100%'
                };
            }

            return baseStyles;
        }
        return {
            width: '100%',
            height: 'auto',
            overflow: 'auto'
        };
    }, [plugin, height]);
    const outerContainerStyle: CSSProperties = {
        position: 'relative',
        width: '100%',
        maxWidth: '100%',
        height: 'auto',
        minHeight: 'auto',
        display: 'block',
        margin: '1em 0',
        padding: 0,
        boxSizing: 'border-box',
        minWidth: '100%',
        // Reserve space based on estimated size to prevent layout shifts
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

    return (
        <div
            id={containerId || 'd3-container'}
            style={outerContainerStyle}
            className={`d3-container ${isStreaming ? 'streaming' : ''}`}
            data-render-id={renderIdRef.current}
            data-visualization-type={typeof spec === 'object' && spec?.type ? spec.type : 'unknown'}
        >
            {/* Show raw content during streaming */}
            {showRawContent && rawContent ? (
                <div style={{
                    position: 'relative',
                    backgroundColor: isDarkMode ? '#1f1f1f' : '#f6f8fa',
                    border: `1px solid ${isDarkMode ? '#303030' : '#e1e4e8'}`,
                    borderRadius: '6px',
                    padding: '16px',
                    margin: '16px 0'
                }}>
                    <div style={{
                        fontSize: '12px',
                        color: isDarkMode ? '#8b949e' : '#586069',
                        marginBottom: '8px',
                        fontWeight: 'bold'
                    }}>
                        {typeof spec === 'object' && spec?.type ? `${spec.type.charAt(0).toUpperCase() + spec.type.slice(1)} Specification` : 'Diagram Specification'}
                    </div>
                    <pre style={{
                        margin: 0,
                        color: isDarkMode ? '#e6e6e6' : '#24292e',
                        fontSize: '13px',
                        lineHeight: '1.45',
                        whiteSpace: 'pre-wrap',
                        wordBreak: 'break-word',
                        fontFamily: 'Monaco, Menlo, "Ubuntu Mono", monospace'
                    }}>{rawContent}</pre>
                </div>
            ) : null}

            {isD3Render ? (
                <>
                    {isSafari() && (
                        <div style={{
                            backgroundColor: isDarkMode ? '#2b2111' : '#fffbe6',
                            border: `1px solid ${isDarkMode ? '#d4b106' : '#d4b106'}`,
                            borderRadius: '4px',
                            padding: '12px',
                            margin: '8px 0',
                            fontSize: '13px',
                            color: isDarkMode ? '#faad14' : '#d46b08'
                        }}>
                            ⚠️ <strong>Safari Compatibility Notice:</strong> Diagrams may not render correctly in Safari.
                            For best results, please use Chrome, Edge, Firefox, or another modern browser.
                        </div>
                    )}
                    <div
                        ref={d3ContainerRef}
                        className={`d3-container ${plugin?.name ? `${plugin.name}-container` : ''}`}
                        style={{
                            width: plugin?.name === 'drawio-renderer' ? '100%' : (containerStyles.width || '100%'),
                            height: containerStyles.height || 'auto',
                            display: 'flex',
                            flexDirection: 'column',
                            alignItems: 'center',
                            position: 'relative',
                            boxSizing: 'border-box',
                            // Apply remaining containerStyles (excluding width/height to avoid override)
                            overflow: containerStyles.overflow,
                            maxWidth: containerStyles.maxWidth,
                            minHeight: containerStyles.minHeight,
                            maxHeight: containerStyles.maxHeight
                        }}
                    />
                </>
            ) : (
                <div
                    ref={vegaContainerRef}
                    id="vega-container"
                    className="vega-lite-container"
                    style={{
                        width: '100%',
                        minWidth: '100%',
                        maxWidth: '100%',
                        display: !isD3Render ? 'block' : 'none',
                        position: 'relative',
                        height: height || '100%'
                    }}
                >
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
                    <div style={{
                        fontWeight: 'bold',
                        color: isDarkMode ? '#58a6ff' : '#0366d6',
                        marginBottom: '12px',
                        fontSize: '14px'
                    }}>
                        {(() => {
                            if (typeof spec === 'object' && spec?.type === 'mermaid') return '🧩 Mermaid Diagram Source:';
                            if (typeof spec === 'object' && (spec?.$schema?.includes('vega-lite') || spec?.mark)) return '📊 Vega-Lite Specification:';
                            if (typeof spec === 'object' && spec?.type === 'graphviz') return '🔗 Graphviz Source:';
                            if (typeof spec === 'string' && spec.includes('graph') && spec.includes('->')) return '🔗 Graphviz Source:';
                            if (typeof spec === 'string' && (spec.includes('flowchart') || spec.includes('sequenceDiagram'))) return '🧩 Mermaid Diagram Source:';
                            return '📄 Diagram Source:';
                        })()}
                    </div>
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
                    overflowX: 'auto',
                    overflowY: 'auto',
                    maxHeight: '60vh',
                    color: isDarkMode ? '#e6e6e6' : '#24292e',
                    margin: 0
                }}>
                    <code style={{
                        display: 'block',
                        whiteSpace: 'pre-wrap',
                        overflowX: 'auto',
                        overflowY: 'auto',
                        wordBreak: 'break-word',
                        wordWrap: 'break-word',
                        minWidth: '100%',
                        width: 'max-content'
                    }}>{typeof spec === 'string' ? spec : JSON.stringify(spec, null, 2)}</code>
                </pre>
            </Modal>
        </div>
    );
};
