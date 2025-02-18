import React, { useState, useEffect, useRef, useCallback } from 'react';
import { useTheme } from '../context/ThemeContext';
import { Spin } from 'antd';
import vegaEmbed from 'vega-embed';
import * as d3 from 'd3';
import { D3RenderPlugin } from '../types/d3';
import { d3RenderPlugins } from '../plugins/d3/registry';

interface D3RendererProps {
    spec: any;
    width?: number;
    height?: number;
    containerId?: string;  
    type?: 'auto' | 'd3' | 'vega-lite';
    onLoad?: () => void;  
    onError?: (error: Error) => void; 
    config?: any; 
}

interface VegaLiteSpec {
    $schema?: string;
    mark?: string | object;
    width?: number | 'container';
    height?: number;
    config?: any;
    [key: string]: any;
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
    const renderCount = useRef(0);
    const [isLoading, setIsLoading] = useState(true);
    const [renderError, setRenderError] = useState<string | null>(null);
    const [parsedSpec, setParsedSpec] = useState<any>(null);

    const handleError = useCallback((error: Error) => {
        console.error('Visualization error:', error);
        setRenderError(error.message);
        setIsLoading(false);
        onError?.(error);
    }, [onError]);

    const renderVegaSpec = useCallback(async (vegaSpec: any) => {
        console.debug('Starting renderVegaSpec', { vegaSpec });
        const container = vegaContainerRef.current;

	if (!container) {
            const error = new Error('Container reference is null');
            handleError(error);
	    return;
        }

	// Clean up any existing content
        if (vegaViewRef.current) {
            try {
                vegaViewRef.current.finalize();
                vegaViewRef.current = null;
            } catch (e) { /* ignore cleanup errors */ }
        }
        
        try {
            const spec = {
                ...vegaSpec,
                width: vegaSpec.width || width || 'container',
                height: vegaSpec.height || height || 300,
                config: {
                    ...vegaSpec.config,
                    style: {
                        "guide-label": {
                            fill: isDarkMode ? '#ffffff' : '#000000'
                        },
                        "guide-title": {
                            fill: isDarkMode ? '#ffffff' : '#000000'
                        },
                        ...vegaSpec.config?.style
                    }
                },
                ...config
            };
            console.debug('Rendering Vega spec:', spec);
            console.debug('Attempting vegaEmbed with:', { container, spec });
            
            const result = await vegaEmbed(container, spec, {
                actions: false,
                theme: isDarkMode ? 'dark' : 'excel',
                defaultStyle: false,
                logLevel: 2,
                patch: (spec) => ({ ...spec, background: 'transparent' }),
                scaleFactor: 2,
                renderer: 'canvas'
            });
            
            vegaViewRef.current = result.view;
            console.debug('Vega view created:', { view: result.view });
            result.view.addEventListener('error', (event: any) => {
                console.error('Vega view error:', event);
                setRenderError(event.error?.message || 'Unknown error in visualization');
            });
            await result.view.runAsync();
        } catch (error) {
            console.error('Vega-Lite rendering error:', error);
            throw error;
        }

    }, [isDarkMode, width, height, config]);
    // Cleanup effect
    useEffect(() => {
        // Return cleanup function
	return () => {
	    // Clean up Vega view
            if (vegaViewRef.current) {
                try {
                    vegaViewRef.current.finalize();
                    vegaViewRef.current = null;
                } catch (e) { /* ignore cleanup errors */ }
            }
        };
    }, []);

    useEffect(() => {
        // Additional cleanup effect for view changes
        return () => {
            if (vegaViewRef.current) {
		vegaViewRef.current.finalize();
                vegaViewRef.current = null;
            }
        };
    }, [spec]); // Only run when spec changes

