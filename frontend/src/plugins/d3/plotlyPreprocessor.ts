import JSON5 from 'json5';
import { classifyColor, namedColorToHex, contrastRatio, ensureReadableFill, isDarkBackground } from './chartTheme';

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

/* ------------------------------------------------------------------------ *
 * Tolerant spec-string parsing (D-230).
 *
 * plotlyPlugin.render / canHandle / isDefinitionComplete all used a bare
 * JSON.parse, so ANY one-lexeme-off input (trailing comma, ```json fence,
 * unquoted keys, single quotes, U+201C/D/18/19 smart quotes, a
 * `var fig = {...};` assignment wrapper with // and /* *​/ comments, or a
 * Python repr with True/False/None/nan) threw before any plugin output. The
 * host page never learned the render failed, so the headless capture harness
 * waited out the full 30s wall clock with an empty DOM and no diagnostic.
 *
 * These helpers give the plugin ONE tolerant entry point. Order mirrors the
 * force-directed recovery (D-024): strip a markdown fence, fold smart quotes
 * to ASCII (json5 rejects U+201C..), slice to the outermost {...} (drops
 * leading prose / a `var x =` prefix / a trailing `;`), try strict JSON
 * (fast, unchanged behaviour), then json5 (trailing commas / unquoted /
 * single-quoted keys / comments), and only as a LAST resort fold Python
 * literals and retry json5. Pure/testable — no DOM.
 * ------------------------------------------------------------------------ */

/** Strip a ```json … ``` (or bare ```) markdown fence, matched or unmatched. */
export function stripPlotlyFence(raw: string): string {
  let t = String(raw).trim();
  const matched = /^```[a-zA-Z0-9_-]*\s*\n?([\s\S]*?)\n?```$/.exec(t);
  if (matched) return matched[1].trim();
  t = t.replace(/^```[a-zA-Z0-9_-]*\s*/, '').replace(/```\s*$/, '');
  return t.trim();
}

/** Normalise smart/curly quotes to ASCII so a copy-pasted payload parses.
 *  json5 does NOT accept U+201C/U+201D/U+2018/U+2019. */
export function normalizePlotlySmartQuotes(raw: string): string {
  return String(raw)
    .replace(/[\u201C\u201D\u201E\u201F]/g, '"')
    .replace(/[\u2018\u2019\u201A\u201B]/g, "'");
}

/**
 * Fold Python-repr literals (`None`/`True`/`False`/`nan`) to their JSON5
 * equivalents. Applied ONLY as a last-resort fallback (after strict JSON and
 * json5 have both failed) and restricted to VALUE positions — a token that is
 * preceded by `:`, `[` or `,` and followed by `,`, `]` or `}` — so a literal
 * appearing inside a quoted string (e.g. a title `"None of the above"`, whose
 * `None` is preceded by `"`) is never rewritten. `Infinity`/`NaN` are already
 * accepted by json5 and left alone.
 */
export function normalizePythonLiterals(raw: string): string {
  return String(raw)
    .replace(/([:[,]\s*)None(?=\s*[,\]}])/g, '$1null')
    .replace(/([:[,]\s*)True(?=\s*[,\]}])/g, '$1true')
    .replace(/([:[,]\s*)False(?=\s*[,\]}])/g, '$1false')
    .replace(/([:[,]\s*)nan(?=\s*[,\]}])/g, '$1NaN');
}

/**
 * Tolerantly parse a JSON-ish Plotly definition string into an object.
 * Returns the parsed object, or `undefined` when unrecoverable (so the caller
 * can THROW a fast named error instead of hanging). Pure/testable.
 */
export function parsePlotlyDefinition(raw: any): any {
  if (raw && typeof raw === 'object') return raw; // already parsed
  if (typeof raw !== 'string') return undefined;
  const cleaned = normalizePlotlySmartQuotes(stripPlotlyFence(raw)).trim();
  if (!cleaned) return undefined;
  const first = cleaned.indexOf('{');
  const last = cleaned.lastIndexOf('}');
  if (first === -1 || last === -1 || last < first) return undefined;
  const body = cleaned.slice(first, last + 1);
  try { return JSON.parse(body); } catch (_e) { /* try json5 */ }
  try { return JSON5.parse(body); } catch (_e) { /* try python-literal fold */ }
  try { return JSON5.parse(normalizePythonLiterals(body)); } catch (_e) { /* unrecoverable */ }
  return undefined;
}

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

/**
 * Enable Plotly's automargin so axis titles / tick labels are never clipped or
 * overprinted (D-236).
 *
 * render() writes a HARDCODED `layout.margin { t:40, r:20, b:40, l:60 }` and
 * never turns on automargin, so text that needs more room than the fixed box
 * collides or is sliced off the canvas in several theme-blind shapes: a
 * yaxis2 title over its own right ticks, 20-char rotated x labels truncated to
 * a stub inside a 40px band, y ticks overprinting in a short figure, an axis
 * title printed on top of a tick. `xaxis.automargin` / `yaxis.automargin`
 * (and the multi-axis xaxis2/yaxis2… variants) let Plotly GROW the margin from
 * the fixed base to fit whatever it actually renders. `title.automargin`
 * (Plotly ≥2.10) does the same for the main layout title so a long single-line
 * title reserves its own band instead of clipping at the paper edge.
 *
 * Conservative and SURGICAL, to honour the "do not alter unrelated output"
 * contract: automargin is set ONLY on axis objects that ALREADY EXIST (the
 * clipping cases in the triage all carry an explicit axis title / tickangle,
 * so the axis object is present) — bare specs and pure-3D/polar specs with no
 * cartesian axis are left byte-identical, and no phantom xaxis/yaxis is
 * injected. The main title gets `automargin` only when it is LONG enough to
 * risk clipping (`title.automargin`, Plotly ≥2.10) so short titles pass
 * through unchanged. Automargin only ever GROWS the margin from the fixed
 * base, so a plot that already fits is pixel-identical. Exported for testing.
 */
