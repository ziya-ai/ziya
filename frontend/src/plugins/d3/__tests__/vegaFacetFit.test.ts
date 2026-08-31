/**
 * Measured cell-width fitting for faceted Vega-Lite views.
 *
 * WHAT THIS REPLACES: an estimated "chrome reserve" constant subtracted from
 * the container width. That estimate could not be made correct, for two
 * independently fatal reasons measured against vega-lite 6.4:
 *
 *   1. Horizontal chrome (legend, facet headers, y-axis labels, padding) is a
 *      LAYOUT OUTPUT that depends on label text, not something derivable from
 *      the spec. Identical row-faceted specs measured cell + 285px with short
 *      legend labels and cell + 437px with long ones.
 *   2. D3Renderer calls plugin.render() on a DETACHED container (tempContainer
 *      is appended only after the render promise resolves), so the measured
 *      container width at that point is the 400px floor, not the real width.
 *      Subtracting a ~450px reserve from 400 underflows to the cell floor,
 *      which would have rendered every faceted chart at minimum cell width.
 *
 * So the width is MEASURED instead of estimated. Vega-Lite compiles every
 * faceting spelling (encoding.row, encoding.column, encoding.facet, and the
 * facet operator) to a single `child_width` signal that is settable and
 * relayouts on run. Two probe points give the exact linear model
 * `assembled = slope * cellWidth + chrome`, which is then solved for the target.
 *
 * MEASURED EVIDENCE (real Vega views, not assumed):
 *   - slope equals the number of horizontally-tiled cells: 1.000 for row
 *     faceting, 6.000 for a six-column facet, 10.000 for ten columns.
 *   - under SIGNAL update (as opposed to recompiling) chrome is CONSTANT, so
 *     the model is exact: solving landed assembled == target with 0.0px
 *     residual across 13 spec shapes x 4 target widths, needing zero
 *     corrective passes.
 *   - the model degrades only below ~50px cells, where a cell can no longer
 *     shrink to its content. The 120px floor keeps that regime unreachable.
 *
 * WOULD THESE FAIL PRE-FIX? Yes — ../vegaFacetFit did not exist, so the import
 * cannot resolve and the suite cannot run at all.
 */
import {
  CELL_WIDTH_SIGNAL,
  MAX_FIT_CORRECTIONS,
  MAX_PROBE_CELL_WIDTH,
  MAX_PROBE_ESCALATIONS,
  MIN_FACET_CELL_WIDTH,
  applyFacetCellWidth,
  calibrateCellWidth,
  calibrateFacetView,
  facetProbePairs,
  isCellTilingSlope,
  measureAssembledWidth,
  probeCellWidth,
  solveCellWidth,
} from '../vegaFacetFit';

/**
 * Stand-in for a Vega View implementing the linear layout law measured above:
 * assembled width = cells * cellWidth + chrome, with a floor below which a
 * cell refuses to shrink (the observed non-linear regime).
 */
function fakeView(opts: {
  cells: number;
  chrome: number;
  initialCell: number;
  hardFloor?: number;
  signalName?: string;
  throwOnWrite?: boolean;
  /**
   * Width of a FIXED-WIDTH element (in practice a top-level title) that the
   * root scenegraph bounds also cover. Below it, assembled width stops
   * responding to the cell width entirely — the regime that made calibration
   * report slope 0 for a real spec and leave the chart clipped.
   */
  pinnedWidth?: number;
  /**
   * Drop the resize() method, standing in for a mocked or partially
   * torn-down view. The fit must still complete rather than throw.
   */
  omitResize?: boolean;
}) {
  const name = opts.signalName ?? CELL_WIDTH_SIGNAL;
  const floor = opts.hardFloor ?? 0;
  const signals: Record<string, number> = { [name]: opts.initialCell };
  let runs = 0;
  let resizes = 0;
  let pendingResize = false;

  const layoutWidth = () => {
    const effective = Math.max(signals[name], floor);
    return Math.max(opts.cells * effective + opts.chrome, opts.pinnedWidth ?? 0);
  };

  // The RENDERED canvas (SVG width + viewBox), as distinct from the
  // scenegraph. Vega derives it during an autosize pass, so it is captured at
  // construction and thereafter only re-derived when resize() has flagged one.
  // A signal write + runAsync leaves it STALE, which is the measured defect:
  // child_width fitted 240 -> 980, scenegraph 1246px, SVG still viewBox 516.
  let canvasWidth = layoutWidth();

  const view: any = {
    runs: () => runs,
    resizes: () => resizes,
    /** Width the SVG canvas / viewBox currently declares. */
    canvasWidth: () => canvasWidth,
    signal(sig: string, value?: number) {
      if (value === undefined) {
        if (!(sig in signals)) throw new Error(`no signal ${sig}`);
        return signals[sig];
      }
      if (opts.throwOnWrite) throw new Error('signal write refused');
      signals[sig] = value;
      return this;
    },
    async runAsync() {
      runs++;
      // Faithful to vega: resize() only sets a flag; the canvas follows on the
      // next run. So resize() without a run must NOT update the canvas.
      if (pendingResize) { canvasWidth = layoutWidth(); pendingResize = false; }
      return this;
    },
    scenegraph() {
      return { root: { bounds: { x1: 0, x2: layoutWidth(), y1: 0, y2: 100 } } };
    },
  };
  if (!opts.omitResize) {
    view.resize = () => { resizes++; pendingResize = true; return view; };
  }
  return view as any;
}

