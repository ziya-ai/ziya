/**
 * Width-aware legend placement for Vega-Lite specs with a FIXED pixel width.
 *
 * A composite spec (vconcat / hconcat / concat) has to carry a concrete
 * per-view width, and applySizing keeps the authored one. Vega then adds the
 * y-axis and the legend OUTSIDE that width, and a symbol legend defaults to
 * the right-hand side. So a 620px view with a three-column legend becomes a
 * ~920px SVG. In the chat pane that is fine; in a ~650px PDF text column the
 * export pipeline scales the whole figure down to fit (fitOversizedFigures),
 * shrinking the plot and every label to make room for a legend that would fit
 * for free BELOW the plot, and leaving an empty band beside the legend.
 *
 * optimizeLegendLayout (vegaLitePlugin) does not visit concat sub-views and
 * decides on entry COUNT, not width. This pass is purely width-driven: when
 * the estimated chart + axis + side legend exceeds the pane, and the author
 * did not choose a legend orientation, the legend moves to the bottom with a
 * column count fitted to the plot width. It is pure and side-effect-free
 * apart from mutating the spec it is handed, so it is unit-testable without
 * vegaEmbed.
 */
import {
  estimateTextWidthPx,
  VEGA_DEFAULT_LEGEND_LABEL_LIMIT,
  VEGA_LEGEND_LABEL_FONT_PX,
} from './vegaTextFit';
import { positiveNumber } from './vegaFacetLayout';

/** Encoding channels that produce a legend. */
export const LEGEND_CHANNELS = ['color', 'fill', 'stroke', 'shape', 'size', 'opacity'];
/** Y-axis tick labels + ticks + title, drawn outside the view width. */
export const Y_AXIS_ROOM_PX = 70;
/** Symbol, symbol-to-label gap and column padding per legend entry. */
export const LEGEND_ENTRY_CHROME_PX = 24;
/** Legend offset from the plot plus its own padding. */
export const LEGEND_BLOCK_PADDING_PX = 18;
/** Column count assumed when the entries cannot be enumerated. */
export const LEGEND_FALLBACK_COLUMNS = 3;
/** A 'container' chart (autosize fit-x) gives up its legend's width from the
 *  pane itself. Past this fraction the plot is starved, so the legend moves
 *  below (a 190px legend is 16% of a 1200px chat pane, 29% of a 650px page). */
export const CONTAINER_SIDE_LEGEND_MAX_FRACTION = 0.25;

export interface LegendSite {
  /** The encoding object that owns the channel (mutable). */
  encoding: any;
  channel: string;
  /** Distinct label strings, from data values and/or an authored scale.domain. */
  labels: string[];
}

export interface LegendRelocation {
  orient: 'bottom';
  columns: number;
  /** Fitted labelLimit written to config.legend, or null when one was authored. */
  labelLimit: number | null;
  chartWidthPx: number;
  paneWidthPx: number;
  estimatedSideLegendPx: number;
}

const isObj = (v: any): v is Record<string, any> => !!v && typeof v === 'object' && !Array.isArray(v);

function labelsFor(enc: any, dataObj: any): string[] {
  const field = enc?.field;
  const out = new Set<string>();
  const rows = Array.isArray(dataObj?.values) ? dataObj.values : [];
  if (field) {
    for (const r of rows) {
      const v = r?.[field];
      if (v !== null && v !== undefined) out.add(String(v));
    }
  }
  const domain = enc?.scale?.domain;
  if (Array.isArray(domain)) {
    for (const v of domain) {
      if (typeof v === 'string' || typeof v === 'number') out.add(String(v));
    }
  }
  return Array.from(out);
}

/**
 * Every legend-bearing channel in the spec tree (layer / vconcat / hconcat /
 * concat / facet-repeat `spec`), with the data nearest to it. Channels whose
 * legend is hidden (`legend: null|false`) or whose scale is the identity
 * (`scale: null`) produce no legend and are skipped.
 */
