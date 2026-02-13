import { type EmbedOptions } from 'vega-embed';
import { D3RenderPlugin } from '../../types/d3';
import { getZoomScript } from '../../utils/popupScriptUtils';

/**
 * Full Vega renderer plugin.
 *
 * vega-embed already ships in the bundle (used by the Vega-Lite plugin) and
 * accepts both Vega and Vega-Lite specs via its `mode` option.  This thin
 * plugin gives the D3Renderer a path to render full Vega specs — unlocking
 * hierarchical layouts (sunburst, treemap, tree, circle-pack), force graphs,
 * geographic projections, word clouds, contour plots, and everything else in
 * the Vega transform/mark catalogue — without touching vegaLitePlugin.ts.
 */

// Detect a full-Vega spec (as opposed to Vega-Lite or other diagram types).
const isVegaSpec = (spec: any): boolean => {
  if (!spec || typeof spec !== 'object') return false;

  // Explicit type marker (simplest path for LLM-generated specs)
  if (spec.type === 'vega') return true;

  // $schema that says "vega" but NOT "vega-lite"
  if (
    spec.$schema &&
    typeof spec.$schema === 'string' &&
    spec.$schema.includes('/vega/') &&
    !spec.$schema.includes('vega-lite')
  ) {
    return true;
  }

  // Structural detection: Vega uses `marks` (array), VL uses `mark` (singular)
  if (Array.isArray(spec.marks) && Array.isArray(spec.data)) return true;

  // Vega specs with `signals` + `scales` + `marks` are unambiguously Vega
  if (spec.signals && spec.scales && spec.marks) return true;

  return false;
};