export const PLOTLY_LONG_TITLE_CHARS = 60;

export function enableAxisAutomargin(layout: any): any {
  if (!layout || typeof layout !== 'object') return layout;
  let out: any = layout;
  const clone = () => { if (out === layout) out = { ...layout }; };

  for (const key of Object.keys(layout)) {
    if (!/^[xy]axis(\d*)$/.test(key)) continue;
    const axis = layout[key];
    if (!axis || typeof axis !== 'object' || Array.isArray(axis)) continue;
    if (axis.automargin === undefined) {
      clone();
      out[key] = { ...axis, automargin: true };
    }
  }

  // Main layout title: reserve its own band ONLY for a long single-line title
  // (short titles never clip and must pass through byte-identical). Multiline
  // titles are already handled upstream by fixMultilineTitle.
  if (layout.title !== undefined) {
    const title = typeof layout.title === 'string' ? { text: layout.title } : layout.title;
    const text: string = (title && typeof title === 'object' && title.text) ? String(title.text) : '';
    const isLong = text.length > PLOTLY_LONG_TITLE_CHARS;
    if (isLong && title && typeof title === 'object' && title.automargin === undefined) {
      clone();
      out.title = { ...title, automargin: true };
    }
  }
  return out;
}

/**
 * Clamp an explicit `layout.width` / `layout.height` to a maximum (D-238).
 * Pure — the caller supplies the ceiling. Only shrinks; never grows or adds a
 * dimension. Returns the SAME reference when nothing needed clamping.
 */
export function clampLayoutDimensions(layout: any, maxW: number, maxH: number): any {
  if (!layout || typeof layout !== 'object') return layout;
  let out: any = layout;
  const clone = () => { if (out === layout) out = { ...layout }; };
  if (typeof layout.width === 'number' && Number.isFinite(layout.width) && layout.width > maxW) {
    clone(); out.width = maxW;
  }
  if (typeof layout.height === 'number' && Number.isFinite(layout.height) && layout.height > maxH) {
    clone(); out.height = maxH;
  }
  return out;
}

/**
 * Under headless capture, clamp an oversized explicit width/height to the
 * capture viewport (D-238).
 *
 * `layout.width`/`height` are honoured literally and `sanitizeLayoutGeometry`
 * only clamps above PLOTLY_MAX_DIMENSION (20000) — far above the ~1280px
 * capture viewport — so a `width:4000` / `height:2600` figure paints past the
 * screenshot bounds and the overflow is silently CROPPED (69% of points / an
 * entire axis lost). Clamping to the viewport lets Plotly autosize/scale ALL
 * the content into the visible area instead of losing it. Scoped to the
 * headless capture (`navigator.webdriver === true`, same gate as
 * `demoteWebglTracesForCapture`) so the interactive UI — where a user may
 * legitimately request a large export size on a wide screen — is UNCHANGED.
 * Exported for unit testing.
 */
export const PLOTLY_CAPTURE_FALLBACK_WIDTH = 1280;
export const PLOTLY_CAPTURE_FALLBACK_HEIGHT = 1024;

export function clampDimensionsToViewportForCapture(layout: any): any {
  if (!layout || typeof layout !== 'object') return layout;
  const isHeadlessCapture =
    typeof navigator !== 'undefined' && (navigator as any).webdriver === true;
  if (!isHeadlessCapture) return layout;
  const vw = (typeof window !== 'undefined' && window.innerWidth > 0)
    ? window.innerWidth : PLOTLY_CAPTURE_FALLBACK_WIDTH;
  const vh = (typeof window !== 'undefined' && window.innerHeight > 0)
    ? window.innerHeight : PLOTLY_CAPTURE_FALLBACK_HEIGHT;
  return clampLayoutDimensions(layout, vw, vh);
}

/* ======================================================================== *
 * G-29 / plotly recovery + colour-token normalisation.
 *
 * Four author-input failure modes that produce a plausible-but-wrong chart
 * with NO error (or a hard corruption), grouped by shared root cause in the
 * plotly preprocessor:
 *
 *  D-231  string-shorthand titles are dropped by plotly v2 (only the object
 *         form {text:…} renders); layout keys emitted at the spec ROOT and
 *         marker keys at the TRACE root are discarded because nothing
 *         promotes/demotes them.
 *  D-232  non-colour tokens (var(--x), 'primary', '$background', a dotted
 *         'theme.text', a bogus template) are passed straight through and
 *         silently fall back to the LIBRARY default instead of the theme
 *         default — a torn/half-themed canvas (worst case: a white slab on the
 *         dark page). Trace-level invalid colours are stripped HERE (→ plotly
 *         assigns a palette series colour); layout-level colours are repaired
 *         theme-aware in applyPlotlyTheme (which knows isDarkMode).
 *  D-234  a four-arg rgb(r,g,b,a) (a common slip for rgba) has its alpha
 *         SILENTLY dropped and renders fully opaque.
 *
 * Every pass is conservative: it only fires when the quirk is present, only
 * ever touches the malformed value, and returns input BY REFERENCE on a no-op.
 * ======================================================================== */

