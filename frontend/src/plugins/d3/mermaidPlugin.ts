import { D3RenderPlugin } from '../../types/d3';
import initMermaidSupport, { enhancePacketDarkMode, buildTimelineDarkThemeVariables, buildSequenceNoteDarkThemeVariables, reapplyLinkStyleStrokes, moveGanttGridBehind, recolorGanttCritLabels, ensureShapeBordersAgainstCanvas, dodgeQuadrantPointCollisions } from './mermaidEnhancer';
import { isDiagramDefinitionComplete } from '../../utils/diagramUtils';
import { extractDefinitionFromYAML } from '../../utils/diagramUtils';
import { rerouteSkipEdges, shouldRerouteEdges } from './mermaidEdgeRerouter';
import { getZoomScript, getDownloadSvgScript } from '../../utils/popupScriptUtils';
import { hexToRgb, enhanceSVGVisibility, calculateContrastRatio } from '../../utils/colorUtils';
import { escapeHtml } from '../../utils/htmlSanitize';

/**
 * D-293 / G-787b57 (mermaid-w1-06): the dark-theme post-render recolour pass
 * repaints every `defs marker path` with the theme line colour, forcing BOTH
 * `stroke` and `fill`. Solid arrowhead triangles need that (they are filled and
 * must stay visible), but ER crow's-foot / cardinality markers are drawn HOLLOW
 * (`fill:none`, stroked outline). Blanket-filling a hollow marker turns it into
 * a solid teal blob that occludes the entity border and hides the cardinality
 * glyph. This resolves the marker's colours: the stroke is always the theme
 * line colour (so the marker stays legible on the canvas), but the fill is only
 * overridden when the marker was actually filled — a hollow marker keeps
 * `fill:none` and its glyph reads through. `lineColor` clears contrast on both
 * canvases (#88c0d0 on #2e3440 = 6.24:1 dark; #333333 on #ffffff = 12.63:1
 * light). The equivalent guard is inlined into the injected `applyMermaidTheme`
 * template below; this exported helper documents and locks the intended
 * behaviour under test.
 */
export function resolveMermaidMarkerColors(
  currentFill: string | null | undefined,
  lineColor: string,
): { stroke: string; fill: string | null } {
  const cf = (currentFill || '').trim().toLowerCase();
  const hollow = cf === 'none' || cf === 'transparent' || cf === '';
  return { stroke: lineColor, fill: hollow ? null : lineColor };
}

// ---------------------------------------------------------------------------
// D-159 (G-79): pie-slice / legend-swatch palette contrast.
//
// Mermaid's default pie palette fails the 3:1 graphical-object floor in BOTH
// themes: the light "default" theme recycles near-white ivory (#ffffe0,
// 1.02:1 on white) and pure yellow (#ffff00, 1.07:1), while the "dark" theme
// collapses to near-black / maroon variants (1.19-1.31:1 on the dark page),
// and adjacent wedges land on two barely-separable lavenders so a slice cannot
// be matched to its legend swatch. The plugin previously set NO pie* theme
// variables (the light branch passed `{}` and the dark branch set only base
// colours), so mermaid's own defaults were used verbatim.
//
// Fix: resolve a curated categorical palette FROM the theme the renderer was
// given (a light-tuned palette on the light page, a dark-tuned palette on the
// dark page — not a single constant swap). Every entry clears >=3:1 against its
// own background (verified with the WCAG relative-luminance formula):
//   LIGHT on #ffffff: min 3.16 (#59a14f, #d17d00) .. max 5.85 (#7b4fb0)
//   DARK  on #1f1f1f: min 6.94 (#b79be6) .. max 10.40 (#e0cf5c)
//         on #262626: min 6.37 .. max 9.55
// pieOpacity is pinned to 1 so those opaque ratios actually hold (mermaid's
// default 0.7 would composite each fill toward the page and reduce contrast),
// and a page-coloured inter-slice stroke draws a gap between neighbours so
// adjacency never depends on two fills differing in luminance.
// ---------------------------------------------------------------------------
export const PIE_PALETTE_LIGHT: string[] = [
    '#4e79a7', '#e15759', '#59a14f', '#b07aa1', '#9c6b3f', '#d17d00',
    '#3a8a86', '#8e792b', '#c05299', '#6f6f6f', '#7b4fb0', '#2f7ab8',
];
export const PIE_PALETTE_DARK: string[] = [
    '#7fb3e0', '#f28e8f', '#8fd48a', '#d6a9cf', '#d9a066', '#f0b24d',
    '#67c7c2', '#e0cf5c', '#ed9ac9', '#b8b8b8', '#b79be6', '#6fc0f0',
];

/**
 * Build the pie-specific mermaid themeVariables (pie1..pie12 + stroke/opacity)
 * for the active theme. These keys only affect pie charts, so merging them into
 * the global themeVariables leaves every other diagram type byte-for-byte
 * unchanged. Exported for unit testing.
 */
export function buildPieThemeVariables(isDarkMode: boolean): Record<string, string> {
    const palette = isDarkMode ? PIE_PALETTE_DARK : PIE_PALETTE_LIGHT;
    const vars: Record<string, string> = {};
    palette.forEach((color, i) => { vars[`pie${i + 1}`] = color; });
    // Opaque fills so the verified per-theme contrast ratios hold (default 0.7
    // would composite the fill toward the page and sink pale slices below floor).
    vars.pieOpacity = '1';
    // Page-coloured gap between adjacent slices + a contrasting outer ring, so
    // neighbouring wedges are separable regardless of their fill luminance.
    vars.pieStrokeColor = isDarkMode ? '#1f1f1f' : '#ffffff';
    vars.pieStrokeWidth = '2px';
    vars.pieOuterStrokeColor = isDarkMode ? '#e6e6e6' : '#333333';
    vars.pieOuterStrokeWidth = '2px';
    // G-14a672 / D-152: pie TEXT colours resolved from the active theme.
    // The dark path uses mermaid theme 'dark', whose base derives a LIGHT pie
    // legend/section/title text colour, so dark legends read. The light path
    // uses theme 'default', whose pieLegendTextColor default is near-white — so
    // at scale the 60-row legend rendered white-on-white (only ~3 of 60 labels
    // visible; the shared textColor override does NOT reach the pie* text keys).
    // Pin all three pie text keys per-theme so the legend/section/title colour
    // is resolved from the theme the renderer was given, not a stray default:
    //   light  #1a1a1a on #ffffff = 17.4:1   (text floor 4.5:1)
    //   dark   #eceff4 on #1f1f1f = 14.3:1
    // The dark value matches the existing 'dark' base, so dark is unchanged.
    const pieTextColor = isDarkMode ? '#eceff4' : '#1a1a1a';
    vars.pieLegendTextColor = pieTextColor;
    vars.pieSectionTextColor = pieTextColor;
    vars.pieTitleTextColor = pieTextColor;
    return vars;
}

// ---------------------------------------------------------------------------
// G-baa164 / D-157 (mermaid-w2-15): pie PALETTE RECYCLING at scale.
//
// mermaid's pie theme exposes only pie1..pie12, so a chart with >12 slices
// drives its d3 ordinal scale past its range and RECYCLES the 12 fills (a
// 60-slice chart repeats each colour 5x). buildPieThemeVariables makes those 12
// canvas-aware but cannot make 60 categories individually identifiable — a fixed
// vendor limit no themeVariable can lift. Post-render we instead assign every
// slice (and its legend swatch) its OWN colour from an evenly-spaced hue ramp,
// with the lightness solved per hue so each fill clears the >=3:1 canvas floor
// on the theme background it was rendered against (light #ffffff / dark #1f1f1f).
// Only fires beyond the 12-entry limit, so a normal pie keeps the curated
// PIE_PALETTE_* untouched. Deterministic (attribute-only) and idempotent.
// ---------------------------------------------------------------------------
export const PIE_RECYCLE_THRESHOLD = 12;

function hslToHex(h: number, s: number, l: number): string {
    // h in [0,360), s/l in [0,1]
    const c = (1 - Math.abs(2 * l - 1)) * s;
    const hp = h / 60;
    const x = c * (1 - Math.abs((hp % 2) - 1));
    let r = 0, g = 0, b = 0;
    if (hp < 1) { r = c; g = x; }
    else if (hp < 2) { r = x; g = c; }
    else if (hp < 3) { g = c; b = x; }
    else if (hp < 4) { g = x; b = c; }
    else if (hp < 5) { r = x; b = c; }
    else { r = c; b = x; }
    const m = l - c / 2;
    const to = (v: number) => Math.round((v + m) * 255).toString(16).padStart(2, '0');
    return `#${to(r)}${to(g)}${to(b)}`;
}

/**
 * Pick a fill for `hue` that clears `floor`:1 contrast against `bg`. Contrast is
 * monotonic in HSL lightness at fixed hue/sat (darker => more contrast on a
 * light bg, lighter => more on a dark bg), so we scan lightness from the
 * background-appropriate end and return the first vivid value that clears the
 * floor. Guarantees a legible, saturated fill for any hue on either canvas.
 */
export function pieRampColor(hue: number, isDarkMode: boolean, tier = 0, floor = 3.0): string {
    const bg = isDarkMode ? '#1f1f1f' : '#ffffff';
    const sat = 0.62;
    // THREE lightness tiers give a SECOND distinguishing axis on top of hue, so
    // two slices that happen to land near each other in hue still separate by
    // tone. On light we want darker fills (lower L); on dark, lighter (higher L).
    // Each tier scans its own band first, then falls through to a shared tail
    // that always reaches the floor, so every (hue, tier) pair clears `floor`:1.
    const tierBands = isDarkMode
        ? [[0.70, 0.66], [0.58, 0.54], [0.80, 0.76]]
        : [[0.32, 0.36], [0.44, 0.48], [0.24, 0.20]];
    const tail = isDarkMode ? [0.50, 0.46, 0.42] : [0.28, 0.20, 0.16];
    const steps = [...tierBands[((tier % 3) + 3) % 3], ...tail];
    for (const l of steps) {
        const hex = hslToHex(((hue % 360) + 360) % 360, sat, l);
        if (calculateContrastRatio(hex, bg) >= floor) return hex;
    }
    // Fallback: extreme lightness that always clears the floor.
    return hslToHex(((hue % 360) + 360) % 360, sat, isDarkMode ? 0.82 : 0.14);
}

export interface PieRecolorResult { recolored: number; sliceCount: number; }

// Golden-angle hue spacing: consecutive slices land ~137.5deg apart on the hue
// wheel (maximally separated), instead of the old monotonic (i*360/n) ramp whose
// adjacent wedges differed by only 360/n deg (6deg at 60 slices) and read as one
// colour. This is the standard technique for making a long sequence of
// categorical colours mutually distinguishable.
const PIE_GOLDEN_ANGLE = 137.508;

export function recolorPieSlicesAtScale(svgElement: Element, isDarkMode: boolean): PieRecolorResult {
    const out: PieRecolorResult = { recolored: 0, sliceCount: 0 };
    const slices = svgElement.querySelectorAll('path.pieCircle');
    out.sliceCount = slices.length;
    if (slices.length <= PIE_RECYCLE_THRESHOLD) return out; // curated palette suffices
    const n = slices.length;
    const colors: string[] = [];
    for (let i = 0; i < n; i++) colors.push(pieRampColor((i * PIE_GOLDEN_ANGLE) % 360, isDarkMode, i % 3));
    slices.forEach((slice, i) => {
        (slice as HTMLElement).setAttribute('fill', colors[i]);
        (slice as unknown as SVGElement).style.setProperty('fill', colors[i], 'important');
        out.recolored++;
    });
    // Match the legend swatches (one rect per slice, in data order) so the key
    // stays in sync with the wedges.
    const swatches = svgElement.querySelectorAll('g.legend rect');
    swatches.forEach((rect, i) => {
        if (i < colors.length) {
            (rect as HTMLElement).setAttribute('fill', colors[i]);
            (rect as unknown as SVGElement).style.setProperty('fill', colors[i], 'important');
        }
    });
    return out;
}

