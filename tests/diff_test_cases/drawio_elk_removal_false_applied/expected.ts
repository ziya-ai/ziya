import { D3RenderPlugin } from '../../types/d3';

// Import maxGraph CSS for proper rendering
import '@maxgraph/core/css/common.css';

import { loadStencilsForShapes } from './drawioStencilLoader';
import { iconRegistry } from './iconRegistry';
import { hexToRgb, isLightBackground, getOptimalTextColor } from '../../utils/colorUtils';
import { DrawIOEnhancer } from './drawioEnhancer';
import { runLayout, applyLayoutToMaxGraph, LayoutNode, LayoutEdge, LayoutContainer } from './layoutEngine';

// Export architecture shapes renderers
export { generateDrawIOFromCatalog } from './renderers/drawioRenderer';
export { generateMermaidFromCatalog } from './renderers/mermaidRenderer';
export {
    generateGraphvizFromCatalog,
    generateGraphvizWithClusters
} from './renderers/graphvizRenderer';

// Export types
export type {
    ArchitectureShape,
    ShapeCategory,
    ColorPalette,
} from './architectureShapesCatalog';
export type { DrawIOShape, DrawIOConnection } from './renderers/drawioRenderer';
export type { MermaidShape, MermaidConnection } from './renderers/mermaidRenderer';
export type { GraphvizShape, GraphvizConnection, GraphvizCluster } from './renderers/graphvizRenderer';

// Export color palettes
export { COLOR_PALETTES } from './architectureShapesCatalog';

// Extend window interface for maxgraph
declare global {
    interface Window {
        maxGraph: any;
        __maxGraphLoaded?: boolean;
        __maxGraphLoading?: Promise<any>;
        __lastDrawIOGraph?: any;
    }
}

export interface DrawIOSpec {
    type: 'drawio' | 'designinspector';
    definition?: string;
    url?: string;
    isStreaming?: boolean;
    isMarkdownBlockClosed?: boolean;
    forceRender?: boolean;
    title?: string;
}

const isDrawIOSpec = (spec: any): spec is DrawIOSpec => {
    if (typeof spec !== 'object' || spec === null) return false;

    if (spec.type === 'drawio' || spec.type === 'designinspector') {
        return !!(spec.definition || spec.url);
    }

    if (typeof spec.definition === 'string') {
        return spec.definition.includes('<mxGraphModel') ||
            spec.definition.includes('<mxfile') ||
            spec.definition.includes('<diagram');
    }

    if (typeof spec.url === 'string') {
        return spec.url.includes('design-inspector.a2z.com');
    }

    return false;
};

const isDefinitionComplete = (definition: string): boolean => {
    if (!definition || definition.trim() === '') return false;

    if (definition.includes('<mxfile')) {
        return definition.includes('</mxfile>');
    }

    if (definition.includes('<mxGraphModel')) {
        return definition.includes('</mxGraphModel>');
    }

    if (definition.includes('<diagram')) {
        return definition.includes('</diagram>');
    }

    return false;
};