export function collectLegendSites(spec: any, inheritedData: any = undefined): LegendSite[] {
  if (!isObj(spec)) return [];
  const data = spec.data ?? inheritedData;
  const sites: LegendSite[] = [];
  if (isObj(spec.encoding)) {
    for (const channel of LEGEND_CHANNELS) {
      const enc = spec.encoding[channel];
      if (!isObj(enc) || !enc.field) continue;
      if (enc.legend === null || enc.legend === false) continue;
      if (enc.scale === null) continue;
      sites.push({ encoding: spec.encoding, channel, labels: labelsFor(enc, data) });
    }
  }
  for (const key of ['layer', 'vconcat', 'hconcat', 'concat']) {
    if (Array.isArray(spec[key])) {
      for (const child of spec[key]) sites.push(...collectLegendSites(child, data));
    }
  }
  if (isObj(spec.spec)) sites.push(...collectLegendSites(spec.spec, data));
  return sites;
}

/**
 * Width the assembled views will occupy, in px, from authored numeric widths:
 * hconcat children add up, everything else takes the widest child. Falls
 * back to `resolvedWidth` (what applySizing chose) when the tree carries no
 * numeric width. Null when neither is a number — a 'container' chart already
 * subtracts its legend under autosize fit-x and is not this module's concern.
 *
 * A composite node's own width is only the DEFAULT for children that lack
 * one, so it never short-circuits the walk: the reported spec had a
 * top-level 400 beside vconcat children authored at 620.
 */
export function estimateChartWidthPx(spec: any, resolvedWidth: number | string | undefined): number | null {
  const walk = (node: any): number | null => {
    if (!isObj(node)) return null;
    const own = positiveNumber(node.width);
    const maxWithOwn = (v: number | null): number | null =>
      own === null ? v : (v === null ? own : Math.max(own, v));
    if (Array.isArray(node.hconcat)) {
      // Side by side: children add up, each falling back to the node default.
      const parts = node.hconcat.map((c: any) => walk(c) ?? own).filter((w: number | null): w is number => w !== null);
      return parts.length ? parts.reduce((a: number, b: number) => a + b, 0) : own;
    }
    for (const key of ['vconcat', 'concat', 'layer']) {
      if (Array.isArray(node[key])) {
        const parts = node[key].map(walk).filter((w): w is number => w !== null);
        if (parts.length) return maxWithOwn(Math.max(...parts));
      }
    }
    if (isObj(node.spec)) return maxWithOwn(walk(node.spec));
    return own;
  };
  const fromTree = walk(spec);
  const fromResolved = typeof resolvedWidth === 'number' ? positiveNumber(resolvedWidth) : null;
  if (fromTree !== null && fromResolved !== null) return Math.max(fromTree, fromResolved);
  return fromTree ?? fromResolved;
}

function authoredOrient(spec: any, sites: LegendSite[]): string | undefined {
  const cfg = spec?.config?.legend;
  if (isObj(cfg) && typeof cfg.orient === 'string') return cfg.orient;
  for (const s of sites) {
    const lg = s.encoding[s.channel]?.legend;
    if (isObj(lg) && typeof lg.orient === 'string') return lg.orient;
  }
  return undefined;
}

function labelLimitPx(spec: any, site: LegendSite): number {
  const lg = site.encoding[site.channel]?.legend;
  if (isObj(lg) && typeof lg.labelLimit === 'number' && lg.labelLimit > 0) return lg.labelLimit;
  const cfg = spec?.config?.legend;
  if (isObj(cfg) && typeof cfg.labelLimit === 'number' && cfg.labelLimit > 0) return cfg.labelLimit;
  return VEGA_DEFAULT_LEGEND_LABEL_LIMIT;
}

function fontPx(spec: any, site: LegendSite): number {
  const lg = site.encoding[site.channel]?.legend;
  if (isObj(lg) && typeof lg.labelFontSize === 'number') return lg.labelFontSize;
  const cfg = spec?.config?.legend;
  if (isObj(cfg) && typeof cfg.labelFontSize === 'number') return cfg.labelFontSize;
  return VEGA_LEGEND_LABEL_FONT_PX;
}

function sideColumns(spec: any, site: LegendSite): number {
  const lg = site.encoding[site.channel]?.legend;
  if (isObj(lg) && positiveNumber(lg.columns) !== null) return lg.columns;
  const cfg = spec?.config?.legend;
  if (isObj(cfg) && positiveNumber(cfg.columns) !== null) return cfg.columns;
  return 1;
}

/** Widest entry (longest label, capped at its labelLimit, plus chrome) for a site. */
function entryPx(spec: any, site: LegendSite): number {
  const font = fontPx(spec, site);
  const longest = site.labels.reduce((m, l) => Math.max(m, estimateTextWidthPx(l, font)), 0);
  const label = longest > 0 ? Math.min(longest, labelLimitPx(spec, site)) : labelLimitPx(spec, site);
  return label + LEGEND_ENTRY_CHROME_PX;
}

