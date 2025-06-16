import { D3RenderPlugin } from '../../types/d3';
import { isDiagramDefinitionComplete } from '../../utils/diagramUtils';

// Import vega-embed dynamically to avoid bundle size issues
let vegaEmbed: any = null;

// Lazy load vega-embed
const loadVegaEmbed = async () => {
  if (!vegaEmbed) {
    try {
      const vegaModule = await import('vega-embed');
      vegaEmbed = vegaModule.default;
    } catch (error) {
      console.error('Failed to load vega-embed:', error);
      throw new Error('Vega-Lite is not available. Please install vega-embed: npm install vega-embed');
    }
  }
  return vegaEmbed;
};

export interface VegaLiteSpec {
  type: 'vega-lite';
  isStreaming?: boolean;
  forceRender?: boolean;
  definition?: string;
  spec?: any; // The actual Vega-Lite specification
  $schema?: string;
  data?: any;
  mark?: any;
  encoding?: any;
  width?: number | string;
  height?: number;
  [key: string]: any; // Allow other Vega-Lite properties
}

// Type guard to check if a spec is for Vega-Lite
const isVegaLiteSpec = (spec: any): spec is VegaLiteSpec => {
  if (typeof spec === 'string') {
    try {
      const parsed = JSON.parse(spec);
      const isValid = isVegaLiteObject(parsed);
      console.log(">>> vegaLitePlugin string spec check:", { isValid, hasSchema: !!parsed.$schema, hasMark: !!parsed.mark });
      return isValid;
    } catch {
      console.log(">>> vegaLitePlugin string spec parse failed");
      return false;
    }
  }

  const isValid = isVegaLiteObject(spec);
  console.log(">>> vegaLitePlugin object spec check:", { isValid, type: spec?.type, hasSchema: !!spec?.$schema, hasMark: !!spec?.mark });
  return isValid;
};

// Helper to check if an object is a Vega-Lite specification
const isVegaLiteObject = (obj: any): boolean => {
  return (
    typeof obj === 'object' &&
    obj !== null &&
    (obj.type === 'vega-lite' ||
      obj.$schema?.includes('vega-lite') ||
      (obj.mark && (obj.encoding || obj.data)) ||
      (obj.data && (obj.mark || obj.layer || obj.concat || obj.facet || obj.repeat)) ||
      // More permissive check for basic Vega-Lite structure
      (obj.mark && obj.data) ||
      // Check for concatenated views
      (obj.vconcat || obj.hconcat) ||
      // Check for faceted views
      (obj.facet && obj.spec) ||
      // Check for repeated views
      (obj.$schema && obj.data) ||
      (obj.encoding && (obj.mark || obj.data)))
  );
};

// Helper to check if a Vega-Lite definition is complete
const isVegaLiteDefinitionComplete = (definition: string): boolean => {
  if (!definition || definition.trim().length === 0) return false;

  try {
    const parsed = JSON.parse(definition);

    // Basic completeness checks
    if (!parsed || typeof parsed !== 'object') return false;

    // Check for required Vega-Lite properties
    const hasData = parsed.data !== undefined;
    const hasVisualization = parsed.mark || parsed.layer || parsed.concat || parsed.facet || parsed.repeat;

    // A complete spec should have both data and visualization
    return hasData && hasVisualization;
  } catch (error) {
    // If JSON is malformed, it's not complete
    return false;
  }
};