// ---------------------------------------------------------------------------
// G-14a672 / D-152: pie STRUCTURE at scale (distinct from the D-159 palette).
//
// The vendored (upstream) mermaid pie renderer sizes the <svg> to the pie
// square only; it then stacks one legend row per data point down a single
// column starting at the pie's vertical centre. Past ~two dozen slices that
// column is far taller than the square viewBox, so the legend is clipped to
// whichever rows happen to fall inside the centred box (the classic
// "only rows 20-39 of 60 are visible, cut mid-row" symptom) and the title,
// laid out just above the pie, is squeezed against / occluded by the legend.
// Separately, every slice emits a percentage <text> at the wedge centroid;
// at 60 slices those converge on the hub into an illegible ink-blob.
//
// This is a layout defect in code we do not own, so we repair it with a
// post-render pass on the emitted SVG (the same pattern used for gantt grid
// z-order and edge rerouting) rather than special-casing the spec:
//   (1) enlarge the viewBox to the UNION of the pie box, the full legend
//       column and the title, so nothing is clipped regardless of slice count;
//   (2) once the per-slice labels are dense enough to collide, drop them — the
//       value is still carried by the legend (pie showData) and by the wedge
//       geometry, so no information is lost, only the overlap.
// A small pie (<= threshold, legend fits the square) is untouched: no label is
// removed and the union never exceeds the existing box, so this is a no-op.
// Exported for unit testing.
// ---------------------------------------------------------------------------
export const PIE_LABEL_COLLISION_THRESHOLD = 24;

// ---------------------------------------------------------------------------
// D-287 / D-145 (oversize-canvas-content-clipped/shrunk-labels-subpixel):
// Mermaid's layout engine over-allocates the viewBox — sometimes far WIDER,
// sometimes far TALLER than the rendered content — leaving a large empty band.
// When that SVG is scaled to fit the capture container, the real content
// collapses to a sub-pixel sliver (labels ~2px). Trimming the viewBox to the
// content bounding box (plus a small pad) reclaims the dead space.
//
// The pre-fix gate only fired when WIDTH could be reclaimed, so the DOMINANT
// mermaid case — a tight-width but grossly-oversize-HEIGHT canvas, with the
// diagram sitting in the top ~15-25% and empty space below — was never
// trimmed, and the content was shrunk on capture. This helper reclaims EITHER
// axis. Pure and exported for unit testing.
// ---------------------------------------------------------------------------
export interface ViewBoxTrimResult {
    shouldTrim: boolean;
    newViewBox: string | null;
    reclaimedWidthPct: number;
    reclaimedHeightPct: number;
}

export function computeViewBoxTrim(
    viewBox: string | null,
    bbox: { x: number; y: number; width: number; height: number } | null | undefined,
    pad = 16,
    threshold = 0.9,
): ViewBoxTrimResult {
    const none: ViewBoxTrimResult = {
        shouldTrim: false, newViewBox: null,
        reclaimedWidthPct: 0, reclaimedHeightPct: 0,
    };
    if (!viewBox || !bbox || !(bbox.width > 0) || !(bbox.height > 0)) return none;
    const parts = viewBox.split(/\s+/).map(Number);
    if (parts.length < 4 || parts.some((n) => Number.isNaN(n))) return none;
    const oldW = parts[2];
    const oldH = parts[3];
    const trimmedW = bbox.width + pad * 2;
    const trimmedH = bbox.height + pad * 2;
    // Reclaim if EITHER axis would shrink by more than (1 - threshold).
    const reclaimW = oldW > 0 && trimmedW < oldW * threshold;
    const reclaimH = oldH > 0 && trimmedH < oldH * threshold;
    const shouldTrim = reclaimW || reclaimH;
    const newViewBox = `${bbox.x - pad} ${bbox.y - pad} ${trimmedW} ${trimmedH}`;
    return {
        shouldTrim,
        newViewBox: shouldTrim ? newViewBox : null,
        reclaimedWidthPct: oldW > 0 ? (1 - trimmedW / oldW) * 100 : 0,
        reclaimedHeightPct: oldH > 0 ? (1 - trimmedH / oldH) * 100 : 0,
    };
}

export interface PieLayoutFixResult {
    isPie: boolean;
    sliceCount: number;
    labelsRemoved: number;
    viewBoxExpanded: boolean;
    newViewBox?: string;
}

function pieParseTranslate(transform: string | null): { x: number; y: number } | null {
    if (!transform) return null;
    const m = transform.match(/translate\(\s*(-?[\d.]+)[ ,]+(-?[\d.]+)\s*\)/);
    if (!m) {
        const single = transform.match(/translate\(\s*(-?[\d.]+)\s*\)/);
        if (single) return { x: parseFloat(single[1]), y: 0 };
        return null;
    }
    return { x: parseFloat(m[1]), y: parseFloat(m[2]) };
}

function pieNum(v: string | null | undefined, fallback: number): number {
    if (v == null) return fallback;
    const n = parseFloat(v);
    return isNaN(n) ? fallback : n;
}

/**
 * Sum the translate() of an element and every ancestor <g> up to (not
 * including) the <svg>. Mermaid places each legend row inside a group that is
 * itself translated to the pie centre, so a legend row's own transform is in
 * the centred group's LOCAL space; to compare it against the root viewBox we
 * must add that ancestor offset. Non-translate transforms are ignored (mermaid
 * pie uses only translate for these groups).
 */
function pieAbsoluteTranslate(el: Element, root: Element): { x: number; y: number } {
    let x = 0;
    let y = 0;
    let node: Element | null = el;
    while (node && node !== root) {
        const t = pieParseTranslate(node.getAttribute('transform'));
        if (t) { x += t.x; y += t.y; }
        node = node.parentElement;
    }
    return { x, y };
}

/**
 * Repair the upstream pie renderer's at-scale legend clipping, title
 * occlusion and slice-label collision. Operates purely on emitted SVG
 * attributes/geometry (no getBBox), so it is deterministic under jsdom and
 * in the headless renderer alike. Idempotent.
 */
export function fixPieLayoutAtScale(svgElement: Element): PieLayoutFixResult {
    const result: PieLayoutFixResult = {
        isPie: false, sliceCount: 0, labelsRemoved: 0, viewBoxExpanded: false,
    };

    const slices = svgElement.querySelectorAll('path.pieCircle');
    const legendItems = svgElement.querySelectorAll('g.legend');
    const titleEl = svgElement.querySelector('.pieTitleText');
    if (slices.length === 0 && legendItems.length === 0 && !titleEl) {
        return result; // not a pie chart
    }
    result.isPie = true;
    result.sliceCount = slices.length || legendItems.length;

    // (1) Anti-collision: past the density threshold the centroid percentage
    // labels overlap into an illegible mass. Remove them; the legend + wedge
    // geometry still convey each slice.
    if (result.sliceCount > PIE_LABEL_COLLISION_THRESHOLD) {
        svgElement.querySelectorAll('text.slice').forEach((l) => {
            l.parentNode?.removeChild(l);
            result.labelsRemoved++;
        });
    }

    // (2) Enclose the full legend column in the viewBox so no row is clipped.
    // The legend column, not the title, is what overflows. CRUCIAL: mermaid's
    // upstream pie renderer (default legend position) lays the column out
    // VERTICALLY CENTRED on the pie hub — legend row `t` is placed at local y
    // `t*E - E*n/2` inside the group that is translated to the pie centre — yet
    // it then sizes the viewBox as `minX 0 w <pieHeight>` (origin at 0, height
    // the pie square only). So at scale the column runs from a large NEGATIVE
    // y (above the box top) to a large positive y (below the box bottom), and
    // only the middle band of rows falls inside the centred box (the observed
    // "rows 20-39 of 60" truncation). Growing only downward — the previous
    // behaviour — reveals the bottom rows but leaves the ~n/2 rows above minY
    // still clipped. We therefore grow the box to the legend's real
    // (ancestor-accumulated) extent in ALL FOUR directions: move the origin
    // up/left as needed and extend right/bottom. Never shrink — a small pie
    // whose legend already fits the box is a no-op.
    const vb = svgElement.getAttribute('viewBox');
    if (vb) {
        const parts = vb.split(/[\s,]+/).map(Number).filter((n) => !isNaN(n));
        if (parts.length === 4) {
            const [minX, minY, w, h] = parts;
            const curRight = minX + w;
            const curBottom = minY + h;
            const pad = 8;
            let needLeft = minX;
            let needTop = minY;
            let needRight = curRight;
            let needBottom = curBottom;
            // Overflow is decided from the RAW (unpadded) row extent so a legend
            // that already fits the box is a true no-op; the pad is added only
            // to the grown box, never used to manufacture a 1-row overflow.
            let overflow = false;

            legendItems.forEach((g) => {
                const t = pieAbsoluteTranslate(g, svgElement);
                const rect = g.querySelector('rect');
                const rw = pieNum(rect?.getAttribute('width'), 18);
                const rh = pieNum(rect?.getAttribute('height'), 18);
                const txt = g.querySelector('text');
                // estimate legend-label width so a long label is not clipped on the right
                const textW = (txt?.textContent?.length ?? 0) * 7 + rw + 8;
                const rawLeft = t.x;
                const rawTop = t.y;
                const rawRight = t.x + Math.max(rw, textW);
                const rawBottom = t.y + Math.max(rh, 14);
                if (
                    rawLeft < minX || rawTop < minY ||
                    rawRight > curRight || rawBottom > curBottom
                ) {
                    overflow = true;
                }
                if (rawLeft - pad < needLeft) needLeft = rawLeft - pad;
                if (rawTop - pad < needTop) needTop = rawTop - pad;
                if (rawRight + pad > needRight) needRight = rawRight + pad;
                if (rawBottom + pad > needBottom) needBottom = rawBottom + pad;
            });

            // Only rewrite if a legend row actually overflows the current box on
            // any side (grow to the legend column; never shrink).
            if (overflow) {
                const nMinX = Math.min(minX, needLeft);
                const nMinY = Math.min(minY, needTop);
                const nW = needRight - nMinX;
                const nH = needBottom - nMinY;
                const newVB = `${nMinX} ${nMinY} ${nW} ${nH}`;
                svgElement.setAttribute('viewBox', newVB);
                // Keep explicit width/height in step so a fixed size attr cannot
                // re-clip the enlarged legend column.
                if (svgElement.getAttribute('height') != null) {
                    svgElement.setAttribute('height', String(nH));
                }
                if (svgElement.getAttribute('width') != null) {
                    svgElement.setAttribute('width', String(nW));
                }
                result.viewBoxExpanded = true;
                result.newViewBox = newVB;
            }
        }
    }

    return result;
}