const normalizeDrawIOXml = (xml: string): string => {
    let normalized = xml.trim();

    // CRITICAL FIX: Clean quote characters FIRST - before any other processing
    // LLMs sometimes generate Unicode quote characters which break XML parsing
    // This MUST happen before the unquoted color fix below
    normalized = normalized
        .replace(/[\u201C\u201D]/g, '"')  // Replace " and " with "
        .replace(/[\u2018\u2019]/g, "'")  // Replace ' and ' with '
        .replace(/[\u201E\u201F]/g, '"')  // Replace „ and ‟ with "
        .replace(/[\u2039\u203A]/g, "'"); // Replace ‹ and › with '
    
    console.log('📐 DrawIO: Normalized Unicode quotes to ASCII');

    // CRITICAL FIX: Add quotes to unquoted XML attribute values at TAG LEVEL
    // This must happen BEFORE any other processing to create valid XML
    
    // Strategy: Use a more robust pattern that explicitly handles XML tag attributes
    // Pattern matches: attribute=unquotedValue where the = is preceded by whitespace/< 
    // and the value is followed by whitespace/>/;
    
    // IMPORTANT: We need to match attribute=value patterns that are NOT already inside quotes
    // The safest way is to match complete tag patterns and fix them
    
    // Fix unquoted hex colors: background=#ffffff -> background="#ffffff"
    // This pattern explicitly requires the hex color to NOT be preceded by a quote
    // Pattern: (whitespace) + attributeName + = + #hexValue + (whitespace|>|/)
    // Where the = is not preceded by " or '
    normalized = normalized.replace(/(\s)(\w+)=(#[0-9a-fA-F]{3,8})(?=[\s>\/])/g, '$1$2="$3"');
    
    // Fix unquoted hex colors without #: fillColor=dae8fc -> fillColor="dae8fc"
    normalized = normalized.replace(/(\s)(\w+[Cc]olor)=([0-9a-fA-F]{6})(?=[\s>\/])/g, '$1$2="$3"');
    
    // Fix unquoted identifier values: resIcon=mxgraph.aws4.lambda -> resIcon="mxgraph.aws4.lambda"  
    // Pattern: (whitespace) + attributeName + = + identifierValue + (whitespace|>|/)
    normalized = normalized.replace(/(\s)(\w+)=([a-zA-Z][a-zA-Z0-9_.]*)(?=[\s>\/])/g, '$1$2="$3"');
    
    // Additional fix: Handle numeric values that should be quoted
    // Match: attribute=123 -> attribute="123" (but only at tag level)
    // This handles edge cases like fontSize=14 which should be fontSize="14"
    normalized = normalized.replace(/(\s)(\w+)=(\d+)(?=[\s>\/])/g, '$1$2="$3"');
    
    // Log sample to verify the fix was applied
    if (normalized.includes('background=')) {
        const idx = normalized.indexOf('background=');
        console.log('📐 DrawIO: Background attribute:', normalized.substring(idx, idx + 25));
    }
    if (normalized.includes('fontSize=')) {
        const idx = normalized.indexOf('fontSize=');
        console.log('📐 DrawIO: FontSize attribute sample:', normalized.substring(idx, idx + 20));
    }
    
    console.log('📐 DrawIO: Added quotes to unquoted hex color attributes');

    // CRITICAL FIX: Clean ampersands BEFORE any XML parsing
    // Fix bare ampersands in attribute values (e.g., "Security & Monitoring")
    // This MUST happen before any DOMParser attempts to parse the XML
    normalized = normalized.replace(/(\w+)="([^"]*)"/g, (match, attrName, attrValue) => {
        // Replace bare ampersands but preserve valid entities
        const fixed = attrValue.replace(/&(?!(amp|lt|gt|quot|apos|#x?[0-9a-fA-F]+);)/g, '&amp;');
        return `${attrName}="${fixed}"`;
    });
    
    // Also handle single-quoted attributes
    normalized = normalized.replace(/(\w+)='([^']*)'/g, (match, attrName, attrValue) => {
        const fixed = attrValue.replace(/&(?!(amp|lt|gt|quot|apos|#x?[0-9a-fA-F]+);)/g, '&amp;');
        return `${attrName}='${fixed}'`;
    });
    
    // Fix accidentally double-escaped entities (e.g., &amp;#xa; should be &#xa;)
    normalized = normalized.replace(/&amp;(#x?[0-9a-fA-F]+;)/g, '&$1');

    console.log('📐 DrawIO: Normalized quotes and ampersands in XML');

    // Clean up any text content after closing tags (LLM sometimes adds descriptions)
    // Find the last proper closing tag (</mxfile>, </diagram>, or </mxGraphModel>)
    const lastMxfileClose = normalized.lastIndexOf('</mxfile>');
    const lastDiagramClose = normalized.lastIndexOf('</diagram>');
    const lastGraphModelClose = normalized.lastIndexOf('</mxGraphModel>');

    // If we have a closing diagram tag but content after it, truncate
    if (lastDiagramClose !== -1) {
        const afterDiagram = normalized.substring(lastDiagramClose + '</diagram>'.length).trim();
        if (afterDiagram && !afterDiagram.startsWith('</mxfile>')) {
            // Remove any text after </diagram> that isn't a closing tag
            console.log('📐 DrawIO: Removing extra content after </diagram>:', afterDiagram.substring(0, 100));
            normalized = normalized.substring(0, lastDiagramClose + '</diagram>'.length);
        }
    }

    // Ensure proper closing tags
    if (normalized.includes('<mxfile') && !normalized.includes('</mxfile>')) {
        console.log('📐 DrawIO: Adding missing </mxfile> closing tag');
        normalized = normalized + '\n</mxfile>';
    }

    if (normalized.includes('<mxGraphModel') && !normalized.includes('<mxfile')) {
        normalized = `<?xml version="1.0" encoding="UTF-8"?>
<mxfile host="ziya" modified="${new Date().toISOString()}" version="1.0">
  <diagram name="Diagram">
    ${normalized}
  </diagram>
</mxfile>`;
    }

    if (normalized.includes('<diagram') && !normalized.includes('<mxfile')) {
        normalized = `<?xml version="1.0" encoding="UTF-8"?>
<mxfile host="ziya" modified="${new Date().toISOString()}" version="1.0">
  ${normalized}
</mxfile>`;
    }

    return normalized;
};

/**
 * Lazy load maxgraph library
 */
async function loadMaxGraph(): Promise<any> {
    if (typeof window !== 'undefined' && window.__maxGraphLoaded && window.maxGraph) {
        return window.maxGraph;
    }

    if (window.__maxGraphLoading) {
        return window.__maxGraphLoading;
    }

    window.__maxGraphLoading = (async () => {
        try {
            console.log('📦 Loading @maxgraph/core...');
            const maxGraphModule = await import('@maxgraph/core');
            window.maxGraph = maxGraphModule;
            window.__maxGraphLoaded = true;
            console.log('✅ @maxgraph/core loaded successfully');
            return maxGraphModule;
        } catch (error) {
            console.error('Failed to load @maxgraph/core:', error);
            throw error;
        }
    })();

    return window.__maxGraphLoading;
}

const createControls = (container: HTMLElement, spec: DrawIOSpec, xml: string, isDarkMode: boolean, graph?: any): void => {
    const controlsDiv = document.createElement('div');
    controlsDiv.className = 'diagram-actions';

    // View Source button
    const viewSourceBtn = document.createElement('button');
    viewSourceBtn.innerHTML = '📄 Source';
    viewSourceBtn.className = 'diagram-action-button';
    viewSourceBtn.onclick = () => {
        const modal = document.createElement('div');
        modal.style.cssText = `
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: rgba(0,0,0,0.7);
            display: flex;
            align-items: center;
            justify-content: center;
            z-index: 10000;
        `;

        const content = document.createElement('div');
        content.style.cssText = `
            background: ${isDarkMode ? '#1f1f1f' : 'white'};
            padding: 24px;
            border-radius: 8px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.15);
            max-width: 800px;
            max-height: 80vh;
            overflow: auto;
        `;

        const pre = document.createElement('pre');
        pre.style.cssText = `
            margin: 0; padding: 12px; 
            background: ${isDarkMode ? '#0d1117' : '#f6f8fa'}; 
            border-radius: 4px; overflow: auto; font-family: monospace; font-size: 12px;
            color: ${isDarkMode ? '#e6e6e6' : '#24292e'};`;
        pre.textContent = xml;

        const closeBtn = document.createElement('button');
        closeBtn.textContent = 'Close';
        closeBtn.style.cssText = `padding: 8px 16px; background: #1890ff; color: white; border: none; border-radius: 4px; cursor: pointer; width: 100%; margin-top: 12px;`;
        closeBtn.onclick = () => modal.remove();

        content.innerHTML = `<h3 style="margin-top: 0; color: ${isDarkMode ? '#e6e6e6' : '#24292e'};">📄 DrawIO XML Source</h3>`;
        content.appendChild(pre);
        content.appendChild(closeBtn);
        modal.appendChild(content);
        modal.onclick = (e) => { if (e.target === modal) modal.remove(); };
        document.body.appendChild(modal);
    };

    // Edit button - DISABLED: External site access is a privacy/security concern
    // This opens diagrams.net which could expose private data
    // TODO: Implement local editing capability or make this opt-in via user preferences
    /*
    const editBtn = document.createElement('button');
    editBtn.innerHTML = '✏️ Edit';
    editBtn.className = 'diagram-action-button';
    editBtn.title = 'Open in diagrams.net editor (external site)';
    editBtn.onclick = () => {
        const encoded = encodeURIComponent(xml);
        const title = encodeURIComponent(spec.title || 'diagram');
        const url = 'https://app.diagrams.net/?title=' + title + '#R' + encoded;
        // WARNING: This sends diagram data to external site
        // Only enable if user explicitly opts in via preferences
        window.open(url, '_blank');
    };
    */

    // Download button - saves locally as .drawio file
    const exportBtn = document.createElement('button');
    exportBtn.innerHTML = '⬇️ Download';
    exportBtn.className = 'diagram-action-button';
    exportBtn.title = 'Download as .drawio file';
    exportBtn.onclick = () => {
        let exportXml = xml;
        if (graph) {
            try {
                const { Codec } = window.maxGraph;
                const codec = new Codec();
                const model = graph.getModel();
                const node = codec.encode(model);

                const xmlDoc = document.implementation.createDocument(null, 'mxfile', null);
                const mxfile = xmlDoc.documentElement;
                mxfile.setAttribute('host', 'ziya');
                mxfile.setAttribute('modified', new Date().toISOString());
                mxfile.setAttribute('version', '1.0');

                const diagram = xmlDoc.createElement('diagram');
                diagram.setAttribute('name', spec.title || 'Diagram');
                diagram.appendChild(node);
                mxfile.appendChild(diagram);

                exportXml = new XMLSerializer().serializeToString(xmlDoc);
            } catch (e) {
                console.warn('Failed to export restyled version, using original:', e);
            }
        }
        const blob = new Blob([exportXml], { type: 'application/xml' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        const filename = (spec.title?.replace(/[^a-z0-9]/gi, '_') || 'diagram') + '.drawio';
        a.download = filename;
        a.click();
        URL.revokeObjectURL(url);
    };

    // Copy to clipboard button - useful for pasting into other tools
    const copyBtn = document.createElement('button');
    copyBtn.innerHTML = '📋 Copy';
    copyBtn.className = 'diagram-action-button';
    copyBtn.title = 'Copy XML to clipboard';
    copyBtn.onclick = async () => {
        let exportXml = xml;
        if (graph) {
            try {
                const { Codec } = window.maxGraph;
                const codec = new Codec();
                const model = graph.getModel();
                const node = codec.encode(model);

                // Wrap in proper DrawIO XML structure
                const xmlDoc = document.implementation.createDocument(null, 'mxfile', null);
                const mxfile = xmlDoc.documentElement;
                mxfile.setAttribute('host', 'ziya');
                mxfile.setAttribute('modified', new Date().toISOString());
                mxfile.setAttribute('version', '1.0');

                const diagram = xmlDoc.createElement('diagram');
                diagram.setAttribute('name', spec.title || 'Diagram');
                diagram.appendChild(node);
                mxfile.appendChild(diagram);

                exportXml = new XMLSerializer().serializeToString(xmlDoc);
            } catch (e) {
                console.warn('Failed to export restyled version, using original:', e);
            }
        }

        try {
            await navigator.clipboard.writeText(exportXml);
            const originalText = copyBtn.innerHTML;
            copyBtn.innerHTML = '✅ Copied';
            setTimeout(() => {
                copyBtn.innerHTML = originalText;
            }, 2000);
        } catch (err) {
            console.error('Failed to copy to clipboard:', err);
            copyBtn.innerHTML = '❌ Failed';
            setTimeout(() => {
                copyBtn.innerHTML = '📋 Copy';
            }, 2000);
        }
    };

    controlsDiv.appendChild(viewSourceBtn);
    // controlsDiv.appendChild(editBtn); // Disabled - external site privacy concern
    controlsDiv.appendChild(exportBtn);
    controlsDiv.appendChild(copyBtn);

    container.appendChild(controlsDiv);
};

const renderDrawIO = async (container: HTMLElement, _d3: any, spec: DrawIOSpec, isDarkMode: boolean): Promise<void> => {
    // Store the render function for retry capability
    console.log('📐 DrawIO: renderDrawIO called');

    // CRITICAL: Mark container to prevent clearing during re-renders
    (container as any).__drawioRendered = true;

    const attemptRender = async () => {
        // Clear previous content
        if (!(container as any).__drawioContentReady) {
            container.innerHTML = '';
        }

        if (spec.isStreaming && !spec.forceRender && spec.definition && !isDefinitionComplete(spec.definition)) {
            container.innerHTML = '<div style="padding: 16px; text-align: center; color: #888;">📐 Drawing diagram...</div>';
            return;
        }

        const xml = spec.definition ? (() => {
            console.log('📐 DrawIO: About to normalize XML, length:', spec.definition.length);
            return normalizeDrawIOXml(spec.definition);
        })() : null;

        if (!xml && !spec.url) {
            container.innerHTML = '<div style="padding: 16px; color: #cf1322;">⚠️ No diagram content provided</div>';
            return;
        }

        // If this diagram uses catalog shapes, ensure stencils are loaded
        if (xml) {
            const shapeIds = extractShapeIdsFromXml(xml);
            if (shapeIds.length > 0) {
                console.log('📦 Loading stencils for shapes:', shapeIds);
                try {
                    await loadStencilsForShapes(shapeIds);
                    console.log('✅ Stencils loaded');
                } catch (stencilError) {
                    console.warn('⚠️ Could not load stencils, shapes may render as boxes:', stencilError);
                }
            }
        }

        try {
            // Lazy load maxgraph
            console.log('📐 DrawIO: About to load maxGraph');
            const maxGraphModule = await loadMaxGraph();
            console.log('📐 DrawIO: maxGraph loaded, module keys:', Object.keys(maxGraphModule).slice(0, 10));

            // CRITICAL FIX: Override arrow size constants BEFORE any rendering
            // MaxGraph uses Constants.ARROW_SIZE (default 30) for arrow head sizing
            // We must override this constant and the marker creation function
            if (!maxGraphModule.MarkerShape.__arrowSizeOverridden) {
                console.log('📐 DrawIO: Installing arrow size overrides for smaller arrows');

                // Override the Constants.ARROW_SIZE global constant
                if (maxGraphModule.Constants) {
                    const originalArrowSize = maxGraphModule.Constants.ARROW_SIZE;
                    maxGraphModule.Constants.ARROW_SIZE = 6;
                    console.log(`📐 DrawIO: Changed Constants.ARROW_SIZE from ${originalArrowSize} to 6`);
                }

                // Override the marker creation function as backup
                const originalCreateMarker = maxGraphModule.MarkerShape.createMarker;
                maxGraphModule.MarkerShape.createMarker = function(canvas, shape, type, pe, dx, dy, size, source, sw, filled) {
                    // Force arrow size to 6 (override any provided size)
                    // Log all parameters to understand what's happening
                    if (!this.__loggedOnce) {
                        console.log(`🎯 createMarker called:`, {
                            size, type, source, sw, filled,
                            pe: pe?.toString().substring(0, 50),
                            dx, dy
                        });
                        this.__loggedOnce = true;
                    }
                    // FORCE size to 1.4 (6 / 4.27 to compensate for the scaling)
                    const customSize = 1.4;
                    return originalCreateMarker.call(this, canvas, shape, type, pe, dx, dy, customSize, source, sw, filled);
                };

                maxGraphModule.MarkerShape.__arrowSizeOverridden = true;
            }

            if (!maxGraphModule.Graph) throw new Error('maxGraph.Graph not found in module');

            // Import Graph and Codec from maxgraph
            const { Graph, Codec, Cell, Geometry, Point } = maxGraphModule;

            // Create controls
            // Note: Controls created later after graph is rendered so we can export restyled version

            // Create container for the graph
            const graphContainer = document.createElement('div');
            graphContainer.style.cssText = `
            position: relative;
            width: 100%;
            min-height: 600px;
            background: ${isDarkMode ? '#0d1117' : '#ffffff'};
            border: 1px solid ${isDarkMode ? '#30363d' : '#d0d7de'};
            overflow: hidden;
        `;

            // Parse the XML
            // CRITICAL FIX: Clean up common XML syntax errors before parsing
            let cleanedXml = xml!;

            // Note: Quote normalization and ampersand fixing now happens in normalizeDrawIOXml()
            // which runs earlier on line 362: const xml = spec.definition ? normalizeDrawIOXml(spec.definition) : null;

            // Additional safety: fix any double-escaped entities that might result
            // This handles cases where &#xa; might have been escaped to &amp;#xa;
            cleanedXml = cleanedXml.replace(/&amp;(#[0-9]+;)/g, '&$1');
            cleanedXml = cleanedXml.replace(/&amp;(#x[0-9a-fA-F]+;)/g, '&$1');

            console.log('📐 DrawIO: Fixed ampersands sample:', cleanedXml.substring(cleanedXml.indexOf('Security'), cleanedXml.indexOf('Security') + 50));

            // NOTE: Legacy quote removal code removed - it was undoing our earlier normalization
            // The normalizeDrawIOXml function now handles all quote fixing properly

            console.log('📐 DrawIO: Cleaned XML preview:', cleanedXml.substring(0, 500));

            // Now parse the cleaned XML
            const parserX = new DOMParser();
            const xmlDocX = parserX.parseFromString(cleanedXml, 'application/xml');

            // Check for parsing errors
            const parseErrorX = xmlDocX.querySelector('parsererror');
            if (parseErrorX) {
                throw new Error('Invalid DrawIO XML: ' + parseErrorX.textContent);
            }

            console.log('📐 DrawIO: Parsed XML document:', xmlDocX.documentElement?.tagName);

            // Extract the diagram definition
            let diagramNode = xmlDocX.querySelector('diagram');
            let graphXmlContent: string | null = null;

            // If we have a diagram node, extract its content
            if (diagramNode) {
                const diagramContent = diagramNode.textContent || diagramNode.innerHTML;
                if (diagramContent) {
                    console.log('📐 DrawIO: Found diagram content, length:', diagramContent.length);
                    // Decode if it's base64 encoded (common in DrawIO files)
                    try {
                        const decoded = atob(diagramContent.trim());
                        console.log('📐 DrawIO: Base64 decoded, length:', decoded.length);

                        // Decompress if needed (DrawIO often compresses)
                        if (decoded && decoded.length > 0) {
                            try {
                                // Try URL decoding first
                                const decompressed = decodeURIComponent(decoded);
                                console.log('📐 DrawIO: URL decompressed content, length:', decompressed.length);
                                graphXmlContent = decompressed;
                            } catch (e) {
                                // If URL decode fails, try pako decompression (zlib)
                                try {
                                    const pako = await import('pako');
                                    const decompressed = pako.inflateRaw(decoded, { to: 'string' });
                                    console.log('📐 DrawIO: Pako decompressed content, length:', decompressed.length);
                                    graphXmlContent = decompressed;
                                } catch (pakoError) {
                                    // Not compressed, use decoded directly
                                    console.log('📐 DrawIO: Content not compressed, using decoded directly');
                                    graphXmlContent = decoded;
                                }
                            }
                        } else {
                            console.warn('📐 DrawIO: Decoded content is empty, skipping');
                            throw new Error('Empty diagram content after base64 decode');
                        }
                    } catch (e) {
                        // Not base64 encoded, use raw content as-is
                        console.log('📐 DrawIO: Content not base64, using raw diagram content');
                        graphXmlContent = diagramContent;
                    }

                    console.log('📐 DrawIO: Final decoded content preview:', graphXmlContent?.substring(0, 200) || 'EMPTY');
                }
            } else {
                // No diagram wrapper, check if we already have mxGraphModel at root
                const rootModel = xmlDoc.querySelector('mxGraphModel');
                console.log('📐 DrawIO: No diagram node, rootModel exists?', !!rootModel);
                if (rootModel) {
                    // Use the entire XML as-is
                    graphXmlContent = xml!;
                }
            }

            // If we still don't have content, fall back to original XML
            if (!graphXmlContent || graphXmlContent.trim() === '') {
                console.warn('📐 DrawIO: No graph content extracted, using original XML');
                graphXmlContent = xml!;
            }

            console.log('📐 DrawIO: Final XML to import, length:', graphXmlContent.length);
            console.log('📐 DrawIO: XML preview:', graphXmlContent.substring(0, 300));
            // Create graph
            console.log('📐 DrawIO: Creating Graph instance');
            const graph = new Graph(graphContainer);

            // Store for debugging
            window.__lastDrawIOGraph = graph;

            // Disable tree images to avoid 404s - use CSS styling instead
            if (maxGraphModule.Constants) {
                maxGraphModule.Constants.STYLE_IMAGE = null;
            }
            graph.collapsedImage = null;
            graph.expandedImage = null;

            // Configure graph for read-only viewing
            graph.setEnabled(false); // Disable editing
            graph.setHtmlLabels(true); // Enable HTML labels for better text rendering
            graph.centerZoom = true;
            graph.setTooltips(true);
            graph.autoSizeCells = true; // Ensure labels are sized properly
            graph.setConnectable(false); // Read-only mode
            
            // CRITICAL: Configure proper orthogonal routing like diagrams.net
            // Override updateFixedTerminalPoint for smart connection points
            graph.view.updateFixedTerminalPoint = function(edge: any, terminal: any, source: any, constraint: any) {
                // Call parent implementation first
                const View = maxGraphModule.GraphView;
                View.prototype.updateFixedTerminalPoint.apply(this, arguments);
                
                // Use routing center for cleaner connection points
                const pts = edge.absolutePoints;
                const pt = pts[source ? 0 : pts.length - 1];
                
                if (terminal != null && pt == null) {
                    edge.setAbsoluteTerminalPoint(
                        new maxGraphModule.Point(
                            this.getRoutingCenterX(terminal),
                            this.getRoutingCenterY(terminal)
                        ),
                        source
                    );
                }
            };


            // CRITICAL: Configure stylesheet defaults BEFORE adding any cells
            // This prevents maxGraph's huge default arrow sizes from being used
            console.log('📐 DrawIO: Configuring stylesheet defaults early');
            const stylesheet = graph.getStylesheet();

            // Configure default edge style with reasonable arrow sizes
            const defaultEdgeStyle = stylesheet.getDefaultEdgeStyle();
            defaultEdgeStyle['edgeStyle'] = 'orthogonalEdgeStyle';
            defaultEdgeStyle['rounded'] = 1;
            defaultEdgeStyle['curved'] = 0;
            defaultEdgeStyle['endArrow'] = 'classic';
            defaultEdgeStyle['endSize'] = 6; // Small arrow heads (6px instead of default ~20-30px)
            defaultEdgeStyle['startArrow'] = 'none';
            defaultEdgeStyle['startSize'] = 6;
            defaultEdgeStyle['strokeWidth'] = 1;

            // Edge label defaults for readability
            defaultEdgeStyle['labelBackgroundColor'] = '#ffffff';
            defaultEdgeStyle['labelBorderColor'] = '#333333';
            defaultEdgeStyle['align'] = 'center';
            defaultEdgeStyle['verticalAlign'] = 'middle';
            defaultEdgeStyle['spacingTop'] = 6;
            defaultEdgeStyle['spacingBottom'] = 6;
            defaultEdgeStyle['spacingLeft'] = 10;
            defaultEdgeStyle['spacingRight'] = 10;

            stylesheet.putDefaultEdgeStyle(defaultEdgeStyle);

            // Configure default vertex style
            const defaultVertexStyle = stylesheet.getDefaultVertexStyle();
            defaultVertexStyle['fontColor'] = '#000000';
            defaultVertexStyle['fontSize'] = 12;
            stylesheet.putDefaultVertexStyle(defaultVertexStyle);

            console.log('📐 DrawIO: Graph created');

            // Find the mxGraphModel node (NOT the mxfile wrapper)
            const parser = new DOMParser();
            const xmlDoc = parser.parseFromString(graphXmlContent!, 'text/xml');

            // Check for parsing errors
            const parseError = xmlDoc.querySelector('parsererror');
            if (parseError) {
                throw new Error('Invalid DrawIO XML after decode: ' + parseError.textContent);
            }

            // Find the mxGraphModel - it should be the direct child or nested in diagram
            let modelNode = xmlDoc.querySelector('mxGraphModel');
            if (!modelNode) {
                // Try looking in diagram wrapper
                const diagramElement = xmlDoc.querySelector('diagram');
                if (diagramElement) {
                    const innerDoc = parser.parseFromString(diagramElement.textContent || '', 'text/xml');
                    modelNode = innerDoc.querySelector('mxGraphModel');
                }
            }

            if (!modelNode) {
                throw new Error('No mxGraphModel found in decoded XML');
            }

            console.log('📐 DrawIO: Found mxGraphModel element, importing into graph');
            console.log('📐 DrawIO: mxGraphModel children:', modelNode.children.length);

            // Create codec - pass the owner document for proper node creation
            const codec = new Codec(modelNode.ownerDocument);
            const model = graph.model;

            // Import using the mxGraph pattern: decode into the graph's model
            model.beginUpdate();
            try {
                // Get the root element which contains all the cells
                const rootElement = modelNode.querySelector('root');
                if (!rootElement) {
                    throw new Error('No root element found in mxGraphModel');
                }

                // Get all mxCell elements
                const cellElements = rootElement.querySelectorAll('mxCell');
                console.log('📐 DrawIO: Found', cellElements.length, 'cell elements in XML');

                if (cellElements.length === 0) {
                    throw new Error('No cells found in diagram - XML may be malformed');
                }

                // Build maps and collections for proper ordering
                const cellMap = new Map<string, any>();
                const vertexCells: Array<{ id: string, element: Element }> = [];
                const edgeCells: Array<{ id: string, element: Element }> = [];

                // First pass: decode all cells and categorize them
                // We need to add vertices before edges for proper z-ordering
                cellElements.forEach((cellElement) => {
                    const cellId = cellElement.getAttribute('id');
                    console.log('📐 DEBUG: Processing cell', cellId);
                    if (cellId) {
                        // Get cell attributes from XML
                        const value = cellElement.getAttribute('value') || '';
                        let style = cellElement.getAttribute('style') || '';
                        const vertex = cellElement.getAttribute('vertex') === '1';
                        const edge = cellElement.getAttribute('edge') === '1';
                        const parent = cellElement.getAttribute('parent');
                        const source = cellElement.getAttribute('source');
                        const target = cellElement.getAttribute('target');

                        console.log('📐 DEBUG: Cell', cellId, 'style string:', style);

                        // Create a proper Cell object
                        const cell = new Cell(value);
                        cell.setId(cellId);
                        cell.setVertex(vertex);
                        cell.setEdge(edge);

                        // Parse and modify style string
                        if (style) {
                            // CRITICAL: maxGraph 0.11+ requires style OBJECTS, not strings
                            // Detect special styles that need preprocessing
                            const isSwimlane = style.includes('swimlane');
                            const isCurved = style.includes('curved=1') || style.includes('rounded=1');

                            // For swimlanes, ensure we have proper label positioning
                            if (isSwimlane && !style.includes('startSize=')) {
                                // Add default startSize for swimlane label area (26px is standard)
                                style = style + ';startSize=26';
                            }

                            // Parse style string into proper object format
                            const styleObj: Record<string, any> = {};
                            style.split(';').forEach(pair => {
                                const trimmedPair = pair.trim();
                                if (!trimmedPair) return;

                                if (trimmedPair.includes('=')) {
                                    const [key, value] = trimmedPair.split('=');
                                    if (key && value !== undefined && value !== '') {
                                        styleObj[key.trim()] = value.trim();
                                    }
                                } else {
                                    // Handle shape names without values (ellipse, rhombus, etc.)
                                    if (['ellipse', 'rhombus', 'triangle', 'cylinder', 'hexagon', 'cloud', 'actor', 'parallelogram'].includes(trimmedPair)) {
                                        styleObj['shape'] = trimmedPair;
                                    } else {
                                        // For flags like 'html', 'rounded', set them as boolean-like
                                        styleObj[trimmedPair] = 1;
                                    }
                                }
                            });

                            // Fix arrow sizes for edges
                            if (edge) {
                                // Edge label positioning fixes
                                styleObj['labelBackgroundColor'] = '#ffffff';
                                styleObj['labelBorderColor'] = '#cccccc';
                                styleObj['align'] = 'center';
                                styleObj['verticalAlign'] = 'middle';

                                // Ensure edge labels are readable with padding
                                styleObj['spacingTop'] = 2;
                                styleObj['spacingBottom'] = 2;
                                styleObj['spacingLeft'] = 4;
                                styleObj['spacingRight'] = 4;

                                // Arrow size fixes
                                styleObj['endSize'] = 6;
                                styleObj['startSize'] = 6;

                                // Handle curved edges properly
                                if (isCurved || styleObj['curved'] || styleObj['rounded']) {
                                    styleObj['curved'] = 1;
                                    styleObj['rounded'] = 1;
                                }
                            }

                            // Swimlane label positioning fixes
                            if (vertex && !styleObj['fontColor']) {
                                // Set appropriate font color based on fill color
                                const fillColor = styleObj['fillColor'];
                                if (fillColor && fillColor !== 'none') {
                                    styleObj['fontColor'] = getOptimalTextColor(fillColor);
                                } else {
                                    // No fill color - use theme-based default
                                    styleObj['fontColor'] = isDarkMode ? '#e0e0e0' : '#000000';
                                }
                            } else if (vertex && styleObj['fontColor'] === '#000000' && isDarkMode) {
                                // Check background color before overriding
                                const fillColor = styleObj['fillColor'];
                                if (fillColor && fillColor !== 'none') {
                                    // Only override if the background is dark
                                    if (!isLightBackground(fillColor)) {
                                        styleObj['fontColor'] = getOptimalTextColor(fillColor);
                                    }
                                } else {
                                    // No fill color - use light text in dark mode
                                    styleObj['fontColor'] = '#e0e0e0';
                                }
                            } else if (vertex && styleObj['fontColor']) {
                                // Check if existing font color has good contrast with fill
                                const fillColor = styleObj['fillColor'];
                                const fontColor = styleObj['fontColor'];

                                if (fillColor && fillColor !== 'none' && fontColor) {
                                    // Check if we need to adjust for better contrast
                                    const fillRgb = hexToRgb(fillColor);
                                    const fontRgb = hexToRgb(fontColor);

                                    if (fillRgb && fontRgb) {
                                        // If both colors are light or both are dark, fix it
                                        const fillIsLight = isLightBackground(fillColor);
                                        const fontIsLight = isLightBackground(fontColor);

                                        if (fillIsLight === fontIsLight) {
                                            styleObj['fontColor'] = getOptimalTextColor(fillColor);
                                        }
                                    }
                                }
                            } else if (vertex && styleObj['fillColor']) {
                                if (isSwimlane) {
                                    // Swimlane labels should be at the top, not centered
                                    styleObj['verticalAlign'] = 'top';
                                    styleObj['align'] = 'center';
                                    styleObj['spacingTop'] = 8;
                                    styleObj['fontSize'] = styleObj['fontSize'] || 12;
                                    styleObj['fontStyle'] = styleObj['fontStyle'] || 1; // Bold

                                    // Ensure label area is visible
                                    if (!styleObj['startSize']) {
                                        styleObj['startSize'] = 35;
                                    }

                                    // Make swimlane backgrounds semi-transparent to see contents
                                    if (!styleObj['fillColor'].includes('opacity')) {
                                        // Keep the color but make it lighter
                                        const color = styleObj['fillColor'];
                                        if (color.startsWith('#')) {
                                            styleObj['fillOpacity'] = 20; // 20% opacity
                                        }
                                    }
                                }
                            }

                            // Set style as OBJECT, not string (maxGraph 0.11+ requirement)
                            cell.setStyle(styleObj);

                            console.log('📐 DEBUG: Set style object for cell', cellId, ':', styleObj);
                        } else {
                            cell.setStyle({});
                        }

                        // Get geometry if it exists
                        const geometryElement = cellElement.querySelector('mxGeometry');
                        if (geometryElement) {
                            const x = parseFloat(geometryElement.getAttribute('x') || '0');
                            const y = parseFloat(geometryElement.getAttribute('y') || '0');
                            const width = parseFloat(geometryElement.getAttribute('width') || '0');
                            const height = parseFloat(geometryElement.getAttribute('height') || '0');

                            const geometry = new Geometry(x, y, width, height);

                            // For edges, parse waypoints
                            if (edge) {
                                const relative = geometryElement.getAttribute('relative');
                                if (relative === '1') {
                                    geometry.relative = true;
                                }
                            }

                            cell.setGeometry(geometry);

                            console.log(`📐 DrawIO: Created cell ${cellId} with geometry:`, { x, y, width, height });
                        } else {
                            console.log(`📐 DrawIO: Created cell ${cellId} (no geometry)`);
                        }

                        cellMap.set(cellId, cell);

                        // Categorize for ordered addition
                        if (edge) {
                            edgeCells.push({ id: cellId, element: cellElement });
                        } else if (vertex) {
                            vertexCells.push({ id: cellId, element: cellElement });
                        }
                    }
                });
                console.log('📦 Applying loaded icons to cells');
                for (const [cellId, cell] of cellMap.entries()) {
                    if (!cell.isVertex()) continue;

                    const style = cell.getStyle();
                    if (!style || typeof style !== 'object') continue;

                    // Check if this cell uses mxgraph.aws4 resource icons
                    // Icons can be in either 'resIcon' or 'shape' properties
                    const resIcon = style['resIcon'] || style['shape'];

                    if (resIcon && typeof resIcon === 'string' && resIcon.startsWith('mxgraph.aws4.')) {
                        // Extract service name: mxgraph.aws4.api_gateway -> api_gateway
                        const serviceName = resIcon.replace('mxgraph.aws4.', '');
                        console.log(`📦 Cell ${cellId} needs icon: ${serviceName}`);

                        // Get icon from registry
                        const iconDataUri = await iconRegistry.getIconAsDataUri('aws', serviceName);
                        if (iconDataUri) {
                            // Set as image on the cell
                            style['image'] = iconDataUri;
                            style['shape'] = 'image';

                            // CRITICAL FIX: Set proper label positioning for AWS icons
                            // Without these, labels render far to the right instead of below the icon
                            style['verticalLabelPosition'] = 'bottom';
                            style['verticalAlign'] = 'top';
                            style['align'] = 'center';
                            style['imageAlign'] = 'center';
                            style['imageVerticalAlign'] = 'top';
                            style['spacingTop'] = 5;

                            console.log(`🔧 LABEL-FIX: Applied label positioning for AWS icon ${cellId}`, {
                                serviceName,
                                labelPosition: 'bottom-center'
                            });

                            cell.setStyle(style);
                            console.log(`✅ Applied icon to cell ${cellId}`);
                        }
                    }
                }

                // Get the model's default root cells - these MUST NOT be re-added
                // In maxGraph, cells 0 and 1 are special root cells created automatically
                const modelRoot = model.getRoot(); // This is cell 0

                // Cell 1 is the default parent (first child of root)
                let defaultParent = null;
                if (modelRoot && modelRoot.children && modelRoot.children.length > 0) {
                    defaultParent = modelRoot.children[0]; // Cell 1
                } else {
                    // Fallback: use graph's default parent
                    defaultParent = graph.getDefaultParent();
                }

                console.log('📐 DrawIO: Model root cells:', {
                    rootId: modelRoot?.getId(),
                    defaultParentId: defaultParent?.getId()
                });

                // Replace our created cells 0 and 1 with the model's existing ones
                if (cellMap.has('0')) cellMap.set('0', modelRoot);
                if (cellMap.has('1')) cellMap.set('1', defaultParent);

                // Build edge direction and bidirectional pair maps
                const vertexEdgeDirections = new Map<string, {
                    incoming: string[],
                    outgoing: string[]
                }>();
                const edgePairs = new Map<string, string[]>();

                edgeCells.forEach(({ id, element }) => {
                    const sourceId = element.getAttribute('source');
                    const targetId = element.getAttribute('target');

                    if (sourceId && targetId) {
                        // Track edge directions
                        if (!vertexEdgeDirections.has(sourceId)) {
                            vertexEdgeDirections.set(sourceId, { incoming: [], outgoing: [] });
                        }
                        vertexEdgeDirections.get(sourceId)!.outgoing.push(id);

                        if (!vertexEdgeDirections.has(targetId)) {
                            vertexEdgeDirections.set(targetId, { incoming: [], outgoing: [] });
                        }
                        vertexEdgeDirections.get(targetId)!.incoming.push(id);

                        // Track bidirectional pairs (A↔B)
                        const pairKey = sourceId < targetId ? `${sourceId}-${targetId}` : `${targetId}-${sourceId}`;
                        if (!edgePairs.has(pairKey)) edgePairs.set(pairKey, []);
                        edgePairs.get(pairKey)!.push(id);
                    }
                });

                console.log('📐 DrawIO: Edge analysis:', {
                    verticesWithBothDirections: Array.from(vertexEdgeDirections.entries())
                        .filter(([_, dirs]) => dirs.incoming.length > 0 && dirs.outgoing.length > 0)
                        .map(([vId, dirs]) => ({ vId, in: dirs.incoming.length, out: dirs.outgoing.length })),
                    bidirectionalPairs: Array.from(edgePairs.entries()).filter(([_, edges]) => edges.length > 1)
                });

                // Handle bidirectional pairs separately (they need offset)
                console.log('📐 DrawIO: Processing bidirectional edge pairs');
                edgeCells.forEach(({ id, element }) => {
                    const cell = cellMap.get(id);
                    if (!cell) return;

                    const sourceId = element.getAttribute('source');
                    const targetId = element.getAttribute('target');
                    if (!sourceId || !targetId) return;

                    // Check if this is a bidirectional pair (A↔B)
                    const pairKey = sourceId < targetId ? `${sourceId}-${targetId}` : `${targetId}-${sourceId}`;
                    const pairEdges = edgePairs.get(pairKey) || [];

                    if (pairEdges.length === 2) {
                        // Bidirectional pair - offset to separate
                        const sourceCell = cellMap.get(sourceId);
                        const targetCell = cellMap.get(targetId);
                        if (!sourceCell || !targetCell) return;

                        const sourceGeom = sourceCell.getGeometry();
                        const targetGeom = targetCell.getGeometry();
                        if (sourceGeom && targetGeom) {
                            const dx = targetGeom.x + targetGeom.width / 2 - (sourceGeom.x + sourceGeom.width / 2);
                            const dy = targetGeom.y + targetGeom.height / 2 - (sourceGeom.y + sourceGeom.height / 2);
                            const isHorizontal = Math.abs(dx) > Math.abs(dy);
                            const edgeIndex = pairEdges.indexOf(id);
                            // Use offset for visual separation
                            const offset = edgeIndex === 0 ? -0.2 : 0.2;

                            const currentStyle = cell.getStyle();

                            if (isHorizontal) {
                                // For horizontal flow, offset vertically
                                // One arrow goes above center, one below
                                currentStyle['exitY'] = 0.5 + offset;
                                currentStyle['entryY'] = 0.5 + offset;
                                // Keep X centered on left/right edges
                                currentStyle['exitX'] = dx > 0 ? 1.0 : 0.0;
                                currentStyle['entryX'] = dx > 0 ? 0.0 : 1.0;
                            } else {
                                // For vertical flow, offset horizontally
                                currentStyle['exitX'] = 0.5 + offset;
                                currentStyle['entryX'] = 0.5 + offset;
                                // Keep Y centered on top/bottom edges
                                currentStyle['exitY'] = dy > 0 ? 1.0 : 0.0;
                                currentStyle['entryY'] = dy > 0 ? 0.0 : 1.0;
                            }
                            cell.setStyle(currentStyle);

                            console.log(`📐 PRE: Bidirectional pair ${id} offset by ${offset}`, {
                                direction: isHorizontal ? 'horizontal' : 'vertical'
                            });
                        }
                    }
                });

                // Separate swimlanes/containers from regular vertices for proper z-ordering
                const swimlaneVertices = vertexCells.filter(({ id }) => {
                    const cell = cellMap.get(id);
                    const style = cell?.getStyle();
                    return style && (style['swimlane'] || style['container']);
                });
                const regularVertices = vertexCells.filter(({ id }) => !swimlaneVertices.find(v => v.id === id));

                // Build ordered list: swimlanes → vertices → edges
                const nonRootIds = [...swimlaneVertices, ...regularVertices, ...edgeCells].map(item => item.id).filter(id => id !== '0' && id !== '1');

                nonRootIds.forEach(id => {
                    const cell = cellMap.get(id);
                    const cellElement = Array.from(cellElements).find(el => el.getAttribute('id') === id);

                    if (cell) {
                        // Look up parent from XML
                        const parentId = cellElement?.getAttribute('parent');
                        const parentCell = parentId ? cellMap.get(parentId) : defaultParent;

                        console.log(`📐 DrawIO: Adding cell ${id} to parent ${parentCell?.getId()}`);
                        graph.addCell(cell, parentCell);

                        // After adding the cell, set up edge connections if this is an edge
                        if (cell.isEdge()) {
                            const sourceId = cellElement?.getAttribute('source');
                            const targetId = cellElement?.getAttribute('target');
                            if (sourceId && targetId) {
                                const sourceCell = cellMap.get(sourceId);
                                const targetCell = cellMap.get(targetId);
                                cell.setTerminal(sourceCell, true);  // true = source
                                cell.setTerminal(targetCell, false); // false = target

                                // CRITICAL: Apply connection points from style to geometry
                                // MaxGraph needs these on the geometry, not just in style
                                const style = cell.getStyle();
                                const geometry = cell.getGeometry();

                                if (style && geometry && typeof style === 'object') {
                                    // Set terminal points on geometry if style has connection points
                                    if (style['exitX'] !== undefined && style['exitY'] !== undefined) {
                                        const sourcePoint = new Point(
                                            parseFloat(style['exitX']),
                                            parseFloat(style['exitY'])
                                        );
                                        geometry.setTerminalPoint(sourcePoint, true); // true = source
                                        console.log(`📐 GEOM: Set source point for ${id}: [${style['exitX']}, ${style['exitY']}]`);
                                    }

                                    if (style['entryX'] !== undefined && style['entryY'] !== undefined) {
                                        const targetPoint = new Point(
                                            parseFloat(style['entryX']),
                                            parseFloat(style['entryY'])
                                        );
                                        geometry.setTerminalPoint(targetPoint, false); // false = target
                                        console.log(`📐 GEOM: Set target point for ${id}: [${style['entryX']}, ${style['entryY']}]`);
                                    }

                                    // Update the cell's geometry
                                    cell.setGeometry(geometry);
                                }
                            }
                        }
                    }
                });

                // PLACEMENT OPTIMIZATION: Reorder shapes within containers to minimize crossings
                console.log('📐 PLACEMENT: Optimizing shape positions within containers');

                // Group vertices by parent and Y-position (row)
                const containerRows = new Map<string, Map<number, Array<{ id: string, cell: any, geom: any }>>>();

                vertexCells.forEach(({ id }) => {
                    const cell = cellMap.get(id);
                    if (!cell) return;
                    const geom = cell.getGeometry();
                    if (!geom) return;

                    // Skip containers themselves
                    const style = cell.getStyle();
                    if (style && (style['swimlane'] || style['container'])) return;

                    const parent = cell.getParent();
                    const parentId = parent?.getId() || 'root';

                    if (!containerRows.has(parentId)) {
                        containerRows.set(parentId, new Map());
                    }
                    const rows = containerRows.get(parentId)!;

                    const rowKey = Math.round(geom.y / 30) * 30; // Group within 30px
                    if (!rows.has(rowKey)) rows.set(rowKey, []);
                    rows.get(rowKey)!.push({ id, cell, geom });
                });

                // Optimize each row in each container
                containerRows.forEach((rows, parentId) => {
                    rows.forEach((rowShapes, rowY) => {
                        if (rowShapes.length <= 1) return;

                        // Calculate optimal X position for each shape based on connections
                        const optimalX = new Map<string, number>();

                        rowShapes.forEach(({ id, cell }) => {
                            const xPositions: number[] = [];
                            const weights: number[] = [];

                            // Find all edges and calculate where they connect
                            edgeCells.forEach(({ element }) => {
                                const sourceId = element.getAttribute('source');
                                const targetId = element.getAttribute('target');

                                if (sourceId === id && targetId) {
                                    const target = cellMap.get(targetId);
                                    if (target) {
                                        // Calculate absolute X
                                        let absX = 0;
                                        let current = target;
                                        while (current && current.getId() !== '0') {
                                            const g = current.getGeometry();
                                            if (g) {
                                                // For calculating position, use center X
                                                if (current === target) {
                                                    absX += g.x + g.width / 2;
                                                } else {
                                                    // Parent container offset
                                                    absX += g.x;
                                                }
                                            }
                                            current = current.getParent();
                                            if (current && (current.getId() === '0' || current.getId() === '1')) break;
                                        }

                                        // Weight vertical connections more heavily (they benefit most from alignment)
                                        const targetParent = target.getParent()?.getId();
                                        const sourceParent = cell.getParent()?.getId();
                                        const weight = targetParent !== sourceParent ? 3.0 : 1.0; // Cross-container edges weighted 3x

                                        xPositions.push(absX);
                                        weights.push(weight);
                                    }
                                } else if (targetId === id && sourceId) {
                                    const source = cellMap.get(sourceId);
                                    if (source) {
                                        let absX = 0;
                                        let current = source;
                                        while (current && current.getId() !== '0') {
                                            const g = current.getGeometry();
                                            if (g) {
                                                if (current === source) {
                                                    absX += g.x + g.width / 2;
                                                } else {
                                                    absX += g.x;
                                                }
                                            }
                                            current = current.getParent();
                                            if (current && (current.getId() === '0' || current.getId() === '1')) break;
                                        }

                                        const sourceParent = source.getParent()?.getId();
                                        const targetParent = cell.getParent()?.getId();
                                        const weight = sourceParent !== targetParent ? 3.0 : 1.0;

                                        xPositions.push(absX);
                                        weights.push(weight);
                                    }
                                }
                            });

                            // Set optimal X as average of connections, or keep current if no connections
                            // Use weighted average to prioritize cross-container vertical connections
                            if (xPositions.length > 0) {
                                const weightedSum = xPositions.reduce((sum, x, i) => sum + x * weights[i], 0);
                                const totalWeight = weights.reduce((a, b) => a + b, 0);
                                optimalX.set(id, weightedSum / totalWeight);

                                console.log(`📐 PLACEMENT: ${id} connection analysis:`, {
                                    connections: xPositions.map((x, i) => ({ x: x.toFixed(1), weight: weights[i] })),
                                    optimalX: (weightedSum / totalWeight).toFixed(1)
                                });
                            } else {
                                optimalX.set(id, rowShapes.find(s => s.id === id)!.geom.x);
                            }
                        });

                        // Sort by optimal X
                        const sorted = [...rowShapes].sort((a, b) =>
                            (optimalX.get(a.id) || 0) - (optimalX.get(b.id) || 0)
                        );

                        // Get existing X positions (sorted) to redistribute
                        const existingXPositions = rowShapes.map(s => s.geom.x).sort((a, b) => a - b);

                        // Assign new positions
                        sorted.forEach((shape, idx) => {
                            const oldX = shape.geom.x;
                            const newX = existingXPositions[idx];
                            if (Math.abs(oldX - newX) > 5) {
                                console.log(`📐 PLACEMENT: ${shape.id} x: ${oldX.toFixed(1)} → ${newX.toFixed(1)} (optimal: ${optimalX.get(shape.id)?.toFixed(1)})`);
                                shape.geom.x = newX;
                                shape.cell.setGeometry(shape.geom);
                            }
                        });
                    });
                });
                
                console.log('✅ PLACEMENT: Optimization complete');
                
                // Use OrthogonalConnector for clean edge routing
                console.log('📐 ROUTER: Using OrthogonalConnector for edge routing');
                
                const { Point } = maxGraphModule;
                
                // Helper to get absolute geometry
                const getAbsoluteGeometry = (cell: any) => {
                    const geom = cell.getGeometry();
                    if (!geom) return null;
                    
                    let absX = geom.x;
                    let absY = geom.y;
                    
                    let parent = cell.getParent();
                    while (parent && parent.getId() !== '0' && parent.getId() !== '1') {
                        const parentGeom = parent.getGeometry();
                        if (parentGeom) {
                            absX += parentGeom.x;
                            absY += parentGeom.y;
                        }
                        parent = parent.getParent();
                    }
                    
                    return { x: absX, y: absY, width: geom.width, height: geom.height };
                };
                
                // Build list of all shapes (for obstacle avoidance)
                const allShapes: Rect[] = [];
                cellMap.forEach((cell, id) => {
                    if (id === '0' || id === '1') return;
                    if (cell.isVertex()) {
                        const absGeom = getAbsoluteGeometry(cell);
                        if (absGeom) {
                            allShapes.push({
                                left: absGeom.x,
                                top: absGeom.y,
                                width: absGeom.width,
                                height: absGeom.height
                            });
                        }
                    }
                });
                
                console.log(`📐 ROUTER: Found ${allShapes.length} shapes for obstacle avoidance`);
                
                // Route each edge
                model.beginUpdate();
                try {
                    edgeCells.forEach(({ id }) => {
                        const cell = cellMap.get(id);
                        if (!cell) return;
                        
                        const source = cell.getTerminal(true);
                        const target = cell.getTerminal(false);
                        if (!source || !target) return;
                        
                        const sourceGeom = getAbsoluteGeometry(source);
                        const targetGeom = getAbsoluteGeometry(target);
                        if (!sourceGeom || !targetGeom) return;
                        
                        // Determine optimal connection sides
                        const { sourceSide, targetSide } = getOptimalSide(
                            { left: sourceGeom.x, top: sourceGeom.y, width: sourceGeom.width, height: sourceGeom.height },
                            { left: targetGeom.x, top: targetGeom.y, width: targetGeom.width, height: targetGeom.height }
                        );
                        
                        // Filter obstacles to exclude source and target
                        const obstacles = allShapes.filter(shape => {
                            return !(shape.left === sourceGeom.x && shape.top === sourceGeom.y) &&
                                   !(shape.left === targetGeom.x && shape.top === targetGeom.y);
                        });
                        
                        // Route the connector
                        const waypoints = routeOrthogonalConnector({
                            pointA: {
                                shape: { left: sourceGeom.x, top: sourceGeom.y, width: sourceGeom.width, height: sourceGeom.height },
                                side: sourceSide,
                                distance: 0.5
                            },
                            pointB: {
                                shape: { left: targetGeom.x, top: targetGeom.y, width: targetGeom.width, height: targetGeom.height },
                                side: targetSide,
                                distance: 0.5
                            },
                            obstacles,
                            shapeMargin: 20
                        });
                        
                        if (waypoints.length >= 2) {
                            const geometry = cell.getGeometry();
                            if (geometry) {
                                const points = waypoints.map(wp => new Point(wp.x, wp.y));
                                
                                geometry.points = points;
                                geometry.relative = false;
                                geometry.setTerminalPoint(null, true);
                                geometry.setTerminalPoint(null, false);
                                cell.setGeometry(geometry);
                                
                                // Update view state
                                const viewState = graph.view.getState(cell);
                                if (viewState) {
                                    viewState.absolutePoints = points;
                                }
                                
                                console.log(`📐 ROUTER: Edge ${id} routed with ${waypoints.length} waypoints`);
                            }
                        }
                    });
                } finally {
                    model.endUpdate();
                }
                
                console.log('✅ ROUTER: All edges routed');
                
                // SIMPLIFIED: Let maxGraph handle edge routing
                // Configure edge style defaults
                const defaultEdgeStyle = stylesheet.getDefaultEdgeStyle();
                defaultEdgeStyle['edgeStyle'] = 'orthogonalEdgeStyle';
                defaultEdgeStyle['rounded'] = 1;
                defaultEdgeStyle['jettySize'] = 'auto';
                defaultEdgeStyle['orthogonalLoop'] = 1;
                stylesheet.putDefaultEdgeStyle(defaultEdgeStyle);
                
                console.log('📐 ROUTING: Using maxGraph built-in orthogonal routing');
                console.log('📐 ELK: Current cellMap size:', cellMap.size);
                
                // Declare layoutResult outside try-catch so it's accessible later
                let layoutResult: any = null;
                
                try {
                    // Convert our graph structure to ELK format
                    const elkNodes: LayoutNode[] = [];
                    const elkEdges: LayoutEdge[] = [];
                    const elkContainers: LayoutContainer[] = [];
                    
                    // Group nodes by container
                    const containerMap = new Map<string, { nodes: LayoutNode[], edges: LayoutEdge[], containerId: string }>();
                
                console.log('📐 ELK: Starting cell iteration...');
                
                // Helper to get absolute geometry (accounting for parent container offsets)
                const getAbsoluteGeometry = (cell: any) => {
                    const geom = cell.getGeometry();
                    if (!geom) return null;
                    
                    let absX = geom.x;
                    let absY = geom.y;
                    
                    // Walk up parent chain to accumulate offsets
                    let parent = cell.getParent();
                    while (parent && parent.getId() !== '0' && parent.getId() !== '1') {
                        const parentGeom = parent.getGeometry();
                        if (parentGeom) {
                            absX += parentGeom.x;
                            absY += parentGeom.y;
                        }
                        parent = parent.getParent();
                    }
                    
                    return { x: absX, y: absY, width: geom.width, height: geom.height, originalGeom: geom };
                };
                
                cellMap.forEach((cell, id) => {
                    if (id === '0' || id === '1') return;
                    
                        const style = cell.getStyle();
                        const isSwimlane = style && (style['swimlane'] || style['container']);
                        
                        if (isSwimlane) {
                            // This is a container
                            const geom = cell.getGeometry();
                            console.log(`📐 ELK: Found container: ${id}`, geom);
                            containerMap.set(id, {
                                nodes: [],
                                edges: [],
                                containerId: id
                        });
                    } else if (cell.isVertex()) {
                        // This is a regular node
                        const absGeom = getAbsoluteGeometry(cell);
                        if (!absGeom) return;
                        console.log(`📐 ELK: Found vertex: ${id}`, {
                            relative: cell.getGeometry(),
                            absolute: { x: absGeom.x, y: absGeom.y }
                        });
                        
                        const node: LayoutNode = {
                            id,
                            width: absGeom.width,
                            height: absGeom.height,
                            x: absGeom.x,
                            y: absGeom.y,
                            labels: cell.getValue() ? [{ text: cell.getValue() }] : undefined
                        };
                            
                            // Find which container this node belongs to
                            const parent = cell.getParent();
                            const parentId = parent?.getId();
                            
                            // Check if parent is a container (not root cells 0/1)
                            if (parentId && parentId !== '0' && parentId !== '1' && containerMap.has(parentId)) {
                                // Node belongs to a container
                                containerMap.get(parentId)!.nodes.push(node);
                                console.log(`📐 ELK: Added node ${id} to container ${parentId}`);
                            } else {
                                // Top-level node (not in any container)
                                elkNodes.push(node);
                                console.log(`📐 ELK: Added node ${id} as top-level (parent: ${parentId})`);
                            }
                        } else if (cell.isEdge()) {
                            // This is an edge
                            const source = cell.getTerminal(true);
                            const target = cell.getTerminal(false);
                            console.log(`📐 ELK: Found edge: ${id}, source: ${source?.getId()}, target: ${target?.getId()}`);
                            
                            if (source && target) {
                                const edge: LayoutEdge = {
                                    id,
                                    source: source.getId(),
                                    target: target.getId(),
                                    labels: cell.getValue() ? [{ text: cell.getValue() }] : undefined
                                };
                                
                                // Determine if edge is within a container or crosses containers
                                const sourceParent = source.getParent()?.getId();
                                const targetParent = target.getParent()?.getId();
                                
                                // Edge is within same container only if both nodes have the same container parent
                                if (sourceParent && targetParent && 
                                    sourceParent === targetParent && 
                                    sourceParent !== '0' && sourceParent !== '1' &&
                                    containerMap.has(sourceParent)) {
                                    // Edge within same container
                                    containerMap.get(sourceParent)!.edges.push(edge);
                                    console.log(`📐 ELK: Added edge ${id} to container ${sourceParent}`);
                                } else {
                                    // Cross-container or top-level edge
                                    elkEdges.push(edge);
                                    console.log(`📐 ELK: Added edge ${id} as cross-container (${sourceParent} -> ${targetParent})`);
                                }
                            }
                        }
                    });
                    
                    // Build containers array
                    containerMap.forEach((data, containerId) => {
                        const cell = cellMap.get(containerId);
                        if (cell) {
                            elkContainers.push({
                                id: containerId,
                                children: data.nodes,
                                edges: data.edges,
                                labels: cell.getValue() ? [{ text: cell.getValue() }] : undefined
                            });
                        }
                    });
                    
                    console.log('📐 ELK: Converted to ELK format', {
                        topLevelNodes: elkNodes.length,
                        topLevelEdges: elkEdges.length,
                        containers: elkContainers.length
                    });
                    
                    // Validate that all edge endpoints exist in the node lists
                    const allNodeIds = new Set([
                        ...elkNodes.map(n => n.id),
                        ...elkContainers.flatMap(c => c.children.map(n => n.id))
                    ]);
                    
                    const allEdges = [...elkEdges, ...elkContainers.flatMap(c => c.edges)];
                    const invalidEdges = allEdges.filter(e => 
                        !allNodeIds.has(e.source) || !allNodeIds.has(e.target)
                    );
                    
                    if (invalidEdges.length > 0) {
                        console.error('❌ ELK: Found edges with missing nodes:', invalidEdges);
                        invalidEdges.forEach(e => {
                            console.error(`  Edge ${e.id}: ${e.source} -> ${e.target}`, {
                                sourceExists: allNodeIds.has(e.source),
                                targetExists: allNodeIds.has(e.target)
                            });
                        });
                        throw new Error(`ELK validation failed: ${invalidEdges.length} edges reference missing nodes`);
                    }
                    
                    console.log('✅ ELK: All edge endpoints validated');
                    
                    // Run ELK layout
                    console.log('📐 ELK: About to call runLayout with:', {
                        topLevelNodes: elkNodes.length,
                        topLevelEdges: elkEdges.length,
                        containers: elkContainers.length,
                        nodeIds: elkNodes.map(n => n.id),
                        edgeIds: elkEdges.map(e => e.id)
                    });
                    layoutResult = await runLayout(elkNodes, elkEdges, {
                        algorithm: 'layered',
                        direction: 'DOWN',
                        edgeRouting: 'ORTHOGONAL',
                        hierarchical: true,
                        spacing: {
                            nodeNode: 80,
                            edgeNode: 40,
                            edgeEdge: 15
                        }
                    }, elkContainers);
                    
                    console.log('📐 ELK: Layout result received:', {
                        nodes: layoutResult.nodes.size,
                        edges: layoutResult.edges.size,
                        containers: layoutResult.containers?.size || 0
                    });
                    
                    // Apply layout results back to maxGraph
                    applyLayoutToMaxGraph(graph.getModel ? graph : { getModel: () => model }, cellMap, layoutResult);
                    
                    console.log('✅ ELK: Layout applied successfully');
                    
                    // Mark that ELK layout was applied successfully
                    graph.__elkLayoutApplied = true;
                } catch (elkError) {
                    console.error('📐 ELK: Layout failed, falling back to manual routing:', elkError);
                    console.error('📐 ELK: Error details:', {
                        message: elkError.message,
                        stack: elkError.stack
                    });
                }

                // CRITICAL: If ELK layout was successful, extract and apply edge routing from ELK results
                if (graph.__elkLayoutApplied && layoutResult && layoutResult.edges) {
                    console.log('📐 ELK: Disabling maxGraph automatic edge routing');
                    
                    // CRITICAL: Force maxGraph to create view states for all cells
                    // We MUST do this BEFORE setting waypoints, otherwise maxGraph ignores them
                    // and recalculates routing when it finally creates the view states
                    console.log('📐 ELK: Forcing view state creation before applying waypoints');
                    graph.view.validate();
                    
                    // Verify view states now exist
                    const sampleEdge = cellMap.get('edge1');
                    if (sampleEdge) {
                        const viewState = graph.view.getState(sampleEdge);
                        console.log('📐 ELK: View state check after validate:', {
                            hasState: !!viewState,
                            absPoints: viewState?.absolutePoints?.length || 0
                        });
                    }
                    
                    console.log('📐 ELK: Applying edge routing from ELK results');
                    
                    // DEBUG: Log what ELK actually gave us for routing
                    // Disable automatic edge routing - we're using ELK's routes
                    const connectionHandler = graph.getPlugin('ConnectionHandler');
                    if (connectionHandler) {
                        connectionHandler.setEnabled(false);
                    }
                    
                    console.log('📐 ELK: Applying edge routing from ELK results');
                    
                    // DEBUG: Log what ELK actually gave us for routing
                    let edgeCount = 0;
                    let edgesWithRouting = 0;
                    layoutResult.edges.forEach((elkEdge, edgeId) => {
                        edgeCount++;
                        if (elkEdge.sections && elkEdge.sections.length > 0) {
                            edgesWithRouting++;
                            const section = elkEdge.sections[0];
                            console.log(`📐 ELK-ROUTE ${edgeId}:`, {
                                hasStart: !!section.startPoint,
                                hasEnd: !!section.endPoint,
                                bendPoints: section.bendPoints?.length || 0,
                                startPoint: section.startPoint,
                                endPoint: section.endPoint
                            });
                        }
                    });
                    console.log(`📐 ELK: ${edgesWithRouting}/${edgeCount} edges have routing sections`);
                    
                    layoutResult.edges.forEach((elkEdge, edgeId) => {
                        const cell = cellMap.get(edgeId);
                        if (!cell || !cell.isEdge()) return;

                        const geometry = cell.getGeometry();
                        if (!geometry) return;

                        // ELK provides edge sections with start/end points and bend points
                        if (elkEdge.sections && elkEdge.sections.length > 0) {
                            const section = elkEdge.sections[0];
                            
                            // CRITICAL: Use ELK's exact routing - build complete waypoint list
                            // including start point, bend points, and end point
                            const points: any[] = [];
                            
                            // ELK start point (absolute coordinates where edge exits source)
                            points.push(new Point(section.startPoint.x, section.startPoint.y));
                            
                            // Bend points (intermediate waypoints)
                            if (section.bendPoints) {
                                section.bendPoints.forEach((bp: any) => {
                                    points.push(new Point(bp.x, bp.y));
                                });
                            }

                            // ELK end point (absolute coordinates where edge enters target)
                            points.push(new Point(section.endPoint.x, section.endPoint.y));

                            console.log(`📐 ELK-ROUTE ${edgeId}: Built waypoint chain:`, {
                                start: `(${section.startPoint.x.toFixed(1)}, ${section.startPoint.y.toFixed(1)})`,
                                bends: section.bendPoints?.length || 0,
                                end: `(${section.endPoint.x.toFixed(1)}, ${section.endPoint.y.toFixed(1)})`,
                                totalPoints: points.length
                            });

                            // CRITICAL: Disable ALL automatic routing
                            // MaxGraph must draw straight lines through our ELK waypoints
                            const style = cell.getStyle() || {};
                            style['edgeStyle'] = undefined; // Disable automatic routing completely
                            style['curved'] = 0; // No curves
                            style['orthogonal'] = 0; // Disable orthogonal routing
                            style['rounded'] = 0; // Disable rounding
                            style['jettySize'] = 0; // No jetty offsets
                            style['exitPerimeter'] = 0; // Don't snap to perimeter
                            style['entryPerimeter'] = 0; // Don't snap to perimeter
                            
                            // CRITICAL: Tell maxGraph to use absolute waypoints, not relative
                            style['sourcePerimeterSpacing'] = 0;
                            style['targetPerimeterSpacing'] = 0;
                            style['segment'] = 0; // No segment calculation
                            
                            cell.setStyle(style);

                            // CRITICAL: Set geometry to ABSOLUTE mode
                            // This tells maxGraph: "use these exact coordinates, don't calculate anything"
                            geometry.relative = false; // Absolute positioning
                            
                            // CRITICAL: Set terminal points to null - we're using absolute waypoints
                            // If these are set, maxGraph will try to snap to shape perimeters
                            geometry.setTerminalPoint(null, true);  // Clear source terminal
                            geometry.setTerminalPoint(null, false); // Clear target terminal

                            // Set waypoints on geometry
                            geometry.points = points;
                            
                            // CRITICAL: Update geometry in model transaction
                            model.beginUpdate();
                            try {
                                cell.setGeometry(geometry);
                                
                                // CRITICAL: Now that geometry is set, update the view state directly
                                // This is the key - we must set absolutePoints on the view state
                                const viewState = graph.view.getState(cell);
                                if (viewState) {
                                    // Convert our Point objects to absolute coordinates for the view
                                    // View's absolutePoints expects actual pixel coordinates
                                    viewState.absolutePoints = points.map(p => {
                                        // Points are already in absolute coordinates from ELK
                                        return new Point(p.x, p.y);
                                    });
                                    
                                    // Mark the view state as invalid so it redraws with our waypoints
                                    graph.view.invalidate(cell, false, false);
                                    
                                    console.log(`📐 ELK: Set absolutePoints on view state for ${edgeId}:`, {
                                        pointCount: viewState.absolutePoints.length
                                    });
                                } else {
                                    console.warn(`📐 ELK: No view state for ${edgeId} - waypoints may not render`);
                                }
                            } finally {
                                model.endUpdate();
                            }
                            
                            // DEBUG: Verify waypoints were actually set
                            const verifyGeom = cell.getGeometry();
                            console.log(`🔍 VERIFY ${edgeId}:`, {
                                pointsSet: points.length,
                                pointsOnGeometry: verifyGeom?.points?.length || 0,
                                relative: verifyGeom?.relative,
                                styleEdgeStyle: cell.getStyle()?.['edgeStyle'],
                                samplePoint: points[0] ? { x: points[0].x, y: points[0].y } : null,
                                sampleGeomPoint: verifyGeom?.points?.[0] ? { x: verifyGeom.points[0].x, y: verifyGeom.points[0].y } : null
                            });
                            
                            // Verify terminal points are cleared
                            console.log(`🔍 TERMINAL-POINTS ${edgeId}:`, {
                                sourceTerminal: geometry.getTerminalPoint(true),
                                targetTerminal: geometry.getTerminalPoint(false)
                            });
                            
                            // Check what maxGraph's view thinks about this edge
                            const viewState = graph.view.getState(cell);
                            console.log(`🔍 VIEW-STATE ${edgeId}:`, {
                                hasState: !!viewState,
                                hasAbsolutePoints: !!viewState?.absolutePoints,
                                absPointCount: viewState?.absolutePoints?.length || 0,
                                firstAbsPoint: viewState?.absolutePoints?.[0],
                                viewStyle: viewState?.style?.edgeStyle
                            });
                            
                            // FINAL verification - check if absolutePoints survived
                            const finalViewState = graph.view.getState(cell);
                            console.log(`🔍 FINAL-VIEW-STATE ${edgeId}:`, {
                                hasState: !!finalViewState,
                                absPointCount: finalViewState?.absolutePoints?.length || 0
                            });
                            
                            console.log(`📐 ELK: Applied routing for edge ${edgeId}:`, {
                                startPoint: section.startPoint,
                                endPoint: section.endPoint,
                                bendPoints: section.bendPoints?.length || 0
                            });
                        } // Close if (elkEdge.sections && elkEdge.sections.length > 0)
                    }); // Close layoutResult.edges.forEach
                    
                    // CRITICAL: Final validation to ensure view states have our waypoints
                    console.log('📐 ELK: Final view validation with locked waypoints');
                    graph.view.validate();
                    
                    console.log('✅ ELK: Edge routing locked and applied');
                }

                // Build edge direction and bidirectional pair maps
                // POST-PROCESS: After cells are added, calculate optimal connection points for ALL edges
                // This ensures clean orthogonal routing without overlaps
                
                // CRITICAL FIX: Skip manual connection point calculation if ELK already provided routing
                if (graph.__elkLayoutApplied) {
                    console.log('📐 ROUTING: Skipping manual connection points - using ELK routing');
                } else {
                    console.log('📐 ROUTING: Applying manual connection point calculation (ELK fallback)');
                
                edgeCells.forEach(({ id, element }) => {
                    const cell = cellMap.get(id);
                    if (!cell) return;

                    const sourceId = element.getAttribute('source');
                    const targetId = element.getAttribute('target');
                    if (!sourceId || !targetId) return;

                    const sourceCell = cellMap.get(sourceId);
                    const targetCell = cellMap.get(targetId);
                    if (!sourceCell || !targetCell) return;

                    // Get absolute positions (accounting for parent containers)
                    const getAbsoluteGeometry = (cell: any) => {
                        let geom = cell.getGeometry();
                        if (!geom) return null;

                        let x = geom.x;
                        let y = geom.y;

                        // Walk up parent chain to get absolute position
                        let parent = cell.getParent();
                        while (parent && parent.getId() !== '0' && parent.getId() !== '1') {
                            const parentGeom = parent.getGeometry();
                            if (parentGeom) {
                                x += parentGeom.x;
                                y += parentGeom.y;
                            }
                            parent = parent.getParent();
                        }

                        return { x, y, width: geom.width, height: geom.height };
                    };

                    const sourceGeom = getAbsoluteGeometry(sourceCell);
                    const targetGeom = getAbsoluteGeometry(targetCell);
                    if (!sourceGeom || !targetGeom) return;


                    const currentStyle = cell.getStyle() || {};

                    // ALWAYS calculate connection points - don't trust defaults

                    // Calculate angle between centers
                    const dx = targetGeom.x + targetGeom.width / 2 - (sourceGeom.x + sourceGeom.width / 2);
                    const dy = targetGeom.y + targetGeom.height / 2 - (sourceGeom.y + sourceGeom.height / 2);
                    const angle = Math.atan2(dy, dx) * (180 / Math.PI);

                    // Calculate flow direction upfront - needed for routing decisions
                    const isHorizontal = Math.abs(dx) > Math.abs(dy);

                    // Calculate edge-to-edge distances (gaps between shapes)
                    const horizontalGap = Math.abs(dx) - (sourceGeom.width + targetGeom.width) / 2;
                    const verticalGap = Math.abs(dy) - (sourceGeom.height + targetGeom.height) / 2;

                    // Check if shapes are aligned on an axis
                    // Horizontally aligned means their horizontal centers are close (can route vertically)
                    const horizontallyAligned = Math.abs(dx) < (sourceGeom.width + targetGeom.width) / 2;
                    // Vertically aligned means their vertical centers are close (can route horizontally)  
                    const verticallyAligned = Math.abs(dy) < (sourceGeom.height + targetGeom.height) / 2;

                    // Alignment determines routing direction regardless of distance
                    // If horizontally aligned → route vertically (straight up/down)
                    // If vertically aligned → route horizontally (straight left/right)
                    const shouldRouteVertically = horizontallyAligned;
                    const shouldRouteHorizontally = verticallyAligned;

                    console.log(`📐 ROUTING: ${id} adjacency check:`, {
                        dx: dx.toFixed(1),
                        dy: dy.toFixed(1),
                        horizontallyAligned,
                        verticallyAligned,
                        shouldRouteHorizontally,
                        shouldRouteVertically
                    });

                    // PRIORITY 1: Adjacent shapes on primary axis - use straight paths
                    if (shouldRouteHorizontally) {
                        // Horizontally adjacent - connect on left/right sides
                        if (dx > 0) {
                            // Target is to the right
                            currentStyle['exitX'] = 1.0; currentStyle['exitY'] = 0.5;
                            currentStyle['entryX'] = 0.0; currentStyle['entryY'] = 0.5;
                        } else {
                            // Target is to the left
                            currentStyle['exitX'] = 0.0; currentStyle['exitY'] = 0.5;
                            currentStyle['entryX'] = 1.0; currentStyle['entryY'] = 0.5;
                        }
                        console.log(`📐 ROUTING: ${id} - horizontal neighbors (straight path)`);
                    } else if (shouldRouteVertically) {
                        // Vertically adjacent - connect on top/bottom sides
                        if (dy > 0) {
                            // Target is below
                            currentStyle['exitX'] = 0.5; currentStyle['exitY'] = 1.0;
                            currentStyle['entryX'] = 0.5; currentStyle['entryY'] = 0.0;
                        } else {
                            // Target is above
                            currentStyle['exitX'] = 0.5; currentStyle['exitY'] = 0.0;
                            currentStyle['entryX'] = 0.5; currentStyle['entryY'] = 1.0;
                        }
                        console.log(`📐 ROUTING: ${id} - vertical neighbors (straight path)`);
                    } else {
                        // Non-adjacent: use dominant axis routing
                        const isHorizontal = Math.abs(dx) > Math.abs(dy);

                        // For diagonal routes crossing multiple layers, use edge routing
                        const isCrossingMultipleLayers = Math.abs(dy) > 150 && Math.abs(dx) > 150;

                        if (isCrossingMultipleLayers) {
                            // Route around perimeter instead of through center
                            if (dx > 0 && dy > 0) {
                                // Bottom-right diagonal: exit right, enter top
                                currentStyle['exitX'] = 1.0; currentStyle['exitY'] = 0.5;
                                currentStyle['entryX'] = 0.5; currentStyle['entryY'] = 0.0;
                            } else if (dx < 0 && dy > 0) {
                                // Bottom-left diagonal: exit left, enter top
                                currentStyle['exitX'] = 0.0; currentStyle['exitY'] = 0.5;
                                currentStyle['entryX'] = 0.5; currentStyle['entryY'] = 0.0;
                            } else if (dx > 0 && dy < 0) {
                                // Top-right diagonal: exit right, enter bottom
                                currentStyle['exitX'] = 1.0; currentStyle['exitY'] = 0.5;
                                currentStyle['entryX'] = 0.5; currentStyle['entryY'] = 1.0;
                            } else {
                                // Top-left diagonal: exit left, enter bottom
                                currentStyle['exitX'] = 0.0; currentStyle['exitY'] = 0.5;
                                currentStyle['entryX'] = 0.5; currentStyle['entryY'] = 1.0;
                            }
                            console.log(`📐 ROUTING: ${id} - diagonal edge route`);
                        } else if (isHorizontal) {
                            if (dx > 0) {
                                currentStyle['exitX'] = 1.0; currentStyle['exitY'] = 0.5;
                                currentStyle['entryX'] = 0.0; currentStyle['entryY'] = 0.5;
                            } else {
                                currentStyle['exitX'] = 0.0; currentStyle['exitY'] = 0.5;
                                currentStyle['entryX'] = 1.0; currentStyle['entryY'] = 0.5;
                            }
                        } else {
                            // Vertical flow dominates
                            if (dy > 0) {
                                currentStyle['exitX'] = 0.5; currentStyle['exitY'] = 1.0;
                                currentStyle['entryX'] = 0.5; currentStyle['entryY'] = 0.0;
                            } else {
                                currentStyle['exitX'] = 0.5; currentStyle['exitY'] = 0.0;
                                currentStyle['entryX'] = 0.5; currentStyle['entryY'] = 1.0;
                            }
                        }
                        console.log(`📐 ROUTING: ${id} - non-adjacent (${isCrossingMultipleLayers ? 'diagonal edge' : isHorizontal ? 'horizontal' : 'vertical'} flow)`);
                    }

                    // Configure orthogonal routing with proper constraints
                    currentStyle['edgeStyle'] = 'orthogonalEdgeStyle';
                    currentStyle['orthogonal'] = '1';
                    currentStyle['rounded'] = '1';
                    currentStyle['jumpStyle'] = 'arc';
                    currentStyle['jumpSize'] = '10';

                    // Routing preferences for cleaner paths
                    currentStyle['jettySize'] = 'auto';
                    currentStyle['exitPerimeter'] = '1';
                    currentStyle['entryPerimeter'] = '1';

                    // Improve edge label positioning to avoid overlaps
                    currentStyle['labelBackgroundColor'] = '#ffffff';
                    currentStyle['labelBorderColor'] = '#d9d9d9';
                    currentStyle['spacingTop'] = 8;
                    currentStyle['spacingBottom'] = 8;
                    currentStyle['spacingLeft'] = 12;
                    currentStyle['spacingRight'] = 12;

                    // Check if edge value/label exists
                    const edgeValue = cell.getValue();
                    const hasLabel = edgeValue && typeof edgeValue === 'string' && edgeValue.trim().length > 0;
                    const isLongLabel = hasLabel && edgeValue.length > 20;

                    if (!hasLabel) {
                        // No label - no need to adjust positioning
                        cell.setStyle(currentStyle);
                        return;
                    }

                    // Determine primary flow direction
                    const flowAngle = Math.atan2(dy, dx) * (180 / Math.PI);
                    const absAngle = Math.abs(flowAngle);

                    // Classify edge direction more precisely
                    const isMainlyHorizontal = absAngle < 45 || absAngle > 135;
                    const isMainlyVertical = absAngle >= 45 && absAngle <= 135;

                    console.log(`📐 LABEL: ${id} flow analysis:`, {
                        dx: dx.toFixed(1),
                        dy: dy.toFixed(1),
                        angle: flowAngle.toFixed(1),
                        isMainlyHorizontal,
                        isMainlyVertical,
                        label: edgeValue.substring(0, 30)
                    });

                    // Position labels based on edge direction
                    if (isMainlyVertical) {
                        // VERTICAL edges: position labels to the LEFT side of the line
                        currentStyle['labelPosition'] = 'center';
                        currentStyle['align'] = 'left';
                        currentStyle['verticalAlign'] = 'middle';
                        currentStyle['spacingLeft'] = isLongLabel ? 28 : 22;
                        currentStyle['labelBackgroundColor'] = 'rgba(255,255,255,0.95)';
                        console.log(`📐 LABEL: ${id} positioned LEFT of vertical line`);
                    } else if (isMainlyHorizontal) {
                        // HORIZONTAL edges: position labels ABOVE the line
                        currentStyle['labelPosition'] = 'center';
                        currentStyle['align'] = 'center';
                        currentStyle['verticalAlign'] = 'bottom';
                        currentStyle['spacingBottom'] = isLongLabel ? 18 : 14;
                        currentStyle['labelBackgroundColor'] = 'rgba(255,255,255,0.95)';
                        console.log(`📐 LABEL: ${id} positioned ABOVE horizontal line`);
                    } else {
                        // DIAGONAL edges: position above and to the side
                        currentStyle['labelPosition'] = 'center';
                        currentStyle['align'] = dx > 0 ? 'left' : 'right';
                        currentStyle['verticalAlign'] = 'top';
                        currentStyle['spacingTop'] = 14;
                        currentStyle['spacingLeft'] = dx > 0 ? 16 : 0;
                        currentStyle['spacingRight'] = dx < 0 ? 16 : 0;
                        currentStyle['labelBackgroundColor'] = 'rgba(255,255,255,0.95)';
                        console.log(`📐 LABEL: ${id} positioned for diagonal line`);
                    }

                    // Extra adjustments for long labels
                    if (isLongLabel) {
                        currentStyle['labelBackgroundColor'] = '#ffffff';
                        currentStyle['spacingTop'] = 10;
                        currentStyle['spacingBottom'] = 10;
                        currentStyle['spacingLeft'] = 14;
                        currentStyle['spacingRight'] = 14;
                    }

                    // Detect edges that will overlap on similar paths and offset them
                    // Group edges by their routing "signature" (direction + approximate path)
                    const routingSignature = `${Math.sign(dx)}_${Math.sign(dy)}_${Math.round(sourceGeom.x / 100)}_${Math.round(sourceGeom.y / 100)}`;
                    // If so, add offset to separate parallel edges
                    const vertexPairKey = sourceId < targetId ? `${sourceId}-${targetId}` : `${targetId}-${sourceId}`;
                    const edgesForPair = edgeCells.filter(({ id: edgeId, element: el }) => {
                        const src = el.getAttribute('source');
                        const tgt = el.getAttribute('target');
                        if (!src || !tgt) return false;
                        const pairKey = src < tgt ? `${src}-${tgt}` : `${tgt}-${src}`;
                        return pairKey === vertexPairKey;
                    });

                    // Handle parallel edges (multiple edges between same vertices)
                    if (edgesForPair.length > 1) {
                        // Multiple edges between same vertices - add offset
                        const edgeIndex = edgesForPair.findIndex(e => e.id === id);
                        const offset = (edgeIndex - (edgesForPair.length - 1) / 2) * 15;
                        currentStyle['sourcePortConstraint'] = 'center';
                        currentStyle['targetPortConstraint'] = 'center';

                        // Offset the edge routing
                        if (isHorizontal) {
                            currentStyle['exitDy'] = offset;
                            currentStyle['entryDy'] = offset;
                        } else {
                            currentStyle['exitDx'] = offset;
                            currentStyle['entryDx'] = offset;
                        }
                    }

                    // Apply to cell style
                    cell.setStyle(currentStyle);
                });
                
                } // End of manual routing fallback

                // Only refresh once, gently
                if (!graph.__elkLayoutApplied) {
                    // Manual routing needs refresh
                    graph.view.validate();
                    graph.refresh();
                }

            } finally {
                model.endUpdate();
            }
            
            console.log('📐 DrawIO: Model update complete');
            
            // Check if bounds are already calculated (and cached incorrectly)
            try {
                const boundsAfterModelUpdate = graph.getGraphBounds();
                console.log('📐 BOUNDS-AFTER-MODEL-UPDATE (BEFORE DOM APPEND):', {
                    x: boundsAfterModelUpdate.x,
                    y: boundsAfterModelUpdate.y,
                    width: boundsAfterModelUpdate.width,
                    height: boundsAfterModelUpdate.height
                });
            } catch (e) {
                console.error('Error getting bounds after model update:', e);
            }
            
            // Force final view update before appending to DOM
            graph.view.validate();
            graph.sizeDidChange();

            console.log('📐 DrawIO: View validated, preparing for DOM append');

            // Add zoom controls to graphContainer before appending
            console.log('📐 DrawIO: Adding zoom controls');
            addZoomControls(graphContainer, graph);

            // Make container relatively positioned for absolute controls
            container.style.position = 'relative';
            
            // CRITICAL: Only clear if not already rendered
            if (!container.querySelector('svg')) {
                container.innerHTML = '';
            }
            (container as any).__drawioContentReady = true;

            // Append graph container with zoom controls
            container.appendChild(graphContainer);
            
            console.log('📐 DrawIO: Graph appended to DOM, now fixing label positions BEFORE fit');

            // Log bounds BEFORE any fixes
            try {
                const boundsBeforeFix = graph.getGraphBounds();
                console.log('📐 BOUNDS-BEFORE-FIX:', {
                    x: boundsBeforeFix.x,
                    y: boundsBeforeFix.y,
                    width: boundsBeforeFix.width,
                    height: boundsBeforeFix.height,
                    right: boundsBeforeFix.x + boundsBeforeFix.width,
                    bottom: boundsBeforeFix.y + boundsBeforeFix.height
                });
            } catch (e) {
                console.error('📐 Error getting bounds before fix:', e);
            }

            // CRITICAL FIX: Fix foreignObject positioning BEFORE calculating bounds and fitting
            // This ensures fit() uses correct bounds without broken label positions
            try {
                // Wait a moment for SVG to be created
                await new Promise(resolve => setTimeout(resolve, 100));
                
                const svgElement = graphContainer.querySelector('svg') as SVGSVGElement;
                if (svgElement) {
                    console.log('📐 DrawIO: Applying enhancer to fix label positions');
                    DrawIOEnhancer.fixAllForeignObjects(svgElement);
                    console.log('✅ DrawIO: Label positions fixed');
                    
                    // CRITICAL: Don't call refresh() if ELK layout was applied
                    // graph.refresh() calls view.clear() which destroys all shapes and redraws
                    // This wipes out the ELK routing we carefully applied
                    console.log('📐 DrawIO: Forcing bounds recalculation');
                    
                    if (!graph.__elkLayoutApplied) {
                        console.log('📐 DrawIO: Manual routing - calling view.invalidate/validate');
                        graph.view.invalidate();
                        graph.view.validate();
                    } else {
                        console.log('📐 DrawIO: ELK layout applied - skipping refresh to preserve routing');
                    }
                    
                    // DEBUG: After all rendering, check if waypoints survived
                    console.log('🔍 FINAL CHECK: Inspecting edge geometries after all rendering');
                    ['edge1', 'edge2', 'edge3', 'edge4'].forEach(edgeId => {
                        const cell = model.getCell(edgeId);
                        if (cell && cell.isEdge()) {
                            const geom = cell.getGeometry();
                            const viewState = graph.view.getState(cell);
                            console.log(`🔍 FINAL ${edgeId}:`, {
                                geomPoints: geom?.points?.length || 0,
                                absPoints: viewState?.absolutePoints?.length || 0,
                                firstGeomPoint: geom?.points?.[0],
                                firstAbsPoint: viewState?.absolutePoints?.[0]
                            });
                        }
                    });
                    
                    // Log bounds before and after for comparison
                    const boundsAfterFix = graph.getGraphBounds();
                    console.log('📐 DrawIO: Bounds after label fix:', {
                        x: boundsAfterFix.x.toFixed(1),
                        y: boundsAfterFix.y.toFixed(1),
                        width: boundsAfterFix.width.toFixed(1),
                        height: boundsAfterFix.height.toFixed(1),
                        right: (boundsAfterFix.x + boundsAfterFix.width).toFixed(1),
                        bottom: (boundsAfterFix.y + boundsAfterFix.height).toFixed(1)
                    });
                }
            } catch (enhanceError) {
                console.error('📐 DrawIO: Error fixing labels:', enhanceError);
            }
            
            // Get the actual content bounds
            const contentBounds = graph.getGraphBounds();
            console.log('📐 DrawIO: Content bounds:', {
                x: contentBounds.x,
                y: contentBounds.y,
                width: contentBounds.width,
                height: contentBounds.height
            });
            
            // Set the graph container to the exact size needed for content
            const padding = 40;
            const containerWidth = contentBounds.width + (padding * 2);
            const containerHeight = contentBounds.height + (padding * 2);
            
            graphContainer.style.width = `${containerWidth}px`;
            graphContainer.style.height = `${containerHeight}px`;
            graphContainer.style.overflow = 'hidden';
            
            console.log('📐 DrawIO: Set container to content size:', {
                contentWidth: contentBounds.width,
                contentHeight: contentBounds.height,
                containerWidth,
                containerHeight
            });
            
            // Center the content within the padded container
            const view = graph.getView();
            view.scale = 1.0; // Keep at 1:1 scale
            view.translate.x = padding - contentBounds.x;
            view.translate.y = padding - contentBounds.y;
            
            console.log('📐 DrawIO: Centered content at 1:1 scale:', {
                scale: view.scale,
                translateX: view.translate.x,
                translateY: view.translate.y
            });
            
        // DIAGNOSTIC: Monitor if diagram gets removed from DOM
        const observer = new MutationObserver((mutations) => {
            mutations.forEach(mut => {
                if (mut.removedNodes.length > 0) {
                    console.error('🚨 DIAGRAM CONTENT REMOVED FROM DOM:', {
                        removedNodes: mut.removedNodes.length,
                        target: mut.target,
                        timestamp: Date.now()
                    });
                }
            });
        });
        observer.observe(container, { childList: true, subtree: true });
        
            // Final validation
            // Add controls now that graph is fully rendered
            createControls(container, spec, xml!, isDarkMode, graph);
            
            // CRITICAL: Mark as stable to prevent React from clearing
            container.setAttribute('data-drawio-stable', 'true');
            graphContainer.setAttribute('data-drawio-graph', 'true');
            console.log('✅ DrawIO: Render complete, container marked stable');
            
            graph.view.validate();
            // NEVER call refresh() - it destroys our ELK routing
            // graph.refresh();
        } catch (error) {
            // Helper function to escape HTML for display
            const escapeHtml = (str: string): string => {
                return str.replace(/&/g, '&amp;')
                    .replace(/</g, '&lt;')
                    .replace(/>/g, '&gt;')
                    .replace(/"/g, '&quot;')
                    .replace(/'/g, '&#039;');
            };

            // Show error in container
            // Reset container styles to allow error content to display properly
            container.style.height = 'auto';
            container.style.minHeight = '200px';
            container.style.overflow = 'visible';

            container.innerHTML = '';

            container.innerHTML = `
                <div style="
                    padding: 20px;
                    background-color: ${isDarkMode ? '#2a1f1f' : '#fff2f0'};
                    border: 1px solid ${isDarkMode ? '#a61d24' : '#ffa39e'};
                    border-radius: 6px;
                    color: ${isDarkMode ? '#ff7875' : '#cf1322'};
                ">
                    <strong>DrawIO Rendering Error:</strong>
                    <pre style="margin: 10px 0; white-space: pre-wrap;">${error instanceof Error ? error.message : 'Unknown error'}</pre>
                    <details>
                        <summary style="
                            cursor: pointer;
                            font-weight: bold;
                            margin: 10px 0;
                            color: ${isDarkMode ? '#ff7875' : '#cf1322'};
                        ">Show Definition</summary>
                        <pre style="
                            max-height: 400px;
                            overflow: auto;
                            background: ${isDarkMode ? '#1f1f1f' : '#f6f8fa'};
                            padding: 12px;
                            border-radius: 4px;
                            margin: 0;
                            word-break: break-word;
                        "><code>${escapeHtml(spec.definition || '')}</code></pre>
                    </details>
                </div>
            `;

            // Add controls even for error cases so user can download/view source
            if (xml) {
                createControls(container, spec, xml, isDarkMode);
            }
        }
    };

    await attemptRender();
};

/**
 * Extract shape IDs from DrawIO XML
 * Used to determine which stencils need to be loaded
 */
function extractShapeIdsFromXml(xml: string): string[] {
    const shapeIds: string[] = [];

    // Look for resIcon attributes (AWS shapes)
    const resIconMatches = xml.matchAll(/resIcon=mxgraph\.aws4\.(\w+)/g);
    for (const match of resIconMatches) {
        shapeIds.push(`aws_${match[1]}`);
    }

    return shapeIds;
}

// Helper function to create zoom buttons
function createZoomButton(label: string, onClick: () => void): HTMLButtonElement {
    const button = document.createElement('button');
    button.textContent = label;
    button.style.cssText = `
        background: rgba(255, 255, 255, 0.9);
        border: 1px solid #ccc;
        border-radius: 4px;
        width: 32px;
        height: 32px;
        cursor: pointer;
        font-size: 16px;
        font-weight: bold;
        box-shadow: 0 2px 4px rgba(0,0,0,0.1);
    `;
    button.onclick = onClick;
    return button;
}

// Helper function to add zoom controls
function addZoomControls(graphContainer: HTMLElement, graph: any): void {
    const zoomControls = document.createElement('div');
    zoomControls.id = 'drawio-zoom-controls';
    zoomControls.style.cssText = 'position: absolute; bottom: 16px; right: 16px; display: flex; flex-direction: row; gap: 8px; z-index: 10000; pointer-events: auto;';

    console.log('📐 DrawIO: Adding zoom controls to container:', {
        containerId: graphContainer.id,
        containerPosition: graphContainer.style.position,
        containerOverflow: graphContainer.style.overflow,
        containerHeight: graphContainer.style.height,
        containerClientHeight: graphContainer.clientHeight,
        containerInDocument: document.body.contains(graphContainer),
        controlsBottom: '16px',
        controlsRight: '16px'
    });

    const zoomInBtn = createZoomButton('+', () => graph.zoomIn());
    const zoomOutBtn = createZoomButton('-', () => graph.zoomOut());
    const zoomFitBtn = createZoomButton('⊡', () => {
        graph.fit();
        graph.center(true, true);
        graph.refresh();
    });

    zoomControls.appendChild(zoomInBtn);
    zoomControls.appendChild(zoomOutBtn);
    zoomControls.appendChild(zoomFitBtn);
    graphContainer.appendChild(zoomControls);

    console.log('📐 DrawIO: Zoom controls added, verifying:', {
        controlsInDOM: !!document.getElementById('drawio-zoom-controls'),
        controlsParent: zoomControls.parentElement?.tagName,
        controlsOffsetHeight: zoomControls.offsetHeight,
        controlsOffsetWidth: zoomControls.offsetWidth,
        controlsPosition: zoomControls.getBoundingClientRect(),
        controlsDisplay: zoomControls.style.display,
        controlsVisible: zoomControls.offsetHeight > 0 && zoomControls.offsetWidth > 0,
        buttonCount: zoomControls.children.length,
        computedStyles: window.getComputedStyle(zoomControls).cssText.substring(0, 200)
    });
}

// Export the DrawIO plugin

// Helper function to add error controls with proper dark mode styling
function addErrorControls(container: HTMLElement, error: any, xml: string, spec: DrawIOSpec, attemptRender: () => Promise<void>, isDarkMode: boolean): void {
    const errorDiv = document.createElement('div');
    errorDiv.style.cssText = 'padding: 16px; background: #fff2f0; border: 1px solid #ffccc7; border-radius: 4px; color: #cf1322;';

    const errorMessage = document.createElement('div');
    errorMessage.textContent = `Error: ${error.message || 'Failed to render diagram'}`;
    errorDiv.appendChild(errorMessage);
    container.appendChild(errorDiv);
}

// Export the DrawIO plugin
export const drawioPlugin: D3RenderPlugin = {
    name: 'drawio-renderer',
    priority: 7,
    sizingConfig: {
        sizingStrategy: 'auto-expand',
        needsDynamicHeight: true,
        needsOverflowVisible: false,
        observeResize: false,
        containerStyles: {
            width: '100%',
            height: 'auto',
            overflow: 'hidden'
        }
    },
    canHandle: isDrawIOSpec,
    isDefinitionComplete: isDefinitionComplete,
    render: renderDrawIO
};