describe('measureAssembledWidth', () => {
  it('reads assembled width from scenegraph bounds', () => {
    const view = fakeView({ cells: 6, chrome: 344, initialCell: 200 });
    expect(measureAssembledWidth(view)).toBe(6 * 200 + 344);
  });

  it('returns null rather than 0 when bounds are unusable', () => {
    // A zero width must NOT be treated as a measurement: 0 is finite, and the
    // 'container' width bug in this same plugin was caused precisely by a
    // finite-but-meaningless 0 passing a isFinite() guard.
    for (const bounds of [
      { x1: 0, x2: 0 }, { x1: NaN, x2: 100 }, { x1: 0, x2: Infinity }, null,
    ]) {
      const view = { scenegraph: () => ({ root: { bounds } }) } as any;
      expect(measureAssembledWidth(view)).toBeNull();
    }
  });

  it('returns null instead of throwing when the view has no scenegraph', () => {
    expect(measureAssembledWidth({} as any)).toBeNull();
    expect(measureAssembledWidth(null as any)).toBeNull();
    expect(measureAssembledWidth({
      scenegraph: () => { throw new Error('view released'); },
    } as any)).toBeNull();
  });
});

describe('calibrateCellWidth', () => {
  it('recovers the exact slope and chrome for row faceting (measured: 1, 285)', () => {
    // Real measurement: cell 600 -> 885, cell 300 -> 585.
    const model = calibrateCellWidth(
      { cell: 600, assembled: 885 }, { cell: 300, assembled: 585 });
    expect(model).not.toBeNull();
    expect(model!.slope).toBeCloseTo(1, 6);
    expect(model!.chrome).toBeCloseTo(285, 6);
  });

  it('recovers cell count as slope for a six-column facet (measured: 6, 344)', () => {
    // Real measurement: cell 200 -> 1544, cell 100 -> 944.
    const model = calibrateCellWidth(
      { cell: 200, assembled: 1544 }, { cell: 100, assembled: 944 });
    expect(model!.slope).toBeCloseTo(6, 6);
    expect(model!.chrome).toBeCloseTo(344, 6);
  });

  it('refuses identical probe points instead of dividing by zero', () => {
    expect(calibrateCellWidth(
      { cell: 100, assembled: 500 }, { cell: 100, assembled: 500 })).toBeNull();
  });

  it('refuses a non-positive slope, which cannot be a real facet layout', () => {
    // Equal assembled widths at different cell widths, or an inverted
    // relationship, mean the measurement is not describing cell tiling.
    expect(calibrateCellWidth(
      { cell: 200, assembled: 400 }, { cell: 100, assembled: 400 })).toBeNull();
    expect(calibrateCellWidth(
      { cell: 200, assembled: 300 }, { cell: 100, assembled: 400 })).toBeNull();
  });

  it('refuses non-finite measurements', () => {
    expect(calibrateCellWidth(
      { cell: 200, assembled: NaN }, { cell: 100, assembled: 300 })).toBeNull();
    expect(calibrateCellWidth(
      { cell: NaN, assembled: 900 }, { cell: 100, assembled: 300 })).toBeNull();
    expect(calibrateCellWidth(null as any, { cell: 1, assembled: 2 })).toBeNull();
  });
});

