/**
 * Plotly.js render plugin for full-featured analytic visualization.
 *
 * Covers 3D charts (scatter3d, surface, mesh3d, volume, cone, streamtube),
 * network/flow (sankey, parcoords, parcats), hierarchical layouts (sunburst,
 * treemap, icicle), statistical (box, violin, histogram2d), geographic,
 * and the full Plotly trace catalog.
 *
 * Spec format (accepts both):
 *   1. Direct:   { type: 'plotly', data: [...], layout: {...}, config: {...} }
 *   2. Wrapped:  { type: 'plotly', definition: '<JSON string>' }
 */

import { D3RenderPlugin } from '../../types/d3';
import { preprocessPlotlySpec } from './plotlyPreprocessor';

declare global {
  interface Window {
    Plotly: any;
    __plotlyLoaded?: boolean;
    __plotlyLoading?: Promise<any>;
  }
}

/** Plotly trace types recognized by structural detection (no explicit marker). */
const PLOTLY_TRACE_TYPES = new Set([
  // 3D — the primary reason this plugin exists
  'scatter3d', 'surface', 'mesh3d', 'volume', 'isosurface', 'cone', 'streamtube',
  // 2D analytic
  'scatter', 'scattergl', 'bar', 'heatmap', 'heatmapgl', 'histogram',
  'histogram2d', 'histogram2dcontour', 'contour', 'box', 'violin',
  'candlestick', 'ohlc', 'waterfall', 'funnel', 'funnelarea',
  // Hierarchical
  'pie', 'sunburst', 'treemap', 'icicle',
  // Network/flow/parallel
  'sankey', 'parcoords', 'parcats',
  // Polar/ternary/carpet
  'scatterpolar', 'scatterpolargl', 'barpolar',
  'scatterternary', 'scattercarpet', 'carpet', 'contourcarpet',
  // Geographic
  'scattergeo', 'scattermapbox', 'choropleth', 'choroplethmapbox', 'densitymapbox',
  // Indicators / tables / specialty
  'indicator', 'table', 'image', 'splom',
]);

function isPlotlySpec(spec: any): boolean {
  if (!spec || typeof spec !== 'object') return false;
  if (spec.type === 'plotly') return true;
  if (Array.isArray(spec.data) && spec.data.length > 0) {
    const firstType = spec.data[0]?.type;
    if (firstType && PLOTLY_TRACE_TYPES.has(firstType)) return true;
  }
  return false;
}

/**
 * Lazy-load Plotly with timeout protection and CDN fallback.
 * Mirrors the loading strategy in mermaidPlugin.ts.
 */
async function loadPlotly(): Promise<any> {
  if (typeof window !== 'undefined' && window.__plotlyLoaded && window.Plotly) {
    return window.Plotly;
  }
  if (window.__plotlyLoading) return window.__plotlyLoading;

  const importWithTimeout = (ms = 5000): Promise<any> => Promise.race([
    import(/* webpackChunkName: "plotly" */ 'plotly.js-dist-min'),
    new Promise((_, reject) =>
      setTimeout(() => reject(new Error(`Plotly import timeout after ${ms}ms`)), ms)
    ),
  ]);

  const loadFromCDN = (): Promise<any> => new Promise((resolve, reject) => {
    console.warn('⚠️ PLOTLY-LOAD: Loading from CDN fallback');
    if (window.Plotly?.newPlot) return resolve({ default: window.Plotly });
    const script = document.createElement('script');
    script.src = 'https://cdn.plot.ly/plotly-2.35.2.min.js';
    script.onload = () => {
      if (window.Plotly?.newPlot) resolve({ default: window.Plotly });
      else reject(new Error('Plotly script loaded but window.Plotly unavailable'));
    };
    script.onerror = () => reject(new Error('Failed to load Plotly from CDN'));
    document.head.appendChild(script);
  });

  window.__plotlyLoading = importWithTimeout(5000)
    .catch(err => {
      console.error('❌ PLOTLY-LOAD: Chunk import failed:', err.message);
      return loadFromCDN();
    })
    .then(module => {
      const Plotly = module.default || module;
      window.Plotly = Plotly;
      window.__plotlyLoaded = true;
      console.log('✅ PLOTLY-LOAD: Module loaded');
      return Plotly;
    });

  return window.__plotlyLoading;
}

