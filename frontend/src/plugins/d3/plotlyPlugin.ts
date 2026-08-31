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
import {
  preprocessPlotlySpec,
  parsePlotlyDefinition,
  isValidColorToken,
  KNOWN_PLOTLY_TEMPLATES,
  guardColorscaleAgainstSurface,
  estimateLegendEntries,
  legendAwareRenderHeightPx,
  PLOTLY_EXTENDED_COLORWAY,
  PLOTLY_COLORWAY_RECYCLE_THRESHOLD,
} from './plotlyPreprocessor';
import { classifyColor, namedColorToHex, isDarkBackground } from './chartTheme';

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
/**
 * Repair invalid author colour tokens in a LAYOUT theme-aware (D-232).
 *
 * A design-system token (var(--x), 'primary', '$background', dotted 'theme.text')
 * or a bogus template name ('plotly_dark_v2') is silently ignored by plotly and
 * then falls back to the LIBRARY default, not the theme default — so under dark
 * theme a rejected paper/plot_bgcolor renders as a WHITE slab on the dark page,
 * and a rejected gridcolor falls to plotly's near-invisible #eee in light. The
 * strip pass in the preprocessor only handles TRACE colours; layout colours need
 * the theme (isDarkMode) to substitute the RIGHT surface, so they are repaired
 * here. Only present-and-invalid values are replaced — well-formed and silent
 * layouts pass through byte-identical (returned by reference on a no-op).
 *
 * Role → replacement:
 *   background (paper_bgcolor/plot_bgcolor/bgcolor/backgroundcolor) → theme surface
 *   font color                                                     → theme font
 *   gridcolor                                                      → #8a8a8a (≥3:1 on BOTH surfaces)
 *   zeroline/line/tick/border color                                → #767676 (≥3:1 on BOTH surfaces)
 * A string `template` that is not a real plotly template is DROPPED so the theme
 * defaults apply. Exported for unit testing.
 */
const PLOTLY_LAYOUT_COLOR_ROLE: Record<string, 'bg' | 'grid' | 'line'> = {
  paper_bgcolor: 'bg', plot_bgcolor: 'bg', bgcolor: 'bg', backgroundcolor: 'bg',
  gridcolor: 'grid',
  zerolinecolor: 'line', linecolor: 'line', tickcolor: 'line', bordercolor: 'line', outlinecolor: 'line',
};

export function sanitizeLayoutColorsForTheme(layout: any, isDarkMode: boolean): any {
  if (!layout || typeof layout !== 'object') return layout;
  // #8a8a8a = 3.45:1 on #ffffff / 4.83:1 on #1e1e1e; #767676 = 4.54:1 / 3.67:1.
  const themeVals = {
    bg: isDarkMode ? '#1e1e1e' : '#ffffff',
    grid: '#8a8a8a',
    line: '#767676',
  };
  const fontColor = isDarkMode ? '#e0e0e0' : '#333333';

  const walk = (node: any): any => {
    if (Array.isArray(node)) {
      let changed = false;
      const o = node.map(x => { const n = walk(x); if (n !== x) changed = true; return n; });
      return changed ? o : node;
    }
    if (!node || typeof node !== 'object') return node;
    let out: any = node;
    const clone = () => { if (out === node) out = { ...node }; };
    for (const k of Object.keys(node)) {
      const v = node[k];
      // A *font object: repair its .color against the theme font colour.
      if (/font$/i.test(k) && v && typeof v === 'object' && !Array.isArray(v)
          && typeof v.color === 'string' && !isValidColorToken(v.color)) {
        clone(); out[k] = { ...v, color: fontColor };
        continue;
      }
      const role = PLOTLY_LAYOUT_COLOR_ROLE[k];
      if (role && typeof v === 'string' && !isValidColorToken(v)) {
        clone(); out[k] = themeVals[role];
        continue;
      }
      const nv = walk(v);
      if (nv !== v) { clone(); out[k] = nv; }
    }
    return out;
  };

  let cleaned = walk(layout);
  // Drop a hallucinated string template so the theme defaults are not suppressed.
  if (typeof cleaned.template === 'string' && !KNOWN_PLOTLY_TEMPLATES.has(cleaned.template)) {
    if (cleaned === layout) cleaned = { ...layout };
    delete cleaned.template;
  }
  return cleaned;
}

