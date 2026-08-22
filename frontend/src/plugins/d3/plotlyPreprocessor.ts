/**
 * Preprocessor for Plotly specs.
 *
 * LLMs routinely emit technically-valid Plotly specs that render poorly:
 * titles colliding with plot area, colorbars positioned outside paper
 * bounds, annotations overlapping subtitles, scene domains too close
 * together, etc.  Rather than instructing the model to avoid these
 * quirks (an unbounded game), we normalize the spec here.
 *
 * Each fix is conservative: it only activates when the quirk is present
 * AND the user hasn't made an explicit choice that would conflict.
 * Specs that were already well-constructed pass through unchanged.
 *
 * Exported individually for unit testing; composed into
 * `preprocessPlotlySpec` which is what the plugin calls.
 */

type PlotlySpec = {
  data?: any[];
  layout?: any;
  config?: any;
  [k: string]: any;
};

/**
 * Offset two-line titles so they don't collide with the plot area.
 *
 * Plotly's default title.y places the title at the very top of the paper,
 * and when `title.text` contains a `<br>` or `<sub>` tag the second line
 * pushes down into the plot. If the user hasn't set title.y explicitly,
 * nudge it down slightly and ensure margin.t is large enough to fit.
 */
export function fixMultilineTitle(layout: any): any {
  if (!layout?.title) return layout;
  const title = typeof layout.title === 'string' ? { text: layout.title } : layout.title;
  const text: string = title.text || '';
  const hasMultiline = text.includes('<br>') || text.includes('<sub>') || text.includes('\n');
  if (!hasMultiline) return layout;

  const newTitle = { ...title };
  if (newTitle.y === undefined) newTitle.y = 0.97;

  const newLayout = { ...layout, title: newTitle };
  const currentTop = layout.margin?.t;
  if (currentTop === undefined || currentTop < 100) {
    newLayout.margin = { ...(layout.margin || {}), t: 100 };
  }
  return newLayout;
}

/**
 * Clamp colorbars positioned beyond the paper boundary.
 *
 * Plotly colorbar.x is in paper coordinates; values > 1 place the bar
 * outside the visible area and it gets clipped. LLMs often emit x=1.15
 * or similar when trying to place multiple colorbars side-by-side.
 * Pull anything > 1.02 back to 0.99 and set xanchor=left so it sits
 * just inside the right edge.
 */
export function clampColorbars(data: any[]): any[] {
  if (!Array.isArray(data)) return data;
  return data.map(trace => {
    if (!trace?.marker?.colorbar && !trace?.colorbar) return trace;
    const patchBar = (bar: any): any => {
      if (!bar) return bar;
      const out = { ...bar };
      if (typeof out.x === 'number' && out.x > 1.02) {
        out.x = 0.99;
        if (out.xanchor === undefined) out.xanchor = 'left';
      }
      if (typeof out.y === 'number' && (out.y > 1.02 || out.y < -0.02)) {
        out.y = Math.max(0.05, Math.min(0.95, out.y));
      }
      return out;
    };
    const newTrace = { ...trace };
    if (newTrace.marker?.colorbar) {
      newTrace.marker = { ...newTrace.marker, colorbar: patchBar(newTrace.marker.colorbar) };
    }
    if (newTrace.colorbar) {
      newTrace.colorbar = patchBar(newTrace.colorbar);
    }
    return newTrace;
  });
}

/**
 * Shrink scene domains that consume the full vertical space when a
 * multi-line title is present.
 *
 * Scenes with `domain.y: [0, 1]` collide with titles at y=0.97.
 * When a multi-line title exists, cap scene.domain.y[1] at 0.88 to
 * leave room. Only applies when the user hasn't set a smaller upper
 * bound already.
 */
export function adjustSceneDomainsForTitle(layout: any): any {
  if (!layout?.title) return layout;
  const titleText = typeof layout.title === 'string' ? layout.title : layout.title?.text || '';
  const hasMultiline = titleText.includes('<br>') || titleText.includes('<sub>');
  if (!hasMultiline) return layout;

  const newLayout = { ...layout };
  for (const key of Object.keys(layout)) {
    if (!key.startsWith('scene')) continue;
    const scene = layout[key];
    if (!scene?.domain?.y) continue;
    const [low, high] = scene.domain.y;
    if (high > 0.9) {
      newLayout[key] = {
        ...scene,
        domain: { ...scene.domain, y: [low, Math.min(high, 0.88)] },
      };
    }
  }
  return newLayout;
}

