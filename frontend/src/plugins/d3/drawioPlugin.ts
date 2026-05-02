import { D3RenderPlugin } from '../../types/d3';

// Import maxGraph CSS for proper rendering
import '@maxgraph/core/css/common.css';

import { loadStencilsForShapes } from './drawioStencilLoader';
import { iconRegistry } from './iconRegistry';
import { enhanceSVGVisibility, isLightBackground, getOptimalTextColor, hexToRgb, calculateContrastRatio } from '../../utils/colorUtils';
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
        __maxGraphArrowOverridden?: boolean;
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

    // CRITICAL FIX: Remove over-quoted color values in style attributes
    // This handles the malformed pattern: style="...fillColor="#fff9c4";..." 
    // which breaks XML parsing because the quote before # ends the style attribute
    // 
    // Strategy: Find patterns like ="#hexcolor" or ="number" within what looks like
    // a style context (preceded by a style key like fillColor, strokeColor, etc.)
    // and remove the quotes around the value

    // Fix quoted hex colors: fillColor="#fff9c4" -> fillColor=#fff9c4
    // This pattern looks for colorKeyword="# and removes the quotes
    normalized = normalized.replace(
        /(\w*[Cc]olor\w*)="(#[0-9a-fA-F]{3,8})"/g,
        '$1=$2'
    );

    // Fix quoted numbers: fontSize="16" -> fontSize=16 (within style context)
    normalized = normalized.replace(
        /(fontSize|strokeWidth|opacity|spacing\w*)="(\d+)"/g,
        '$1=$2'
    );

    console.log('📐 DrawIO: Removed over-quoted values in style attributes');

    // Fix unquoted XML tag-level attributes (e.g. vertex=1 -> vertex="1")
    // Process each tag individually so we don't corrupt values inside
    // already-quoted attributes (e.g. value="Group=fsw" must stay intact)
    normalized = normalized.replace(/<[a-zA-Z]\w*\b[^>]*>/g, (tag) => {
        // Mask properly-quoted attribute values so fix regexes skip over them
        const placeholders: string[] = [];
        let masked = tag.replace(/\w+="[^"]*"/g, (m) => {
            placeholders.push(m);
            return `__QATTR${placeholders.length - 1}__`;
        });
        masked = masked.replace(/\w+='[^']*'/g, (m) => {
            placeholders.push(m);
            return `__QATTR${placeholders.length - 1}__`;
        });

        // Fix unquoted attributes in the masked tag (only tag-level ones remain)
        masked = masked.replace(/(\s)(\w+)=(#[0-9a-fA-F]{3,8})(?=[\s>\/])/g, '$1$2="$3"');
        masked = masked.replace(/(\s)(\w+[Cc]olor)=([0-9a-fA-F]{6})(?=[\s>\/])/g, '$1$2="$3"');
        masked = masked.replace(/(\s)(\w+)=([a-zA-Z][a-zA-Z0-9_.]*)(?=[\s>\/])/g, '$1$2="$3"');
        masked = masked.replace(/(\s)(\w+)=(\d+)(?=[\s>\/])/g, '$1$2="$3"');

        // Restore masked quoted attributes
        masked = masked.replace(/__QATTR(\d+)__/g, (_, idx) => placeholders[parseInt(idx)]);
        return masked;
    });

    console.log('📐 DrawIO: Fixed unquoted tag-level attributes');

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

    // CRITICAL FIX: Clamp relative geometry values for edge labels
    // MaxGraph rejects x/y values outside [-1, 1] range for relative geometries
    // Edge labels with x="-0.1" etc need to be clamped to valid range
    // ONLY apply to geometries with relative="1" — vertex geometries use absolute pixel coords
    normalized = normalized.replace(
        /<mxGeometry\s+([^>]*?)x="(-?\d+\.?\d*)"([^>]*?)>/g,
        (match, before, xValue, after) => {
            // Only clamp if this geometry has relative="1" (edge labels)
            if (!match.includes('relative="1"')) return match;
            const x = parseFloat(xValue);
            const clampedX = Math.max(-1, Math.min(1, isNaN(x) ? 0 : x));
            return `<mxGeometry ${before}x="${clampedX}"${after}>`;
        }
    );
    normalized = normalized.replace(
        /<mxGeometry\s+([^>]*?)y="(-?\d+\.?\d*)"([^>]*?)>/g,
        (match, before, yValue, after) => {
            // Only clamp if this geometry has relative="1" (edge labels)
            if (!match.includes('relative="1"')) return match;
            const y = parseFloat(yValue);
            const clampedY = Math.max(-1, Math.min(1, isNaN(y) ? 0 : y));
            return `<mxGeometry ${before}y="${clampedY}"${after}>`;
        }
    );
    console.log('📐 DrawIO: Clamped relative geometry values to valid range');

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
            // Since 0.6.0, codecs must be registered before encode/decode
            if (maxGraphModule.registerCoreCodecs) {
                maxGraphModule.registerCoreCodecs();
            }
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
    controlsDiv.className = 'diagram-actions drawio-controls';

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

    // Edit button - toggle edit mode with grid
    const editBtn = document.createElement('button');
    editBtn.innerHTML = '✏️ Edit';
    editBtn.className = 'diagram-action-button';
    editBtn.title = 'Enable editing mode with grid';

    let isEditMode = false;

    editBtn.onclick = () => {
        if (!graph) return;

        isEditMode = !isEditMode;

        const graphContainer = container.querySelector('.drawio-graph-container') as HTMLElement;

        if (isEditMode) {
            // Enable edit mode
            editBtn.innerHTML = '👁️ View';
            editBtn.title = 'Exit editing mode';

            // Enable graph interaction
            graph.setEnabled(true);
            graph.setConnectable(true);

            // Enable grid
            graph.view.gridEnabled = true;

            // Set background to show grid properly
            if (graphContainer) {
                graphContainer.style.background = isDarkMode ? '#0d1117' : '#ffffff';
                // Add grid pattern
                graphContainer.style.backgroundImage = `
                    linear-gradient(${isDarkMode ? '#ffffff11' : '#00000011'} 1px, transparent 1px),
                    linear-gradient(90deg, ${isDarkMode ? '#ffffff11' : '#00000011'} 1px, transparent 1px)
                `;
                graphContainer.style.backgroundSize = '10px 10px';
            }

            // Enable snap to grid
            graph.setGridEnabled(true);
            graph.setGridSize(10);

            // Allow moving and resizing
            graph.setCellsMovable(true);
            graph.setCellsResizable(true);

            console.log('📐 DrawIO: Edit mode ENABLED');
        } else {
            // Disable edit mode
            editBtn.innerHTML = '✏️ Edit';
            editBtn.title = 'Enable editing mode with grid';

            // Disable graph interaction
            graph.setEnabled(false);
            graph.setConnectable(false);

            // Disable grid
            graph.view.gridEnabled = false;

            // Restore transparent background
            if (graphContainer) {
                graphContainer.style.background = 'transparent';
                graphContainer.style.backgroundImage = 'none';
            }

            // Disable snap to grid
            graph.setGridEnabled(false);

            // Disable moving and resizing
            graph.setCellsMovable(false);
            graph.setCellsResizable(false);

            console.log('📐 DrawIO: Edit mode DISABLED');
        }

        graph.refresh();

        // graph.refresh() revalidates the view and rewrites every cell's
        // SVG, which wipes the text-cell CSS margin-left corrections applied
        // by DrawIOEnhancer.forceTextCellPositioning. Re-apply them after the
        // refresh settles, on the next tick and again briefly after, so the
        // text cells keep their corrected positions when toggling edit mode.
        const svg = graphContainer?.querySelector('svg') as SVGSVGElement | null;
        if (svg) {
            setTimeout(() => DrawIOEnhancer.forceTextCellPositioning(svg, graph), 0);
            setTimeout(() => DrawIOEnhancer.forceTextCellPositioning(svg, graph), 50);
            // Also re-apply the main enhancer so container-label clamps
            // (e.g. the trust-boundary outline label) get restored after
            // maxGraph's refresh wipes them.
            setTimeout(() => DrawIOEnhancer.fixAllForeignObjects(svg, graph), 0);
            setTimeout(() => DrawIOEnhancer.fixAllForeignObjects(svg, graph), 50);
        }
    };

    // Open button - pop out diagram into its own resizable window
    const openBtn = document.createElement('button');
    openBtn.innerHTML = '↗️ Open';
    openBtn.className = 'diagram-action-button';
    openBtn.title = 'Open diagram in a new window';
    openBtn.onclick = () => {
        const svgEl = container.querySelector('.drawio-graph-container svg') as SVGSVGElement | null;
        if (!svgEl) {
            console.warn('DrawIO: no SVG available to open');
            return;
        }

        // Clone so we don't mutate the on-page SVG
        const svgClone = svgEl.cloneNode(true) as SVGSVGElement;

        // Ensure the SVG has a viewBox so it can scale responsively.
        // mxGraph-rendered SVGs typically use absolute width/height with no viewBox,
        // which prevents CSS-based scaling.
        if (!svgClone.getAttribute('viewBox')) {
            let vbW = 0, vbH = 0;
            const wAttr = parseFloat(svgEl.getAttribute('width') || '');
            const hAttr = parseFloat(svgEl.getAttribute('height') || '');
            if (isFinite(wAttr) && isFinite(hAttr) && wAttr > 0 && hAttr > 0) {
                vbW = wAttr; vbH = hAttr;
            } else {
                try {
                    const bbox = (svgEl as any).getBBox();
                    vbW = bbox.width || svgEl.clientWidth || 800;
                    vbH = bbox.height || svgEl.clientHeight || 600;
                } catch {
                    vbW = svgEl.clientWidth || 800;
                    vbH = svgEl.clientHeight || 600;
                }
            }
            svgClone.setAttribute('viewBox', `0 0 ${vbW} ${vbH}`);
        }
        svgClone.setAttribute('preserveAspectRatio', 'xMidYMid meet');
        svgClone.removeAttribute('width');
        svgClone.removeAttribute('height');
        svgClone.style.width = '100%';
        svgClone.style.height = '100%';
        svgClone.style.display = 'block';

        const svgData = new XMLSerializer().serializeToString(svgClone);
        const title = (spec.title || 'DrawIO Diagram').replace(/[<>]/g, '');
        const bg = isDarkMode ? '#1f1f1f' : '#ffffff';
        const fg = isDarkMode ? '#e6e6e6' : '#24292e';
        const toolbarBg = isDarkMode ? '#2d2d2d' : '#f1f3f5';
        const toolbarBorder = isDarkMode ? '#30363d' : '#dee2e6';

        const htmlContent = `
<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>${title}</title>
<style>
  html, body { margin:0; padding:0; height:100%; background:${bg}; color:${fg};
    font-family: system-ui, -apple-system, sans-serif; }
  body { display:flex; flex-direction:column; }
  .toolbar { background:${toolbarBg}; border-bottom:1px solid ${toolbarBorder};
    padding:8px; display:flex; gap:8px; align-items:center; }
  .toolbar button { background:#1890ff; color:#fff; border:none; border-radius:4px;
    padding:6px 12px; cursor:pointer; font-size:13px; }
  .toolbar button:hover { background:#40a9ff; }
  .container { flex:1; overflow:auto; position:relative; }
  /* Viewport grows with zoom so container's overflow:auto gives real scroll
     area to pan against. At scale=1 it fills the container exactly. */
  #viewport { width:100%; height:100%; min-width:100%; min-height:100%;
    display:flex; align-items:center; justify-content:center; padding:20px;
    box-sizing:border-box; transition: width 0.15s ease, height 0.15s ease; }
  #viewport svg { width:100%; height:100%; display:block; }
</style></head>
<body>
  <div class="toolbar">
    <strong style="margin-right:12px;">${title}</strong>
    <button onclick="zoomIn()">Zoom In</button>
    <button onclick="zoomOut()">Zoom Out</button>
    <button onclick="resetZoom()">Reset</button>
    <button onclick="downloadSvg()">Download SVG</button>
  </div>
  <div class="container"><div id="viewport">${svgData}</div></div>
  <script>
    let scale = 1;
    const vp = document.getElementById('viewport');
    const container = document.querySelector('.container');
    // Click-and-drag panning
    let isDown = false, sx = 0, sy = 0, sl = 0, st = 0;
    container.style.cursor = 'grab';
    container.addEventListener('mousedown', function(e) {
      if (e.button !== 0) return;
      if (e.target.closest('button')) return;
      isDown = true; sx = e.pageX; sy = e.pageY;
      sl = container.scrollLeft; st = container.scrollTop;
      container.style.cursor = 'grabbing';
      e.preventDefault();
    });
    window.addEventListener('mouseup', function() {
      if (!isDown) return; isDown = false; container.style.cursor = 'grab';
    });
    window.addEventListener('mousemove', function(e) {
      if (!isDown) return;
      container.scrollLeft = sl - (e.pageX - sx);
      container.scrollTop = st - (e.pageY - sy);
    });
    function apply() {
      const pct = (scale * 100) + '%';
      vp.style.width = pct;
      vp.style.height = pct;
    }
    function zoomIn() { scale = Math.min(scale * 1.2, 10); apply(); }
    function zoomOut() { scale = Math.max(scale / 1.2, 0.1); apply(); }
    function resetZoom() { scale = 1; apply(); }
    window.addEventListener('wheel', function(e) {
      if (e.ctrlKey || e.metaKey) {
        e.preventDefault();
        if (e.deltaY < 0) zoomIn(); else zoomOut();
      }
    }, { passive: false });
    function downloadSvg() {
      const svg = vp.querySelector('svg');
      if (!svg) return;
      const data = new XMLSerializer().serializeToString(svg);
      const doc = '<?xml version="1.0" encoding="UTF-8"?>\\n' + data;
      const blob = new Blob([doc], { type: 'image/svg+xml' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = ${JSON.stringify((spec.title?.replace(/[^a-z0-9]/gi, '_') || 'diagram') + '.svg')};
      a.click();
      setTimeout(function() { URL.revokeObjectURL(url); }, 1000);
    }
  </script>
</body></html>`;

        const blob = new Blob([htmlContent], { type: 'text/html' });
        const url = URL.createObjectURL(blob);

        const svgRect = svgEl.getBoundingClientRect();
        const width = Math.min(Math.max(Math.round(svgRect.width) + 60, 600), 1600);
        const height = Math.min(Math.max(Math.round(svgRect.height) + 120, 500), 1200);

        const popupWindow = window.open(
            url,
            'DrawIODiagram',
            `width=${width},height=${height},resizable=yes,scrollbars=yes,status=no,toolbar=no,menubar=no,location=no`
        );
        if (popupWindow) popupWindow.focus();
        setTimeout(() => URL.revokeObjectURL(url), 10000);
    };

    controlsDiv.appendChild(viewSourceBtn);
    // controlsDiv.appendChild(editBtn); // Disabled - external site privacy concern
    controlsDiv.appendChild(exportBtn);
    controlsDiv.appendChild(copyBtn);
    controlsDiv.appendChild(editBtn);
    controlsDiv.appendChild(openBtn);

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
            // In 0.22+, use StyleDefaultsConfig for global defaults.
            // EdgeMarkerRegistry replaces the removed MarkerShape registry.
            if (!window.__maxGraphArrowOverridden) {
                console.log('📐 DrawIO: Installing arrow size overrides for smaller arrows');

                // Use StyleDefaultsConfig (0.22+) with Constants fallback (pre-0.22)
                if (maxGraphModule.StyleDefaultsConfig) {
                    maxGraphModule.StyleDefaultsConfig.arrowSize = 6;
                    maxGraphModule.StyleDefaultsConfig.markerSize = 6;
                    console.log('📐 DrawIO: Set StyleDefaultsConfig.arrowSize=6, markerSize=6');
                } else if (maxGraphModule.constants) {
                    maxGraphModule.constants.ARROW_SIZE = 6;
                    console.log('📐 DrawIO: Set constants.ARROW_SIZE=6 (legacy path)');
                }

                window.__maxGraphArrowOverridden = true;
            }

            if (!maxGraphModule.Graph) throw new Error('maxGraph.Graph not found in module');

            // Import Graph and Codec from maxgraph
            const { Graph, Codec, Cell, Geometry, Point } = maxGraphModule;

            // Create controls
            // Note: Controls created later after graph is rendered so we can export restyled version

            // Create container for the graph
            const graphContainer = document.createElement('div');
            graphContainer.className = 'drawio-graph-container';
            graphContainer.style.cssText = `
            position: relative;
            min-height: 600px;
            background: transparent;
            border: 1px solid ${isDarkMode ? '#30363d88' : '#d0d7de88'};;
            overflow: auto;
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
                const rootModel = xmlDocX.querySelector('mxGraphModel');
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

            // Override removeCells so that deleting a container also deletes all
            // of its descendants. Without this, maxGraph reparents children to the
            // container's parent (root) instead of removing them.
            const _origRemoveCells = graph.removeCells.bind(graph);
            graph.removeCells = function(cells: any[] = [], includeEdges: boolean = true) {
                const allCells = [...cells];
                const collectDescendants = (cell: any) => {
                    if (cell.children && cell.children.length > 0) {
                        cell.children.forEach((child: any) => {
                            allCells.push(child);
                            collectDescendants(child);
                        });
                    }
                };
                cells.forEach(collectDescendants);
                return _origRemoveCells(allCells, includeEdges);
            };

            // Disable tree images to avoid 404s - use CSS styling instead
            // In 0.22, constants are exported as a namespace (lowercase)
            if (maxGraphModule.constants) {
                maxGraphModule.constants.STYLE_IMAGE = null;
            }
            // Read-only viewer: disable folding entirely.  This stops
            // CellRenderer from emitting the collapse/expand <image>
            // elements at all, which avoids the /collapsed.gif and
            // /expanded.gif requests (maxGraph's Client.imageBasePath
            // defaults to '.', so those resolve to the page root).
            if (graph.options) graph.options.foldingEnabled = false;

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
                const viewProto = Object.getPrototypeOf(this);
                const originalFn = viewProto.updateFixedTerminalPoint || maxGraphModule.GraphView?.prototype?.updateFixedTerminalPoint;
                if (originalFn) originalFn.apply(this, arguments);

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
            defaultEdgeStyle['endSize'] = 3; // Small arrow heads — rendered size is (endSize + strokeWidth) * viewScale
            defaultEdgeStyle['startArrow'] = 'none';
            defaultEdgeStyle['startSize'] = 3;
            defaultEdgeStyle['strokeWidth'] = 1.5;

            // Edge labels: no white background (obscures the line behind
            // them). Rely on font contrast against the diagram background.
            defaultEdgeStyle['labelBackgroundColor'] = 'none';
            defaultEdgeStyle['labelBorderColor'] = 'none';
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
                                // Arrow marker sizing: maxGraph computes marker extent
                                // as (endSize + strokeWidth) * viewScale. With fit()
                                // scaling the diagram ~2x, keep endSize small.
                                if (!styleObj['endArrow']) {
                                    styleObj['endArrow'] = 'classicThin';
                                }
                                styleObj['endSize'] = 3;
                                if (!styleObj['startArrow'] || styleObj['startArrow'] === 'none') {
                                    styleObj['startSize'] = 3;
                                }

                                // Remove author's labelBackgroundColor if
                                // set — we don't want white backgrounds
                                // blocking the line behind labels.
                                delete styleObj['labelBackgroundColor'];
                                delete styleObj['labelBorderColor'];
                                styleObj['spacingTop'] = styleObj['spacingTop'] || 2;
                                styleObj['spacingBottom'] = styleObj['spacingBottom'] || 2;
                                styleObj['spacingLeft'] = styleObj['spacingLeft'] || 4;
                                styleObj['spacingRight'] = styleObj['spacingRight'] || 4;

                                // Handle curved edges properly
                                if (isCurved || styleObj['curved'] || styleObj['rounded']) {
                                    styleObj['curved'] = 1;
                                    styleObj['rounded'] = 1;
                                }
                            }

                            // CRITICAL: ALWAYS validate text contrast for ANY cell with fill colors
                            // This includes vertices, text cells, and list items in swimlanes
                            if (styleObj['fillColor'] && styleObj['fillColor'] !== 'none') {
                                const fillColor = styleObj['fillColor'];
                                const fontColor = styleObj['fontColor'];

                                // Calculate optimal text color for this background
                                const optimalColor = getOptimalTextColor(fillColor);

                                // ALWAYS verify contrast ratio - don't trust LLM-provided fontColor
                                const contrast = fontColor ? calculateContrastRatio(fontColor, fillColor) : 0;
                                const needsFix = !fontColor || contrast < 3.0;
                                if (needsFix) {
                                    styleObj['fontColor'] = optimalColor;
                                    console.log(`📐 CONTRAST-FIX: Cell ${cellId} - ${fillColor} bg, contrast ${contrast.toFixed(1)} < 3.0, ${fontColor || 'unset'} → ${optimalColor}`);
                                }
                            } else if (!styleObj['fontColor']) {
                                // No fill color - use theme-based default
                                styleObj['fontColor'] = isDarkMode ? '#e0e0e0' : '#000000';
                            }

                            // Special handling for swimlanes (only for actual vertex cells)
                            if (vertex && styleObj['fillColor']) {
                                if (isSwimlane) {
                                    // Swimlane labels should be at the top, not centered
                                    styleObj['verticalAlign'] = 'top';
                                    styleObj['align'] = 'center';
                                    styleObj['spacingTop'] = 8;
                                    styleObj['fontSize'] = styleObj['fontSize'] || 12;
                                    styleObj['fontStyle'] = styleObj['fontStyle'] || 1;

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

                            // Compute absolute exit/entry coordinates so both endpoints
                            // of each pair edge land at the SAME absolute position on the
                            // cross-axis. Using raw fractions (e.g. exitY=0.7, entryY=0.7)
                            // produces misaligned endpoints when source and target boxes
                            // have different heights/widths, forcing Manhattan to insert
                            // an unnecessary perpendicular jog ("crick") mid-edge.
                            if (isHorizontal) {
                                const yTop = Math.max(sourceGeom.y, targetGeom.y);
                                const yBot = Math.min(
                                    sourceGeom.y + sourceGeom.height,
                                    targetGeom.y + targetGeom.height
                                );
                                if (yBot > yTop) {
                                    // Non-empty vertical overlap: pick an absolute Y in
                                    // that band and convert to per-box fractions.
                                    const overlap = yBot - yTop;
                                    const absY = (yTop + yBot) / 2 + offset * overlap;
                                    currentStyle['exitY'] = (absY - sourceGeom.y) / sourceGeom.height;
                                    currentStyle['entryY'] = (absY - targetGeom.y) / targetGeom.height;
                                } else {
                                    // No overlap: fall back to symmetric fractional offset.
                                    currentStyle['exitY'] = 0.5 + offset;
                                    currentStyle['entryY'] = 0.5 + offset;
                                }
                                currentStyle['exitX'] = dx > 0 ? 1.0 : 0.0;
                                currentStyle['entryX'] = dx > 0 ? 0.0 : 1.0;
                            } else {
                                const xLeft = Math.max(sourceGeom.x, targetGeom.x);
                                const xRight = Math.min(
                                    sourceGeom.x + sourceGeom.width,
                                    targetGeom.x + targetGeom.width
                                );
                                if (xRight > xLeft) {
                                    const overlap = xRight - xLeft;
                                    const absX = (xLeft + xRight) / 2 + offset * overlap;
                                    currentStyle['exitX'] = (absX - sourceGeom.x) / sourceGeom.width;
                                    currentStyle['entryX'] = (absX - targetGeom.x) / targetGeom.width;
                                } else {
                                    currentStyle['exitX'] = 0.5 + offset;
                                    currentStyle['entryX'] = 0.5 + offset;
                                }
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

                // Build ordered list: swimlanes → edges → vertices
                // CRITICAL: Edges must be added BEFORE vertices so vertices render on top
                // This ensures connection lines appear behind the service icons
                const nonRootIds = [...swimlaneVertices, ...edgeCells, ...regularVertices].map(item => item.id).filter(id => id !== '0' && id !== '1');

                console.log('📐 DrawIO: Adding cells in z-order (bottom to top):', { swimlanes: swimlaneVertices.length, edges: edgeCells.length, vertices: regularVertices.length });

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

                // Detect explicit layout BEFORE running any position/route optimizers.
                // Diagrams with author-specified coordinates should NOT be rearranged
                // by the placement optimizer or the custom orthogonal router — those
                // are for auto-layout diagrams only.
                let hasExplicitLayout = false;
                cellMap.forEach((cell, id) => {
                    if (id === '0' || id === '1') return;
                    if (cell.isVertex()) {
                        const geom = cell.getGeometry();
                        if (geom && (geom.x !== 0 || geom.y !== 0)) {
                            hasExplicitLayout = true;
                        }
                    }
                });
                console.log('📐 LAYOUT-CHECK: hasExplicitLayout =', hasExplicitLayout);

                if (hasExplicitLayout) {
                    console.log('📐 LAYOUT-CHECK: Skipping placement optimizer and custom router — diagram has explicit positions');
                    graph.__hasExplicitLayout = true;
                }

                if (!hasExplicitLayout) {
                    // --- BEGIN auto-layout-only section (placement optimizer + router) ---

                    console.log('📐 PLACEMENT: Optimizing shape positions within containers (auto-layout)');

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
                } // --- END placement-optimizer-only section (auto-layout only) ---

                // For explicit-layout diagrams, delegate edge routing to maxGraph's
                // ManhattanConnector — a built-in A*-based obstacle-aware orthogonal
                // router (file: @maxgraph/core/.../edge/Manhattan.js). It builds an
                // obstacle map over all vertex cells and finds the shortest orthogonal
                // path avoiding them. Much better than our hand-rolled routing.
                //
                // For auto-layout diagrams we keep the A* fallback router (below) —
                // it runs after ELK and provides a fallback when ELK fails.
                if (hasExplicitLayout) {
                    // Column redistribution pass for explicit-layout diagrams.
                    // Uses GEOMETRIC containment (not model-parent) because many
                    // DFDs use plain dashed rectangles as trust boundaries with
                    // every cell flat-parented to cell "1" in the XML. For each
                    // vertex, find the smallest OTHER vertex whose absolute bbox
                    // fully contains it — that's the effective container. Leaves
                    // (vertices that don't themselves contain others) within the
                    // same container get clustered into columns by center-X
                    // proximity and redistributed with equal left/inter/right gaps.
                    // Does not redistribute containers themselves, rows (Y), or
                    // single-leaf containers.
                    const COLUMN_CLUSTER_THRESHOLD = 60; // px
                    const CONTAINER_EDGE_MARGIN = 20;    // px
                    const getAbs = (cell: any): { x: number; y: number; w: number; h: number } | null => {
                        const g = cell.getGeometry();
                        if (!g) return null;
                        let x = g.x, y = g.y;
                        let p = cell.getParent();
                        while (p && p.getId() !== '0' && p.getId() !== '1') {
                            const pg = p.getGeometry();
                            if (pg) { x += pg.x; y += pg.y; }
                            p = p.getParent();
                        }
                        return { x, y, w: g.width, h: g.height };
                    };
                    const verts: Array<{ id: string; cell: any; abs: { x: number; y: number; w: number; h: number } }> = [];
                    cellMap.forEach((cell, id) => {
                        if (id === '0' || id === '1' || !cell.isVertex()) return;
                        const abs = getAbs(cell);
                        if (abs) verts.push({ id, cell, abs });
                    });
                    const bboxContains = (o: { x: number; y: number; w: number; h: number }, i: { x: number; y: number; w: number; h: number }) =>
                        i.x >= o.x && i.y >= o.y && i.x + i.w <= o.x + o.w && i.y + i.h <= o.y + o.h;
                    const containerOf = new Map<string, string>();
                    const hasChildren = new Set<string>();
                    verts.forEach((inner) => {
                        let bestId: string | null = null;
                        let bestArea = Infinity;
                        verts.forEach((outer) => {
                            if (outer.id === inner.id) return;
                            if (!bboxContains(outer.abs, inner.abs)) return;
                            hasChildren.add(outer.id);
                            const area = outer.abs.w * outer.abs.h;
                            if (area < bestArea) { bestArea = area; bestId = outer.id; }
                        });
                        if (bestId) containerOf.set(inner.id, bestId);
                    });
                    const vertsById = new Map(verts.map(v => [v.id, v]));
                    const childrenByContainer = new Map<string, Array<{ cell: any; geom: any; abs: typeof verts[0]['abs'] }>>();
                    verts.forEach(({ id, cell, abs }) => {
                        if (hasChildren.has(id)) return; // container itself, not a leaf
                        const parentId = containerOf.get(id);
                        if (!parentId) return;
                        const geom = cell.getGeometry();
                        if (!geom) return;
                        if (!childrenByContainer.has(parentId)) childrenByContainer.set(parentId, []);
                        childrenByContainer.get(parentId)!.push({ cell, geom, abs });
                    });
                    childrenByContainer.forEach((kids, parentId) => {
                        if (kids.length < 2) return;
                        const parentAbs = vertsById.get(parentId)?.abs;
                        if (!parentAbs) return;
                        const sorted = [...kids].sort((a, b) =>
                            (a.abs.x + a.abs.w / 2) - (b.abs.x + b.abs.w / 2));
                        const cols: Array<Array<typeof kids[0]>> = [[sorted[0]]];
                        for (let i = 1; i < sorted.length; i++) {
                            const curCenter = sorted[i].abs.x + sorted[i].abs.w / 2;
                            const lastCol = cols[cols.length - 1];
                            const lastCenter = lastCol[0].abs.x + lastCol[0].abs.w / 2;
                            if (Math.abs(curCenter - lastCenter) < COLUMN_CLUSTER_THRESHOLD) {
                                lastCol.push(sorted[i]);
                            } else {
                                cols.push([sorted[i]]);
                            }
                        }
                        const colWidths = cols.map(c => Math.max(...c.map(k => k.abs.w)));
                        const totalW = colWidths.reduce((a, b) => a + b, 0);
                        const avail = parentAbs.w - 2 * CONTAINER_EDGE_MARGIN;
                        if (totalW >= avail) return;
                        const gap = (avail - totalW) / (cols.length + 1);
                        let absCursor = parentAbs.x + CONTAINER_EDGE_MARGIN + gap;
                        cols.forEach((col, i) => {
                            const absColCenter = absCursor + colWidths[i] / 2;
                            col.forEach(({ cell, geom, abs }) => {
                                const dx = absColCenter - (abs.x + abs.w / 2);
                                if (Math.abs(dx) > 1) {
                                    geom.x += dx;
                                    cell.setGeometry(geom);
                                }
                            });
                            absCursor += colWidths[i] + gap;
                        });
                        console.log(`📐 REDIST: Container ${parentId} — ${cols.length} column(s) redistributed`);
                    });

                    // Bidirectional-pair trunk separation.
                    // Manhattan computes each edge independently and picks the
                    // geometric midpoint of the source/target gap for the trunk.
                    // For pairs A↔B, both edges choose the same trunk X (or Y),
                    // producing overlapping/crossing trunks. Fix by writing
                    // explicit waypoints on each pair edge — Manhattan sees
                    // non-empty geometry.points and delegates to SegmentConnector,
                    // which respects waypoints. We offset the two trunks by
                    // ±TRUNK_OFFSET px on the cross-axis so they run parallel.
                    // Loses Manhattan's obstacle avoidance for these specific
                    // edges — acceptable tradeoff since bidirectional pairs
                    // usually route in clear corridors between two adjacent cells.
                    const TRUNK_OFFSET = 8; // px each side of midpoint (~16px total separation, matches horizontal-pair endpoint spacing)
                    const processedPairs = new Set<string>();
                    edgeCells.forEach(({ id, element }) => {
                        const sourceId = element.getAttribute('source');
                        const targetId = element.getAttribute('target');
                        if (!sourceId || !targetId) return;
                        const pairKey = sourceId < targetId ? `${sourceId}-${targetId}` : `${targetId}-${sourceId}`;
                        if (processedPairs.has(pairKey)) return;
                        const pairIds = edgePairs.get(pairKey) || [];
                        if (pairIds.length !== 2) return;
                        processedPairs.add(pairKey);

                        const [aId, bId] = pairIds;
                        const aCell = cellMap.get(aId);
                        const bCell = cellMap.get(bId);
                        if (!aCell || !bCell) return;
                        const aSrc = cellMap.get(sourceId < targetId ? sourceId : targetId);
                        const aTgt = cellMap.get(sourceId < targetId ? targetId : sourceId);
                        if (!aSrc || !aTgt) return;
                        const sg = aSrc.getGeometry();
                        const tg = aTgt.getGeometry();
                        if (!sg || !tg) return;

                        // Compute true gaps (0 when axes overlap, positive when they
                        // don't). A trunk offset is only needed when BOTH axes have a
                        // gap — i.e. source and target are diagonally offset. If only
                        // one axis has a gap (side-by-side or stacked), Manhattan
                        // draws a single straight segment and our existing endpoint
                        // alignment handles parallel separation; adding waypoints
                        // here would introduce an unwanted kink.
                        const horizGap = Math.max(
                            0,
                            Math.max(sg.x, tg.x) - Math.min(sg.x + sg.width, tg.x + tg.width));
                        const vertGap = Math.max(
                            0,
                            Math.max(sg.y, tg.y) - Math.min(sg.y + sg.height, tg.y + tg.height));
                        if (horizGap <= 0 || vertGap <= 0) {
                            console.log(`📐 PAIR-TRUNK: skip pair (${pairKey}) — gaps H=${horizGap} V=${vertGap}`);
                            return;
                        }
                        const horizontal = horizGap >= vertGap;

                        pairIds.forEach((pid, idx) => {
                            const cell = cellMap.get(pid);
                            if (!cell) return;
                            const off = (idx === 0 ? -1 : 1) * TRUNK_OFFSET;
                            const geom = cell.getGeometry();
                            if (!geom) return;

                            // Waypoints must appear in source→target order. sg/tg
                            // are the pair's lex-ordered endpoints (sg = min id),
                            // but this specific edge may go in either direction.
                            const edgeEl = edgeCells.find(e => e.id === pid)?.element;
                            const actualSrc = edgeEl?.getAttribute('source');
                            const canonicalSrc = sourceId < targetId ? sourceId : targetId;
                            const [near, far] = actualSrc === canonicalSrc ? [sg, tg] : [tg, sg];

                            // Waypoint position on the cross-axis must match the
                            // port position the earlier bidirectional pre-pass
                            // wrote into exitY/entryY (horizontal pairs) or
                            // exitX/entryX (vertical pairs). Any mismatch forces
                            // SegmentConnector to bridge with a short diagonal
                            // final segment — which renders as a crooked arrow
                            // angle at the cell edge.
                            const cs = cell.getStyle() || {};
                            if (horizontal) {
                                const leftRight = Math.min(sg.x + sg.width, tg.x + tg.width);
                                const rightLeft = Math.max(sg.x, tg.x);
                                const midX = (leftRight + rightLeft) / 2 + off;
                                const nearFrac = parseFloat(cs.exitY ?? '0.5');
                                const farFrac = parseFloat(cs.entryY ?? '0.5');
                                geom.points = [new Point(midX, near.y + nearFrac * near.height),
                                               new Point(midX, far.y + farFrac * far.height)];
                            } else {
                                const topBottom = Math.min(sg.y + sg.height, tg.y + tg.height);
                                const bottomTop = Math.max(sg.y, tg.y);
                                const midY = (topBottom + bottomTop) / 2 + off;
                                const nearFrac = parseFloat(cs.exitX ?? '0.5');
                                const farFrac = parseFloat(cs.entryX ?? '0.5');
                                geom.points = [new Point(near.x + nearFrac * near.width, midY),
                                               new Point(far.x + farFrac * far.width, midY)];
                            }
                            cell.setGeometry(geom);
                            console.log(`📐 PAIR-TRUNK: ${pid} offset ${off}px (${horizontal ? 'H' : 'V'})`);
                        });
                    });

                    console.log('📐 ROUTER: Using maxGraph ManhattanConnector for explicit-layout edges');
                    edgeCells.forEach(({ id, element }) => {
                        // Diagnostic: log exit/entry style that will reach Manhattan,
                        // plus current source/target geometry, for vertical bidirectional
                        // pairs. Helps us understand why f6/f10 and similar pairs produce
                        // crossed routes instead of stacked parallel ones.
                        const sId = element.getAttribute('source');
                        const tId = element.getAttribute('target');
                        const c = cellMap.get(id);
                        const s = sId ? cellMap.get(sId) : null;
                        const t = tId ? cellMap.get(tId) : null;
                        const cs = c?.getStyle?.();
                        const sg = s?.getGeometry?.();
                        const tg = t?.getGeometry?.();
                        console.log(`🔎 MANHATTAN-IN ${id}`, { src: sId, tgt: tId, srcGeom: sg && { x: sg.x, y: sg.y, w: sg.width, h: sg.height }, tgtGeom: tg && { x: tg.x, y: tg.y, w: tg.width, h: tg.height }, exitX: cs?.exitX, exitY: cs?.exitY, entryX: cs?.entryX, entryY: cs?.entryY, hasGeomPoints: !!c?.getGeometry()?.points?.length });
                        const cell = cellMap.get(id);
                        if (!cell) return;
                        const style = cell.getStyle() || {};
                        style['edgeStyle'] = 'manhattanEdgeStyle';

                        // Label positioning: offset perpendicular to the edge's
                        // dominant axis so text sits beside the line, not on top
                        // of it. Manhattan may bend the edge, but the dominant
                        // source→target axis is a reasonable heuristic for
                        // "which side of the edge has more empty space".
                        //
                        // Caveats: this does not detect collisions with OTHER
                        // edges' lines or with vertex boxes. Two parallel edges
                        // with the same dominant axis will both offset to the
                        // same side and may stack their labels.
                        const sourceId = element.getAttribute('source');
                        const targetId = element.getAttribute('target');
                        const sCell = sourceId ? cellMap.get(sourceId) : null;
                        const tCell = targetId ? cellMap.get(targetId) : null;
                        const sGeom = sCell?.getGeometry();
                        const tGeom = tCell?.getGeometry();
                        if (sGeom && tGeom) {
                            const dx = Math.abs((tGeom.x + tGeom.width / 2) - (sGeom.x + sGeom.width / 2));
                            const dy = Math.abs((tGeom.y + tGeom.height / 2) - (sGeom.y + sGeom.height / 2));
                            if (dx >= dy) {
                                // Horizontal-dominant edge → label above the line.
                                style['verticalAlign'] = 'bottom';
                                style['align'] = 'center';
                                style['spacingBottom'] = 10;
                            } else {
                                // Vertical-dominant edge → label to the LEFT of the line.
                                style['align'] = 'left';
                                style['verticalAlign'] = 'middle';
                                style['spacingLeft'] = 14;
                            }
                        }

                        cell.setStyle(style);
                    });
                    graph.__orthogonalRoutingApplied = true;

                    // ROUTE-FIX DETECTION: Manhattan silently falls back to
                    // OrthogonalConnector when its ObstacleMap can't find a route
                    // (padded source/target bboxes leave no orthogonal corridor).
                    // The fallback draws a naive L-bend through any vertices
                    // between source and target. Detect such edges after routing
                    // settles so LABEL-AVOID knows which edges are unreliable.
                    // Logs only in this pass; repair added in a follow-up.
                    try {
                        // Force all edges to re-route before checking. Setting
                        // edgeStyle on cells above doesn't mark them dirty, so a
                        // plain validate() may return stale 2-point absolutePoints
                        // from before Manhattan was selected.
                        edgeCells.forEach(({ id }) => {
                            const c = cellMap.get(id);
                            if (c) graph.view.invalidate(c, false, false);
                        });
                        graph.view.validate();
                        type RBox = { id: string; x: number; y: number; w: number; h: number };
                        const allV: RBox[] = [];
                        cellMap.forEach((c, id) => {
                            if (id === '0' || id === '1' || !c.isVertex()) return;
                            const st = graph.view.getState(c);
                            if (st) allV.push({ id, x: st.x, y: st.y, w: st.width, h: st.height });
                        });
                        const boxContains = (o: RBox, i: RBox) =>
                            i.x >= o.x && i.y >= o.y &&
                            i.x + i.w <= o.x + o.w && i.y + i.h <= o.y + o.h;
                        // Exclude containers (any vertex fully enclosing another).
                        // Routing through a trust-boundary rectangle is not a bug.
                        const leaves = allV.filter(v =>
                            !allV.some(o => o.id !== v.id && boxContains(v, o)));
                        console.log(`📐 ROUTE-FIX: view scale=${graph.view.scale}, leaves at detection:`,
                            leaves.map(v => `${v.id}@(${v.x.toFixed(0)},${v.y.toFixed(0)}) ${v.w.toFixed(0)}x${v.h.toFixed(0)}`).join(', '));
                        const hitsBox = (a: any, b: any, box: RBox): boolean => {
                            const bx0 = box.x + 1, by0 = box.y + 1;
                            const bx1 = box.x + box.w - 1, by1 = box.y + box.h - 1;
                            if (Math.abs(a.x - b.x) < 0.5) {
                                const x = a.x;
                                if (x <= bx0 || x >= bx1) return false;
                                const y0 = Math.min(a.y, b.y), y1 = Math.max(a.y, b.y);
                                return y1 > by0 + 1 && y0 < by1 - 1;
                            }
                            if (Math.abs(a.y - b.y) < 0.5) {
                                const y = a.y;
                                if (y <= by0 || y >= by1) return false;
                                const x0 = Math.min(a.x, b.x), x1 = Math.max(a.x, b.x);
                                return x1 > bx0 + 1 && x0 < bx1 - 1;
                            }
                            return false;
                        };
                        const broken: Array<{ id: string; crosses: string[] }> = [];
                        edgeCells.forEach(({ id }) => {
                            const cell = cellMap.get(id);
                            if (!cell) return;
                            const st = graph.view.getState(cell);
                            const pts = st?.absolutePoints;
                            if (!pts || pts.length < 2) {
                                console.log(`📐 ROUTE-FIX SKIP ${id}: pts=${pts?.length ?? 'nil'}`);
                                return;
                            }
                            console.log(`📐 ROUTE-FIX CHECK ${id}: ${pts.length}pt ${
                                pts.map((p:any) => `(${Math.round(p.x)},${Math.round(p.y)})`).join('→')
                            }`);
                            const sId = cell.getTerminal?.(true)?.getId?.();
                            const tId = cell.getTerminal?.(false)?.getId?.();
                            const crosses: string[] = [];
                            for (let i = 0; i < pts.length - 1; i++) {
                                leaves.forEach(v => {
                                    if (v.id === sId || v.id === tId) return;
                                    if (hitsBox(pts[i], pts[i + 1], v)) crosses.push(v.id);
                                });
                            }
                            const uniq = [...new Set(crosses)];
                            if (uniq.length > 0) broken.push({ id, crosses: uniq });
                        });
                        graph.__brokenRoutes = broken;
                        if (broken.length > 0) {
                            console.warn(`📐 ROUTE-FIX DETECT: ${broken.length} edge(s) cross vertex interiors:`,
                                broken.map(b => `${b.id}→[${b.crosses.join(',')}]`).join(' '));
                        } else {
                            console.log('📐 ROUTE-FIX DETECT: all edges clear of vertex interiors');
                        }
                    } catch (e) {
                        console.warn('📐 ROUTE-FIX DETECT failed', e);
                    }

                    // Label collision avoidance (first pass).
                    // Force Manhattan to run once so absolutePoints are populated,
                    // then for each edge whose DEFAULT label position (midpoint
                    // of total path length) overlaps a non-endpoint vertex, shift
                    // the label via geometry.offset toward the midpoint of the
                    // edge's longest segment. Does NOT currently handle:
                    // label-vs-label stacking, overlap with the post-shift position,
                    // container (trust-boundary) collisions, or width-based wrapping.
                    try {
                        graph.view.validate();
                    } catch (e) {
                        console.warn('📐 LABEL-AVOID: early validate failed', e);
                    }
                    const _scale = graph.view.scale || 1;
                    // Build the obstacle set for label placement. We must
                    // EXCLUDE container-like vertices — any vertex whose
                    // absolute bbox fully encloses another vertex is acting
                    // as a group/boundary/swimlane (trust boundaries, AWS
                    // region outlines, etc.), not a leaf shape the label
                    // should avoid. Without this exclusion the outermost
                    // boundary covers the entire canvas and every candidate
                    // label position "hits" it, so LABEL-AVOID gives up on
                    // every edge. Using geometric containment rather than
                    // style hints (fillColor, dashed) keeps this robust
                    // across diagram conventions.
                    type _Box = { id: string; x: number; y: number; w: number; h: number };
                    const _allVerts: _Box[] = [];
                    cellMap.forEach((c, id) => {
                        if (id === '0' || id === '1' || !c.isVertex()) return;
                        const st = graph.view.getState(c);
                        if (!st) return;
                        _allVerts.push({ id, x: st.x, y: st.y, w: st.width, h: st.height });
                    });
                    const _bboxContains = (outer: _Box, inner: _Box) =>
                        inner.x >= outer.x && inner.y >= outer.y &&
                        inner.x + inner.w <= outer.x + outer.w &&
                        inner.y + inner.h <= outer.y + outer.h;
                    const _isContainer = new Set<string>();
                    _allVerts.forEach((outer) => {
                        if (_allVerts.some(inner => inner.id !== outer.id && _bboxContains(outer, inner))) {
                            _isContainer.add(outer.id);
                        }
                    });
                    const _vbox: _Box[] = _allVerts.filter(v => !_isContainer.has(v.id));
                    console.log(`📐 LABEL-AVOID: obstacles=${_vbox.length} leaves, excluded ${_isContainer.size} containers [${[..._isContainer].join(',')}]`);
                    // Text width estimation. The previous halfW used
                    // txt.length * 3.5 capped at 90, which was both over-
                    // inclusive (short labels overestimated at 3.5 px/char)
                    // and under-inclusive (long labels clipped to the cap).
                    // Canvas 2D measureText with the cell's actual font size
                    // gives pixel-accurate widths in model coordinates; the
                    // caller scales by _scale to match view-coord obstacles.
                    const _measureCanvas = document.createElement('canvas');
                    const _measureCtx = _measureCanvas.getContext('2d');
                    const _measureText = (text: string, fontSize: number): { w: number; h: number } => {
                        if (!_measureCtx) return { w: text.length * fontSize * 0.55, h: fontSize * 1.2 };
                        _measureCtx.font = `${fontSize}px Arial, Helvetica, sans-serif`;
                        return { w: _measureCtx.measureText(text).width, h: fontSize * 1.2 };
                    };
                    const _pointAtLen = (pts: any[], t: number) => {
                        let acc = 0;
                        for (let i = 0; i < pts.length - 1; i++) {
                            const sl = Math.hypot(pts[i + 1].x - pts[i].x, pts[i + 1].y - pts[i].y);
                            if (acc + sl >= t) {
                                const r = sl > 0 ? (t - acc) / sl : 0;
                                return { x: pts[i].x + r * (pts[i + 1].x - pts[i].x), y: pts[i].y + r * (pts[i + 1].y - pts[i].y) };
                            }
                            acc += sl;
                        }
                        return pts[pts.length - 1];
                    };
                    model.beginUpdate();
                    try {
                        edgeCells.forEach(({ id }) => {
                            const cell = cellMap.get(id);
                            if (!cell) return;
                            const st = graph.view.getState(cell);
                            const pts = st?.absolutePoints;
                            if (!pts || pts.length < 2) return;
                            const txt = cell.getValue?.();
                            if (typeof txt !== 'string' || txt.length === 0) return;
                            const srcId = cell.getTerminal?.(true)?.getId?.();
                            const tgtId = cell.getTerminal?.(false)?.getId?.();
                            // Anchor the edge label so it sits in the geometric gap
                            // between source and target boxes along the edge's
                            // dominant axis, while keeping the cross-axis position
                            // ON the edge line (so the label stays visually attached
                            // to the route instead of drifting off it).
                            //
                            // Horizontal-dominant edge: use the X midpoint between
                            //   the box centers, keep the path-midpoint Y.
                            // Vertical-dominant edge: use the Y midpoint between
                            //   the box centers, keep the path-midpoint X.
                            const _srcCell = cell.getTerminal?.(true);
                            const _tgtCell = cell.getTerminal?.(false);
                            const _srcSt = _srcCell ? graph.view.getState(_srcCell) : null;
                            const _tgtSt = _tgtCell ? graph.view.getState(_tgtCell) : null;
                            // For multi-segment routed edges (L-shapes, S-shapes),
                            // total-length midpoint can land near a bend instead of
                            // along a visible segment. Use the midpoint of the LONGEST
                            // segment instead — places labels in the biggest open run.
                            // For 2-point edges this degenerates to the only segment's
                            // midpoint, identical to the old behavior.
                            let _longestSeg = { start: pts[0], end: pts[1], len: 0 };
                            for (let i = 0; i < pts.length - 1; i++) {
                                const a = pts[i], b = pts[i + 1];
                                const l = Math.hypot(b.x - a.x, b.y - a.y);
                                if (l > _longestSeg.len) {
                                    _longestSeg = { start: a, end: b, len: l };
                                }
                            }
                            const _pathMid = {
                                x: (_longestSeg.start.x + _longestSeg.end.x) / 2,
                                y: (_longestSeg.start.y + _longestSeg.end.y) / 2,
                            };
                            let _anchor: { x: number; y: number } | null = null;
                            if (_srcSt && _tgtSt) {
                                const sCx = _srcSt.x + _srcSt.width / 2;
                                const sCy = _srcSt.y + _srcSt.height / 2;
                                const tCx = _tgtSt.x + _tgtSt.width / 2;
                                const tCy = _tgtSt.y + _tgtSt.height / 2;
                                const dx = Math.abs(tCx - sCx);
                                const dy = Math.abs(tCy - sCy);
                                if (dx >= dy) {
                                    // Horizontal-dominant: adjust X only.
                                    _anchor = { x: (sCx + tCx) / 2, y: _pathMid.y };
                                } else {
                                    // Vertical-dominant: adjust Y only.
                                    _anchor = { x: _pathMid.x, y: (sCy + tCy) / 2 };
                                }
                            }
                            const _cellStyle = cell.getStyle?.() || {};
                            const _fontSize = parseFloat(_cellStyle.fontSize) || 12;
                            // Auto-wrap long labels at natural break points if the
                            // single-line width significantly exceeds the available
                            // gap between source and target boxes along the edge's
                            // dominant axis. Natural break points in priority order:
                            // before '(' (parenthetical), after ',', after ' / '.
                            // Only accept a wrap if both halves are meaningfully
                            // smaller than the single line — avoids one-long-one-short
                            // wraps that don't actually help readability.
                            // Line-break token: `<br>` for html=1 labels (rendered in
                            // foreignObject), `\n` for plain SVG labels (rendered as
                            // <tspan>s). Using the wrong one leaks literal text.
                            const _br = _cellStyle.html ? '<br>' : '\n';
                            let _labelText = txt;
                            if (_srcSt && _tgtSt && !/<br\s*\/?>|\n/.test(txt)) {
                                const _singleW = _measureText(txt, _fontSize).w;
                                const _isHoriz = Math.abs((_tgtSt.x + _tgtSt.width / 2) - (_srcSt.x + _srcSt.width / 2))
                                    >= Math.abs((_tgtSt.y + _tgtSt.height / 2) - (_srcSt.y + _srcSt.height / 2));
                                // Wrap only for horizontal-dominant edges: label width
                                // competes with the horizontal gap between boxes. On
                                // vertical edges the label sits beside the line and
                                // width isn't the scarce dimension.
                                const _gap = _isHoriz
                                    ? Math.max(0, Math.max(_srcSt.x, _tgtSt.x) - Math.min(_srcSt.x + _srcSt.width, _tgtSt.x + _tgtSt.width))
                                    : Infinity;
                                console.log(`📐 WRAP-DIAG ${id} txt="${txt.slice(0,30)}" isHoriz=${_isHoriz} singleW=${_singleW.toFixed(0)} gap=${_gap === Infinity ? 'inf' : _gap.toFixed(0)} willTry=${_isHoriz && _singleW > _gap * 1.3 && _gap > 40}`);
                                // Threshold 1.0: wrap whenever label width exceeds the
                                // available corridor. Using a multiplier >1 introduced
                                // false negatives for labels that are just over the gap.
                                if (_isHoriz && _singleW > _gap && _gap > 40) {
                                    const _breaks: Array<{ re: RegExp; where: 'before' | 'after' }> = [
                                        { re: / \(/, where: 'before' },
                                        { re: /, /, where: 'after' },
                                        { re: / \/ /, where: 'after' },
                                    ];
                                    for (const { re, where } of _breaks) {
                                        const m = txt.match(re);
                                        if (!m || m.index === undefined) continue;
                                        const idx = where === 'before' ? m.index : m.index + m[0].length;
                                        const left = txt.slice(0, idx).trimEnd();
                                        const right = txt.slice(idx).trimStart();
                                        if (!left || !right) continue;
                                        const lw = _measureText(left, _fontSize).w;
                                        const rw = _measureText(right, _fontSize).w;
                                        console.log(`  WRAP-TRY ${id} at /${re.source}/ left="${left.slice(0,25)}"(${lw.toFixed(0)}) right="${right.slice(0,25)}"(${rw.toFixed(0)}) threshold=${(_singleW*0.85).toFixed(0)} accept=${Math.max(lw,rw) < _singleW*0.85}`);
                                        if (Math.max(lw, rw) < _singleW * 0.85) {
                                            _labelText = left + _br + right;
                                            cell.setValue(_labelText);
                                            console.log(`📐 LABEL-WRAP: ${id} "${txt.slice(0,20)}" ${_singleW.toFixed(0)}px (gap ${_gap.toFixed(0)}px) → 2 lines [${lw.toFixed(0)}/${rw.toFixed(0)}] via ${_br === '\n' ? '\\n' : '<br>'}`);
                                            break;
                                        }
                                    }
                                }
                            }
                            // Measure label (multi-line aware).
                            // html=1 edge labels render <br> as a line break (foreignObject);
                            // plain edge labels render \n as a line break (<tspan>).
                            // Measurement splits on either and takes the max line width.
                            const _lines = _labelText.split(/<br\s*\/?>|\n/i);
                            const _maxLineW = Math.max(..._lines.map(l => _measureText(l, _fontSize).w));
                            const _totalLineH = _fontSize * 1.2 * _lines.length;
                            const halfW = (_maxLineW / 2) * _scale;
                            const halfH = (_totalLineH / 2) * _scale;
                            // Bidirectional pair offset: when two edges connect
                            // the same vertex pair (A↔B), both anchor at the
                            // exact same point and their labels stack on each
                            // other. Shift each label perpendicular to the
                            // edge's dominant axis, one to each side, so the
                            // viewer can tell which label goes with which
                            // trunk. The pair's two trunks themselves are
                            // already separated earlier by PAIR-TRUNK.
                            const _pairKey = (srcId && tgtId)
                                ? (srcId < tgtId ? `${srcId}-${tgtId}` : `${tgtId}-${srcId}`)
                                : null;
                            const _pairIds = _pairKey ? (edgePairs.get(_pairKey) || []) : [];
                            if (_anchor && _pairIds.length === 2 && _srcSt && _tgtSt) {
                                const _idx = _pairIds.indexOf(id);
                                const _side = _idx === 0 ? -1 : 1;
                                // Reduced from 8 — the previous value placed wrapped labels
                                // noticeably above/below their edges.
                                const _gap = 2;
                                // Override the earlier Manhattan label-side hint (which sets
                                // verticalAlign='bottom' / 'middle' + a spacing offset). For
                                // pair-offset labels we want the label CENTERED on our
                                // computed anchor so the pair offset math (anchor ± halfH)
                                // places the label correctly relative to the edge line.
                                // Without this override, maxGraph's flex-end alignment extends
                                // the label a full halfH above the anchor, so the label sits
                                // ~2*halfH above the edge line instead of halfH+gap.
                                // Clone the style so we pass a distinct object reference to
                                // model.setStyle — maxGraph's StyleChange uses `style !==
                                // cell.getStyle()` reference comparison to decide whether to
                                // fire a change event. Mutating the existing object skips
                                // the change and the TextShape is never re-applied.
                                const _ps = { ...(cell.getStyle() || {}) };
                                _ps['verticalAlign'] = 'middle';
                                _ps['align'] = 'center';
                                delete _ps['spacingBottom'];
                                delete _ps['spacingTop'];
                                model.setStyle(cell, _ps);
                                const _pDx = Math.abs((_tgtSt.x + _tgtSt.width / 2) - (_srcSt.x + _srcSt.width / 2));
                                const _pDy = Math.abs((_tgtSt.y + _tgtSt.height / 2) - (_srcSt.y + _srcSt.height / 2));
                                if (_pDy >= _pDx) {
                                    // Vertical pair: offset labels left/right of trunks
                                    _anchor.x += _side * (halfW + _gap);
                                } else {
                                    // Horizontal pair: offset labels above/below trunks
                                    _anchor.y += _side * (halfH + _gap);
                                }
                                console.log(`📐 LABEL-PAIR: ${id} pair ${_pairKey} side=${_side} anchor=(${_anchor.x.toFixed(0)},${_anchor.y.toFixed(0)})`);
                            }
                            // Build segment table (length + cumulative start) and
                            // sort by length desc so longest segments get priority.
                            const segs: Array<{ start: number; len: number }> = [];
                            let total = 0;
                            for (let i = 0; i < pts.length - 1; i++) {
                                const sl = Math.hypot(pts[i + 1].x - pts[i].x, pts[i + 1].y - pts[i].y);
                                segs.push({ start: total, len: sl });
                                total += sl;
                            }
                            if (total < 1) return;
                            segs.sort((a, b) => b.len - a.len);
                            // `strict=false` permits grazing src/tgt — used for the
                            // default-midpoint check, since edge labels legitimately
                            // sit close to their endpoints. `strict=true` forbids
                            // landing inside src/tgt — used for alternate candidates,
                            // because a shifted label that ends up inside the very
                            // box it's supposed to connect is worse than the default.
                            const hitsAt = (pt: { x: number; y: number }, strict: boolean) => _vbox.some(v =>
                                (strict || (v.id !== srcId && v.id !== tgtId)) &&
                                pt.x + halfW >= v.x && pt.x - halfW <= v.x + v.w &&
                                pt.y + halfH >= v.y && pt.y - halfH <= v.y + v.h
                            );
                            // Apply the box-centers anchor as the new default position.
                            // LABEL-AVOID's hit-check runs against this anchor, not the
                            // path midpoint.
                            const defPt_probe = _pointAtLen(pts, total / 2);
                            const wantDefault = _anchor || defPt_probe;
                            if (_anchor) {
                                const geomA = cell.getGeometry();
                                if (geomA) {
                                    const adx = (_anchor.x - defPt_probe.x) / _scale;
                                    const ady = (_anchor.y - defPt_probe.y) / _scale;
                                    geomA.offset = new Point(adx, ady);
                                    cell.setGeometry(geomA);
                                    console.log(`📐 LABEL-ANCHOR: ${id} "${txt.slice(0,20)}" pathMid=(${defPt_probe.x.toFixed(0)},${defPt_probe.y.toFixed(0)}) → centersMid=(${_anchor.x.toFixed(0)},${_anchor.y.toFixed(0)}) offset=(${adx.toFixed(1)},${ady.toFixed(1)})`);
                                }
                            }
                            const defHits = hitsAt(wantDefault, false);
                            console.log(`📐 LABEL-CHECK: ${id} "${txt.slice(0,20)}" anchor=(${wantDefault.x.toFixed(0)},${wantDefault.y.toFixed(0)}) defHits=${defHits} halfW=${halfW.toFixed(1)} vboxN=${_vbox.length}`);
                            if (!defHits) return;
                            // Candidate positions along the edge:
                            //   1. default (midpoint of total length)
                            //   2. midpoint of each segment (long → short)
                            //   3. quarter-points of the 3 longest segments
                            // Return the first non-overlapping one. If every
                            // candidate is blocked, fall through to default
                            // (label stays where maxGraph would have placed it).
                            const defPt = wantDefault;
                            const cands: Array<{ x: number; y: number }> = [];
                            segs.forEach(s => cands.push(_pointAtLen(pts, s.start + s.len / 2)));
                            segs.slice(0, 3).forEach(s => {
                                if (s.len < 40) return;
                                cands.push(_pointAtLen(pts, s.start + s.len * 0.25));
                                cands.push(_pointAtLen(pts, s.start + s.len * 0.75));
                            });
                            const firstFree = cands.find(c => !hitsAt(c, true));
                            if (!firstFree) {
                                console.log(`📐 LABEL-AVOID: ${id} "${txt.slice(0,20)}" no free candidate among ${cands.length}`);
                                return;
                            }
                            const wantPt = firstFree;
                            const dx = (wantPt.x - defPt.x) / _scale;
                            const dy = (wantPt.y - defPt.y) / _scale;
                            if (Math.abs(dx) < 2 && Math.abs(dy) < 2) return;
                            const geom = cell.getGeometry();
                            if (!geom) return;
                            geom.offset = new Point(dx, dy);
                            cell.setGeometry(geom);
                            console.log(`📐 LABEL-AVOID: ${id} "${txt.slice(0, 24)}" shifted (${dx.toFixed(1)}, ${dy.toFixed(1)})`);
                        });
                    } finally {
                        model.endUpdate();
                    }

                } else {

                    // ROUTER: Orthogonal connector helper types and functions
                    interface Rect {
                        left: number;
                        top: number;
                        width: number;
                        height: number;
                    }

                    type Side = 'top' | 'right' | 'bottom' | 'left';

                    interface Point {
                        x: number;
                        y: number;
                    }

                    interface ConnectionPoint {
                        shape: Rect;
                        side: Side;
                        distance: number;
                    }

                    /**
                     * Determine optimal sides for connecting two shapes
                     */
                    const getOptimalSide = (sourceRect: Rect, targetRect: Rect): { sourceSide: Side, targetSide: Side } => {
                        const sourceCenterX = sourceRect.left + sourceRect.width / 2;
                        const sourceCenterY = sourceRect.top + sourceRect.height / 2;
                        const targetCenterX = targetRect.left + targetRect.width / 2;
                        const targetCenterY = targetRect.top + targetRect.height / 2;

                        const dx = targetCenterX - sourceCenterX;
                        const dy = targetCenterY - sourceCenterY;

                        const absDx = Math.abs(dx);
                        const absDy = Math.abs(dy);

                        // Prefer vertical routing when:
                        // 1. Shapes are horizontally aligned (small dx), OR
                        // 2. Vertical distance is significantly larger than horizontal (ratio > 0.6)
                        const isVerticallyAligned = absDx < 30;
                        const isVerticalDominant = absDy > 0 && (absDy / (absDx + absDy)) > 0.6;
                        const isHorizontal = !isVerticallyAligned && !isVerticalDominant && absDx > absDy;

                        if (isHorizontal) {
                            return {
                                sourceSide: dx > 0 ? 'right' : 'left',
                                targetSide: dx > 0 ? 'left' : 'right'
                            };
                        } else {
                            return {
                                sourceSide: dy > 0 ? 'bottom' : 'top',
                                targetSide: dy > 0 ? 'top' : 'bottom'
                            };
                        }
                    };

                    /**
                     * Get the connection point on a shape's edge
                     */
                    const getConnectionPoint = (cp: ConnectionPoint): Point => {
                        const { shape, side, distance } = cp;

                        switch (side) {
                            case 'top':
                                return { x: shape.left + shape.width * distance, y: shape.top };
                            case 'right':
                                return { x: shape.left + shape.width, y: shape.top + shape.height * distance };
                            case 'bottom':
                                return { x: shape.left + shape.width * distance, y: shape.top + shape.height };
                            case 'left':
                                return { x: shape.left, y: shape.top + shape.height * distance };
                        }
                    };

                    /**
                     * Route an orthogonal connector with obstacle avoidance
                     */
                    const routeOrthogonalConnector = (config: {
                        pointA: ConnectionPoint;
                        pointB: ConnectionPoint;
                        obstacles: Rect[];
                        shapeMargin: number;
                    }): Point[] => {
                        const { pointA, pointB, obstacles, shapeMargin } = config;
                        const startPoint = getConnectionPoint(pointA);
                        const endPoint = getConnectionPoint(pointB);

                        // Visibility-grid A*: build a sparse grid of candidate X/Y
                        // coordinates from obstacle bounds (inflated by shapeMargin)
                        // plus the endpoints, then A* along grid lines while
                        // rejecting segments that cross any inflated obstacle.
                        const infl = shapeMargin;
                        const hitsObstacle = (p1: Point, p2: Point): boolean => {
                            const xMin = Math.min(p1.x, p2.x), xMax = Math.max(p1.x, p2.x);
                            const yMin = Math.min(p1.y, p2.y), yMax = Math.max(p1.y, p2.y);
                            for (const o of obstacles) {
                                const l = o.left - infl, r = o.left + o.width + infl;
                                const t = o.top - infl, b = o.top + o.height + infl;
                                // Strict inside test: touching the inflated edge is allowed,
                                // so the corridor along an obstacle margin is usable.
                                if (xMax <= l || xMin >= r || yMax <= t || yMin >= b) continue;
                                return true;
                            }
                            return false;
                        };

                        const xs = new Set<number>([startPoint.x, endPoint.x]);
                        const ys = new Set<number>([startPoint.y, endPoint.y]);
                        for (const o of obstacles) {
                            xs.add(o.left - infl);
                            xs.add(o.left + o.width + infl);
                            ys.add(o.top - infl);
                            ys.add(o.top + o.height + infl);
                        }
                        const xsArr = Array.from(xs).sort((a, b) => a - b);
                        const ysArr = Array.from(ys).sort((a, b) => a - b);
                        const xIdx = new Map<number, number>(xsArr.map((x, i) => [x, i]));
                        const yIdx = new Map<number, number>(ysArr.map((y, i) => [y, i]));

                        const nodeId = (x: number, y: number) => `${x},${y}`;
                        const startId = nodeId(startPoint.x, startPoint.y);
                        const endId = nodeId(endPoint.x, endPoint.y);
                        const gScore = new Map<string, number>([[startId, 0]]);
                        const prev = new Map<string, { id: string; x: number; y: number }>();
                        const open = new Set<string>([startId]);
                        const h = (x: number, y: number) =>
                            Math.abs(x - endPoint.x) + Math.abs(y - endPoint.y);

                        const TURN_PENALTY = 8;
                        while (open.size) {
                            // Pick lowest f = g + h (O(n) per pop — n is small here).
                            let curId = '', curF = Infinity, curX = 0, curY = 0;
                            for (const id of open) {
                                const [sx, sy] = id.split(',').map(Number);
                                const f = (gScore.get(id) ?? Infinity) + h(sx, sy);
                                if (f < curF) { curF = f; curId = id; curX = sx; curY = sy; }
                            }
                            if (!curId || curId === endId) break;
                            open.delete(curId);

                            const cxi = xIdx.get(curX) ?? -1;
                            const cyi = yIdx.get(curY) ?? -1;
                            if (cxi < 0 || cyi < 0) continue;
                            const p = prev.get(curId);
                            const curDirX = p ? Math.sign(curX - p.x) : 0;
                            const curDirY = p ? Math.sign(curY - p.y) : 0;

                            for (const [dx, dy] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
                                let nx = curX, ny = curY;
                                if (dx !== 0) {
                                    const ni = cxi + dx;
                                    if (ni < 0 || ni >= xsArr.length) continue;
                                    nx = xsArr[ni];
                                } else {
                                    const ni = cyi + dy;
                                    if (ni < 0 || ni >= ysArr.length) continue;
                                    ny = ysArr[ni];
                                }
                                if (hitsObstacle({ x: curX, y: curY }, { x: nx, y: ny })) continue;
                                const segLen = Math.abs(nx - curX) + Math.abs(ny - curY);
                                const ndx = Math.sign(nx - curX), ndy = Math.sign(ny - curY);
                                const turn = (curDirX !== 0 || curDirY !== 0) &&
                                    (ndx !== curDirX || ndy !== curDirY) ? TURN_PENALTY : 0;
                                const tentative = (gScore.get(curId) ?? Infinity) + segLen + turn;
                                const nid = nodeId(nx, ny);
                                if (tentative < (gScore.get(nid) ?? Infinity)) {
                                    gScore.set(nid, tentative);
                                    prev.set(nid, { id: curId, x: curX, y: curY });
                                    open.add(nid);
                                }
                            }
                        }

                        // Reconstruct. If no path was found, fall back to naive L-bend
                        // (may clip obstacles, but lets the diagram still render).
                        if (!prev.has(endId) && startId !== endId) {
                            return [startPoint, { x: endPoint.x, y: startPoint.y }, endPoint];
                        }
                        const path: Point[] = [];
                        let cur: string | undefined = endId;
                        while (cur) {
                            const [px, py] = cur.split(',').map(Number);
                            path.unshift({ x: px, y: py });
                            cur = prev.get(cur)?.id;
                        }
                        // Collapse collinear runs so the edge geometry has only corners.
                        const cleaned: Point[] = [path[0]];
                        for (let i = 1; i < path.length; i++) {
                            const a = cleaned[cleaned.length - 1];
                            const b = path[i];
                            if (cleaned.length >= 2) {
                                const pPrev = cleaned[cleaned.length - 2];
                                const d1x = Math.sign(a.x - pPrev.x), d1y = Math.sign(a.y - pPrev.y);
                                const d2x = Math.sign(b.x - a.x), d2y = Math.sign(b.y - a.y);
                                if (d1x === d2x && d1y === d2y) { cleaned[cleaned.length - 1] = b; continue; }
                            }
                            cleaned.push(b);
                        }
                        return cleaned;
                    };

                    console.log('📐 ROUTER: Using OrthogonalConnector for edge routing');

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

                    // Mark that orthogonal routing was applied
                    graph.__orthogonalRoutingApplied = true;
                    console.log('✅ ROUTER: All edges routed');

                } // --- END router section (auto-layout only; explicit uses Manhattan above) ---

                if (hasExplicitLayout) {
                    console.log('📐 ELK: Diagram has explicit positioning - SKIPPING automatic layout');
                    graph.__elkLayoutSkipped = true;
                    // Skip the entire ELK section below
                } else {
                    console.log('📐 ELK: No explicit positioning detected - running automatic layout');

                    console.log('📐 ELK: Preparing graph for automatic layout');
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
                } // Close the hasExplicitLayout else block

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
                }  // Close the ELK routing if block
                // BUILD edge direction and bidirectional pair maps
                // POST-PROCESS: After cells are added, calculate optimal connection points for ALL edges
                // This ensures clean orthogonal routing without overlaps

                // CRITICAL FIX: Skip manual connection point calculation if orthogonal or ELK routing already applied
                if (graph.__elkLayoutApplied || graph.__orthogonalRoutingApplied) {
                    console.log('📐 ROUTING: Skipping manual connection points - using automatic routing');
                } else {
                    console.log('📐 ROUTING: Applying manual connection point calculation (ELK fallback)');

                    // Shared helper: get a cell's absolute position by
                    // walking its parent chain (accounting for swimlane/
                    // container offsets). Used both by the pre-pass below
                    // and by the per-edge routing forEach that follows.
                    const getAbsoluteGeometry = (cell: any) => {
                        const geom = cell.getGeometry?.();
                        if (!geom) return null;
                        let x = geom.x || 0;
                        let y = geom.y || 0;
                        let parent = cell.getParent?.();
                        while (parent && parent.getId?.() !== '0' && parent.getId?.() !== '1') {
                            const pg = parent.getGeometry?.();
                            if (pg) {
                                x += pg.x || 0;
                                y += pg.y || 0;
                            }
                            parent = parent.getParent?.();
                        }
                        return { x, y, width: geom.width, height: geom.height };
                    };

                    // Pre-pass: group edges by which side of which vertex they
                    // enter/exit. When multiple edges end on the same side of
                    // the same vertex, we'll distribute them along that side
                    // instead of stacking them at 0.5 where they overlap and
                    // their labels become ambiguous.
                    const incomingBySide = new Map<string, string[]>();  // key = `${vertexId}:${side}`, value = edge ids
                    const outgoingBySide = new Map<string, string[]>();
                    const edgeSideInfo = new Map<string, { sourceSide: string; targetSide: string }>();

                    const classifyEdgeSide = (dx: number, dy: number): { sourceSide: string; targetSide: string } => {
                        const absDx = Math.abs(dx);
                        const absDy = Math.abs(dy);
                        if (absDx < 30 || (absDy > 0 && absDy / (absDx + absDy) > 0.6)) {
                            // Vertical routing
                            return {
                                sourceSide: dy > 0 ? 'bottom' : 'top',
                                targetSide: dy > 0 ? 'top' : 'bottom',
                            };
                        }
                        return {
                            sourceSide: dx > 0 ? 'right' : 'left',
                            targetSide: dx > 0 ? 'left' : 'right',
                        };
                    };

                    edgeCells.forEach(({ id, element }) => {
                        const sourceId = element.getAttribute('source');
                        const targetId = element.getAttribute('target');
                        if (!sourceId || !targetId) return;
                        const s = cellMap.get(sourceId), t = cellMap.get(targetId);
                        if (!s || !t) return;
                        const sGeom = getAbsoluteGeometry(s), tGeom = getAbsoluteGeometry(t);
                        if (!sGeom || !tGeom) return;
                        const dx = (tGeom.x + tGeom.width / 2) - (sGeom.x + sGeom.width / 2);
                        const dy = (tGeom.y + tGeom.height / 2) - (sGeom.y + sGeom.height / 2);
                        const sides = classifyEdgeSide(dx, dy);
                        edgeSideInfo.set(id, sides);
                        const inKey = `${targetId}:${sides.targetSide}`;
                        const outKey = `${sourceId}:${sides.sourceSide}`;
                        if (!incomingBySide.has(inKey)) incomingBySide.set(inKey, []);
                        if (!outgoingBySide.has(outKey)) outgoingBySide.set(outKey, []);
                        incomingBySide.get(inKey)!.push(id);
                        outgoingBySide.get(outKey)!.push(id);
                    });
                    console.log('📐 ROUTING: Convergence map (incoming):', Object.fromEntries(incomingBySide));
                    console.log('📐 ROUTING: Convergence map (outgoing):', Object.fromEntries(outgoingBySide));

                    edgeCells.forEach(({ id, element }) => {
                        const cell = cellMap.get(id);
                        if (!cell) return;

                        const sourceId = element.getAttribute('source');
                        const targetId = element.getAttribute('target');
                        if (!sourceId || !targetId) return;

                        const sourceCell = cellMap.get(sourceId);
                        const targetCell = cellMap.get(targetId);
                        if (!sourceCell || !targetCell) return;

                        const sourceGeom = getAbsoluteGeometry(sourceCell);
                        const targetGeom = getAbsoluteGeometry(targetCell);
                        if (!sourceGeom || !targetGeom) return;


                        const currentStyle = cell.getStyle() || {};

                        // Calculate connection points based on actual positions

                        const dx = targetGeom.x + targetGeom.width / 2 - (sourceGeom.x + sourceGeom.width / 2);
                        const dy = targetGeom.y + targetGeom.height / 2 - (sourceGeom.y + sourceGeom.height / 2);

                        const absDx = Math.abs(dx);
                        const absDy = Math.abs(dy);

                        // Prefer vertical routing when:
                        // 1. Shapes are horizontally aligned (small dx), OR  
                        // 2. Vertical distance is significantly larger than horizontal (ratio > 0.6)
                        const isVerticallyAligned = absDx < 30;
                        const isVerticalDominant = absDy > 0 && (absDy / (absDx + absDy)) > 0.6;
                        const isHorizontallyAligned = absDy < 30;
                        const isHorizontalDominant = absDx > 0 && (absDx / (absDx + absDy)) > 0.6;

                        console.log(`📐 ROUTING: ${id} alignment check:`, {
                            dx: dx.toFixed(1),
                            dy: dy.toFixed(1),
                            isVerticallyAligned,
                            isHorizontallyAligned,
                            isVerticalDominant,
                            isHorizontalDominant
                        });

                        // PRIORITY: Use vertical routing when shapes are vertically aligned OR vertical is dominant
                        if (isVerticallyAligned || isVerticalDominant) {
                            // Vertically stacked - use top/bottom connections
                            if (dy > 0) {
                                // Target is below source
                                currentStyle['exitX'] = 0.5; currentStyle['exitY'] = 1.0;
                                currentStyle['entryX'] = 0.5; currentStyle['entryY'] = 0.0;
                            } else {
                                // Target is above source
                                currentStyle['exitX'] = 0.5; currentStyle['exitY'] = 0.0;
                                currentStyle['entryX'] = 0.5; currentStyle['entryY'] = 1.0;
                            }
                            console.log(`📐 ROUTING: ${id} - vertical routing (aligned or dominant)`);
                        } else if (isHorizontallyAligned || isHorizontalDominant) {
                            // Horizontally aligned - use left/right connections
                            if (dx > 0) {
                                // Target is to the right
                                currentStyle['exitX'] = 1.0; currentStyle['exitY'] = 0.5;
                                currentStyle['entryX'] = 0.0; currentStyle['entryY'] = 0.5;
                            } else {
                                // Target is to the left
                                currentStyle['exitX'] = 0.0; currentStyle['exitY'] = 0.5;
                                currentStyle['entryX'] = 1.0; currentStyle['entryY'] = 0.5;
                            }
                            console.log(`📐 ROUTING: ${id} - horizontal routing (aligned or dominant)`);
                        } else {
                            // Diagonal connection - use dominant axis
                            const isHorizontalDominant = Math.abs(dx) > Math.abs(dy);

                            if (isHorizontalDominant) {
                                // Horizontal flow dominates
                                currentStyle['exitX'] = 1.0; currentStyle['exitY'] = 0.5;
                                currentStyle['entryX'] = dx > 0 ? 0.0 : 1.0;
                                currentStyle['entryY'] = 0.5;
                            } else {
                                // Vertical flow dominates
                                currentStyle['exitX'] = 0.5; currentStyle['exitY'] = 1.0;
                                currentStyle['entryX'] = 0.5;
                                currentStyle['entryY'] = dy > 0 ? 0.0 : 1.0;
                            }
                            console.log(`📐 ROUTING: ${id} - diagonal (${isHorizontalDominant ? 'horizontal' : 'vertical'} dominant)`);
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

                        // Label positioning (no opaque backgrounds so lines stay visible)
                        delete currentStyle['labelBackgroundColor'];
                        delete currentStyle['labelBorderColor'];
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
                            console.log(`📐 LABEL: ${id} positioned LEFT of vertical line`);
                        } else if (isMainlyHorizontal) {
                            // HORIZONTAL edges: position labels ABOVE the line
                            currentStyle['labelPosition'] = 'center';
                            currentStyle['align'] = 'center';
                            currentStyle['verticalAlign'] = 'bottom';
                            currentStyle['spacingBottom'] = isLongLabel ? 18 : 14;
                            console.log(`📐 LABEL: ${id} positioned ABOVE horizontal line`);
                        } else {
                            // DIAGONAL edges: position above and to the side
                            currentStyle['labelPosition'] = 'center';
                            currentStyle['align'] = dx > 0 ? 'left' : 'right';
                            currentStyle['verticalAlign'] = 'top';
                            currentStyle['spacingTop'] = 14;
                            currentStyle['spacingLeft'] = dx > 0 ? 16 : 0;
                            currentStyle['spacingRight'] = dx < 0 ? 16 : 0;
                            console.log(`📐 LABEL: ${id} positioned for diagonal line`);
                        }

                        // Extra adjustments for long labels
                        if (isLongLabel) {
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

                            // Offset the edge routing perpendicular to the axis the
                            // edge was actually routed along above. The three branches
                            // above choose either vertical (top/bottom ports) or
                            // horizontal (left/right ports); the diagonal fallback
                            // uses absDx > absDy as its tiebreaker, so we replicate
                            // that precedence here to stay consistent with whichever
                            // branch fired.
                            const routedVertically = isVerticallyAligned || isVerticalDominant;
                            const routedHorizontally =
                                !routedVertically &&
                                (isHorizontallyAligned || isHorizontalDominant || absDx > absDy);
                            const isHorizontal = routedHorizontally;

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

                    // Only refresh once, gently
                    if (!graph.__elkLayoutApplied) {
                        // Manual routing needs refresh
                        graph.view.validate();
                        graph.refresh();
                    }
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

            // Enable click-and-drag panning when not in edit mode
            enableDragPan(graphContainer, () => !graph.isEnabled());

            // Make container centered and relatively positioned for absolute controls
            container.style.position = 'relative';
            container.style.display = 'flex';
            container.style.justifyContent = 'center';
            container.style.alignItems = 'flex-start'; // Align to top, center horizontally

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
                    DrawIOEnhancer.fixAllForeignObjects(svgElement, graph);
                    console.log('✅ DrawIO: Label positions fixed');

                    // Scale down oversized arrow markers after fit() amplifies them
                    DrawIOEnhancer.scaleDownArrowMarkers(svgElement, 8);

                    // CRITICAL: Don't call refresh() if ELK layout was applied
                    // graph.refresh() calls view.clear() which destroys all shapes and redraws
                    // This wipes out the ELK routing we carefully applied
                    console.log('📐 DrawIO: Forcing bounds recalculation');

                    // Skip explicit invalidate/validate when we have ELK layout
                    // OR explicit layout — fitCenter() below will revalidate anyway,
                    // and running validate here just wipes out the foreignObject
                    // positioning we applied in the enhancer (only to have fitCenter
                    // wipe it again).
                    if (!graph.__elkLayoutApplied && !graph.__hasExplicitLayout) {
                        console.log('📐 DrawIO: Auto-layout path - calling view.invalidate/validate');
                        graph.view.invalidate();
                        graph.view.validate();
                    } else {
                        console.log('📐 DrawIO: ELK or explicit layout - skipping refresh (fitCenter will revalidate)');
                    }

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
                console.error('📐 Error in label positioning enhancer:', enhanceError);
            }

            // CRITICAL: Use maxGraph's built-in fit() and center() functions
            // instead of manual sizing - this ensures proper scaling and positioning
            console.log('📐 DrawIO: Fitting and centering diagram using maxGraph functions');

            // Wait for DOM to be fully rendered before calling fit()
            // Multiple attempts with increasing delays to handle slow rendering
            const applyFitAndCenter = () => {
                try {
                    // fit() lives on the FitPlugin in maxGraph >=0.17; fitCenter both fits and centers
                    const fitPlugin = graph.getPlugin('fit');
                    fitPlugin?.fitCenter({ margin: 20 });

                    // CRITICAL: After fit(), resize graphContainer to match content bounds
                    // This allows the parent container to center it properly
                    const bounds = graph.getGraphBounds();
                    const zoomControlsHeight = 60; // 32px controls + 16px bottom margin + 12px spacing

                    // Let graphContainer fill its parent horizontally so fit()
                    // can use the full available width. Height is sized to the
                    // content so we don't leave vertical whitespace below.
                    const neededHeight = Math.ceil(bounds.height + 40 + zoomControlsHeight);
                    graphContainer.style.width = '100%';
                    graphContainer.style.height = `${neededHeight}px`;
                    graphContainer.style.minHeight = `${neededHeight}px`;

                    // Refit now that the container has its final width so the
                    // diagram scales up to actually fill the available space.
                    fitPlugin?.fitCenter({ margin: 20 });

                    console.log('✅ DrawIO: Fit and center applied, container resized to content', {
                        contentBounds: { width: bounds.width, height: bounds.height }
                    });

                    // Re-apply force-positioning for text-only cells. fitCenter's
                    // revalidation pass rewrites the foreignObject x/y and the
                    // inner div's CSS, which undoes the positioning we applied
                    // in DrawIOEnhancer.fixAllForeignObjects above.
                    const currentSvg = graphContainer.querySelector('svg') as SVGSVGElement | null;
                    if (currentSvg) {
                        DrawIOEnhancer.forceTextCellPositioning(currentSvg, graph);
                        // Also re-run the main enhancer so any container-label
                        // clamps survive fitCenter's revalidation pass.
                        DrawIOEnhancer.fixAllForeignObjects(currentSvg, graph);
                    }

                    // Post-routing label offset correction. LABEL-AVOID
                    // calculates geometry.offset against absolutePoints at a
                    // point BEFORE Manhattan routing has finalized its bends,
                    // so offsets for L/S-shaped edges end up relative to a
                    // straight-line midpoint that no longer exists. After fit
                    // settles the routing, we recompute the offset against the
                    // CURRENT longest segment's midpoint so multi-bend edges
                    // land on a visible segment.
                    if (graph.__hasExplicitLayout) {
                        const Point = window.maxGraph?.Point;
                        const model = graph.model;
                        model.beginUpdate();
                        try {
                            graph.getDefaultParent().getDescendants().forEach((cell: any) => {
                                if (!cell.isEdge?.()) return;
                                const st = graph.view.getState(cell);
                                const pts = st?.absolutePoints;
                                if (!pts || pts.length < 3) return;
                                let longest = { a: pts[0], b: pts[1], len: 0 };
                                for (let i = 0; i < pts.length - 1; i++) {
                                    const l = Math.hypot(pts[i+1].x - pts[i].x, pts[i+1].y - pts[i].y);
                                    if (l > longest.len) longest = { a: pts[i], b: pts[i+1], len: l };
                                }
                                const segMid = { x: (longest.a.x + longest.b.x) / 2, y: (longest.a.y + longest.b.y) / 2 };
                                let total = 0;
                                for (let i = 0; i < pts.length - 1; i++) total += Math.hypot(pts[i+1].x - pts[i].x, pts[i+1].y - pts[i].y);
                                let acc = 0;
                                let pathMid: any = pts[pts.length - 1];
                                for (let i = 0; i < pts.length - 1; i++) {
                                    const sl = Math.hypot(pts[i+1].x - pts[i].x, pts[i+1].y - pts[i].y);
                                    if (acc + sl >= total / 2) {
                                        const r = sl > 0 ? (total/2 - acc) / sl : 0;
                                        pathMid = { x: pts[i].x + r * (pts[i+1].x - pts[i].x), y: pts[i].y + r * (pts[i+1].y - pts[i].y) };
                                        break;
                                    }
                                    acc += sl;
                                }
                                const scale = graph.view.scale || 1;
                                const dx = (segMid.x - pathMid.x) / scale;
                                const dy = (segMid.y - pathMid.y) / scale;
                                if (Math.abs(dx) < 2 && Math.abs(dy) < 2) return;
                                const geom = cell.getGeometry();
                                if (!geom) return;
                                const newGeom = geom.clone();
                                // Idempotent: write the absolute target offset, not base+delta.
                                // Previous base+delta accumulated across multiple fitCenter calls
                                // (50ms/200ms/500ms timeouts + ResizeObserver), pushing labels
                                // progressively farther each pass.
                                newGeom.offset = new Point(dx, dy);
                                cell.setGeometry(newGeom);
                                model.setStyle(cell, { ...(cell.getStyle() || {}) });
                                console.log(`📐 POST-FIT ${cell.getId()}: shift (${dx.toFixed(1)}, ${dy.toFixed(1)}), segLen=${longest.len.toFixed(0)}`);
                            });
                        } finally {
                            model.endUpdate();
                        }
                    }

                    console.log('✅ DrawIO: Fit and center applied successfully');
                } catch (fitError) {
                    console.warn('📐 DrawIO: Fit/center error:', fitError);
                }
            };

            // Try immediately and then with increasing delays
            setTimeout(applyFitAndCenter, 50);
            setTimeout(applyFitAndCenter, 200);
            setTimeout(applyFitAndCenter, 500);

            // Add ResizeObserver to re-fit and re-center when parent container resizes
            console.log('📐 DrawIO: Setting up ResizeObserver for responsive resizing');

            const resizeObserver = new ResizeObserver((entries) => {
                for (const entry of entries) {
                    // Only respond to significant size changes (> 50px difference)
                    const newWidth = entry.contentRect.width;
                    const oldWidth = (container as any).__lastWidth || 0;

                    if (Math.abs(newWidth - oldWidth) > 50) {
                        console.log('📐 DrawIO: Container resized, re-fitting diagram', {
                            oldWidth,
                            newWidth,
                            delta: newWidth - oldWidth
                        });

                        (container as any).__lastWidth = newWidth;

                        // Re-apply fit and center with a small delay to let layout settle
                        setTimeout(() => applyFitAndCenter(), 100);
                    }
                }
            });

            resizeObserver.observe(container);
            console.log('📐 DrawIO: ResizeObserver attached to graphContainer');

            // Final validation
            // Add controls now that graph is fully rendered
            createControls(container, spec, xml!, isDarkMode, graph);

            // CRITICAL: Mark as stable to prevent React from clearing
            container.setAttribute('data-drawio-stable', 'true');
            graphContainer.setAttribute('data-drawio-graph', 'true');
            console.log('✅ DrawIO: Render complete, container marked stable');

            // Skip revalidation for explicit/ELK layouts — it would overwrite
            // the text-cell margin-left positioning we just applied.
            if (!graph.__elkLayoutApplied && !graph.__hasExplicitLayout) {
                graph.view.validate();
            } else {
                console.log('📐 DrawIO: Skipping final view.validate (would wipe text positioning)');
            }

            // Apply universal visibility enhancement after render
            const svgElement = graphContainer.querySelector('svg');
            if (svgElement) {
                const result = enhanceSVGVisibility(svgElement, isDarkMode, { debug: true });
                console.log(`✅ DrawIO visibility enhanced:`, result);
            }
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

    // After any zoom action, maxGraph revalidates the view which wipes out
    // the text-cell positioning corrections applied by DrawIOEnhancer.
    // Re-apply them after each zoom so alignment persists.
    const reapplyAfterViewChange = () => {
        const svg = graphContainer.querySelector('svg') as SVGSVGElement | null;
        if (svg) {
            // Small delay to let maxGraph finish its revalidation pass first.
            setTimeout(() => DrawIOEnhancer.forceTextCellPositioning(svg, graph), 0);
            setTimeout(() => DrawIOEnhancer.forceTextCellPositioning(svg, graph), 50);
        }
    };

    const zoomInBtn = createZoomButton('+', () => {
        graph.zoomIn();
        reapplyAfterViewChange();
    });
    const zoomOutBtn = createZoomButton('-', () => {
        graph.zoomOut();
        reapplyAfterViewChange();
    });
    reapplyAfterViewChange();
    const zoomFitBtn = createZoomButton('⊡', () => {
        try {
            // fit() lives on the FitPlugin in maxGraph >=0.17; fitCenter both fits and centers
            const fitPlugin = graph.getPlugin('fit');
            fitPlugin?.fitCenter({ margin: 20 });
            console.log('📐 DrawIO: Manual fit triggered from zoom button');
        } catch (e) {
            console.warn('📐 DrawIO: Fit error from button:', e);
        }
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

// Enables click-and-drag panning of the graph container when the predicate returns true.
// Uses native scrollLeft/scrollTop. Runs in capture phase so it wins vs mxGraph handlers.
function enableDragPan(graphContainer: HTMLElement, canPan: () => boolean): void {
    let isDown = false;
    let startX = 0;
    let startY = 0;
    let scrollLeft = 0;
    let scrollTop = 0;
    const prevCursor = graphContainer.style.cursor;
    const updateCursor = () => {
        if (isDown) { graphContainer.style.cursor = 'grabbing'; return; }
        graphContainer.style.cursor = canPan() ? 'grab' : (prevCursor || '');
    };
    updateCursor();

    graphContainer.addEventListener('mousedown', (e: MouseEvent) => {
        if (!canPan()) { updateCursor(); return; }
        if (e.button !== 0) return;
        const target = e.target as HTMLElement;
        if (target && target.closest('#drawio-zoom-controls')) return;
        if (target && target.closest('button, input, select, textarea, a')) return;
        isDown = true;
        startX = e.pageX;
        startY = e.pageY;
        scrollLeft = graphContainer.scrollLeft;
        scrollTop = graphContainer.scrollTop;
        graphContainer.style.cursor = 'grabbing';
        graphContainer.style.userSelect = 'none';
        e.preventDefault();
        e.stopPropagation();
    }, true);

    const endDrag = () => {
        if (!isDown) return;
        isDown = false;
        graphContainer.style.userSelect = '';
        updateCursor();
    };
    window.addEventListener('mouseup', endDrag, true);
    window.addEventListener('mouseleave', endDrag, true);
    window.addEventListener('mousemove', (e: MouseEvent) => {
        if (!isDown) return;
        graphContainer.scrollLeft = scrollLeft - (e.pageX - startX);
        graphContainer.scrollTop = scrollTop - (e.pageY - startY);
        e.preventDefault();
        e.stopPropagation();
    }, true);
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
        needsOverflowVisible: true,
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
