/**
 * Measured cell-width fitting for faceted Vega-Lite views.
 *
 * WHY MEASURE RATHER THAN ESTIMATE. ./vegaFacetLayout can only produce an
 * opening guess at a faceted spec's per-cell width, for two reasons that are
 * each independently fatal to an estimate:
 *
 *   1. The horizontal chrome a faceted view adds outside its cells (legend,
 *      facet headers, y-axis labels and titles, view padding) is a LAYOUT
 *      OUTPUT that depends on label text. On vega-lite 6.4 the same
 *      row-faceted spec measured cellWidth + 285px with short legend labels
 *      and cellWidth + 437px with long ones. Nothing in the spec predicts it.
 *   2. D3Renderer calls plugin.render() on a DETACHED container — it creates
 *      tempContainer, renders into it, and appends it only after the render
 *      promise resolves — so getBoundingClientRect().width is 0 there and the
 *      "available width" is really the 400px floor. Arithmetic on it cannot
 *      be correct, and subtracting a ~450px chrome reserve from 400 underflows
 *      to the cell floor, pinning every faceted chart to minimum width.
 *
 * WHAT MAKES MEASURING EXACT. Vega-Lite compiles every faceting spelling
 * (encoding.row, encoding.column, encoding.facet, and the facet operator) to a
 * single `child_width` signal which is settable and relayouts on run. Assembled
 * width is linear in it:
 *
 *     assembled = slope * cellWidth + chrome
 *
 * where slope is the number of horizontally-tiled cells — measured as 1.000 for
 * row faceting, 6.000 for a six-column facet and 10.000 for ten columns, so it
 * never has to be counted from the data (which is impossible for URL-sourced
 * data anyway). Two probe points determine slope and chrome exactly. Solving
 * that model landed assembled width ON the target with 0.0px residual across
 * 13 spec shapes x 4 container widths, needing zero corrective passes.
 *
 * The model degrades only below roughly 50px cells, where a cell can no longer
 * shrink to fit its content; MIN_FACET_CELL_WIDTH keeps that regime unreachable.
 * Past that floor a grid legitimately cannot fit, which is reported rather than
 * hidden so the caller can enable scrolling instead of clipping.
 *
 * The model also degrades where a FIXED-WIDTH element is wider than the cell
 * grid, most often a top-level title. Root-bounds width is then PINNED by that
 * element and stops responding to child_width: two real specs measured slope
 * 0.000 (calibration returns null) and slope 0.758 (a bogus model) from probe
 * points around the 240px detached-container estimate, and the resulting charts
 * were left at the estimate and clipped. Calibration therefore escalates to
 * larger probe pairs until the slope reads as a whole cell count, i.e. until
 * both probes sit above the pinning width.
 */

/** The per-cell width signal every faceted Vega-Lite compilation exposes. */
export const CELL_WIDTH_SIGNAL = 'child_width';

/** Floor for a fitted cell width. Also keeps layout out of the non-linear regime. */
export const MIN_FACET_CELL_WIDTH = 120;

/** Probe points below this measure the non-linear regime and skew the slope. */
export const FACET_PROBE_FLOOR = 40;

/** Offset used when a cell is too small to probe downward from. */
export const FACET_PROBE_DELTA = 80;

/**
 * How far a measured slope may sit from a whole number and still be believed.
 *
 * Slope IS the count of horizontally-tiled cells, so a real measurement is a
 * whole number. A fractional slope means a probe point was taken where
 * assembled width is pinned by a fixed-width element (a title wider than the
 * cells) rather than driven by the cells, so the samples describe the pin.
 */
export const SLOPE_INTEGER_TOLERANCE = 0.05;

/** Escalating probe pairs allowed while searching for the unpinned regime. */
export const MAX_PROBE_ESCALATIONS = 3;

/** Ceiling on a probe cell width, so escalation cannot run away. */
export const MAX_PROBE_CELL_WIDTH = 8000;

/** Verification passes allowed after the solve. Measured need: 0. */
export const MAX_FIT_CORRECTIONS = 2;

/** The linear layout law relating cell width to assembled width. */
export interface CellWidthModel {
  /** Horizontally-tiled cell count. */
  slope: number;
  /** Additive extent outside the cells, in px. */
  chrome: number;
}

export interface CellWidthSample {
  cell: number;
  assembled: number;
}