/**
 * G-0daf08 / D-153: canvas-aware LIGHT-theme themeVariables for the mermaid
 * diagram types whose stock `default` palette is placed without reference to
 * the WHITE render canvas. The dark branch of `mermaid.initialize` carries an
 * extensive high-contrast override; the light branch historically carried ONLY
 * `buildPieThemeVariables(false)`, so mermaid's default palette produced
 * illegible output on white:
 *   - quadrantChart white point labels on near-white quadrants (~1.04:1, w1-13)
 *   - journey white task labels on pale-yellow bands (~1.02:1, w1-09)
 *   - gitGraph pale yellow/green branch strokes on white (~1.07:1, w1-10)
 *   - mindmap pale link ribbons (~1.05:1, w1-11)
 *
 * This block ONLY sets text/label colours and the diagram-type-specific
 * palettes (quadrant*, journey task text, git0..git7 + labels, lineColor). It
 * deliberately does NOT touch node backgrounds (mainBkg/nodeBkg/clusterBkg),
 * so flowchart/class/state/sequence diagrams — whose default light contrast is
 * already fine — render byte-for-byte as before.
 *
 * Every colour is verified against the white canvas (#ffffff) with WCAG:
 *   text fills #1a1a1a = 17.40:1, #333333 = 12.63:1 (well above 4.5:1);
 *   git strokes #1f77b4 4.82, #d62728 5.02, #2e7d32 5.13, #9467bd 4.26,
 *   #c55a11 4.33, #0e7490 5.36, #b5179e 5.86, #495057 8.18 (all > 3:1 for a
 *   graphical stroke), each branch label using the black/white that reads best
 *   on its own branch colour (>= 4.5:1). Exported for unit testing.
 */
export function buildMermaidLightThemeVariables(): Record<string, any> {
    return {
        // Generic text / lines — dark on the white canvas.
        textColor: '#1a1a1a',
        lineColor: '#333333',          // mindmap ribbons + generic edges
        titleColor: '#1a1a1a',
        labelColor: '#1a1a1a',

        // Journey: task labels defaulted to white on pale-yellow bands.
        taskTextColor: '#1a1a1a',
        taskTextDarkColor: '#1a1a1a',
        taskTextLightColor: '#1a1a1a',
        taskTextOutsideColor: '#1a1a1a',
        actorTextColor: '#1a1a1a',

        // quadrantChart: point labels + quadrant titles/axes defaulted to white.
        quadrantPointTextFill: '#1a1a1a',
        quadrant1TextFill: '#1a1a1a',
        quadrant2TextFill: '#1a1a1a',
        quadrant3TextFill: '#1a1a1a',
        quadrant4TextFill: '#1a1a1a',
        quadrantXAxisTextFill: '#333333',
        quadrantYAxisTextFill: '#333333',
        quadrantTitleFill: '#1a1a1a',

        // gitGraph: pale default branch palette -> saturated, white-legible.
        git0: '#1f77b4', git1: '#d62728', git2: '#2e7d32', git3: '#9467bd',
        git4: '#c55a11', git5: '#0e7490', git6: '#b5179e', git7: '#495057',
        gitBranchLabel0: '#ffffff', gitBranchLabel1: '#ffffff',
        gitBranchLabel2: '#ffffff', gitBranchLabel3: '#000000',
        gitBranchLabel4: '#000000', gitBranchLabel5: '#ffffff',
        gitBranchLabel6: '#ffffff', gitBranchLabel7: '#ffffff',
        tagLabelColor: '#1a1a1a',

        // xychart-beta: default plotColorPalette leads with pale #ECECFF, so the
        // first bar series is near-white on white (~1.15:1, w1-14). Override ONLY
        // plotColorPalette with the white-legible palette (bar #1f77b4 4.82:1,
        // line #d62728 5.02:1 on #ffffff — graphical > 3:1); every other xyChart
        // subkey (axis/title/background) is omitted so mermaid falls back to its
        // primaryTextColor/background defaults, which are already dark-on-white.
        xyChart: {
            plotColorPalette:
                '#1f77b4,#d62728,#2e7d32,#9467bd,#c55a11,#0e7490,#b5179e,#495057',
        },
    };
}

/**
 * D-293 (mermaid-w3-07 / w1-10 dark): explicit DARK gitGraph palette.
 *
 * The dark `mermaid.initialize` themeVariables block sets a generic edge
 * `lineColor` and node palette but NEVER the per-branch git palette
 * (git0..git7, gitBranchLabel*, commitLabel*). mermaid's built-in `dark` theme
 * then derives the branch colours self-referentially from a single base hue,
 * so every branch reads as a near-identical muted slate (w3-07 "flattened to
 * near-identical hues"), and the commit-id label defaults to a dark-navy on a
 * mid-slate chip (~1.69:1, w1-10). The universal visibility pass compounded
 * this by repainting the low-contrast branch strokes to the single theme teal.
 *
 * We pin a saturated, canvas-resolved branch palette (mirroring the working
 * LIGHT `buildMermaidLightThemeVariables` git block, re-toned brighter for the
 * dark canvas) plus legible commit/tag label colours. Only merged for dark +
 * gitgraph, so no other diagram type is affected and the light render — which
 * already passes — is byte-for-byte unchanged.
 *
 * Contrast (computed, WCAG):
 *   branch colours vs dark canvas: git0 5.29 · git1 3.91 · git2 6.20 · git3
 *     4.24 · git4 5.63 · git5 5.27 · git6 5.24 · git7 5.86 (all >= 3:1 graphic
 *     floor on #2e3440, and higher on #1e1e1e); on the light canvas the render
 *     uses the light palette so this block never applies there.
 *   branch label (#000000) on each branch chip: >= 6.57:1 (text floor 4.5).
 *   commit-id label #eceff4 on chip #1f2430: 13.46:1 (was ~1.69:1).
 *   tag label #1a1a1a on #b5cea8: 10.24:1.
 */
export function buildGitGraphDarkThemeVariables(): Record<string, string> {
    return {
        // Saturated, dark-legible per-branch stroke palette.
        git0: '#61afef', git1: '#e06c75', git2: '#98c379', git3: '#c678dd',
        git4: '#e5a04c', git5: '#56b6c2', git6: '#ff79c6', git7: '#abb2bf',
        // Branch labels sit on the bright branch chip -> black text clears 4.5:1.
        gitBranchLabel0: '#000000', gitBranchLabel1: '#000000',
        gitBranchLabel2: '#000000', gitBranchLabel3: '#000000',
        gitBranchLabel4: '#000000', gitBranchLabel5: '#000000',
        gitBranchLabel6: '#000000', gitBranchLabel7: '#000000',
        // Commit-id label: near-white on an opaque dark chip (was dark-on-slate).
        commitLabelColor: '#eceff4',
        commitLabelBackground: '#1f2430',
        // Tag label: dark text on a pale-green plate with a teal border.
        tagLabelColor: '#1a1a1a',
        tagLabelBackground: '#b5cea8',
        tagLabelBorder: '#88c0d0',
    };
}

// Add mermaid to window for TypeScript
declare global {
    interface Window {
        mermaid: any;
        __mermaidLoaded?: boolean;
        __mermaidLoading?: Promise<any>;
    }
}

// Define the specification for Mermaid diagrams
export interface MermaidSpec {
    type: 'mermaid';
    isStreaming?: boolean;
    isMarkdownBlockClosed?: boolean;
    forceRender?: boolean;
    definition: string;
    theme?: 'default' | 'dark' | 'neutral' | 'forest'; // Optional theme override
}

// Type guard to check if a spec is for Mermaid
const isMermaidSpec = (spec: any): spec is MermaidSpec => {
    // Handle JSON-wrapped mermaid specs
    if (typeof spec === 'object' && spec !== null && (spec.type === 'mermaid' || spec.chart === 'mermaid') && spec.definition) {
        return typeof spec.definition === 'string' && spec.definition.trim().length > 0;
    }

    // Handle direct mermaid spec objects
    return (
        typeof spec === 'object' &&
        spec !== null &&
        (spec.type === 'mermaid' || spec.chart === 'mermaid') &&
        typeof spec.definition === 'string' &&
        spec.definition.trim().length > 0
    );
};

/** Return true when the automatic post-render contrast pass should run. */
export function shouldEnhanceMermaidVisibility(definition: string): boolean {
    const explicitTextColor = /(?:^|\n)\s*(?:classDef\s+\S+|style\s+\S+)\s+[^\n]*\bcolor\s*:/i;
    return !explicitTextColor.test(definition);
}

const SCALE_CONFIG = {
    TARGET_FONT_SIZE: 14,   // Target font size in pixels
    MIN_FONT_SIZE: 12,      // Minimum font size in pixels
    MAX_FONT_SIZE: 18,      // Maximum font size in pixels
    MAX_SCALE: 3.0,         // Maximum scale factor
    MIN_SCALE: 0.3          // Minimum scale factor (for very large text)
};

// Global render queue to serialize Mermaid rendering and prevent conflicts
class MermaidRenderQueue {
    private queue: Array<() => Promise<any>> = [];
    private isProcessing = false;
    private pendingDiagrams = new Set<string>(); // Track diagrams being processed

    async enqueue<T>(renderFn: () => Promise<T>): Promise<T> {
        return new Promise((resolve, reject) => {
            this.queue.push(async () => {
                try {
                    const result = await renderFn();
                    resolve(result);
                } catch (error) {
                    reject(error);
                }
            });

            this.processQueue();
        });
    }

    private async processQueue() {
        if (this.isProcessing || this.queue.length === 0) return;

        // Minimal Safari throttling - only for very large queues
        const isSafari = typeof navigator !== 'undefined' && /^((?!chrome|android).)*safari/i.test(navigator.userAgent);
        if (isSafari && this.queue.length > 10) {
            console.log('🍎 SAFARI-THROTTLE: Large queue detected, processing with delay');
            await new Promise(resolve => setTimeout(resolve, 50)); // Reduced from 200ms to 50ms
        }

        this.isProcessing = true;

        console.log(`🎯 RENDER-QUEUE: Processing item ${this.queue.length} remaining`);

        const renderFn = this.queue.shift()!;
        await renderFn();
        this.isProcessing = false;

        this.processQueue(); // Process next item
    }
}

const renderQueue = new MermaidRenderQueue();

/**
 * Lazy load mermaid library
 * Uses dynamic import with timeout, falls back to CDN if chunk loading fails
 */
async function loadMermaid(): Promise<any> {
    if (typeof window !== 'undefined' && window.__mermaidLoaded && window.mermaid) {
        return window.mermaid;
    }

    // If already loading, wait for it
    if (window.__mermaidLoading) {
        return await window.__mermaidLoading;
    }

    // Helper: Import with timeout protection
    const importMermaidWithTimeout = (timeoutMs: number = 3000): Promise<any> => {
        return Promise.race([
            import(/* webpackChunkName: "mermaid" */ 'mermaid'),
            new Promise((_, reject) =>
                setTimeout(() => reject(new Error(`Import timeout after ${timeoutMs}ms`)), timeoutMs)
            )
        ]);
    };

    // Helper: Load mermaid from CDN as fallback
    const loadFromCDN = (): Promise<any> => {
        return new Promise((resolve, reject) => {
            console.warn('⚠️ MERMAID-LOAD: Loading from CDN fallback');

            // Check if already loaded by CDN in a previous attempt
            if (window.mermaid && typeof window.mermaid.render === 'function') {
                console.log('✅ MERMAID-LOAD: Already available on window');
                return resolve({ default: window.mermaid });
            }

            const script = document.createElement('script');
            script.src = 'https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js';

            script.onload = () => {
                console.log('✅ MERMAID-LOAD: Loaded from CDN successfully');
                if (window.mermaid && typeof window.mermaid.render === 'function') {
                    // Wrap in module-like object to match import() structure
                    resolve({ default: window.mermaid });
                } else {
                    reject(new Error('Mermaid script loaded but window.mermaid not available'));
                }
            };

            script.onerror = (e) => {
                console.error('❌ MERMAID-LOAD: CDN fallback also failed:', e);
                reject(new Error('Failed to load Mermaid from CDN'));
            };

            document.head.appendChild(script);
        });
    };

    // Start loading with timeout protection and CDN fallback
    window.__mermaidLoading = importMermaidWithTimeout(3000)
        .catch(error => {
            console.error('❌ MERMAID-LOAD: Chunk import failed:', error.message);
            // Fall back to CDN
            return loadFromCDN();
        })
        .then(module => {
            console.log('✅ MERMAID-LOAD: Module loaded successfully');
            const mermaid = module.default;
            initMermaidSupport(mermaid);
            window.mermaid = mermaid;
            window.__mermaidLoaded = true;
            return mermaid;
        });

    return await window.__mermaidLoading;
}