export const vegaPlugin: D3RenderPlugin = {
  name: 'vega-renderer',
  // Higher than vega-lite-renderer (8) so we claim full Vega specs first.
  // VL's canHandle won't match these anyway ($schema check, singular `mark`),
  // but the priority ordering makes the intent explicit.
  priority: 9,
  sizingConfig: {
    sizingStrategy: 'responsive',
    needsDynamicHeight: true,
    needsOverflowVisible: true,
    observeResize: true,
    containerStyles: {
      width: '100%',
      height: 'auto',
      minHeight: '400px',
      overflow: 'visible',
    },
  },

  canHandle: (spec: any): boolean => {
    // Handle string specs that might be JSON
    if (typeof spec === 'string') {
      try {
        return isVegaSpec(JSON.parse(spec));
      } catch {
        return false;
      }
    }
    // Handle wrapper objects with a definition field
    if (spec?.type === 'vega' && spec?.definition) return true;
    return isVegaSpec(spec);
  },

  render: async (
    container: HTMLElement,
    _d3: any,
    spec: any,
    isDarkMode: boolean,
  ): Promise<void> => {
    const vegaEmbedModule = await import('vega-embed');
    const vegaEmbed = vegaEmbedModule.default;

    // Resolve the actual Vega spec from possible wrapper formats
    let vegaSpec: any;
    if (typeof spec === 'string') {
      vegaSpec = JSON.parse(spec);
    } else if (spec.definition && typeof spec.definition === 'string') {
      vegaSpec = JSON.parse(spec.definition);
    } else if (spec.definition && typeof spec.definition === 'object') {
      vegaSpec = spec.definition;
    } else {
      // Clone and strip our internal properties
      const { type, isStreaming, isMarkdownBlockClosed, forceRender, ...rest } = spec;
      vegaSpec = rest;
    }

    // Ensure $schema points to Vega v6
    if (!vegaSpec.$schema) {
      vegaSpec.$schema = 'https://vega.github.io/schema/vega/v6.json';
    }

    container.innerHTML = '';
    container.style.position = 'relative';

    // --- Build HTML chrome (title, legend, footer) outside the SVG ---
    // This avoids coordinate issues when postRenderSizing rewrites the viewBox.
    const title = vegaSpec.title?.text || '';
    const titleEl = document.createElement('div');
    titleEl.style.cssText = `text-align:center; font-size:16px; font-weight:bold; color:${isDarkMode ? '#ddd' : '#333'}; padding:8px 0 4px; font-family:system-ui,-apple-system,sans-serif;`;
    titleEl.textContent = title || '';
    if (title) container.appendChild(titleEl);
    // Remove title from spec so Vega doesn't also render it
    delete vegaSpec.title;

    const legendEl = document.createElement('div');
    legendEl.style.cssText = `position:absolute; top:${title ? '36px' : '8px'}; right:12px; z-index:10; font-size:12px; font-family:system-ui,-apple-system,sans-serif; color:${isDarkMode ? '#bbb' : '#555'}; line-height:1.8;`;
    // Build legend from marks if spec has a known color scheme
    // (populated below after we inspect the data)
    container.appendChild(legendEl);

    const footerEl = document.createElement('div');
    footerEl.style.cssText = `text-align:center; font-size:13px; font-style:italic; color:${isDarkMode ? '#888' : '#999'}; padding:4px 0 8px; font-family:system-ui,-apple-system,sans-serif;`;
    footerEl.textContent = 'Hover any section for line details';
    container.appendChild(footerEl);

    const renderDiv = document.createElement('div');
    renderDiv.style.cssText = 'width:100%; max-width:100%; overflow:hidden; box-sizing:border-box;';
    container.appendChild(renderDiv);

    // --- Strip out title/legend/footer marks from the Vega spec ---
    // so only the sunburst arcs + arc labels remain in the SVG.
    if (vegaSpec.marks && Array.isArray(vegaSpec.marks)) {
      vegaSpec.marks = vegaSpec.marks.filter((mark: any) => {
        // Keep arc and text-on-arc marks; remove standalone text and group (legend) marks
        if (mark.type === 'arc') return true;
        if (mark.type === 'text' && mark.from?.data) return true; // text labels on data arcs
        // Remove static text marks (title, footer) and group marks (legend)
        if (mark.type === 'text' && !mark.from?.data) return false;
        if (mark.type === 'group') return false;
        return true;
      });
    }

    // --- Strip signals that drove the removed footer text ---
    if (vegaSpec.signals && Array.isArray(vegaSpec.signals)) {
      // Keep signals but we'll use them for the HTML footer via the Vega view API
    }

    const embedOptions: EmbedOptions = {
      mode: 'vega' as const,
      actions: false,
      theme: isDarkMode ? 'dark' : undefined,
      renderer: 'svg',
      scaleFactor: 1,
    };

    const result = await Promise.race([
      vegaEmbed(renderDiv, vegaSpec, embedOptions),
      new Promise<never>((_, reject) =>
        setTimeout(() => reject(new Error('Vega render timeout after 15 seconds')), 15000),
      ),
    ]);

    // Store view reference for cleanup
    (container as any)._vegaView = result.view;

    // Wire up hover signal to HTML footer
    try {
      result.view.addSignalListener('hoveredMove', (_name: string, value: any) => {
        footerEl.textContent = value || 'Hover any section for line details';
      });
    } catch { /* signal may not exist in all specs */ }

    // Populate legend from data analysis
    const dataValues = vegaSpec.data?.[0]?.values || [];
    const hasTraps = dataValues.some((d: any) => d.trap);
    const hasGambits = dataValues.some((d: any) => d.gambit);
    const hasAdv = dataValues.some((d: any) => d.adv !== undefined);
    if (hasTraps || hasGambits || hasAdv) {
      const items: string[] = [];
      if (hasAdv) {
        items.push(`<span style="display:inline-block;width:14px;height:14px;background:hsl(145,75%,42%);border-radius:3px;vertical-align:middle;margin-right:6px"></span> ✅ White winning`);
        items.push(`<span style="display:inline-block;width:14px;height:14px;background:hsl(145,25%,22%);border-radius:3px;vertical-align:middle;margin-right:6px"></span> Equal position`);
      }
      if (hasGambits) {
        items.push(`<span style="display:inline-block;width:14px;height:14px;background:hsl(215,65%,42%);border-radius:3px;vertical-align:middle;margin-right:6px"></span> ⚔ White gambit`);
      }
      if (hasTraps) {
        items.push(`<span style="display:inline-block;width:14px;height:14px;background:hsl(0,65%,40%);border-radius:3px;vertical-align:middle;margin-right:6px"></span> 🪤 Black's mistake`);
      }
      legendEl.innerHTML = items.join('<br>');
    }

    // --- Post-render sizing: make SVG responsive and expand parents ---
    const postRenderSizing = () => {
      const svg = renderDiv.querySelector('svg');
      const vegaEmbedEl = renderDiv.querySelector('.vega-embed') as HTMLElement;
      if (!svg) return;

      // Use getBBox() to measure the ACTUAL rendered content including
      // rotated text labels that overflow the declared viewBox.
      let svgW = 0, svgH = 0, bboxX = 0, bboxY = 0;
      try {
        const bbox = (svg as unknown as SVGGraphicsElement).getBBox();
        svgW = bbox.width;
        svgH = bbox.height;
        bboxX = bbox.x;
        bboxY = bbox.y;
      } catch {
        // getBBox can fail if SVG isn't rendered yet; fall back to viewBox
        const viewBox = svg.getAttribute('viewBox');
        if (viewBox) {
          const parts = viewBox.split(/[\s,]+/).map(Number);
          svgW = parts[2] || 0;
          svgH = parts[3] || 0;
        }
      }
      if (!svgH) svgH = parseFloat(svg.getAttribute('height') || '0');
      if (!svgW) svgW = parseFloat(svg.getAttribute('width') || '0');

      // Set viewBox to the full bounding box so ALL content is visible,
      // then make the SVG scale responsively within its container.
      svg.setAttribute('viewBox', `${bboxX} ${bboxY} ${svgW} ${svgH}`);
      svg.removeAttribute('width');
      svg.removeAttribute('height');
      svg.style.width = '100%';
      svg.style.height = 'auto';
      svg.setAttribute('preserveAspectRatio', 'xMidYMid meet');
      svg.style.display = 'block';
      svg.style.overflow = 'hidden';

      // Size the container to the actual content aspect ratio (using real bbox)
      const containerW = container.getBoundingClientRect().width || svgW;
      const aspect = svgH / (svgW || 1);
      const neededH = Math.ceil(containerW * aspect) + 40;

      renderDiv.style.height = `${neededH}px`;
      container.style.minHeight = `${neededH}px`;
      if (vegaEmbedEl) {
        vegaEmbedEl.style.width = '100%';
        vegaEmbedEl.style.height = `${neededH}px`;
        vegaEmbedEl.style.overflow = 'hidden';
      }

      // Walk up parent chain and expand any constraining containers
      let parent = container.parentElement;
      let levelsWalked = 0;
      while (parent && levelsWalked < 5) {
        levelsWalked++;
        if (parent.classList.contains('d3-container') || parent.hasAttribute('data-visualization-type')) {
          const parentH = parent.getBoundingClientRect().height;
          if (parentH < neededH + 20) {
            (parent as HTMLElement).style.height = 'auto';
            (parent as HTMLElement).style.minHeight = `${neededH + 20}px`;
            (parent as HTMLElement).style.maxHeight = 'none';
            (parent as HTMLElement).style.overflow = 'visible';
          }
        }
        parent = parent.parentElement;
      }
    };

    // Run sizing immediately and again after a short delay for late-layout cases
    postRenderSizing();
    setTimeout(postRenderSizing, 150);
    setTimeout(() => {
      postRenderSizing();
      // Signal completion after sizing is stable
      container.dispatchEvent(
        new CustomEvent('vega-render-complete', { detail: { success: true } }),
      );
    }, 300);

    // Also observe resize to keep sizing correct on window changes
    const resizeObserver = new ResizeObserver(() => postRenderSizing());
    resizeObserver.observe(container);
    // Store for cleanup by D3Renderer
    (container as any)._vegaResizeObserver = resizeObserver;

    // --- Action buttons (Open / Save / Source) ---
    const actions = document.createElement('div');
    actions.className = 'diagram-actions';
    actions.style.cssText =
      'position:absolute; top:-4px; right:8px; z-index:1000; opacity:0; transition:opacity 0.2s;';
    container.style.position = 'relative';

    const mkBtn = (label: string, cls: string): HTMLButtonElement => {
      const b = document.createElement('button');
      b.innerHTML = label;
      b.className = `diagram-action-button ${cls}`;
      return b;
    };

    // Open in popout
    const openBtn = mkBtn('↗️ Open', 'vega-open-button');
    openBtn.onclick = () => {
      const svg = container.querySelector('svg');
      if (!svg) return;
      const svgData = new XMLSerializer().serializeToString(svg);
      const html = `<!DOCTYPE html><html><head><meta charset="UTF-8"><title>Vega Visualization</title>
<style>body{margin:0;display:flex;flex-direction:column;height:100vh;background:${isDarkMode ? '#212529' : '#f8f9fa'};font-family:system-ui}
.toolbar{background:${isDarkMode ? '#343a40' : '#f1f3f5'};border-bottom:1px solid ${isDarkMode ? '#495057' : '#dee2e6'};padding:8px;display:flex;justify-content:space-between}
.toolbar button{background:#4361ee;color:#fff;border:none;border-radius:4px;padding:6px 12px;cursor:pointer;margin-right:8px}
.container{flex:1;display:flex;justify-content:center;align-items:center;overflow:auto;padding:20px}
svg{max-width:100%;max-height:100%;height:auto;width:auto}</style></head>
<body><div class="toolbar"><div><button onclick="zoomIn()">Zoom In</button><button onclick="zoomOut()">Zoom Out</button><button onclick="resetZoom()">Reset</button></div>
<div><button onclick="downloadSvg()">Download SVG</button></div></div>
<div class="container">${svgData}</div>
<script>${getZoomScript()}
function downloadSvg(){const s=new XMLSerializer().serializeToString(document.querySelector('svg'));const b=new Blob([s],{type:'image/svg+xml'});const u=URL.createObjectURL(b);const a=document.createElement('a');a.href=u;a.download='vega-${Date.now()}.svg';a.click();setTimeout(()=>URL.revokeObjectURL(u),1000)}
</script></body></html>`;
      const blob = new Blob([html], { type: 'text/html' });
      const url = URL.createObjectURL(blob);
      const w = window.open(url, 'VegaVis', 'width=900,height=700,resizable=yes,scrollbars=yes');
      if (w) w.focus();
      setTimeout(() => URL.revokeObjectURL(url), 10000);
    };
    actions.appendChild(openBtn);

    // Save SVG
    const saveBtn = mkBtn('💾 Save', 'vega-save-button');
    saveBtn.onclick = () => {
      const svg = container.querySelector('svg');
      if (!svg) return;
      const data = new XMLSerializer().serializeToString(svg);
      const blob = new Blob([`<?xml version="1.0" encoding="UTF-8"?>\n${data}`], { type: 'image/svg+xml' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `vega-visualization-${Date.now()}.svg`;
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    };
    actions.appendChild(saveBtn);

    // View source
    const srcBtn = mkBtn('📝 Source', 'vega-source-button');
    let showingSrc = false;
    srcBtn.onclick = () => {
      showingSrc = !showingSrc;
      srcBtn.innerHTML = showingSrc ? '🎨 View' : '📝 Source';
      if (showingSrc) {
        renderDiv.style.display = 'none';
        const pre = document.createElement('pre');
        pre.className = 'vega-source-view';
        pre.style.cssText = `background:${isDarkMode ? '#1f1f1f' : '#f6f8fa'};padding:16px;border-radius:4px;overflow:auto;max-height:80vh;margin:0;color:${isDarkMode ? '#e6e6e6' : '#24292e'};font-size:13px;line-height:1.45;`;
        pre.textContent = JSON.stringify(vegaSpec, null, 2);
        container.appendChild(pre);
      } else {
        container.querySelector('.vega-source-view')?.remove();
        renderDiv.style.display = '';
      }
    };
    actions.appendChild(srcBtn);

    container.insertBefore(actions, container.firstChild);
    container.addEventListener('mouseenter', () => (actions.style.opacity = '1'));
    container.addEventListener('mouseleave', () => (actions.style.opacity = '0'));
  },
};
