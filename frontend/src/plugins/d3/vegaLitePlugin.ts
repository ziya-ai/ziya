import { D3RenderPlugin } from '../../types/d3';
import { isDiagramDefinitionComplete } from '../../utils/diagramUtils';
import { extractDefinitionFromYAML } from '../../utils/diagramUtils';

import vegaEmbed, { EmbedOptions } from 'vega-embed';

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
      return isVegaLiteObject(parsed);
    } catch {
      return false;
    }
  }

  return isVegaLiteObject(spec);
};

// Helper to check if an object is a Vega-Lite specification
const isVegaLiteObject = (obj: any): boolean => {
  return (
    typeof obj === 'object' &&
    obj !== null &&
    // Explicitly reject other known diagram types to prevent mis-identification
    (!obj.type || !['mermaid', 'graphviz', 'network', 'd2', 'joint'].includes(obj.type)) &&
    (
      obj.type === 'vega-lite' ||
      (obj.$schema && typeof obj.$schema === 'string' && obj.$schema.includes('vega-lite')) ||
      (obj.mark && (obj.encoding || obj.data)) ||
      (obj.layer && Array.isArray(obj.layer)) ||
      (obj.vconcat && Array.isArray(obj.vconcat)) ||
      (obj.hconcat && Array.isArray(obj.hconcat)) ||
      (obj.facet && obj.spec)
    )
  );
};