export const mermaidPlugin: D3RenderPlugin = {
    name: 'mermaid-renderer',
    priority: 5,
    sizingConfig: {
        sizingStrategy: 'auto-expand',
        needsDynamicHeight: true,
        needsOverflowVisible: true,
        observeResize: true,
        containerStyles: {
            width: '100%',
            maxWidth: '100%',
            height: 'auto',
            minHeight: 'auto',
            overflow: 'hidden',
            // Safari-specific: ensure container can grow to accommodate scaled content
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center'
        }
    },

    canHandle: (spec: any): boolean => {
        // Handle JSON-wrapped mermaid specs like {"type": "mermaid", "definition": "..."}
        if (typeof spec === 'object' && spec !== null && (spec.type === 'mermaid' || spec.chart === 'mermaid') && spec.definition) {
            return typeof spec.definition === 'string' && spec.definition.trim().length > 0;
        }

        // Handle direct mermaid spec objects
        if (isMermaidSpec(spec)) {
            return true;
        }

        return false;
    },

    // Helper to check if a mermaid definition is complete
    isDefinitionComplete: (definition: string): boolean => {
        if (!definition || definition.trim().length === 0) return false;

        // Check for basic completeness indicators
        const lines = definition.trim().split('\n');
        if (lines.length < 2) return false;

        const firstLine = lines[0].trim().toLowerCase();

        // Check for specific diagram types
        if (firstLine.startsWith('graph') || firstLine.startsWith('flowchart')) {
            // For flowcharts, check for balanced braces
            const openBraces = definition.split('{').length - 1;
            const closeBraces = definition.split('}').length - 1;
            return openBraces === closeBraces && openBraces > 0;
        }

        // For other diagram types, check if there are at least a few lines
        // and the definition doesn't end with an incomplete code block
        return lines.length >= 3 && !definition.endsWith('```');
    },


    render: async (container: HTMLElement, d3: any, spec: MermaidSpec, isDarkMode: boolean): Promise<void> => {
        // Lazy load mermaid library
        const mermaid = await loadMermaid();
        if (!mermaid) {
            throw new Error('Failed to load mermaid library');
        }

        // Skip queue for Safari to avoid delays - Mermaid can handle concurrent renders
        const isSafari = typeof navigator !== 'undefined' && /^((?!chrome|android).)*safari/i.test(navigator.userAgent);
        if (isSafari) {
            // Add Safari warning to Mermaid diagrams specifically
            console.warn('🍎 SAFARI-MERMAID: Rendering Mermaid diagram on Safari - compatibility issues expected');
            const result = await renderSingleDiagram(container, d3, spec, isDarkMode, mermaid);
            // Add a small notice that rendering may be degraded
            console.warn('🍎 SAFARI-MERMAID: Mermaid diagram rendered on Safari. Visual artifacts or performance issues may occur.');
            return result;
        } else {
            // Use render queue for other browsers to prevent conflicts
            return renderQueue.enqueue(async () => {
                return await renderSingleDiagram(container, d3, spec, isDarkMode, mermaid);
            });
        }
    }
};