describe('solveCellWidth', () => {
  it('solves row faceting to land exactly on the target', () => {
    const model = { slope: 1, chrome: 285 };
    const { cellWidth, clamped } = solveCellWidth(model, 1160);
    expect(clamped).toBe(false);
    expect(model.slope * cellWidth + model.chrome).toBeLessThanOrEqual(1160);
    expect(cellWidth).toBe(875);
  });

  it('divides across cells for a six-column facet', () => {
    const model = { slope: 6, chrome: 344 };
    const { cellWidth } = solveCellWidth(model, 1160);
    expect(cellWidth).toBe(Math.floor((1160 - 344) / 6));
    expect(model.slope * cellWidth + model.chrome).toBeLessThanOrEqual(1160);
  });

  it('floors rather than rounds, so the solution never overshoots', () => {
    // Rounding up by one pixel per cell overflows by `slope` pixels, and the
    // faceted container does not scale an oversized view back down.
    const model = { slope: 7, chrome: 100 };
    for (const target of [1000, 1001, 1002, 1003, 1234, 999]) {
      const { cellWidth } = solveCellWidth(model, target);
      expect(model.slope * cellWidth + model.chrome).toBeLessThanOrEqual(target);
    }
  });

  it('clamps to the floor and REPORTS it when the grid cannot fit', () => {
    // 20 columns in 480px cannot be honoured; the caller needs to know so it
    // can enable scrolling rather than silently clipping.
    const { cellWidth, clamped } = solveCellWidth({ slope: 20, chrome: 300 }, 480);
    expect(cellWidth).toBe(MIN_FACET_CELL_WIDTH);
    expect(clamped).toBe(true);
  });

  it('clamps when chrome alone already exceeds the target', () => {
    const { cellWidth, clamped } = solveCellWidth({ slope: 6, chrome: 900 }, 500);
    expect(cellWidth).toBe(MIN_FACET_CELL_WIDTH);
    expect(clamped).toBe(true);
  });

  it('clamps on a null model or unusable target rather than emitting NaN', () => {
    for (const [model, target] of [
      [null, 1000], [{ slope: 6, chrome: 100 }, 0],
      [{ slope: 6, chrome: 100 }, -5], [{ slope: 6, chrome: 100 }, NaN],
    ] as const) {
      const out = solveCellWidth(model as any, target as number);
      expect(Number.isFinite(out.cellWidth)).toBe(true);
      expect(out.cellWidth).toBe(MIN_FACET_CELL_WIDTH);
      expect(out.clamped).toBe(true);
    }
  });

  it('honours a caller-supplied floor', () => {
    const out = solveCellWidth({ slope: 20, chrome: 300 }, 480, 60);
    expect(out.cellWidth).toBe(60);
  });
});

describe('probeCellWidth', () => {
  it('halves a comfortable cell width to get a second sample', () => {
    expect(probeCellWidth(600)).toBe(300);
  });

  it('never returns the same width as the input (that would divide by zero)', () => {
    for (const cell of [0, 1, 2, 3, 30, 40, 79, 80, 81, 600, 1e6]) {
      expect(probeCellWidth(cell)).not.toBe(cell);
    }
  });

  it('grows instead of shrinking when halving would go under the probe floor', () => {
    // A probe point below the layout floor measures the non-linear regime and
    // yields a bogus slope, so small cells are probed upward.
    expect(probeCellWidth(30)).toBeGreaterThan(30);
  });

  it('returns a usable width for degenerate input', () => {
    for (const bad of [0, -100, NaN, Infinity]) {
      const out = probeCellWidth(bad as number);
      expect(Number.isFinite(out)).toBe(true);
      expect(out).toBeGreaterThan(0);
    }
  });
});