/**
 * Enforce minimum gap between horizontally-adjacent scene domains.
 *
 * Scenes with `scene.domain.x: [0, 0.48]` and `scene2.domain.x: [0.52, 1]`
 * leave only 4% gap, and axis labels visually merge. If two scenes have
 * touching or near-touching x-domains, widen the gap to ≥ 6%.
 */
export function ensureSceneDomainGaps(layout: any): any {
  if (!layout) return layout;
  const scenes = Object.keys(layout).filter(k => k.startsWith('scene') && layout[k]?.domain?.x);
  if (scenes.length < 2) return layout;

  const sorted = scenes
    .map(k => ({ key: k, x: layout[k].domain.x as [number, number] }))
    .sort((a, b) => a.x[0] - b.x[0]);

  const newLayout = { ...layout };
  for (let i = 0; i < sorted.length - 1; i++) {
    const a = sorted[i];
    const b = sorted[i + 1];
    const gap = b.x[0] - a.x[1];
    if (gap < 0.06) {
      const shift = (0.06 - gap) / 2;
      const newA = Math.max(0, a.x[1] - shift);
      const newB = Math.min(1, b.x[0] + shift);
      newLayout[a.key] = { ...layout[a.key], domain: { ...layout[a.key].domain, x: [a.x[0], newA] } };
      newLayout[b.key] = { ...layout[b.key], domain: { ...layout[b.key].domain, x: [newB, b.x[1]] } };
    }
  }
  return newLayout;
}

/**
 * Adjust paper-referenced annotations that sit in the title zone.
 *
 * When a multi-line title is present and annotations use yref='paper'
 * with y > 0.92, they overlap the subtitle. Pull them down to 0.89
 * only in that specific case.
 */
export function adjustAnnotationsForTitle(layout: any): any {
  if (!layout?.annotations || !Array.isArray(layout.annotations)) return layout;
  const titleText = typeof layout.title === 'string' ? layout.title : layout.title?.text || '';
  const hasMultiline = titleText.includes('<br>') || titleText.includes('<sub>');
  if (!hasMultiline) return layout;

  const newAnnotations = layout.annotations.map((ann: any) => {
    if (ann?.yref === 'paper' && typeof ann.y === 'number' && ann.y > 0.92) {
      return { ...ann, y: 0.89 };
    }
    return ann;
  });
  return { ...layout, annotations: newAnnotations };
}

/**
 * Clamp degenerate layout geometry that stalls the headless capture.
 *
 * A figure `layout.width <= 0` / `layout.height <= 0` produces a plot area
 * with zero or negative pixel size, and an absurd `layout.font.size` (e.g.
 * 1e6) forces pathological text-metric/reflow work. Under the persistent
 * headless capture path (app/services/diagram_renderer.py) such a layout
 * never settles to a paint-complete state, so the capture times out with
 * NO image even though plotly.js internally "finished". This is a whole
 * class of degenerate-geometry input, not one spec: any non-finite,
 * zero, or negative width/height, or an out-of-range font size, is coerced
 * to a sane value here. Well-formed layouts (positive finite width/height,
 * reasonable font size, or simply omitting them for autosize) pass through
 * UNCHANGED. Exported for unit testing.
 */
export const PLOTLY_MIN_DIMENSION = 10;
export const PLOTLY_MAX_DIMENSION = 20000;
export const PLOTLY_MAX_FONT_SIZE = 200;
export const PLOTLY_MIN_FONT_SIZE = 1;

