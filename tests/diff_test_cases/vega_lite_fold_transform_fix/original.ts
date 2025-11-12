import { D3RenderPlugin } from '../../types/d3';
import { isDiagramDefinitionComplete } from '../../utils/diagramUtils';
import { extractDefinitionFromYAML } from '../../utils/diagramUtils';

import vegaEmbed, { EmbedOptions } from 'vega-embed';

export interface VegaLiteSpec {
  type: 'vega-lite';
  isStreaming?: boolean;
  isMarkdownBlockClosed?: boolean;
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

  // Check for obviously incomplete JSON (common during streaming)
  const trimmed = definition.trim();
  if (trimmed.endsWith(',') || trimmed.endsWith('{') || trimmed.endsWith('[')) {
    return false;
  }

  // Check for unterminated strings (common streaming issue)
  if (trimmed.includes('"$') && !trimmed.includes('"$schema"')) {
    return false;
  }

  try {
    const parsed = JSON.parse(definition);

    // Basic completeness checks
    if (!parsed || typeof parsed !== 'object') return false;

    // Check for incomplete schema URLs (common during streaming)
    if (parsed.$schema && typeof parsed.$schema === 'string' && !parsed.$schema.endsWith('.json')) {
      return false;
    }

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
    console.log('Vega-Lite streaming state:', {
      isStreaming: spec.isStreaming,
      isMarkdownBlockClosed: spec.isMarkdownBlockClosed,
      forceRender: spec.forceRender
    });

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

    // Determine if we should wait for more content
    // Priority: explicit streaming flags, then content analysis
    let shouldWaitForComplete = false;

    // Content analysis - this takes priority over streaming flags
    // Check if this is a complete Vega-Lite object (has schema, data, mark, etc.)
    const isCompleteVegaLiteObject = spec.$schema && (spec.data || spec.datasets) &&
      (spec.mark || spec.layer || spec.vconcat || spec.hconcat || spec.facet || spec.repeat);

    if (isCompleteVegaLiteObject) {
      console.log('Vega-Lite: Complete object spec detected, proceeding with render');
      shouldWaitForComplete = false;
    } else if (spec.definition) {
      // Only check definition if we don't have a complete object
      if (spec.definition) {
        const definition = spec.definition || JSON.stringify(spec);

        // If definition is empty or just whitespace, always wait unless forced
        if (!definition || definition.trim().length === 0) {
          console.log('Vega-Lite: Waiting due to empty definition', { isStreaming: spec.isStreaming, isMarkdownBlockClosed: spec.isMarkdownBlockClosed });
          shouldWaitForComplete = !spec.forceRender;
        } else {

          const isComplete = isDiagramDefinitionComplete(definition, 'vega-lite');
          console.log('Vega-Lite definition completeness check:', {
            isComplete,
            definitionLength: definition.length,
            definitionPreview: definition.substring(0, 100)
          });

          // If definition is incomplete and we're not forcing render, wait
          if (!isComplete && !spec.forceRender) {
            console.log('Vega-Lite: Waiting due to incomplete definition', { isStreaming: spec.isStreaming, isMarkdownBlockClosed: spec.isMarkdownBlockClosed });
            shouldWaitForComplete = true;
          }

          // Special case: obvious streaming indicators
          if (definition.includes('Unterminated string') || definition.includes('Unexpected end')) {
            console.log('Vega-Lite: Waiting due to JSON parsing indicators');
            shouldWaitForComplete = true;
          }
        }
      } else if (!isCompleteVegaLiteObject) {
        // No definition at all, wait unless forced
        console.log('Vega-Lite: Waiting due to missing definition', { isStreaming: spec.isStreaming, isMarkdownBlockClosed: spec.isMarkdownBlockClosed });
        shouldWaitForComplete = !spec.forceRender;
      }
    }

    // Final override: if markdown block is closed, try to render regardless of other conditions
    if (spec.isMarkdownBlockClosed && (isCompleteVegaLiteObject || (spec.definition && spec.definition.trim().length > 0))) {
      console.log('Vega-Lite: Markdown block closed with content, forcing render');
      shouldWaitForComplete = false;
    }

    // Additional check: if we're actively streaming and block isn't closed, wait regardless of content
    if (spec.isStreaming && !spec.isMarkdownBlockClosed && !spec.forceRender) {
      console.log('Vega-Lite: Waiting due to active streaming state');
      shouldWaitForComplete = true;
    }

    console.log('Vega-Lite: After all checks, shouldWaitForComplete =', shouldWaitForComplete);

    console.log('Vega-Lite: About to check shouldWaitForComplete condition...');
    if (shouldWaitForComplete) {
      console.log('Vega-Lite: Inside shouldWaitForComplete block');
      // Create enhanced waiting interface with debugging options like Mermaid
      const waitingContainer = document.createElement('div');
      waitingContainer.style.cssText = `
        text-align: center; 
        padding: 20px; 
        background-color: ${isDarkMode ? '#1f1f1f' : '#f6f8fa'}; 
        border: 1px dashed #ccc; 
        border-radius: 4px;
        position: relative;
      `;
      
      waitingContainer.innerHTML = `
        <p>Waiting for complete Vega-Lite specification...</p>
        <div style="margin-top: 15px;">
          <button class="vega-lite-retry-btn" style="
            background-color: #4361ee;
            color: white;
            border: none;
            border-radius: 4px;
            padding: 8px 16px;
            margin: 0 5px;
            cursor: pointer;
            font-size: 14px;
          ">🔄 Force Render</button>
          <button class="vega-lite-source-btn" style="
            background-color: #6c757d;
            color: white;
            border: none;
            border-radius: 4px;
            padding: 8px 16px;
            margin: 0 5px;
            cursor: pointer;
            font-size: 14px;
          ">📝 View Source</button>
        </div>
      `;
      
      container.innerHTML = '';
      container.appendChild(waitingContainer);
      
      // Add event listeners for the buttons using querySelector instead of getElementById
      const retryButton = waitingContainer.querySelector('.vega-lite-retry-btn') as HTMLButtonElement;
      const sourceButton = waitingContainer.querySelector('.vega-lite-source-btn') as HTMLButtonElement;
      
      if (retryButton) {
        retryButton.onclick = () => {
          console.log('Force rendering Vega-Lite visualization');
          const forceSpec = { ...spec, forceRender: true };
          vegaLitePlugin.render(container, d3, forceSpec, isDarkMode);
        };
      }
      
      if (sourceButton) {
        sourceButton.onclick = () => {
          console.log('Showing Vega-Lite source for debugging');
          const sourceDefinition = spec.definition || JSON.stringify(spec, null, 2);
          container.innerHTML = `
              <div style="
                background-color: ${isDarkMode ? '#1f1f1f' : '#f6f8fa'};
                border: 1px solid ${isDarkMode ? '#444' : '#e1e4e8'};
                border-radius: 6px;
                padding: 16px;
                margin: 10px 0;
              ">
                <div style="margin-bottom: 15px; display: flex; justify-content: space-between; align-items: center;">
                  <strong style="color: ${isDarkMode ? '#f8f9fa' : '#24292e'};">Vega-Lite Source:</strong>
                  <div>
                    <button class="force-render-from-source" style="
                      background-color: #28a745;
                      color: white;
                      border: none;
                      border-radius: 4px;
                      padding: 6px 12px;
                      margin-right: 8px;
                      cursor: pointer;
                      font-size: 13px;
                    ">🔄 Force Render</button>
                    <button class="expand-source" style="
                      background-color: #6c757d;
                      color: white;
                      border: none;
                      border-radius: 4px;
                      padding: 6px 12px;
                      cursor: pointer;
                      font-size: 13px;
                    ">📄 Expand</button>
                  </div>
                </div>
                <pre style="
                  background-color: ${isDarkMode ? '#0d1117' : '#f6f8fa'};
                  padding: 12px;
                  border-radius: 4px;
                  overflow: auto;
                  max-height: 300px;
                  margin: 0;
                  border: 1px solid ${isDarkMode ? '#30363d' : '#e1e4e8'};
                  font-family: 'SFMono-Regular', 'Monaco', 'Inconsolata', 'Liberation Mono', 'Courier New', monospace;
                  font-size: 13px;
                  line-height: 1.45;
                  color: ${isDarkMode ? '#e6edf3' : '#24292f'};
                "><code>${sourceDefinition}</code></pre>
                <div style="margin-top: 12px; font-size: 13px; color: ${isDarkMode ? '#8b949e' : '#656d76'};">
                  <strong>Debug Info:</strong><br>
                  • Streaming: ${spec.isStreaming ? 'Yes' : 'No'}<br>
                  • Block Closed: ${spec.isMarkdownBlockClosed ? 'Yes' : 'No'}<br>
                  • Definition Length: ${sourceDefinition.length} characters<br>
                  • Complete Object: ${isCompleteVegaLiteObject ? 'Yes' : 'No'}
                </div>
              </div>
            `;
          
          // Add event listeners for the new buttons in the source view
          setTimeout(() => {
            const forceRenderBtn = container.querySelector('.force-render-from-source') as HTMLButtonElement;
            const expandBtn = container.querySelector('.expand-source') as HTMLButtonElement;
            
            if (forceRenderBtn) {
              forceRenderBtn.onclick = () => {
                console.log('Force rendering from source view');
                const forceSpec = { ...spec, forceRender: true };
                vegaLitePlugin.render(container, d3, forceSpec, isDarkMode);
              };
            }
            
            if (expandBtn) {
              expandBtn.onclick = () => {
                const pre = container.querySelector('pre') as HTMLElement;
                if (pre) {
                  const isExpanded = pre.style.maxHeight === 'none';
                  pre.style.maxHeight = isExpanded ? '300px' : 'none';
                  expandBtn.textContent = isExpanded ? '📄 Expand' : '📄 Collapse';
                }
              };
            }
          }, 0);
        };
      }
      
      return; // Exit early and wait for complete definition
    }
    console.log('Vega-Lite: Past shouldWaitForComplete check, about to declare vegaSpec');
    console.log('Vega-Lite: Proceeding with spec processing...');
    let vegaSpec: any;

    // CRITICAL PREPROCESSING: Fix common issues that cause "Cannot read properties of undefined (reading 'type')"
    const preprocessVegaSpec = (rawSpec: any): any => {
      if (!rawSpec || typeof rawSpec !== 'object') {
        return rawSpec;
      }

      const spec = JSON.parse(JSON.stringify(rawSpec)); // Deep clone
      
      console.log('🔧 VEGA-PREPROCESS: Starting comprehensive preprocessing');
      
      // Fix 1: Remove problematic shape encodings entirely - this is the most common cause
      if (spec.encoding?.shape) {
        console.log('🔧 VEGA-PREPROCESS: Found shape encoding, analyzing...');
        const shapeEnc = spec.encoding.shape;
        
        // Remove shape encoding in these problematic cases:
        let shouldRemoveShape = false;
        
        if (shapeEnc.type === 'ordinal' && spec.data?.values) {
          const fieldValues = spec.data.values.map((d: any) => d[shapeEnc.field]).filter((v: any) => v !== undefined);
          const isNumeric = fieldValues.length > 0 && fieldValues.every((v: any) => typeof v === 'number');
          if (isNumeric) {
            console.log('🔧 VEGA-PREPROCESS: Removing ordinal shape encoding with numeric data');
            shouldRemoveShape = true;
          }
        }
        
        if (shapeEnc.scale?.range?.some((s: any) => typeof s === 'string' && s.includes('triangle'))) {
          console.log('🔧 VEGA-PREPROCESS: Removing shape encoding with problematic triangle shapes');
          shouldRemoveShape = true;
        }
        
        if (!shapeEnc.field || !spec.data?.values) {
          console.log('🔧 VEGA-PREPROCESS: Removing shape encoding without proper field/data');
          shouldRemoveShape = true;
        }
        
        if (shouldRemoveShape) {
          delete spec.encoding.shape;
          console.log('🔧 VEGA-PREPROCESS: Removed problematic shape encoding');
        }
      }
      
      // Fix 2: Validate all encoding field references
      if (spec.encoding && spec.data?.values && Array.isArray(spec.data.values) && spec.data.values.length > 0) {
        const availableFields = Object.keys(spec.data.values[0]);
        console.log('🔧 VEGA-PREPROCESS: Available fields:', availableFields);
        
        Object.keys(spec.encoding).forEach(channel => {
          const channelSpec = spec.encoding[channel];
          if (channelSpec?.field && !availableFields.includes(channelSpec.field)) {
            console.log(`🔧 VEGA-PREPROCESS: Removing ${channel} encoding with invalid field: ${channelSpec.field}`);
            delete spec.encoding[channel];
          }
        });
      }
      
      // Fix 3: Clean up null/undefined values in scale domains
      ['x', 'y', 'color', 'fill', 'stroke', 'opacity', 'size'].forEach(channel => {
        if (spec.encoding?.[channel]?.scale?.domain) {
          const domain = spec.encoding[channel].scale.domain;
          if (Array.isArray(domain)) {
            const cleanDomain = domain.filter(v => v !== null && v !== undefined);
            if (cleanDomain.length !== domain.length) {
              console.log(`🔧 VEGA-PREPROCESS: Cleaned null values from ${channel} domain`);
              if (cleanDomain.length > 0) {
                spec.encoding[channel].scale.domain = cleanDomain;
              } else {
                delete spec.encoding[channel].scale.domain;
              }
            }
          } else if (domain === null || domain === undefined) {
            console.log(`🔧 VEGA-PREPROCESS: Removed null ${channel} domain`);
            delete spec.encoding[channel].scale.domain;
          }
        }
      });
      
      // Fix 4: Ensure schema exists
      if (!spec.$schema) {
        spec.$schema = 'https://vega.github.io/schema/vega-lite/v5.json';
      }
      
      console.log('🔧 VEGA-PREPROCESS: Preprocessing complete');
      return spec;
    };

    if (typeof spec === 'string') {
      const extractedContent = extractDefinitionFromYAML(spec, 'vega-lite');
      try {
        const rawSpec = JSON.parse(extractedContent);
        vegaSpec = preprocessVegaSpec(sanitizeSpec(rawSpec));
      } catch (parseError) {
        console.debug('Vega-Lite: JSON parse error during processing:', parseError);
        throw parseError; // Re-throw to be handled by outer try-catch
      }
    } else if (spec.definition) {
      const extractedContent = extractDefinitionFromYAML(spec.definition, 'vega-lite');
      try {
        const rawSpec = JSON.parse(extractedContent);
        vegaSpec = preprocessVegaSpec(sanitizeSpec(rawSpec));
      } catch (parseError) {
        console.debug('Vega-Lite: JSON parse error during processing:', parseError);
        throw parseError; // Re-throw to be handled by outer try-catch
      }
    } else {
      // Use the spec object directly, but remove our custom properties
      const rawSpec = sanitizeSpec({ ...spec });
      ['type', 'isStreaming', 'forceRender', 'definition'].forEach(prop => delete rawSpec[prop]);
      vegaSpec = preprocessVegaSpec(rawSpec);
    }

    console.log('Vega-Lite: Spec processed, starting try block for rendering...');

    try {
      // COMPREHENSIVE FIX: Handle all cases that cause "Cannot read properties of null (reading 'slice')" error
      // This error occurs when Vega-Lite tries to process invalid scale domains or ranges
      
      // Fix 0: Upgrade old schema versions that may be causing compatibility issues
      if (vegaSpec.$schema && vegaSpec.$schema.includes('v4')) {
        console.log('SCHEMA FIX: Upgrading old v4 schema to v5 for better compatibility');
        vegaSpec.$schema = 'https://vega.github.io/schema/vega-lite/v5.json';
      }
      
      // Fix 0.1: Handle nominal fields with many categories that might cause slice errors
      if (vegaSpec.encoding?.x?.field === 'file' && vegaSpec.data?.values) {
        const fileCount = vegaSpec.data.values.length;
        if (fileCount > 20) {
          console.log('FILE COUNT FIX: Too many files for x-axis, limiting to top 20');
          vegaSpec.data.values = vegaSpec.data.values.slice(0, 20);
        }
      }
      
      // Fix 1: Handle shape encoding with null/undefined values
      if (vegaSpec.encoding?.shape) {
        const shapeEncoding = vegaSpec.encoding.shape;
        console.log('SHAPE FIX: Processing shape encoding:', JSON.stringify(shapeEncoding, null, 2));
        
        // Check if we have data to validate against
        if (vegaSpec.data?.values && Array.isArray(vegaSpec.data.values)) {
          const fieldValues = vegaSpec.data.values
            .map(d => d[shapeEncoding.field])
            .filter(v => v !== null && v !== undefined);
            
          // If field has null/undefined values or is numeric with ordinal type, remove shape encoding
          if (fieldValues.length === 0 || 
              (shapeEncoding.type === 'ordinal' && fieldValues.every(v => typeof v === 'number'))) {
            console.log('SHAPE FIX: Removing problematic shape encoding');
            delete vegaSpec.encoding.shape;
          } else if (shapeEncoding.scale?.range) {
            // Fix invalid shape names in scale range
            const validShapes = ['circle', 'square', 'cross', 'diamond', 'triangle-up', 'triangle-down', 'triangle-right', 'triangle-left'];
            vegaSpec.encoding.shape.scale.range = shapeEncoding.scale.range
              .map(shape => validShapes.includes(shape) ? shape : 'circle')
              .slice(0, Math.min(shapeEncoding.scale.range.length, fieldValues.length));
          }
        } else {
          // No data available to validate, remove shape encoding as safety measure
          console.log('SHAPE FIX: No data available, removing shape encoding as safety measure');
          delete vegaSpec.encoding.shape;
        }
      }
      
      // Fix 2: Handle color encoding with invalid scale domains
      ['color', 'fill', 'stroke', 'opacity', 'size'].forEach(channel => {
        if (vegaSpec.encoding?.[channel]?.scale?.domain) {
          const domain = vegaSpec.encoding[channel].scale.domain;
          if (domain === null || (Array.isArray(domain) && domain.some(v => v === null || v === undefined))) {
            console.log(`DOMAIN FIX: Removing null values from ${channel} domain`);
            if (Array.isArray(domain)) {
              vegaSpec.encoding[channel].scale.domain = domain.filter(v => v !== null && v !== undefined);
            } else {
              delete vegaSpec.encoding[channel].scale.domain;
            }
          }
        }
      });
      
      // Fix 3: Validate data fields before rendering
      if (vegaSpec.data?.values && Array.isArray(vegaSpec.data.values)) {
        // Remove any rows with all null/undefined values
        const cleanedValues = vegaSpec.data.values.filter(row => 
          row && typeof row === 'object' && Object.values(row).some(v => v !== null && v !== undefined)
        );
        
        if (cleanedValues.length < vegaSpec.data.values.length) {
          console.log(`DATA FIX: Cleaned ${vegaSpec.data.values.length - cleanedValues.length} empty rows`);
          vegaSpec.data.values = cleanedValues;
        }
      }
      
      // Handle shape encoding that causes "Cannot read properties of null (reading 'slice')" error

      // Apply this fix EARLY in the preprocessing pipeline
    // Additional post-preprocessing validations and fixes
    console.log('🔧 VEGA-POST-PROCESS: Starting additional fixes');
    
    // Handle problematic axis configurations
    if (vegaSpec.encoding?.x?.axis?.labelLimit !== undefined && vegaSpec.encoding.x.axis.labelLimit <= 0) {
      console.log('🔧 VEGA-POST-PROCESS: Fixing problematic axis labelLimit');
      delete vegaSpec.encoding.x.axis.labelLimit;
    }
    
    // Fix invalid color names like "#green" 
    try {
      let specStringForColorFix = JSON.stringify(vegaSpec);
      // Fix colors with # prefix that should be plain color names
      specStringForColorFix = specStringForColorFix.replace(/"#(green|red|orange|blue|yellow|purple|black|white|gray|grey|cyan|magenta|pink|brown|violet|indigo|gold|silver)"/gi, '"$1"');
      // Fix invalid color names that aren't real CSS colors
      specStringForColorFix = specStringForColorFix.replace(/"rainbow"/gi, '"#ff6b6b"');
      specStringForColorFix = specStringForColorFix.replace(/"gradient"/gi, '"#4ecdc4"');
      specStringForColorFix = specStringForColorFix.replace(/"multicolor"/gi, '"#45b7d1"');
      vegaSpec = JSON.parse(specStringForColorFix);
      console.log('🔧 VEGA-POST-PROCESS: Applied color fixes to spec');
    } catch (e) {
      console.warn("Could not apply color fix to Vega-Lite spec", e);
    }
    
    // Validate the spec before rendering
    if (!vegaSpec || typeof vegaSpec !== 'object') {
      throw new Error('Invalid Vega-Lite specification: spec must be an object');
    }
    
    // Ensure required properties exist
    if (!vegaSpec.data && !vegaSpec.datasets) {
      throw new Error('Invalid Vega-Lite specification: missing data or datasets');
    }
    
    // Check for valid mark or composition
    if (!vegaSpec.mark && !vegaSpec.layer && !vegaSpec.vconcat && !vegaSpec.hconcat && 
        !vegaSpec.facet && !vegaSpec.repeat && !vegaSpec.transform) {
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
        type: 'fit',
        contains: 'content'
      };
    } else if (vegaSpec.autosize && typeof vegaSpec.autosize === 'object') {
      // Preserve existing autosize configuration
      vegaSpec.autosize = { ...vegaSpec.autosize };
    }

    // For charts with explicit dimensions, use a more conservative autosize
    if ((vegaSpec.width && vegaSpec.width > 0) || (vegaSpec.height && vegaSpec.height > 0)) {
      vegaSpec.autosize = {
        type: 'fit',
        contains: 'content'
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
      };
    }

    // Ensure reasonable minimum dimensions for complex visualizations
    if (vegaSpec.width && vegaSpec.width < 200) {
      vegaSpec.width = 400;
    }
    if (vegaSpec.height && vegaSpec.height < 250) {
      vegaSpec.height = 300;
    }

    // Fix unrecognized signal references that may be added during preprocessing
    const removeInvalidSignals = (obj: any): any => {
      if (obj && typeof obj === 'object') {
        if (Array.isArray(obj)) {
          return obj.map(removeInvalidSignals);
        } else {
          const cleaned: any = {};
          for (const [key, value] of Object.entries(obj)) {
            // Skip signal references that aren't defined
            if (key === 'signal' && typeof value === 'string' && value.includes('tier_focus')) {
              console.log('Removing invalid signal reference:', value);
              continue;
            }
            cleaned[key] = removeInvalidSignals(value);
          }
          return cleaned;
        }
      }
      return obj;
    };
    
    vegaSpec = removeInvalidSignals(vegaSpec);

    // Fix treemap charts missing x and color encodings
    if (vegaSpec.mark && (vegaSpec.mark.type === 'rect' || vegaSpec.mark === 'rect') &&
        vegaSpec.params && vegaSpec.params.some(p => p.bind && p.bind.input === 'select') &&
        vegaSpec.transform && vegaSpec.transform.some(t => t.aggregate) &&
        vegaSpec.encoding && vegaSpec.encoding.y && !vegaSpec.encoding.x && !vegaSpec.encoding.color) {
      console.log('Fixing treemap chart x and color encodings');
      
      vegaSpec.encoding.x = {
        field: 'total_budget',
        type: 'quantitative',
        title: 'Budget'
      };
      vegaSpec.encoding.color = {
        field: 'display_category',
        type: 'nominal',
        title: 'Category'
      };
    }

    // Fix population density plots with sequence/coordinate issues
    if (vegaSpec.transform && vegaSpec.data?.values && vegaSpec.data.values[0]?.lat && vegaSpec.data.values[0]?.lon) {
      console.log('Fixing population density plot sequence and coordinates');
      
      // Fix any sequence transform that uses datum.count instead of datum.dot_count
      vegaSpec.transform.forEach(transform => {
        if (transform.calculate && transform.calculate.includes('datum.count')) {
          transform.calculate = transform.calculate.replace('datum.count', 'datum.dot_count');
          console.log('Fixed sequence transform to use dot_count');
        }
      });
      
      // Fix x/y encodings to use jittered coordinates correctly
      if (vegaSpec.encoding && (vegaSpec.encoding.x?.field === 'lat' || vegaSpec.encoding.y?.field === 'lon')) {
        vegaSpec.encoding.x = {
          field: 'jittered_lon',
          type: 'quantitative',
          axis: null
        };
        vegaSpec.encoding.y = {
          field: 'jittered_lat', 
          type: 'quantitative',
          axis: null
        };
        console.log('Fixed x/y encodings to use jittered coordinates');
      }
    }

    // Fix sequence transforms that use expressions (need to be flattened)
    if (vegaSpec.transform && vegaSpec.transform.some(t => t.sequence && t.sequence.stop && t.sequence.stop.expr)) {
      console.log('Fixing sequence transform with expression');
      
      // Find the sequence transform and convert it to a flatten transform
      const sequenceIndex = vegaSpec.transform.findIndex(t => t.sequence);
      if (sequenceIndex >= 0) {
        const sequenceTransform = vegaSpec.transform[sequenceIndex];
        
        // Replace sequence with flatten transform
        vegaSpec.transform[sequenceIndex] = {
          flatten: [sequenceTransform.as || 'unit']
        };
        
        // Add a calculate transform to generate the sequence data
        vegaSpec.transform.splice(sequenceIndex, 0, {
          calculate: `sequence(1, datum.count + 1)`,
          as: sequenceTransform.as || 'unit'
        });
      }
    }

    // Fix indexOf function usage in calculate transforms (not supported in Vega-Lite)
    if (vegaSpec.transform && Array.isArray(vegaSpec.transform)) {
      vegaSpec.transform.forEach((transform, index) => {
        if (transform.calculate && transform.calculate.includes('indexOf(')) {
          console.log(`Fixing indexOf usage in transform ${index}: ${transform.calculate}`);
          
          // Replace indexOf with conditional expressions using regex
          let newCalculate = transform.calculate.replace(
            /indexOf\(\[([^\]]+)\],\s*([^)]+)\)/g,
            (match, arrayStr, field) => {
              console.log(`Match found: ${match}, Array: ${arrayStr}, Field: ${field}`);
              const items = arrayStr.split(',').map(item => item.trim().replace(/['"]/g, ''));
              console.log(`Parsed items:`, items);
              const conditions = items.map((item, idx) => 
                `${field.trim()} == '${item}' ? ${idx}`
              ).join(' : ');
              const result = `(${conditions} : -1)`;
              console.log(`Generated replacement: ${result}`);
              return result;
            }
          );
          
          transform.calculate = newCalculate;
          console.log(`Final calculate: ${newCalculate}`);
        }
      });
    }

    // Fix strokeDash encoding without proper scale (causes 'slice' error)
    if (vegaSpec.encoding?.strokeDash && vegaSpec.encoding.strokeDash.type === 'nominal' && !vegaSpec.encoding.strokeDash.scale) {
      console.log('Fixing strokeDash encoding scale');
      vegaSpec.encoding.strokeDash.scale = {
        range: [[1, 0], [5, 5]]  // solid line, dashed line
      };
    }

    // Fix radar chart transform order - window transforms must come before calculate transforms that use them
    if (vegaSpec.transform && Array.isArray(vegaSpec.transform)) {
      const hasWindowTransform = vegaSpec.transform.some(t => t.window);
      const hasCalculateUsingSkillIndex = vegaSpec.transform.some(t => 
        t.calculate && t.calculate.includes('skill_index')
      );
      
      if (hasWindowTransform && hasCalculateUsingSkillIndex) {
        console.log('Fixing radar chart transform order');
        // Move window transforms before calculate transforms
        const windowTransforms = vegaSpec.transform.filter(t => t.window);
        const otherTransforms = vegaSpec.transform.filter(t => !t.window);
        vegaSpec.transform = [...windowTransforms, ...otherTransforms];
      }
    }

    // Fix population pyramid charts missing x and color encodings
    if (vegaSpec.mark && (vegaSpec.mark.type === 'bar' || vegaSpec.mark === 'bar') &&
        vegaSpec.transform && vegaSpec.transform.some(t => t.fold && t.fold.includes('male') && t.fold.includes('female')) &&
        vegaSpec.encoding && vegaSpec.encoding.y && !vegaSpec.encoding.x && !vegaSpec.encoding.color) {
      console.log('Fixing population pyramid chart x and color encodings');
      
      vegaSpec.encoding.x = {
        field: 'signed_population',
        type: 'quantitative',
        title: 'Population',
        axis: {
          format: '~s'
        }
      };
      vegaSpec.encoding.color = {
        field: 'gender',
        type: 'nominal',
        scale: {
          domain: ['male', 'female'],
          range: ['#4575b4', '#d73027']
        },
        title: 'Gender'
      };
    }

    // Fix hierarchical drill-down charts missing x,y encodings
    if (vegaSpec.mark && (vegaSpec.mark.type === 'bar' || vegaSpec.mark === 'bar') &&
        vegaSpec.params && vegaSpec.params.some(p => p.bind && p.bind.input === 'select') &&
        vegaSpec.transform && vegaSpec.transform.some(t => t.calculate && t.calculate.includes('drill_level')) &&
        vegaSpec.encoding && vegaSpec.encoding.color && !vegaSpec.encoding.x && !vegaSpec.encoding.y) {
      console.log('Fixing hierarchical drill-down chart x,y encodings');
      
      vegaSpec.encoding.x = {
        field: 'display_category',
        type: 'nominal',
        title: 'Category'
      };
      vegaSpec.encoding.y = {
        field: 'total_value',
        type: 'quantitative',
        title: 'Value'
      };
    }

    // Fix 2D histograms missing x,y,color encodings
    if (vegaSpec.mark && (vegaSpec.mark.type === 'rect' || vegaSpec.mark === 'rect') &&
        vegaSpec.transform && vegaSpec.transform.some(t => t.aggregate && t.groupby && t.groupby.length === 2 && t.groupby.every(g => g.bin)) &&
        vegaSpec.encoding && !vegaSpec.encoding.x && !vegaSpec.encoding.y && !vegaSpec.encoding.color) {
      console.log('Fixing 2D histogram x,y,color encodings');
      
      vegaSpec.encoding.x = {
        field: 'x_center',
        type: 'quantitative',
        title: 'X'
      };
      vegaSpec.encoding.y = {
        field: 'y_center',
        type: 'quantitative',
        title: 'Y'
      };
      vegaSpec.encoding.color = {
        field: 'count',
        type: 'quantitative',
        scale: { scheme: 'blues' },
        title: 'Count'
      };
    }

    // Fix combined violin/box plots with incorrect field references
    if (vegaSpec.layer && Array.isArray(vegaSpec.layer)) {
      vegaSpec.layer.forEach((layer, index) => {
        if (layer.transform && layer.transform.some(t => t.density) &&
            layer.mark && (layer.mark.type === 'area' || layer.mark === 'area') &&
            layer.encoding && layer.encoding.x && layer.encoding.x.field === 'value') {
          console.log(`Fixing violin layer ${index} field references`);
          
          // For violin plots, x should be the density transform output field 'value', not the original 'value'
          // The density transform creates new 'value' and 'density' fields
          layer.encoding.x = {
            field: 'value', // This is the density transform output, not original data
            type: 'quantitative',
            title: layer.encoding.x.title || 'Value'
          };
          
          // Remove xOffset as it's causing issues - the density field should control the shape
          if (layer.encoding.xOffset) {
            delete layer.encoding.xOffset;
            console.log(`Removed xOffset from violin layer ${index}`);
          }
        }
      });
    }

    // Fix gauge charts missing theta2 encoding (enhanced for multi-layer gauges)
    if (vegaSpec.facet && vegaSpec.spec && vegaSpec.spec.layer &&
        vegaSpec.spec.layer.some(layer => layer.mark && layer.mark.type === 'arc')) {
      console.log('Fixing multi-layer gauge chart encodings');
      
      vegaSpec.spec.layer.forEach((layer, index) => {
        if (layer.mark && layer.mark.type === 'arc') {
          // Case 1: Background arc with theta2 in mark - don't add theta encoding
          if (layer.mark.theta2 && layer.encoding?.color?.value) {
            // This is a background arc, remove any theta encodings we might have added
            if (layer.encoding.theta) {
              delete layer.encoding.theta;
              console.log(`Removed theta encoding from background arc layer ${index}`);
            }
            if (layer.encoding.theta2) {
              delete layer.encoding.theta2;
              console.log(`Removed theta2 encoding from background arc layer ${index}`);
            }
          }
          // Case 2: Data arc with theta field but missing theta2
          else if (layer.encoding && layer.encoding.theta && layer.encoding.theta.field && !layer.encoding.theta2) {
            layer.encoding.theta2 = { value: 0 };
            console.log(`Added theta2 to data arc layer ${index}`);
          }
        }
      });
    }

    // Fix geographic dot density plots with wrong x,y field references
    if (vegaSpec.mark && (vegaSpec.mark.type === 'circle' || vegaSpec.mark === 'circle') &&
        vegaSpec.transform && vegaSpec.transform.some(t => t.sequence) &&
        vegaSpec.encoding && vegaSpec.encoding.x && vegaSpec.encoding.y &&
        (vegaSpec.encoding.x.field === 'x' || vegaSpec.encoding.y.field === 'y') &&
        vegaSpec.data?.values && vegaSpec.data.values[0]?.lat && vegaSpec.data.values[0]?.lon) {
      console.log('Fixing geographic dot density plot x,y field references');
      
      vegaSpec.encoding.x = {
        field: 'jittered_lon',
        type: 'quantitative',
        axis: null
      };
      vegaSpec.encoding.y = {
        field: 'jittered_lat',
        type: 'quantitative',
        axis: null
      };
    }

    // Fix annual calendar charts missing y-axis encoding
    if (vegaSpec.mark && (vegaSpec.mark.type === 'rect' || vegaSpec.mark === 'rect') &&
        vegaSpec.encoding && vegaSpec.encoding.x && vegaSpec.encoding.x.field === 'weekday' && !vegaSpec.encoding.y &&
        vegaSpec.transform && vegaSpec.transform.some(t => t.calculate && t.calculate.includes('week('))) {
      console.log('Fixing annual calendar chart y-axis encoding');
      
      vegaSpec.encoding.y = {
        field: 'week',
        type: 'ordinal',
        title: 'Week of Year'
      };
    }

    // Fix hexbin plots missing x,y,size encodings
    if (vegaSpec.mark && (vegaSpec.mark.type === 'circle' || vegaSpec.mark === 'circle') &&
        vegaSpec.transform && vegaSpec.transform.some(t => t.aggregate && t.groupby && t.groupby.some(g => g.bin)) &&
        vegaSpec.encoding && !vegaSpec.encoding.x && !vegaSpec.encoding.y) {
      console.log('Fixing hexbin plot x,y,size encodings');
      
      vegaSpec.encoding.x = {
        field: 'x_center',
        type: 'quantitative',
        title: 'X'
      };
      vegaSpec.encoding.y = {
        field: 'y_center',
        type: 'quantitative',
        title: 'Y'
      };
      vegaSpec.encoding.size = {
        field: 'count',
        type: 'quantitative',
        title: 'Count',
        scale: { range: [50, 500] }
      };
    }

    // Fix stacked bar charts missing x,y encodings
    if (vegaSpec.mark && (vegaSpec.mark.type === 'bar' || vegaSpec.mark === 'bar') &&
        vegaSpec.encoding && vegaSpec.encoding.color && !vegaSpec.encoding.x && !vegaSpec.encoding.y &&
        vegaSpec.data?.values && vegaSpec.data.values.length > 0) {
      console.log('Fixing stacked bar chart x,y encodings');
      
      const firstRow = vegaSpec.data.values[0];
      const fields = Object.keys(firstRow);
      const categoricalField = fields.find(f => typeof firstRow[f] === 'string' && f !== vegaSpec.encoding.color.field);
      const quantitativeField = fields.find(f => typeof firstRow[f] === 'number' && (f.includes('revenue') || f.includes('value') || f.includes('amount')));
      
      if (categoricalField && quantitativeField) {
        vegaSpec.encoding.x = {
          field: categoricalField,
          type: 'nominal',
          title: categoricalField.charAt(0).toUpperCase() + categoricalField.slice(1)
        };
        vegaSpec.encoding.y = {
          field: quantitativeField,
          type: 'quantitative',
          title: quantitativeField.charAt(0).toUpperCase() + quantitativeField.slice(1)
        };
      }
    }

    // Fix spiral charts missing x,y encodings
    if (vegaSpec.mark && (vegaSpec.mark.type === 'circle' || vegaSpec.mark === 'circle') &&
        vegaSpec.transform && vegaSpec.transform.some(t => t.calculate && (t.calculate.includes('cos(') || t.calculate.includes('sin('))) &&
        vegaSpec.encoding && !vegaSpec.encoding.x && !vegaSpec.encoding.y) {
      console.log('Fixing spiral chart x,y encodings');
      
      vegaSpec.encoding.x = {
        field: 'x',
        type: 'quantitative',
        axis: null
      };
      vegaSpec.encoding.y = {
        field: 'y',
        type: 'quantitative',
        axis: null
      };
    }

    // Fix diverging bar charts missing x and color encodings
    if (vegaSpec.mark && (vegaSpec.mark.type === 'bar' || vegaSpec.mark === 'bar') &&
        vegaSpec.transform && vegaSpec.transform.some(t => t.fold && t.fold.includes('positive') && t.fold.includes('negative')) &&
        vegaSpec.encoding && vegaSpec.encoding.y && !vegaSpec.encoding.x && !vegaSpec.encoding.color) {
      console.log('Fixing diverging bar chart x and color encodings');
      
      vegaSpec.encoding.x = {
        field: 'value',
        type: 'quantitative',
        title: 'Value'
      };
      vegaSpec.encoding.color = {
        field: 'sentiment',
        type: 'nominal',
        scale: {
          domain: ['positive', 'negative'],
          range: ['#2ca02c', '#d62728']
        },
        title: 'Sentiment'
      };
    }

    // Fix scatter plots missing encodings
    if (vegaSpec.mark && (vegaSpec.mark.type === 'point' || vegaSpec.mark === 'point' || vegaSpec.mark.type === 'circle') &&
        vegaSpec.data?.values && !vegaSpec.encoding?.x && !vegaSpec.encoding?.y) {
      console.log('Fixing scatter plot x,y encodings');
      const firstRow = vegaSpec.data.values[0];
      const numericFields = Object.keys(firstRow).filter(key => typeof firstRow[key] === 'number');
      if (numericFields.length >= 2) {
        vegaSpec.encoding = vegaSpec.encoding || {};
        vegaSpec.encoding.x = { field: numericFields[0], type: 'quantitative' };
        vegaSpec.encoding.y = { field: numericFields[1], type: 'quantitative' };
      }
    }

    // Fix line charts missing encodings
    if (vegaSpec.mark && (vegaSpec.mark.type === 'line' || vegaSpec.mark === 'line') &&
        vegaSpec.data?.values && !vegaSpec.encoding?.x && !vegaSpec.encoding?.y && !vegaSpec.transform?.some(t => t.fold)) {
      console.log('Fixing line chart x,y encodings');
      const firstRow = vegaSpec.data.values[0];
      const fields = Object.keys(firstRow);
      const dateField = fields.find(f => firstRow[f] && (f.includes('date') || f.includes('time') || typeof firstRow[f] === 'string' && firstRow[f].match(/\d{4}-\d{2}-\d{2}/)));
      const numericField = fields.find(f => typeof firstRow[f] === 'number');
      
      if (dateField && numericField) {
        vegaSpec.encoding = vegaSpec.encoding || {};
        vegaSpec.encoding.x = { field: dateField, type: 'temporal' };
        vegaSpec.encoding.y = { field: numericField, type: 'quantitative' };
      }
    }

    // Fix heatmaps missing encodings
    if (vegaSpec.mark && (vegaSpec.mark.type === 'rect' || vegaSpec.mark === 'rect') &&
        vegaSpec.encoding?.color && !vegaSpec.encoding?.x && !vegaSpec.encoding?.y &&
        !vegaSpec.transform?.some(t => t.calculate && (t.calculate.includes('day(') || t.calculate.includes('week(')))) {
      console.log('Fixing heatmap x,y encodings');
      const firstRow = vegaSpec.data?.values?.[0];
      if (firstRow) {
        const fields = Object.keys(firstRow);
        const categoricalFields = fields.filter(f => typeof firstRow[f] === 'string' || typeof firstRow[f] === 'number');
        if (categoricalFields.length >= 2) {
          vegaSpec.encoding.x = { field: categoricalFields[0], type: 'nominal' };
          vegaSpec.encoding.y = { field: categoricalFields[1], type: 'nominal' };
        }
      }
    }

    // Fix violin plots missing x,y encodings after density transform
    if (vegaSpec.mark && (vegaSpec.mark.type === 'area' || vegaSpec.mark === 'area') &&
        vegaSpec.transform && vegaSpec.transform.some(t => t.density) &&
        vegaSpec.encoding && !vegaSpec.encoding.x && !vegaSpec.encoding.y) {
      console.log('Fixing violin plot x,y encodings');
      
      // Add x and y encodings for density plot
      vegaSpec.encoding.x = {
        field: 'value',
        type: 'quantitative',
        title: 'Response Time'
      };
      vegaSpec.encoding.y = {
        field: 'density',
        type: 'quantitative',
        title: 'Density'
      };
    }

    // Fix histograms with aggregate count issues (enhanced)
    if (vegaSpec.mark === 'bar' && vegaSpec.encoding && vegaSpec.encoding.x && vegaSpec.encoding.x.bin &&
        vegaSpec.encoding.y && vegaSpec.encoding.y.aggregate === 'count') {
      console.log('Fixing histogram aggregate count encoding');
      
      // Ensure proper y encoding structure for count aggregate
      vegaSpec.encoding.y = {
        aggregate: 'count',
        type: 'quantitative',
        title: vegaSpec.encoding.y.title || 'Count'
      };
      // Remove any incorrect field reference
      delete vegaSpec.encoding.y.field;
    }

    // Fix calendar heatmaps missing x,y encodings
    if (vegaSpec.mark && (vegaSpec.mark.type === 'rect' || vegaSpec.mark === 'rect') &&
        vegaSpec.transform && vegaSpec.transform.some(t => t.calculate && (t.calculate.includes('day(') || t.calculate.includes('week('))) &&
        vegaSpec.encoding && vegaSpec.encoding.color && !vegaSpec.encoding.x && !vegaSpec.encoding.y) {
      console.log('Fixing calendar heatmap x,y encodings');
      
      // Add x and y encodings for calendar layout
      vegaSpec.encoding.x = {
        field: 'day',
        type: 'ordinal',
        title: 'Day of Week'
      };
      vegaSpec.encoding.y = {
        field: 'week',
        type: 'ordinal',
        title: 'Week'
      }
    }

    // Fix line charts with y encoding missing field property
    if (vegaSpec.mark && (vegaSpec.mark.type === 'line' || vegaSpec.mark === 'line') &&
        vegaSpec.encoding && vegaSpec.encoding.y && !vegaSpec.encoding.y.field) {
      console.log('Fixing line chart with y encoding missing field property');
      
      // If there's a fold transform, use the value field from it
      if (vegaSpec.transform && vegaSpec.transform.some(t => t.fold)) {
        const foldTransform = vegaSpec.transform.find(t => t.fold);
        const valueField = foldTransform?.as?.[1] || 'value';
        vegaSpec.encoding.y.field = valueField;
        console.log(`Added y field from fold transform: "${valueField}"`);
      } else if (vegaSpec.transform && vegaSpec.transform.some(t => t.calculate && t.as === 'y')) {
        // If there's a calculated field 'y', use that
        vegaSpec.encoding.y.field = 'y';
        console.log('Added y field from calculated field: "y"');
      }
    }

    // Fix area charts with fold transforms missing y-axis encoding
    if (vegaSpec.mark && (vegaSpec.mark.type === 'area' || vegaSpec.mark === 'area') &&
        vegaSpec.transform && vegaSpec.transform.some(t => t.fold) &&
        vegaSpec.encoding && vegaSpec.encoding.x && !vegaSpec.encoding.y) {
      console.log('Fixing area chart with fold transform missing y-axis encoding');
      
      // Find the fold transform to get the correct field name for the y-axis
      const foldTransform = vegaSpec.transform.find(t => t.fold);
      const yFieldName = foldTransform?.as?.[1] || 'value'; // Default to 'value' if not specified
      
      vegaSpec.encoding.y = {
        field: yFieldName,
        type: 'quantitative',
        title: yFieldName.charAt(0).toUpperCase() + yFieldName.slice(1)
      };
      
      // Also add color encoding to distinguish the different areas
      if (!vegaSpec.encoding.color) {
        const colorFieldName = foldTransform?.as?.[0] || 'key'; // Default to 'key' if not specified
        vegaSpec.encoding.color = {
          field: colorFieldName,
    // Fix bar charts with fold transforms missing y-axis encoding for the 'value' field
    if (vegaSpec.mark && (vegaSpec.mark.type === 'bar' || vegaSpec.mark === 'bar') &&
        vegaSpec.transform && vegaSpec.transform.some(t => t.fold) &&
        vegaSpec.encoding && vegaSpec.encoding.x && (!vegaSpec.encoding.y || !vegaSpec.encoding.y.field)) {
      console.log('Fixing bar chart with fold transform missing y-axis encoding for value field');
      
      // Find the fold transform to get the correct field name for the y-axis
      const foldTransform = vegaSpec.transform.find(t => t.fold);
      const yFieldName = foldTransform?.as?.[1] || 'value'; // Use the second field from fold's 'as' array
      
      // Create or update y encoding
      if (!vegaSpec.encoding.y) {
        vegaSpec.encoding.y = {};
      }
      vegaSpec.encoding.y = {
        ...vegaSpec.encoding.y,
        field: yFieldName,
        type: 'quantitative',
        title: yFieldName.charAt(0).toUpperCase() + yFieldName.slice(1)
      };
      
      // Also add color encoding to distinguish the different characters
      if (!vegaSpec.encoding.color) {
        const colorFieldName = foldTransform?.as?.[0] || 'key'; // Use the first field from fold's 'as' array
        vegaSpec.encoding.color = {
          field: colorFieldName,
          type: 'nominal',
          title: colorFieldName.charAt(0).toUpperCase() + colorFieldName.slice(1)
        };
      }
    }
      // Find the fold transform to get the correct field name for the y-axis
      const foldTransform = vegaSpec.transform.find(t => t.fold);
      const yFieldName = foldTransform?.as?.[1] || 'value'; // Default to 'value' if not specified
      
      vegaSpec.encoding.y = {
        field: yFieldName,
        type: 'quantitative',
        title: yFieldName.charAt(0).toUpperCase() + yFieldName.slice(1)
      };
      
      // Also add color encoding to distinguish the different lines
      if (!vegaSpec.encoding.color) {
        const colorFieldName = foldTransform?.as?.[0] || 'key'; // Default to 'key' if not specified
        vegaSpec.encoding.color = {
          field: colorFieldName,
          type: 'nominal',
          title: colorFieldName.charAt(0).toUpperCase() + colorFieldName.slice(1)
        };
      }
    }

    // Fix line charts with fold transforms missing y-axis encoding for the folded value field
    if (vegaSpec.mark && (vegaSpec.mark.type === 'line' || vegaSpec.mark === 'line') &&
        vegaSpec.transform && vegaSpec.transform.some(t => t.fold) &&
        vegaSpec.encoding && vegaSpec.encoding.detail && !vegaSpec.encoding.x && !vegaSpec.encoding.y) {
      console.log('Fixing parallel coordinates chart x,y encodings');
      
      // Add x and y encodings for parallel coordinates
      vegaSpec.encoding.x = {
        field: 'dimension',
        type: 'nominal',
        title: 'Dimensions'
      };
      vegaSpec.encoding.y = {
        field: 'value',
        type: 'quantitative',
        title: 'Value'
      };
    }

    // Fix isotope/dot plot charts missing x,y encodings
    if (vegaSpec.mark && (vegaSpec.mark.type === 'circle' || vegaSpec.mark === 'circle') &&
        vegaSpec.transform && vegaSpec.transform.some(t => t.sequence) &&
        vegaSpec.encoding && !vegaSpec.encoding.x && !vegaSpec.encoding.y) {
      console.log('Fixing isotope chart x,y encodings');
      
      // Add x and y encodings using calculated positions
      vegaSpec.encoding.x = {
        field: 'x',
        type: 'ordinal',
        axis: null  // Hide axis for cleaner look
      };
      vegaSpec.encoding.y = {
        field: 'y',
        type: 'ordinal',
        axis: null  // Hide axis for cleaner look
      };
    }

    // Fix waterfall charts missing y-axis encoding
    if (vegaSpec.mark === 'bar' && vegaSpec.transform && 
        vegaSpec.transform.some(t => t.window && t.window.some(w => w.op === 'sum')) &&
        vegaSpec.encoding && vegaSpec.encoding.x && !vegaSpec.encoding.y) {
      console.log('Fixing waterfall chart y-axis encoding');
      
      // Add y and y2 encoding for proper waterfall display
      vegaSpec.encoding.y = {
        field: 'previous_sum',
        type: 'quantitative',
        title: 'Value'
      };
      vegaSpec.encoding.y2 = {
        field: 'sum',
        type: 'quantitative'
      };
    }

    // Fix for bar charts missing y-axis encoding (common issue with flow/journey charts)
    if (vegaSpec.mark && (vegaSpec.mark === 'bar' || vegaSpec.mark.type === 'bar') && 
        vegaSpec.encoding && vegaSpec.encoding.x && !vegaSpec.encoding.y) {
      console.log('Fixing bar chart missing y-axis encoding');
      
      // Check if we have a calculated flow field or similar categorical field
      const flowField = vegaSpec.transform?.find(t => t.calculate && t.as)?.as;
      const categoricalFields = ['flow', 'source', 'target', 'category', 'group'];
      
      let yField = flowField;
      if (!yField) {
        // Look for a suitable categorical field in the data
        if (vegaSpec.data?.values && vegaSpec.data.values.length > 0) {
          const firstRow = vegaSpec.data.values[0];
          yField = categoricalFields.find(field => firstRow.hasOwnProperty(field));
        }
      }
      
      if (yField) {
        vegaSpec.encoding.y = {
          field: yField,
          type: 'nominal',
          title: yField.charAt(0).toUpperCase() + yField.slice(1)
        };
        console.log(`Added y-axis encoding with field: ${yField}`);
      }
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

    // Fix for top-level facet with transforms - transforms should be in spec
    if (vegaSpec.facet && vegaSpec.transform && !vegaSpec.spec) {
      console.log("Found top-level facet with transforms, moving transforms to spec");
      console.log("Original spec:", JSON.stringify(vegaSpec, null, 2));

      // Extract facet configuration and transforms
      const { facet, transform, layer, mark, encoding, ...otherProps } = vegaSpec;

      // Create the corrected spec structure
      const correctedSpec = {
        ...otherProps, // Preserve $schema, data, description, etc.
        facet: facet,
        spec: {
          transform: transform,
          ...(layer && { layer }),
          ...(mark && { mark }),
          ...(encoding && { encoding })
        }
      };

      vegaSpec = correctedSpec;
      console.log("Corrected facet specification structure:");
      console.log("New spec:", JSON.stringify(vegaSpec, null, 2));
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

    // Pre-render legend optimization - configure legends to wrap based on estimated size
    const optimizeLegendLayout = (spec: any) => {
      const chartHeight = spec.height || 400;
      const maxLegendHeight = chartHeight * 0.6; // Legend shouldn't exceed 60% of chart height
      const estimatedItemHeight = 20; // Approximate height per legend item including padding
      const maxItemsPerColumn = Math.floor(maxLegendHeight / estimatedItemHeight);
      
      console.log(`Legend optimization: Chart height=${chartHeight}, Max legend height=${maxLegendHeight}, Max items per column=${maxItemsPerColumn}`);
      
      // Helper to count unique values in a field
      const countUniqueValues = (fieldName: string, data: any[]): number => {
        if (!data || !Array.isArray(data)) return 0;
        const uniqueValues = new Set();
        data.forEach((row: any) => {
          if (row[fieldName] !== undefined && row[fieldName] !== null) {
            uniqueValues.add(row[fieldName]);
          }
        });
        return uniqueValues.size;
      };
      
      // Helper to apply legend column wrapping
      const applyLegendWrapping = (encoding: any, channel: string, data: any[]) => {
        if (!encoding[channel] || !encoding[channel].field) return;
        
        const fieldName = encoding[channel].field;
        const uniqueCount = countUniqueValues(fieldName, data);
        
        if (uniqueCount > maxItemsPerColumn) {
          const neededColumns = Math.ceil(uniqueCount / maxItemsPerColumn);
          const columns = Math.min(neededColumns, 3); // Cap at 3 columns to prevent horizontal overflow
          
          console.log(`Applying legend wrapping to ${channel} field "${fieldName}": ${uniqueCount} items -> ${columns} columns`);
          
          if (!encoding[channel].legend) {
            encoding[channel].legend = {};
          }
          
          encoding[channel].legend = {
            ...encoding[channel].legend,
            columns: columns,
            symbolLimit: 0,
            labelLimit: 80, // Shorter labels to fit multiple columns
            titleLimit: 100,
            orient: 'bottom', // Move to bottom to avoid vertical overflow
            offset: 5,
            padding: 3,
            rowPadding: 2,
            columnPadding: 6
          };
        }
      };
      
      // Get the main data source
      const mainData = spec.data?.values || [];
      
      // Apply to main spec encoding
      if (spec.encoding) {
        ['color', 'fill', 'stroke', 'shape', 'size', 'opacity'].forEach(channel => {
          applyLegendWrapping(spec.encoding, channel, mainData);
        });
      }
      
      // Apply to layer encodings
      if (spec.layer && Array.isArray(spec.layer)) {
        spec.layer.forEach((layer: any, layerIndex: number) => {
          if (layer.encoding) {
            // Use layer-specific data if available, otherwise use main data
            const layerData = layer.data?.values || mainData;
            ['color', 'fill', 'stroke', 'shape', 'size', 'opacity'].forEach(channel => {
              applyLegendWrapping(layer.encoding, channel, layerData);
            });
          }
        });
      }
    };
    
    // Apply legend optimization before rendering
    optimizeLegendLayout(vegaSpec);

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
    }

    // Final validation: ensure the spec is still valid after preprocessing
    if (vegaSpec.encoding && Object.keys(vegaSpec.encoding).length === 0) {
      throw new Error('Invalid Vega-Lite specification: all encoding channels were invalid and removed');
    }

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
    renderContainer.style.maxWidth = '100%';
    renderContainer.style.overflow = 'hidden';
    renderContainer.style.boxSizing = 'border-box';
    container.appendChild(renderContainer);

    const sanitizedSpec = vegaSpec;

    // Render the visualization
    // Deep clone and sanitize the spec one last time to be safe by serializing and parsing.
    // This removes any non-plain-object properties that might be causing issues.
    const finalSpec = JSON.parse(JSON.stringify(sanitizedSpec));

    console.log('Vega-Lite: About to call vegaEmbed with finalSpec:', finalSpec);

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

    console.log('Vega-Lite: vegaEmbed completed successfully:', result);

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
    // Position the buttons in the top-right corner, 12px higher than default
    actionsContainer.style.cssText = `
        position: absolute;
        top: -4px;
        right: 8px;
        z-index: 1000;
        opacity: 0;
        transition: opacity 0.2s ease-in-out;
      `;
    // The diagram-actions class from index.css will handle additional styling

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
    const originalVegaContainer = vegaContainer;
    const sourceButton = document.createElement('button');
    sourceButton.innerHTML = showingSource ? '🎨 View' : '📝 Source';
    sourceButton.className = 'diagram-action-button vega-lite-source-button';
    sourceButton.onclick = () => {
      showingSource = !showingSource;
      sourceButton.innerHTML = showingSource ? '🎨 View' : '📝 Source';

      if (showingSource) {
        // Hide the vega container and show source
        if (originalVegaContainer) {
          vegaContainer.style.display = 'none';
        }

        // Clear container and add source view
        const sourceView = document.createElement('pre');
        sourceView.style.cssText = `
            background-color: ${isDarkMode ? '#1f1f1f' : '#f6f8fa'};
            padding: 16px;
            border-radius: 4px;
            overflow: auto;
            width: 100%;
            height: auto;
            max-height: 80vh;
            margin: 0;
            box-sizing: border-box;
            color: ${isDarkMode ? '#e6e6e6' : '#24292e'};
        `;
        sourceView.innerHTML = `<code>${JSON.stringify(vegaSpec, null, 2)}</code>`;
        
        container.innerHTML = '';
        container.appendChild(sourceView);
        
        // Force parent container to accommodate the source view
        let parent = container.parentElement;
        while (parent && (parent.classList.contains('d3-container') || parent.classList.contains('vega-lite-renderer-container'))) {
          const parentElement = parent as HTMLElement;
          parentElement.style.height = 'auto';
          parentElement.style.minHeight = 'auto';
          parentElement.style.overflow = 'auto';
          parent = parent.parentElement;
        }

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
        
        // Restore parent container sizing for visualization
        let parent = container.parentElement;
        while (parent && (parent.classList.contains('d3-container') || parent.classList.contains('vega-lite-renderer-container'))) {
          const parentElement = parent as HTMLElement;
          // Let the visualization determine its own height again
          parentElement.style.height = '';
          parent = parent.parentElement;
        }
      };
    };
    actionsContainer.appendChild(sourceButton);

    // Force the container to have position: relative
    container.style.position = 'relative';

    // Insert actions container at the beginning of the container
    container.insertBefore(actionsContainer, container.firstChild);

    // Ensure the actions container is visible on hover
    container.addEventListener('mouseenter', () => actionsContainer.style.opacity = '1');
    container.addEventListener('mouseleave', () => actionsContainer.style.opacity = '0');

    // Post-render fixes for sizing issues
    setTimeout(() => {
      const svgElement = container.querySelector('svg');
      const vegaEmbedDiv = container.querySelector('.vega-embed') as HTMLElement;

      // Fix SVG scaling when content is smaller than container
      const svg = container.querySelector('svg');
      const embedDiv = container.querySelector('.vega-embed') as HTMLElement;

      if (svg && embedDiv) {
        const containerRect = embedDiv.getBoundingClientRect();
        const svgRect = svg.getBoundingClientRect();
        
        // Only scale if SVG is significantly smaller than container
        const scaleX = containerRect.width / svgRect.width;
        const scaleY = containerRect.height / svgRect.height;
        const scale = Math.min(scaleX, scaleY);
        
        if (scale > 1.2) { // Only scale if there's significant wasted space
          const finalScale = Math.min(scale, 2.5); // Cap scaling
          svg.style.transform = `scale(${finalScale})`;
          svg.style.transformOrigin = 'center center';
          console.log(`Scaled SVG by ${finalScale}x to reduce wasted space`);
        }
      }

      // Fix SVG to fill vega-embed container properly
      const svgEl = container.querySelector('svg');
      const embedContainer = container.querySelector('.vega-embed') as HTMLElement;

      if (svgEl && embedContainer) {
        // Make SVG fill the container
        svgEl.style.width = '100%';
        svgEl.style.height = '100%';
        svgEl.removeAttribute('width');
        svgEl.removeAttribute('height');
        svgEl.setAttribute('preserveAspectRatio', 'xMidYMid meet');
        
        console.log('Made SVG responsive to fill vega-embed container');
      }

      // Critical fix: Ensure vega-embed div doesn't exceed parent width
      if (vegaEmbedDiv) {
        vegaEmbedDiv.style.maxWidth = '100%';
        vegaEmbedDiv.style.overflow = 'hidden';
      }

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
          while (parent) {
            if (parent.classList.contains('vega-lite-renderer-container')) {
              (parent as HTMLElement).style.height = 'auto';
              (parent as HTMLElement).style.minHeight = `${svgHeight}px`;
              (parent as HTMLElement).style.overflow = 'visible';
              console.log('Updated Vega-Lite parent container:', parent.className, 'to height: auto, minHeight:', svgHeight);
              break; // Only modify our own container
            }
            parent = parent.parentElement;
          }
        }

        // For complex layouts, ensure proper scaling
        if (vegaSpec.vconcat || vegaSpec.hconcat || vegaSpec.facet) {
          svgElement.setAttribute('preserveAspectRatio', 'xMidYMid meet');

          // Force a reflow to ensure proper sizing
          setTimeout(() => {
            container.style.display = 'none';
            void container.offsetHeight; // Trigger reflow
            container.style.display = '';
          }, 100);
        }
      
      console.log('Vega-Lite visualization rendered successfully');
    }
    }, 100);
  } catch (error) {
  console.error('Vega-Lite rendering error:', error);

  // Log streaming state during error for debugging
  console.log('Vega-Lite error context:', {
    isStreaming: spec.isStreaming,
    isMarkdownBlockClosed: spec.isMarkdownBlockClosed,
    forceRender: spec.forceRender
  });

  // Check if this looks like a streaming/incomplete JSON error
  const isStreamingError = (error instanceof Error) && (
    error.message.includes('Unterminated string') ||
    error.message.includes('Unexpected end of JSON input') ||
    error.message.includes('Unexpected token')
  );
  
  // Separate check for incomplete definition (only relevant if we're actually streaming)
  const isIncompleteDefinition = spec.definition && !isVegaLiteDefinitionComplete(spec.definition);

  // During streaming or with incomplete JSON, don't show errors unless forced
  // CRITICAL: Don't suppress errors when forceRender is true - user explicitly wants to see what's wrong
  const shouldSuppressError = (
    !spec.forceRender && (
      (spec.isStreaming && !spec.isMarkdownBlockClosed) ||
      (spec.isStreaming && isIncompleteDefinition)
    ) ||
    (!spec.definition || spec.definition.trim().length === 0) ||
    (isStreamingError && !spec.forceRender)
  );

  if (shouldSuppressError) {
    console.debug('Suppressing Vega-Lite streaming error:', error instanceof Error ? error.message : String(error));
    // Show waiting message instead of error
    const suppressedErrorContainer = document.createElement('div');
    suppressedErrorContainer.style.cssText = `
      text-align: center; 
      padding: 20px; 
      background-color: ${isDarkMode ? '#1f1f1f' : '#f6f8fa'}; 
      border: 1px dashed #ccc; 
      border-radius: 4px;
      position: relative;
    `;
    
    suppressedErrorContainer.innerHTML = `
      <p>Waiting for complete Vega-Lite specification...</p>
      <div style="margin-top: 15px;">
        <button class="vega-lite-force-retry-btn" style="
          background-color: #dc3545;
          color: white;
          border: none;
          border-radius: 4px;
          padding: 8px 16px;
          margin: 0 5px;
          cursor: pointer;
          font-size: 14px;
        ">🔄 Force Render Anyway</button>
        <button id="vega-lite-debug-source-${Date.now()}" style="
          background-color: #6c757d;
          color: white;
          border: none;
          border-radius: 4px;
          padding: 8px 16px;
          margin: 0 5px;
          cursor: pointer;
          font-size: 14px;
        ">🔍 Debug Source</button>
      </div>
      <div style="margin-top: 10px; font-size: 12px; color: ${isDarkMode ? '#8b949e' : '#656d76'};">
        Error: ${error instanceof Error ? error.message : String(error)}
      </div>
    `;
    
    container.innerHTML = '';
    container.appendChild(suppressedErrorContainer);
    
    // Add event listeners
    const forceRetryId = suppressedErrorContainer.querySelector('[id^="vega-lite-force-retry-"]')?.id;
    const debugSourceId = suppressedErrorContainer.querySelector('[id^="vega-lite-debug-source-"]')?.id;
    
    if (forceRetryId) {
      const forceRetryButton = document.getElementById(forceRetryId);
      if (forceRetryButton) {
        forceRetryButton.onclick = () => {
          console.log('Force rendering Vega-Lite despite streaming error');
          const forceSpec = { ...spec, forceRender: true };
          vegaLitePlugin.render(container, d3, forceSpec, isDarkMode);
        };
      }
    }
    
    if (debugSourceId) {
      const debugSourceButton = document.getElementById(debugSourceId);
      if (debugSourceButton) {
        debugSourceButton.onclick = () => {
          showVegaLiteDebugView(container, spec, isDarkMode, error instanceof Error ? error : new Error(String(error)));
        };
      }
    }
    
    return;
  }

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

  // Create error container with proper sizing
  showVegaLiteErrorView(container, spec, isDarkMode, error instanceof Error ? error : new Error(String(error)));

  // Apply the same container sizing logic used for successful renders
  setTimeout(() => {
    const errorDiv = container.querySelector('.vega-lite-error, .vega-lite-debug') as HTMLElement;
    if (errorDiv) {
      const errorRect = errorDiv.getBoundingClientRect();
      console.log('Error container size:', errorRect);

      // Force parent containers to accommodate the error content
      let parent = container.parentElement;
      while (parent && (parent.classList.contains('d3-container') || parent.classList.contains('vega-lite-renderer-container'))) {
        const parentElement = parent as HTMLElement;

        // Only modify Vega-Lite containers, not all d3-containers
        if (parentElement.classList.contains('vega-lite-renderer-container')) {
          // Force auto height and visible overflow for error display
          parentElement.style.height = 'auto';
          parentElement.style.minHeight = `${errorRect.height + 40}px`;
          parentElement.style.overflow = 'visible';

          // Also ensure the container can grow
          if (parentElement.style.maxHeight) {
            parentElement.style.maxHeight = 'none';
          }
          console.log(`Updated parent ${parentElement.className} height to ${errorRect.height + 40}px for error display`);
        }
        parent = parent.parentElement;
      }
    }
  }, 100);
}
}
};

/**
 * Show enhanced error view with debugging options similar to Mermaid
 */
function showVegaLiteErrorView(container: HTMLElement, spec: VegaLiteSpec, isDarkMode: boolean, error: Error): void {
  const sourceDefinition = spec.definition || JSON.stringify(spec, null, 2);
  const isCompleteVegaLiteObject = spec.$schema && (spec.data || spec.datasets) &&
    (spec.mark || spec.layer || spec.vconcat || spec.hconcat || spec.facet || spec.repeat);

  container.innerHTML = `
    <div class="vega-lite-error" style="
      padding: 16px;
      margin: 16px 0;
      border-radius: 6px;
      background-color: ${isDarkMode ? '#2a1f1f' : '#fff2f0'};
      border: 1px solid ${isDarkMode ? '#a61d24' : '#ffa39e'};
      color: ${isDarkMode ? '#ff7875' : '#cf1322'};
    ">
      <div style="margin-bottom: 15px;">
        <strong>Vega-Lite Rendering Error:</strong>
        <p>${error.message || 'Unknown error'}</p>
      </div>
      
      <div style="margin-bottom: 15px;">
        <button class="vega-lite-retry-error-btn" style="
          background-color: #28a745;
          color: white;
          border: none;
          border-radius: 4px;
          padding: 8px 16px;
          margin: 0 5px;
          cursor: pointer;
          font-size: 14px;
        ">🔄 Retry Rendering</button>
        <button class="vega-lite-show-source-btn" style="
          background-color: #007bff;
          color: white;
          border: none;
          border-radius: 4px;
          padding: 8px 16px;
          margin: 0 5px;
          cursor: pointer;
          font-size: 14px;
        ">📝 Show Source</button>
        <button class="vega-lite-force-render-btn" style="
          background-color: #dc3545;
          color: white;
          border: none;
          border-radius: 4px;
          padding: 8px 16px;
          margin: 0 5px;
          cursor: pointer;
          font-size: 14px;
        ">⚡ Force Render</button>
      </div>
      
      <div style="font-size: 13px; color: ${isDarkMode ? '#8b949e' : '#656d76'};">
        <strong>Debug Info:</strong><br>
        • Streaming: ${spec.isStreaming ? 'Yes' : 'No'}<br>
        • Block Closed: ${spec.isMarkdownBlockClosed ? 'Yes' : 'No'}<br>
        • Definition Length: ${sourceDefinition.length} characters<br>
        • Complete Object: ${isCompleteVegaLiteObject ? 'Yes' : 'No'}<br>
        • Error Type: ${error.constructor.name}
      </div>
    </div>
  `;

  // Add event listeners
  const retryButton = container.querySelector('.vega-lite-retry-error-btn') as HTMLButtonElement;
  const sourceButton = container.querySelector('.vega-lite-show-source-btn') as HTMLButtonElement;
  const forceButton = container.querySelector('.vega-lite-force-render-btn') as HTMLButtonElement;

  if (retryButton) {
    retryButton.onclick = () => vegaLitePlugin.render(container, null, spec, isDarkMode);
  }

  if (sourceButton) {
    sourceButton.onclick = () => showVegaLiteDebugView(container, spec, isDarkMode, error);
  }

  if (forceButton) {
    forceButton.onclick = () => {
      const forceSpec = { ...spec, forceRender: true };
      vegaLitePlugin.render(container, null, forceSpec, isDarkMode);
    };
  }
}

/**
 * Show debug view with source code and detailed information
 */
function showVegaLiteDebugView(container: HTMLElement, spec: VegaLiteSpec, isDarkMode: boolean, error?: Error): void {
  const sourceDefinition = spec.definition || JSON.stringify(spec, null, 2);
  const isCompleteVegaLiteObject = spec.$schema && (spec.data || spec.datasets) &&
    (spec.mark || spec.layer || spec.vconcat || spec.hconcat || spec.facet || spec.repeat);

  container.innerHTML = `
    <div class="vega-lite-debug" style="
      background-color: ${isDarkMode ? '#1f1f1f' : '#f6f8fa'};
      border: 1px solid ${isDarkMode ? '#444' : '#e1e4e8'};
      border-radius: 6px;
      padding: 16px;
      margin: 10px 0;
    ">
      <div style="margin-bottom: 15px; display: flex; justify-content: space-between; align-items: center;">
        <strong style="color: ${isDarkMode ? '#f8f9fa' : '#24292e'};">Vega-Lite Debug View</strong>
        <div>
          <button class="vega-lite-debug-retry-btn" style="
            background-color: #28a745;
            color: white;
            border: none;
            border-radius: 4px;
            padding: 6px 12px;
            margin-right: 8px;
            cursor: pointer;
            font-size: 13px;
          ">🔄 Retry</button>
          <button id="vega-lite-debug-force-${Date.now()}" style="
            background-color: #dc3545;
            color: white;
            border: none;
            border-radius: 4px;
            padding: 6px 12px;
            cursor: pointer;
            font-size: 13px;
          ">⚡ Force Render</button>
        </div>
      </div>
      
      ${error ? `
        <div style="
          background-color: ${isDarkMode ? '#2d1b1b' : '#ffeaea'};
          border: 1px solid ${isDarkMode ? '#a61d24' : '#ffa39e'};
          border-radius: 4px;
          padding: 12px;
          margin-bottom: 15px;
        ">
          <strong style="color: ${isDarkMode ? '#ff7875' : '#cf1322'};">Error Details:</strong><br>
          <code style="color: ${isDarkMode ? '#ffa7cc' : '#d1242f'};">${error.message}</code>
        </div>
      ` : ''}
      
      <div style="margin-bottom: 15px;">
        <strong style="color: ${isDarkMode ? '#f8f9fa' : '#24292e'};">Debug Information:</strong><br>
        <div style="font-size: 13px; color: ${isDarkMode ? '#8b949e' : '#656d76'}; margin-top: 8px;">
          • Streaming State: ${spec.isStreaming ? 'Active' : 'Inactive'}<br>
          • Markdown Block: ${spec.isMarkdownBlockClosed ? 'Closed' : 'Open'}<br>
          • Force Render: ${spec.forceRender ? 'Yes' : 'No'}<br>
          • Definition Length: ${sourceDefinition.length} characters<br>
          • Complete Object: ${isCompleteVegaLiteObject ? 'Yes' : 'No'}<br>
          • Has Schema: ${spec.$schema ? 'Yes' : 'No'}<br>
          • Has Data: ${spec.data || spec.datasets ? 'Yes' : 'No'}<br>
          • Has Mark/Layer: ${spec.mark || spec.layer ? 'Yes' : 'No'}
        </div>
      </div>
      
      <details open>
        <summary style="cursor: pointer; margin-bottom: 10px; color: ${isDarkMode ? '#f8f9fa' : '#24292e'};">
          <strong>Source Specification</strong>
        </summary>
        <pre style="
          background-color: ${isDarkMode ? '#0d1117' : '#f6f8fa'};
          padding: 12px;
          border-radius: 4px;
          overflow: auto;
          max-height: 400px;
          margin: 0;
          border: 1px solid ${isDarkMode ? '#30363d' : '#e1e4e8'};
          font-family: 'SFMono-Regular', 'Monaco', 'Inconsolata', 'Liberation Mono', 'Courier New', monospace;
          font-size: 13px;
          line-height: 1.45;
          color: ${isDarkMode ? '#e6edf3' : '#24292f'};
        "><code>${sourceDefinition}</code></pre>
      </details>
    </div>
  `;

  // Add event listeners for debug view buttons using querySelector scoped to the container
  const retryButton = container.querySelector('.vega-lite-debug-retry-btn') as HTMLButtonElement;
  const forceButton = container.querySelector('.vega-lite-debug-force-btn') as HTMLButtonElement;

  if (retryButton) {
    retryButton.onclick = () => vegaLitePlugin.render(container, null, spec, isDarkMode);
  }

  if (forceButton) {
    forceButton.onclick = () => {
      const forceSpec = { ...spec, forceRender: true };
    };
  }
  
  // Force parent containers to accommodate the debug content
  setTimeout(() => {
    const debugDiv = container.querySelector('.vega-lite-debug') as HTMLElement;
    if (debugDiv) {
      const debugRect = debugDiv.getBoundingClientRect();
      console.log('Debug container size:', debugRect);

      // Force parent containers to accommodate the debug content
      let parent = container.parentElement;
      while (parent && (parent.classList.contains('d3-container') || parent.classList.contains('vega-lite-renderer-container'))) {
        const parentElement = parent as HTMLElement;
        
        // Force auto height and visible overflow for debug display
        parentElement.style.height = 'auto';
        parentElement.style.minHeight = `${Math.max(debugRect.height + 40, 400)}px`;
        parentElement.style.overflow = 'visible';
        
        // Also ensure the container can grow
        if (parentElement.style.maxHeight) {
          parentElement.style.maxHeight = 'none';
        }
        console.log(`Updated parent ${parentElement.className} height to ${debugRect.height + 40}px for debug display`);
        parent = parent.parentElement;
      }
    }
  }, 100);
}