function sanitizeSpec(obj: any): any {
  if (obj === null || obj === undefined) {
    return undefined;
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
  sizingConfig: {
    sizingStrategy: 'content-driven',
    needsDynamicHeight: true,
    needsOverflowVisible: true,
    observeResize: true,
    containerStyles: {
      width: '100%',
      height: 'auto'
    }
  },

  canHandle: (spec: any): boolean => {
    return isVegaLiteSpec(spec);
  },

  // Helper to check if a vega-lite definition is complete
  isDefinitionComplete: (definition: string): boolean => {
    return isVegaLiteDefinitionComplete(definition);
  },

  render: async (container: HTMLElement, d3: any, spec: VegaLiteSpec, isDarkMode: boolean): Promise<void> => {
    console.log('Vega-Lite plugin render called with spec:', spec);

    // Clean up any existing Vega view first
    const existingView = (container as any)._vegaView;
    if (existingView) {
      existingView.finalize();
      delete (container as any)._vegaView;
    }

    // Clear container
    container.innerHTML = '';

    // Remove any existing vega-embed instances
    const existingEmbeds = container.querySelectorAll('.vega-embed');
    existingEmbeds.forEach(embed => {
      if (embed.parentNode) embed.parentNode.removeChild(embed);
    });

      // Declare vegaSpec outside try-catch so it's accessible in error handling
      let vegaSpec: any;

    try {

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

      // Declare vegaSpec outside try-catch so it's accessible in error handling
      let vegaSpec: any;

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
        }
      }

      if (typeof spec === 'string') {
        const extractedContent = extractDefinitionFromYAML(spec, 'vega-lite');
        vegaSpec = sanitizeSpec(JSON.parse(extractedContent));
      } else if (spec.definition) {
        const extractedContent = extractDefinitionFromYAML(spec.definition, 'vega-lite');
        vegaSpec = sanitizeSpec(JSON.parse(extractedContent));
      } else {
        // Use the spec object directly, but remove our custom properties
        vegaSpec = sanitizeSpec({ ...spec });
        delete vegaSpec.type;
        delete vegaSpec.isStreaming;
        delete vegaSpec.forceRender;
        delete vegaSpec.definition;
      }

      // Fix for invalid color names like "#green" which can be produced by LLMs
      try {
        let specStringForColorFix = JSON.stringify(vegaSpec);
        specStringForColorFix = specStringForColorFix.replace(/"#(green|red|orange|blue|yellow|purple|black|white|gray|grey|cyan|magenta|pink|brown|violet|indigo|gold|silver)"/gi, '"$1"');
        vegaSpec = JSON.parse(specStringForColorFix);
      } catch (e) {
        console.warn("Could not apply color fix to Vega-Lite spec", e);
        // if it fails, we continue with the original spec
      }

      // Special handling for violin plots and other density-based visualizations
      if (vegaSpec.transform && vegaSpec.transform.some((t: any) => t.density || t.kde)) {
        console.log('Detected density/violin plot, ensuring proper configuration');
        // Ensure the spec has the right structure for density plots
        if (!vegaSpec.mark) {
          vegaSpec.mark = { type: "area", opacity: 0.7 };
        }
      }

      // Validate the spec before rendering
      if (!vegaSpec || typeof vegaSpec !== 'object') {
        throw new Error('Invalid Vega-Lite specification: spec must be an object');
      }

      // Ensure required properties exist
      if (!vegaSpec.data && !vegaSpec.datasets) {
        throw new Error('Invalid Vega-Lite specification: missing data or datasets');
      }

      // Allow specs with transforms even if they don't have explicit marks initially
      if (!vegaSpec.mark && !vegaSpec.layer && !vegaSpec.vconcat && !vegaSpec.hconcat && !vegaSpec.facet && !vegaSpec.repeat && !vegaSpec.transform) {
        throw new Error('Invalid Vega-Lite specification: missing mark or composition');
      }

      // Get container dimensions for responsive sizing
      const containerRect = container.getBoundingClientRect();
      const availableWidth = Math.max(containerRect.width - 40, 400); // Account for padding, minimum 400px
      const availableHeight = Math.max(containerRect.height || 400, 300); // Minimum 300px height

      // Only override dimensions if they're not explicitly set in the spec
      if (!vegaSpec.width && vegaSpec.width !== 0) {
        vegaSpec.width = availableWidth;
      }

      // Only set height if not explicitly specified and not using complex layouts
      if (!vegaSpec.height && vegaSpec.height !== 0 && !vegaSpec.vconcat && !vegaSpec.hconcat && !vegaSpec.facet) {
        // For simple charts without explicit height, use a reasonable default
        vegaSpec.height = Math.min(availableHeight * 0.8, 500);
      }

      // Only set autosize if not already configured
      if (!vegaSpec.autosize) {
        vegaSpec.autosize = {
          type: 'fit-x',
          contains: 'padding',
          resize: true
        };
      } else if (vegaSpec.autosize && typeof vegaSpec.autosize === 'object') {
        // Preserve existing autosize configuration
        vegaSpec.autosize = { ...vegaSpec.autosize };
      }

      // For charts with explicit dimensions, use a more conservative autosize
      if ((vegaSpec.width && vegaSpec.width > 0) || (vegaSpec.height && vegaSpec.height > 0)) {
        vegaSpec.autosize = {
          type: 'fit-x',
          contains: 'padding',
          resize: true
        };
      }

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

      // Fix common violin plot specification issues - correct field mappings for density transform
      if (vegaSpec.transform && vegaSpec.transform.some((t: any) => t.density)) {
        console.log('Processing violin plot specification');
        
        // For violin plots with density transform, we need to fix the encoding
        // The density transform creates 'value' (x-axis) and 'density' (y-axis) fields
        if (vegaSpec.encoding) {
          // Check if this is incorrectly mapping original data fields instead of density output
          const hasIncorrectMapping = vegaSpec.encoding.x && vegaSpec.encoding.y && 
            (vegaSpec.encoding.x.field !== 'value' || vegaSpec.encoding.y.field !== 'density');
          
          if (hasIncorrectMapping) {
            console.log('Fixing violin plot field mappings for density transform');
            
            // Store the grouping field for faceting/coloring
            const groupField = vegaSpec.transform.find((t: any) => t.density)?.groupby?.[0];
            
            // Fix the encoding to use density transform output fields
            vegaSpec.encoding = {
              x: {
                field: 'value',
                type: 'quantitative',
                title: vegaSpec.encoding.y?.title || 'Value'
              },
              y: {
                field: 'density',
                type: 'quantitative',
                title: 'Density'
              },
              // Preserve color/faceting based on the grouping field
              ...(groupField && vegaSpec.encoding.color && { color: { ...vegaSpec.encoding.color, field: groupField } }),
              ...(groupField && vegaSpec.encoding.column && { column: { ...vegaSpec.encoding.column, field: groupField } }),
              ...(groupField && vegaSpec.encoding.row && { row: { ...vegaSpec.encoding.row, field: groupField } })
            };
          }
        }
        
        // Ensure proper mark configuration for violin plots  
        if (!vegaSpec.mark || (typeof vegaSpec.mark === 'string' && vegaSpec.mark === 'area')) {
          vegaSpec.mark = { type: "area", opacity: 0.7 };
        }
      }

      // Remove problematic autosize that can cause rendering issues
      if (vegaSpec.autosize && vegaSpec.autosize.type === 'fit-x') {
        delete vegaSpec.autosize;
      }

      // Ensure reasonable minimum dimensions for complex visualizations
      if (vegaSpec.width && vegaSpec.width < 200) {
        vegaSpec.width = 400;
      }
      if (vegaSpec.height && vegaSpec.height < 250) {
        vegaSpec.height = 300;
      }

      // Fix for layered charts with selections
      if ((vegaSpec.layer || vegaSpec.facet || vegaSpec.vconcat || vegaSpec.hconcat) && vegaSpec.params) {
        // Check if we have selection parameters
        const selectionParams = vegaSpec.params.filter(param => param.select);

        if (selectionParams.length > 0) {
          console.log("Found top-level selection parameters in composite chart, applying fix");

          // Create a deep clone of the spec to avoid modifying the original
          const modifiedSpec = JSON.parse(JSON.stringify(vegaSpec));

          // Remove the top-level params
          delete modifiedSpec.params;

          if (modifiedSpec.layer) {
            if (!modifiedSpec.layer[0].params) modifiedSpec.layer[0].params = [];
            modifiedSpec.layer[0].params.push(...selectionParams);
            console.log("Moved selection parameters to first layer");
          } else if (modifiedSpec.facet) {
            if (!modifiedSpec.spec.params) modifiedSpec.spec.params = [];
            modifiedSpec.spec.params.push(...selectionParams);
            console.log("Moved selection parameters to facet spec");
          } else if (modifiedSpec.vconcat) {
            if (!modifiedSpec.vconcat[0].params) modifiedSpec.vconcat[0].params = [];
            modifiedSpec.vconcat[0].params.push(...selectionParams);
            console.log("Moved selection parameters to first vconcat view");
          } else if (modifiedSpec.hconcat) {
            if (!modifiedSpec.hconcat[0].params) modifiedSpec.hconcat[0].params = [];
            modifiedSpec.hconcat[0].params.push(...selectionParams);
            console.log("Moved selection parameters to first hconcat view");
          }

          // Use the modified spec
          vegaSpec = modifiedSpec;
        }
      }

      // Fix for facet encoding incorrectly placed inside encoding object (common LLM mistake)
      if (vegaSpec.encoding?.facet) {
        console.log("Found facet in encoding, moving to top level");
        console.log("Original spec:", JSON.stringify(vegaSpec, null, 2));

        // Extract facet configuration
        const facetConfig = vegaSpec.encoding.facet;

        // Create new spec structure - preserve all original properties except mark and encoding
        const { mark, encoding, ...otherProps } = vegaSpec;

        // Create the corrected encoding without facet
        const correctedEncoding = { ...encoding };
        delete correctedEncoding.facet;

        const correctedSpec = {
          ...otherProps, // Preserve $schema, data, description, etc.
          facet: facetConfig,
          spec: {
            mark: mark,
            encoding: correctedEncoding
          }
        };

        vegaSpec = correctedSpec;
        console.log("Corrected facet specification structure:");
        console.log("New spec:", JSON.stringify(vegaSpec, null, 2));
      }

      // Generic fix for specifications that have conflicting width/height with container
      if (vegaSpec.width === 0 || vegaSpec.height === 0) {
        delete vegaSpec.width;
        delete vegaSpec.height;
      }

      // Remove resolve scales that can cause rendering issues
      if (vegaSpec.resolve && vegaSpec.resolve.scale) {
        delete vegaSpec.resolve;
      }

      // Apply theme
      const embedOptions: EmbedOptions = {
        actions: false,
        theme: isDarkMode ? 'dark' : 'excel',
        renderer: 'svg' as const, // Use SVG for better scaling with complex layouts
        scaleFactor: 1,
        // Don't override width/height in embed options if they're set in the spec
        ...((!vegaSpec.width || vegaSpec.width === 0) && { width: availableWidth }),
        ...((!vegaSpec.height || vegaSpec.height === 0) && { height: availableHeight * 0.6 }),
        config: {
          view: {
            // Only set continuous dimensions if not explicitly specified
            ...((!vegaSpec.width || vegaSpec.width === 0) && { continuousWidth: availableWidth }),
            ...((!vegaSpec.height || vegaSpec.height === 0) && { continuousHeight: availableHeight * 0.6 }),
            stroke: 'transparent' // Remove default border
          }
        }
      };

      // Set explicit container dimensions for complex layouts
      if (vegaSpec.vconcat || vegaSpec.hconcat || vegaSpec.facet) {
        container.style.minHeight = `${availableHeight}px`;
      }

      // Remove loading spinner
      try {
        if (loadingSpinner && loadingSpinner.parentNode) {
          container.removeChild(loadingSpinner);
        }
      } catch (e) {
        console.warn('Could not remove loading spinner (this is normal for multiple renders):', e instanceof Error ? e.message : String(e));
      }

      // Create a fresh container div to ensure no conflicts
      const renderContainer = document.createElement('div');
      renderContainer.style.width = '100%';
      container.appendChild(renderContainer);

      const sanitizedSpec = vegaSpec;

      // Render the visualization
      // Deep clone and sanitize the spec one last time to be safe by serializing and parsing.
      // This removes any non-plain-object properties that might be causing issues.
      const finalSpec = JSON.parse(JSON.stringify(sanitizedSpec));
      
      // Log final spec before rendering for violin plots
      if (finalSpec.transform && finalSpec.transform.some((t: any) => t.density)) {
        console.log('Final spec being sent to vega-embed for violin plot:', JSON.stringify(finalSpec, null, 2));
      }
      

      // Additional validation before calling vega-embed
      try {
        // Validate that all field references exist in the data
        if (finalSpec.encoding && finalSpec.data?.values && Array.isArray(finalSpec.data.values) && finalSpec.data.values.length > 0) {
          const dataFields = new Set();
          Object.keys(finalSpec.data.values[0]).forEach(key => dataFields.add(key));

          // Check encoding fields
          Object.values(finalSpec.encoding).forEach((encoding: any) => {
            if (encoding?.field && !dataFields.has(encoding.field) && !finalSpec.transform) {
              console.warn(`Field '${encoding.field}' referenced in encoding but not found in data`);
            }
          });
        } else if (finalSpec.transform) {
          // For specs with transforms (like violin plots), skip field validation
          // as fields may be generated by the transform
          console.log('Spec uses transforms, skipping field validation');
        } else if (finalSpec.data?.url) {
          // For specs with external data, skip field validation
          console.log('Spec uses external data, skipping field validation');
        } else {
          console.log('Skipping field validation for this spec type');
        }
      } catch (validationError) {
        console.warn('Spec validation warning:', validationError);
      }

      const result = await vegaEmbed(renderContainer, finalSpec, embedOptions);

      // Log successful render for violin plots
      if (finalSpec.transform && finalSpec.transform.some((t: any) => t.density)) {
        console.log('Violin plot vega-embed result:', result);
        console.log('Violin plot SVG element:', renderContainer.querySelector('svg'));
        console.log('Violin plot container dimensions:', renderContainer.getBoundingClientRect());
      }

      // Store the view for cleanup
      (container as any)._vegaView = result.view;

      // Simple fix: Make parent containers fit the Vega-Lite content
      setTimeout(() => {
        const vegaEmbedDiv = renderContainer.querySelector('.vega-embed') as HTMLElement;
        if (vegaEmbedDiv) {
          const vegaHeight = vegaEmbedDiv.offsetHeight;
          const vegaWidth = vegaEmbedDiv.offsetWidth;

          // Adjust parent d3-container to fit
          let parent = container.parentElement;
          while (parent && parent.classList.contains('d3-container')) {
            const parentEl = parent as HTMLElement;
            if (parentEl.offsetHeight < vegaHeight) {
              parentEl.style.height = `${vegaHeight + 20}px`;
              parentEl.style.minHeight = `${vegaHeight + 20}px`;
            }
            parent = parent.parentElement;
          }
        }
      }, 100);

      // Set up Vega-Lite specific resize handling
      const setupVegaLiteResizing = () => {
        const vegaEmbedDiv = renderContainer.querySelector('.vega-embed') as HTMLElement;
        const vegaSvg = renderContainer.querySelector('svg') as SVGSVGElement;

        console.log('Setting up Vega-Lite specific resizing:', {
          vegaEmbedDiv: !!vegaEmbedDiv,
          vegaSvg: !!vegaSvg,
          renderContainer: renderContainer.getBoundingClientRect(),
          container: container.getBoundingClientRect()
        });

        if (vegaEmbedDiv || vegaSvg) {
          const targetElement = vegaEmbedDiv || vegaSvg;

          const vegaResizeObserver = new ResizeObserver((entries) => {
            for (const entry of entries) {
              const actualHeight = entry.contentRect.height;
              const actualWidth = entry.contentRect.width;

              console.log(`Vega-Lite element resized: ${actualWidth}x${actualHeight}`);

              // Update parent containers
              let parent = container.parentElement;
              while (parent && (parent.classList.contains('d3-container') || parent.classList.contains('vega-lite-container'))) {
                const parentElement = parent as HTMLElement;
                const currentHeight = parentElement.getBoundingClientRect().height;

                console.log(`Parent ${parentElement.className}: current=${currentHeight}, needed=${actualHeight + 40}`);

                if (currentHeight < actualHeight + 40) {
                  parentElement.style.height = `${actualHeight + 40}px`;
                  parentElement.style.minHeight = `${actualHeight + 40}px`;
                  parentElement.style.overflow = 'visible';
                  console.log(`Updated parent ${parentElement.className} height to ${actualHeight + 40}px`);
                }

                parent = parent.parentElement;
              }
            }
          });

          vegaResizeObserver.observe(targetElement);
          console.log('Vega-Lite resize observer attached to:', targetElement.tagName, targetElement.className);
        }
      };

      // Also set up a MutationObserver to catch when Vega-Lite adds elements
      const mutationObserver = new MutationObserver((mutations) => {
        mutations.forEach((mutation) => {
          if (mutation.type === 'childList') {
            mutation.addedNodes.forEach((node) => {
              if (node.nodeType === Node.ELEMENT_NODE) {
                const element = node as Element;
                if (element.classList.contains('vega-embed') || element.tagName.toLowerCase() === 'svg') {
                  console.log('Vega-Lite element added via mutation observer:', element.tagName, element.className);
                  setTimeout(setupVegaLiteResizing, 100);
                }
              }
            });
          }
        });
      });

      // Observe the render container for changes
      mutationObserver.observe(renderContainer, {
        childList: true,
        subtree: true
      });

      // Clean up mutation observer after a reasonable time
      setTimeout(() => {
        mutationObserver.disconnect();
      }, 5000);

      // Set up resizing with multiple attempts
      setTimeout(setupVegaLiteResizing, 100);
      setTimeout(setupVegaLiteResizing, 500);
      setTimeout(setupVegaLiteResizing, 1000);

      // Immediate size check and adjustment
      setTimeout(() => {
        const vegaEmbedDiv = renderContainer.querySelector('.vega-embed') as HTMLElement;
        if (vegaEmbedDiv) {
          const vegaRect = vegaEmbedDiv.getBoundingClientRect();
          console.log('Immediate Vega-Lite size check:', vegaRect);

          let parent = container.parentElement;
          while (parent && parent.classList.contains('d3-container')) {
            const parentRect = parent.getBoundingClientRect();
            console.log(`Parent ${parent.className} size:`, parentRect);

            if (parentRect.height < vegaRect.height + 40) {
              (parent as HTMLElement).style.height = `${vegaRect.height + 40}px`;
              (parent as HTMLElement).style.minHeight = `${vegaRect.height + 40}px`;
              console.log(`Immediately adjusted parent ${parent.className} to ${vegaRect.height + 40}px`);
            }
            parent = parent.parentElement;
          }
        }
      }, 200);

      // Store references to the vega view and container content
      const vegaContainer = renderContainer.querySelector('.vega-embed') as HTMLElement;

      // Add action buttons container
      const actionsContainer = document.createElement('div');
      actionsContainer.className = 'diagram-actions';
      // The diagram-actions class from index.css will handle styling

      // Force container to have position: relative for absolute positioning
      container.style.position = 'relative';

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
            vegaEmbed(container, vegaSpec, embedOptions);
          }

          // Re-add the actions container
          container.insertBefore(actionsContainer, container.firstChild);
        };
        actionsContainer.appendChild(sourceButton);

        // Force the container to have position: relative
        container.style.position = 'relative';

        // Insert actions container at the beginning of the container
        container.insertBefore(actionsContainer, container.firstChild);

        // Ensure the actions container is visible on hover
        container.addEventListener('mouseenter', () => actionsContainer.style.opacity = '1');
        container.addEventListener('mouseleave', () => actionsContainer.style.opacity = '0');
        // Insert actions container as the first child to match other plugins
        container.insertBefore(actionsContainer, container.firstChild);

        // Post-render fixes for sizing issues
        setTimeout(() => {
          const svgElement = container.querySelector('svg');
          const vegaEmbedDiv = container.classList.contains('vega-embed') ? container : container.querySelector('.vega-embed') as HTMLElement;

          if (svgElement) {
            // Only apply responsive sizing if the chart doesn't have explicit dimensions
            const hasExplicitWidth = vegaSpec.width && vegaSpec.width > 0;
            const hasExplicitHeight = vegaSpec.height && vegaSpec.height > 0;

            if (!hasExplicitWidth) {
              svgElement.style.width = '100%';
              svgElement.style.maxWidth = '100%';
            }

            if (!hasExplicitHeight) {
              svgElement.style.height = 'auto';
            }

            svgElement.style.display = 'block';

            console.log(">>> vegaLitePlugin: SVG sizing applied:", {
              svgWidth: svgElement.style.width,
              containerWidth: container.getBoundingClientRect().width,
              svgRect: svgElement.getBoundingClientRect()
            });
          }

          if (vegaEmbedDiv) {
            // Only apply responsive width if not explicitly set
            const hasExplicitWidth = vegaSpec.width && vegaSpec.width > 0;

            if (!hasExplicitWidth) {
              vegaEmbedDiv.style.width = '100%';
              vegaEmbedDiv.style.maxWidth = '100%';
            }
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
            } else {
              // Try to get dimensions from the SVG attributes
              const width = svgElement.getAttribute('width');
              const height = svgElement.getAttribute('height');
              if (width && height) {
                svgWidth = parseFloat(width);
                svgHeight = parseFloat(height);
              }
            }

            console.log('SVG content dimensions:', { svgWidth, svgHeight, viewBox });

            // Only apply responsive sizing if chart doesn't have explicit dimensions
            const hasExplicitWidth = vegaSpec.width && vegaSpec.width > 0;
            const hasExplicitHeight = vegaSpec.height && vegaSpec.height > 0;

            if (!hasExplicitWidth) {
              svgElement.style.width = '100%';
              svgElement.style.maxWidth = '100%';
            }

            if (!hasExplicitHeight) {
              svgElement.style.height = 'auto';
            }

            svgElement.style.overflow = 'visible';

            // Force the vega-embed container to accommodate the full SVG content
            if (vegaEmbedDiv) {
              // Only calculate responsive height if height isn't explicitly set
              if (!hasExplicitHeight) {
                const containerWidth = vegaEmbedDiv.getBoundingClientRect().width;
                const aspectRatio = svgHeight / svgWidth;
                const neededHeight = containerWidth * aspectRatio;

                vegaEmbedDiv.style.height = `${neededHeight}px`;
                vegaEmbedDiv.style.minHeight = `${neededHeight}px`;

                console.log('Container sizing:', { containerWidth, aspectRatio, neededHeight });
              }

              vegaEmbedDiv.style.display = 'block';
              vegaEmbedDiv.style.overflow = 'visible';
            }

            // Force container and all parents to accommodate the content
            const forceContainerResize = () => {
              const actualHeight = svgElement.getBoundingClientRect().height;
              let parent = container.parentElement;
              while (parent && parent.classList.contains('d3-container')) {
                const parentElement = parent as HTMLElement;
                if (parentElement.getBoundingClientRect().height < actualHeight + 40) {
                  parentElement.style.height = `${actualHeight + 40}px`;
                  parentElement.style.minHeight = `${actualHeight + 40}px`;
                  console.log(`Force resized parent ${parentElement.className} to ${actualHeight + 40}px`);
                }
                parent = parent.parentElement;
              }
            };

            setTimeout(forceContainerResize, 100);
            setTimeout(forceContainerResize, 500);

            // Only adjust parent containers if height isn't explicitly set
            if (!hasExplicitHeight) {
              let parent = container.parentElement;
              while (parent && parent.classList.contains('d3-container')) {
                (parent as HTMLElement).style.height = 'auto';
                (parent as HTMLElement).style.minHeight = `${svgHeight}px`;
                (parent as HTMLElement).style.overflow = 'visible';
                console.log('Updated parent container:', parent.className, 'to height: auto, minHeight:', svgHeight);
                parent = parent.parentElement;
              }
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

      }
    } catch (error) {
      console.error('Vega-Lite rendering error:', error);

      // Special error handling for violin plots
      if (vegaSpec && vegaSpec.transform && vegaSpec.transform.some((t: any) => t.density || t.kde)) {
        console.error('Violin plot specific error details:', {
          hasData: !!vegaSpec.data,
          hasTransform: !!vegaSpec.transform,
          hasMark: !!vegaSpec.mark,
          transformTypes: vegaSpec.transform.map((t: any) => Object.keys(t))
        });
      }

      // Add more specific error information for debugging
      if (error instanceof Error) {
        console.error('Error details:', {
          message: error.message,
          stack: error.stack
        });
      }

      // Remove loading spinner if it exists
      try {
        const spinner = container.querySelector('.vega-lite-loading-spinner') as HTMLElement;
        if (spinner && spinner.parentNode) {
          container.removeChild(spinner);
        }
      } catch (e) {
        console.warn('Could not remove loading spinner during error handling:', e instanceof Error ? e.message : String(e));
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