/**
 * Inject a theme-aware layout when the user hasn't supplied a template.
 * Respects explicit layout.template — if present, passes through unchanged.
 * Exported for unit testing.
 */
/** Resolve a colour token to #hex (hex literal or CSS named colour), else null. */
function resolveToHex(c: any): string | null {
  const cl = classifyColor(c);
  if (!cl) return null;
  if (cl.hex) return cl.hex;
  if (cl.named) return namedColorToHex(cl.named);
  return null;
}

/**
 * Reconcile a themed layout's canvas surface with the active theme (D-233 dark
 * half). When the author pinned a paper/plot background whose luminance
 * DISAGREES with the theme (a light bg under dark theme or vice-versa), the
 * theme font — which we inject — becomes unreadable on it. Rather than swap the
 * font to a hardcoded colour (which cannot satisfy BOTH surfaces), the theme
 * OWNS the canvas: a clashing author background is resolved back to the theme
 * surface so the whole figure is consistently themed and the theme font reads
 * everywhere. Regression-safe: (1) if the author explicitly pinned a font
 * colour we assume a deliberate custom scheme and return `merged` untouched;
 * (2) an author background that AGREES with the theme (e.g. a dark bg under
 * dark theme) is left alone. Exported for unit testing.
 */
export function reconcilePlotlyThemeSurface(merged: any, base: any, isDarkMode: boolean): any {
  if (!merged || typeof merged !== 'object') return merged;
  const authorPinnedFont = !!(base && base.font && typeof base.font === 'object'
    && typeof base.font.color === 'string');
  if (authorPinnedFont) return merged;

  const surface = isDarkMode ? '#1e1e1e' : '#ffffff';
  const themeFont = isDarkMode ? '#e0e0e0' : '#333333';
  let out: any = merged;
  const clone = () => { if (out === merged) out = { ...merged }; };

  for (const key of ['paper_bgcolor', 'plot_bgcolor']) {
    const v = merged[key];
    if (typeof v !== 'string') continue;
    const hex = resolveToHex(v);
    if (!hex) continue;
    if (isDarkBackground(hex) !== isDarkMode) { clone(); out[key] = surface; }
  }
  const curFont = out.font && typeof out.font === 'object' ? out.font.color : undefined;
  if (curFont !== themeFont) { clone(); out.font = { ...(out.font || {}), color: themeFont }; }
  return out;
}