describe('calibrateFacetView (view adapter)', () => {
  it('derives the true model from a view by probing it', async () => {
    const view = fakeView({ cells: 6, chrome: 344, initialCell: 240 });
    const model = await calibrateFacetView(view);
    expect(model!.slope).toBeCloseTo(6, 6);
    expect(model!.chrome).toBeCloseTo(344, 6);
  });

  it('costs a bounded number of view runs', async () => {
    // Calibration happens while the container is still detached, so its cost is
    // invisible — but an unbounded number of runs would not be. One run to
    // reach the probe point, one to restore.
    const view = fakeView({ cells: 1, chrome: 285, initialCell: 240 });
    await calibrateFacetView(view);
    expect(view.runs()).toBe(2);
  });

  it('restores the opening cell width, so a failed fit is not left at the probe', async () => {
    // If the fit never runs — container never attached, or clientWidth stuck at
    // 0 behind a display:none ancestor — the view must be left at the estimate
    // vegaSizing chose, NOT stranded at whatever probe point was convenient.
    const view = fakeView({ cells: 6, chrome: 344, initialCell: 240 });
    await calibrateFacetView(view);
    expect(view.signal(CELL_WIDTH_SIGNAL)).toBe(240);
  });

  it('returns null when the view has no cell-width signal', async () => {
    // A non-faceted or Vega-v5 view must not be mangled.
    const view = fakeView({ cells: 1, chrome: 0, initialCell: 100, signalName: 'other' });
    expect(await calibrateFacetView(view)).toBeNull();
  });

  it('returns null instead of throwing when signal writes are refused', async () => {
    const view = fakeView({ cells: 1, chrome: 0, initialCell: 100, throwOnWrite: true });
    expect(await calibrateFacetView(view)).toBeNull();
  });
});

describe('applyFacetCellWidth (the end-to-end fit)', () => {
  it('lands the assembled view exactly on the target for row faceting', async () => {
    const view = fakeView({ cells: 1, chrome: 285, initialCell: 240 });
    const model = await calibrateFacetView(view);
    const fit = await applyFacetCellWidth(view, model!, 1160);
    expect(fit.clamped).toBe(false);
    expect(fit.overflows).toBe(false);
    expect(fit.assembledWidth).toBe(1160);
    expect(fit.corrections).toBe(0);
  });

  it('fits a six-column facet inside the target', async () => {
    const view = fakeView({ cells: 6, chrome: 344, initialCell: 240 });
    const model = await calibrateFacetView(view);
    const fit = await applyFacetCellWidth(view, model!, 1160);
    expect(fit.overflows).toBe(false);
    expect(fit.assembledWidth!).toBeLessThanOrEqual(1160);
    expect(fit.assembledWidth!).toBeGreaterThan(1160 - 6);
  });

  it('reports clamped + overflowing when the grid genuinely cannot fit', async () => {
    // This is the case that MUST be reported rather than silently clipped:
    // 20 cells at the 120px floor is ~2900px inside a 480px container.
    const view = fakeView({ cells: 20, chrome: 300, initialCell: 240 });
    const model = await calibrateFacetView(view);
    const fit = await applyFacetCellWidth(view, model!, 480);
    expect(fit.clamped).toBe(true);
    expect(fit.overflows).toBe(true);
    expect(fit.cellWidth).toBe(MIN_FACET_CELL_WIDTH);
  });

  it('never leaves an unclamped view overflowing its target', async () => {
    // The load-bearing invariant: if we did not clamp, we fit.
    for (const cells of [1, 2, 3, 6, 8]) {
      for (const chrome of [120, 285, 344, 437]) {
        for (const target of [1600, 1160, 900, 700]) {
          const view = fakeView({ cells, chrome, initialCell: 240 });
          const model = await calibrateFacetView(view);
          const fit = await applyFacetCellWidth(view, model!, target);
          if (!fit.clamped) {
            expect(fit.assembledWidth!).toBeLessThanOrEqual(target);
            expect(fit.overflows).toBe(false);
          }
        }
      }
    }
  });

  it('corrects, within a bounded pass count, when layout is not perfectly linear', async () => {
    // hardFloor makes the fake view refuse to shrink past 200px per cell, the
    // real non-linear regime. The fit must terminate, not spin.
    const view = fakeView({ cells: 6, chrome: 344, initialCell: 600, hardFloor: 200 });
    const model = await calibrateFacetView(view);
    const fit = await applyFacetCellWidth(view, model!, 900);
    expect(fit.corrections).toBeLessThanOrEqual(MAX_FIT_CORRECTIONS);
  });

  it('terminates instead of looping when a correction cannot change the width', async () => {
    const view = fakeView({ cells: 6, chrome: 5000, initialCell: 240 });
    const model = await calibrateFacetView(view);
    const fit = await applyFacetCellWidth(view, model!, 600);
    expect(fit.cellWidth).toBe(MIN_FACET_CELL_WIDTH);
    expect(fit.corrections).toBeLessThanOrEqual(MAX_FIT_CORRECTIONS);
  });

  it('does not report a false fit when the width could not be written', async () => {
    const view = fakeView({ cells: 6, chrome: 344, initialCell: 240 });
    const model = await calibrateFacetView(view);
    const frozen = { ...view, signal: () => { throw new Error('released'); } } as any;
    const fit = await applyFacetCellWidth(frozen, model!, 1160);
    expect(fit.assembledWidth).toBeNull();
    expect(fit.overflows).toBe(false);
  });
});