export interface FacetFitResult {
  cellWidth: number;
  /** True when the grid could not fit and was held at the floor. */
  clamped: boolean;
  assembledWidth: number | null;
  corrections: number;
  /** True when the view is wider than the target — the caller must scroll. */
  overflows: boolean;
}

/**
 * Assembled width of the rendered view, or null if it cannot be measured.
 *
 * Returns null rather than 0 for an unusable scenegraph. A finite-but-
 * meaningless 0 passing an isFinite() guard is exactly what caused the
 * zero-width 'container' render this plugin already carries a workaround for,
 * so 0 is explicitly not treated as a measurement.
 */
export function measureAssembledWidth(view: any): number | null {
  try {
    const bounds = view?.scenegraph?.()?.root?.bounds;
    if (bounds) {
      const { x1, x2 } = bounds;
      if (Number.isFinite(x1) && Number.isFinite(x2)) {
        const width = x2 - x1;
        if (width > 0) return width;
      }
    }
  } catch {
    /* a released or partially torn-down view: fall through to null */
  }
  return null;
}

/**
 * Recover the layout law from two samples. Returns null when the samples cannot
 * describe cell tiling — identical cell widths (division by zero), a
 * non-positive slope (assembled width not growing with cell width), or any
 * non-finite input.
 */
export function calibrateCellWidth(
  a: CellWidthSample,
  b: CellWidthSample,
): CellWidthModel | null {
  if (!a || !b) return null;
  const dCell = a.cell - b.cell;
  if (!Number.isFinite(dCell) || dCell === 0) return null;
  const dAssembled = a.assembled - b.assembled;
  if (!Number.isFinite(dAssembled)) return null;
  const slope = dAssembled / dCell;
  if (!Number.isFinite(slope) || slope <= 0) return null;
  const chrome = a.assembled - slope * a.cell;
  if (!Number.isFinite(chrome)) return null;
  return { slope, chrome };
}

/**
 * Cell width that makes the assembled view fit `targetWidth`.
 *
 * Floors rather than rounds: rounding up by a single pixel per cell overflows
 * by `slope` pixels, and an oversized faceted view is never scaled back down.
 */
export function solveCellWidth(
  model: CellWidthModel | null,
  targetWidth: number,
  minCellWidth: number = MIN_FACET_CELL_WIDTH,
): { cellWidth: number; clamped: boolean } {
  if (!model || !Number.isFinite(targetWidth) || targetWidth <= 0) {
    return { cellWidth: minCellWidth, clamped: true };
  }
  const ideal = (targetWidth - model.chrome) / model.slope;
  if (!Number.isFinite(ideal)) return { cellWidth: minCellWidth, clamped: true };
  const floored = Math.floor(ideal);
  if (floored < minCellWidth) return { cellWidth: minCellWidth, clamped: true };
  return { cellWidth: floored, clamped: false };
}

/**
 * Second probe point for calibration. Halving is preferred, but a small cell is
 * probed UPWARD instead: a probe below the layout floor measures the non-linear
 * regime and yields a bogus slope. Never returns the input width, which would
 * make calibration divide by zero.
 */
export function probeCellWidth(
  cell: number,
  floor: number = FACET_PROBE_FLOOR,
  delta: number = FACET_PROBE_DELTA,
): number {
  if (!Number.isFinite(cell) || cell <= 0) return floor + delta;
  const halved = Math.round(cell / 2);
  if (halved >= floor && halved !== cell) return halved;
  return cell + delta;
}

/**
 * True when a measured slope can be a cell count: at least one cell, and a
 * whole number within tolerance. A fractional slope is the signature of a
 * probe pair taken where assembled width is pinned by a fixed-width element
 * (typically a title) instead of being driven by the cells.
 */
export function isCellTilingSlope(
  slope: number,
  tolerance: number = SLOPE_INTEGER_TOLERANCE,
): boolean {
  if (!Number.isFinite(slope)) return false;
  if (slope < 1 - tolerance) return false;
  return Math.abs(slope - Math.round(slope)) <= tolerance;
}

/**
 * Probe pairs to try, in order. The first pair is the opening cell width and
 * its counterpart, which is the cheap case and the only one needed when nothing
 * pins the layout. Subsequent pairs double upward, because a pinned measurement
 * can only be escaped from ABOVE — the pinning element has a fixed width, so a
 * large enough cell grid always exceeds it.
 */