export function sanitizeLayoutGeometry(layout: any): any {
  if (!layout || typeof layout !== 'object') return layout;
  let changed = false;
  const out: any = { ...layout };

  const fixDim = (v: any): number | undefined => {
    if (v === undefined || v === null) return undefined; // let autosize handle it
    if (typeof v !== 'number' || !Number.isFinite(v) || v <= 0) {
      changed = true;
      return undefined; // drop degenerate dim -> Plotly autosizes to container
    }
    if (v > PLOTLY_MAX_DIMENSION) {
      changed = true;
      return PLOTLY_MAX_DIMENSION;
    }
    if (v < PLOTLY_MIN_DIMENSION) {
      changed = true;
      return PLOTLY_MIN_DIMENSION;
    }
    return v;
  };

  if ('width' in layout) {
    const w = fixDim(layout.width);
    if (w === undefined) delete out.width; else out.width = w;
  }
  if ('height' in layout) {
    const h = fixDim(layout.height);
    if (h === undefined) delete out.height; else out.height = h;
  }

  // Font size: coerce non-finite / out-of-range to a bounded value.
  const fixFont = (font: any): any => {
    if (!font || typeof font !== 'object') return font;
    if ('size' in font) {
      const sz = font.size;
      if (typeof sz !== 'number' || !Number.isFinite(sz) || sz <= 0) {
        changed = true;
        return { ...font, size: 12 };
      }
      if (sz > PLOTLY_MAX_FONT_SIZE) {
        changed = true;
        return { ...font, size: PLOTLY_MAX_FONT_SIZE };
      }
      if (sz < PLOTLY_MIN_FONT_SIZE) {
        changed = true;
        return { ...font, size: PLOTLY_MIN_FONT_SIZE };
      }
    }
    return font;
  };
  if (layout.font) {
    const f = fixFont(layout.font);
    if (f !== layout.font) out.font = f;
  }

  return changed ? out : layout;
}

/**
 * Disambiguate duplicate labels in hierarchy traces (treemap / sunburst /
 * icicle) so the d3 hierarchy/stratify builder does not throw
 * "ambiguous: <label>" and silently drop the entire trace.
 *
 * In these traces `parents[i]` references another node BY LABEL when no
 * explicit `ids` array is given. If two rows share the same label (e.g. a
 * duplicate `"root"` both with parent `""`), the stratifier cannot tell
 * which node a child refers to and aborts, dropping the whole trace —
 * silent data loss. Plotly-proper would coalesce same-label rows into one
 * node; to preserve every declared row instead, we synthesize a unique
 * `ids` array (only when the author did not supply one) and rewrite
 * `parents` to reference the FIRST row bearing each label. This yields an
 * unambiguous hierarchy that renders, keeping all rows.
 *
 * Only activates when a hierarchy trace has duplicate labels AND no
 * explicit `ids`. Traces that are already unambiguous, or that already
 * carry `ids`, pass through UNCHANGED. Exported for unit testing.
 */
const HIERARCHY_TRACE_TYPES = new Set(['treemap', 'sunburst', 'icicle']);

export function disambiguateHierarchyLabels(data: any[]): any[] {
  if (!Array.isArray(data)) return data;
  return data.map(trace => {
    if (!trace || typeof trace !== 'object') return trace;
    if (!HIERARCHY_TRACE_TYPES.has(trace.type)) return trace;
    // Author-supplied ids already make nodes unique -> nothing to do.
    if (Array.isArray(trace.ids) && trace.ids.length > 0) return trace;
    const labels = trace.labels;
    if (!Array.isArray(labels) || labels.length === 0) return trace;

    // Detect duplicate labels.
    const seen = new Set<string>();
    let hasDup = false;
    for (const l of labels) {
      const key = String(l);
      if (seen.has(key)) { hasDup = true; break; }
      seen.add(key);
    }
    if (!hasDup) return trace;

    // First-occurrence id for each label value.
    const firstIdForLabel = new Map<string, string>();
    const ids: string[] = labels.map((l: any, i: number) => {
      const key = String(l);
      const id = `${key}\u0000${i}`;
      if (!firstIdForLabel.has(key)) firstIdForLabel.set(key, id);
      return id;
    });

    const parents = Array.isArray(trace.parents) ? trace.parents : [];
    const newParents = parents.map((p: any) => {
      if (p === '' || p === null || p === undefined) return '';
      const key = String(p);
      // Point at the first node bearing that label; if the parent label was
      // never declared as a node, leave it (renderer treats as orphan/root).
      return firstIdForLabel.has(key) ? firstIdForLabel.get(key)! : p;
    });

    return { ...trace, ids, parents: newParents };
  });
}

