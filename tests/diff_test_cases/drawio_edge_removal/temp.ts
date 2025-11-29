import { D3RenderPlugin } from '../../types/d3';

// Import maxGraph CSS for proper rendering
import '@maxgraph/core/css/common.css';

// Extend window interface for maxgraph
declare global {
    interface Window {
        maxGraph: any;
        __maxGraphLoaded?: boolean;
        __maxGraphLoading?: Promise<any>;
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
    controlsDiv.style.cssText = 'display: flex; justify-content: space-between; align-items: center; gap: 8px; padding: 8px 12px; background-color: rgba(0, 0, 0, 0.03); border-bottom: 1px solid rgba(0, 0, 0, 0.1);';

    // Title
    const titleDiv = document.createElement('div');
    titleDiv.style.cssText = 'font-weight: 600; font-size: 14px; color: #6b46c1;';
    titleDiv.textContent = `📐 ${spec.title || 'DrawIO Diagram'}`;

    // Buttons container
    const buttonsDiv = document.createElement('div');
    buttonsDiv.style.cssText = 'display: flex; gap: 8px;';

    const downloadBtn = document.createElement('button');
    downloadBtn.innerHTML = '💾 Download';
    downloadBtn.title = 'Download as .drawio file';
    downloadBtn.style.cssText = 'padding: 4px 12px; border: 1px solid #d9d9d9; background: white; border-radius: 4px; cursor: pointer; font-size: 13px; transition: all 0.2s;';
    downloadBtn.onmouseenter = () => {
        downloadBtn.style.background = '#f0f0f0';
        downloadBtn.style.transform = 'translateY(-1px)';
    };
    downloadBtn.onmouseleave = () => {
        downloadBtn.style.background = 'white';
        downloadBtn.style.transform = 'translateY(0)';
    };
    downloadBtn.onclick = () => {
        // Export the restyled version from the graph if available
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
        const blob = new Blob([exportXml], { type: 'application/xml' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        const filename = (spec.title?.replace(/[^a-z0-9]/gi, '_') || 'diagram') + '.drawio';
        a.download = filename;
        a.click();
        URL.revokeObjectURL(url);
    };

    // Export to DesignInspector button
    const exportBtn = document.createElement('button');
    exportBtn.innerHTML = '📤 Export';
    exportBtn.title = 'Export to DesignInspector';
    exportBtn.style.cssText = 'padding: 4px 12px; border: 1px solid #1890ff; background: #1890ff; color: white; border-radius: 4px; cursor: pointer; font-size: 13px; transition: all 0.2s;';
    exportBtn.onmouseenter = () => {
        exportBtn.style.background = '#096dd9';
        exportBtn.style.transform = 'translateY(-1px)';
    };
    exportBtn.onmouseleave = () => {
        exportBtn.style.background = '#1890ff';
        exportBtn.style.transform = 'translateY(0)';
    };
    exportBtn.onclick = () => {
        downloadBtn.click();

        const modal = document.createElement('div');
        modal.style.cssText = `
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: rgba(0,0,0,0.5);
            display: flex;
            align-items: center;
            justify-content: center;
            z-index: 10000;
        `;

        const content = document.createElement('div');
        content.style.cssText = `
            background: white;
            padding: 24px;
            border-radius: 8px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.15);
            max-width: 500px;
        `;
        content.innerHTML = `
            <h3 style="margin-top: 0; color: #1890ff;">📤 Export to DesignInspector</h3>
            <p>Diagram downloaded as <code>${spec.title || 'diagram'}.drawio</code></p>
            <p><strong>Upload to DesignInspector:</strong></p>
            <ol style="padding-left: 20px; line-height: 1.8;">
                <li>Go to <a href="https://design-inspector.a2z.com" target="_blank" style="color: #1890ff;">design-inspector.a2z.com</a></li>
                <li>Click <strong>"Upload"</strong> or <strong>"Import"</strong></li>
                <li>Select the downloaded .drawio file</li>
            </ol>
            <button onclick="this.closest('[style*=fixed]').remove()" style="padding: 8px 16px; background: #1890ff; color: white; border: none; border-radius: 4px; cursor: pointer; width: 100%; margin-top: 12px;">Got it</button>
        `;

        modal.appendChild(content);
        modal.onclick = (e) => { if (e.target === modal) modal.remove(); };
        document.body.appendChild(modal);
        setTimeout(() => modal.remove(), 30000);
    };

    // Edit button
    const editBtn = document.createElement('button');
    editBtn.innerHTML = '✏️ Edit';
    editBtn.title = 'Open in DrawIO editor';
    editBtn.style.cssText = 'padding: 4px 12px; border: 1px solid #d9d9d9; background: white; border-radius: 4px; cursor: pointer; font-size: 13px; transition: all 0.2s;';
    editBtn.onmouseenter = () => {
        editBtn.style.background = '#f0f0f0';
        editBtn.style.transform = 'translateY(-1px)';
    };
    editBtn.onmouseleave = () => {
        editBtn.style.background = 'white';
        editBtn.style.transform = 'translateY(0)';
    };
    editBtn.onclick = () => {
        const encoded = encodeURIComponent(exportXml || xml);
        const title = encodeURIComponent(spec.title || 'diagram');
        const url = 'https://app.diagrams.net/?title=' + title + '#R' + encoded;
        window.open(url, '_blank');
    };

    // View Source button
    const viewSourceBtn = document.createElement('button');
    viewSourceBtn.innerHTML = '📄 Source';
    viewSourceBtn.title = 'View DrawIO XML source';
    viewSourceBtn.style.cssText = `
        padding: 4px 12px;
        border: 1px solid #d9d9d9;
        background: white;
        border-radius: 4px;
        cursor: pointer;
        font-size: 13px;
        transition: all 0.2s;
    `;
    viewSourceBtn.onmouseenter = () => {
        viewSourceBtn.style.background = '#f0f0f0';
        viewSourceBtn.style.transform = 'translateY(-1px)';
    };
    viewSourceBtn.onmouseleave = () => {
        viewSourceBtn.style.background = 'white';
        viewSourceBtn.style.transform = 'translateY(0)';
    };
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

    buttonsDiv.appendChild(downloadBtn);
    buttonsDiv.appendChild(exportBtn);
    buttonsDiv.appendChild(editBtn);
    buttonsDiv.appendChild(viewSourceBtn);

    controlsDiv.appendChild(titleDiv);
    controlsDiv.appendChild(buttonsDiv);
    container.appendChild(controlsDiv);
};

const renderDrawIO = async (container: HTMLElement, _d3: any, spec: DrawIOSpec, isDarkMode: boolean): Promise<void> => {
    // Store the render function for retry capability
    console.log('📐 DrawIO: renderDrawIO called');

    const attemptRender = async () => {
        // Clear previous content
        container.innerHTML = '';

        if (spec.isStreaming && !spec.forceRender && spec.definition && !isDefinitionComplete(spec.definition)) {
            container.innerHTML = '<div style="padding: 16px; text-align: center; color: #888;">📐 Drawing diagram...</div>';
            return;
        }

        const xml = spec.definition ? normalizeDrawIOXml(spec.definition) : null;

        if (!xml && !spec.url) {
            container.innerHTML = '<div style="padding: 16px; color: #cf1322;">⚠️ No diagram content provided</div>';
            return;
        }

        // Handle URL references (DesignInspector links)
        if (spec.url) {
            const linkDiv = document.createElement('div');
            linkDiv.style.cssText = `
            padding: 16px;
            background: ${isDarkMode ? '#1a1a1a' : '#f8f9fa'};
            border: 2px solid ${isDarkMode ? '#444' : '#ddd'};
            border-radius: 8px;
            text-align: center;
        `;
            linkDiv.innerHTML = `
            <div style="margin-bottom: 12px; font-weight: bold; color: #6b46c1;">
                📐 DesignInspector Diagram
            </div>
            <a href="${spec.url}" target="_blank" style="color: #1890ff; text-decoration: none;">
                ${spec.url}
            </a>
            <div style="margin-top: 12px;">
                <button onclick="window.open('${spec.url}', '_blank')" style="padding: 8px 16px; background: #1890ff; color: white; border: none; border-radius: 4px; cursor: pointer;">
                    Open in DesignInspector →
                </button>
            </div>
        `;
            container.appendChild(linkDiv);
            return;
        }

        try {
            // Lazy load maxgraph
            console.log('📐 DrawIO: About to load maxGraph');
            const maxGraphModule = await loadMaxGraph();
            console.log('📐 DrawIO: maxGraph loaded, module keys:', Object.keys(maxGraphModule).slice(0, 10));

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
            min-height: 400px;
            background: ${isDarkMode ? '#0d1117' : '#ffffff'};
            border: 1px solid ${isDarkMode ? '#30363d' : '#d0d7de'};
            overflow: hidden;
        `;

            // Parse the XML
            // CRITICAL FIX: Clean up common XML syntax errors before parsing
            let cleanedXml = xml!;
            
            // Fix common attribute errors like strokeColor="#6c8ebf;" -> strokeColor=#6c8ebf
            // Remove quotes before # in hex colors
            cleanedXml = cleanedXml.replace(/(\w+)=["']#/g, '$1=#');
            
            // Remove trailing semicolons in attribute values (DrawIO artifact)
            cleanedXml = cleanedXml.replace(/#([0-9a-fA-F]{6});"/g, '#$1"');
            cleanedXml = cleanedXml.replace(/#([0-9a-fA-F]{6});'/g, "#$1'");
            
            // Remove semicolons at end of attribute values that don't have closing quotes
            cleanedXml = cleanedXml.replace(/=([^"'\s>]+);(\s)/g, '=$1$2');
            
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

            // Configure graph for read-only viewing
            graph.setEnabled(false); // Disable editing
            graph.setHtmlLabels(true); // Enable HTML labels for better text rendering
            graph.centerZoom = true;
            graph.setTooltips(true);
            graph.autoSizeCells = true; // Ensure labels are sized properly
            graph.setConnectable(false); // Read-only mode

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
                const vertexCells: Array<{id: string, element: Element}> = [];
                const edgeCells: Array<{id: string, element: Element}> = [];

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
                            if (vertex && styleObj['fillColor'] && !styleObj['fontColor']) {
                                if (isSwimlane) {
                                    // Swimlane labels should be at the top, not centered
                                    styleObj['verticalAlign'] = 'top';
                                    styleObj['align'] = 'center';
                                    styleObj['spacingTop'] = 4;
                                    styleObj['fontSize'] = styleObj['fontSize'] || 12;
                                    styleObj['fontStyle'] = styleObj['fontStyle'] || 1; // Bold
                                    
                                    // Ensure label area is visible
                                    if (!styleObj['startSize']) {
                                        styleObj['startSize'] = 26;
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
                                
                                styleObj['fontColor'] = '#000000';
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
                            edgeCells.push({id: cellId, element: cellElement});
                        } else if (vertex) {
                            vertexCells.push({id: cellId, element: cellElement});
                        }
                    }
                });

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
                
                edgeCells.forEach(({id, element}) => {
                    const sourceId = element.getAttribute('source');
                    const targetId = element.getAttribute('target');
                    
                    if (sourceId && targetId) {
                        // Track edge directions
                        if (!vertexEdgeDirections.has(sourceId)) {
                            vertexEdgeDirections.set(sourceId, {incoming: [], outgoing: []});
                        }
                        vertexEdgeDirections.get(sourceId)!.outgoing.push(id);
                        
                        if (!vertexEdgeDirections.has(targetId)) {
                            vertexEdgeDirections.set(targetId, {incoming: [], outgoing: []});
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
                        .map(([vId, dirs]) => ({vId, in: dirs.incoming.length, out: dirs.outgoing.length})),
                    bidirectionalPairs: Array.from(edgePairs.entries()).filter(([_, edges]) => edges.length > 1)
                });

                // Separate swimlanes/containers from regular vertices for proper z-ordering
                const swimlaneVertices = vertexCells.filter(({id}) => {
                    const cell = cellMap.get(id);
                    const style = cell?.getStyle();
                    return style && (style['swimlane'] || style['container']);
                });
                const regularVertices = vertexCells.filter(({id}) => !swimlaneVertices.find(v => v.id === id));
                
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
                                
                                // CRITICAL FIX: Separate in/out attachment points for rectangles,
                                // and offset bidirectional pairs
                                const currentStyle = cell.getStyle();
                                
                                if (currentStyle &&
                                    currentStyle['exitX'] === undefined && 
                                    currentStyle['exitY'] === undefined &&
                                    currentStyle['entryX'] === undefined && 
                                    currentStyle['entryY'] === undefined) {
                                    
                                    // Check if this is a bidirectional pair (A↔B)
                                    const pairKey = sourceId < targetId ? `${sourceId}-${targetId}` : `${targetId}-${sourceId}`;
                                    const pairEdges = edgePairs.get(pairKey) || [];
                                    const isBidirectional = pairEdges.length > 1;
                                    
                                    if (isBidirectional) {
                                        // CASE 1: True bidirectional - offset to prevent overlap
                                        const sourceGeom = sourceCell?.getGeometry();
                                        const targetGeom = targetCell?.getGeometry();
                                        
                                        if (sourceGeom && targetGeom) {
                                            const dx = targetGeom.x + targetGeom.width/2 - (sourceGeom.x + sourceGeom.width/2);
                                            const dy = targetGeom.y + targetGeom.height/2 - (sourceGeom.y + sourceGeom.height/2);
                                            const isHorizontal = Math.abs(dx) > Math.abs(dy);
                                            
                                            const edgeIndex = pairEdges.indexOf(id);
                                            const offset = edgeIndex === 0 ? -0.2 : 0.2;
                                            
                                            if (isHorizontal) {
                                                currentStyle['exitY'] = 0.5 + offset;
                                                currentStyle['entryY'] = 0.5 + offset;
                                            } else {
                                                currentStyle['exitX'] = 0.5 + offset;
                                                currentStyle['entryX'] = 0.5 + offset;
                                            }
                                            
                                            cell.setStyle(currentStyle);
                                            console.log(`📐 DrawIO: Offset bidirectional edge ${id} by ${offset}`);
                                        }
                                    } else {
                                        // CASE 2: Check for in/out on rectangles (skip rhombus/ellipse/special shapes)
                                        const sourceStyle = sourceCell?.getStyle();
                                        const targetStyle = targetCell?.getStyle();
                                        
                                        const isSourceRectangle = sourceStyle && 
                                            !sourceStyle['rhombus'] && 
                                            !sourceStyle['ellipse'] && 
                                            sourceStyle['shape'] !== 'rhombus' &&
                                            sourceStyle['shape'] !== 'ellipse';
                                            
                                        const isTargetRectangle = targetStyle && 
                                            !targetStyle['rhombus'] && 
                                            !targetStyle['ellipse'] &&
                                            targetStyle['shape'] !== 'rhombus' &&
                                            targetStyle['shape'] !== 'ellipse';
                                        
                                        const sourceDirs = vertexEdgeDirections.get(sourceId);
                                        const targetDirs = vertexEdgeDirections.get(targetId);
                                        
                                        const sourceHasBoth = sourceDirs && sourceDirs.incoming.length > 0 && sourceDirs.outgoing.length > 0;
                                        const targetHasBoth = targetDirs && targetDirs.incoming.length > 0 && targetDirs.outgoing.length > 0;
                                        
                                        // Only adjust rectangles with both in/out
                                        if ((isSourceRectangle && sourceHasBoth) || (isTargetRectangle && targetHasBoth)) {
                                            const sourceGeom = sourceCell?.getGeometry();
                                            const targetGeom = targetCell?.getGeometry();
                                            
                                            if (sourceGeom && targetGeom) {
                                                const dx = targetGeom.x + targetGeom.width/2 - (sourceGeom.x + sourceGeom.width/2);
                                                const dy = targetGeom.y + targetGeom.height/2 - (sourceGeom.y + sourceGeom.height/2);
                                                const angle = Math.atan2(dy, dx) * (180 / Math.PI);
                                                
                                                // Use angle-based attachment (respects natural flow direction)
                                                if (isSourceRectangle && sourceHasBoth) {
                                                    if (angle >= -45 && angle < 45) { currentStyle['exitX'] = 1.0; currentStyle['exitY'] = 0.5; }
                                                    else if (angle >= 45 && angle < 135) { currentStyle['exitX'] = 0.5; currentStyle['exitY'] = 1.0; }
                                                    else if (angle >= 135 || angle < -135) { currentStyle['exitX'] = 0.0; currentStyle['exitY'] = 0.5; }
                                                    else { currentStyle['exitX'] = 0.5; currentStyle['exitY'] = 0.0; }
                                                }
                                                
                                                if (isTargetRectangle && targetHasBoth) {
                                                    const reverseAngle = (angle + 180) % 360 - 180;
                                                    if (reverseAngle >= -45 && reverseAngle < 45) { currentStyle['entryX'] = 0.0; currentStyle['entryY'] = 0.5; }
                                                    else if (reverseAngle >= 45 && reverseAngle < 135) { currentStyle['entryX'] = 0.5; currentStyle['entryY'] = 0.0; }
                                                    else if (reverseAngle >= 135 || reverseAngle < -135) { currentStyle['entryX'] = 1.0; currentStyle['entryY'] = 0.5; }
                                                    else { currentStyle['entryX'] = 0.5; currentStyle['entryY'] = 1.0; }
                                                }
                                                
                                                cell.setStyle(currentStyle);
                                                console.log(`📐 DrawIO: Separated in/out for rectangle edge ${id}`);
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                });
                // CRITICAL FIX: After adding all cells, refresh the view to apply styles
                console.log('📐 DrawIO: Refreshing graph view to apply cell styles');
                
                // DEBUG: Check what styles the graph sees
                console.log('📐 DEBUG: Checking cell styles in graph model:');
                Object.keys(model.cells || {}).forEach(cellId => {
                    const cell = model.cells[cellId];
                    if (cell && cellId !== '0' && cellId !== '1') {
                        console.log('📐 DEBUG: Cell', cellId, {
                            value: cell.getValue(),
                            style: cell.getStyle(),
                            isVertex: cell.isVertex(),
                            isEdge: cell.isEdge()
                        });
                    }
                });
                
                // CRITICAL FIX: Set reasonable arrow size defaults before rendering
                // maxGraph uses very large default arrow sizes, we need to override them
                console.log('📐 DrawIO: Configuring stylesheet defaults');
                const stylesheet = graph.getStylesheet();
                
                // Configure default vertex style
                const defaultVertexStyle = stylesheet.getDefaultVertexStyle();
                
                const defaultEdgeStyle = stylesheet.getDefaultEdgeStyle();
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
                
                // Vertex label defaults
                defaultVertexStyle['fontColor'] = '#000000';
                defaultVertexStyle['fontSize'] = 12;
                stylesheet.putDefaultVertexStyle(defaultVertexStyle);
                
                console.log('📐 DEBUG: Graph stylesheet:', stylesheet);
                graph.view.clear(); // Clear any cached view states
                graph.view.validate(); // Rebuild view states with current cell styles
                graph.refresh(); // Force complete redraw with styles
                
                // DEBUG: Check view states after refresh
                console.log('📐 DEBUG: After refresh, checking view states:');
                console.log('📐 DEBUG: graph.view.states type:', typeof graph.view.states);
                console.log('📐 DEBUG: graph.view.states:', graph.view.states);
                
                // CRITICAL FIX: graph.view.states is NOT a Map, it's a CellStatePreview object
                // with a .map property that IS a Map. We need to access .map directly
                const statesMap = (graph.view.states as any)?.map;
                console.log('📐 DEBUG: statesMap extracted:', statesMap);
                console.log('📐 DEBUG: statesMap type:', typeof statesMap);
                console.log('📐 DEBUG: statesMap constructor:', statesMap?.constructor?.name);
                console.log('📐 DEBUG: is Map?:', statesMap instanceof Map);
                console.log('📐 DEBUG: statesMap keys:', statesMap ? Array.from(Object.keys(statesMap)).slice(0, 5) : 'none');
                
                if (statesMap && typeof statesMap === 'object') {
                    const stateEntries = Object.entries(statesMap);
                    console.log('📐 DEBUG: View states object found, entries:', stateEntries.length);
                    
                    console.log('📐 DEBUG: model.cells keys:', Object.keys(model.cells || {}));
                    
                    // View states are now created correctly with object styles
                    console.log('📐 DEBUG: Styles applied via cell.setStyle() object format');
                } else {
                    console.warn('📐 DEBUG: statesMap is not an object or is null, cannot apply styles');
                }

                const cellCount = model.cells ? Object.keys(model.cells).length : 0;
                console.log('📐 DrawIO: Decode complete, cells in model:', cellCount);

                if (cellCount <= 2) {
                    throw new Error('Diagram appears empty - only root cells exist');
                }

                // Force immediate view update
                graph.view.validate();
                graph.sizeDidChange();
            } finally {
                model.endUpdate();
            }
            
            console.log('📐 DrawIO: Model update complete, cells in model:', {
                totalCells: model.cells ? Object.keys(model.cells).length : 0,
                cellIds: model.cells ? Object.keys(model.cells) : []
            });
            
            // Force immediate view update
            graph.view.validate();
            graph.sizeDidChange();

            const zoomControlSpace = 80; // 64px for controls + 16px bottom margin

            console.log('📐 DrawIO: ==================== FIT AND CENTER START ====================');

            try {
                graph.refresh();

                const bounds = graph.getGraphBounds();
                console.log('📐 DrawIO: Content bounds from graph:', {
                    x: bounds.x,
                    y: bounds.y,
                    width: bounds.width,
                    height: bounds.height,
                    right: bounds.x + bounds.width,
                    bottom: bounds.y + bounds.height
                });

                if (bounds && bounds.width > 0 && bounds.height > 0) {
                    // Use a fixed container width for initial calculation
                    const containerWidth = 800;
                    const marginX = 40;
                    const marginY = 40;
                    
                    // Calculate scale to fit content in original container dimensions
                    const scaleX = (containerWidth - marginX * 2) / bounds.width;
                    const scaleY = 4; // Allow reasonable vertical scaling
                    const scale = Math.min(scaleX, scaleY, 1);

                    console.log('📐 DrawIO: Scale calculation:', {
                        scaleX: scaleX,
                        scaleY: scaleY,
                        finalScale: scale,
                        limitedBy: scale === scaleX ? 'width' : (scale === scaleY ? 'height' : 'max(1)')
                    });

                    // Calculate needed height to fit scaled content plus space for zoom controls
                    const scaledContentHeight = bounds.height * scale;
                    const scaledContentWidth = bounds.width * scale;
                    const neededHeight = Math.max(scaledContentHeight + marginY * 2, 400) + zoomControlSpace;

                    console.log('📐 DrawIO: Adjusting container height:', {
                        boundsHeight: bounds.height,
                        scaledContentHeight: scaledContentHeight,
                        withMargins: scaledContentHeight + marginY * 2,
                        minHeight: 400,
                        zoomControlSpace: zoomControlSpace,
                        neededHeight: neededHeight,
                        scale: scale
                    });

                    // Set the container to the calculated height
                    graphContainer.style.height = `${neededHeight}px`;

                    console.log('📐 DrawIO: Set container style.height to:', neededHeight);
                    // Wait for browser to apply the height change
                    await new Promise(resolve => setTimeout(resolve, 50));
                    
                    // Force graph to update with new dimensions
                    graph.sizeDidChange();

                    console.log('📐 DrawIO: Graph after sizeDidChange:', {
                        viewScale: graph.view.scale,
                        viewTranslate: { x: graph.view.translate.x, y: graph.view.translate.y }
                    });

                    console.log('📐 DrawIO: Container after height adjustment:', {
                        clientHeight: graphContainer.clientHeight,
                        styleHeight: graphContainer.style.height
                    });
                    
                    // Check if SVG exists and has content
                    const svgCheck = graphContainer.querySelector('svg');
                    console.log('📐 DrawIO: SVG check before scaling:', {
                        exists: !!svgCheck,
                        children: svgCheck?.children.length
                    });

                    // Set scale
                    graph.view.setScale(scale);
                    
                    // Center the scaled bounds in the viewport
                    // Make sure we account for the actual position of the content bounds
                    const finalContainerHeight = neededHeight - zoomControlSpace;
                    
                    console.log('📐 DrawIO: Centering calculation inputs:', {
                        containerWidth: containerWidth,
                        finalContainerHeight: finalContainerHeight,
                        boundsX: bounds.x,
                        boundsY: bounds.y,
                        boundsWidth: bounds.width,
                        boundsHeight: bounds.height,
                        scale: scale
                    });

                    // Calculate translation to center content
                    // We want to move the content so its center aligns with the container center
                    const contentCenterX = bounds.x + bounds.width / 2;
                    const contentCenterY = bounds.y + bounds.height / 2;
                    const containerCenterX = containerWidth / 2;
                    const containerCenterY = finalContainerHeight / 2;
                    
                    console.log('📐 DrawIO: Center points:', {
                        contentCenterX, contentCenterY,
                        containerCenterX, containerCenterY
                    });
                    
                    const dx = (containerCenterX - contentCenterX * scale);
                    const dy = (containerCenterY - contentCenterY * scale);
                    graph.view.setTranslate(dx, dy);

                    console.log('📐 DrawIO: About to refresh graph after transforms');
                    
                    // Critical: validate view and refresh to apply transformations
                    graph.view.validate();
                    graph.refresh();
                    
                    console.log('📐 DrawIO: Graph refreshed, checking rendering');
                    
                    // Verify cells are actually rendered
                    const renderedCells = graph.view.states.size;
                    console.log('📐 DrawIO: Rendered cell states:', renderedCells);

                    await new Promise(resolve => setTimeout(resolve, 100));

                    console.log('📐 DrawIO: Manual center with translate:', { dx, dy });
                    
                    // Fix SVG dimensions
                    const svgElement = graphContainer.querySelector('svg');
                    if (svgElement) {
                        svgElement.style.width = '100%';
                        svgElement.style.height = `${neededHeight}px`;
                        svgElement.setAttribute('width', containerWidth.toString());
                        svgElement.setAttribute('height', neededHeight.toString());
                        
                        // Log SVG content
                        console.log('📐 DrawIO: SVG structure:', {
                            children: svgElement.children.length,
                            childTags: Array.from(svgElement.children).map(c => c.tagName),
                            innerHTML: svgElement.innerHTML.substring(0, 500)
                        });

                        console.log('📐 DrawIO: Final graph state:', {
                            scale: graph.view.scale,
                            translateX: graph.view.translate.x,
                            translateY: graph.view.translate.y,
                            containerWidth,
                            containerHeight: neededHeight
                        });
                        console.log('📐 DrawIO: SVG dimensions after fit:', {
                        width: svgElement.getAttribute('width'),
                        height: svgElement.getAttribute('height'),
                        viewBox: svgElement.getAttribute('viewBox'),
                        styleWidth: svgElement.style.width,
                        styleHeight: svgElement.style.height
                    });
                }
                } else {
                    console.warn('📐 DrawIO: Invalid bounds:', bounds);
                    graph.view.setScale(1);
                    graph.view.setTranslate(0, 0);
                    graphContainer.style.height = '600px';
                }
            } catch (fitError) {
                console.error('📐 DrawIO: Error during fit/center:', fitError);
                // Fallback to reasonable defaults
                graph.view.setScale(1);
                graph.center(true, true);
            }

            console.log('📐 DrawIO: ==================== FIT AND CENTER END ====================');
            
            // Add zoom controls to graphContainer before appending
            console.log('📐 DrawIO: Adding zoom controls');
            addZoomControls(graphContainer, graph);
            
            // Now append everything to parent container
            container.innerHTML = ''; // Clear any previous content
            container.appendChild(graphContainer);
            
            console.log('📐 DrawIO: Container appended, zoom controls should be visible');
            console.log('📐 DrawIO: ==================== FIT AND CENTER END ====================');
            
            // Add zoom controls to graphContainer before appending
            console.log('📐 DrawIO: Adding zoom controls to graphContainer');
            addZoomControls(graphContainer, graph);
            
            // Add controls AFTER graph is fully rendered so we can export the restyled version
            if (xml) {
                createControls(container, spec, xml, isDarkMode, graph);
            }
            
            // Now append everything to parent container
            container.innerHTML = ''; // Clear any previous content
            container.appendChild(graphContainer);
            
            console.log('✅ DrawIO diagram rendered successfully');

        } catch (error) {
            console.error('📐 DrawIO rendering error:', error);
            console.error('📐 DrawIO error stack:', error instanceof Error ? error.stack : 'no stack');

            // Show error in container
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
                        <summary>Show Definition</summary>
                        <pre><code>${spec.definition || ''}</code></pre>
                    </details>
                </div>
            `;
        }
    };

    await attemptRender();
};

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