/** Plotly's built-in template NAMES. A string `layout.template` that is not
 *  one of these is a typo/hallucination (e.g. 'plotly_dark_v2') that plotly
 *  silently ignores while ALSO suppressing our theme defaults — so it must be
 *  dropped. An OBJECT template (a real inline template) is always kept. */
export const KNOWN_PLOTLY_TEMPLATES = new Set([
  'plotly', 'plotly_white', 'plotly_dark', 'ggplot2', 'seaborn',
  'simple_white', 'none', 'presentation', 'xgridoff', 'ygridoff', 'gridon',
]);

/** The CSS3/CSS4 named colours plotly (and the browser) actually resolve. A
 *  bare word NOT in this set (e.g. 'primary', 'surface') is a design-system
 *  token, not a colour, and must be treated as invalid. */
const CSS_NAMED_COLORS = new Set([
  'aliceblue','antiquewhite','aqua','aquamarine','azure','beige','bisque','black',
  'blanchedalmond','blue','blueviolet','brown','burlywood','cadetblue','chartreuse',
  'chocolate','coral','cornflowerblue','cornsilk','crimson','cyan','darkblue',
  'darkcyan','darkgoldenrod','darkgray','darkgrey','darkgreen','darkkhaki',
  'darkmagenta','darkolivegreen','darkorange','darkorchid','darkred','darksalmon',
  'darkseagreen','darkslateblue','darkslategray','darkslategrey','darkturquoise',
  'darkviolet','deeppink','deepskyblue','dimgray','dimgrey','dodgerblue','firebrick',
  'floralwhite','forestgreen','fuchsia','gainsboro','ghostwhite','gold','goldenrod',
  'gray','grey','green','greenyellow','honeydew','hotpink','indianred','indigo',
  'ivory','khaki','lavender','lavenderblush','lawngreen','lemonchiffon','lightblue',
  'lightcoral','lightcyan','lightgoldenrodyellow','lightgray','lightgrey','lightgreen',
  'lightpink','lightsalmon','lightseagreen','lightskyblue','lightslategray',
  'lightslategrey','lightsteelblue','lightyellow','lime','limegreen','linen','magenta',
  'maroon','mediumaquamarine','mediumblue','mediumorchid','mediumpurple',
  'mediumseagreen','mediumslateblue','mediumspringgreen','mediumturquoise',
  'mediumvioletred','midnightblue','mintcream','mistyrose','moccasin','navajowhite',
  'navy','oldlace','olive','olivedrab','orange','orangered','orchid','palegoldenrod',
  'palegreen','paleturquoise','palevioletred','papayawhip','peachpuff','peru','pink',
  'plum','powderblue','purple','rebeccapurple','red','rosybrown','royalblue',
  'saddlebrown','salmon','sandybrown','seagreen','seashell','sienna','silver','skyblue',
  'slateblue','slategray','slategrey','snow','springgreen','steelblue','tan','teal',
  'thistle','tomato','turquoise','violet','wheat','white','whitesmoke','yellow',
  'yellowgreen','transparent',
]);

/**
 * True when `v` is a colour plotly can actually resolve: a #hex (3/4/6/8 digit),
 * a CSS colour FUNCTION (rgb/rgba/hsl/hsla/hwb/lab/lch/oklab/oklch/color()), or a
 * CSS named colour. A design-system token — var(--x), calc(), a `$sigil`, a
 * dotted `theme.text`, or a bare word like 'primary'/'neutral-300' not in the
 * named set — returns false. Non-strings return false. Exported for the plugin's
 * theme-aware layout repair. Conservative: any recognised colour FORM is trusted
 * (so a legitimate percentage rgb() or uncommon-but-valid form is never dropped).
 */
