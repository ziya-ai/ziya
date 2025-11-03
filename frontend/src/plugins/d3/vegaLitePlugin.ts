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
          console.log('🔧 VEGA-PREPROCESS: Removing shape encoding with missing field or data');
          shouldRemoveShape = true;
        }

        if (shouldRemoveShape) {
          console.log('🔧 VEGA-PREPROCESS: Removing problematic shape encoding');
          delete spec.encoding.shape;
        }
      }

      // Fix 1.5: Fix fold transform field name mismatches
      if (spec.transform && spec.transform.some((t: any) => t.fold) && spec.encoding) {
        const foldTransform = spec.transform.find((t: any) => t.fold);
        if (foldTransform && foldTransform.as) {
          const keyField = foldTransform.as[0] || 'key';
          const valueField = foldTransform.as[1] || 'value';

          Object.keys(spec.encoding).forEach(channel => {
            const channelSpec = spec.encoding[channel];
            if (channelSpec && channelSpec.field) {
              // Fix "value" -> actual value field name from fold transform
              if (channelSpec.field === 'value' && valueField !== 'value') {
                console.log(`🔧 FOLD-FIX: Fixed fold transform field mismatch: "value" -> "${valueField}" in ${channel} encoding`);
                channelSpec.field = valueField;
              }
              // Fix "key" -> actual key field name from fold transform
              if (channelSpec.field === 'key' && keyField !== 'key') {
                console.log(`🔧 FOLD-FIX: Fixed fold transform field mismatch: "key" -> "${keyField}" in ${channel} encoding`);
                channelSpec.field = keyField;
              }
              // Fix "dimension" -> actual key field name from fold transform (common in parallel coordinates)
              if (channelSpec.field === 'dimension' && keyField !== 'dimension') {
                console.log(`🔧 FOLD-FIX: Fixed fold transform field mismatch: "dimension" -> "${keyField}" in ${channel} encoding`);
                channelSpec.field = keyField;
              }
            }
          });
        }
      }

      // Fix 1.75: Convert boolean values to strings in data
      if (spec.data?.values && Array.isArray(spec.data.values)) {
        spec.data.values = spec.data.values.map((row: any) => {
          const newRow = { ...row };
          Object.keys(newRow).forEach(key => {
            if (typeof newRow[key] === 'boolean') {
              newRow[key] = newRow[key] ? 'Yes' : 'No';
            }
          });
          return newRow;
        });
        console.log('🔧 VEGA-PREPROCESS: Converted boolean values to strings');
      }

      // Fix 1.8: Convert boolean values in hconcat/vconcat/layer specs
      const processNestedSpecs = (specs: any[]) => {
        specs.forEach((nestedSpec: any) => {
          if (nestedSpec.data?.values && Array.isArray(nestedSpec.data.values)) {
            nestedSpec.data.values = nestedSpec.data.values.map((row: any) => {
              const newRow = { ...row };
              Object.keys(newRow).forEach(key => {
                if (typeof newRow[key] === 'boolean') {
                  newRow[key] = newRow[key] ? 'Yes' : 'No';
                }
              });
              return newRow;
            });
          }
        });
      };

      if (spec.hconcat) processNestedSpecs(spec.hconcat);
      if (spec.vconcat) processNestedSpecs(spec.vconcat);
      if (spec.layer) processNestedSpecs(spec.layer);

      // Fix 1.9: Convert deprecated 'ordinal' type to 'nominal'
      const convertOrdinalToNominal = (encoding: any) => {
        if (!encoding) return;
        Object.keys(encoding).forEach(channel => {
          const channelSpec = encoding[channel];
          if (Array.isArray(channelSpec)) {
            channelSpec.forEach(c => {
              if (c?.type === 'ordinal') {
                c.type = 'nominal';
                console.log(`🔧 VEGA-PREPROCESS: Converted ${channel} type from ordinal to nominal`);
              }
            });
          } else if (channelSpec?.type === 'ordinal') {
            channelSpec.type = 'nominal';
            console.log(`🔧 VEGA-PREPROCESS: Converted ${channel} type from ordinal to nominal`);
          }
        });
      };

      convertOrdinalToNominal(spec.encoding);
      if (spec.hconcat) spec.hconcat.forEach((s: any) => convertOrdinalToNominal(s.encoding));
      if (spec.vconcat) spec.vconcat.forEach((s: any) => convertOrdinalToNominal(s.encoding));
      if (spec.layer) spec.layer.forEach((s: any) => convertOrdinalToNominal(s.encoding));

      // Fix 1.91: Fix color legends showing hex codes instead of meaningful labels
      const fixColorLegendLabels = (encoding: any, dataValues: any[]) => {
        if (!encoding || !dataValues || dataValues.length === 0) return;

        ['color', 'fill', 'stroke'].forEach(channel => {
          const channelSpec = encoding[channel];
          if (channelSpec?.field && channelSpec?.type === 'nominal') {
            // Check if the field contains literal color values (hex codes)
            const fieldValues = [...new Set(dataValues.map(d => d[channelSpec.field]))].filter(v => v !== null && v !== undefined);
            const hasHexColors = fieldValues.some(value =>
              typeof value === 'string' && /^#[0-9A-Fa-f]{6}$/.test(value)
            );

            if (hasHexColors) {
              console.log(`🔧 LEGEND-LABEL-FIX: Color field "${channelSpec.field}" contains hex codes, switching to meaningful field for legend`);

              // Find a more meaningful field for legend labels
              const meaningfulFields = ['rating', 'label', 'name', 'category', 'type', 'status', 'group'];
              const labelField = meaningfulFields.find(field =>
                dataValues.length > 0 && dataValues[0].hasOwnProperty(field)
              );

              if (labelField) {
                // Create mapping from meaningful label to color
                const labelToColor = new Map();
                dataValues.forEach(d => {
                  if (d[channelSpec.field] && d[labelField]) {
                    labelToColor.set(d[labelField], d[channelSpec.field]);
                  }
                });

                // Get unique labels and their corresponding colors
                const uniqueLabels = [...new Set(dataValues.map(d => d[labelField]).filter(v => v !== null && v !== undefined))];
                const colorRange = uniqueLabels.map(label => labelToColor.get(label));

                // Update encoding to use the meaningful field with proper color mapping
                channelSpec.field = labelField;
                channelSpec.scale = {
                  domain: uniqueLabels,
                  range: colorRange,
                  type: 'ordinal'
                };

                console.log(`🔧 LEGEND-LABEL-FIX: Switched to meaningful field "${labelField}" with color mapping:`, 
                  uniqueLabels.map((label, i) => `${label} -> ${colorRange[i]}`));
              }
            }
          }
        });
      };

      if (spec.data?.values) {
        fixColorLegendLabels(spec.encoding, spec.data.values);
      }

      // Fix literal color values being used as field references
      const fixLiteralColorFields = (encoding: any, dataValues: any[]) => {
        if (!encoding || !dataValues || dataValues.length === 0) return;

        ['color', 'fill', 'stroke'].forEach(channel => {
          const channelSpec = encoding[channel];
          if (channelSpec?.field && channelSpec?.type === 'nominal') {
            // Check if the field contains literal color values (hex codes)
            const fieldValues = [...new Set(dataValues.map(d => d[channelSpec.field]))].filter(v => v !== null && v !== undefined);
            const hasHexColors = fieldValues.some(value =>
              typeof value === 'string' && /^#[0-9A-Fa-f]{6}$/.test(value)
            );

            if (hasHexColors) {
              console.log(`🔧 LITERAL-COLOR-FIX: Converting literal color field "${channelSpec.field}" in ${channel} channel`);

              // Create a proper scale mapping the field values to themselves as colors
              channelSpec.scale = {
                domain: fieldValues,
                range: fieldValues, // Use the hex values as the actual colors
                type: 'ordinal'
              };

              // Update legend to show meaningful labels if possible
              if (channelSpec.legend === undefined) {
                // Try to find a more meaningful field for legend labels
                const meaningfulFields = ['signal', 'type', 'category', 'label', 'name'];
                const labelField = meaningfulFields.find(field =>
                  dataValues.length > 0 && dataValues[0].hasOwnProperty(field)
                );

                if (labelField) {
                  // Create mapping from color to meaningful label
                  const colorToLabel = new Map();
                  dataValues.forEach(d => {
                    if (d[channelSpec.field] && d[labelField]) {
                      colorToLabel.set(d[channelSpec.field], d[labelField]);
                    }
                  });

                  // Update domain and range to use meaningful labels
                  const uniqueLabels = [...new Set(Array.from(colorToLabel.values()))];
                  const labelToColor = new Map();
                  colorToLabel.forEach((label, color) => {
                    labelToColor.set(label, color);
                  });

                  channelSpec.scale.domain = uniqueLabels;
                  channelSpec.scale.range = uniqueLabels.map(label => labelToColor.get(label));
                  channelSpec.field = labelField;

                  console.log(`🔧 LITERAL-COLOR-FIX: Switched to meaningful field "${labelField}" with proper color mapping`);
                }
              }
            }
          }
        });
      };

      // Fix grid-based layouts with overlapping elements and poor axis labeling
      const fixGridLayoutIssues = (spec: any) => {
        if (!spec.layer || !Array.isArray(spec.layer) || !spec.data?.values) return;

        const dataValues = spec.data.values;

        // Check if this looks like a grid layout (has x,y coordinates and multiple layers)
        const hasGridCoordinates = dataValues.every(d =>
          typeof d.x === 'number' && typeof d.y === 'number'
        );

        if (!hasGridCoordinates) return;

        console.log('🔧 GRID-LAYOUT-FIX: Detected grid-based layout with overlapping elements');

        // Check for layers that use the same x/y fields but with different ranges (causing overlap)
        const hasOverlappingRanges = spec.layer.some((layer, i) => {
          if (!layer.encoding?.x?.field || !layer.encoding?.y?.field) return false;

          return spec.layer.some((otherLayer, j) => {
            if (i >= j || !otherLayer.encoding?.x?.field || !otherLayer.encoding?.y?.field) return false;

            const sameFields = layer.encoding.x.field === otherLayer.encoding.x.field &&
              layer.encoding.y.field === otherLayer.encoding.y.field;
            const differentRanges = JSON.stringify(layer.encoding.x.scale?.range) !==
              JSON.stringify(otherLayer.encoding.x.scale?.range);

            return sameFields && differentRanges;
          });
        });

        if (hasOverlappingRanges) {
          console.log('🔧 GRID-LAYOUT-FIX: Found overlapping ranges, standardizing positioning');

          // Get unique x and y values to determine grid dimensions
          const uniqueX = [...new Set(dataValues.map(d => d.x))].sort((a, b) => (a as number) - (b as number));
          const uniqueY = [...new Set(dataValues.map(d => d.y))].sort((a, b) => (a as number) - (b as number));

          // Find the best fields to use for meaningful axis labels (generic approach)
          const availableFields = Object.keys(dataValues[0] || {});
          const xLabelField = availableFields.find(field =>
            field !== 'x' && field !== 'y' && field !== 'color' &&
            dataValues.every(d => d.x !== undefined && d[field] !== undefined)
          ) || 'x';
          const yLabelField = availableFields.find(field =>
            field !== 'x' && field !== 'y' && field !== 'color' && field !== xLabelField &&
            dataValues.every(d => d.y !== undefined && d[field] !== undefined)
          ) || 'y';

          console.log('🔧 GRID-LAYOUT-FIX: Grid dimensions:', { uniqueX, uniqueY, xLabelField, yLabelField });

          // Calculate proper spacing for grid
          const cellWidth = Math.max(100, (spec.width || 600) / (uniqueX.length + 1));
          const cellHeight = Math.max(60, (spec.height || 400) / (uniqueY.length + 1));

          // Standardize all layers to use the same consistent positioning
          spec.layer.forEach((layer, index) => {
            // Handle rectangle layer (usually first layer with fill encoding)
            if (layer.encoding?.x?.field === 'x' && layer.encoding?.fill) {
              // Fix mark sizing to ensure consistent rectangle dimensions
              // Remove explicit width/height and let ordinal bands handle sizing
              const { width, height, ...markProps } = layer.mark;
              layer.mark = {
                ...markProps,
                type: 'rect'
              };

              console.log('🔧 GRID-LAYOUT-FIX: Removed explicit width/height from rect mark to use ordinal bands');
            }

            // Update encoding for any layer using x/y coordinates
            if (layer.encoding?.x?.field === 'x') {

              layer.encoding.x = {
                field: xLabelField,
                type: 'ordinal',
                axis: index === 0 ? {
                  title: xLabelField.charAt(0).toUpperCase() + xLabelField.slice(1).replace('_', ' '),
                  labelAngle: -45,
                  labelLimit: 120,
                  labelPadding: 10,
                  titlePadding: 15,
                  offset: 5,
                  grid: false,
                  ticks: true,
                  labelFontSize: 10
                } : {
                  labels: false, ticks: false, title: null, grid: false
                }
              };
              layer.encoding.y = {
                field: yLabelField,
                type: 'ordinal',
                axis: index === 0 ? {
                  title: yLabelField.charAt(0).toUpperCase() + yLabelField.slice(1).replace('_', ' '),
                  labelAngle: 0,
                  labelPadding: 10,
                  titlePadding: 15,
                  offset: 5,
                  grid: false,
                  ticks: true,
                  labelFontSize: 11
                } : {
                  labels: false, ticks: false, title: null, grid: false
                }
              };
            }
            // Handle text layers - remove them entirely as they'll be redundant with proper axis labels
            if (layer.mark?.type === 'text') {
              // Check if this text layer shows symbols or other meaningful data
              const textField = layer.encoding?.text?.field;
              if (textField && textField !== xLabelField && textField !== yLabelField) {
                // This is a symbol/data text layer - preserve it but fix its positioning
                console.log('🔧 GRID-LAYOUT-FIX: Preserving symbol text layer', index, 'field:', textField);

                // Update positioning to match the rectangle layer
                layer.encoding.x = {
                  field: xLabelField,
                  type: 'ordinal',
                  axis: null // No axis for text layer
                };
                layer.encoding.y = {
                  field: yLabelField,
                  type: 'ordinal',
                  axis: null // No axis for text layer
                };

                // Ensure text is visible with proper styling
                if (!layer.mark.color) {
                  layer.mark.color = 'black';
                }
                if (!layer.mark.fontSize) {
                  layer.mark.fontSize = 12;
                }
              } else {
                console.log('🔧 GRID-LAYOUT-FIX: Removing redundant text layer', index);
                layer._remove = true;
              }
            }
          });

          // Remove marked layers
          spec.layer = spec.layer.filter((layer: any) => !layer._remove);

          console.log('🔧 GRID-LAYOUT-FIX: Applied consistent grid positioning');
        }
      };

      // Fix 1.95: Add explicit domain for nominal scales with range but no domain
      const addDomainForNominalScales = (encoding: any, dataValues: any[]) => {
        if (!encoding || !dataValues || dataValues.length === 0) return;
        ['color', 'fill', 'stroke', 'size', 'shape', 'opacity'].forEach(channel => {
          const channelSpec = encoding[channel];
          if (channelSpec?.field && channelSpec?.type === 'nominal' && channelSpec?.scale?.range && !channelSpec?.scale?.domain) {
            const uniqueValues = [...new Set(dataValues.map(d => d[channelSpec.field]))].filter(v => v !== null && v !== undefined).sort();
            if (uniqueValues.length > 0) {
              channelSpec.scale.domain = uniqueValues;
              console.log(`🔧 VEGA-PREPROCESS: Added domain ${JSON.stringify(uniqueValues)} for ${channel} channel`);

              // Fix range length to match domain length
              if (Array.isArray(channelSpec.scale.range) && channelSpec.scale.range.length !== uniqueValues.length) {
                if (uniqueValues.length === 1) {
                  channelSpec.scale.range = [channelSpec.scale.range[channelSpec.scale.range.length - 1]];
                  console.log(`🔧 VEGA-PREPROCESS: Adjusted ${channel} range to match single-value domain`);
                } else if (channelSpec.scale.range.length < uniqueValues.length) {
                  // Extend range by repeating last value
                  const lastValue = channelSpec.scale.range[channelSpec.scale.range.length - 1];
                  while (channelSpec.scale.range.length < uniqueValues.length) {
                    channelSpec.scale.range.push(lastValue);
                  }
                  console.log(`🔧 VEGA-PREPROCESS: Extended ${channel} range to match domain length`);
                }
              }
            }
          }
        });
      };

      if (spec.data?.values) {
        addDomainForNominalScales(spec.encoding, spec.data.values);
        fixLiteralColorFields(spec.encoding, spec.data.values);
        fixGridLayoutIssues(spec);
      }
      if (spec.hconcat) {
        spec.hconcat.forEach((s: any) => {
          const dataValues = s.data?.values || spec.data?.values;
          if (dataValues) {
            addDomainForNominalScales(s.encoding, dataValues);
            fixLiteralColorFields(s.encoding, dataValues);
          }
        });
      }
      if (spec.vconcat) {
        spec.vconcat.forEach((s: any) => {
          const dataValues = s.data?.values || spec.data?.values;
          if (dataValues) {
            addDomainForNominalScales(s.encoding, dataValues);
            fixLiteralColorFields(s.encoding, dataValues);
          }
        });
      }
      if (spec.layer) {
        spec.layer.forEach((s: any) => {
          const dataValues = s.data?.values || spec.data?.values;
          if (dataValues) {
            addDomainForNominalScales(s.encoding, dataValues);
            fixLiteralColorFields(s.encoding, dataValues);
          }
        });
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

      // Fix 3.7: Convert problematic log scales to symlog
      const fixLogScales = (encoding: any, dataValues: any[]) => {
        if (!encoding || !dataValues || dataValues.length === 0) return;

        ['x', 'y'].forEach(channel => {
          const channelSpec = encoding[channel];
          if (channelSpec?.field && channelSpec?.type === 'quantitative' && channelSpec?.scale?.type === 'log') {
            const values = dataValues.map(d => d[channelSpec.field]).filter(v => typeof v === 'number' && !isNaN(v));
            const hasZeroOrNegative = values.some(v => v <= 0);
            const range = Math.max(...values) / Math.min(...values.filter(v => v > 0));

            // Convert log to symlog if there are zero/negative values, or use linear if range is small
            if (hasZeroOrNegative) {
              channelSpec.scale.type = 'symlog';
              console.log(`🔧 VEGA-PREPROCESS: Converted ${channel} log scale to symlog (has zero/negative values)`);
            } else if (range < 100) {
              delete channelSpec.scale.type;
              console.log(`🔧 VEGA-PREPROCESS: Removed ${channel} log scale (range too small: ${range.toFixed(1)}x)`);
            }
          }
        });
      };

      if (spec.data?.values) {
        fixLogScales(spec.encoding, spec.data.values);
      }
      if (spec.hconcat) spec.hconcat.forEach((s: any) => {
        const dataValues = s.data?.values || spec.data?.values;
        if (dataValues) fixLogScales(s.encoding, dataValues);
      });
      if (spec.vconcat) spec.vconcat.forEach((s: any) => {
        const dataValues = s.data?.values || spec.data?.values;
        if (dataValues) fixLogScales(s.encoding, dataValues);
      });
      if (spec.layer) spec.layer.forEach((s: any) => {
        const dataValues = s.data?.values || spec.data?.values;
        if (dataValues) fixLogScales(s.encoding, dataValues);
      });

      // Fix 4: Ensure schema exists
      if (!spec.$schema) {
        spec.$schema = 'https://vega.github.io/schema/vega-lite/v5.json';
      }

      console.log('🔧 VEGA-PREPROCESS: Preprocessing complete');
      return spec;
    };

    // Fix 6: Handle rect charts with fixed y values that render as single rectangles
    const fixRectChartsWithFixedY = (spec: any): any => {
      if (!spec.mark || (spec.mark !== 'rect' && spec.mark.type !== 'rect')) {
        return spec;
      }

      // Check if we have a fixed y value that would cause stacking
      if (spec.encoding?.y?.value !== undefined && spec.encoding?.x?.field) {
        console.log('🔧 RECT-Y-FIX: Detected rect chart with fixed y value, converting to proper visualization');

        // Analyze the data to determine what kind of chart this should be
        if (spec.data?.values && Array.isArray(spec.data.values)) {
          const firstRow = spec.data.values[0] || {};
          const numericFields = Object.keys(firstRow).filter(key =>
            key !== spec.encoding.x.field &&
            (typeof firstRow[key] === 'number' ||
              spec.transform?.some((t: any) => t.as === key))
          );

          // If we have calculated fields or multiple numeric fields, this should probably be a bar chart
          if (numericFields.length > 0) {
            console.log(`🔧 RECT-Y-FIX: Converting to bar chart using field: ${numericFields[0]}`);

            // Convert to bar chart
            spec.mark = 'bar';

            // Use the first numeric field or calculated field for y-axis
            spec.encoding.y = {
              field: numericFields[0],
              type: 'quantitative',
              title: numericFields[0].replace(/_/g, ' ').replace(/\b\w/g, (l: string) => l.toUpperCase())
            };

            // If there are multiple numeric fields and transforms, suggest using fold transform
            if (numericFields.length > 1 && !spec.transform?.some((t: any) => t.fold)) {
              console.log('🔧 RECT-Y-FIX: Multiple numeric fields detected, might benefit from fold transform');
            }
          }
        }
      }

      return spec;
    };

    // Fix 7: Handle area/line charts missing y-axis encoding after window transforms
    const fixChartsWithMissingYAfterTransforms = (spec: any): any => {
      // Check for area or line charts with transforms but missing y encoding
      if (spec.mark && (spec.mark.type === 'area' || spec.mark === 'area' || spec.mark.type === 'line' || spec.mark === 'line') &&
        spec.transform && spec.encoding?.x && !spec.encoding?.y) {

        console.log('🔧 MISSING-Y-FIX: Detected area/line chart with transforms but missing y-axis encoding');

        // Look for window transforms that create calculated fields
        const windowTransform = spec.transform.find((t: any) => t.window);
        if (windowTransform && windowTransform.window && Array.isArray(windowTransform.window)) {
          const calculatedField = windowTransform.window.find((w: any) => w.as && w.field);

          if (calculatedField?.as) {
            console.log(`🔧 MISSING-Y-FIX: Adding y-axis encoding using calculated field: ${calculatedField.as}`);
            spec.encoding.y = {
              field: calculatedField.as,
              type: 'quantitative',
              title: calculatedField.as.replace(/_/g, ' ').replace(/\b\w/g, (l: string) => l.toUpperCase())
            };
          }
        }

        // CRITICAL FIX: Handle case where window transform creates cumulative field but we need better field detection
        if (!spec.encoding.y && windowTransform?.window) {
          // Find any calculated field from the window transform
          const windowOp = windowTransform.window.find((w: any) => w.as);
          if (windowOp?.as) {
            console.log(`🔧 MISSING-Y-FIX: Using window transform output field: ${windowOp.as}`);
            spec.encoding.y = {
              field: windowOp.as,
              type: 'quantitative',
              title: windowOp.as.replace(/_/g, ' ').replace(/\b\w/g, (l: string) => l.toUpperCase())
            };
          }
        }
      }

      return spec;
    };

    // Fix 8: Handle arc/pie charts with invalid color schemes (hex instead of scheme name)
    const fixInvalidColorSchemeInArcs = (spec: any): any => {
      if (!spec.mark || (spec.mark !== 'arc' && spec.mark.type !== 'arc')) {
        return spec;
      }

      // Fix invalid color scheme (hex color instead of scheme name)
      if (spec.encoding?.color?.scale?.scheme && typeof spec.encoding.color.scale.scheme === 'string' &&
        spec.encoding.color.scale.scheme.startsWith('#')) {
        console.log('🔧 ARC-COLOR-FIX: Converting invalid hex color scheme to proper color range');
        const hexColor = spec.encoding.color.scale.scheme;
        delete spec.encoding.color.scale.scheme;

        // Generate a color palette based on the provided hex color
        spec.encoding.color.scale.range = generateColorPalette(hexColor, spec.data?.values?.length || 8);
      }

      return spec;
    };

    // Fix 9: Handle arc/pie charts missing theta2 encoding
    const fixMissingTheta2InArcs = (spec: any): any => {
      if (!spec.mark || (spec.mark !== 'arc' && spec.mark.type !== 'arc')) {
        return spec;
      }

      // Add theta2 encoding for proper arc segments if missing
      if (spec.encoding?.theta && !spec.encoding?.theta2) {
        console.log('🔧 ARC-THETA2-FIX: Adding theta2 encoding for proper arc segments');
        spec.encoding.theta2 = { value: 0 };
      }

      return spec;
    };

    // Helper function to generate a color palette from a base hex color
    const generateColorPalette = (baseColor: string, count: number): string[] => {
      // Simple color variations based on the base color
      const variations = [
        '#ff6b6b', '#4ecdc4', '#45b7d1', '#96ceb4', '#ffd93d',
        '#ff9ff3', '#54a0ff', '#5f27cd', '#ff9f43', '#0abde3',
        '#006ba6', '#f18701', '#d00000', '#8900f2', '#a8e6cf'
      ];

      // If we have enough variations, use them; otherwise cycle through
      const palette: string[] = [];
      for (let i = 0; i < count; i++) {
        palette.push(variations[i % variations.length]);
      }
      return palette;
    };

    // Fix 10: Handle layered charts with mismatched y-axis scales
    const fixLayeredChartsWithMismatchedScales = (spec: any): any => {
      if (!spec.layer || !Array.isArray(spec.layer) || spec.layer.length < 2) {
        return spec;
      }

      // Check if we have layers with different y-axis fields and scales
      const yFields = spec.layer.map((layer: any) => layer.encoding?.y?.field).filter(Boolean);
      const hasLogScale = spec.layer.some((layer: any) => layer.encoding?.y?.scale?.type === 'log');
      const hasLinearScale = spec.layer.some((layer: any) => !layer.encoding?.y?.scale?.type || layer.encoding?.y?.scale?.type === 'linear');

      if (yFields.length > 1 && hasLogScale && hasLinearScale) {
        console.log('🔧 DUAL-AXIS-FIX: Detected layered chart with mismatched y-axis scales');

        // CRITICAL: Remove problematic domains that cause scale conflicts
        spec.layer.forEach((layer: any, index: number) => {
          if (layer.encoding?.y?.scale?.domain) {
            console.log(`🔧 DUAL-AXIS-FIX: Removing conflicting domain from layer ${index}`);
            delete layer.encoding.y.scale.domain;
          }
        });

        // Add resolve scales to use independent y-axes
        spec.resolve = {
          scale: {
            y: 'independent'
          }
        };

        // CRITICAL FIX: For nominal x-axis with log y-axis, ensure proper positioning
        if (spec.layer.some(layer => layer.encoding?.x?.type === 'nominal' && layer.encoding?.y?.scale?.type === 'log')) {
          console.log('🔧 DUAL-AXIS-FIX: Converting nominal x-axis to ordinal for better log scale compatibility');
          spec.layer.forEach(layer => {
            if (layer.encoding?.x?.type === 'nominal') {
              layer.encoding.x.type = 'ordinal';
            }
          });
        }

        spec.resolve = {
          scale: {
            y: 'independent'
          }
        };

        // Ensure each layer has proper axis configuration
        spec.layer.forEach((layer, index) => {
          if (layer.encoding?.y) {
            if (!layer.encoding.y.axis) {
              layer.encoding.y.axis = {};
            }
            // First layer gets left axis, subsequent layers get right axis
            layer.encoding.y.axis.orient = index === 0 ? 'left' : 'right';
            layer.encoding.y.axis.grid = index === 0; // Only show grid for first layer
          }
        });

        spec.layer.forEach((layer: any, index: number) => {
          if (layer.encoding?.y && index > 0) {
            // Position subsequent y-axes on the right side
            if (!layer.encoding.y.axis) {
              layer.encoding.y.axis = {};
            }
            layer.encoding.y.axis.orient = 'right';

            // Ensure the right axis is visible
            layer.encoding.y.axis.grid = false; // Avoid grid conflicts
          }
        });

        console.log('🔧 DUAL-AXIS-FIX: Added independent y-axis scaling');
      }

      return spec;
    };

    // Fix 11: Handle rect charts with fold transforms missing x-axis and color encodings
    const fixRectChartsWithFoldMissingEncodings = (spec: any): any => {
      if (!spec.mark || (spec.mark !== 'rect' && spec.mark.type !== 'rect')) {
        return spec;
      }

      // Check if we have a fold transform but missing x or color encodings
      if (spec.transform?.some((t: any) => t.fold) && spec.encoding?.y &&
        (!spec.encoding?.x || !spec.encoding?.color)) {

        console.log('🔧 RECT-FOLD-FIX: Detected rect chart with fold transform missing x/color encodings');

        const foldTransform = spec.transform.find((t: any) => t.fold);
        const keyField = foldTransform?.as?.[0] || 'key';    // skill_level
        const valueField = foldTransform?.as?.[1] || 'value'; // percentage

        // Add missing x encoding (for the values)
        if (!spec.encoding.x) {
          spec.encoding.x = {
            field: valueField,
            type: 'quantitative',
            title: valueField.replace(/_/g, ' ').replace(/\b\w/g, (l: string) => l.toUpperCase())
          };
        }

        // Add missing color encoding (for the categories)  
        if (!spec.encoding.color) {
          spec.encoding.color = {
            field: keyField,
            type: 'nominal',
            title: keyField.replace(/_/g, ' ').replace(/\b\w/g, (l: string) => l.toUpperCase())
          };
        }

        console.log(`🔧 RECT-FOLD-FIX: Added x="${valueField}" and color="${keyField}" encodings`);
      }

      return spec;
    };

    // Fix 12: Handle layered charts with inappropriate y-axis domains
    const fixInappropriateYAxisDomainsInLayers = (spec: any): any => {
      if (!spec.layer || !Array.isArray(spec.layer)) {
        return spec;
      }

      console.log('🔧 Y-DOMAIN-FIX: Checking layered chart y-axis domains');

      spec.layer.forEach((layer: any, index: number) => {
        if (layer.encoding?.y?.scale?.domain && layer.encoding.y.field) {
          const yField = layer.encoding.y.field;

          // Check if the scale domain is inappropriate for the data
          if (spec.data?.values) {
            const fieldValues = spec.data.values.map(d => d[yField]).filter(v => v !== undefined);
            const minVal = Math.min(...fieldValues);
            const maxVal = Math.max(...fieldValues);
            const domainMin = layer.encoding.y.scale.domain[0];
            const domainMax = layer.encoding.y.scale.domain[1];

            // If the domain is much larger than the data range, remove it to use auto-scaling
            if (domainMax > maxVal * 5 || domainMin < minVal - (maxVal - minVal)) {
              console.log(`🔧 Y-DOMAIN-FIX: Removing inappropriate y-axis domain [${domainMin}, ${domainMax}] for field "${yField}" with range [${minVal}, ${maxVal}]`);
              delete layer.encoding.y.scale.domain;
            }
          }
        }
      });

      return spec;
    };

    // Fix 13: Handle point charts with fold transforms missing y-axis and color encodings
    const fixPointChartsWithFoldMissingEncodings = (spec: any): any => {
      if (!spec.mark || (spec.mark !== 'point' && spec.mark.type !== 'point')) {
        return spec;
      }

      // Check if we have a fold transform and x-axis but missing y-axis or color
      if (spec.transform?.some((t: any) => t.fold) && spec.encoding?.x &&
        (!spec.encoding?.y || !spec.encoding?.color)) {

        console.log('🔧 POINT-FOLD-FIX: Detected point chart with fold transform missing encodings');

        const foldTransform = spec.transform.find((t: any) => t.fold);
        const keyField = foldTransform?.as?.[0] || 'key';     // skill_type
        const valueField = foldTransform?.as?.[1] || 'value'; // level

        // Add missing y-axis encoding
        if (!spec.encoding.y) {
          spec.encoding.y = {
            field: valueField,
            type: 'quantitative',
            title: valueField.replace(/_/g, ' ').replace(/\b\w/g, (l: string) => l.toUpperCase())
          };
        }

        // Add missing color encoding to differentiate skill types
        if (!spec.encoding.color) {
          spec.encoding.color = {
            field: keyField,
            type: 'nominal',
            title: keyField.replace(/_/g, ' ').replace(/\b\w/g, (l: string) => l.toUpperCase())
          };
        }

        console.log(`🔧 POINT-FOLD-FIX: Added y="${valueField}" and color="${keyField}" encodings`);
      }

      return spec;
    };

    // Fix 14: Handle bar charts with fold transforms missing x-axis and color encodings
    const fixBarChartsWithFoldMissingEncodings = (spec: any): any => {
      if (!spec.mark || (spec.mark !== 'bar' && spec.mark.type !== 'bar')) {
        return spec;
      }

      // Check if we have a fold transform and y-axis but missing x-axis or color encodings
      if (spec.transform?.some((t: any) => t.fold) && spec.encoding?.y &&
        (!spec.encoding?.x || !spec.encoding?.color)) {

        console.log('🔧 BAR-FOLD-FIX: Detected bar chart with fold transform missing x/color encodings');

        const foldTransform = spec.transform.find((t: any) => t.fold);
        const keyField = foldTransform?.as?.[0] || 'key';     // period (before/after)
        const valueField = foldTransform?.as?.[1] || 'value'; // performance

        // Add missing x encoding (for the values)
        if (!spec.encoding.x) {
          spec.encoding.x = {
            field: valueField,
            type: 'quantitative',
            title: valueField.replace(/_/g, ' ').replace(/\b\w/g, (l: string) => l.toUpperCase())
          };
        }

        // Add missing color encoding (for the categories)
        if (!spec.encoding.color) {
          spec.encoding.color = {
            field: keyField,
            type: 'nominal',
            title: keyField.replace(/_/g, ' ').replace(/\b\w/g, (l: string) => l.toUpperCase())
          };
        }

        console.log(`🔧 BAR-FOLD-FIX: Added x="${valueField}" and color="${keyField}" encodings`);
      }

      return spec;
    };

    // Fix 15: Handle tooltip encodings that can cause destructuring errors
    const fixTooltipEncodings = (spec: any): any => {
      if (!spec.encoding?.tooltip) return spec;

      console.log('🔧 TOOLTIP-FIX: Fixing tooltip encodings that may cause destructuring errors');

      // If tooltip is an array, validate each item
      if (Array.isArray(spec.encoding.tooltip)) {
        spec.encoding.tooltip = spec.encoding.tooltip.map((tooltipItem: any, index: number) => {
          // Ensure each tooltip item has the required structure
          if (typeof tooltipItem === 'object' && tooltipItem !== null) {
            // If missing required properties, fix them
            if (!tooltipItem.field && !tooltipItem.aggregate && !tooltipItem.value) {
              console.log(`🔧 TOOLTIP-FIX: Removing invalid tooltip item at index ${index}:`, tooltipItem);
              return null; // Mark for removal
            }

            // Ensure type is set if field is present
            if (tooltipItem.field && !tooltipItem.type) {
              // Infer type from field name or data
              tooltipItem.type = tooltipItem.field.includes('cases') || tooltipItem.field === 'complexity' ? 'quantitative' : 'nominal';
              console.log(`🔧 TOOLTIP-FIX: Added missing type '${tooltipItem.type}' to tooltip field '${tooltipItem.field}'`);
            }
          }
          return tooltipItem;
        }).filter(item => item !== null); // Remove invalid items
      }

      return spec;
    };


    // Fix 5: Improve LLM-generated chart compatibility
    const fixLLMGeneratedCharts = (spec: any): any => {
      console.log('🔧 LLM-CHART-FIX: Starting LLM-generated chart fixes');

      // Fix 5.1: Convert sequential numeric fields with nominal type to proper ordinal
      if (spec.encoding && spec.data?.values) {
        Object.keys(spec.encoding).forEach(channel => {
          const channelSpec = spec.encoding[channel];
          if (channelSpec?.field && channelSpec?.type === 'nominal') {
            const fieldValues = spec.data.values.map((d: any) => d[channelSpec.field]);

            // Check if this looks like years (4-digit numbers)
            const looksLikeYears = fieldValues.every((val: any) =>
              typeof val === 'number' && val >= 1900 && val <= 2100
            );

            if (looksLikeYears && channel === 'x') {
              console.log(`🔧 LLM-CHART-FIX: Converting numeric years in ${channel} from nominal to ordinal`);
              channelSpec.type = 'ordinal';
              // Convert numeric years to strings for better ordinal handling
              spec.data.values = spec.data.values.map((row: any) => ({
                ...row,
                [channelSpec.field]: String(row[channelSpec.field])
              }));
            }

            // Check if this is sequential numbers (like attempts 1,2,3,4,5)
            const looksLikeSequence = fieldValues.every((val: any) => typeof val === 'number') &&
              fieldValues.length > 1 &&
              Math.max(...fieldValues) - Math.min(...fieldValues) === fieldValues.length - 1;

            if (looksLikeSequence && channel === 'x') {
              console.log(`🔧 LLM-CHART-FIX: Converting sequential numbers in ${channel} from nominal to ordinal`);
              channelSpec.type = 'ordinal';
            }
          }
        });
      }

      // Fix 5.2: Ensure line charts have proper mark configuration
      if (spec.mark === 'line' && spec.encoding?.x && spec.encoding?.y) {
        console.log('🔧 LLM-CHART-FIX: Adding points to line chart for better visibility');
        spec.mark = {
          type: 'line',
          point: true,
          strokeWidth: 2
        };
      }

      // Fix 5.3: Handle line charts with size encoding (should use point marks instead)
      if (spec.mark === 'line' && spec.encoding?.size) {
        console.log('🔧 LLM-CHART-FIX: Converting simple line chart with size encoding to point chart');
        spec.mark = {
          type: 'point',
          filled: true,
          strokeWidth: 2
        };
      }

      // Fix 5.3b: Handle line charts with point:true and size encoding 
      if (spec.mark && typeof spec.mark === 'object' && spec.mark.type === 'line' && spec.mark.point && spec.encoding?.size) {
        console.log('🔧 LLM-CHART-FIX: Converting line+point chart with size encoding to pure point chart');
        spec.mark = {
          type: 'point',
          filled: true,
          strokeWidth: spec.mark.strokeWidth || 2,
          color: spec.mark.color
        };
      }

      console.log('🔧 LLM-CHART-FIX: LLM chart fixes complete');
      return spec;
    };

    // Apply LLM fixes before other preprocessing
    spec = fixRectChartsWithFixedY(spec);

    // Apply missing Y-axis fix after rect fixes
    spec = fixChartsWithMissingYAfterTransforms(spec);

    // Apply arc chart fixes
    spec = fixInvalidColorSchemeInArcs(spec);
    spec = fixMissingTheta2InArcs(spec);

    // Apply layered chart fixes
    spec = fixLayeredChartsWithMismatchedScales(spec);

    // Apply rect with fold fixes
    spec = fixRectChartsWithFoldMissingEncodings(spec);

    // Apply inappropriate domain fixes
    spec = fixInappropriateYAxisDomainsInLayers(spec);

    // Apply point chart fixes
    spec = fixPointChartsWithFoldMissingEncodings(spec);

    // Apply bar chart with fold fixes
    spec = fixBarChartsWithFoldMissingEncodings(spec);

    // Apply LLM fixes after rect fixes
    spec = fixLLMGeneratedCharts(spec);

    // Apply tooltip fixes
    spec = fixTooltipEncodings(spec);

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
      ['type', 'isStreaming', 'forceRender', 'definition', 'isMarkdownBlockClosed'].forEach(prop => delete rawSpec[prop]);
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

      // CRITICAL FIX: Handle shape encoding that causes "Cannot read properties of null (reading 'slice')" error

      // Apply this fix EARLY in the preprocessing pipeline
      // Additional post-preprocessing validations and fixes
      console.log('🔧 VEGA-POST-PROCESS: Starting additional fixes');

      // Fix problematic axis labelLimit values that can cause rendering failures
      if (vegaSpec.encoding?.x?.axis?.labelLimit !== undefined && vegaSpec.encoding.x.axis.labelLimit <= 0) {
        console.log('🔧 VEGA-POST-PROCESS: Fixing problematic axis labelLimit');
        delete vegaSpec.encoding.x.axis.labelLimit;
      }

      // Fix problematic axis properties in layered charts
      if (vegaSpec.layer && Array.isArray(vegaSpec.layer)) {
        vegaSpec.layer.forEach((layer, index) => {
          if (layer.encoding) {
            ['x', 'y'].forEach(axis => {
              if (layer.encoding[axis]?.axis?.labelLimit !== undefined && layer.encoding[axis].axis.labelLimit <= 0) {
                console.log(`🔧 VEGA-POST-PROCESS: Fixing problematic ${axis} axis labelLimit in layer ${index}`);
                delete layer.encoding[axis].axis.labelLimit;
              }
            });
          }
        });
      }

      // Fix layered charts with mismatched y-axis scales and missing legends
      if (vegaSpec.layer && Array.isArray(vegaSpec.layer) && vegaSpec.layer.length > 1) {
        console.log('🔧 VEGA-POST-PROCESS: Fixing layered chart scales and legends');

        // CRITICAL FIX: Handle layers accessing fields from before fold transform
        // Check if we have a fold transform at the top level
        const hasFoldTransform = vegaSpec.transform && vegaSpec.transform.some((t: any) => t.fold);

        if (hasFoldTransform && vegaSpec.data?.values) {
          const originalData = vegaSpec.data.values;
          const foldTransform = vegaSpec.transform.find((t: any) => t.fold);
          const foldedFields = foldTransform?.fold || [];

          // Check each layer for field references that aren't in the folded data
          vegaSpec.layer.forEach((layer: any, index: number) => {
            if (layer.encoding) {
              Object.keys(layer.encoding).forEach(channel => {
                const channelSpec = layer.encoding[channel];
                if (channelSpec?.field && !foldedFields.includes(channelSpec.field) &&
                  originalData.length > 0 && originalData[0].hasOwnProperty(channelSpec.field)) {
                  // This layer needs access to the original data
                  console.log(`🔧 FOLD-DATA-FIX: Layer ${index} needs original data for field "${channelSpec.field}"`);
                  layer.data = { values: originalData };
                }
              });
            }
          });
        }

        vegaSpec.layer.forEach((layer: any, index: number) => {
          if (layer.encoding?.y?.scale?.domain && layer.encoding.y.field) {
            const yField = layer.encoding.y.field;

            // Check if the scale domain is inappropriate for the data
            if (vegaSpec.data?.values) {
              const fieldValues = vegaSpec.data.values.map(d => d[yField]).filter(v => v !== undefined);
              const minVal = Math.min(...fieldValues);
              const maxVal = Math.max(...fieldValues);
              const domainMin = layer.encoding.y.scale.domain[0];
              const domainMax = layer.encoding.y.scale.domain[1];

              // If the domain is much larger than the data range, remove it to use auto-scaling
              if (domainMax > maxVal * 5 || domainMin < minVal - (maxVal - minVal)) {
                console.log(`Removing inappropriate y-axis domain [${domainMin}, ${domainMax}] for field "${yField}" with range [${minVal}, ${maxVal}]`);
                delete layer.encoding.y.scale.domain;
              }
            }
          }

          // Add legend labels for layered charts if missing
          if (layer.mark?.color && !layer.encoding?.color && index > 0) {
            // This layer has a hardcoded color but no legend - we'll handle this in resolve below
          }
        });
      }

      // Fix faceted charts with bars using fixed y values instead of proper encodings
      if (vegaSpec.facet && vegaSpec.spec?.layer) {
        console.log('🔧 VEGA-POST-PROCESS: Fixing faceted chart layer encodings');

        vegaSpec.spec.layer.forEach((layer, index) => {
          if (layer.mark?.type === 'bar' && layer.encoding?.y?.value !== undefined) {
            console.log(`Fixing bar layer ${index} with fixed y value`);

            // Remove the fixed y value and create proper bar encoding
            delete layer.encoding.y.value;

            // For horizontal bars, we need to swap x and y
            if (layer.encoding.x?.field && layer.encoding.x.type === 'quantitative') {
              // This should be a horizontal bar chart
              const xField = layer.encoding.x.field;
              const xConfig = { ...layer.encoding.x };

              // Swap x and y for horizontal bars
              layer.encoding.y = xConfig;
              layer.encoding.x = {
                field: vegaSpec.facet.row?.field || vegaSpec.facet.column?.field,
                type: 'nominal'
              };
            }
          }
        });
      }

      // Fix layered charts missing legends for hardcoded colors
      if (vegaSpec.layer && Array.isArray(vegaSpec.layer) && vegaSpec.layer.length > 1) {
        console.log('🔧 VEGA-POST-PROCESS: Adding legends for layered chart with hardcoded colors');

        const layersWithHardcodedColors = vegaSpec.layer.filter(layer =>
          layer.encoding?.color?.value || layer.mark?.color
        );

        if (layersWithHardcodedColors.length > 0) {
          // Create a synthetic dataset for the legend
          const legendData: { series: string; color: string }[] = [];
          vegaSpec.layer.forEach((layer, index) => {
            const color = layer.encoding?.color?.value || layer.mark?.color;
            const yField = layer.encoding?.y?.field;

            if (color && yField) {
              legendData.push({
                series: yField.replace('_', ' ').replace(/\b\w/g, l => l.toUpperCase()),
                color: color
              });
            }
          });

          if (legendData.length > 0) {
            // Add a legend layer
            vegaSpec.layer.push({
              data: { values: legendData },
              mark: { type: 'point', size: 0, opacity: 0 },
              encoding: {
                color: {
                  field: 'series',
                  type: 'nominal',
                  scale: {
                    domain: legendData.map(d => d.series),
                    range: legendData.map(d => d.color)
                  },
                  legend: { title: 'Metrics' }
                }
              }
            });
          }
        }
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
            type: 'nominal'
          };
        }
      }

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
        };
      }

      // Fix bar charts with fold transforms missing y-axis encoding for the 'value' field
      if (vegaSpec.mark && (vegaSpec.mark.type === 'bar' || vegaSpec.mark === 'bar') &&
        vegaSpec.transform && vegaSpec.transform.some(t => t.fold) &&
        vegaSpec.encoding && vegaSpec.encoding.x && (!vegaSpec.encoding.y || !vegaSpec.encoding.y.field)) {
        console.log('Fixing bar chart with fold transform missing y-axis encoding for value field');

        const foldTransform = vegaSpec.transform.find(t => t.fold);
        const yFieldName = foldTransform?.as?.[1] || 'value';

        vegaSpec.encoding.y = {
          field: yFieldName,
          type: 'quantitative',
          title: yFieldName.charAt(0).toUpperCase() + yFieldName.slice(1)
        };
      }

      // Fix for bar charts missing y-axis encoding (common issue with flow/journey charts)
      if (vegaSpec.mark && (vegaSpec.mark.type === 'bar' || vegaSpec.mark === 'bar') &&
        vegaSpec.encoding && vegaSpec.encoding.x && !vegaSpec.encoding.y) {
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

      // Fix regular line charts with fold transforms missing y-axis encoding
      if (vegaSpec.mark && (vegaSpec.mark.type === 'line' || vegaSpec.mark === 'line') &&
        vegaSpec.transform && vegaSpec.transform.some(t => t.fold) &&
        vegaSpec.encoding && vegaSpec.encoding.x && (!vegaSpec.encoding.y || !vegaSpec.encoding.y.field)) {
        console.log('Fixing line chart with fold transform missing y-axis encoding');

        // Find the fold transform to get the correct field name for the y-axis
        const foldTransform = vegaSpec.transform.find(t => t.fold);
        const yFieldName = foldTransform?.as?.[1] || 'value';

        if (!vegaSpec.encoding.y) {
          vegaSpec.encoding.y = {};
        }
        vegaSpec.encoding.y = {
          ...vegaSpec.encoding.y,
          field: yFieldName,
          type: 'quantitative',
          title: yFieldName.charAt(0).toUpperCase() + yFieldName.slice(1)
        };

        // Also add color encoding to distinguish the different lines if not already present
        if (!vegaSpec.encoding.color) {
          const colorFieldName = foldTransform?.as?.[0] || 'key';
          vegaSpec.encoding.color = {
            field: colorFieldName,
            type: 'nominal',
            title: colorFieldName.charAt(0).toUpperCase() + colorFieldName.slice(1)
          };
        }
      }

      // Fix area charts with fold transforms missing y-axis encoding
      if (vegaSpec.mark && (vegaSpec.mark.type === 'area' || vegaSpec.mark === 'area') &&
        vegaSpec.transform && vegaSpec.transform.some(t => t.fold) &&
        vegaSpec.encoding && vegaSpec.encoding.x && !vegaSpec.encoding.y) {
        console.log('Fixing area chart with fold transform missing y-axis encoding');

        const foldTransform = vegaSpec.transform.find(t => t.fold);
        const yFieldName = foldTransform?.as?.[1] || 'value';

        vegaSpec.encoding.y = {
          field: yFieldName,
          type: 'quantitative',
          title: yFieldName.charAt(0).toUpperCase() + yFieldName.slice(1)
        };

        if (!vegaSpec.encoding.color) {
          const colorFieldName = foldTransform?.as?.[0] || 'key';
          vegaSpec.encoding.color = {
            field: colorFieldName,
            type: 'nominal',
            title: colorFieldName.charAt(0).toUpperCase() + colorFieldName.slice(1)
          };
        }
      }

      // Fix rect/heatmap charts with fold transforms missing y-axis and color encodings
      if (vegaSpec.mark && (vegaSpec.mark.type === 'rect' || vegaSpec.mark === 'rect') &&
        vegaSpec.transform && vegaSpec.transform.some(t => t.fold) &&
        vegaSpec.encoding && vegaSpec.encoding.x && !vegaSpec.encoding.y) {
        console.log('Fixing rect chart with fold transform missing y-axis encoding');

        const foldTransform = vegaSpec.transform.find(t => t.fold);
        const keyField = foldTransform?.as?.[0] || 'key'; // Element field
        const valueField = foldTransform?.as?.[1] || 'value'; // Mastery level field

        vegaSpec.encoding.y = {
          field: keyField,
          type: 'nominal',
          title: keyField.charAt(0).toUpperCase() + keyField.slice(1)
        };

        // Use the value field for color encoding to show intensity
        if (!vegaSpec.encoding.color) {
          vegaSpec.encoding.color = {
            field: valueField,
            type: 'quantitative',
            title: valueField.charAt(0).toUpperCase() + valueField.slice(1).replace('_', ' '),
            scale: {
              scheme: 'viridis'
            }
          };
        }
      }

      // Fix rect/heatmap charts with aggregate transforms missing color encoding
      if (vegaSpec.mark && (vegaSpec.mark.type === 'rect' || vegaSpec.mark === 'rect') &&
        vegaSpec.transform && vegaSpec.transform.some(t => t.aggregate) &&
        vegaSpec.encoding && vegaSpec.encoding.x && vegaSpec.encoding.y && !vegaSpec.encoding.color) {
        console.log('Fixing rect chart with aggregate transform missing color encoding');

        // Look for calculated fields or aggregated fields to use for color
        const calculateTransform = vegaSpec.transform.find(t => t.calculate);
        const aggregateTransform = vegaSpec.transform.find(t => t.aggregate);

        let colorField: string | null = null;
        if (calculateTransform?.as) {
          colorField = calculateTransform.as; // Use calculated field like "effective_power"
        } else if (aggregateTransform?.aggregate) {
          // Use the first aggregated field
          colorField = aggregateTransform.aggregate[0]?.as;
        }

        if (colorField) {
          vegaSpec.encoding.color = {
            field: colorField,
            type: 'quantitative',
            title: colorField.charAt(0).toUpperCase() + colorField.slice(1).replace('_', ' '),
            scale: {
              scheme: 'blues'
            }
          };
          console.log(`Added color encoding with field: "${colorField}"`);
        }
      }

      // Fix arc/pie charts missing theta encoding for segment sizing
      if (vegaSpec.mark && (vegaSpec.mark.type === 'arc' || vegaSpec.mark === 'arc') &&
        vegaSpec.encoding && vegaSpec.encoding.color && !vegaSpec.encoding.theta) {
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

      // Fix line charts with detail encoding that may be interfering with proper rendering
      if (vegaSpec.mark && (vegaSpec.mark.type === 'line' || vegaSpec.mark === 'line') &&
        vegaSpec.encoding && vegaSpec.encoding.detail && vegaSpec.encoding.color &&
        vegaSpec.encoding.detail.field === vegaSpec.encoding.color.field) {
        console.log('Fixing line chart with redundant detail encoding');

        // Remove redundant detail encoding when it duplicates color encoding
        delete vegaSpec.encoding.detail;
        console.log('Removed redundant detail encoding that duplicated color encoding');

        // Ensure we have proper x and y encodings after field fixes
        if (vegaSpec.encoding.x && vegaSpec.encoding.y && vegaSpec.encoding.x.field && vegaSpec.encoding.y.field) {
          console.log(`Line chart encodings verified: x="${vegaSpec.encoding.x.field}", y="${vegaSpec.encoding.y.field}", color="${vegaSpec.encoding.color.field}"`);
        } else {
          console.warn('Line chart still missing required encodings after detail fix');
        }
      }

      // Fix line charts with fold transforms missing both x and y encodings
      if (vegaSpec.mark && (vegaSpec.mark.type === 'line' || vegaSpec.mark === 'line') &&
        vegaSpec.transform && vegaSpec.transform.some(t => t.fold) &&
        vegaSpec.encoding && !vegaSpec.encoding.x && !vegaSpec.encoding.y) {
        console.log('Fixing line chart with fold transform missing both x and y encodings');

        const foldTransform = vegaSpec.transform.find(t => t.fold);
        const keyField = foldTransform?.as?.[0] || 'key';
        const valueField = foldTransform?.as?.[1] || 'value';

        vegaSpec.encoding.x = {
          field: keyField,
          type: 'nominal',
          title: keyField.charAt(0).toUpperCase() + keyField.slice(1)
        };
        vegaSpec.encoding.y = {
          field: valueField,
          type: 'quantitative',
          title: valueField.charAt(0).toUpperCase() + valueField.slice(1).replace('_', ' ')
        };
      }

      // Fix chronological ordering for time-based fold transforms
      if (vegaSpec.transform && vegaSpec.transform.some(t => t.fold) &&
        vegaSpec.encoding && vegaSpec.encoding.x && vegaSpec.encoding.x.type === 'nominal') {
        const foldTransform = vegaSpec.transform.find(t => t.fold);
        if (foldTransform?.fold && Array.isArray(foldTransform.fold)) {
          // Check if this looks like a time-based sequence that should be ordered
          const foldFields = foldTransform.fold;
          const hasTimeSequence = foldFields.some(field =>
            field.includes('before') || field.includes('after') ||
            field.includes('start') || field.includes('end') ||
            field.includes('initial') || field.includes('final')
          );

          if (hasTimeSequence) {
            console.log('Fixing chronological ordering for time-based fold transform');
            vegaSpec.encoding.x.sort = foldFields; // Use original fold order
          }
        }
      }

      // Fix line charts with fold transforms missing y-axis encoding for the folded value field
      if (vegaSpec.mark && (vegaSpec.mark.type === 'line' || vegaSpec.mark === 'line') &&
        vegaSpec.transform && vegaSpec.transform.some(t => t.fold) &&
        vegaSpec.encoding && vegaSpec.encoding.x && (!vegaSpec.encoding.y || !vegaSpec.encoding.y.field)) {
        console.log('Fixing line chart with fold transform missing y-axis encoding');

        // Find the fold transform to get the correct field name for the y-axis
        const foldTransform = vegaSpec.transform.find(t => t.fold);
        const yFieldName = foldTransform?.as?.[1] || 'value';

        if (!vegaSpec.encoding.y) {
          vegaSpec.encoding.y = {};
        }
        vegaSpec.encoding.y = {
          ...vegaSpec.encoding.y,
          field: yFieldName,
          type: 'quantitative',
          title: yFieldName.charAt(0).toUpperCase() + yFieldName.slice(1)
        };

        // Also add color encoding to distinguish the different lines if not already present
        if (!vegaSpec.encoding.color) {
          const colorFieldName = foldTransform?.as?.[0] || 'key';
          vegaSpec.encoding.color = {
            field: colorFieldName,
            type: 'nominal',
            title: colorFieldName.charAt(0).toUpperCase() + colorFieldName.slice(1)
          };
        }
      }

      // Fix area charts with fold transforms missing y-axis encoding
      if (vegaSpec.mark && (vegaSpec.mark.type === 'area' || vegaSpec.mark === 'area') &&
        vegaSpec.transform && vegaSpec.transform.some(t => t.fold) &&
        vegaSpec.encoding && vegaSpec.encoding.x && !vegaSpec.encoding.y) {
        console.log('Fixing area chart with fold transform missing y-axis encoding');

        const foldTransform = vegaSpec.transform.find(t => t.fold);
        const yFieldName = foldTransform?.as?.[1] || 'value';

        vegaSpec.encoding.y = {
          field: yFieldName,
          type: 'quantitative',
          title: yFieldName.charAt(0).toUpperCase() + yFieldName.slice(1)
        };

        if (!vegaSpec.encoding.color) {
          const colorFieldName = foldTransform?.as?.[0] || 'key';
          vegaSpec.encoding.color = {
            field: colorFieldName,
            type: 'nominal',
            title: colorFieldName.charAt(0).toUpperCase() + colorFieldName.slice(1)
          };
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

      // Fix regular line charts with fold transforms missing y-axis encoding
      if (vegaSpec.mark && (vegaSpec.mark.type === 'line' || vegaSpec.mark === 'line') &&
        vegaSpec.transform && vegaSpec.transform.some(t => t.fold) &&
        vegaSpec.encoding && vegaSpec.encoding.x && (!vegaSpec.encoding.y || !vegaSpec.encoding.y.field)) {
        console.log('Fixing line chart with fold transform missing y-axis encoding');

        // Find the fold transform to get the correct field name for the y-axis
        const foldTransform = vegaSpec.transform.find(t => t.fold);
        const yFieldName = foldTransform?.as?.[1] || 'value';

        if (!vegaSpec.encoding.y) {
          vegaSpec.encoding.y = {};
        }
        vegaSpec.encoding.y = {
          ...vegaSpec.encoding.y,
          field: yFieldName,
          type: 'quantitative',
          title: yFieldName.charAt(0).toUpperCase() + yFieldName.slice(1)
        };

        // Also add color encoding to distinguish the different lines if not already present
        if (!vegaSpec.encoding.color) {
          const colorFieldName = foldTransform?.as?.[0] || 'key';
          vegaSpec.encoding.color = {
            field: colorFieldName,
            type: 'nominal',
            title: colorFieldName.charAt(0).toUpperCase() + colorFieldName.slice(1)
          };
        }
      }

      // Fix area charts with fold transforms missing y-axis encoding
      if (vegaSpec.mark && (vegaSpec.mark.type === 'area' || vegaSpec.mark === 'area') &&
        vegaSpec.transform && vegaSpec.transform.some(t => t.fold) &&
        vegaSpec.encoding && vegaSpec.encoding.x && !vegaSpec.encoding.y) {
        console.log('Fixing area chart with fold transform missing y-axis encoding');

        const foldTransform = vegaSpec.transform.find(t => t.fold);
        const yFieldName = foldTransform?.as?.[1] || 'value';

        vegaSpec.encoding.y = {
          field: yFieldName,
          type: 'quantitative',
          title: yFieldName.charAt(0).toUpperCase() + yFieldName.slice(1)
        };

        if (!vegaSpec.encoding.color) {
          const colorFieldName = foldTransform?.as?.[0] || 'key';
          vegaSpec.encoding.color = {
            field: colorFieldName,
            type: 'nominal',
            title: colorFieldName.charAt(0).toUpperCase() + colorFieldName.slice(1)
          };
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

      // Fix regular line charts with fold transforms missing y-axis encoding
      if (vegaSpec.mark && (vegaSpec.mark.type === 'line' || vegaSpec.mark === 'line') &&
        vegaSpec.transform && vegaSpec.transform.some(t => t.fold) &&
        vegaSpec.encoding && vegaSpec.encoding.x && (!vegaSpec.encoding.y || !vegaSpec.encoding.y.field)) {
        console.log('Fixing line chart with fold transform missing y-axis encoding');

        // Find the fold transform to get the correct field name for the y-axis
        const foldTransform = vegaSpec.transform.find(t => t.fold);
        const yFieldName = foldTransform?.as?.[1] || 'value';

        if (!vegaSpec.encoding.y) {
          vegaSpec.encoding.y = {};
        }
        vegaSpec.encoding.y = {
          ...vegaSpec.encoding.y,
          field: yFieldName,
          type: 'quantitative',
          title: yFieldName.charAt(0).toUpperCase() + yFieldName.slice(1)
        };

        // Also add color encoding to distinguish the different lines if not already present
        if (!vegaSpec.encoding.color) {
          const colorFieldName = foldTransform?.as?.[0] || 'key';
          vegaSpec.encoding.color = {
            field: colorFieldName,
            type: 'nominal',
            title: colorFieldName.charAt(0).toUpperCase() + colorFieldName.slice(1)
          };
        }
      }

      // Fix area charts with fold transforms missing y-axis encoding
      if (vegaSpec.mark && (vegaSpec.mark.type === 'area' || vegaSpec.mark === 'area') &&
        vegaSpec.transform && vegaSpec.transform.some(t => t.fold) &&
        vegaSpec.encoding && vegaSpec.encoding.x && !vegaSpec.encoding.y) {
        console.log('Fixing area chart with fold transform missing y-axis encoding');

        const foldTransform = vegaSpec.transform.find(t => t.fold);
        const yFieldName = foldTransform?.as?.[1] || 'value';

        vegaSpec.encoding.y = {
          field: yFieldName,
          type: 'quantitative',
          title: yFieldName.charAt(0).toUpperCase() + yFieldName.slice(1)
        };

        if (!vegaSpec.encoding.color) {
          const colorFieldName = foldTransform?.as?.[0] || 'key';
          vegaSpec.encoding.color = {
            field: colorFieldName,
            type: 'nominal',
            title: colorFieldName.charAt(0).toUpperCase() + colorFieldName.slice(1)
          };
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

      // Fix regular line charts with fold transforms missing y-axis encoding
      if (vegaSpec.mark && (vegaSpec.mark.type === 'line' || vegaSpec.mark === 'line') &&
        vegaSpec.transform && vegaSpec.transform.some(t => t.fold) &&
        vegaSpec.encoding && vegaSpec.encoding.x && (!vegaSpec.encoding.y || !vegaSpec.encoding.y.field)) {
        console.log('Fixing line chart with fold transform missing y-axis encoding');

        // Find the fold transform to get the correct field name for the y-axis
        const foldTransform = vegaSpec.transform.find(t => t.fold);
        const yFieldName = foldTransform?.as?.[1] || 'value';

        if (!vegaSpec.encoding.y) {
          vegaSpec.encoding.y = {};
        }
        vegaSpec.encoding.y = {
          ...vegaSpec.encoding.y,
          field: yFieldName,
          type: 'quantitative',
          title: yFieldName.charAt(0).toUpperCase() + yFieldName.slice(1)
        };

        // Also add color encoding to distinguish the different lines if not already present
        if (!vegaSpec.encoding.color) {
          const colorFieldName = foldTransform?.as?.[0] || 'key';
          vegaSpec.encoding.color = {
            field: colorFieldName,
            type: 'nominal',
            title: colorFieldName.charAt(0).toUpperCase() + colorFieldName.slice(1)
          };
        }
      }

      // Fix area charts with fold transforms missing y-axis encoding
      if (vegaSpec.mark && (vegaSpec.mark.type === 'area' || vegaSpec.mark === 'area') &&
        vegaSpec.transform && vegaSpec.transform.some(t => t.fold) &&
        vegaSpec.encoding && vegaSpec.encoding.x && !vegaSpec.encoding.y) {
        console.log('Fixing area chart with fold transform missing y-axis encoding');

        const foldTransform = vegaSpec.transform.find(t => t.fold);
        const yFieldName = foldTransform?.as?.[1] || 'value';

        vegaSpec.encoding.y = {
          field: yFieldName,
          type: 'quantitative',
          title: yFieldName.charAt(0).toUpperCase() + yFieldName.slice(1)
        };

        if (!vegaSpec.encoding.color) {
          const colorFieldName = foldTransform?.as?.[0] || 'key';
          vegaSpec.encoding.color = {
            field: colorFieldName,
            type: 'nominal',
            title: colorFieldName.charAt(0).toUpperCase() + colorFieldName.slice(1)
          };
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

      // Fix regular line charts with fold transforms missing y-axis encoding
      if (vegaSpec.mark && (vegaSpec.mark.type === 'line' || vegaSpec.mark === 'line') &&
        vegaSpec.transform && vegaSpec.transform.some(t => t.fold) &&
        vegaSpec.encoding && vegaSpec.encoding.x && (!vegaSpec.encoding.y || !vegaSpec.encoding.y.field)) {
        console.log('Fixing line chart with fold transform missing y-axis encoding');

        // Find the fold transform to get the correct field name for the y-axis
        const foldTransform = vegaSpec.transform.find(t => t.fold);
        const yFieldName = foldTransform?.as?.[1] || 'value';

        if (!vegaSpec.encoding.y) {
          vegaSpec.encoding.y = {};
        }
        vegaSpec.encoding.y = {
          ...vegaSpec.encoding.y,
          field: yFieldName,
          type: 'quantitative',
          title: yFieldName.charAt(0).toUpperCase() + yFieldName.slice(1)
        };

        // Also add color encoding to distinguish the different lines if not already present
        if (!vegaSpec.encoding.color) {
          const colorFieldName = foldTransform?.as?.[0] || 'key';
          vegaSpec.encoding.color = {
            field: colorFieldName,
            type: 'nominal',
            title: colorFieldName.charAt(0).toUpperCase() + colorFieldName.slice(1)
          };
        }
      }

      // Fix area charts with fold transforms missing y-axis encoding
      if (vegaSpec.mark && (vegaSpec.mark.type === 'area' || vegaSpec.mark === 'area') &&
        vegaSpec.transform && vegaSpec.transform.some(t => t.fold) &&
        vegaSpec.encoding && vegaSpec.encoding.x && !vegaSpec.encoding.y) {
        console.log('Fixing area chart with fold transform missing y-axis encoding');

        const foldTransform = vegaSpec.transform.find(t => t.fold);
        const yFieldName = foldTransform?.as?.[1] || 'value';

        vegaSpec.encoding.y = {
          field: yFieldName,
          type: 'quantitative',
          title: yFieldName.charAt(0).toUpperCase() + yFieldName.slice(1)
        };

        if (!vegaSpec.encoding.color) {
          const colorFieldName = foldTransform?.as?.[0] || 'key';
          vegaSpec.encoding.color = {
            field: colorFieldName,
            type: 'nominal',
            title: colorFieldName.charAt(0).toUpperCase() + colorFieldName.slice(1)
          };
        }
      }

      // Fix arc/pie charts missing theta encoding for segment sizing
      if (vegaSpec.mark && (vegaSpec.mark.type === 'arc' || vegaSpec.mark === 'arc') &&
        vegaSpec.encoding && vegaSpec.encoding.color && !vegaSpec.encoding.theta) {
        console.log('Fixing arc chart missing theta encoding for segment sizing');

        // Look for calculated fields first (like total_value)
        const calculatedField = vegaSpec.transform?.find(t => t.calculate && t.as)?.as;

        if (calculatedField) {
          vegaSpec.encoding.theta = {
            field: calculatedField,
            type: 'quantitative',
            title: calculatedField.charAt(0).toUpperCase() + calculatedField.slice(1).replace('_', ' ')
          };
          console.log(`Added theta encoding with calculated field: "${calculatedField}"`);
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

      // Fix boxplot charts with invalid axis configuration
      if (vegaSpec.mark && (vegaSpec.mark.type === 'boxplot' || vegaSpec.mark === 'boxplot') &&
        vegaSpec.encoding && vegaSpec.encoding.x && vegaSpec.encoding.y) {
        console.log('Fixing boxplot chart axis configuration');

        // Boxplots need the continuous field on x-axis and categorical on y-axis, or vice versa
        // Check if we have the axes swapped (continuous on y, categorical on x)
        if (vegaSpec.encoding.x.type === 'nominal' && vegaSpec.encoding.y.type === 'quantitative') {
          console.log('Swapping x and y axes for boxplot to put continuous field on x-axis');

          // Swap the encodings
          const tempX = vegaSpec.encoding.x;
          vegaSpec.encoding.x = vegaSpec.encoding.y;
          vegaSpec.encoding.y = tempX;

          // Update titles appropriately
          if (!vegaSpec.encoding.x.title) vegaSpec.encoding.x.title = 'Value';
          if (!vegaSpec.encoding.y.title) vegaSpec.encoding.y.title = 'Category';

          console.log('Boxplot axes swapped successfully');
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

      // Make finalSpec available globally for debugging
      (window as any).__lastVegaSpec = finalSpec;

      console.log('Vega-Lite: About to call vegaEmbed with finalSpec:', finalSpec);
      console.log('Vega-Lite: finalSpec available as window.__lastVegaSpec');

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

      // WORKAROUND: Remove $schema as it can cause parser issues in some Vega versions
      const embedSpec = { ...finalSpec };
      delete embedSpec.$schema;
      console.log('🔧 VEGA-EMBED: Removed $schema for compatibility');

      const result = await vegaEmbed(renderContainer, embedSpec, embedOptions);

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
          sourceView.innerHTML = `<div style="
          font-weight: bold;
          color: ${isDarkMode ? '#58a6ff' : '#0366d6'};
          margin-bottom: 12px;
          font-size: 14px;
        ">📊 Vega-Lite Specification:</div><code>${JSON.stringify(vegaSpec, null, 2)}</code>`;

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
          container.insertBefore(actionsContainer, container.firstChild);
        } else {
          // Restore the visualization
          const sourceView = container.querySelector('pre');
          if (sourceView) {
            container.removeChild(sourceView);
          }

          // Restore the vega container
          if (originalVegaContainer && originalVegaContainer.parentNode !== container) {
            originalVegaContainer.style.display = '';
            container.appendChild(originalVegaContainer);
          } else if (originalVegaContainer) {
            originalVegaContainer.style.display = '';
          } else {
            // Re-render the visualization if the container was lost
            const renderContainer = document.createElement('div');
            renderContainer.style.cssText = 'width: 100%; max-width: 100%; overflow: hidden; box-sizing: border-box;';
            container.appendChild(renderContainer);
            vegaEmbed(renderContainer, vegaSpec, embedOptions);
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

    } // End of try block
    catch (error) {
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
          vegaLitePlugin.render(container, null, forceSpec, isDarkMode);
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
  }

  // Close showVegaLiteDebugView function
};