export function facetProbePairs(
  cell: number,
  escalations: number = MAX_PROBE_ESCALATIONS,
  ceiling: number = MAX_PROBE_CELL_WIDTH,
): Array<[number, number]> {
  const base = Number.isFinite(cell) && cell > 0
    ? cell
    : FACET_PROBE_FLOOR + FACET_PROBE_DELTA;
  const pairs: Array<[number, number]> = [[probeCellWidth(base), base]];
  let lo = base;
  for (let i = 0; i < escalations; i++) {
    lo *= 2;
    const hi = lo * 2;
    if (hi > ceiling) break;
    pairs.push([lo, hi]);
  }
  return pairs;
}

function readSignal(view: any, name: string): number | undefined {
  try { return view.signal(name); } catch { return undefined; }
}

async function writeSignal(view: any, name: string, value: number): Promise<boolean> {
  try { view.signal(name, value); await view.runAsync(); return true; }
  catch { return false; }
}

/**
 * Make the rendered canvas follow the layout the fit just produced.
 *
 * A signal write plus runAsync() relayouts the SCENEGRAPH but leaves vega's
 * internal autosize flag clear, so the rendered SVG keeps the width, height
 * and viewBox it was given by the PRE-FIT layout. Measured on vega 6 /
 * vega-lite 6.4 in Chromium at a 1248px container: after fitting child_width
 * from the 240px detached-container estimate up to 980px, the scenegraph
 * correctly reported 1246px while the SVG still carried
 * `width="516" viewBox="0 0 516 561"` — 516 being chrome plus the stale 240px
 * estimate. Two separate visible failures both trace to that one attribute:
 *
 *   1. This plugin's CSS pins the SVG to `width: 100%; height: auto`. With a
 *      viewBox present that is a UNIFORM SCALE, not a re-layout, so a
 *      516-unit viewBox stretched across 1248px magnifies the entire chart by
 *      2.42x — and the height follows the aspect ratio, turning 561px of
 *      chart into a 1357px element. Measured factors across four real specs
 *      were 2.42x, 2.57x, 2.86x and 2.66x, each exactly
 *      containerWidth / viewBoxWidth. The tallest reached 5771px, which is
 *      then clipped to a centred slice of the 960px render viewport — the
 *      grey-band-above-and-below symptom.
 *   2. Scenegraph content past the stale viewBox width falls outside the
 *      element box entirely, cutting off the right-hand cells and bars.
 *
 * View.resize() sets vega's `_autosize` flag and touches the autosize signal,
 * which is what forces the top-level ViewLayout to re-run and the canvas to be
 * re-derived from the current scenegraph. Verified in-page: viewBox went
 * 516 -> 1256 (matching the 1247px content) and the element height collapsed
 * from 1357px to 557px.
 *
 * Called only once the width is final. Resizing on each probe would recompute
 * autosize for widths that are about to be discarded, and the probes measure
 * scenegraph bounds, which a canvas resize does not inform. Guarded on the
 * method existing, since a mocked or torn-down view may not expose it, and
 * swallowing is correct here: a canvas left stale is the pre-existing
 * behaviour, not a new failure.
 */
async function syncCanvasToLayout(view: any): Promise<void> {
  try {
    if (typeof view?.resize !== 'function') return;
    view.resize();
    await view.runAsync();
  } catch {
    /* released view: leave the canvas as it stands */
  }
}

/**
 * Sample assembled width at a cell width, skipping the signal write when the
 * view already sits there so the healthy case still costs two runs.
 */
async function sampleAt(view: any, cell: number): Promise<CellWidthSample | null> {
  if (readSignal(view, CELL_WIDTH_SIGNAL) !== cell &&
      !(await writeSignal(view, CELL_WIDTH_SIGNAL, cell))) {
    return null;
  }
  const assembled = measureAssembledWidth(view);
  return assembled === null ? null : { cell, assembled };
}

/**
 * Derive the layout law by probing a live view. Costs two runs in the common
 * case and is intended to be called while the container is still detached,
 * where that cost is not visible. Returns null for a view with no cell-width
 * signal, i.e. anything that is not a faceted Vega-Lite compilation — such a
 * view is left untouched.
 *
 * Probe pairs escalate while the measured slope is not a whole cell count: a
 * fixed-width title wider than the cell grid pins assembled width, and a pair
 * taken inside that pinned band yields slope 0 (no model at all) or a
 * fractional slope (a wrong one). A model from a pinned pair is kept only as a
 * last-resort fallback, since it still beats leaving the chart unfitted.
 */