export function isValidColorToken(v: any): boolean {
  if (typeof v !== 'string') return false;
  const s = v.trim().toLowerCase();
  if (!s) return false;
  if (s[0] === '#') return /^#([0-9a-f]{3,4}|[0-9a-f]{6}|[0-9a-f]{8})$/.test(s);
  if (/^(rgb|rgba|hsl|hsla|hwb|lab|lch|oklab|oklch|color)\(/.test(s)) return true;
  return CSS_NAMED_COLORS.has(s);
}

/** Coerce a plotly title from the deprecated string shorthand to the object
 *  form `{ text }`, folding a v1 `titlefont` sibling into `title.font`.
 *  `container` is the object OWNING a `title` (layout, an axis, polar.radialaxis…).
 *  Returns a new container only when a change was needed. */
function coerceTitleOnContainer(container: any): any {
  if (!container || typeof container !== 'object' || Array.isArray(container)) return container;
  const hasStringTitle = typeof container.title === 'string';
  const hasLegacyTitlefont = container.titlefont && typeof container.titlefont === 'object';
  if (!hasStringTitle && !hasLegacyTitlefont) return container;
  const out: any = { ...container };
  const title = hasStringTitle ? { text: container.title } : { ...(container.title || {}) };
  // Fold the removed v1 `titlefont` key into title.font (only if unset).
  if (hasLegacyTitlefont) {
    if (title.font === undefined) title.font = container.titlefont;
    delete out.titlefont;
  }
  out.title = title;
  return out;
}

/**
 * Coerce every string-shorthand title in a layout to the object form (D-231a).
 * plotly.js v2 dropped the string shorthand for layout.title AND axis titles, so
 * a plain-string title renders NOTHING with no error. Covers layout.title, every
 * cartesian axis (xaxis/yaxis/xaxis2…), scene axes, and polar axes. Also folds a
 * v1 `titlefont` into `title.font`. Returns input by reference on a no-op.
 */
export function coerceStringTitles(layout: any): any {
  if (!layout || typeof layout !== 'object') return layout;
  let out: any = layout;
  const clone = () => { if (out === layout) out = { ...layout }; };

  const c0 = coerceTitleOnContainer(layout);
  if (c0 !== layout) { clone(); out.title = c0.title; if (!('titlefont' in c0)) delete out.titlefont; }

  for (const key of Object.keys(layout)) {
    const v = layout[key];
    if (!v || typeof v !== 'object' || Array.isArray(v)) continue;
    if (/^[xy]axis\d*$/.test(key)) {
      const cv = coerceTitleOnContainer(v);
      if (cv !== v) { clone(); out[key] = cv; }
    } else if (key === 'polar' || key === 'ternary' || key.startsWith('scene')) {
      // Nested axis containers (radialaxis/angularaxis/xaxis/yaxis/zaxis/aaxis…).
      let sub: any = v; let subChanged = false;
      for (const ak of Object.keys(v)) {
        const av = v[ak];
        if (av && typeof av === 'object' && !Array.isArray(av) && /axis$/i.test(ak)) {
          const cav = coerceTitleOnContainer(av);
          if (cav !== av) { if (sub === v) sub = { ...v }; sub[ak] = cav; subChanged = true; }
        }
      }
      if (subChanged) { clone(); out[key] = sub; }
    }
  }
  return out;
}

/** Layout-only keys models commonly emit at the spec ROOT instead of inside
 *  `layout`. Promoted into `spec.layout` (only when not already set there). A
 *  conservative allowlist so reserved top-level keys (data/config/frames/type/
 *  definition/streaming flags) are never touched. */
export const PLOTLY_LAYOUT_ROOT_KEYS = new Set([
  'title', 'showlegend', 'legend', 'margin', 'annotations', 'shapes', 'images',
  'barmode', 'bargap', 'bargroupgap', 'boxmode', 'violinmode', 'hovermode',
  'grid', 'coloraxis', 'polar', 'scene', 'ternary', 'geo', 'mapbox',
  'paper_bgcolor', 'plot_bgcolor', 'font', 'colorway',
  'xaxis', 'yaxis', 'xaxis2', 'yaxis2', 'xaxis3', 'yaxis3', 'xaxis4', 'yaxis4',
]);

/**
 * Promote known layout keys sitting at the spec ROOT into `spec.layout` (D-231b).
 * A model that writes `{ type:'plotly', data:[…], title:'…', xaxis:{…} }` loses
 * the title/axes entirely because plotly only reads them from `layout`. Only
 * allowlisted keys are moved, and only when `layout` does not already define
 * them (an explicit layout value wins). Returns input by reference on a no-op.
 */
export function promoteRootLayoutKeys(spec: PlotlySpec): PlotlySpec {
  if (!spec || typeof spec !== 'object') return spec;
  const present = Object.keys(spec).filter(k => PLOTLY_LAYOUT_ROOT_KEYS.has(k));
  if (present.length === 0) return spec;
  const layout = { ...(spec.layout || {}) };
  const out: any = { ...spec };
  let moved = false;
  for (const k of present) {
    if (!(k in layout)) { layout[k] = spec[k]; moved = true; }
    delete out[k];
  }
  if (!moved && present.every(k => k in (spec.layout || {}))) {
    // keys existed at root but layout already had them: still strip the dead
    // root copies so they don't confuse downstream / isPlotlySpec detection.
    for (const k of present) delete out[k];
    out.layout = spec.layout;
    return out;
  }
  out.layout = layout;
  return out;
}

/** Marker-owned keys models commonly emit at the TRACE root. `color`/`size` are
 *  NOT valid top-level trace attributes for the common trace types, so they are
 *  discarded by plotly; demote them into `marker` (only when marker does not
 *  already set them). `opacity` is intentionally EXCLUDED — it IS a valid
 *  trace-level attribute, so moving it would change semantics. */
export function demoteTraceLevelMarkerKeys(data: any[]): any[] {
  if (!Array.isArray(data)) return data;
  let anyChanged = false;
  const out = data.map(trace => {
    if (!trace || typeof trace !== 'object') return trace;
    const hasColor = 'color' in trace && (typeof trace.color === 'string' || Array.isArray(trace.color));
    const hasSize = 'size' in trace && (typeof trace.size === 'number' || Array.isArray(trace.size));
    // v1 `bardir` -> `orientation` (bar traces); mechanical, values match ('h'/'v').
    const hasBardir = trace.type === 'bar' && typeof trace.bardir === 'string' && trace.orientation === undefined;
    if (!hasColor && !hasSize && !hasBardir) return trace;
    const marker = (trace.marker && typeof trace.marker === 'object' && !Array.isArray(trace.marker))
      ? { ...trace.marker } : {};
    const nt: any = { ...trace };
    if (hasColor && marker.color === undefined) { marker.color = trace.color; }
    if (hasColor) delete nt.color;
    if (hasSize && marker.size === undefined) { marker.size = trace.size; }
    if (hasSize) delete nt.size;
    if (hasBardir) { nt.orientation = trace.bardir; delete nt.bardir; }
    nt.marker = marker;
    anyChanged = true;
    return nt;
  });
  return anyChanged ? out : data;
}

/** Recursively rewrite a four-argument `rgb(r,g,b,a)` (a common slip for rgba)
 *  to `rgba(r,g,b,a)` in every string value (D-234). The pattern only matches
 *  the malformed four-arg form, so rewriting anywhere in the spec is safe (a
 *  well-formed three-arg rgb() never matches). Returns input by reference on a
 *  no-op. */
const RGB_FOUR_ARG_RE = /\brgb\(\s*([\d.]+%?)\s*,\s*([\d.]+%?)\s*,\s*([\d.]+%?)\s*,\s*([\d.]+%?)\s*\)/gi;
function rewriteRgbAlpha(s: string): string {
  RGB_FOUR_ARG_RE.lastIndex = 0;
  if (!RGB_FOUR_ARG_RE.test(s)) return s;
  return s.replace(RGB_FOUR_ARG_RE, 'rgba($1,$2,$3,$4)');
}
export function normalizeColorFunctionAlpha<T>(node: T): T {
  if (typeof node === 'string') return rewriteRgbAlpha(node) as any;
  if (Array.isArray(node)) {
    let changed = false;
    const o = node.map(x => { const n = normalizeColorFunctionAlpha(x); if (n !== x) changed = true; return n; });
    return (changed ? o : node) as any;
  }
  if (node && typeof node === 'object') {
    let out: any = node;
    for (const k of Object.keys(node as any)) {
      const v = (node as any)[k];
      const n = normalizeColorFunctionAlpha(v);
      if (n !== v) { if (out === node) out = { ...(node as any) }; out[k] = n; }
    }
    return out;
  }
  return node;
}

/** Colour keys whose scalar-string value, when an invalid token, is stripped
 *  from a TRACE so plotly assigns a legible palette/series colour instead of
 *  the token silently degrading to the library default (D-232). Arrays are left
 *  untouched (they may be data-mapped values driving a colorscale). */
const TRACE_COLOR_KEYS = new Set([
  'color', 'bgcolor', 'bordercolor', 'gridcolor', 'linecolor', 'zerolinecolor',
  'fillcolor', 'tickcolor', 'outlinecolor',
]);

function stripInvalidColorsDeep(node: any): any {
  if (Array.isArray(node)) {
    let changed = false;
    const o = node.map(x => { const n = stripInvalidColorsDeep(x); if (n !== x) changed = true; return n; });
    return changed ? o : node;
  }
  if (!node || typeof node !== 'object') return node;
  let out: any = node;
  const clone = () => { if (out === node) out = { ...node }; };
  for (const k of Object.keys(node)) {
    const v = node[k];
    if (TRACE_COLOR_KEYS.has(k) && typeof v === 'string' && !isValidColorToken(v)) {
      clone(); delete out[k];
      continue;
    }
    const nv = stripInvalidColorsDeep(v);
    if (nv !== v) { clone(); out[k] = nv; }
  }
  return out;
}

/** Strip invalid colour tokens from every trace (D-232, trace side). */
export function stripInvalidTraceColors(data: any[]): any[] {
  if (!Array.isArray(data)) return data;
  let anyChanged = false;
  const out = data.map(t => { const n = stripInvalidColorsDeep(t); if (n !== t) anyChanged = true; return n; });
  return anyChanged ? out : data;
}

/* ======================================================================== *
 * G-52 / plotly data-shape recovery + legibility floors.
 * ======================================================================== */

/** Cartesian XY trace types for which an omitted `x` means "reuse the shared
 *  category/index positions". An explicit `undefined` type defaults to
 *  `scatter` in plotly, so it is treated as cartesian too. */
const XY_CARTESIAN_TRACE_TYPES = new Set([
  'scatter', 'scattergl', 'bar', 'line', 'box', 'violin', 'waterfall', 'funnel',
]);

/**
 * Back-fill an omitted `x` on a later trace from a sibling that supplied one
 * (D-235).
 *
 * A model routinely emits two comparable series but drops `x` on the second:
 * `[{x:['a','b','c','d'], y:[…]}, {y:[…]}]`. Plotly then gives the second trace
 * implicit indices 0,1,2,3 — and because the FIRST trace made the axis
 * CATEGORICAL (string x), those indices are APPENDED as four brand-new
 * categories after a,b,c,d, so two series meant to overlay the same four
 * categories are drawn in disjoint halves of the plot. The chart is legible and
 * completely misleading, identically in both themes.
 *
 * Fix: when a cartesian trace omits `x`, has a `y` array, and a sibling on the
 * SAME x-axis supplies an `x` array of matching length, copy that `x` so the
 * series align over the shared categories. Conservative: fires only on the
 * length match and same-axis check; a trace that already has `x`, a non-cartesian
 * trace (pie/sunburst/sankey/…), or a length mismatch is left UNCHANGED, and the
 * input is returned by reference on a no-op. Exported for unit testing.
 */
export function backfillMissingTraceX(data: any[]): any[] {
  if (!Array.isArray(data) || data.length < 2) return data;
  // Reference trace: the first carrying a non-empty x array.
  const ref = data.find(
    t => t && typeof t === 'object' && Array.isArray(t.x) && t.x.length > 0,
  );
  if (!ref) return data;
  const refAxis = ref.xaxis || 'x';
  let anyChanged = false;
  const out = data.map(trace => {
    if (!trace || typeof trace !== 'object' || trace === ref) return trace;
    // Already has an x -> leave it.
    if (trace.x !== undefined && trace.x !== null) return trace;
    // Only cartesian XY traces (undefined type defaults to scatter).
    if (trace.type !== undefined && !XY_CARTESIAN_TRACE_TYPES.has(trace.type)) return trace;
    if (!Array.isArray(trace.y) || trace.y.length === 0) return trace;
    if (trace.y.length !== ref.x.length) return trace;
    // Only reuse x within the same x-axis assignment.
    if ((trace.xaxis || 'x') !== refAxis) return trace;
    anyChanged = true;
    return { ...trace, x: ref.x };
  });
  return anyChanged ? out : data;
}

/** Trace types whose x/y arrays are categorical LABELS (row/column identities),
 *  where a string axis should be a plain category axis rather than a guessed
 *  date/linear axis. */
const CATEGORY_AXIS_TRACE_TYPES = new Set([
  'heatmap', 'heatmapgl', 'contour', 'bar',
]);

function isNumericLike(v: any): boolean {
  if (typeof v === 'number') return Number.isFinite(v);
  if (typeof v === 'string') {
    const t = v.trim();
    if (t === '') return false;
    return !Number.isNaN(Number(t));
  }
  return false;
}

/** A string plotly would (correctly) parse as a real calendar date the author
 *  intended: it carries a 4-digit year run. The date-FRAGMENT bug this pass
 *  targets ("00-06","06-12","12-18") has only 2-digit groups, so we coerce those
 *  to categories but leave anything with a year alone. */
function looksLikeFullDate(v: any): boolean {
  return typeof v === 'string' && /\d{4}/.test(v);
}

/** True when every element of `arr` is a non-numeric, non-full-date string. */
function allCategoricalStrings(arr: any): boolean {
  if (!Array.isArray(arr) || arr.length === 0) return false;
  for (const v of arr) {
    if (typeof v !== 'string') return false;
    if (isNumericLike(v)) return false;
    if (looksLikeFullDate(v)) return false;
  }
  return true;
}

/** Map a trace axis reference ('x','x2','y3',…) to its layout key ('xaxis',
 *  'xaxis2','yaxis3',…). */
function axisLayoutKey(letter: 'x' | 'y', traceAxis: any): string {
  const base = letter === 'x' ? 'xaxis' : 'yaxis';
  if (typeof traceAxis === 'string') {
    const m = /^[xy](\d+)$/.exec(traceAxis);
    if (m) return base + m[1];
  }
  return base;
}

/**
 * Force `type:'category'` on a heatmap/bar/contour axis whose label array is all
 * non-numeric strings, so plotly does not date-coerce category fragments
 * (D-242).
 *
 * Plotly date-coerces strings that look like date fragments: a heatmap y of
 * `['00-06','06-12','12-18','18-24']` becomes YEARS 1998-2003, the real row
 * labels are silently lost, and the tick count no longer matches the cell rows.
 * Setting the axis type to `'category'` is the plotly-sanctioned cure and is a
 * NO-OP for label arrays plotly already treats as categories — it only changes
 * output in the misparse case. Guarded three ways to honour "do not alter
 * unrelated output": (1) only heatmap/bar/contour traces, (2) only when the axis
 * array is entirely non-numeric strings with NO 4-digit-year value (so a genuine
 * ISO/`YYYY-…` date axis the author wanted is never overridden), and (3) only
 * when the author did not already set that axis's `type`. Returns input by
 * reference on a no-op. Exported for unit testing.
 */
export function coerceCategoricalStringAxes(spec: PlotlySpec): PlotlySpec {
  if (!spec || typeof spec !== 'object' || !Array.isArray(spec.data)) return spec;
  const layout = (spec.layout && typeof spec.layout === 'object') ? spec.layout : {};
  const needed = new Set<string>();
  for (const trace of spec.data) {
    if (!trace || typeof trace !== 'object') continue;
    if (!CATEGORY_AXIS_TRACE_TYPES.has(trace.type)) continue;
    for (const letter of ['x', 'y'] as const) {
      if (!allCategoricalStrings(trace[letter])) continue;
      const key = axisLayoutKey(letter, trace[letter + 'axis']);
      const axis = layout[key];
      if (axis && typeof axis === 'object' && axis.type !== undefined) continue; // author chose
      needed.add(key);
    }
  }
  if (needed.size === 0) return spec;
  const newLayout: any = { ...layout };
  for (const key of needed) {
    const axis = (newLayout[key] && typeof newLayout[key] === 'object') ? { ...newLayout[key] } : {};
    axis.type = 'category';
    newLayout[key] = axis;
  }
  return { ...spec, layout: newLayout };
}

/** Trace types that draw text INSIDE shapes and auto-shrink it with no floor. */
const INSHAPE_TEXT_TRACE_TYPES = new Set([
  'pie', 'sunburst', 'treemap', 'icicle', 'funnelarea',
]);
export const PLOTLY_UNIFORM_TEXT_MINSIZE = 6;

/**
 * Set a minimum in-shape-text size with drop-when-too-small on pie / sunburst /
 * treemap / icicle / funnelarea (D-239).
 *
 * Plotly auto-shrinks in-shape text on these traces with NO minimum font size
 * and no drop rule, so at density the labels become a sub-pixel smudge — silent
 * illegibility rather than an error, identically in both themes (the picker is
 * per-element and theme-independent). `layout.uniformtext = { minsize, mode:
 * 'hide' }` is plotly's own lever for exactly this: text that cannot be shown at
 * >= `minsize` is HIDDEN (recoverable on hover) instead of shrunk into an
 * unreadable smear. It is a layout policy, not a dimension, so it is unaffected
 * by the headless-capture viewport clamp.
 *
 * Conservative: fires only when at least one in-shape-text trace is present AND
 * the author did not already set `layout.uniformtext`. Sparse charts whose text
 * all fits at >= `minsize` are byte-identical (nothing is hidden); only the
 * dense, already-illegible case changes (smear -> legible + dropped tiny
 * labels). Returns input by reference on a no-op. Exported for unit testing.
 */
export function enforceInShapeTextFloor(spec: PlotlySpec): PlotlySpec {
  if (!spec || typeof spec !== 'object' || !Array.isArray(spec.data)) return spec;
  const layout = (spec.layout && typeof spec.layout === 'object') ? spec.layout : undefined;
  if (layout && layout.uniformtext !== undefined) return spec; // author chose
  const hasInShape = spec.data.some(
    t => t && typeof t === 'object' && INSHAPE_TEXT_TRACE_TYPES.has(t.type),
  );
  if (!hasInShape) return spec;
  const newLayout = {
    ...(layout || {}),
    uniformtext: { minsize: PLOTLY_UNIFORM_TEXT_MINSIZE, mode: 'hide' },
  };
  return { ...spec, layout: newLayout };
}

/** Compose all preprocessors. Order matters: title fix first so subsequent
 *  passes see the adjusted title state. */
export function preprocessPlotlySpec(spec: PlotlySpec): PlotlySpec {
  if (!spec || typeof spec !== 'object') return spec;
  // D-234: fold four-arg rgb()->rgba() everywhere BEFORE any colour validation.
  spec = normalizeColorFunctionAlpha(spec);
  // D-231b: promote layout keys emitted at the spec root into `layout` so the
  // title/axis coercion below sees them.
  spec = promoteRootLayoutKeys(spec);
  let layout = spec.layout;
  // D-231a: string-shorthand titles -> {text} (plotly v2 drops the string form).
  layout = coerceStringTitles(layout);
  layout = fixMultilineTitle(layout);
  layout = adjustSceneDomainsForTitle(layout);
  layout = ensureSceneDomainGaps(layout);
  layout = adjustAnnotationsForTitle(layout);
  layout = sanitizeLayoutGeometry(layout);
  layout = enableAxisAutomargin(layout);
  layout = clampDimensionsToViewportForCapture(layout);
  let data = clampColorbars(spec.data || []);
  data = demoteWebglTracesForCapture(data);
  data = disambiguateHierarchyLabels(data);
  data = clampHistogramBins(data);
  // D-231b: demote marker-owned keys (color/size) emitted at the trace root.
  data = demoteTraceLevelMarkerKeys(data);
  // D-232 (trace side): strip invalid colour tokens -> plotly palette default.
  data = stripInvalidTraceColors(data);
  // D-235: back-fill an omitted x on a later trace from a sibling so two series
  // align over the shared categories instead of appearing in disjoint halves.
  data = backfillMissingTraceX(data);
  // Final magnitude pass over the WHOLE spec: clamp every font.size and any
  // astronomical marker.size that would block the render thread (Issue 48).
  let composed = clampExtremeSizes({ ...spec, data, layout });
  // D-242: category string axes on heatmap/bar/contour so plotly does not
  // date-coerce label fragments ("00-06" -> year 1998).
  composed = coerceCategoricalStringAxes(composed);
  // D-239: min in-shape-text floor so pie/sunburst/treemap labels are dropped
  // when too small rather than shrunk into an illegible smear.
  composed = enforceInShapeTextFloor(composed);
  return composed;
}

/* ======================================================================== *
 * G-71 / plotly colorscale-vs-surface guard + legend-overflow sizing.
 *
 * Two theme-independent-at-source defects that both surface differently per
 * theme because the effective SURFACE differs per theme:
 *
 *  D-233  a colorscale endpoint equal to the surface behind the cells renders
 *         an invisible "hole" in the grid (LIGHT: a z-min #fff cell on the
 *         #ffffff plot area = 1.00:1). The engine had no guard that a colorscale
 *         endpoint differs from the surface. Fixed here by nudging ONLY an
 *         endpoint that collides with the (theme-resolved) surface toward the
 *         surface-opposite until the cell is a visible distinct shape. The
 *         theme-resolved surface is supplied by the caller (the plugin, which
 *         knows isDarkMode), so a #fff endpoint is nudged on a light plot area
 *         but left untouched on a dark one — the same endpoint, resolved
 *         per-theme, never a blind constant swap. (The DARK half of D-233 — a
 *         dark global font on an author's surviving light paper — is fixed in
 *         plotlyPlugin.applyPlotlyTheme, which owns theme surface resolution.)
 *
 *  D-241  a >26-entry legend is clipped behind a non-existent scrollbar in the
 *         static 60vh capture div, and the default 10-colour colorway recycles
 *         so unlabelled series share a colour with labelled ones. Fixed here by
 *         (a) estimating legend rows and growing the render div height, and (b)
 *         an extended colorway applied ONLY when the series count exceeds the
 *         default palette length (so <=10-series figures are byte-identical).
 *
 * Every pass is conservative and returns input BY REFERENCE on a no-op.
 * ======================================================================== */

/** Resolve a colour token to #hex (hex literal or CSS named colour), else null. */
function plotlyResolveHex(c: any): string | null {
  const cl = classifyColor(c);
  if (!cl) return null;
  if (cl.hex) return cl.hex;
  if (cl.named) return namedColorToHex(cl.named);
  return null;
}

// A colorscale endpoint whose contrast with the surface is below the collision
// threshold is a "hole" (the cell blends into the background behind it); it is
// nudged until it clears the min-contrast floor so the cell reads as a visibly
// distinct shape. 1.5:1 is a large-area (whole-cell) legibility floor, not the
// WCAG text floor — the goal is "the cell is present", not "read text on it".
export const PLOTLY_COLORSCALE_COLLISION_CONTRAST = 1.1;
export const PLOTLY_COLORSCALE_MIN_CONTRAST = 1.5;

/** Nudge any endpoint of one array colorscale that collides with `surfaceHex`.
 *  Named colorscales (string like 'Viridis') are left untouched. */
function guardOneColorscale(scale: any, surfaceHex: string): any {
  if (!Array.isArray(scale)) return scale;
  let changed = false;
  const out = scale.map((stop: any) => {
    if (!Array.isArray(stop) || stop.length < 2 || typeof stop[1] !== 'string') return stop;
    const hex = plotlyResolveHex(stop[1]);
    if (!hex) return stop;
    if (contrastRatio(hex, surfaceHex) >= PLOTLY_COLORSCALE_COLLISION_CONTRAST) return stop;
    const fallback = isDarkBackground(surfaceHex) ? '#5a5a5a' : '#c0c0c0';
    const nudged = ensureReadableFill(hex, surfaceHex, fallback, PLOTLY_COLORSCALE_MIN_CONTRAST);
    if (nudged === hex) return stop;
    changed = true;
    return [stop[0], nudged];
  });
  return changed ? out : scale;
}

/**
 * Guard every trace's array colorscale so no endpoint is invisible against the
 * theme-resolved plot surface (D-233). `surface` is the effective plot/paper
 * background the caller resolved for the active theme. Returns input by
 * reference on a no-op. Exported for unit testing.
 */
export function guardColorscaleAgainstSurface(data: any[], surface: any): any[] {
  if (!Array.isArray(data)) return data;
  const surfaceHex = plotlyResolveHex(surface);
  if (!surfaceHex) return data;
  let anyChanged = false;
  const out = data.map((trace: any) => {
    if (!trace || typeof trace !== 'object') return trace;
    let nt: any = trace;
    if (Array.isArray(trace.colorscale)) {
      const ns = guardOneColorscale(trace.colorscale, surfaceHex);
      if (ns !== trace.colorscale) nt = { ...nt, colorscale: ns };
    }
    if (trace.marker && typeof trace.marker === 'object' && Array.isArray(trace.marker.colorscale)) {
      const ns = guardOneColorscale(trace.marker.colorscale, surfaceHex);
      if (ns !== trace.marker.colorscale) nt = { ...nt, marker: { ...nt.marker, colorscale: ns } };
    }
    if (nt !== trace) anyChanged = true;
    return nt;
  });
  return anyChanged ? out : data;
}

/** Trace types that do NOT draw a legend entry (they use a colorbar or are 3D
 *  surfaces), so they must not inflate the legend-row estimate. */
const PLOTLY_NON_LEGEND_TRACE_TYPES = new Set([
  'heatmap', 'heatmapgl', 'contour', 'surface', 'mesh3d', 'volume', 'isosurface',
  'histogram2d', 'histogram2dcontour', 'choropleth', 'choroplethmapbox',
  'densitymapbox', 'table', 'image', 'cone', 'streamtube',
]);

/**
 * Estimate how many vertical legend rows a spec will draw (D-241). Counts
 * legend-bearing traces with `showlegend !== false`; returns 0 when the layout
 * disables the legend. Exported for unit testing.
 */
export function estimateLegendEntries(data: any[], layout: any): number {
  if (!Array.isArray(data)) return 0;
  if (layout && typeof layout === 'object' && layout.showlegend === false) return 0;
  let n = 0;
  for (const t of data) {
    if (!t || typeof t !== 'object') continue;
    if (t.showlegend === false) continue;
    if (typeof t.type === 'string' && PLOTLY_NON_LEGEND_TRACE_TYPES.has(t.type)) continue;
    n++;
  }
  return n;
}

// A vertical legend clips past ~26 entries in the default 60vh capture div.
export const PLOTLY_LEGEND_CLIP_ENTRIES = 26;
export const PLOTLY_LEGEND_ROW_PX = 20;
export const PLOTLY_LEGEND_MAX_HEIGHT_PX = 2400;
export const PLOTLY_LEGEND_MIN_GROWN_HEIGHT_PX = 480;

/**
 * When a spec has more legend entries than fit in the default capture div,
 * return a taller pixel height so the whole legend is captured; else `null`
 * (the caller keeps the default 60vh, so ordinary figures are unchanged).
 * Exported for unit testing.
 */
export function legendAwareRenderHeightPx(entryCount: number): number | null {
  if (!(entryCount > PLOTLY_LEGEND_CLIP_ENTRIES)) return null;
  const needed = entryCount * PLOTLY_LEGEND_ROW_PX + 140;
  return Math.min(Math.max(needed, PLOTLY_LEGEND_MIN_GROWN_HEIGHT_PX), PLOTLY_LEGEND_MAX_HEIGHT_PX);
}

// Series beyond this count recycle plotly's default 10-colour palette (D-241).
export const PLOTLY_COLORWAY_RECYCLE_THRESHOLD = 10;

/**
 * Extended, distinct colorway so figures with >10 series stop recycling the
 * default 10-colour palette. The FIRST 10 entries are plotly's own default
 * palette, so the traces that already rendered fine keep their exact colours;
 * 14 further distinct hues follow for series 11+. Applied by the plugin ONLY
 * when the legend-bearing trace count exceeds the palette length AND no
 * author/theme colorway is set, so <=10-series figures are byte-identical.
 */
export const PLOTLY_EXTENDED_COLORWAY = [
  // plotly default 10 (unchanged, so series 1..10 look exactly as before)
  '#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd',
  '#8c564b', '#e377c2', '#7f7f7f', '#bcbd22', '#17becf',
  // 14 further distinct hues (D3 category20b/c saturated tones) for series 11+
  '#393b79', '#637939', '#8c6d31', '#843c39', '#7b4173',
  '#3182bd', '#e6550d', '#31a354', '#756bb1', '#636363',
  '#ad494a', '#8ca252', '#bd9e39', '#7b6888',
];