/** Estimated width of the default right-hand legend block for these sites. */
export function estimateSideLegendWidthPx(spec: any, sites: LegendSite[]): number {
  if (sites.length === 0) return 0;
  // Multiple legends stack vertically on the side, so the widest one governs.
  const widest = sites.reduce(
    (m, s) => Math.max(m, sideColumns(spec, s) * entryPx(spec, s)), 0,
  );
  return widest + LEGEND_BLOCK_PADDING_PX;
}

/**
 * Move a default side legend below the plot when chart + y-axis + legend
 * would not fit the pane, or — for a 'container' chart (chartWidthPx null),
 * whose plot is whatever the pane leaves after the legend — when the side
 * legend would take more than CONTAINER_SIDE_LEGEND_MAX_FRACTION of the pane.
 * Returns what was applied, or null when nothing changed: no legend, an
 * authored orient anywhere (author intent wins), or the legend fits.
 */
export function relocateOverflowingSideLegend(
  spec: any,
  chartWidthPx: number | null,
  paneWidthPx: number,
): LegendRelocation | null {
  if (!isObj(spec) || !(paneWidthPx > 0)) return null;
  if (chartWidthPx !== null && !(chartWidthPx > 0)) return null;
  const sites = collectLegendSites(spec);
  if (sites.length === 0) return null;
  if (authoredOrient(spec, sites) !== undefined) return null;

  const estimatedSideLegendPx = estimateSideLegendWidthPx(spec, sites);
  let plotWidthPx: number;
  if (chartWidthPx === null) {
    // Container-sized: the plot is the pane minus axis and legend.
    if (estimatedSideLegendPx <= CONTAINER_SIDE_LEGEND_MAX_FRACTION * paneWidthPx) return null;
    plotWidthPx = Math.max(1, paneWidthPx - Y_AXIS_ROOM_PX);
    chartWidthPx = plotWidthPx;
  } else {
    if (chartWidthPx + Y_AXIS_ROOM_PX + estimatedSideLegendPx <= paneWidthPx) return null;
    plotWidthPx = chartWidthPx;
  }

  // Columns that fit across the plot width. Bounded by the entry count so a
  // short legend is not spread over more columns than it has entries.
  const widestEntry = sites.reduce((m, s) => Math.max(m, entryPx(spec, s)), 0);
  const maxEntries = sites.reduce((m, s) => Math.max(m, s.labels.length), 0);
  const fitCols = Math.max(1, Math.floor(plotWidthPx / Math.max(widestEntry, 1)));
  const columns = maxEntries > 0 ? Math.min(fitCols, maxEntries) : Math.min(fitCols, LEGEND_FALLBACK_COLUMNS);

  const cfg = isObj(spec.config) ? spec.config : {};
  const legendCfg = isObj(cfg.legend) ? cfg.legend : {};
  // Below the plot each column has chartWidth/columns of room, usually far
  // more than Vega's 160px default labelLimit, which would ellipsise long
  // labels for no reason. Fit the limit to the longest label within that
  // room unless the author set one (in config or on a channel).
  const authoredLimit = (typeof legendCfg.labelLimit === 'number') ||
    sites.some(s => { const lg = s.encoding[s.channel]?.legend; return isObj(lg) && typeof lg.labelLimit === 'number'; });
  let labelLimit: number | null = null;
  if (!authoredLimit) {
    const room = Math.floor(plotWidthPx / columns) - LEGEND_ENTRY_CHROME_PX;
    const longest = sites.reduce((m, s) => Math.max(m,
      s.labels.reduce((mm, l) => Math.max(mm, estimateTextWidthPx(l, fontPx(spec, s))), 0)), 0);
    labelLimit = Math.max(1, Math.min(longest + 6, room));
  }
  spec.config = { ...cfg, legend: { ...legendCfg, orient: 'bottom', columns,
    ...(labelLimit !== null ? { labelLimit } : {}) } };
  // An authored per-channel column count was chosen for the side layout and
  // would override the config; it follows the relocation.
  for (const s of sites) {
    const lg = s.encoding[s.channel]?.legend;
    if (isObj(lg) && positiveNumber(lg.columns) !== null) {
      s.encoding[s.channel].legend = { ...lg, columns };
    }
  }
  return { orient: 'bottom', columns, labelLimit, chartWidthPx, paneWidthPx, estimatedSideLegendPx };
}