describe('isCellTilingSlope', () => {
  it('accepts whole cell counts, within sub-pixel measurement tolerance', () => {
    for (const slope of [1, 1.01, 0.99, 6, 6.02, 10, 20]) {
      expect(isCellTilingSlope(slope)).toBe(true);
    }
  });

  it('rejects the slopes a title-pinned probe pair produces', () => {
    // Both measured on real vega-lite 6.4 views whose top-level title was wider
    // than the cell grid at the 240px detached-container estimate: 0.000 for
    // "export-markdown-conversation: per-dimension scores (0-5), Ziya
    // highlighted" and 0.758 for "Application-level encryption at rest:
    // per-dimension scores (0-5)".
    for (const slope of [0, 0.758, 0.5, 0.9, -1, NaN, Infinity]) {
      expect(isCellTilingSlope(slope as number)).toBe(false);
    }
  });
});

describe('facetProbePairs', () => {
  it('tries the cheap opening pair first', () => {
    const [first] = facetProbePairs(240);
    expect(first).toEqual([probeCellWidth(240), 240]);
  });

  it('escalates UPWARD, because a pinned band can only be escaped from above', () => {
    const pairs = facetProbePairs(240);
    expect(pairs.length).toBe(1 + MAX_PROBE_ESCALATIONS);
    // Every escalation must probe strictly higher than the opening pair, or it
    // measures the same pinned width again.
    const openingMax = Math.max(...pairs[0]);
    for (const [lo, hi] of pairs.slice(1)) {
      expect(lo).toBeGreaterThan(openingMax);
      expect(hi).toBeGreaterThan(lo);
      expect(hi).toBeLessThanOrEqual(MAX_PROBE_CELL_WIDTH);
    }
  });

  it('never yields equal endpoints, which would divide by zero', () => {
    for (const cell of [0, -5, NaN, 40, 120, 240, 1000, 7999, 100000]) {
      for (const [lo, hi] of facetProbePairs(cell as number)) {
        expect(lo).not.toBe(hi);
        expect(Number.isFinite(lo) && Number.isFinite(hi)).toBe(true);
      }
    }
  });
});