export async function calibrateFacetView(view: any): Promise<CellWidthModel | null> {
  const opening = readSignal(view, CELL_WIDTH_SIGNAL);
  if (!Number.isFinite(opening as number)) return null;

  let fallback: CellWidthModel | null = null;
  try {
    for (const [lo, hi] of facetProbePairs(opening as number)) {
      const low = await sampleAt(view, lo);
      if (low === null) break;
      const high = await sampleAt(view, hi);
      if (high === null) break;
      const model = calibrateCellWidth(high, low);
      if (!model) continue;
      if (fallback === null) fallback = model;
      if (isCellTilingSlope(model.slope)) return model;
    }
  } finally {
    // Restore the opening width. If the fit never runs — the container is
    // never attached, or reports clientWidth 0 forever because an ancestor is
    // display:none — the view must be left at the estimate rather than
    // stranded at whatever probe point happened to be convenient.
    if (readSignal(view, CELL_WIDTH_SIGNAL) !== opening) {
      await writeSignal(view, CELL_WIDTH_SIGNAL, opening as number);
      // The probes moved the layout, so the canvas is now stale against the
      // restored width too. A view whose fit never runs must still be
      // self-consistent, otherwise calibrating and then bailing out leaves
      // the chart MORE wrongly scaled than never having probed it.
      await syncCanvasToLayout(view);
    }
  }
  return fallback;
}

/**
 * Solve and apply the cell width for a measured target. The verification loop
 * is a bounded safety net for layouts that are not perfectly linear; with the
 * 120px floor in place the measured need is zero passes.
 */
export async function applyFacetCellWidth(
  view: any,
  model: CellWidthModel,
  targetWidth: number,
  minCellWidth: number = MIN_FACET_CELL_WIDTH,
): Promise<FacetFitResult> {
  let { cellWidth, clamped } = solveCellWidth(model, targetWidth, minCellWidth);
  if (!(await writeSignal(view, CELL_WIDTH_SIGNAL, cellWidth))) {
    // Never report a fit that was not actually applied.
    return { cellWidth, clamped, assembledWidth: null, corrections: 0, overflows: false };
  }
  let assembled = measureAssembledWidth(view);
  let corrections = 0;

  while (assembled !== null && assembled > targetWidth && !clamped &&
         corrections < MAX_FIT_CORRECTIONS) {
    const overflow = assembled - targetWidth;
    const next = Math.max(minCellWidth, Math.floor(cellWidth - overflow / model.slope));
    if (next === cellWidth) break; // cannot improve; stop rather than spin
    const previousCell = cellWidth;
    const previousClamped = clamped;
    const previousAssembled = assembled;
    cellWidth = next;
    clamped = cellWidth <= minCellWidth;
    if (!(await writeSignal(view, CELL_WIDTH_SIGNAL, cellWidth))) break;
    assembled = measureAssembledWidth(view);
    corrections++;
    // Overflow that shrinking the cells does not relieve comes from a
    // FIXED-WIDTH element — a title wider than the cell grid is the usual
    // case — and no cell width can fix it. Put the solved width back instead
    // of crushing the cells to the floor for no gain; the caller scrolls.
    if (assembled === null || assembled > previousAssembled - 1) {
      cellWidth = previousCell;
      clamped = previousClamped;
      await writeSignal(view, CELL_WIDTH_SIGNAL, cellWidth);
      assembled = previousAssembled;
      break;
    }
  }

  // The cell width is final; make the canvas match it. Deliberately after the
  // correction loop and deliberately not re-measuring afterwards: the loop
  // reasons about scenegraph extent, which is what the model describes, and an
  // autosize pass was measured to move that extent by 1px (1246 -> 1247) —
  // inside the tolerance `overflows` already carries. Re-measuring here would
  // let a sub-pixel padding change flip the scroll decision.
  await syncCanvasToLayout(view);

  return {
    cellWidth,
    clamped,
    assembledWidth: assembled,
    corrections,
    // 1px tolerance: sub-pixel text extent should not trigger scrollbars.
    overflows: assembled !== null && assembled > targetWidth + 1,
  };
}