/**
 * Inject a theme-aware layout when the user hasn't supplied a template.
 * Respects explicit layout.template — if present, passes through unchanged.
 * Exported for unit testing.
 */
export function applyPlotlyTheme(layout: any, isDarkMode: boolean): any {
  const base = layout || {};
  if (base.template) return { ...base };

  if (isDarkMode) {
    const axisDark = { gridcolor: '#333', zerolinecolor: '#555' };
    const sceneAxis = { ...axisDark, backgroundcolor: '#1e1e1e', showbackground: true };
    return {
      paper_bgcolor: '#1e1e1e',
      plot_bgcolor: '#1e1e1e',
      font: { color: '#e0e0e0' },
      ...base,
      xaxis: { ...axisDark, ...(base.xaxis || {}) },
      yaxis: { ...axisDark, ...(base.yaxis || {}) },
      scene: {
        xaxis: sceneAxis,
        yaxis: sceneAxis,
        zaxis: sceneAxis,
        ...(base.scene || {}),
      },
    };
  }
  return {
    paper_bgcolor: '#ffffff',
    plot_bgcolor: '#ffffff',
    font: { color: '#333333' },
    ...base,
  };
}

export const plotlyPlugin: D3RenderPlugin = {
  name: 'plotly-renderer',
  priority: 9,
  sizingConfig: {
    sizingStrategy: 'responsive',
    needsDynamicHeight: true,
    needsOverflowVisible: true,
    minHeight: 400,
    observeResize: true,
    containerStyles: {
      width: '100%',
      height: 'auto',
      minHeight: '400px',
      overflow: 'hidden',
    },
  },

  canHandle: (spec: any): boolean => {
    if (typeof spec === 'string') {
      try { return isPlotlySpec(JSON.parse(spec)); }
      catch { return false; }
    }
    if (spec?.type === 'plotly' && spec?.definition) return true;
    return isPlotlySpec(spec);
  },

  isDefinitionComplete: (definition: string): boolean => {
    if (!definition || definition.trim().length === 0) return false;
    try {
      const parsed = JSON.parse(definition);
      return !!(parsed && Array.isArray(parsed.data) && parsed.data.length > 0);
    } catch {
      return false;
    }
  },

  render: async (container: HTMLElement, _d3: any, spec: any, isDarkMode: boolean): Promise<void> => {
    // Resolve spec from possible wrapper formats
    let plotlySpec: any;
    if (typeof spec === 'string') {
      plotlySpec = JSON.parse(spec);
    } else if (spec.definition && typeof spec.definition === 'string') {
      plotlySpec = JSON.parse(spec.definition);
    } else if (spec.definition && typeof spec.definition === 'object') {
      plotlySpec = spec.definition;
    } else {
      const { type, isStreaming, isMarkdownBlockClosed, forceRender, ...rest } = spec;
      plotlySpec = rest;
    }

    // Streaming guard — preserve completed render, show placeholder otherwise
    if (spec.isStreaming && !spec.isMarkdownBlockClosed && !spec.forceRender) {
      if (container.querySelector('.js-plotly-plot')) return;
      container.innerHTML = '<div style="padding:16px;text-align:center;color:#888;">📊 Waiting for complete Plotly spec...</div>';
      return;
    }

    if (!plotlySpec.data || !Array.isArray(plotlySpec.data) || plotlySpec.data.length === 0) {
      throw new Error('Invalid Plotly spec: missing or empty "data" array');
    }

    // Normalize common LLM-emitted quirks before handing to Plotly.
    plotlySpec = preprocessPlotlySpec(plotlySpec);

    const Plotly = await loadPlotly();

    container.innerHTML = '';
    container.style.position = 'relative';
    container.style.width = '100%';

    const renderDiv = document.createElement('div');
    const specHeight = plotlySpec.layout?.height;
    renderDiv.style.cssText = `width:100%;height:${specHeight ? specHeight + 'px' : '60vh'};min-height:400px;box-sizing:border-box;`;
    container.appendChild(renderDiv);

    const layout = {
      autosize: true,
      margin: { t: 40, r: 20, b: 40, l: 60 },
      ...applyPlotlyTheme(plotlySpec.layout, isDarkMode),
    };
    const config = {
      responsive: true,
      displaylogo: false,
      modeBarButtonsToRemove: ['sendDataToCloud', 'toggleHover'],
      ...plotlySpec.config,
    };

    await Plotly.newPlot(renderDiv, plotlySpec.data, layout, config);

    // Force a resize after the next paint — the container's final width
    // often isn't known at newPlot time, causing Plotly to fall back to
    // its 700x450 default. Re-running Plots.resize picks up the real width.
    requestAnimationFrame(() => {
      try { Plotly.Plots.resize(renderDiv); } catch { /* torn down */ }
    });
    setTimeout(() => {
      try { Plotly.Plots.resize(renderDiv); } catch { /* torn down */ }
    }, 200);

    const resizeObserver = new ResizeObserver(() => {
      try { Plotly.Plots.resize(renderDiv); } catch { /* render torn down */ }
    });
    resizeObserver.observe(container);
    (container as any)._plotlyResizeObserver = resizeObserver;
    (container as any)._plotlyDiv = renderDiv;

    addActionButtons(container, renderDiv, plotlySpec, isDarkMode, Plotly);

    container.dispatchEvent(new CustomEvent('plotly-render-complete', {
      detail: { success: true }, bubbles: true,
    }));
  },
};