// ── A top/bottom legend wider than its own hconcat cell ─────────────────────
//
// A horizontal legend is laid out inside its concat cell, and Vega sizes each
// grid column to the full bounds of its cell, guides included. A legend wider
// than its view therefore widens the whole column and pushes every later
// panel right. Measured on the reported spec (14px colour stripe with a
// 3-column bottom legend, then 200px and 260px panels): the stripe's column
// came out 525px wide and the next panel started at x=530 instead of 18.
// Vega-Lite has no per-cell bounds, so the repair is:
//   1. fewer legend columns, when one column fits inside the cell; otherwise
//   2. `bounds: "flush"` on the row (columns sized by view width alone, guides
//      overhang), with the column count fitted to the room before the next
//      cell's same-side legend so the two cannot overprint.
// Flush is applied only when no non-first cell has a left-side guide and no
// non-last cell has a right-side guide; those would overhang a neighbour's
// plot. Otherwise the legend falls back to one column. An authored `bounds`
// is never replaced. Gradient legends (fixed length) are not fitted here.

/** vega-lite config.concat.spacing default. */
export const VL_DEFAULT_CONCAT_SPACING_PX = 20;
/** Tolerance before a legend counts as wider than its cell. */
export const CELL_LEGEND_SLACK_PX = 8;

export interface CellLegendFit {
  cellIndex: number;
  channel: string;
  cellWidthPx: number;
  estimatedLegendPx: number;
  columns: number;
  /** True when the row lays out with bounds "flush" (set here or authored). */
  flushed: boolean;
}

const HORIZONTAL_ORIENTS = new Set(['top', 'bottom']);
const LEFT_ORIENTS = new Set(['left', 'top-left', 'bottom-left']);
const RIGHT_ORIENTS = new Set(['right', 'top-right', 'bottom-right']);
const GRADIENT_CHANNELS = new Set(['color', 'fill', 'stroke', 'opacity']);

function siteOrient(root: any, site: LegendSite): string {
  const lg = site.encoding[site.channel]?.legend;
  if (isObj(lg) && typeof lg.orient === 'string') return lg.orient;
  const cfg = root?.config?.legend;
  if (isObj(cfg) && typeof cfg.orient === 'string') return cfg.orient;
  return 'right';
}

function isGradientSite(site: LegendSite): boolean {
  const t = site.encoding[site.channel]?.type;
  return GRADIENT_CHANNELS.has(site.channel) && (t === 'quantitative' || t === 'temporal');
}

/** Columns a top/bottom symbol legend lays out: all entries in one row
 *  unless columns or a vertical direction say otherwise. */
function horizontalColumns(root: any, site: LegendSite): number {
  const lg = site.encoding[site.channel]?.legend;
  if (isObj(lg) && positiveNumber(lg.columns) !== null) return lg.columns;
  const cfg = root?.config?.legend;
  if (isObj(cfg) && positiveNumber(cfg.columns) !== null) return cfg.columns;
  if ((isObj(lg) && lg.direction === 'vertical') || (isObj(cfg) && cfg.direction === 'vertical')) return 1;
  return Math.max(1, site.labels.length || LEGEND_FALLBACK_COLUMNS);
}

/** Which sides of a cell carry guides drawn outside its view, or null when
 *  the cell is a shape this pass does not reason about (nested composites). */
function cellGuideSides(root: any, cell: any, data: any): { left: boolean; right: boolean } | null {
  if (!isObj(cell)) return null;
  if (['hconcat', 'vconcat', 'concat', 'facet', 'repeat', 'spec'].some(k => k in cell)) return null;
  const layers: any[] = Array.isArray(cell.layer) ? cell.layer : [];
  if (layers.some(l => !isObj(l) || Array.isArray(l.layer))) return null;
  const encs = [cell.encoding, ...layers.map(l => l.encoding)].filter(isObj);
  if (encs.some(e => e.row || e.column || e.facet)) return null;
  let left = false;
  let right = false;
  const ys = encs.map(e => e.y).filter(y => isObj(y) && (y.field || y.aggregate));
  // vega-lite merges a layered axis: axis:null on any layer hides it (observed).
  if (ys.length && !ys.some(y => y.axis === null || y.axis === false)) {
    const orient = ys.map(y => y.axis?.orient).find(o => typeof o === 'string');
    if (orient === 'right') right = true; else left = true;
  }
  for (const s of collectLegendSites(cell, data)) {
    const o = siteOrient(root, s);
    if (LEFT_ORIENTS.has(o)) left = true;
    if (RIGHT_ORIENTS.has(o)) right = true;
  }
  return { left, right };
}