/**
 * Demote WebGL trace types to their SVG equivalents when rendering under
 * headless capture (Playwright sets `navigator.webdriver === true`).
 *
 * WebGL traces (`scattergl`, `scatterpolargl`, `heatmapgl`, ...) render to a
 * <canvas> via a WebGL context. In the persistent single-browser headless
 * capture path (app/services/diagram_renderer.py reuses ONE Chromium), those
 * contexts accumulate and hit Chromium's active-WebGL-context ceiling ("Too
 * many active WebGL contexts. Oldest context will be lost."), so the render
 * page's completion handshake never settles -> the capture times out even
 * though plotly.js rendered the plot correctly. A one-shot server-side PNG
 * gains nothing from WebGL, so we swap `*gl` -> the SVG-backed trace by
 * stripping the trailing `gl`. This is a no-op for the interactive UI, where
 * `navigator.webdriver` is false and WebGL performance is preserved.
 *
 * General across the whole WebGL trace family, not one spec. Exported for
 * unit testing.
 */
export function demoteWebglTracesForCapture(data: any[]): any[] {
  if (!Array.isArray(data)) return data;
  const isHeadlessCapture =
    typeof navigator !== 'undefined' && (navigator as any).webdriver === true;
  if (!isHeadlessCapture) return data;
  return data.map(trace => {
    if (trace && typeof trace.type === 'string' && /gl$/.test(trace.type)) {
      return { ...trace, type: trace.type.replace(/gl$/, '') };
    }
    return trace;
  });
}

/**
 * Clamp astronomically large `nbinsx` / `nbinsy` on histogram-family traces.
 *
 * Plotly's histogram / histogram2d / histogram2dcontour traces auto-bin their
 * data. `nbinsx` / `nbinsy` are an UPPER BOUND on the number of bins along each
 * axis. For a 2D histogram the bin grid is nbinsx × nbinsy cells, so a value
 * like `nbinsy: 1e9` (a billion) forces plotly's synchronous auto-binning to
 * compute bin edges and accumulate over an intractable ~1e9-cell grid — the
 * render never terminates (observed: hangs past the 300s hard cap, ZERO output,
 * total data loss). This is the same UNBOUNDED-WORK class as graphviz
 * `minlen=1e6` (Issue 5): a single degenerate magnitude field starves the
 * render loop.
 *
 * We clamp `nbinsx` / `nbinsy` to a sane maximum (a histogram with more than a
 * few thousand bins is not legible on any real canvas anyway), and coerce
 * negative / zero / non-finite values to `undefined` so plotly falls back to
 * its own automatic bin count (which is what a negative value like `nbinsx:-10`
 * already meant to plotly — but we normalize it explicitly rather than relying
 * on that). Reasonable bin counts (e.g. 20, 100, 1000) pass through UNCHANGED,
 * and non-histogram traces are never touched. Exported for unit testing.
 */
const HISTOGRAM_TRACE_TYPES = new Set(['histogram', 'histogram2d', 'histogram2dcontour']);
export const PLOTLY_MAX_NBINS = 1000;

export function clampHistogramBins(data: any[]): any[] {
  if (!Array.isArray(data)) return data;
  return data.map(trace => {
    if (!trace || typeof trace !== 'object') return trace;
    if (!HISTOGRAM_TRACE_TYPES.has(trace.type)) return trace;
    if (!('nbinsx' in trace) && !('nbinsy' in trace)) return trace;

    let changed = false;
    const SENTINEL = Symbol('unchanged');
    const fixBins = (v: any): number | undefined | typeof SENTINEL => {
      // SENTINEL means "leave as-is"; undefined means "delete -> plotly autobins".
      if (typeof v !== 'number' || !Number.isFinite(v) || v <= 0) {
        changed = true;
        return undefined; // negative / zero / NaN / Infinity / non-number -> autobin
      }
      if (v > PLOTLY_MAX_NBINS) {
        changed = true;
        return PLOTLY_MAX_NBINS;
      }
      return SENTINEL; // in-range -> unchanged
    };

    const out: any = { ...trace };
    if ('nbinsx' in trace) {
      const nx = fixBins(trace.nbinsx);
      if (nx === undefined) delete out.nbinsx;
      else if (nx !== SENTINEL) out.nbinsx = nx;
    }
    if ('nbinsy' in trace) {
      const ny = fixBins(trace.nbinsy);
      if (ny === undefined) delete out.nbinsy;
      else if (ny !== SENTINEL) out.nbinsy = ny;
    }
    return changed ? out : trace;
  });
}