function addActionButtons(
  container: HTMLElement,
  plotDiv: HTMLElement,
  spec: any,
  isDarkMode: boolean,
  Plotly: any,
): void {
  const actions = document.createElement('div');
  actions.className = 'diagram-actions';
  actions.style.cssText =
    'position:absolute;top:-4px;right:8px;z-index:1000;opacity:0;transition:opacity 0.2s;';

  const mkBtn = (label: string, cls: string): HTMLButtonElement => {
    const b = document.createElement('button');
    b.innerHTML = label;
    b.className = `diagram-action-button ${cls}`;
    return b;
  };

  const saveBtn = mkBtn('💾 Save', 'plotly-save-button');
  saveBtn.onclick = async () => {
    try {
      const url = await Plotly.toImage(plotDiv, {
        format: 'png', width: 1200, height: 800, scale: 2,
      });
      const a = document.createElement('a');
      a.href = url;
      a.download = `plotly-${Date.now()}.png`;
      a.click();
    } catch (e) {
      console.error('Plotly save failed:', e);
    }
  };
  actions.appendChild(saveBtn);

  const srcBtn = mkBtn('📝 Source', 'plotly-source-button');
  let showing = false;
  srcBtn.onclick = () => {
    showing = !showing;
    srcBtn.innerHTML = showing ? '🎨 View' : '📝 Source';
    if (showing) {
      plotDiv.style.display = 'none';
      const pre = document.createElement('pre');
      pre.className = 'plotly-source-view';
      pre.style.cssText = `background:${isDarkMode ? '#1f1f1f' : '#f6f8fa'};padding:16px;border-radius:4px;overflow:auto;max-height:80vh;margin:0;color:${isDarkMode ? '#e6e6e6' : '#24292e'};font-size:13px;line-height:1.45;`;
      pre.textContent = JSON.stringify(spec, null, 2);
      container.appendChild(pre);
    } else {
      container.querySelector('.plotly-source-view')?.remove();
      plotDiv.style.display = '';
    }
  };
  actions.appendChild(srcBtn);

  container.insertBefore(actions, container.firstChild);
  container.addEventListener('mouseenter', () => (actions.style.opacity = '1'));
  container.addEventListener('mouseleave', () => (actions.style.opacity = '0'));
}