    useEffect(() => {
	const initializeVisualization = async () => {
          try {
	      // Preprocess and parse the spec
	      console.debug('Raw spec received:', { spec, type, isString: typeof spec === 'string' });
              const preprocessSpec = (rawSpec: string | object): any => {
                  if (typeof rawSpec !== 'string') {
                      return rawSpec;
                  }
                  // Remove comments and normalize color values
                  const cleanJson = rawSpec
                      // Remove single-line comments
                      .replace(/^\s*\/\/.*$/gm, '')
                      // Remove multi-line comments
                      .replace(/\/\*[\s\S]*?\*\//g, '')
                      // Handle common color names with # prefix
                      .replace(/"#(lightgrey|lightgray)"/g, '"#d3d3d3"')
                      .replace(/"#(darkgrey|darkgray)"/g, '"#a9a9a9"')
                      .replace(/"#white"/g, '"#ffffff"')
                      .replace(/"#black"/g, '"#000000"')
                      // Clean up any empty lines and multiple spaces
                      .replace(/^\s*[\r\n]/gm, '')
                      .replace(/\s+/g, ' ')
                      .trim();
                  try {
	              // Use Function constructor to evaluate the code safely
                      const evalFunction = new Function(`
			  const spec = ${cleanJson.replace(
                              /"render"\s*:\s*function/g,
                              '"render": function'
                          )};
			  spec.render = spec.render.bind(spec);
                          return spec;
                      `);
                      
                      try {
                          return evalFunction();
                      } catch (evalError) {
                          console.error('Error evaluating D3 spec:', evalError);
                          throw new Error('Invalid D3 specification format');
                      }
                  } catch (parseError) {
                      console.error('Error parsing preprocessed JSON:', parseError);
                      const errorMessage = parseError instanceof Error 
                          ? parseError.message 
                          : 'Unknown JSON parsing error';
                      throw new Error(`Invalid JSON after preprocessing: ${errorMessage}`);
                  }
              };
              const parsed = preprocessSpec(spec);
	      console.debug('Parsed spec:', {
                  parsed,
                  hasRender: typeof parsed?.render === 'function',
                  type: parsed?.type,
                  renderer: parsed?.renderer,
                  keys: Object.keys(parsed || {})
              });
	      // Check if this is a D3-specific spec
	      if (type === 'd3' || parsed.renderer === 'd3') {
                  // Handle as direct D3 visualization
                  const container = d3ContainerRef.current;
                  if (!container) {
                      throw new Error('Container reference is null');
                  }

		  renderCount.current++;
                  const currentRender = renderCount.current;
                  console.debug(`[D3-${currentRender}] Starting render`, {
                      hasExistingD3: !!d3ContainerRef.current,
                      containerChildren: container.children.length
                  });

		  // Create new selection and store it
                  const selection = d3.select(container);

		  console.debug('Container details:', {
                      element: container,
                      size: { width: container.clientWidth, height: container.clientHeight },
                      children: container.children.length
                  });

                  // Clear any existing content
		  container.innerHTML = '';
                  selection.selectAll('*').remove();
		  console.debug(`[D3-${currentRender}] Cleared existing content`);

                  // Handle D3 rendering
                  if (parsed.render && typeof parsed.render === 'function') {
		      console.debug(`[D3-${currentRender}] About to call render function`);
		      try {
			  parsed.render(container, d3);
			  console.debug('D3 render complete', {
                              containerChildren: container.children.length,
                              svgElement: container.querySelector('svg')
			  });
                      } catch (error) {
			  if (error instanceof Error) {
                              console.error('D3 render stack:', error.stack);
                              console.error('D3 render error:', error.message);
                          }
                          console.error('D3 render error details:', error);
			  // If render fails, try finding a plugin as fallback
                          const plugin = findPlugin(parsed);
                          if (plugin) {
                              console.debug(`Falling back to ${plugin.name} plugin`);
                              plugin.render(container, d3, parsed);
                          } else {
                              throw error;
                          }
                      }
                  } else {
	              // Try finding a plugin when no render function is provided
                      const plugin = findPlugin(parsed);
                      if (plugin) {
                          console.debug(`Using ${plugin.name} plugin for visualization`);
                          plugin.render(container, d3, parsed);
                      } else {
                          throw new Error('No render function or compatible plugin found for this visualization');
                      }
		  }
                  console.debug(`[D3-${currentRender}] Render complete`, {
                      containerChildren: container.children.length,
                      hasD3Ref: !!d3ContainerRef.current
                  });
                  setIsLoading(false);
                  onLoad?.();
	      } else if (type === 'auto' || type === 'vega-lite') {
                  const vegaSpec = convertD3ToVega(parsed);
                  await renderVegaSpec(vegaSpec);
                  setIsLoading(false);
                  onLoad?.();
	      } else {
		  throw new Error(`Unsupported visualization type: ${type}`);
	      }
	  } catch (error) {
	      const errorMessage = error instanceof Error
                  ? `Failed to parse specification: ${error.message}`
                  : 'Failed to parse specification';
              console.error('Error in initializeVisualization:', error);
              handleError(new Error(errorMessage));
              setRenderError(errorMessage);
	  }
        };
        initializeVisualization();
    }, [spec, width, height, isDarkMode, containerId, onLoad, onError, config, handleError, renderVegaSpec]);

    const convertD3ToVega = (d3Spec: any) => {
        console.debug('Converting D3 to Vega:', { type: d3Spec.type });
        const baseSpec = {
            $schema: "https://vega.github.io/schema/vega-lite/v5.json",
            width: width || 'container',
            height: height || 300,
            data: { values: Array.isArray(d3Spec.data) ? d3Spec.data : [d3Spec.data] }
        };
        switch (d3Spec.type) {
            case 'bar':
                return {
                    ...baseSpec,
                    mark: 'bar',
                    encoding: {
                        x: { field: 'label', type: 'nominal', axis: { labelAngle: -45 } },
                        y: { field: 'value', type: 'quantitative' },
                        tooltip: [
                            { field: 'label', type: 'nominal' },
                            { field: 'value', type: 'quantitative' }
                        ]
                    }
                };
            case 'line':
                return {
                    ...baseSpec,
		    mark: {
                        type: 'line',
                        point: true
                    },

                    encoding: {
			x: {
                            field: 'x',
                            type: 'quantitative',
                            scale: {
                                zero: false,
                                nice: true
                            }
                        },
                        y: {
                            field: 'y',
                            type: 'quantitative',
                            scale: {
                                zero: false,
                                nice: true
                            }
                        },
                        tooltip: [
                            { field: 'x', type: 'quantitative' },
                            { field: 'y', type: 'quantitative' }
                        ]
                    }
                };
            default:
                return null;
        }
    };

        return (
        console.debug('Rendering component', { isLoading, hasError: !!renderError }),
        <div
            id={containerId}
            style={{
                width: '100%',
                height,
                position: 'relative',
                overflow: 'hidden',
                minHeight: '200px',
                padding: '16px'
            }}
        >
	    {type === 'd3' ? (
                <div ref={d3ContainerRef}
                    className="d3-container"
                    style={{
                        width: '100%',
			height: height || '300px',
                        minHeight: '200px',
                        position: 'relative'
                    }}
                />
            ) : (
                <div ref={vegaContainerRef}
                id="vega-container"
                style={{
                    position: 'relative',
                    width: '100%',
                    height: '100%'
                }}
            >
                {isLoading ? (
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
                    </div>
                ) : renderError ? (
                    <div style={{
                        padding: '20px',
                        color: isDarkMode ? '#ff4d4f' : '#f5222d',
                        textAlign: 'center'
                    }}>
                        {renderError}
                    </div>
                ) : null}
            </div>
	    )}
        </div>
    );

};