export const vegaLitePlugin: D3RenderPlugin = {
  name: 'vega-lite-renderer',
  priority: 8, // Higher priority than basic chart but lower than mermaid/graphviz

  canHandle: (spec: any): boolean => {
    return isVegaLiteSpec(spec);
  },

  // Helper to check if a vega-lite definition is complete
  isDefinitionComplete: (definition: string): boolean => {
    return isVegaLiteDefinitionComplete(definition);
  },

  render: async (container: HTMLElement, d3: any, spec: VegaLiteSpec, isDarkMode: boolean): Promise<void> => {
    console.log('Vega-Lite plugin render called with spec:', spec);

    try {
      // Clear container
      container.innerHTML = '';

      // Show loading spinner
      const loadingSpinner = document.createElement('div');
      loadingSpinner.className = 'vega-lite-loading-spinner';
      loadingSpinner.style.cssText = `
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        padding: 2em;
        min-height: 150px;
        width: 100%;
      `;
      loadingSpinner.innerHTML = `
        <div style="
          border: 4px solid rgba(0, 0, 0, 0.1);
          border-top: 4px solid ${isDarkMode ? '#4cc9f0' : '#3498db'};
          border-radius: 50%;
          width: 40px;
          height: 40px;
          animation: vega-lite-spin 1s linear infinite;
          margin-bottom: 15px;
        "></div>
        <div style="font-family: system-ui, -apple-system, sans-serif; color: ${isDarkMode ? '#eceff4' : '#333333'};">
          Loading Vega-Lite visualization...
        </div>
      `;
      container.appendChild(loadingSpinner);

      // Add spinner animation
      if (!document.querySelector('#vega-lite-spinner-keyframes')) {
        const keyframes = document.createElement('style');
        keyframes.id = 'vega-lite-spinner-keyframes';
        keyframes.textContent = `
          @keyframes vega-lite-spin {
            0% { transform: rotate(0deg); }
            100% { transform: rotate(360deg); }
          }
        `;
        document.head.appendChild(keyframes);
      }

      // If we're streaming and the definition is incomplete, show a waiting message
      if (spec.isStreaming && !spec.forceRender) {
        const definition = spec.definition || JSON.stringify(spec);
        const isComplete = isDiagramDefinitionComplete(definition, 'vega-lite');

        if (!isComplete) {
          loadingSpinner.innerHTML = `
            <div style="text-align: center; padding: 20px; background-color: ${isDarkMode ? '#1f1f1f' : '#f6f8fa'}; border: 1px dashed #ccc; border-radius: 4px;">
              <p>Waiting for complete Vega-Lite specification...</p>
            </div>
          `;
          return;
        }
      }

      // Load vega-embed
      const embed = await loadVegaEmbed();

      // Parse the specification
      let vegaSpec: any;
      if (typeof spec === 'string') {
        console.log(">>> vegaLitePlugin: Parsing string spec");
        vegaSpec = JSON.parse(spec);
      } else if (spec.definition) {
        console.log(">>> vegaLitePlugin: Parsing definition property");
        vegaSpec = JSON.parse(spec.definition);
      } else {
        console.log(">>> vegaLitePlugin: Using spec object directly");
        // Use the spec object directly, but remove our custom properties
        vegaSpec = { ...spec };
        delete vegaSpec.type;
        delete vegaSpec.isStreaming;
        delete vegaSpec.forceRender;
        delete vegaSpec.definition;
      }

      console.log(">>> vegaLitePlugin: Final spec for rendering:", {
        hasSchema: !!vegaSpec.$schema,
        hasMark: !!vegaSpec.mark,
        hasData: !!vegaSpec.data,
        hasEncoding: !!vegaSpec.encoding,
        hasVConcat: !!vegaSpec.vconcat,
        hasHConcat: !!vegaSpec.hconcat,
        hasFacet: !!vegaSpec.facet,
        hasRepeat: !!vegaSpec.repeat
      });

      // Get container dimensions for responsive sizing
      const containerRect = container.getBoundingClientRect();
      const availableWidth = Math.max(containerRect.width - 40, 400); // Account for padding, minimum 400px
      const availableHeight = Math.max(containerRect.height || 400, 300); // Minimum 300px height

      console.log(">>> vegaLitePlugin: Container dimensions:", {
        containerWidth: containerRect.width,
        availableWidth,
        availableHeight
      });

      // Set responsive width - use container width for all visualizations
      vegaSpec.width = availableWidth;

      // Handle height based on visualization type
      if (!vegaSpec.height && !vegaSpec.vconcat && !vegaSpec.hconcat && !vegaSpec.facet) {
        // For simple charts, let height be determined by content but set a reasonable default
        vegaSpec.height = Math.min(availableHeight * 0.6, 400);
      }

      // Configure autosize to make visualizations responsive
      vegaSpec.autosize = {
        type: 'fit-x',
        contains: 'padding',
        resize: true
      };

      // Ensure axis labels are properly displayed without overriding user config
      if (vegaSpec.layer) {
        vegaSpec.layer.forEach(layer => {
          if (layer.encoding?.x && !layer.encoding.x.axis) {
            // Only add default axis config if none exists
            layer.encoding.x.axis = {
              labelAngle: 0,
              labelLimit: 0, // No limit to prevent truncation
              labelFontSize: 11
            };
          }
          if (layer.encoding?.y && !layer.encoding.y.axis) {
            // Ensure y-axis labels are also properly displayed
            layer.encoding.y.axis = {
              labelLimit: 0,
              labelFontSize: 11
            };
          }
        });
      }

      // Ensure it has a schema if not present
      if (!vegaSpec.$schema) {
        vegaSpec.$schema = 'https://vega.github.io/schema/vega-lite/v5.json';
      }

      // Fix radar chart specifications that use theta/radius encoding
      if (vegaSpec.encoding?.theta && vegaSpec.encoding?.radius) {
        // Convert to proper radar chart using arc mark
        vegaSpec.mark = { type: "arc", innerRadius: 20, outerRadius: 100 };
        vegaSpec.encoding = {
          theta: { field: "level", type: "quantitative", scale: { type: "linear", range: [0, 6.28] } },
          radius: { field: "level", type: "quantitative", scale: { type: "linear", range: [20, 100] } },
          color: { field: "skill", type: "nominal" },
          tooltip: [
            { field: "skill", type: "nominal" },
            { field: "level", type: "quantitative", title: "Mastery Level" }
          ]
        };
      }

      // Apply theme
      const embedOptions = {
        actions: false,
        theme: isDarkMode ? 'dark' : 'excel',
        renderer: 'svg' as const, // Use SVG for better scaling with complex layouts
        scaleFactor: 1,
        width: availableWidth,
        height: vegaSpec.height || availableHeight * 0.6,
        config: {
          view: {
            continuousWidth: availableWidth,
            continuousHeight: vegaSpec.height || availableHeight * 0.6,
            stroke: 'transparent' // Remove default border
          },
          background: null // Let container handle background
        }
      };

      // Set explicit container dimensions for complex layouts
      if (vegaSpec.vconcat || vegaSpec.hconcat || vegaSpec.facet) {
        container.style.minHeight = `${availableHeight}px`;
        container.style.width = '100%';
      }

      // Remove loading spinner
      container.removeChild(loadingSpinner);

      // Add debugging for complex layouts
      if (vegaSpec.vconcat || vegaSpec.hconcat || vegaSpec.facet) {
        console.log(">>> vegaLitePlugin: Rendering complex layout:", {
          type: vegaSpec.vconcat ? 'vconcat' : vegaSpec.hconcat ? 'hconcat' : 'facet',
          containerWidth: container.offsetWidth,
          containerHeight: container.offsetHeight
        });
      }

      // Render the visualization
      const result = await embed(container, vegaSpec, embedOptions);

      // Store references to the vega view and container content
      const vegaView = result.view;
      const vegaContainer = container.querySelector('.vega-embed') as HTMLElement;

      // Add action buttons container
      const actionsContainer = document.createElement('div');
      actionsContainer.className = 'diagram-actions';

      // Add Open button
      const openButton = document.createElement('button');
      openButton.innerHTML = '↗️ Open';
      openButton.className = 'diagram-action-button vega-lite-open-button';
      openButton.onclick = () => {
        // Get the SVG element - check both the stored container and current container
        let svgElement = vegaContainer?.querySelector('svg');
        if (!svgElement) {
          // Fallback: look in the current container
          svgElement = container.querySelector('svg');
        }
        if (!svgElement) return;

        console.log('Opening Vega-Lite visualization in popup');

        // Get the SVG dimensions
        const svgGraphics = svgElement as unknown as SVGGraphicsElement;
        let width = 800;
        let height = 600;

        try {
          // Try to get the bounding box
          const bbox = svgGraphics.getBBox();
          width = Math.max(bbox.width + 100, 600); // Add padding, minimum 600px
          height = Math.max(bbox.height + 150, 400); // Add padding, minimum 400px
        } catch (e) {
          console.warn('Could not get SVG dimensions, using defaults', e);
        }

        // Get SVG data
        const svgData = new XMLSerializer().serializeToString(svgElement);

        // Create an HTML document that will display the SVG responsively
        const htmlContent = `
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <title>Vega-Lite Visualization</title>
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
        <body data-theme="${isDarkMode ? 'dark' : 'light'}">
            <div class="toolbar">
                <div>
                    <button onclick="zoomIn()">Zoom In</button>
                    <button onclick="zoomOut()">Zoom Out</button>
                    <button onclick="resetZoom()">Reset</button>
                    <button class="theme-toggle" onclick="toggleTheme()">${isDarkMode ? '☀️ Light' : '🌙 Dark'}</button>
                </div>
                <div>
                    <button onclick="downloadSvg()">Download SVG</button>
                    <button onclick="downloadSpec()">Download Spec</button>
                </div>
            </div>
            <div class="container" id="svg-container">
                ${svgData}
            </div>
            <script>
                const svg = document.querySelector('svg');
                let currentScale = 1;
                let isDarkMode = ${isDarkMode};
                
                // Store the original Vega-Lite spec
                const vegaSpec = ${JSON.stringify(vegaSpec, null, 2)};
                
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
                    
                    // Re-render Vega-Lite visualization with new theme
                    reRenderVegaVisualization();
                }
                
                function reRenderVegaVisualization() {
                    const container = document.getElementById('svg-container');
                    if (!container) return;
                    
                    // Clear the container
                    container.innerHTML = '';
                    
                    // Import vega-embed dynamically in the popup
                    import('https://cdn.jsdelivr.net/npm/vega-embed@6').then(vegaEmbedModule => {
                        const vegaEmbed = vegaEmbedModule.default || vegaEmbedModule;
                        
                        // Re-render with new theme
                        vegaEmbed(container, vegaSpec, {
                            actions: false,
                            theme: isDarkMode ? 'dark' : 'excel',
                            renderer: 'svg',
                            scaleFactor: 1
                        }).then(result => {
                            // Make sure SVG is responsive
                            const svg = container.querySelector('svg');
                            if (svg) {
                                svg.setAttribute('width', '100%');
                                svg.setAttribute('height', '100%');
                                svg.setAttribute('preserveAspectRatio', 'xMidYMid meet');
                            }
                        });
                    });
                }
                
                function downloadSvg() {
                    const svgData = new XMLSerializer().serializeToString(svg);
                    const svgDoc = \`<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" "http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd">
\${svgData}\`;
                    
                    const svgBlob = new Blob([svgDoc], {type: 'image/svg+xml'});
                    const url = URL.createObjectURL(svgBlob);
                    
                    const link = document.createElement('a');
                    link.href = url;
                    link.download = 'vega-lite-visualization-${Date.now()}.svg';
                    document.body.appendChild(link);
                    link.click();
                    document.body.removeChild(link);
                    
                    setTimeout(() => URL.revokeObjectURL(url), 1000);
                }
                
                function downloadSpec() {
                    const specBlob = new Blob([JSON.stringify(vegaSpec, null, 2)], {type: 'application/json'});
                    const url = URL.createObjectURL(specBlob);
                    
                    const link = document.createElement('a');
                    link.href = url;
                    link.download = 'vega-lite-spec-${Date.now()}.json';
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
          'VegaLiteVisualization',
          `width=${width},height=${height},resizable=yes,scrollbars=yes,status=no,toolbar=no,menubar=no,location=no`
        );

        // Focus the new window
        if (popupWindow) {
          popupWindow.focus();
          console.log('Popup window opened successfully');
        } else {
          console.error('Failed to open popup window - popup blocked?');
          alert('Popup blocked! Please allow popups for this site to open the visualization.');
        }

        // Clean up the URL object after a delay
        setTimeout(() => URL.revokeObjectURL(url), 10000);
      };
      actionsContainer.appendChild(openButton);

      // Add Save button
      const saveButton = document.createElement('button');
      saveButton.innerHTML = '💾 Save';
      saveButton.className = 'diagram-action-button vega-lite-save-button';
      saveButton.onclick = () => {
        // Get the SVG element - check both the stored container and current container
        let svgElement = vegaContainer?.querySelector('svg');
        if (!svgElement) {
          // Fallback: look in the current container
          svgElement = container.querySelector('svg');
        }
        if (!svgElement) return;

        console.log('Saving Vega-Lite visualization as SVG');

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
        link.download = `vega-lite-visualization-${Date.now()}.svg`;
        document.body.appendChild(link);
        console.log('Triggering download for:', link.download);
        link.click();
        document.body.removeChild(link);

        // Clean up the URL object after a delay
        setTimeout(() => URL.revokeObjectURL(url), 1000);
      };
      actionsContainer.appendChild(saveButton);

      // Add Source button
      let showingSource = false;
      let originalVegaContainer = vegaContainer;
      const sourceButton = document.createElement('button');
      sourceButton.innerHTML = showingSource ? '🎨 View' : '📝 Source';
      sourceButton.className = 'diagram-action-button vega-lite-source-button';
      sourceButton.onclick = () => {
        showingSource = !showingSource;
        sourceButton.innerHTML = showingSource ? '🎨 View' : '📝 Source';

        if (showingSource) {
          // Hide the vega container and show source
          if (originalVegaContainer && originalVegaContainer.parentNode === container) {
            vegaContainer.style.display = 'none';
          }

          // Clear container and add source view
          container.innerHTML = `<pre style="
            background-color: ${isDarkMode ? '#1f1f1f' : '#f6f8fa'};
            padding: 16px;
            border-radius: 4px;
            overflow: auto;
            width: 100%;
            height: 100%;
            margin: 0;
            box-sizing: border-box;
            color: ${isDarkMode ? '#e6e6e6' : '#24292e'};
          "><code>${JSON.stringify(vegaSpec, null, 2)}</code></pre>`;

          // Re-add the actions container after clearing innerHTML
          if (actionsContainer.parentNode !== container) {
            container.insertBefore(actionsContainer, container.firstChild);
          }
        } else {
          // Restore the visualization
          const sourceView = container.querySelector('pre');
          if (sourceView) {
            container.removeChild(sourceView);
          }
          // Restore the vega container
          if (originalVegaContainer) {
            originalVegaContainer.style.display = '';
            if (originalVegaContainer.parentNode !== container) {
              container.appendChild(originalVegaContainer);
            }
          } else {
            // Re-render the visualization if the container was lost
            embed(container, vegaSpec, embedOptions);
          }

          // Re-add the actions container
          container.insertBefore(actionsContainer, container.firstChild);
        }
      };
      actionsContainer.appendChild(sourceButton);

      // Add actions container to the top of the container
      container.insertBefore(actionsContainer, container.firstChild);

      // Post-render fixes for sizing issues
      setTimeout(() => {
        const svgElement = container.querySelector('svg');
        const vegaEmbedDiv = container.classList.contains('vega-embed') ? container : container.querySelector('.vega-embed') as HTMLElement;

        if (svgElement) {
          // Ensure SVG uses full container width
          svgElement.style.width = '100%';
          svgElement.style.maxWidth = '100%';
          svgElement.style.height = 'auto';
          svgElement.style.display = 'block';

          // Remove any fixed width/height attributes that might constrain sizing
          svgElement.removeAttribute('width');
          svgElement.removeAttribute('height');

          console.log(">>> vegaLitePlugin: SVG sizing applied:", {
            svgWidth: svgElement.style.width,
            containerWidth: container.getBoundingClientRect().width,
            svgRect: svgElement.getBoundingClientRect()
          });
        }

        if (vegaEmbedDiv) {
          vegaEmbedDiv.style.width = '100%';
          vegaEmbedDiv.style.maxWidth = '100%';
        }

        // Force parent containers to use full width
        let parent = container.parentElement;
        while (parent && parent.classList.contains('d3-container')) {
          (parent as HTMLElement).style.width = '100%';
          (parent as HTMLElement).style.maxWidth = '100%';
          parent = parent.parentElement;
        }

        // Add global debugging functions to window
        (window as any).debugVegaLite = {
          container,
          svgElement,
          vegaEmbedDiv,
          getSVGActualSize: () => {
            if (!svgElement) return null;
            const rect = svgElement.getBoundingClientRect();
            const viewBox = svgElement.getAttribute('viewBox');
            const [vbX, vbY, vbWidth, vbHeight] = viewBox ? viewBox.split(' ').map(Number) : [0, 0, 0, 0];
            return {
              actualWidth: rect.width,
              actualHeight: rect.height,
              viewBoxWidth: vbWidth,
              viewBoxHeight: vbHeight,
              scaleRatio: rect.width / vbWidth
            };
          },
          inspect: () => {
            console.log('=== Vega-Lite Debug Info ===');
            console.log('Container:', container);
            console.log('SVG Element:', svgElement);
            console.log('Vega Embed Div:', vegaEmbedDiv);

            if (svgElement) {
              console.log('SVG getBoundingClientRect():', svgElement.getBoundingClientRect());
              console.log('SVG viewBox:', svgElement.getAttribute('viewBox'));
              console.log('SVG style:', svgElement.style.cssText);
              console.log('SVG computed style:', window.getComputedStyle(svgElement));
            }

            if (vegaEmbedDiv) {
              console.log('Embed div getBoundingClientRect():', vegaEmbedDiv.getBoundingClientRect());
              console.log('Embed div style:', vegaEmbedDiv.style.cssText);
              console.log('Embed div computed style:', window.getComputedStyle(vegaEmbedDiv));
            }

            console.log('Container getBoundingClientRect():', container.getBoundingClientRect());

            // Check if SVG is being clipped
            const svgRect = svgElement?.getBoundingClientRect();
            const containerRect = container.getBoundingClientRect();
            if (svgRect && containerRect) {
              console.log('SVG overflow check:', {
                svgBottom: svgRect.bottom,
                containerBottom: containerRect.bottom,
                isOverflowing: svgRect.bottom > containerRect.bottom,
                overflowAmount: svgRect.bottom - containerRect.bottom
              });
            }

            // Check parent containers that might be constraining height
            let parent = container.parentElement;
            let level = 0;
            while (parent && level < 3) {
              const parentRect = parent.getBoundingClientRect();
              console.log(`Parent level ${level}:`, {
                element: parent,
                className: parent.className,
                rect: parentRect,
                style: parent.style.cssText,
                computedHeight: window.getComputedStyle(parent).height
              });
              parent = parent.parentElement;
              level++;
            }
          }
        };

        console.log('Vega-Lite debugging available: window.debugVegaLite.inspect()');

        if (svgElement) {
          // Get the SVG viewBox dimensions (the actual content size)
          const viewBox = svgElement.getAttribute('viewBox');
          let svgWidth = 680;
          let svgHeight = 774;

          if (viewBox) {
            const [, , width, height] = viewBox.split(' ').map(Number);
            svgWidth = width;
            svgHeight = height;
          }

          console.log('SVG content dimensions:', { svgWidth, svgHeight, viewBox });

          // Force SVG to be responsive but maintain aspect ratio
          svgElement.style.width = '100%';
          svgElement.style.height = 'auto';
          svgElement.style.maxWidth = '100%';
          svgElement.style.overflow = 'visible';

          // Force the vega-embed container to accommodate the full SVG content
          if (vegaEmbedDiv) {
            // Calculate the actual height needed based on current width and aspect ratio
            const containerWidth = vegaEmbedDiv.getBoundingClientRect().width;
            const aspectRatio = svgHeight / svgWidth;
            const neededHeight = containerWidth * aspectRatio;

            vegaEmbedDiv.style.height = `${neededHeight}px`;
            vegaEmbedDiv.style.minHeight = `${neededHeight}px`;
            vegaEmbedDiv.style.display = 'block';
            vegaEmbedDiv.style.overflow = 'visible';

            console.log('Container sizing:', { containerWidth, aspectRatio, neededHeight });
          }

          // Force parent d3-containers to accommodate the full height
          let parent = container.parentElement;
          while (parent && parent.classList.contains('d3-container')) {
            (parent as HTMLElement).style.height = 'auto';
            (parent as HTMLElement).style.minHeight = `${svgHeight}px`;
            (parent as HTMLElement).style.overflow = 'visible';
            console.log('Updated parent container:', parent.className, 'to height: auto, minHeight:', svgHeight);
            parent = parent.parentElement;
          }

          // For complex layouts, ensure proper scaling
          if (vegaSpec.vconcat || vegaSpec.hconcat || vegaSpec.facet) {
            svgElement.setAttribute('preserveAspectRatio', 'xMidYMid meet');

            // Force a reflow to ensure proper sizing
            container.style.display = 'none';
            void container.offsetHeight; // Trigger reflow
            container.style.display = '';
          }
        }
      }, 100);

      console.log('Vega-Lite visualization rendered successfully');

    } catch (error) {
      console.error('Vega-Lite rendering error:', error);

      // Add more specific error information for debugging
      if (error instanceof Error) {
        console.error('Error details:', {
          message: error.message,
          stack: error.stack
        });
      }

      // Remove loading spinner if it exists
      const spinner = container.querySelector('.vega-lite-loading-spinner');
      if (spinner) {
        container.removeChild(spinner);
      }

      container.innerHTML = `
        <div class="vega-lite-error" style="
          padding: 16px;
          margin: 16px 0;
          border-radius: 6px;
          background-color: ${isDarkMode ? '#2a1f1f' : '#fff2f0'};
          border: 1px solid ${isDarkMode ? '#a61d24' : '#ffa39e'};
          color: ${isDarkMode ? '#ff7875' : '#cf1322'};
        ">
          <strong>Vega-Lite Error:</strong>
          <p>${error instanceof Error ? error.message : 'Unknown error'}</p>
          <details>
            <summary>Show Specification</summary>
            <pre><code>${typeof spec === 'string' ? spec : JSON.stringify(spec, null, 2)}</code></pre>
          </details>
        </div>
      `;
    }
  }
};