async function renderSingleDiagram(container: HTMLElement, d3: any, spec: MermaidSpec, isDarkMode: boolean, mermaid: any): Promise<void> {
    console.log(`🎯 MERMAID SINGLE RENDER with spec:`, spec);
    console.log(`Mermaid plugin render called with spec type: ${spec.type}, definition length: ${spec.definition.length}`);
    console.log('📊 DIAGRAM PREVIEW:', spec.definition.substring(0, 100).replace(/\n/g, '\\n'));
    console.log('🔍 HAS HTML TAGS:', spec.definition.includes('<br'));

    let renderSuccessful = false;
    try {
        container.innerHTML = '';

        // Ensure CSS dark-mode selectors can reach this container's contents
        container.classList.add('mermaid-container');

        // Add loading spinner while mermaid renders
        const loadingSpinner = document.createElement('div');
        loadingSpinner.className = 'mermaid-loading-spinner';
        loadingSpinner.style.cssText = `
                display: flex;
                flex-direction: column;
                align-items: center;
                justify-content: center;
                padding: 2em;
                min-height: 150px;
                width: 100%;
            `;

        // Add spinner animation
        loadingSpinner.innerHTML = `
                <div style="
                    border: 4px solid rgba(0, 0, 0, 0.1);
                    border-top: 4px solid ${isDarkMode ? '#4cc9f0' : '#3498db'};
                    border-radius: 50%;
                    width: 40px;
                    height: 40px;
                    animation: mermaid-spin 1s linear infinite;
                    margin-bottom: 15px;
                "></div>
                <div style="font-family: system-ui, -apple-system, sans-serif; color: ${isDarkMode ? '#eceff4' : '#333333'};">
                    Rendering diagram...
                </div>
            `;

        container.appendChild(loadingSpinner);

        // This allows the content to display as highlighted code during streaming
        if (!spec.isMarkdownBlockClosed && !spec.forceRender) {
            // If we already have a rendered diagram, preserve it — a late
            // re-render with stale flags must not destroy finished work.
            if (container.querySelector('svg') || container.querySelector('.diagram-actions')) {
                return;
            }
            console.log('Mermaid: Markdown block still open, letting content display as code');
            container.innerHTML = '';
            return; // Exit early - let markdown renderer handle the streaming content
        }

        // Only proceed with rendering when we have a complete definition
        if (!spec.definition || spec.definition.trim().length < 10) {
            console.log('Mermaid: Definition too short, waiting for more content');
            return; // Exit early and wait for complete definition
        }
        // Initialize mermaid with graph-specific settings

        // Extract actual content from YAML wrapper if present, but don't do other preprocessing
        // The enhanced render function will handle all preprocessing
        let rawDefinition: string;

        // Handle JSON-wrapped specs vs direct definition strings
        if (typeof spec === 'object' && spec.definition) {
            let def = spec.definition;

            // Handle double-wrapped JSON definitions
            if (typeof def === 'string' && def.trim().startsWith('{')) {
                try {
                    const parsed = JSON.parse(def);
                    if (parsed.type === 'mermaid' && parsed.definition) {
                        rawDefinition = parsed.definition;
                        console.log('Extracted definition from double-wrapped JSON');
                    } else {
                        rawDefinition = extractDefinitionFromYAML(def, 'mermaid');
                        console.log('Used YAML extraction from string definition');
                    }
                } catch {
                    rawDefinition = extractDefinitionFromYAML(def, 'mermaid');
                    console.log('JSON parse failed, using YAML extraction');
                }
            } else {
                rawDefinition = extractDefinitionFromYAML(def, 'mermaid');
            }
        } else if (typeof spec === 'string') {
            rawDefinition = extractDefinitionFromYAML(spec, 'mermaid');
        } else {
            throw new Error('Invalid mermaid spec: no definition found');
        }

        // CRITICAL DEBUG: Log what we're about to send to Mermaid
        console.log('🔧 MERMAID-DEBUG: About to render with rawDefinition:', {
            type: typeof rawDefinition,
            length: rawDefinition?.length || 0,
            firstChar: rawDefinition?.charAt(0) || 'N/A',
            first50: rawDefinition?.substring(0, 50) || 'N/A',
            startsWithGantt: rawDefinition?.trim().startsWith('gantt') || false
        });

        console.log('Raw definition (first 200 chars):', rawDefinition.substring(0, 200));

        // Detect diagram type
        const lines = rawDefinition.trim().split('\n');
        let firstLine = lines[0]?.trim() || '';

        // Skip YAML frontmatter if present
        if ((firstLine === '---' || firstLine.startsWith('%%')) && lines.length > 2) {
            firstLine = lines.find((line, idx) => idx > 0 && line.trim() && line.trim() !== '---')?.trim() || '';
        }

        // Skip %%{init:...}%% directives to find actual diagram type
        if (firstLine.startsWith('%%')) {
            firstLine = lines.find(line => {
                const t = line.trim();
                return t && !t.startsWith('%%');
            })?.trim() || '';
        }
        const diagramType = firstLine.replace(/^(\w+).*$/, '$1').toLowerCase();

        // Create a guaranteed unique ID that won't conflict with other diagrams
        const containerId = container.id || container.className || 'mermaid';
        const mermaidId = `${containerId}-${Date.now()}-${Math.random().toString(16).substring(2, 10)}`;

        mermaid.initialize({
            startOnLoad: false,
            theme: isDarkMode ? 'dark' : 'default',
            securityLevel: 'loose',
            fontFamily: '"Arial", sans-serif',
            fontSize: 14,
            themeVariables: (isDarkMode ? Object.assign({
                // High contrast dark theme
                primaryColor: '#88c0d0',
                primaryTextColor: '#eceff4', // Light text for dark backgrounds
                primaryBorderColor: '#88c0d0',
                lineColor: '#88c0d0',
                secondaryColor: '#5e81ac',
                tertiaryColor: '#2e3440',

                // Text colors
                textColor: '#eceff4',
                loopTextColor: '#eceff4',

                // Node colors
                mainBkg: '#3b4252',
                secondBkg: '#434c5e',
                nodeBorder: '#88c0d0',

                // Edge colors
                edgeLabelBackground: '#4c566a',

                // Contrast colors
                altBackground: '#2e3440',

                // Flowchart specific
                nodeBkg: '#3b4252',
                clusterBkg: '#2e3440',
                titleColor: '#88c0d0',

                // Class diagram specific
                classText: '#eceff4',

                // State diagram specific
                labelColor: '#eceff4',

                // Sequence diagram specific
                actorBkg: '#4c566a',
                actorBorder: '#88c0d0',
                activationBkg: '#5e81ac',

                // Gantt chart specific
                sectionBkgColor: '#3b4252',
                altSectionBkgColor: '#434c5e',
                gridColor: '#eceff4',
                todayLineColor: '#88c0d0'
            }, diagramType === 'timeline' ? buildTimelineDarkThemeVariables() : {}, (diagramType === 'gitgraph' || diagramType === 'git') ? buildGitGraphDarkThemeVariables() : {}, buildSequenceNoteDarkThemeVariables(), buildPieThemeVariables(true)) : Object.assign({}, buildMermaidLightThemeVariables(), buildPieThemeVariables(false))),
            flowchart: {
                htmlLabels: true,
                curve: 'basis',
                padding: 20,
                nodeSpacing: 60,
                rankSpacing: 50,
                diagramPadding: 8,
            },
            sequence: {
                diagramMarginX: 50,
                diagramMarginY: 30,
                actorMargin: 50,
                width: 150,
                height: 65,
                boxMargin: 10,
                boxTextMargin: 5,
                noteMargin: 10,
                messageMargin: 35,
                mirrorActors: true,
                bottomMarginAdj: 1,
                useMaxWidth: true,
            },
            gantt: {
                titleTopMargin: 25,
                barHeight: 20,
                barGap: 4,
                topPadding: 50,
                leftPadding: 75,
                gridLineStartPadding: 35,
                fontSize: 11,
                sectionFontSize: 11,
                numberSectionStyles: 4,
                axisFormat: '%Y-%m-%d',
                topAxis: false,
            },
        });

        // Render the diagram
        console.log(`Attempting to render mermaid with ID: ${mermaidId}`);

        let svg: string;
        let renderError: Error | null = null;
        try {
            const result = await mermaid.render(mermaidId, rawDefinition);

            // Check if result is valid
            if (!result || typeof result !== 'object') {
                console.error('Invalid mermaid render result:', result);
                throw new Error('Mermaid render returned invalid result');
            }
            console.log('Mermaid render result:', result);
            svg = result.svg;

            // Check if we got a valid SVG
            if (!svg || svg.trim() === '') {
                console.error('Empty SVG returned from Mermaid render - likely parse error');
                console.error('Definition that failed (first 500 chars):', rawDefinition.substring(0, 500));

                // Check if there was a parsing error that Mermaid swallowed
                // This is a common issue where Mermaid returns empty result instead of throwing
                throw new Error('Mermaid parsing failed - empty SVG returned. This usually indicates syntax errors in the diagram definition.');
            }

            // Additional validation - check if SVG contains actual content
            if (svg.length < 100 || !svg.includes('<svg')) {
                console.error('SVG appears to be malformed or too short:', svg.substring(0, 200));
                throw new Error('Mermaid returned malformed SVG - likely parsing error');
            }

            renderSuccessful = true;
            console.log(`Mermaid render successful, got SVG of length: ${svg.length}`);

            // Check if the SVG contains an error message
            if (svg.includes('syntax error') || svg.includes('Parse error')) {
                throw new Error('Mermaid syntax error in diagram');
            }
        } catch (renderError) {
            console.error('Error rendering mermaid diagram:', renderError);

            // Normalize for better error reporting
            const normalizedError = renderError instanceof Error ? renderError : new Error(String(renderError));

            // Log the specific error details
            if (normalizedError instanceof Error) {
                console.error('Error name:', normalizedError.name);
                console.error('Error message:', normalizedError.message);
                console.error('Error stack:', normalizedError.stack);
            }

            console.error('Failed definition (first 500 chars):', rawDefinition.substring(0, 500));
            console.error('Raw definition that failed:', rawDefinition.substring(0, 500));
            throw normalizedError;
        }

        // Add the animation keyframes if they don't exist yet
        if (!document.querySelector('#mermaid-spinner-keyframes')) {
            const keyframes = document.createElement('style');
            keyframes.id = 'mermaid-spinner-keyframes';
            keyframes.textContent = `
                    @keyframes mermaid-spin {
                        0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); }
                    }
                `;
            document.head.appendChild(keyframes);
        }

        // Create wrapper div
        const wrapper = document.createElement('div');
        wrapper.className = 'mermaid-wrapper';
        wrapper.style.cssText = `
                width: 100%;
                max-width: 100%;
                overflow: auto;
                padding: 1em;
                display: flex;
                justify-content: center;
            `;
        wrapper.innerHTML = svg;

        // Remove the loading spinner
        try {
            if (loadingSpinner && loadingSpinner.parentNode === container) {
                container.removeChild(loadingSpinner);
            }
        } catch (e) {
            console.warn('Could not remove loading spinner (this is normal for multiple renders):', e instanceof Error ? e.message : String(e));
        }

        // CRITICAL: Remove old wrapper before adding new one to prevent duplicate diagrams
        const oldWrapper = container.querySelector('.mermaid-wrapper');
        if (oldWrapper) {
            console.log('🧹 REMOVING old wrapper before adding new one');
            container.removeChild(oldWrapper);
        }

        // Add wrapper to container
        container.appendChild(wrapper);

        // Get the SVG element after it's in the DOM
        const svgElement = wrapper.querySelector('svg');
        if (!svgElement) {
            throw new Error('Failed to get SVG element after rendering');
        }

        // Trim the viewBox to the actual content bounding box.
        // Mermaid's layout engine sometimes allocates a viewBox much wider
        // than the rendered content, leaving large empty margins that waste
        // space when the SVG is scaled to fit the container.
        try {
            const svgG = svgElement as unknown as SVGGraphicsElement;
            const bbox = svgG.getBBox();
            const vb = svgElement.getAttribute('viewBox');
            // D-287/D-145: reclaim excess viewBox on EITHER axis (width OR
            // height). Mermaid over-allocates height as often as width; the
            // old width-only gate left the oversize-height canvas untrimmed,
            // shrinking real content to a sub-pixel sliver on capture.
            const trim = computeViewBoxTrim(vb, bbox);
            if (trim.shouldTrim && trim.newViewBox) {
                svgElement.setAttribute('viewBox', trim.newViewBox);
                console.log(`📐 VIEWBOX-TRIM: ${vb} → ${trim.newViewBox} (reclaimed ${trim.reclaimedWidthPct.toFixed(0)}% width, ${trim.reclaimedHeightPct.toFixed(0)}% height)`);
            }
        } catch (e) {
            console.warn('VIEWBOX-TRIM: Could not read SVG bounding box:', e);
        }


        if (!renderSuccessful) return;

        // UNIVERSAL FIX: Apply centralized visibility enhancement with DELAYED execution.
        // Preserve author-specified text colors from either classDef or per-node style
        // declarations instead of overriding them with inferred contrast colors.
        // D-293 (mermaid-w3-04 sankey ribbons, w1-10 gitGraph branch lines):
        // sankey link ribbons encode flow MAGNITUDE as their stroke-width and
        // gitGraph branch lines encode per-branch IDENTITY as their stroke
        // colour/width. The universal line pass (enhanceSVGVisibility FIX 3)
        // treats every path as a mere connector: it repaints low-contrast
        // strokes to the single theme colour and rewrites stroke-width to a
        // flat ~1.5-2px, destroying the graphical encoding (ribbons collapse
        // to ~1px near-monochrome, branch lines drop 3px->1px). Exempt those
        // data-bearing paths so their author/mermaid stroke survives. Gated to
        // dark: these specs pass in light and light stays byte-unchanged.
        let lineSkipSelectors: string[] = [];
        if (isDarkMode) {
            if (diagramType === 'sankey') {
                // sankey-beta ribbons are <path>; nodes are <rect> (untouched).
                lineSkipSelectors = ['path'];
            } else if (diagramType === 'gitgraph' || diagramType === 'git') {
                // gitGraph branch/merge lines are <path>/<line>; commit dots
                // are <circle> and labels are <text> (both still remediated).
                lineSkipSelectors = ['path', 'line'];
            }
        }
        if (shouldEnhanceMermaidVisibility(rawDefinition)) {
            const runVisibilityFix = (phase: string) => {
                console.log(`🎨 VISIBILITY-FIX (${phase}): Starting universal enhancement`);
                // D-154: mermaid renders text on arbitrary node/note/section
                // fills, so text needs the WCAG 4.5:1 floor rather than the 3:1
                // graphic floor — a mid-tone fill (mindmap ROOT circle w1-11,
                // sequence NOTE plate w1-03) can otherwise leave a ~3.1:1 label
                // that clears 3.0 and is never remediated. Only mermaid opts in;
                // drawio/graphviz keep the 3.0 default.
                const result = enhanceSVGVisibility(svgElement, isDarkMode, { debug: true, textMinContrast: 4.5, skipSelectors: lineSkipSelectors });
                console.log(`🎨 VISIBILITY-FIX (${phase}): Complete`);

                console.group('🎨 MERMAID-CONTRAST: Visibility Enhancement Results');
                console.log(`Phase: ${phase}`);
                console.log(`Diagram Type: "${diagramType}"`);
                console.log(`Dark Mode: ${isDarkMode}`);
                console.log(`Text Fixed:`, result.textFixed);
                console.log(`Shapes Fixed:`, result.shapesFixed);
                console.log(`Lines Fixed:`, result.linesFixed);
                console.log(`Details:`, result);

                console.groupEnd();
            };
            // Run synchronously so contrast fixes cannot be lost when a
            // re-render races the delayed timer. The SVG's embedded <style>
            // is already applied at this point, so background detection via
            // getComputedStyle works. Re-run at 500ms (idempotent) to catch
            // late CSS/theme mutations.
            runVisibilityFix('immediate');
            setTimeout(() => runVisibilityFix('delayed'), 500);
        } else {
            console.log('🎨 VISIBILITY-FIX: Skipping post-processing - diagram has explicit color styles');
        }

        // POST-RENDER (G-9c6f76 / D-294, w3-11): fan out quadrantChart data
        // points that mermaid placed at (near-)identical coordinates so their
        // markers and labels stop stacking into an unreadable smear (e.g. five
        // points at the [0.5,0.5] centre). Placement-only and theme-independent,
        // so it runs regardless of the contrast-enhancement opt-in and cannot be
        // undone by a re-render (idempotent: distinct points are never moved).
        if (diagramType === 'quadrantchart' || diagramType === 'quadrant') {
            const runDodge = (phase: string) => {
                try {
                    const n = dodgeQuadrantPointCollisions(svgElement);
                    if (n) console.log(`📍 QUADRANT-DODGE (${phase}): separated ${n} colliding point(s)`);
                } catch (e) { console.warn('QUADRANT-DODGE failed:', e); }
            };
            runDodge('immediate');
            setTimeout(() => runDodge('delayed'), 520);
        }

        // POST-RENDER (D-161 / G-40): re-apply explicit `linkStyle` edge strokes.
        // In dark, the visibility pass repaints every edge with the theme
        // lineColor, silently discarding deliberately colour-coded edges. Run
        // AFTER the immediate + delayed (500ms) visibility fixes so it wins, and
        // honour-then-lighten dark-unfriendly author strokes to clear 3:1.
        // Light is untouched (the helper returns 0 there — strokes already kept).
        if (isDarkMode && (diagramType === 'flowchart' || diagramType === 'graph')) {
            const reapplyLinks = () => {
                try {
                    const n = reapplyLinkStyleStrokes(svgElement, rawDefinition, true);
                    if (n) console.log(`🎨 LINKSTYLE-DARK-REAPPLY: restored ${n} edge stroke(s)`);
                } catch (e) { console.warn('LINKSTYLE-DARK-REAPPLY failed:', e); }
            };
            reapplyLinks();
            setTimeout(reapplyLinks, 650);
        }

        // POST-RENDER (G-632224 / D-295): keep node/block box borders and edge
        // strokes visible against the theme canvas when an author style/init
        // palette sets a fill+border (or forces #fff fills + #f8f8f8 lines) that
        // matches the surface. Runs for BOTH themes and INDEPENDENTLY of
        // shouldEnhanceMermaidVisibility — a definition with an explicit `color:`
        // (w3-08) skips the universal visibility pass, so this is the only pass
        // that reaches its dissolved borders. Theme-resolved outline
        // (#333333 on light 12.63:1, #e6e6e6 on dark 13.36:1); only repaints a
        // shape that has actually dissolved into the canvas.
        if (diagramType === 'flowchart' || diagramType === 'graph' || diagramType === 'block') {
            const fixBorders = () => {
                try {
                    const n = ensureShapeBordersAgainstCanvas(svgElement, isDarkMode);
                    if (n) console.log(`🖼️ BOX-BORDER-CANVAS: repainted ${n} dissolved border/edge stroke(s)`);
                } catch (e) { console.warn('BOX-BORDER-CANVAS failed:', e); }
            };
            fixBorders();
            setTimeout(fixBorders, 650);
        }

        // POST-RENDER (D-170 / G-40): ensure gantt date gridlines paint BEHIND the
        // task bars (mermaid can emit the grid group after the bars, slicing them),
        // and recolour under-contrast crit-task labels to black (both themes).
        if (diagramType === 'gantt') {
            const fixGantt = () => {
                try {
                    if (moveGanttGridBehind(svgElement)) {
                        console.log('📊 GANTT-GRID-ZORDER: moved date gridlines behind task bars');
                    }
                    recolorGanttCritLabels(svgElement, isDarkMode);
                } catch (e) { console.warn('GANTT-GRID-ZORDER failed:', e); }
            };
            fixGantt();
            setTimeout(fixGantt, 650);
        }

        // POST-RENDER: Reroute skip edges that cut through intermediate nodes
        // Mermaid/dagre draws feedback loops as straight lines through nodes;
        // this replaces those paths with arced bezier curves.
        setTimeout(() => {
            if (shouldRerouteEdges(svgElement)) {
                console.log('🔀 EDGE-REROUTE: Diagram qualifies for skip-edge rerouting');
                const rerouteResult = rerouteSkipEdges(svgElement);
                console.log('🔀 EDGE-REROUTE: Complete', {
                    total: rerouteResult.totalEdges,
                    rerouted: rerouteResult.reroutedEdges,
                    skipped: rerouteResult.skippedEdges,
                    details: rerouteResult.details
                });
            }
        }, 600); // Run after visibility enhancement (500ms)

        // POST-RENDER: Fix gantt axis labels when year-offset was applied
        // The preprocessor shifted years by +N to work around JS Date limitations;
        // subtract the offset from every axis tick label to show original years.
        const yearOffsetMatch = rawDefinition.match(/gantt-year-offset:\s*(\d+)/);
        if (yearOffsetMatch && diagramType === 'gantt') {
            const offset = parseInt(yearOffsetMatch[1], 10);
            console.log(`📅 GANTT-YEAR-FIX: Correcting axis labels by -${offset}`);
            setTimeout(() => {
                // Mermaid renders axis ticks as <text> inside <g class="tick">
                const ticks = svgElement.querySelectorAll('.tick text, .x text, .xAxis text');
                let fixed = 0;
                ticks.forEach((el: Element) => {
                    const txt = el.textContent?.trim() ?? '';
                    const yearMatch = txt.match(/^(\d{4})$/);
                    if (yearMatch) {
                        const original = parseInt(yearMatch[1], 10) - offset;
                        el.textContent = String(original);
                        fixed++;
                    }
                });
                // Also check for any text elements that look like offset years
                if (fixed === 0) {
                    svgElement.querySelectorAll('text').forEach((el: Element) => {
                        const txt = el.textContent?.trim() ?? '';
                        if (/^\d{4}$/.test(txt) && parseInt(txt, 10) >= offset) {
                            el.textContent = String(parseInt(txt, 10) - offset);
                        }
                    });
                }
                console.log(`📅 GANTT-YEAR-FIX: Fixed ${fixed} axis labels`);
            }, 300);
        }

        // POST-RENDER (G-14a672 / D-152): repair the upstream pie renderer's
        // at-scale legend clipping, title occlusion and slice-label collision.
        // Runs AFTER the viewBox-trim (which only ever shrinks) and BEFORE
        // responsive scaling, so scaling operates on the corrected viewBox.
        if (diagramType === 'pie') {
            // D-157: eliminate palette recycling past mermaid's 12-entry pie
            // limit by giving each slice its own canvas-aware colour BEFORE the
            // layout fix runs.
            try {
                const rc = recolorPieSlicesAtScale(svgElement, isDarkMode);
                if (rc.recolored > 0) {
                    console.log(`🥧 PIE-RECOLOR: ${rc.recolored}/${rc.sliceCount} slices given distinct canvas-aware fills`);
                }
            } catch (e) { console.warn('PIE-RECOLOR failed:', e); }
            try {
                const r = fixPieLayoutAtScale(svgElement);
                if (r.isPie && (r.viewBoxExpanded || r.labelsRemoved)) {
                    console.log(`🥧 PIE-LAYOUT-FIX: slices=${r.sliceCount} labelsRemoved=${r.labelsRemoved} viewBox=${r.newViewBox ?? 'unchanged'}`);
                }
            } catch (e) { console.warn('PIE-LAYOUT-FIX failed:', e); }
        }

        // Apply unified responsive scaling for all browsers
        applyUnifiedResponsiveScaling(container, svgElement, isDarkMode);

        // Add action buttons
        const actionsContainer = document.createElement('div');
        actionsContainer.className = 'diagram-actions';

        // Add Open button
        const openButton = document.createElement('button');
        openButton.innerHTML = '↗️ Open';
        openButton.className = 'diagram-action-button mermaid-open-button';
        openButton.onclick = () => {
            // Get the SVG element
            const svgElement = wrapper.querySelector('svg');
            if (!svgElement) return;

            // Get the SVG dimensions
            const svgGraphics = svgElement as unknown as SVGGraphicsElement;
            let width = 600;
            let height = 400;

            try {
                // Try to get the bounding box
                const bbox = svgGraphics.getBBox();
                width = Math.max(bbox.width + 50, 400); // Add padding, minimum 400px
                height = Math.max(bbox.height + 100, 300); // Add padding, minimum 300px
            } catch (e) {
                console.warn('Could not get SVG dimensions, using defaults', e);
            }

            // Create a new SVG with proper XML declaration and doctype
            const svgData = new XMLSerializer().serializeToString(svgElement);

            // Create an HTML document that will display the SVG responsively
            const htmlContent = `
                <!DOCTYPE html>
                <html>
                <head>
                    <meta charset="UTF-8">
                    <title>Mermaid Diagram</title>
                    <style>
                        :root {
                            --bg-color: #f8f9fa;
                            --text-color: #212529;
                            --toolbar-bg: #f1f3f5;
                            --toolbar-border: #dee2e6;
                            --button-bg: #4361ee;
                            --button-hover: #3a0ca3;
                        }
                        
                        [data-theme="dark"] {
                            --bg-color: #212529;
                            --text-color: #f8f9fa;
                            --toolbar-bg: #343a40;
                            --toolbar-border: #495057;
                            --button-bg: #4361ee;
                            --button-hover: #5a72f0;
                        }
                        
                        body {
                            margin: 0;
                            padding: 0;
                            display: flex;
                            flex-direction: column;
                            height: 100vh;
                            background-color: var(--bg-color);
                            color: var(--text-color);
                            font-family: system-ui, -apple-system, sans-serif;
                            transition: background-color 0.3s ease, color 0.3s ease;
                        }
                        
                        .toolbar {
                            background-color: var(--toolbar-bg);
                            border-bottom: 1px solid var(--toolbar-border);
                            padding: 8px;
                            display: flex;
                            justify-content: space-between;
                            align-items: center;
                            transition: background-color 0.3s ease, border-color 0.3s ease;
                        }
                        
                        .toolbar button {
                            background-color: var(--button-bg);
                            color: white;
                            border: none;
                            border-radius: 4px;
                            padding: 6px 12px;
                            cursor: pointer;
                            margin-right: 8px;
                            font-size: 14px;
                            transition: background-color 0.3s ease;
                        }
                        
                        .toolbar button:hover {
                            background-color: var(--button-hover);
                        }
                        
                        .theme-toggle {
                            background-color: transparent;
                            border: 1px solid var(--text-color);
                            color: var(--text-color);
                            padding: 4px 8px;
                            font-size: 12px;
                        }
                        
                        .theme-toggle:hover {
                            background-color: var(--text-color);
                            color: var(--bg-color);
                        }
                        
                        .container {
                            flex: 1;
                            display: flex;
                            justify-content: center;
                            align-items: center;
                            overflow: auto;
                            padding: 20px;
                        }
                        
                        svg {
                            max-width: 100%;
                            max-height: 100%;
                            height: auto;
                            width: auto;
                            transition: all 0.3s ease;
                        }
                    </style>
                </head>
                <body data-theme="${isDarkMode ? 'dark' : 'light'}">
                    <div class="toolbar">
                        <div>
                            <button onclick="zoomIn()">Zoom In</button>
                            <button onclick="zoomOut()">Zoom Out</button>
                            <button onclick="resetZoom()">Reset</button>
                            <button class="theme-toggle" onclick="toggleTheme()">${isDarkMode ? '☀️ Light' : '🌙 Dark'}</button>
                        </div>
                        <div>
                            <button onclick="downloadSvg()">Download SVG</button>
                        </div>
                    </div>
                    <div class="container" id="svg-container">
                        ${svgData}
                    </div>
                    <script>
                        ${getZoomScript()}
                        ${getDownloadSvgScript(`mermaid-diagram-${Date.now()}.svg`)}
                        let isDarkMode = ${isDarkMode ? 'true' : 'false'};
                        
                        svg.setAttribute('preserveAspectRatio', 'xMidYMid meet');
                        function getOptimalTextColor(bgColor) {
                            if (!bgColor || bgColor === 'none' || bgColor === 'transparent') return isDarkMode ? '#eceff4' : '#000000';
                            var m = bgColor.match(/^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i);
                            if (!m) {
                                m = bgColor.match(/^#([0-9a-f])([0-9a-f])([0-9a-f])$/i);
                                if (m) m = [null, m[1]+m[1], m[2]+m[2], m[3]+m[3]];
                            }
                            if (!m) return isDarkMode ? '#eceff4' : '#000000';
                            var r = parseInt(m[1], 16), g = parseInt(m[2], 16), b = parseInt(m[3], 16);
                            var luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255;
                            return luminance > 0.35 ? '#000000' : '#ffffff';
                        }

                        function toggleTheme() {
                            isDarkMode = !isDarkMode;
                            const body = document.body;
                            const themeButton = document.querySelector('.theme-toggle');
                            
                            if (isDarkMode) {
                                body.setAttribute('data-theme', 'dark');
                                themeButton.textContent = '☀️ Light';
                            } else {
                                body.setAttribute('data-theme', 'light');
                                themeButton.textContent = '🌙 Dark';
                            }
                            
                            // Re-render Mermaid diagram with new theme
                            reRenderMermaidDiagram();
                        }
                        
                        function reRenderMermaidDiagram() {
                            const svgContainer = document.getElementById('svg-container');
                            const currentSvg = svgContainer.querySelector('svg');
                            
                            if (!currentSvg) return;
                            
                            // Apply Mermaid-specific theme styling
                            applyMermaidTheme(currentSvg, isDarkMode);
                        }
                        
                        function applyMermaidTheme(svgEl, isDark) {

                            var darkTheme = {
                                primaryColor: '#88c0d0',
                                primaryTextColor: '#ffffff',
                                primaryBorderColor: '#88c0d0',
                                lineColor: '#88c0d0',
                                secondaryColor: '#5e81ac',
                                tertiaryColor: '#2e3440',
                                textColor: '#eceff4',
                                mainBkg: '#3b4252',
                                secondBkg: '#434c5e',
                                nodeBorder: '#88c0d0',
                                edgeLabelBackground: '#4c566a',
                                altBackground: '#2e3440',
                                nodeBkg: '#3b4252',
                                clusterBkg: '#2e3440'
                            };
                            
                            var lightTheme = {
                                primaryColor: '#1890ff',
                                primaryTextColor: '#000000',
                                primaryBorderColor: '#1890ff',
                                lineColor: '#333333',
                                secondaryColor: '#f0f0f0',
                                tertiaryColor: '#ffffff',
                                textColor: '#333333',
                                mainBkg: '#ffffff',
                                secondBkg: '#f8f9fa',
                                nodeBorder: '#cccccc',
                                edgeLabelBackground: '#ffffff',
                                altBackground: '#f5f5f5',
                                nodeBkg: '#ffffff',
                                clusterBkg: '#f8f9fa'
                            };
                            
                            var colors = isDark ? darkTheme : lightTheme;
                            var textColor = isDark ? '#eceff4' : '#1a1a2e';

                            // SVG background
                            svgEl.style.backgroundColor = isDark ? '#2e3440' : '#ffffff';

                            // Node shapes
                            svgEl.querySelectorAll('.node rect, .node circle, .node polygon, .node path').forEach(function(el) {
                                el.style.setProperty('fill', colors.nodeBkg, 'important');
                                el.style.setProperty('stroke', colors.nodeBorder, 'important');
                            });

                            // Cluster / subgraph backgrounds
                            svgEl.querySelectorAll('.cluster rect').forEach(function(el) {
                                el.style.setProperty('fill', colors.clusterBkg, 'important');
                                el.style.setProperty('stroke', colors.nodeBorder, 'important');
                            });

                            // Edge paths and arrowheads
                            svgEl.querySelectorAll('.edgePath path, .flowchart-link, path.path').forEach(function(el) {
                                el.style.setProperty('stroke', colors.lineColor, 'important');
                            });
                            svgEl.querySelectorAll('defs marker path').forEach(function(el) {
                                // D-293/w1-06: crow's-foot / cardinality markers are drawn hollow
                                // (fill:none) — blanket-filling them with the theme line colour turns
                                // them into solid blobs that occlude the entity border and hide the
                                // cardinality glyph. Recolour a marker's FILL only when it was actually
                                // filled (solid arrowheads); always recolour the stroke so the marker
                                // stays visible. Keeps hollow markers hollow.
                                // D-156/D-293: an ER crow's-foot / cardinality marker whose
                                // hollowness comes from a CSS rule has NO inline fill attribute, so
                                // curFill reads back EMPTY here. Treat empty/absent fill as hollow
                                // too (matching resolveMermaidMarkerColors) so we never fabricate a
                                // solid teal fill that occludes the entity border. Only a marker that
                                // is ACTUALLY filled is recoloured.
                                var curFill = (el.getAttribute('fill') || el.style.getPropertyValue('fill') || '').trim().toLowerCase();
                                el.style.setProperty('stroke', colors.lineColor, 'important');
                                if (curFill !== 'none' && curFill !== 'transparent' && curFill !== '') {
                                    el.style.setProperty('fill', colors.lineColor, 'important');
                                }
                            });

                            // Edge label backgrounds
                            svgEl.querySelectorAll('.edgeLabel rect, .edgeLabel .label-container').forEach(function(el) {
                                el.style.setProperty('fill', colors.edgeLabelBackground, 'important');
                                el.style.setProperty('stroke', 'none', 'important');
                            });

                            // ALL text: SVG text elements, foreignObject content, labels
                            svgEl.querySelectorAll('text').forEach(function(el) {
                                el.style.setProperty('fill', textColor, 'important');
                            });
                            svgEl.querySelectorAll('foreignObject span, foreignObject div, foreignObject p').forEach(function(el) {
                                el.style.setProperty('color', textColor, 'important');
                            });

                            // Node labels: contrast against node background
                            svgEl.querySelectorAll('.node .label text, .node .label tspan').forEach(function(textEl) {
                                var parentGroup = textEl.closest('g.node');
                                if (parentGroup) {
                                    var bg = parentGroup.querySelector('rect, polygon, circle, path');
                                    if (bg) {
                                        var fill = bg.style.getPropertyValue('fill') || bg.getAttribute('fill');
                                        var contrast = fill ? getOptimalTextColor(fill) : textColor;
                                        textEl.style.setProperty('fill', contrast, 'important');
                                    }
                                }
                            });
                            svgEl.querySelectorAll('.node foreignObject span, .node foreignObject div').forEach(function(el) {
                                var parentGroup = el.closest('g.node');
                                if (parentGroup) {
                                    var bg = parentGroup.querySelector('rect, polygon, circle, path');
                                    if (bg) {
                                        var fill = bg.style.getPropertyValue('fill') || bg.getAttribute('fill');
                                        var contrast = fill ? getOptimalTextColor(fill) : textColor;
                                        el.style.setProperty('color', contrast, 'important');
                                    }
                                }
                            });

                            // Cluster / subgraph title text
                            svgEl.querySelectorAll('.cluster text, .cluster tspan').forEach(function(el) {
                                el.style.setProperty('fill', textColor, 'important');
                            });
                        }
                        
                        // No re-theme on load: the serialized SVG was already rendered
                        // with the parent window's theme. Re-theming here would discard
                        // per-node style directives and mermaid's own layout CSS.
                    </script>
                </body>
                </html>
                `;

            // Create a blob with the HTML content
            const blob = new Blob([htmlContent], { type: 'text/html' });
            const url = URL.createObjectURL(blob);

            // Open in a new window with specific dimensions
            const popupWindow = window.open(
                url,
                'MermaidDiagram',
                `width=${width},height=${height},resizable=yes,scrollbars=yes,status=no,toolbar=no,menubar=no,location=no`
            );

            // Focus the new window
            if (popupWindow) {
                popupWindow.focus();
            }

            // Clean up the URL object after a delay
            setTimeout(() => URL.revokeObjectURL(url), 10000);
        };
        actionsContainer.appendChild(openButton);

        // Add Save button
        const saveButton = document.createElement('button');
        saveButton.innerHTML = '💾 Save';
        saveButton.className = 'diagram-action-button mermaid-save-button';
        saveButton.onclick = () => {
            // Get the SVG element
            const svgElement = wrapper.querySelector('svg');
            if (!svgElement) return;

            // Create a new SVG with proper XML declaration and doctype
            const svgData = new XMLSerializer().serializeToString(svgElement);

            // Create a properly formatted SVG document with XML declaration
            const svgDoc = `<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" "http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd">
${svgData}`;

            // Create a blob with the SVG content
            const blob = new Blob([svgDoc], { type: 'image/svg+xml' });
            const url = URL.createObjectURL(blob);

            // Create a download link
            const link = document.createElement('a');
            link.href = url;
            link.download = `mermaid-diagram-${Date.now()}.svg`;
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);

            // Clean up the URL object after a delay
            setTimeout(() => URL.revokeObjectURL(url), 1000);
        };
        actionsContainer.appendChild(saveButton);

        // Add Source button
        let showingSource = false;
        let cachedSVG: string;
        let cachedScale: string;
        let cachedWrapperHeight: string;

        // Cache initial state
        cachedSVG = wrapper.innerHTML;
        cachedScale = svgElement.style.transform;
        cachedWrapperHeight = wrapper.style.minHeight;

        const sourceButton = document.createElement('button');
        sourceButton.innerHTML = showingSource ? '🎨 View' : '📝 Source';
        sourceButton.className = 'diagram-action-button mermaid-source-button';
        sourceButton.onclick = () => {
            showingSource = !showingSource;
            sourceButton.innerHTML = showingSource ? '🎨 View' : '📝 Source';

            if (showingSource) {
                // Cache current state before showing source
                cachedSVG = wrapper.innerHTML;
                cachedScale = svgElement.style.transform;
                cachedWrapperHeight = wrapper.style.minHeight;

                wrapper.innerHTML = `<div style="
                        font-size: 12px;
                        font-weight: 600;
                        color: ${isDarkMode ? '#88c0d0' : '#6c757d'};
                        padding: 8px 16px 0;
                        letter-spacing: 0.5px;
                        text-transform: uppercase;
                    ">📝 Mermaid Source</div>
                    <pre style="
                        background-color: ${isDarkMode ? '#1f1f1f' : '#f6f8fa'};
                        padding: 16px;
                        border-radius: 4px;
                        overflow: auto;
                        color: ${isDarkMode ? '#e6e6e6' : '#24292e'};
                    "><code>${escapeHtml(spec.definition ?? '')}</code></pre>`;
            } else {
                // Restore cached state
                wrapper.innerHTML = cachedSVG;

                // Reapply the scaling that was applied before
                const restoredSVG = wrapper.querySelector('svg');
                if (restoredSVG && cachedScale) {
                    (restoredSVG as SVGElement).style.transform = cachedScale;
                }
                if (cachedWrapperHeight) {
                    wrapper.style.minHeight = cachedWrapperHeight;
                }
            }
        };
        actionsContainer.appendChild(sourceButton);

        // Add actions container
        container.insertBefore(actionsContainer, wrapper);

    } catch (error: any) {
        console.error('Mermaid rendering error:', error);

        // Remove any loading spinner if it exists
        const spinner = container.querySelector('.mermaid-loading-spinner');
        if (spinner) {
            container.removeChild(spinner);
        }

        // Clear container before showing error
        if (container.innerHTML.includes('Rendering diagram')) {
            container.innerHTML = '';
        }

        // Extract the first line to check for diagram type
        const lines = spec.definition.trim().split('\n');
        const firstLine = lines[0]?.trim() || '';
        const diagramType = firstLine.split(' ')[0];

        // Enhanced error analysis
        let errorTitle = 'Mermaid Rendering Error';
        let errorMessage = 'There was an error rendering the diagram.';

        // Analyze the error message for common patterns
        const errorMsg = error.message || '';

        if (errorMsg.includes('Parse error') || errorMsg.includes('Expecting')) {
            errorTitle = 'Mermaid Syntax Error';
            errorMessage = 'There is a syntax error in the Mermaid diagram definition. The diagram may have malformed connections or invalid characters.';
        } else if (errorMsg.includes('Lexical error') || errorMsg.includes('Unrecognized text')) {
            errorTitle = 'Mermaid Lexical Error';
            errorMessage = 'Mermaid encountered unrecognized text or invalid syntax in the diagram definition.';
        } else if (errorMsg.includes('empty SVG returned')) {
            // Check if this diagram type is actually supported
            try {
                const { detectSupportedDiagramTypes, normalizeDiagramType } = await import('./mermaidEnhancer');
                const supportedTypes = detectSupportedDiagramTypes(mermaid);
                const normalizedType = normalizeDiagramType(diagramType, mermaid);

                console.log('Type detection debug:', {
                    diagramType,
                    normalizedType,
                    supportedTypesSize: supportedTypes.size,
                    supportedTypes: Array.from(supportedTypes),
                    hasOriginal: supportedTypes.has(diagramType),
                    hasNormalized: supportedTypes.has(normalizedType)
                });

                // If detection failed (empty set), fall back to parsing error
                if (supportedTypes.size === 0) {
                    console.warn('Type detection returned empty set, falling back to parsing error');
                    errorTitle = 'Mermaid Parsing Error';
                    errorMessage = 'Mermaid parsing failed - this usually indicates syntax errors in the diagram definition.';
                } else if (diagramType && !supportedTypes.has(diagramType) && !supportedTypes.has(normalizedType)) {
                    errorTitle = 'Unsupported Diagram Type';
                    errorMessage = `The diagram type "${diagramType}" is not supported in the current version of Mermaid. This may be a beta feature that hasn't been released yet.`;
                } else {
                    errorTitle = 'Mermaid Parsing Error';
                    errorMessage = 'Mermaid parsing failed - this usually indicates syntax errors in the diagram definition.';
                }
            } catch (detectionError) {
                console.warn('Could not detect supported types:', detectionError);
                errorTitle = 'Mermaid Parsing Error';
                errorMessage = 'Mermaid parsing failed - this usually indicates syntax errors in the diagram definition.';
            }
        }

        if (!spec.isStreaming || spec.forceRender) {
            // First clear the container and add the error message
            container.innerHTML = `
                <div class="mermaid-error">
                    <strong>${errorTitle}:</strong>
                    <p>${errorMessage}</p>
                    <pre>${escapeHtml(error.message || 'Unknown error')}</pre>
                    <details>
                        <summary>Show Definition</summary>
                        <pre><code>${escapeHtml(spec.definition ?? '')}</code></pre>
                    </details>
                </div>
                `;

            // Tag the card so the headless harness fails fast with this
            // message rather than polling for an svg the error card never
            // contains (see railroadPlugin.renderError for the full contract).
            const errorCard = container.querySelector('.mermaid-error');
            if (errorCard) {
                errorCard.setAttribute(
                    'data-diagram-error',
                    errorTitle + ': ' + (error.message || errorMessage));
            }

            // Create buttons
            const viewSourceButton = document.createElement('button');
            viewSourceButton.innerHTML = '📝 View Source';
            viewSourceButton.className = 'diagram-action-button mermaid-source-button';
            viewSourceButton.style.cssText = `
                    background-color: #4361ee;
                    color: white;
                    border: none;
                    border-radius: 4px;
                    padding: 8px 16px;
                    margin: 10px 5px;
                    cursor: pointer;
                    display: inline-block;
                `;

            // Add retry button
            const retryButton = document.createElement('button');
            retryButton.innerHTML = '🔄 Retry Rendering';
            retryButton.className = 'diagram-action-button mermaid-retry-button';
            retryButton.style.cssText = `
                    background-color: #4361ee;
                    color: white;
                    border: none;
                    border-radius: 4px;
                    padding: 8px 16px;
                    margin: 10px auto;
                    cursor: pointer;
                    display: block;
                `;

            // Create button container
            const buttonContainer = document.createElement('div');
            buttonContainer.style.textAlign = 'center';
            buttonContainer.appendChild(viewSourceButton);
            buttonContainer.appendChild(retryButton);
            container.appendChild(buttonContainer);

            // Add event listeners
            retryButton.onclick = () => mermaidPlugin.render(container, d3, spec, isDarkMode);
            viewSourceButton.onclick = () => {
                // Toggle between error view and source view
                const errorView = container.querySelector('.mermaid-error');
                if (errorView) {
                    // Create source view
                    container.innerHTML = `<pre style="
                            background-color: ${isDarkMode ? '#1f1f1f' : '#f6f8fa'};
                            padding: 16px;
                            border-radius: 4px;
                            overflow: auto;
                            color: ${isDarkMode ? '#e6e6e6' : '#24292e'};
                        "><code>${escapeHtml(spec.definition ?? '')}</code></pre>`;

                    // Add back button
                    const backButton = document.createElement('button');
                    backButton.innerHTML = '⬅️ Back to Error';
                    backButton.className = 'diagram-action-button';
                    backButton.style.cssText = `
                            background-color: #4361ee;
                            color: white;
                            border: none;
                            border-radius: 4px;
                            padding: 8px 16px;
                            margin: 10px auto;
                            cursor: pointer;
                            display: block;
                        `;
                    backButton.onclick = () => mermaidPlugin.render(container, d3, spec, isDarkMode);
                    container.appendChild(backButton);
                }
            };
        }
    }
};