describe('calibrateFacetView with a title wider than the cell grid', () => {
  // REGRESSION: root scenegraph bounds cover the top-level title, which does
  // not shrink with child_width. Probing at the 240px detached estimate and its
  // half put BOTH samples inside the title-pinned band, so assembled width did
  // not move and the model was rejected — logged as "could not calibrate cell
  // width; keeping the opening estimate". The chart then kept an unfitted cell
  // width with no overflow handling and was clipped by the container's
  // overflow:hidden. Pre-fix these two cases return null / a 0.758 slope.

  it('recovers slope 1 for a spec whose pinned width kills the opening pair', async () => {
    // Measured: cell 120 -> 665px, cell 240 -> 756px (pin 665, chrome 516).
    const view = fakeView({ cells: 1, chrome: 516, initialCell: 240, pinnedWidth: 665 });
    const model = await calibrateFacetView(view);
    expect(model).not.toBeNull();
    expect(model!.slope).toBeCloseTo(1, 6);
    expect(model!.chrome).toBeCloseTo(516, 6);
  });

  it('recovers a model where the opening pair measured slope 0.000', async () => {
    // Measured: cell 120 and cell 240 both -> 769px (pin 769, chrome 299).
    const view = fakeView({ cells: 1, chrome: 299, initialCell: 240, pinnedWidth: 769 });
    const model = await calibrateFacetView(view);
    expect(model).not.toBeNull();
    expect(model!.slope).toBeCloseTo(1, 6);
    expect(model!.chrome).toBeCloseTo(299, 6);
  });

  it('still fits the view inside the container once calibrated', async () => {
    const view = fakeView({ cells: 1, chrome: 299, initialCell: 240, pinnedWidth: 769 });
    const model = await calibrateFacetView(view);
    const fit = await applyFacetCellWidth(view, model!, 1160);
    expect(fit.overflows).toBe(false);
    expect(fit.assembledWidth).toBe(1160);
  });

  it('keeps escalation bounded and restores the opening width when it fails', async () => {
    // A pin nothing can escape: every pair measures the same width.
    const view = fakeView({ cells: 1, chrome: 0, initialCell: 240, pinnedWidth: 1e6 });
    expect(await calibrateFacetView(view)).toBeNull();
    expect(view.signal(CELL_WIDTH_SIGNAL)).toBe(240);
    // Two runs per pair plus one restore.
    expect(view.runs()).toBeLessThanOrEqual(2 * (1 + MAX_PROBE_ESCALATIONS) + 1);
  });

  it('does not crush the cells when the overflow comes from the pinned width', async () => {
    // The 900px pin exceeds a 600px target no matter how small the cells get.
    // Shrinking them to the floor cannot help and only destroys the chart, so
    // the solved width must survive and the caller must be told to scroll.
    const view = fakeView({ cells: 1, chrome: 299, initialCell: 240, pinnedWidth: 900 });
    const model = await calibrateFacetView(view);
    const fit = await applyFacetCellWidth(view, model!, 600);
    expect(fit.overflows).toBe(true);
    expect(fit.cellWidth).toBeGreaterThan(MIN_FACET_CELL_WIDTH);
    expect(view.signal(CELL_WIDTH_SIGNAL)).toBe(fit.cellWidth);
    expect(fit.corrections).toBeLessThanOrEqual(MAX_FIT_CORRECTIONS);
  });
});

/**
 * Canvas synchronisation.
 *
 * MEASURED DEFECT (vega 6 / vega-lite 6.4, Chromium, 1248px container). A
 * signal write + runAsync relayouts the scenegraph but leaves vega's autosize
 * flag clear, so the SVG keeps its PRE-FIT width/height/viewBox. On a real
 * faceted spec the fit moved child_width 240 -> 980 and the scenegraph
 * correctly reported 1246px, while the SVG still carried
 * `width="516" viewBox="0 0 516 561"` (516 = chrome 276 + the stale 240px
 * detached-container estimate).
 *
 * Why a stale attribute is not cosmetic: the plugin's CSS pins the SVG to
 * `width: 100%; height: auto`, and with a viewBox present that is a UNIFORM
 * SCALE. Four real specs measured blow-up factors of 2.42x, 2.57x, 2.86x and
 * 2.66x — each exactly containerWidth / viewBoxWidth — turning 561px of chart
 * into a 1357px element and, worst case, 2016px into 5771px. That is then
 * clipped to a centred slice of the 960px render viewport, which is the
 * grey-band-above-and-below symptom. Separately, content past the stale
 * viewBox width lands outside the element box, cutting off the right-hand
 * cells.
 *
 * Verified in-page: calling view.resize() + runAsync() after the fit moved the
 * viewBox 516 -> 1256 (matching the 1247px content) and collapsed the element
 * height 1357px -> 557px.
 *
 * WOULD THESE FAIL PRE-FIX? Yes — applyFacetCellWidth never called resize(),
 * so resizes() stays 0 and canvasWidth() stays at the pre-fit value.
 */
