import React, { useState, useEffect, useRef, useMemo, CSSProperties } from 'react';
import { useTheme } from '../context/ThemeContext';
import { Spin, Modal } from 'antd';
import vegaEmbed from 'vega-embed';
import * as d3 from 'd3';
import { D3RenderPlugin } from '../types/d3';
import { d3RenderPlugins } from '../plugins/d3/registry';

type RenderType = 'auto' | 'vega-lite' | 'd3';

interface D3RendererProps {
    spec: any;
    width?: number;
    height?: number;
    containerId?: string;  
    type?: RenderType;
    onLoad?: () => void;  
    onError?: (error: Error) => void; 
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

export const D3Renderer: React.FC<D3RendererProps> = ({
    spec,
    width = 600,
    height = 400,
    containerId,
    type = 'auto',
    onLoad,
    onError,
    config
}) => {
    const { isDarkMode } = useTheme();
    const vegaContainerRef = useRef<HTMLDivElement>(null);
    const d3ContainerRef = useRef<HTMLDivElement>(null);
    const vegaViewRef = useRef<any>(null);
    const [isLoading, setIsLoading] = useState(true);
    const [renderError, setRenderError] = useState<string | null>(null);
    const [errorDetails, setErrorDetails] = useState<string[]>([]);
    const cleanupRef = useRef<(() => void) | null>(null);
    const simulationRef = useRef<any>(null);
    const [isSourceModalVisible, setIsSourceModalVisible] = useState(false);
    const renderIdRef = useRef<number>(0);
    const mounted = useRef(true);

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

// Main rendering useEffect
useEffect(() => {
    const currentRender = ++renderIdRef.current;
    console.debug(`Starting render #${currentRender}`);

    const initializeVisualization = async () => {
        setIsLoading(true);
        try {
            if (!spec) {
                throw new Error('No specification provided');
                    return;
            }

            let parsed: any;
            let specLines: string[];

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
                setIsLoading(true); // Keep loading while we wait for complete spec
                console.debug('Waiting for complete spec:', parseError);
                return;
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
                
                if (currentRender !== renderIdRef.current) {
                    console.debug(`Skipping stale render #${currentRender}`);
                    return;
                }

                // Cleanup existing simulation
                if (simulationRef.current) {
                    try {
                        simulationRef.current.stop();
                        simulationRef.current = null;
                    } catch (error) {
                        console.warn('Error cleaning up simulation:', error);
                    }
                }

                // Set container dimensions
                container.style.width = `${width}px`;
                container.style.height = `${height}px`;
                container.style.position = 'relative';
                container.style.overflow = 'hidden';

                // Create temporary container for safe rendering
                const tempContainer = document.createElement('div');
                tempContainer.style.width = '100%';
                tempContainer.style.height = '100%';

                let renderSuccessful = false;

                try {
                    // Clear any existing content
                    while (container.firstChild) {
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
                                height: height || 400
                            }, isDarkMode);
                            renderSuccessful = true;
                        }
                    } else {
                        const plugin = findPlugin(parsed);
                        if (!plugin) {
                            throw new Error('No render function or compatible plugin found');
                        }
                        
                        console.debug('Using plugin:', plugin.name);
                        plugin.render(tempContainer, d3, parsed, isDarkMode);
                        renderSuccessful = true;
                    }

                    // Only replace main container content if render was successful
                    if (renderSuccessful) {
                        container.innerHTML = '';
                        container.appendChild(tempContainer);
                    }
                } catch (renderError) {
                    console.error('D3 render error:', renderError);
                    setRenderError(renderError instanceof Error ? renderError.message : 'Render failed');
                    throw renderError;
                }

                if (!renderSuccessful) {
                    throw new Error('Render did not complete successfully');
                }

                setIsLoading(false);
                setRenderError(null);
                onLoad?.();
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

            if (!mounted.current) {
                result.view.finalize();
                return;
            }

            vegaViewRef.current = result.view;
            setIsLoading(false);
            onLoad?.();

        } catch (error: any) {
            console.error('Visualization error:', error);
            // Only show error if we're not just waiting for complete spec
            if (error.message !== 'Unexpected end of JSON input') {
                setRenderError(error.message);
                setErrorDetails([error.message]);
            }
            setIsLoading(false);
        }
    };
    
    if (mounted.current) {
        initializeVisualization();
    }

    return () => {
        mounted.current = false;
    };
}, [spec, type, width, height, isDarkMode]);


    const isD3Render = useMemo(() => {
        const plugin = typeof spec === 'object' && spec !== null ? findPlugin(spec) : undefined;
        return type === 'd3' || (typeof spec === 'object' && (spec?.renderer === 'd3' || !!plugin));
    }, [type, spec]);

    // Determine if it's specifically a Mermaid render
    const isMermaidRender = useMemo(() => {
        const plugin = typeof spec === 'object' && spec !== null ? findPlugin(spec) : undefined;
        return plugin?.name === 'mermaid-renderer';
    }, [spec]);

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
        height: 'auto',
        display: 'block',
        overflow: 'visible',
        margin: 'lem 0',
        padding: 0,
        boxSizing: 'border-box',
    };

    const formatSource = (source: any): string => {
        try {
            return JSON.stringify(JSON.parse(typeof source === 'string' ? source : JSON.stringify(source)), null, 2);
        } catch (e) {
            return typeof source === 'string' ? source : JSON.stringify(source, null, 2);
        }
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
                            const svgData = new XMLSerializer().serializeToString(svg);
                            const dataUri = `data:image/svg+xml;base64,${btoa(unescape(encodeURIComponent(svgData)))}`;
                            window.open(dataUri, '_blank');
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
                            const svgData = new XMLSerializer().serializeToString(svg);
                            const dataUri = `data:image/svg+xml;base64,${btoa(unescape(encodeURIComponent(svgData)))}`;
                            const link = document.createElement('a');
                            link.href = dataUri;
                            link.download = `visualization-${Date.now()}.svg`;
                            document.body.appendChild(link);
                            link.click();
                            document.body.removeChild(link);
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
        >
            {isD3Render? (
                <div 
                    ref={d3ContainerRef}
                    className={`d3-container ${isMermaidRender ? 'mermaid-container' : ''}`}
                    style={{
                        ...containerStyles,
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
                    style={{
                        display: !isD3Render ? 'block' : 'none',
                        position: 'relative',
                        width: '100%',
                        height: height || '100%'
                    }}
                >
                    {(isLoading || !spec) && (
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
                    {renderError && (
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