/**
 * Clamp astronomically large size-like magnitudes ANYWHERE in the spec that
 * force plotly.js into unbounded SYNCHRONOUS layout / text-metric / paint work,
 * blocking the single JS event-loop thread so the headless capture never
 * settles (observed: 300s hard-cap hang with ZERO output — Issue 48). Because
 * the work is synchronous, plotly's own async completion handshake and any
 * `setTimeout`-based soft abort can never run, so only the external process cap
 * can kill the render. This is the same UNBOUNDED-WORK class as the histogram
 * `nbinsy:1e9` hang (Issue 36) and graphviz `minlen=1e6` (Issue 5): a single
 * degenerate magnitude field starves the render loop.
 *
 * TWO magnitude vectors are handled here, both generalizations of clamps that
 * already existed but were INCOMPLETE:
 *
 *  1. font.size — a font size is always literal pixels; a value like `1e4`
 *     (10000px) forces plotly to measure/lay out enormous glyphs, and across a
 *     multi-subplot grid (a 3x3 dashboard, a 15-dimension splom, a table with
 *     many cells) that text-metric work explodes. `sanitizeLayoutGeometry`
 *     already clamped `layout.font.size`, but PER-TRACE and NESTED fonts leaked
 *     through entirely: `table.header.font.size` / `table.cells.font.size`,
 *     `marker.colorbar.title.font`, `hoverlabel.font`, axis `tickfont` /
 *     `titlefont`, `legend.font`, annotation `font`, etc. We now clamp EVERY
 *     object sitting under a `*font` key (font, tickfont, titlefont,
 *     insidetextfont, outsidetextfont, hoverfont, ...) at ARBITRARY DEPTH in
 *     both `data` and `layout`. A font >200px is never legible on any real
 *     canvas, so clamping to PLOTLY_MAX_FONT_SIZE is lossless in practice;
 *     non-finite / <=0 sizes are coerced to a sane default.
 *
 *  2. marker.size — an astronomical marker radius (e.g. splom `marker.size:1e6`)
 *     forces plotly to build million-pixel symbol geometry per point across
 *     every subplot. We clamp numeric sizes (and numeric array elements) beyond
 *     PLOTLY_MAX_MARKER_SIZE, and coerce non-finite / negative sizes to 0. This
 *     is done ONLY when the trace has no `sizeref`: with `sizeref` set,
 *     `marker.size` holds raw DATA values that plotly scales down (a bubble
 *     chart may legitimately carry population counts like 1e6), so those are
 *     left UNTOUCHED. Non-numeric array elements (null / strings) are also left
 *     for plotly to handle.
 *
 * Well-formed specs (font sizes <= 200, marker sizes in range or governed by
 * `sizeref`) pass through UNCHANGED and are returned BY REFERENCE (no-op is
 * reference-stable, so the pass is provably free of collateral mutation).
 * Exported for unit testing.
 */
export const PLOTLY_MAX_MARKER_SIZE = 1000;

// Matches plotly's family of font sub-object keys: font, tickfont, titlefont,
// insidetextfont, outsidetextfont, hoverfont, etc.
const FONT_KEY_RE = /font$/i;

function clampToFontRange(sz: any): number {
  if (typeof sz !== 'number' || !Number.isFinite(sz) || sz <= 0) return 12;
  if (sz > PLOTLY_MAX_FONT_SIZE) return PLOTLY_MAX_FONT_SIZE;
  if (sz < PLOTLY_MIN_FONT_SIZE) return PLOTLY_MIN_FONT_SIZE;
  return sz;
}