describe('canvas sync after the fit', () => {
  it('leaves the canvas matching the fitted layout, not the opening estimate', async () => {
    // The measured case: chrome 276, opening estimate 240 -> canvas 516.
    const view = fakeView({ cells: 1, chrome: 276, initialCell: 240 });
    expect(view.canvasWidth()).toBe(516); // the stale value, pre-fit

    const model = await calibrateFacetView(view);
    const fit = await applyFacetCellWidth(view, model!, 1248);

    expect(fit.assembledWidth).toBe(1248);
    expect(view.canvasWidth()).toBe(fit.assembledWidth);
  });

  it('removes the CSS magnification that the stale canvas caused', async () => {
    // The defect expressed as the user sees it. `width: 100%` against a
    // viewBox is a uniform scale, so the vertical blow-up factor is exactly
    // containerWidth / canvasWidth. It must land at 1, not 2.42.
    const container = 1248;
    const view = fakeView({ cells: 1, chrome: 276, initialCell: 240 });

    const staleFactor = container / view.canvasWidth();
    expect(staleFactor).toBeGreaterThan(2); // 1248/516 = 2.42, as measured

    const model = await calibrateFacetView(view);
    await applyFacetCellWidth(view, model!, container);

    const syncedFactor = container / view.canvasWidth();
    expect(syncedFactor).toBeCloseTo(1, 2);
  });

  it('syncs the canvas even when the grid could not fit and must scroll', async () => {
    // A clamped fit still changed the cell width, so the canvas is still
    // stale. Reporting overflow while leaving the canvas mis-scaled would
    // make the scroll extent wrong as well as the height.
    const view = fakeView({ cells: 20, chrome: 300, initialCell: 240 });
    const model = await calibrateFacetView(view);
    const fit = await applyFacetCellWidth(view, model!, 480);

    expect(fit.clamped).toBe(true);
    expect(view.canvasWidth()).toBe(fit.assembledWidth);
  });

  it('costs exactly one resize for a successful fit', async () => {
    // Bounded cost: resizing per probe would recompute autosize for widths
    // about to be discarded, and the probes read scenegraph bounds, which a
    // canvas resize does not inform.
    const view = fakeView({ cells: 6, chrome: 344, initialCell: 240 });
    const model = await calibrateFacetView(view);
    expect(view.resizes()).toBe(0); // calibration alone must not resize
    await applyFacetCellWidth(view, model!, 1160);
    expect(view.resizes()).toBe(1);
  });

  it('syncs the canvas when calibration escalates and then restores', async () => {
    // Reaching the restore branch at all requires escalation: without a pin,
    // the opening pair succeeds and the view is already back at the opening
    // width, so nothing is restored and nothing needs syncing. A 900px pin
    // makes the first two pairs unusable, so the loop ends at an escalated
    // probe width and must both restore AND re-sync — otherwise a bail-out
    // leaves the canvas describing a probe width that was discarded.
    const view = fakeView({ cells: 1, chrome: 276, initialCell: 240, pinnedWidth: 900 });
    const model = await calibrateFacetView(view);

    expect(model).not.toBeNull();
    expect(model!.slope).toBeCloseTo(1, 6);
    expect(view.signal(CELL_WIDTH_SIGNAL)).toBe(240);
    expect(view.resizes()).toBe(1);
    // Consistent with the restored width rather than the last probe.
    expect(view.canvasWidth()).toBe(900);
  });

  it('does not resize when the width could not be applied', async () => {
    // Nothing was applied, so there is no new layout for the canvas to follow.
    const view = fakeView({ cells: 6, chrome: 344, initialCell: 240 });
    const model = await calibrateFacetView(view);
    let resizes = 0;
    const frozen = {
      ...view,
      resize: () => { resizes++; },
      signal: () => { throw new Error('released'); },
    } as any;
    const fit = await applyFacetCellWidth(frozen, model!, 1160);
    expect(fit.assembledWidth).toBeNull();
    expect(resizes).toBe(0);
  });

  it('completes the fit on a view that exposes no resize method', async () => {
    // Negative control for the guard: a mocked or torn-down view must degrade
    // to the old stale-canvas behaviour, not throw and lose the fit entirely.
    const view = fakeView({ cells: 1, chrome: 276, initialCell: 240, omitResize: true });
    expect(typeof (view as any).resize).toBe('undefined');
    const model = await calibrateFacetView(view);
    const fit = await applyFacetCellWidth(view, model!, 1248);
    expect(fit.assembledWidth).toBe(1248);
    expect(fit.overflows).toBe(false);
  });

  it('does not update the canvas from a resize that never ran', async () => {
    // Pins the fake to vega's real contract, so the suite cannot pass by
    // modelling resize() as immediate when the implementation forgot the run.
    const view = fakeView({ cells: 1, chrome: 276, initialCell: 240 });
    view.signal(CELL_WIDTH_SIGNAL, 980);
    view.resize();
    expect(view.canvasWidth()).toBe(516);
    await view.runAsync();
    expect(view.canvasWidth()).toBe(1256);
  });
});
