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
  demoteWebglTracesForCapture,
  parsePlotlyDefinition,
  isValidColorToken,
  guardColorscaleAgainstSurface,
  estimateLegendEntries,
  legendAwareRenderHeightPx,
  sankeyAwareRenderHeightPx,
  PLOTLY_EXTENDED_COLORWAY,
  PLOTLY_COLORWAY_RECYCLE_THRESHOLD,
  isD3HierarchySpec,
  hierarchySpecToPlotly,
} from './plotlyPreprocessor';
import { classifyColor, namedColorToHex, isDarkBackground, ensureReadableFill } from './chartTheme';

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

/**
 * Per-render budget for Plotly.newPlot (D-302). Some trace-family combinations
 * never settle; without a bound the headless capture harness waits its full
 * wall-clock timeout and yields a blank. 12s < the harness ceiling, so a hung
 * combo throws a diagnostic with time to spare while a merely-slow-but-valid
 * render still completes.
 */
export const PLOTLY_NEWPLOT_BUDGET_MS = 12000;

function isPlotlySpec(spec: any): boolean {
  if (!spec || typeof spec !== 'object') return false;
  if (spec.type === 'plotly') return true;
  // d3-style nested treemap/sunburst/icicle: Plotly renders these natively
  // once flattened, and nothing else claims the shape (first-run "No
  // compatible plugin found for visualization type \"treemap\"").
  if (isD3HierarchySpec(spec)) return true;
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
  // D-458: drop ANY string template (named or not). plotly.js-dist-min ships no
  // registered named templates (plotly_dark/plotly_white/... are a plotly.py
  // concept), so a string `template` is silently ignored by plotly and the
  // figure falls back to LIBRARY light defaults — a white slab on the dark page
  // and the author's dark request lost. Dropping the string lets applyPlotlyTheme
  // resolve the ACTIVE renderer theme instead. An OBJECT template (a real inline
  // template) is left intact and honoured by applyPlotlyTheme's early return.
  if (typeof cleaned.template === 'string') {
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

/**
 * Reconcile AUTHOR-pinned foreground colours against the RESOLVED theme surface
 * (D-457). applyPlotlyTheme re-backgrounds the canvas and sets a global font,
 * but author-pinned colours on specific elements are never checked against the
 * themed surface they end up on, so under the dark theme:
 *   • an author title.font.color (e.g. #1f77b4 = 3.46:1 on #1e1e1e) stays below
 *     the 4.5:1 text floor;
 *   • an author legend.bgcolor that CLASHES with the theme (a white panel under
 *     dark) keeps the themed light-on-... global font unreadable on it (1.32:1);
 *   • an author guide-shape stroke (#333 on #1e1e1e = 1.32:1) vanishes.
 * Each is resolved FROM the active theme (not a blind constant): the surface is
 * the theme-resolved paper/plot colour, and every repair uses ensureReadableFill
 * so the chosen colour satisfies the floor on THAT background — correct in light
 * and dark alike. Conservative: a colour already clearing the floor, or a legend
 * background that AGREES with the theme, is left byte-identical. Runs AFTER
 * applyPlotlyTheme so it sees the resolved surfaces. Exported for unit testing.
 */
export function repairAuthorColorsForTheme(layout: any, isDarkMode: boolean): any {
  if (!layout || typeof layout !== 'object') return layout;
  const surface = isDarkMode ? '#1e1e1e' : '#ffffff';
  const themeFont = isDarkMode ? '#e0e0e0' : '#333333';
  const themeLine = '#767676'; // 4.54:1 on #fff / 3.67:1 on #1e1e1e (line floor)
  const paperHex = resolveToHex(layout.paper_bgcolor) || surface;
  const plotHex = resolveToHex(layout.plot_bgcolor) || paperHex;

  let out: any = layout;
  const clone = () => { if (out === layout) out = { ...layout }; };

  // Title font colour, against the paper surface it is drawn on.
  const title = layout.title;
  if (title && typeof title === 'object' && title.font && typeof title.font === 'object'
      && typeof title.font.color === 'string') {
    const cur = title.font.color;
    const repaired = ensureReadableFill(cur, paperHex, themeFont, 4.5);
    if (repaired !== cur) {
      clone();
      out.title = { ...title, font: { ...title.font, color: repaired } };
    }
  }

  // Legend: resolve a theme-clashing author bgcolor back to the theme surface so
  // the themed font reads on it, then repair an explicit legend font colour that
  // still fails against the (resolved) legend background.
  const legend = layout.legend;
  if (legend && typeof legend === 'object') {
    let nextLegend: any = legend;
    const legendClone = () => { if (nextLegend === legend) nextLegend = { ...legend }; };
    let legendBgHex = resolveToHex(legend.bgcolor);
    if (legendBgHex && isDarkBackground(legendBgHex) !== isDarkMode) {
      legendClone();
      nextLegend.bgcolor = surface;
      legendBgHex = resolveToHex(surface);
    }
    const bgForText = legendBgHex || paperHex;
    if (legend.font && typeof legend.font === 'object' && typeof legend.font.color === 'string') {
      const repaired = ensureReadableFill(legend.font.color, bgForText, themeFont, 4.5);
      if (repaired !== legend.font.color) {
        legendClone();
        nextLegend.font = { ...legend.font, color: repaired };
      }
    }
    if (nextLegend !== legend) { clone(); out.legend = nextLegend; }
  }

  // Author guide-shape strokes, against the plot surface (3:1 line floor).
  if (Array.isArray(layout.shapes)) {
    let shapesChanged = false;
    const shapes = layout.shapes.map((sh: any) => {
      if (!sh || typeof sh !== 'object' || !sh.line || typeof sh.line !== 'object'
          || typeof sh.line.color !== 'string') return sh;
      const repaired = ensureReadableFill(sh.line.color, plotHex, themeLine, 3);
      if (repaired === sh.line.color) return sh;
      shapesChanged = true;
      return { ...sh, line: { ...sh.line, color: repaired } };
    });
    if (shapesChanged) { clone(); out.shapes = shapes; }
  }

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
/**
 * Trace families whose subplot DOMAIN Plotly auto-fits to a square sized for
 * the plot area present at layout time and does NOT re-solve on a bare
 * relayout({width,height}). A figure containing any of these needs a full
 * Plotly.react() after the container settles so its domain re-centres at the
 * final width (D-300). Cartesian families (scatter/bar/heatmap/...) are absent
 * because relayout re-lays their plot area correctly on its own.
 */
export const PLOTLY_DOMAIN_TRACE_TYPES = new Set<string>([
  'pie', 'sunburst', 'icicle', 'treemap', 'funnelarea',
  'scatterpolar', 'scatterpolargl', 'barpolar',
  'scatterternary',
]);

export function hasDomainTrace(data: any): boolean {
  if (!Array.isArray(data)) return false;
  return data.some(
    (t) => t && typeof t === 'object' &&
      typeof t.type === 'string' &&
      PLOTLY_DOMAIN_TRACE_TYPES.has(t.type),
  );
}

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

// D-301 (render-div-vertical-misalignment-clips-figure): the render div's
// min-height. When the author fixed an explicit layout.height, that height is
// the min-height too, so a short author-fixed figure (e.g. 4000x260) is NOT
// inflated to a 400px div (which left an empty band above and clipped the
// bottom on capture). The 400px legibility floor applies only when no explicit
// height was authored. Pure/exported for unit testing.
export function plotlyRenderMinHeightCss(specHeight: number | undefined | null): string {
  return specHeight ? `${specHeight}px` : '400px';
}

// D-304 (title-clipped-by-grown-legend-div): the plugin forces a compact
// `margin: { t: 40 }` on every figure. Plotly draws the layout title INSIDE
// that top margin, so a 40px band is tight for a default ~17px title and the
// glyphs are clipped through their middle — worst on a legend-grown tall div
// (D-241), where the extra height made the squeeze visible on capture. Plotly's
// `title.automargin` makes the title PUSH the top margin so it is always fully
// reserved, regardless of the fixed margin.t or the div height. Enable it
// whenever a title is present and the author has not chosen a value; a no-op for
// titleless figures (byte-identical) and theme-independent (the title colour is
// themed elsewhere), so it holds in light and dark alike. Pure/exported for
// unit testing.
export function ensurePlotlyTitleAutomargin(layout: any): any {
  if (!layout || typeof layout !== 'object') return layout;
  const title = layout.title;
  if (title == null || title === '') return layout;
  if (typeof title === 'string') {
    return { ...layout, title: { text: title, automargin: true } };
  }
  if (typeof title === 'object') {
    if (title.automargin === undefined) {
      return { ...layout, title: { ...title, automargin: true } };
    }
  }
  return layout;
}

/**
 * Release everything a previous render attached to `container`: the
 * ResizeObserver and, via Plotly.purge, the plot's DOM and its WebGL contexts.
 *
 * Without this, WebGL-backed traces (scattergl, heatmapgl, scatter3d, surface,
 * ...) kept their GL context alive after the chart was re-rendered or
 * unmounted until GC happened to collect the detached canvas. Chromium counts
 * live contexts against a per-page ceiling (~16) and loses the OLDEST one when
 * it is exceeded; a long conversation with several GL charts hit that ceiling
 * and the lost contexts surfaced as repeated
 * "gl-shader: Error compiling shader: null" throws from Plotly's draw loop.
 * Safe to call on a container that never held a plot. Exported for testing.
 */
export function teardownPlotlyContainer(container: HTMLElement, Plotly?: any): void {
  const c = container as any;
  try { c._plotlyResizeObserver?.disconnect(); } catch { /* already gone */ }
  c._plotlyResizeObserver = undefined;
  try { c._plotlyViewportObserver?.disconnect(); } catch { /* already gone */ }
  c._plotlyViewportObserver = undefined;
  const div = c._plotlyDiv;
  c._plotlyDiv = undefined;
  if (!div) return;
  const P = Plotly || (typeof window !== 'undefined' ? (window as any).Plotly : undefined);
  // Collect the canvases before purge detaches them.
  const canvases: HTMLCanvasElement[] = Array.from(div.querySelectorAll('canvas'));
  disablePlotlyBuiltinContextRecovery(div);
  try { P?.purge?.(div); } catch { /* plot already torn down */ }
  // Plotly.purge removes the canvases but never calls WEBGL_lose_context, so
  // Chromium keeps counting each context as live until the detached canvas is
  // garbage-collected. In a conversation with many GL charts that lag alone
  // pushed the page over the ~16-context ceiling on every re-render and
  // evicted the oldest live chart. Release the contexts now.
  canvases.forEach(releaseWebglContext);
}

/**
 * Lose the WebGL context on a canvas so the browser frees its slot at once.
 * A canvas that holds (or can take) a 2D context is skipped so this never
 * creates a GL context just to lose it. Safe on an already-lost context.
 */
function releaseWebglContext(canvas: HTMLCanvasElement): void {
  if (typeof WebGLRenderingContext === 'undefined') return;
  let gl: any = null;
  try {
    if (canvas.getContext('2d')) return;
    gl = canvas.getContext('webgl') || canvas.getContext('webgl2') || canvas.getContext('experimental-webgl');
  } catch { return; }
  if (!gl || typeof gl.getExtension !== 'function') return;
  try {
    if (gl.isContextLost && gl.isContextLost()) return;
    gl.getExtension('WEBGL_lose_context')?.loseContext();
  } catch { /* context already gone */ }
}

/**
 * Plotly's gl3d scenes install their own context-loss handler
 * (`glplot.oncontextloss = () => scene.recoverContext()`), which disposes the
 * plot and then polls `scene.glplot.gl.isContextLost()` every frame until the
 * context returns. Our recovery purges the scene instead, which nulls
 * `scene.glplot` and turns that poll into a "Cannot read properties of null
 * (reading 'gl')" throw; the two recoveries also fight over one canvas.
 * Detach Plotly's handler so ours is the only one. No-op for non-3D plots.
 */
function disablePlotlyBuiltinContextRecovery(div: HTMLElement): void {
  const fl = (div as any)._fullLayout;
  const ids: string[] = fl?._subplots?.gl3d || [];
  for (const id of ids) {
    const glplot = fl[id]?._scene?.glplot;
    if (glplot) glplot.oncontextloss = null;
  }
}

/**
 * True when `el` is more than one viewport height above or below the visible
 * area. Unmeasurable elements (detached, zero rect) count as visible.
 */
function isFarOffscreen(el: HTMLElement): boolean {
  if (!el.isConnected || typeof window === 'undefined') return false;
  const r = el.getBoundingClientRect();
  if (r.width === 0 && r.height === 0) return false;
  const vh = window.innerHeight || 0;
  return r.bottom < -vh || r.top > 2 * vh;
}

/** Recoveries attempted per container before giving up on a lost context. */
export const PLOTLY_MAX_CONTEXT_LOSS_RECOVERIES = 1;

/**
 * Recover from `webglcontextlost` on the plot's canvases.
 *
 * Plotly has no context-loss recovery of its own: once Chromium revokes the
 * context (context ceiling, GPU reset, sleep/wake) every subsequent draw in
 * Plotly's rAF/hover callbacks throws `gl-shader: Error compiling shader:
 * null` (null = getShaderInfoLog on a lost context) outside any await or try
 * in this file, so the throws escape to window.onerror once per frame. Purge
 * the dead plot immediately to stop that loop, then re-render once with the
 * `*gl` traces demoted to their SVG equivalents so the chart comes back
 * without a new GL context. Trace families with no SVG equivalent (3D) are
 * retried as-is once; a second loss leaves a static notice rather than a
 * throw loop.
 */
function installContextLossRecovery(
  container: HTMLElement,
  renderDiv: HTMLElement,
  plotlySpec: any,
  isDarkMode: boolean,
  Plotly: any,
): void {
  disablePlotlyBuiltinContextRecovery(renderDiv);
  const canvases = renderDiv.querySelectorAll('canvas');
  if (canvases.length === 0) return;
  let handled = false;
  const rerender = (s: any, detail: Record<string, unknown>) => {
    // `render` is async here, but the D3RenderPlugin interface types it as
    // possibly sync; Promise.resolve normalizes either shape for `.then`.
    Promise.resolve(plotlyPlugin.render(container, null, s, isDarkMode)).then(
      () => container.dispatchEvent(new CustomEvent('plotly-context-lost', {
        detail: { recovered: true, ...detail }, bubbles: true,
      })),
      (err: unknown) => console.warn('Plotly context-loss re-render failed:', err),
    );
  };
  const onLost = () => {
    if (handled) return;
    handled = true;
    const c = container as any;
    // A loss raised by teardownPlotlyContainer itself (re-render, unmount)
    // has already moved _plotlyDiv on; only the current plot recovers.
    if (c._plotlyDiv !== renderDiv) return;
    teardownPlotlyContainer(container, Plotly);
    // Under the context ceiling the browser evicts the OLDEST context, which
    // is usually a chart scrolled far out of view. Re-rendering it at once
    // takes a new context, evicts the next-oldest, and cascades down the
    // page until every GL chart has burnt its recovery. Off-screen, leave a
    // placeholder and redraw when the chart scrolls back into view; this
    // does not count against the recovery budget.
    if (isFarOffscreen(container) && typeof IntersectionObserver !== 'undefined') {
      container.innerHTML =
        '<div class="plotly-context-paused" style="padding:16px;text-align:center;color:#888;">' +
        '📊 Chart paused to stay within the browser\'s WebGL limit; it redraws when scrolled into view.</div>';
      const io = new IntersectionObserver(entries => {
        if (!entries.some(e => e.isIntersecting)) return;
        io.disconnect();
        if (c._plotlyViewportObserver === io) c._plotlyViewportObserver = undefined;
        rerender(plotlySpec, { deferred: true });
      }, { rootMargin: '100% 0px' });
      io.observe(container);
      c._plotlyViewportObserver = io;
      container.dispatchEvent(new CustomEvent('plotly-context-lost', {
        detail: { recovered: false, deferred: true }, bubbles: true,
      }));
      return;
    }
    const losses = (c._plotlyContextLosses || 0) + 1;
    c._plotlyContextLosses = losses;
    if (losses > PLOTLY_MAX_CONTEXT_LOSS_RECOVERIES) {
      container.innerHTML =
        '<div class="plotly-context-lost" style="padding:16px;text-align:center;color:#888;">' +
        '📊 WebGL context lost; this chart could not be recovered. Reload to redraw it.</div>';
      container.dispatchEvent(new CustomEvent('plotly-context-lost', {
        detail: { recovered: false }, bubbles: true,
      }));
      return;
    }
    rerender({ ...plotlySpec, data: demoteWebglTracesForCapture(plotlySpec.data, true) }, {});
  };
  canvases.forEach(cv => cv.addEventListener('webglcontextlost', onLost, { once: true }));
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
    } else if (isD3HierarchySpec(spec)) {
      // Convert BEFORE the destructuring below, which strips `type` -- the
      // very field the hierarchy shape is recognised by.
      plotlySpec = hierarchySpecToPlotly(spec);
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

    // Purge any plot a previous render left on this container so its WebGL
    // contexts are released now rather than when GC reaches the detached div.
    teardownPlotlyContainer(container, Plotly);
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
      // D-241 legend grow OR D-197 dense-sankey grow, whichever is taller: a
      // sankey draws only one legend entry, so the legend path never fires for
      // it, but a crowded sankey column needs the same taller capture div.
      const grownPx = legendAwareRenderHeightPx(legendEntries);
      const sankeyPx = sankeyAwareRenderHeightPx(plotlySpec.data);
      const px = Math.max(grownPx ?? 0, sankeyPx ?? 0);
      divHeight = px > 0 ? px + 'px' : '60vh';
    }
    // D-301 (render-div-vertical-misalignment-clips-figure): when the author
    // fixed an explicit layout.height, honour it as the min-height too. The
    // former unconditional min-height:400px inflated a short author-fixed
    // figure (e.g. 4000x260) to a 400px div, and safeResize() then relaid the
    // plot to that inflated clientHeight — leaving a ~140px empty band above
    // the figure and clipping it at the bottom. Only apply the 400px legibility
    // floor when NO explicit height was authored.
    const minHeightCss = plotlyRenderMinHeightCss(specHeight);
    renderDiv.style.cssText = `width:100%;height:${divHeight};min-height:${minHeightCss};box-sizing:border-box;`;
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
    // D-304: reserve top-margin space for the title so the forced compact
    // margin.t (40px) can never clip it — worst on a legend-grown tall div.
    Object.assign(layout, ensurePlotlyTitleAutomargin(layout));
    // D-457: repair author-pinned title/legend/shape colours against the
    // theme-resolved surfaces (runs after applyPlotlyTheme has set them).
    Object.assign(layout, repairAuthorColorsForTheme(layout, isDarkMode));
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

    // D-302: certain trace-family combinations (a `splom` inside a layout.grid
    // beside contour/histogram2d; `carpet` + `contourcarpet`) make
    // Plotly.newPlot never settle, so the headless capture harness waited out
    // its full wall-clock timeout and surfaced a blank capture (svg:0/canvas:0)
    // with total data loss and NO diagnostic. demoteWebglTracesForCapture only
    // rewrites 'gl'-suffix types, so nothing guards these families. Bound
    // newPlot with a race so an un-settling combination throws a fast NAMED
    // error (mirroring the D-230 parse-failure throw) instead of a silent hang —
    // a named failure the harness can record beats a blank 30s timeout.
    await Promise.race([
      Plotly.newPlot(renderDiv, plotData, layout, config),
      new Promise((_resolve, reject) => setTimeout(
        () => reject(new Error(
          `Plotly.newPlot did not settle within ${PLOTLY_NEWPLOT_BUDGET_MS}ms ` +
          '(likely an unsupported trace-family combination such as splom-in-grid ' +
          'or carpet/contourcarpet); aborting to surface a diagnostic instead of ' +
          'a blank capture timeout.'
        )),
        PLOTLY_NEWPLOT_BUDGET_MS,
      )),
    ]);

    // Animation frames. A figure's `frames` array is driven by
    // layout.updatemenus (Play) and layout.sliders whose steps call
    // method:"animate" with frame NAMES. The four-argument newPlot above has
    // no slot for frames, so until they are registered here the Play button
    // and slider render but do nothing (animate() finds an empty frame
    // store). addFrames needs a plot on the div, hence after newPlot settles.
    // The resize path's four-arg Plotly.react() leaves an existing frame
    // store alone, so a single registration suffices; the context-loss
    // re-render re-enters render() with the same spec and re-registers.
    // A malformed frame degrades to a static chart, not an error panel over
    // a chart newPlot already drew.
    if (Array.isArray(plotlySpec.frames) && plotlySpec.frames.length > 0) {
      try {
        await Plotly.addFrames(renderDiv, plotlySpec.frames);
      } catch (err) {
        console.warn('Plotly.addFrames rejected; chart rendered without animation:', err);
      }
    }

    // Plotly.Plots.resize() returns a Promise that REJECTS asynchronously
    // ("Resize must be passed a displayed plot div element") when the div
    // has been detached or hidden by the time a deferred callback fires
    // (React re-render, cleanup racing the timers, or a viz still off in
    // a collapsed/inactive tab). A bare try/catch only catches synchronous
    // throws, so that rejection was escaping as an unhandled promise
    // rejection. Check the div is connected AND actually displayed before
    // calling resize, and always attach a .catch() to swallow any
    // rejection that still slips through the check-then-call gap.
    // D-300: Plotly.Plots.resize() recomputes only the CARTESIAN plot area from
    // the new width; it does NOT re-solve domain-based trace geometry (pie,
    // sunburst, icicle, treemap, scatterpolar, barpolar, scatterternary). After
    // the container settles to its true capture width those traces stayed pinned
    // to their stale newPlot-time domain — the disc rendered top-left in empty
    // paper while the layout title and paper-referenced (0.5,0.5) annotations
    // tracked the real 1280px width. Force a FULL relayout at the MEASURED
    // container geometry (explicit width/height) so every trace family, domain
    // ones included, re-solves against the final size. The measured-size guard
    // (w/h > 0) also subsumes the old `offsetParent === null` bail: a
    // detached/hidden div reports 0 and is skipped, but a displayed-yet-
    // unpositioned div (e.g. in the headless capture harness) is no longer
    // falsely skipped. Async rejection is still swallowed via try/catch + .catch.
    // D-300: measuring the div and calling Plotly.relayout({width,height})
    // re-centres PAPER-referenced items (layout.title, annotations at 0.5) at
    // the new width but does NOT re-solve the auto-fitted DOMAIN of a
    // non-cartesian subplot (pie/sunburst/icicle/treemap/funnelarea/polar/
    // ternary). Plotly fits those subplots to a SQUARE sized for the plot area
    // present at newPlot time; when newPlot ran at a stale narrow width the
    // domain stayed e.g. x:[0,0.4], so after the paper grew to the true capture
    // width the disc stayed pinned top-left while the title floated centred over
    // empty paper (observed: w1-08/09/14, w2-05/15). relayout alone left this
    // untouched — same blind spot as the earlier Plots.resize(). A full
    // Plotly.react() re-runs supplyDefaults+calc so every subplot domain is
    // recomputed against the final plot area; we hand it the already-themed
    // data/layout so the D-214 polar/table theming is preserved. Cartesian-only
    // figures keep the cheaper relayout path they already pass under, so the ~40
    // cartesian specs are byte-identical.
    const needsDomainResolve = hasDomainTrace(plotData);
    const safeResize = () => {
      if (!renderDiv.isConnected) return;
      const w = Math.round(renderDiv.clientWidth || 0);
      const h = Math.round(renderDiv.clientHeight || 0);
      try {
        let p: any;
        if (w > 0 && h > 0) {
          const sizedLayout = { ...layout, width: w, height: h, autosize: false };
          p = needsDomainResolve
            ? Plotly.react(renderDiv, plotData, sizedLayout, config)
            : Plotly.relayout(renderDiv, { width: w, height: h, autosize: false });
        } else {
          p = Plotly.Plots.resize(renderDiv);
        }
        if (p && typeof p.catch === 'function') p.catch(() => { /* torn down mid-resize */ });
      } catch { /* torn down */ }
    };

    // Force a resize after the next paint — the container's final width
    // often isn't known at newPlot time, causing Plotly to fall back to
    // its 700x450 default. Re-running the relayout picks up the real width
    // and re-solves domain-trace geometry.
    requestAnimationFrame(safeResize);
    setTimeout(safeResize, 200);

    const resizeObserver = new ResizeObserver(safeResize);
    resizeObserver.observe(container);
    (container as any)._plotlyResizeObserver = resizeObserver;
    (container as any)._plotlyDiv = renderDiv;
    // Teardown hook run by D3Renderer on re-render and unmount (the plugin
    // contract returns void, so this is the only channel back to the host).
    (container as any).__vizCleanup = () => teardownPlotlyContainer(container, Plotly);

    addActionButtons(container, renderDiv, plotlySpec, isDarkMode, Plotly);

    installContextLossRecovery(container, renderDiv, plotlySpec, isDarkMode, Plotly);

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