export function applyPlotlyTheme(layout: any, isDarkMode: boolean): any {
  // D-232: repair invalid author colour tokens / bogus template BEFORE theming
  // so a rejected surface/grid/font colour falls back to the THEME default, not
  // the library default (dark white-slab, light near-invisible #eee grid).
  const base = sanitizeLayoutColorsForTheme(layout || {}, isDarkMode);
  if (base.template) return { ...base };

  let merged: any;
  if (isDarkMode) {
    const axisDark = { gridcolor: '#333', zerolinecolor: '#555' };
    const sceneAxis = { ...axisDark, backgroundcolor: '#1e1e1e', showbackground: true };
    const polarAxisDark = { gridcolor: '#333', linecolor: '#555' };
    merged = {
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
      // D-243: the dark branch previously set a GLOBAL dark font.color but only
      // re-backgrounded paper/plot/xaxis/yaxis/scene, so every OTHER surface
      // plotly can paint kept its light default and the dark global font landed
      // unreadable on it (#e0e0e0 on white polar/table bg ≈ 1.32:1). Theme the
      // remaining subplot surfaces too. Merged AFTER `...base` with the author's
      // own sub-object spread last, so an explicit author choice still wins.
      polar: {
        bgcolor: '#1e1e1e',
        radialaxis: polarAxisDark,
        angularaxis: polarAxisDark,
        ...(base.polar || {}),
      },
      ternary: {
        bgcolor: '#1e1e1e',
        ...(base.ternary || {}),
      },
      geo: {
        bgcolor: '#1e1e1e',
        lakecolor: '#1e1e1e',
        landcolor: '#2a2a2a',
        ...(base.geo || {}),
      },
    };
  } else {
    merged = {
      paper_bgcolor: '#ffffff',
      plot_bgcolor: '#ffffff',
      font: { color: '#333333' },
      ...base,
    };
  }
  // D-233 (dark half): the `...base` spread lets an author paper/plot_bgcolor
  // WIN over the theme surface, so an author `paper_bgcolor:'#fff'` survives
  // under dark theme and the dark global font (#e0e0e0) lands on it at 1.32:1 —
  // title / ticks / colorbar label effectively gone (and the mirror in light).
  // The theme owns the canvas: when an author background CLASHES with the theme
  // (its luminance disagrees) and the author did NOT pin an explicit font
  // colour, resolve the surface back to the theme's own so the whole figure is
  // consistently themed and the theme font is readable everywhere.
  merged = themeDarkAnnotations(merged, isDarkMode);
  return reconcilePlotlyThemeSurface(merged, base, isDarkMode);
}

/**
 * Re-theme annotation arrow + text colour in DARK mode only (D-263).
 *
 * applyPlotlyTheme sets a global dark font.color but layout.annotations own
 * their own arrowcolor and font.color, which plotly leaves at its default
 * (#444) when unset. On the dark plot surface that arrow measures 1.71:1
 * (about 1.43:1 on a dark shape band) so the pointer effectively vanishes.
 * This resolves the annotation colours FROM the dark theme (not a blind
 * constant swap): each field is filled with the theme foreground ONLY when the
 * author left it unset, so an explicit author colour still wins, and LIGHT is
 * never touched (the arrow keeps its default #444 = 9.74:1 on white).
 * Exported for unit testing.
 */
export function themeDarkAnnotations(layout: any, isDarkMode: boolean): any {
  if (!isDarkMode || !layout || !Array.isArray(layout.annotations)) return layout;
  const DARK_FG = '#e0e0e0';
  let changed = false;
  const annotations = layout.annotations.map((ann: any) => {
    if (!ann || typeof ann !== 'object') return ann;
    const next: any = { ...ann };
    let touched = false;
    if (next.arrowcolor === undefined) { next.arrowcolor = DARK_FG; touched = true; }
    const font = next.font && typeof next.font === 'object' ? { ...next.font } : {};
    if (font.color === undefined) { font.color = DARK_FG; next.font = font; touched = true; }
    if (touched) { changed = true; return next; }
    return ann;
  });
  return changed ? { ...layout, annotations } : layout;
}

/**
 * Theme trace-OWNED surfaces that inherit the global font (D-243).
 *
 * A `table` trace paints its own header/cell fills in `data`, NOT in `layout`,
 * so `applyPlotlyTheme` (layout-only) can never reach them: under dark theme
 * the fills stayed white while the global font flipped to #e0e0e0, ghosting the
 * entire table body out at ~1.32:1. Here we patch table header/cell fills and
 * fonts to the dark surface. Dark-GATED (light returns the data byte-identical,
 * so the light theme is provably unaffected) and CONSERVATIVE (each field set
 * only when the author left it unset, so an explicit author colour is kept).
 * Exported for unit testing.
 */