// Unified responsive scaling that works across all browsers including Safari
/**
 * Find the maximum EFFECTIVE font size as actually rendered on screen
 * CRITICAL: Returns the maximum DECLARED font size (we'll apply viewBox scaling separately)
 */
function findMaxDeclaredFontSize(svgElement: SVGElement): number {
    const textElements = svgElement.querySelectorAll('text, tspan');
    const foreignObjectContainers = svgElement.querySelectorAll('foreignObject');
    let maxFontSize = 0;

    // Measure SVG text elements - get DECLARED size from computedStyle
    textElements.forEach(textEl => {
        const text = textEl.textContent?.trim();
        if (!text) return;

        const computedStyle = window.getComputedStyle(textEl);
        const declaredSize = parseFloat(computedStyle.fontSize || '0');

        if (declaredSize > maxFontSize) {
            maxFontSize = declaredSize;
        }
    });

    // Also check foreignObject elements
    foreignObjectContainers.forEach((fo) => {
        const textEls = fo.querySelectorAll('span, div');
        textEls.forEach(el => {
            const text = el.textContent?.trim();
            if (!text) return;

            const computedStyle = window.getComputedStyle(el);
            const declaredSize = parseFloat(computedStyle.fontSize || '0');

            if (declaredSize > maxFontSize) {
                maxFontSize = declaredSize;
            }
        });
    });

    console.log(`📏 FONT-DETECTION: Max declared font size: ${maxFontSize.toFixed(1)}px`);
    return maxFontSize;
}