/** Horizontal room from cell i's left edge to the next cell carrying a
 *  same-side legend (or the end of the row). Unknown widths stop the walk. */
function roomBeforeNextLegend(
  root: any, i: number, orient: string, widths: (number | null)[],
  sites: LegendSite[][], spacing: number,
): number {
  let room = widths[i] as number;
  for (let j = i + 1; j < widths.length; j++) {
    if (sites[j].some(s => siteOrient(root, s) === orient)) return room;
    const wj = widths[j];
    if (wj === null) return room;
    room += spacing + wj;
  }
  return room;
}

function fitRow(root: any, row: any, data: any, fits: CellLegendFit[]): void {
  const cells: any[] = row.hconcat;
  const cfgSpacing = root?.config?.concat?.spacing;
  const spacing = typeof row.spacing === 'number' && row.spacing >= 0 ? row.spacing
    : (typeof cfgSpacing === 'number' && cfgSpacing >= 0 ? cfgSpacing : VL_DEFAULT_CONCAT_SPACING_PX);
  const widths = cells.map(c => positiveNumber(c?.width));
  const sites = cells.map(c => collectLegendSites(c, data).filter(s => HORIZONTAL_ORIENTS.has(siteOrient(root, s))));
  let flushSafe: boolean | null = null;
  const canFlush = (): boolean => {
    if (flushSafe === null) {
      flushSafe = cells.every((c, i) => {
        const g = cellGuideSides(root, c, data);
        return g !== null && !(i > 0 && g.left) && !(i < cells.length - 1 && g.right);
      });
    }
    return flushSafe;
  };
  cells.forEach((cell, i) => {
    const w = widths[i];
    if (w === null) return;
    for (const site of sites[i]) {
      if (isGradientSite(site)) continue;
      const entry = entryPx(root, site);
      const cols = horizontalColumns(root, site);
      const estimatedLegendPx = cols * entry + LEGEND_BLOCK_PADDING_PX;
      if (estimatedLegendPx <= w + CELL_LEGEND_SLACK_PX) continue;
      let room = w;
      let boundsChanged = false;
      if (entry + LEGEND_BLOCK_PADDING_PX > w + CELL_LEGEND_SLACK_PX) {
        if (row.bounds === undefined && canFlush()) {
          row.bounds = 'flush';
          boundsChanged = true;
        }
        if (row.bounds === 'flush') {
          room = roomBeforeNextLegend(root, i, siteOrient(root, site), widths, sites, spacing);
        }
      }
      const maxCols = Math.max(1, Math.min(cols, site.labels.length || cols));
      const columns = Math.max(1, Math.min(maxCols, Math.floor((room - LEGEND_BLOCK_PADDING_PX) / entry)));
      if (columns >= cols && !boundsChanged) continue;
      if (columns < cols) {
        const lg = site.encoding[site.channel].legend;
        site.encoding[site.channel].legend = { ...(isObj(lg) ? lg : {}), columns };
      }
      fits.push({
        cellIndex: i, channel: site.channel, cellWidthPx: w, estimatedLegendPx,
        columns: Math.min(columns, cols), flushed: row.bounds === 'flush',
      });
    }
  });
}

/**
 * Stop a top/bottom legend that is wider than its own hconcat cell from
 * widening that cell's grid column. Visits every hconcat row in the tree
 * (including rows nested in vconcat). Mutates the spec; returns one record
 * per legend it changed, empty when nothing needed fitting.
 */
export function fitNarrowConcatCellLegends(spec: any): CellLegendFit[] {
  const fits: CellLegendFit[] = [];
  const visit = (node: any, data: any): void => {
    if (!isObj(node)) return;
    const d = node.data ?? data;
    if (Array.isArray(node.hconcat)) fitRow(spec, node, d, fits);
    for (const key of ['vconcat', 'hconcat', 'concat']) {
      if (Array.isArray(node[key])) node[key].forEach((c: any) => visit(c, d));
    }
    if (isObj(node.spec)) visit(node.spec, d);
  };
  visit(spec, undefined);
  return fits;
}