/**
 * Recursively clamp `size` on every font-like sub-object. Returns the SAME
 * reference when nothing needed clamping (so callers can rely on reference
 * equality to detect a no-op); only the touched branch is cloned otherwise.
 */
function clampFontSizesDeep(node: any): any {
  if (Array.isArray(node)) {
    let changed = false;
    const out = node.map(el => {
      const ne = clampFontSizesDeep(el);
      if (ne !== el) changed = true;
      return ne;
    });
    return changed ? out : node;
  }
  if (!node || typeof node !== 'object') return node;

  let out: any = node;
  const clone = () => { if (out === node) out = { ...node }; };

  for (const k of Object.keys(node)) {
    const v = node[k];
    let nv = clampFontSizesDeep(v);
    // If this child is a font-ish object carrying a bad size, clamp its size.
    if (FONT_KEY_RE.test(k) && nv && typeof nv === 'object' && !Array.isArray(nv) && 'size' in nv) {
      const cs = clampToFontRange(nv.size);
      if (cs !== nv.size) nv = { ...nv, size: cs };
    }
    if (nv !== v) { clone(); out[k] = nv; }
  }
  return out;
}

function clampMarkerSizeValue(size: any): any {
  if (typeof size === 'number') {
    if (!Number.isFinite(size) || size < 0) return 0;
    if (size > PLOTLY_MAX_MARKER_SIZE) return PLOTLY_MAX_MARKER_SIZE;
    return size;
  }
  if (Array.isArray(size)) {
    let changed = false;
    const out = size.map(s => {
      if (typeof s !== 'number') return s; // leave null/strings for plotly
      if (!Number.isFinite(s) || s < 0) { changed = true; return 0; }
      if (s > PLOTLY_MAX_MARKER_SIZE) { changed = true; return PLOTLY_MAX_MARKER_SIZE; }
      return s;
    });
    return changed ? out : size;
  }
  return size;
}

function clampTraceMarkerSizes(data: any[]): any[] {
  if (!Array.isArray(data)) return data;
  let anyChanged = false;
  const out = data.map(trace => {
    if (!trace || typeof trace !== 'object') return trace;
    const marker = trace.marker;
    if (!marker || typeof marker !== 'object' || Array.isArray(marker)) return trace;
    if (!('size' in marker)) return trace;
    // sizeref present -> marker.size holds raw data values plotly scales; legit.
    if (marker.sizeref !== undefined) return trace;
    const ns = clampMarkerSizeValue(marker.size);
    if (ns === marker.size) return trace;
    anyChanged = true;
    return { ...trace, marker: { ...marker, size: ns } };
  });
  return anyChanged ? out : data;
}

export function clampExtremeSizes(spec: PlotlySpec): PlotlySpec {
  if (!spec || typeof spec !== 'object') return spec;
  let out: PlotlySpec = spec;
  if (Array.isArray(spec.data)) {
    const nd = clampTraceMarkerSizes(spec.data);
    if (nd !== spec.data) out = { ...out, data: nd };
  }
  // Deep font clamp over the whole spec (data + layout, any depth).
  return clampFontSizesDeep(out);
}

/** Compose all preprocessors. Order matters: title fix first so subsequent
 *  passes see the adjusted title state. */
export function preprocessPlotlySpec(spec: PlotlySpec): PlotlySpec {
  if (!spec || typeof spec !== 'object') return spec;
  let layout = spec.layout;
  layout = fixMultilineTitle(layout);
  layout = adjustSceneDomainsForTitle(layout);
  layout = ensureSceneDomainGaps(layout);
  layout = adjustAnnotationsForTitle(layout);
  layout = sanitizeLayoutGeometry(layout);
  let data = clampColorbars(spec.data || []);
  data = demoteWebglTracesForCapture(data);
  data = disambiguateHierarchyLabels(data);
  data = clampHistogramBins(data);
  // Final magnitude pass over the WHOLE spec: clamp every font.size and any
  // astronomical marker.size that would block the render thread (Issue 48).
  return clampExtremeSizes({ ...spec, data, layout });
}