/**
 * Scale diagram to achieve target EFFECTIVE font size (as rendered on screen)
 * Key insight: effective font = declared font × (svg width / viewBox width)
 * So we set svg width = viewBox width × (target font / declared font)
 */
function applyEffectiveFontScaling(svgElement: SVGElement, mermaidWrapper: HTMLElement, diagramType: string): void {
    const viewBox = svgElement.getAttribute('viewBox');
    if (!viewBox) {
        console.log('🎯 EFFECTIVE-SCALE: No viewBox found, skipping');
        return;
    }

    const [, , vbW, vbH] = viewBox.split(' ').map(Number);

    // Find max DECLARED font size
    const maxDeclaredFont = findMaxDeclaredFontSize(svgElement);

    if (maxDeclaredFont === 0) {
        console.log('🎯 EFFECTIVE-SCALE: No text found, skipping');
        return;
    }

    console.log(`🎯 EFFECTIVE-SCALE: Type="${diagramType}", Declared font=${maxDeclaredFont.toFixed(1)}px, ViewBox=${vbW.toFixed(0)}×${vbH.toFixed(0)}`);

    // Calculate viewBox scale needed for target font size
    // effective font = declared font × viewBox scale
    // target font = declared font × target viewBox scale
    // target viewBox scale = target font / declared font
    const targetViewBoxScale = SCALE_CONFIG.TARGET_FONT_SIZE / maxDeclaredFont;

    // New SVG dimensions
    let newWidth = vbW * targetViewBoxScale;
    let newHeight = vbH * targetViewBoxScale;

    // Clamp to container width so the diagram fills available space
    // without overflowing. Fall back to a reasonable default.
    const containerWidth = mermaidWrapper.closest('.d3-container')?.clientWidth || mermaidWrapper.parentElement?.clientWidth || 0;
    const maxWidth = containerWidth > 200 ? containerWidth - 20 : 900;
    const minWidth = 100;
    if (newWidth > maxWidth) {
        const ratio = maxWidth / newWidth;
        newWidth = maxWidth;
        newHeight = newHeight * ratio;
    } else if (newWidth < minWidth) {
        const ratio = minWidth / newWidth;
        newWidth = minWidth;
        newHeight = newHeight * ratio;
    }

    // Remove default width attribute and apply calculated dimensions
    svgElement.removeAttribute('width');
    svgElement.removeAttribute('height');
    svgElement.style.setProperty('width', `${newWidth}px`, 'important');
    svgElement.style.setProperty('height', `${newHeight}px`, 'important');
    svgElement.style.setProperty('max-width', 'none', 'important');
    svgElement.style.setProperty('max-height', 'none', 'important');
    svgElement.style.setProperty('transform', 'none', 'important');

    // Set wrapper to match content
    mermaidWrapper.style.setProperty('min-height', `${newHeight + 20}px`, 'important');
    mermaidWrapper.style.setProperty('height', 'auto', 'important');
    mermaidWrapper.style.setProperty('max-height', 'none', 'important');
    mermaidWrapper.style.setProperty('width', '100%', 'important');
    mermaidWrapper.style.setProperty('display', 'flex', 'important');
    mermaidWrapper.style.setProperty('justify-content', 'center', 'important');
    mermaidWrapper.style.setProperty('align-items', 'flex-start', 'important');
    mermaidWrapper.style.setProperty('overflow', 'visible', 'important');
    mermaidWrapper.style.setProperty('padding', '10px', 'important');

    // Fix parent d3-container
    const d3Container = mermaidWrapper.closest('.d3-container');
    if (d3Container) {
        (d3Container as HTMLElement).style.setProperty('height', 'auto', 'important');
        (d3Container as HTMLElement).style.setProperty('min-height', 'auto', 'important');
        const outer = d3Container.parentElement?.closest('.d3-container');
        if (outer) {
            (outer as HTMLElement).style.setProperty('height', 'auto', 'important');
            (outer as HTMLElement).style.setProperty('min-height', 'auto', 'important');
        }
    }

    const finalScale = newWidth / vbW;
    const finalEffectiveFont = maxDeclaredFont * finalScale;
    console.log(`🎯 EFFECTIVE-SCALE: ${vbW.toFixed(0)}×${vbH.toFixed(0)} → ${newWidth.toFixed(0)}×${newHeight.toFixed(0)} (scale: ${finalScale.toFixed(3)}, effective font: ${finalEffectiveFont.toFixed(1)}px)`);
}