export function applyPlotlyTraceTheme(data: any[], isDarkMode: boolean): any[] {
  if (!isDarkMode || !Array.isArray(data)) return data;
  const DARK_CELL = '#1e1e1e';
  const DARK_HEADER = '#2a2a2a';
  const DARK_FONT = '#e0e0e0';
  const DARK_LINE = '#555';

  let anyChanged = false;
  const out = data.map(trace => {
    if (!trace || typeof trace !== 'object' || trace.type !== 'table') return trace;

    const themeSection = (section: any, defaultFill: string): any => {
      const sec = section && typeof section === 'object' ? { ...section } : {};
      // fill.color
      const fill = sec.fill && typeof sec.fill === 'object' ? { ...sec.fill } : {};
      if (fill.color === undefined) fill.color = defaultFill;
      sec.fill = fill;
      // font.color
      const font = sec.font && typeof sec.font === 'object' ? { ...sec.font } : {};
      if (font.color === undefined) font.color = DARK_FONT;
      sec.font = font;
      // line.color (cell/header borders)
      const line = sec.line && typeof sec.line === 'object' ? { ...sec.line } : {};
      if (line.color === undefined) line.color = DARK_LINE;
      sec.line = line;
      return sec;
    };

    anyChanged = true;
    return {
      ...trace,
      header: themeSection(trace.header, DARK_HEADER),
      cells: themeSection(trace.cells, DARK_CELL),
    };
  });
  return anyChanged ? out : data;
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
      const parsed = parsePlotlyDefinition(spec);
      return parsed ? isPlotlySpec(parsed) : false;
    }
    if (spec?.type === 'plotly' && spec?.definition) return true;
    return isPlotlySpec(spec);
  },

  isDefinitionComplete: (definition: string): boolean => {
    if (!definition || definition.trim().length === 0) return false;
    const parsed = parsePlotlyDefinition(definition);
    return !!(parsed && Array.isArray(parsed.data) && parsed.data.length > 0);
  },

  render: async (container: HTMLElement, _d3: any, spec: any, isDarkMode: boolean): Promise<void> => {
    // Resolve spec from possible wrapper formats via the TOLERANT parser (D-230):
    // a bare JSON.parse here threw on any one-lexeme-off input (fence, trailing
    // comma, unquoted/single/smart quotes, `var x =` wrapper, Python literals),
    // and because the failure was never signalled to the host page the headless
    // capture harness waited out the full 30s wall clock with an empty DOM.
    let plotlySpec: any;
    if (typeof spec === 'string') {
      plotlySpec = parsePlotlyDefinition(spec);
    } else if (spec.definition !== undefined) {
      plotlySpec = parsePlotlyDefinition(spec.definition);
    } else {
      const { type, isStreaming, isMarkdownBlockClosed, forceRender, ...rest } = spec;
      plotlySpec = rest;
    }

    // Streaming guard — preserve completed render, show placeholder otherwise.
    // Runs BEFORE the parse-failure throw so a partial spec still streaming in
    // shows the placeholder rather than a hard error.
    if (spec.isStreaming && !spec.isMarkdownBlockClosed && !spec.forceRender) {
      if (container.querySelector('.js-plotly-plot')) return;
      container.innerHTML = '<div style="padding:16px;text-align:center;color:#888;">📊 Waiting for complete Plotly spec...</div>';
      return;
    }

    // D-230: when the definition is unrecoverable, THROW a fast NAMED error
    // instead of returning silently — a named failure beats a 30s empty-DOM
    // timeout with no diagnostic.
    if (plotlySpec === undefined || plotlySpec === null || typeof plotlySpec !== 'object') {
      throw new Error(
        'Plotly spec parse failed: definition is not valid JSON/JSON5 (checked ' +
        'markdown fences, smart quotes, trailing commas, unquoted/single-quoted ' +
        'keys, comments, assignment wrappers and Python literals).'
      );
    }

    if (!plotlySpec.data || !Array.isArray(plotlySpec.data) || plotlySpec.data.length === 0) {
      throw new Error('Invalid Plotly spec: missing or empty "data" array');
    }

    // Normalize common LLM-emitted quirks before handing to Plotly.
    plotlySpec = preprocessPlotlySpec(plotlySpec);
    // Theme trace-owned surfaces (table fills/fonts) that the layout-only
    // applyPlotlyTheme cannot reach (D-243). Dark-gated; a no-op in light.
    plotlySpec = { ...plotlySpec, data: applyPlotlyTraceTheme(plotlySpec.data, isDarkMode) };

    const Plotly = await loadPlotly();

    container.innerHTML = '';
    container.style.position = 'relative';
    container.style.width = '100%';

    const renderDiv = document.createElement('div');
    const specHeight = plotlySpec.layout?.height;
    // D-241: a vertical legend past ~26 entries is clipped behind a scrollbar
    // that does not exist in the static capture. When the author fixed no
    // height, grow the render div so the whole legend is captured; ordinary
    // (<=26-entry) figures keep the default 60vh unchanged.
    const legendEntries = estimateLegendEntries(plotlySpec.data, plotlySpec.layout);
    let divHeight: string;
    if (specHeight) {
      divHeight = specHeight + 'px';
    } else {
      const grownPx = legendAwareRenderHeightPx(legendEntries);
      divHeight = grownPx !== null ? grownPx + 'px' : '60vh';
    }
    renderDiv.style.cssText = `width:100%;height:${divHeight};min-height:400px;box-sizing:border-box;`;
    container.appendChild(renderDiv);

    const layout: any = {
      autosize: true,
      margin: { t: 40, r: 20, b: 40, l: 60 },
      ...applyPlotlyTheme(plotlySpec.layout, isDarkMode),
    };
    // D-241: extend the colorway when the default 10-colour palette would
    // recycle (>10 series) and no author/theme colorway is set, so unlabelled
    // series no longer share a colour with labelled ones. <=10-series figures
    // never receive this, so they are byte-identical.
    if (layout.colorway === undefined && legendEntries > PLOTLY_COLORWAY_RECYCLE_THRESHOLD) {
      layout.colorway = PLOTLY_EXTENDED_COLORWAY;
    }
    const config = {
      responsive: true,
      displaylogo: false,
      modeBarButtonsToRemove: ['sendDataToCloud', 'toggleHover'],
      ...plotlySpec.config,
    };

    // D-233 (light half): guard any colorscale endpoint that collides with the
    // theme-resolved plot surface so a z-min cell equal to the background is no
    // longer an invisible hole in the grid. Resolved against the ACTUAL themed
    // surface, so a #fff endpoint is nudged on a light plot area but left
    // untouched on a dark one.
    const surfaceBg = layout.plot_bgcolor || layout.paper_bgcolor;
    const plotData = guardColorscaleAgainstSurface(plotlySpec.data, surfaceBg);

    await Plotly.newPlot(renderDiv, plotData, layout, config);

    // Plotly.Plots.resize() returns a Promise that REJECTS asynchronously
    // ("Resize must be passed a displayed plot div element") when the div
    // has been detached or hidden by the time a deferred callback fires
    // (React re-render, cleanup racing the timers, or a viz still off in
    // a collapsed/inactive tab). A bare try/catch only catches synchronous
    // throws, so that rejection was escaping as an unhandled promise
    // rejection. Check the div is connected AND actually displayed before
    // calling resize, and always attach a .catch() to swallow any
    // rejection that still slips through the check-then-call gap.
    const safeResize = () => {
      if (!renderDiv.isConnected || renderDiv.offsetParent === null) return;
      try {
        const p = Plotly.Plots.resize(renderDiv);
        if (p && typeof p.catch === 'function') p.catch(() => { /* torn down mid-resize */ });
      } catch { /* torn down */ }
    };

    // Force a resize after the next paint — the container's final width
    // often isn't known at newPlot time, causing Plotly to fall back to
    // its 700x450 default. Re-running Plots.resize picks up the real width.
    requestAnimationFrame(safeResize);
    setTimeout(safeResize, 200);

    const resizeObserver = new ResizeObserver(safeResize);
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