function applyUnifiedResponsiveScaling(
    container: HTMLElement,
    svgElement: SVGElement,
    isDarkMode: boolean,
    diagramType: string = 'unknown'
) {
    console.log('🎯 UNIFIED-SCALING: Applying responsive scaling for all browsers');

    const isSafari = /^((?!chrome|android).)*safari/i.test(navigator.userAgent);
    const mermaidWrapper = container.querySelector('.mermaid-wrapper') as HTMLElement;

    if (!mermaidWrapper) {
        console.warn('No mermaid wrapper found, cannot apply responsive scaling');
        return;
    }

    // MINIMAL approach: Just ensure proper viewBox and preserve Mermaid's layout
    const viewBox = svgElement.getAttribute('viewBox');
    if (viewBox) {
        console.log('🎯 UNIFIED-SCALING: SVG has viewBox:', viewBox);
        // Ensure proper responsive attributes without breaking positioning
        svgElement.setAttribute('preserveAspectRatio', 'xMidYMid meet');
    } else {
        console.warn('🎯 UNIFIED-SCALING: No viewBox found, this may cause positioning issues');
        // Don't add a viewBox if Mermaid didn't create one - this can break positioning
    }

    // For Safari, apply minimal scaling if the diagram is too small
    if (isSafari) {
        // Apply effective font-based scaling
        setTimeout(() => {
            applyEffectiveFontScaling(svgElement, mermaidWrapper, diagramType);
        }, 250);

        setTimeout(() => {
            const svgRect = svgElement.getBoundingClientRect();
            const containerRect = container.getBoundingClientRect();

            console.log('🎯 SAFARI-SIZE-CHECK:', {
                svgWidth: svgRect.width,
                svgHeight: svgRect.height,
                containerWidth: containerRect.width,
                currentTransform: svgElement.style.transform
            });

            // Only scale if the diagram is significantly smaller than the container
            if (svgRect.width > 0 && containerRect.width > 0 &&
                svgRect.width < containerRect.width * 0.6) {
                const targetScale = Math.min(containerRect.width * 0.9 / svgRect.width, 4.0);
                svgElement.style.transform = `scale(${targetScale})`;
                svgElement.style.transformOrigin = 'center center';
                console.log(`🎯 SAFARI-SCALE: Applied scaling ${targetScale}x (${svgRect.width}px → ${svgRect.width * targetScale}px)`);

                // Adjust wrapper to accommodate scaled content
                mermaidWrapper.style.minHeight = `${svgRect.height * targetScale + 40}px`;
                mermaidWrapper.style.minWidth = `${svgRect.width * targetScale}px`;
                mermaidWrapper.style.width = 'auto'; // Let it expand to fit scaled content
            } else {
                console.log('🎯 SAFARI-SCALE: No scaling needed, diagram size looks good');
            }
        }, 200); // Give Mermaid time to finish positioning
    } else {
        // For non-Safari browsers, apply effective font-based scaling
        setTimeout(() => {
            console.log(`🎯 SCALE: Starting effective font scaling for ${diagramType}`);
            applyEffectiveFontScaling(svgElement, mermaidWrapper, diagramType);
        }, 500);
    }
    // Configure wrapper for responsive behavior without breaking Mermaid's positioning
    mermaidWrapper.style.width = '100%';
    mermaidWrapper.style.maxWidth = '100%';
    mermaidWrapper.style.overflow = 'auto'; // Changed from 'visible' to 'auto' to handle large scaled content
    mermaidWrapper.style.display = 'flex';
    mermaidWrapper.style.justifyContent = 'center';
    mermaidWrapper.style.alignItems = 'flex-start'; // Changed from 'center' to preserve top alignment
    mermaidWrapper.style.padding = '1em';

    console.log('🎯 UNIFIED-SCALING: Responsive configuration applied', isSafari ? '(Safari)' : '(Other)');
}
