/**
 * Force-directed graph plugin for D3 visualization.
 *
 * Handles specs with type "force-directed" or "force".  Uses d3-force
 * simulation for automatic layout of nodes connected by links.
 *
 * Accepted spec shapes:
 *   { type: "force-directed", data: { nodes: [...], links: [...] }, style?: {...} }
 *   { type: "force", nodes: [...], links: [...], style?: {...} }
 */
import { D3RenderPlugin } from '../../types/d3';
import {
  classifyColor,
  contrastRatio,
  compositeOver,
  ensureReadableFill,
  isDarkBackground,
  truncateLabel,
} from './chartTheme';
import JSON5 from 'json5';

interface ForceNode {
  id: string;
  group?: number;
  size?: number;
  color?: string;
  label?: string;
  // d3-force adds these at runtime
  x?: number;
  y?: number;
  fx?: number | null;
  fy?: number | null;
}

interface ForceLink {
  source: string | ForceNode;
  target: string | ForceNode;
  value?: number;
  color?: string;
}

interface ForceStyle {
  background?: string;
  nodeColors?: Record<string, string>;
  nodeColor?: string;
  linkColor?: string;
  linkOpacity?: number;
  labelColor?: string;
  fontSize?: number;
}

/** Default palette for node groups when no explicit colors are given. */
const DEFAULT_GROUP_COLORS = [
  '#4e79a7', '#f28e2b', '#e15759', '#76b7b2', '#59a14f',
  '#edc948', '#b07aa1', '#ff9da7', '#9c755f', '#bab0ac',
];

/** Absolute clamp for a node's rendered radius (px). */
export const FORCE_MAX_NODE_RADIUS = 200;

/** Max characters shown in a node label before ellipsis truncation (D-021). */
export const FORCE_MAX_LABEL_CHARS = 24;

/**
 * Canonical page surfaces used for contrast resolution when the caller does not
 * pin an explicit background. The DARK surface matches the app's dark page
 * (~#212121) rather than the old hardcoded #1a1a2e, which sat at only 1.06:1
 * against the page and produced a two-tone split panel (D-019).
 */
export const FORCE_LIGHT_BG = '#ffffff';
export const FORCE_DARK_BG = '#212121';

export interface ForceColorResolution {
  /** Opaque hex used for ALL contrast math (the effective canvas). */
  effectiveBg: string;
  /** SVG background to paint, or null = leave transparent so the page shows
   *  through (prevents the dark two-tone seam). */
  paintBg: string | null;
  /** Link stroke colour, guaranteed to clear 3:1 against effectiveBg once
   *  composited at linkOpacity. */
  linkStroke: string;
  /** Link stroke-opacity actually applied. */
  linkOpacity: number;
  /** Node/label text colour, contrast-reconciled against effectiveBg. */
  labelColor: string;
  /** Whether the effective canvas is dark (drives node-stroke halo direction). */
  darkCanvas: boolean;
}

/**
 * Nudge a stroke colour so that, composited over `bg` at `opacity`, it clears
 * `minRatio` (WCAG graphical floor). The stroke is pushed toward the
 * canvas-opposite (white on a dark canvas, black on a light one) in fixed steps;
 * if even the extreme cannot reach the floor at this opacity the extreme is
 * returned (best effort). Pure/testable.
 */
export function readableStroke(
  input: string,
  bg: string,
  opacity: number,
  fallback: string,
  minRatio = 3,
): string {
  const c = classifyColor(input);
  // Named CSS colours: keep as-is (contrast uncomputable without resolving).
  if (c && c.named) return c.named;
  let hex = c && c.hex ? c.hex : (classifyColor(fallback)?.hex || fallback);
  const dark = isDarkBackground(bg);
  const target = dark ? '#ffffff' : '#000000';
  // Try the requested colour first, then blend toward the canvas-opposite.
  const parse = (h: string): [number, number, number] | null => {
    const m = /^#?([0-9a-f]{6})$/i.exec(h.trim());
    if (!m) return null;
    const v = m[1];
    return [parseInt(v.slice(0, 2), 16), parseInt(v.slice(2, 4), 16), parseInt(v.slice(4, 6), 16)];
  };
  const start = parse(hex);
  const goal = parse(target)!;
  if (!start) return hex;
  let best = hex;
  for (let t = 0; t <= 1.0001; t += 0.1) {
    const cand = '#' + [0, 1, 2]
      .map((i) => Math.round(start[i] + (goal[i] - start[i]) * t).toString(16).padStart(2, '0'))
      .join('');
    best = cand;
    if (contrastRatio(compositeOver(cand, bg, opacity), bg) >= minRatio) return cand;
  }
  return best;
}

/**
 * Resolve every theme-dependent colour for a force-directed render FROM the
 * effective canvas, not a raw isDarkMode flag.
 *   - D-019: paint no background unless the caller pins one, so the SVG inherits
 *     the page surface (no #1a1a2e-vs-page seam); effectiveBg = pinned hex if
 *     given, else the theme page surface.
 *   - D-017: default link stroke + opacity chosen so the composited edge clears
 *     3:1 in both themes (was #999/#555 @0.6 -> 1.78/1.56, ghost hairlines).
 *   - D-018: label colour is a per-canvas default, and a caller-supplied
 *     labelColor is contrast-reconciled against the effective canvas (text 4.5
 *     floor) instead of being passed through verbatim.
 * Pure/testable (no DOM, no d3).
 */
export function resolveForceColors(isDarkMode: boolean, style: ForceStyle = {}): ForceColorResolution {
  const themeBg = isDarkMode ? FORCE_DARK_BG : FORCE_LIGHT_BG;
  const explicit = classifyColor(style.background);
  const effectiveBg = explicit && explicit.hex ? explicit.hex : themeBg;
  const paintBg = style.background ? style.background : null;
  const darkCanvas = isDarkBackground(effectiveBg);

  const defaultLink = darkCanvas ? '#b0b0b0' : '#6b6b6b';
  const linkOpacity = typeof style.linkOpacity === 'number' ? style.linkOpacity : 0.9;
  const linkStroke = readableStroke(style.linkColor || defaultLink, effectiveBg, linkOpacity, defaultLink);

  const defaultLabel = darkCanvas ? '#e0e0e0' : '#333333';
  const labelColor = style.labelColor
    ? ensureReadableFill(style.labelColor, effectiveBg, defaultLabel, 4.5)
    : defaultLabel;

  return { effectiveBg, paintBg, linkStroke, linkOpacity, labelColor, darkCanvas };
}

/**
 * Rotate the hue of a hex colour by `deg` degrees (pure RGB<->HSL, no d3), used
 * to de-collide recycled group palette entries past the 10-entry table (D-020):
 * group index %10 alone made the 11th distinct group reuse the 1st group's
 * colour. A per-cycle golden-angle-ish rotation keeps recycled groups visually
 * distinct. Non-hex input is returned unchanged.
 */
export function rotateHue(hex: string, deg: number): string {
  const m = /^#?([0-9a-f]{6})$/i.exec((hex || '').trim());
  if (!m) return hex;
  const v = m[1];
  let r = parseInt(v.slice(0, 2), 16) / 255;
  let g = parseInt(v.slice(2, 4), 16) / 255;
  let b = parseInt(v.slice(4, 6), 16) / 255;
  const max = Math.max(r, g, b), min = Math.min(r, g, b);
  const l = (max + min) / 2;
  let h = 0, s = 0;
  const d = max - min;
  if (d !== 0) {
    s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
    if (max === r) h = ((g - b) / d + (g < b ? 6 : 0));
    else if (max === g) h = (b - r) / d + 2;
    else h = (r - g) / d + 4;
    h *= 60;
  }
  h = (((h + deg) % 360) + 360) % 360;
  const c = (1 - Math.abs(2 * l - 1)) * s;
  const x = c * (1 - Math.abs(((h / 60) % 2) - 1));
  const mm = l - c / 2;
  let rp = 0, gp = 0, bp = 0;
  if (h < 60) { rp = c; gp = x; }
  else if (h < 120) { rp = x; gp = c; }
  else if (h < 180) { gp = c; bp = x; }
  else if (h < 240) { gp = x; bp = c; }
  else if (h < 300) { rp = x; bp = c; }
  else { rp = c; bp = x; }
  const toHex = (n: number) =>
    Math.max(0, Math.min(255, Math.round((n + mm) * 255))).toString(16).padStart(2, '0');
  return '#' + toHex(rp) + toHex(gp) + toHex(bp);
}

/**
 * Resolve the group-palette colour for a node group index, contrast-reconciled
 * against the EFFECTIVE canvas (D-020). DEFAULT_GROUP_COLORS is dark-tuned:
 * measured on #ffffff, half its entries fall below the 3:1 graphical floor
 * (e.g. #edc948 1.61, #76b7b2 2.29, #ff9da7 1.98) while all clear it on #212121.
 * Returning the raw entry therefore made light-theme groups near-invisible.
 * Here the entry is nudged toward the surface-opposite until it clears 3:1
 * (per-theme resolution, NOT a constant swap — dark entries are already fine and
 * pass through unchanged), and groups past the palette length are hue-rotated so
 * distinct groups no longer collapse onto the same colour at %10 recycling.
 * Pure/testable.
 */
export function groupColor(group: number | undefined, effectiveBg: string): string {
  const n = DEFAULT_GROUP_COLORS.length;
  const gi = Number.isFinite(group as number) ? Math.max(0, Math.trunc(group as number)) : 0;
  const idx = gi % n;
  const cycle = Math.floor(gi / n);
  let base = DEFAULT_GROUP_COLORS[idx];
  if (cycle > 0) base = rotateHue(base, cycle * 137);
  return ensureReadableFill(base, effectiveBg, base, 3);
}

/**
 * Resolve a node's fill against the effective canvas. Precedence:
 *   node.color  ->  style.nodeColors[group]  ->  style.nodeColor (uniform)  ->
 *   group palette.
 * `style.nodeColor` (a declared ForceStyle option) was previously never read —
 * getNodeColor checked only d.color / nodeColors / palette, so a caller's
 * uniform fill was silently ignored and every node fell back to the palette
 * (D-121). A CALLER-supplied colour is contrast-reconciled (D-018/D-023):
 * transparent / zero-alpha / unresolvable tokens fall back to the palette (so a
 * colour can never erase a node), a near-surface hex is nudged to the floor.
 * The palette branch is itself reconciled via groupColor (D-020). Pure/testable.
 */
export function resolveNodeFill(
  d: { color?: string; group?: number },
  style: ForceStyle,
  effectiveBg: string,
): string {
  const palette = groupColor(d.group, effectiveBg);
  const nodeColors = style.nodeColors || {};
  const explicit = d.color || nodeColors[String(d.group ?? 0)] || style.nodeColor;
  if (explicit) return ensureReadableFill(explicit, effectiveBg, palette, 3);
  return palette;
}

/**
 * Resolve a single link's stroke. `link.color` (a declared ForceLink option) was
 * dropped because render set `stroke` ONCE on the parent <g> from the global
 * linkColor rather than per-datum, so per-link colours and any ok/warn/err
 * semantics were lost (D-121). Here a per-link colour is contrast-reconciled
 * against the effective canvas at the applied opacity (readableStroke), falling
 * back to the resolved default when absent/unresolvable. Pure/testable.
 */
export function resolveLinkStroke(
  link: { color?: string },
  effectiveBg: string,
  opacity: number,
  defaultStroke: string,
): string {
  if (link && link.color) return readableStroke(link.color, effectiveBg, opacity, defaultStroke);
  return defaultStroke;
}

/**
 * Shorten a link segment so it ends `targetR + gap` before the target node
 * centre, i.e. at the node's rim. The arrow marker is drawn with
 * markerUnits='userSpaceOnUse' (a fixed pixel size independent of stroke-width)
 * and its tip at the line's end coordinate, so shortening the segment to the rim
 * is what makes the arrowhead sit at — and scale independently of — the target
 * node, regardless of that node's radius (D-022). The old fixed refX=20 with the
 * default markerUnits='strokeWidth' drew a giant arrowhead over the node centre
 * on heavy edges and a near-invisible one on thin edges. Pure/testable.
 */
export function shortenToTarget(
  sx: number, sy: number, tx: number, ty: number, targetR: number, gap = 4,
): { x: number; y: number } {
  const dx = tx - sx, dy = ty - sy;
  const dist = Math.hypot(dx, dy);
  if (!(dist > 0)) return { x: tx, y: ty };
  const off = Math.max(0, targetR) + gap;
  const k = Math.max(0, (dist - off)) / dist;
  return { x: sx + dx * k, y: sy + dy * k };
}

/**
 * Normalise a raw {nodes, links} graph so edges are not silently dropped by the
 * render-time endpoint filter (D-124 — the "confident wrong picture" mode).
 * Two independent failures both left every edge unresolved, and with no link
 * force the hardcoded charge repulsion then flung the now-unlinked nodes
 * off-canvas with no signal to the user:
 *   1. endpoint aliases: links carrying `from`/`to` (or `src`/`dst`) never mapped
 *      to `source`/`target`, and nodes carrying `name` never mapped to `id`.
 *   2. array-index endpoints: numeric endpoints (d3-force's own documented
 *      default) never resolved against the `.id(d => d.id)` accessor.
 * Here node ids are back-filled from name/label, endpoint aliases are mapped, a
 * numeric (or numeric-string) endpoint that is not itself a real id resolves to
 * the node at that array index, and links whose endpoints still cannot resolve
 * are counted in `dropped` so the caller can warn instead of rendering a
 * scatter. Returns NEW objects; pure/testable.
 */
export function normalizeGraph<N extends Record<string, any>, L extends Record<string, any>>(
  nodes: N[],
  links: L[],
): { nodes: N[]; links: L[]; dropped: number } {
  const outNodes: N[] = (Array.isArray(nodes) ? nodes : []).map((raw) => {
    const n: Record<string, any> = { ...raw };
    if (n.id == null && n.name != null) n.id = n.name;
    if (n.id == null && n.label != null) n.id = n.label;
    return n as N;
  });
  const idByIndex = outNodes.map((n) => (n.id != null ? String(n.id) : ''));
  const idSet = new Set(idByIndex.filter((id) => id !== ''));

  const resolveEndpoint = (e: any): string => {
    if (e && typeof e === 'object') return e.id != null ? String(e.id) : '';
    if (typeof e === 'number') {
      const asId = String(e);
      if (idSet.has(asId)) return asId; // a real id that happens to be numeric
      if (Number.isInteger(e) && e >= 0 && e < idByIndex.length && idByIndex[e] !== '') return idByIndex[e];
      return '';
    }
    if (typeof e === 'string') {
      if (idSet.has(e)) return e;
      if (/^\d+$/.test(e)) {
        const i = Number(e);
        if (i >= 0 && i < idByIndex.length && idByIndex[i] !== '') return idByIndex[i];
      }
      return e; // an unknown string id — reported as dropped below
    }
    return '';
  };

  let dropped = 0;
  const outLinks: L[] = [];
  for (const raw of Array.isArray(links) ? links : []) {
    const l: Record<string, any> = { ...raw };
    const s = l.source != null ? l.source : (l.from != null ? l.from : l.src);
    const t = l.target != null ? l.target : (l.to != null ? l.to : l.dst);
    const sid = resolveEndpoint(s);
    const tid = resolveEndpoint(t);
    if (idSet.has(sid) && idSet.has(tid)) {
      l.source = sid;
      l.target = tid;
      outLinks.push(l as L);
    } else {
      dropped++;
    }
  }
  return { nodes: outNodes, links: outLinks, dropped };
}

export interface FitTransform { k: number; x: number; y: number; }

/**
 * Compute a zoom-to-bounds transform that fits the settled node extent (each
 * point's circle included) into the [padding, size-padding] viewport, centred.
 *
 * The plugin sizes the SVG to a fixed viewBox with overflow:hidden and never
 * fit the settled simulation, so any graph larger than the box — plus
 * disconnected components thrown wide by charge repulsion, and extreme aspect
 * ratios — was silently clipped or ejected off-canvas (D-016). Applying this
 * transform to the zoom group re-centres and scales the graph to fit.
 *
 * Scale is clamped to [0.2, 2]: never below the zoom floor, and tiny graphs are
 * not blown up past 2x (which would turn a 3-node graph into giant blobs).
 * Non-finite points are ignored; fewer than one usable point returns identity.
 * Pure/testable.
 */
export function computeFitTransform(
  points: Array<{ x: number; y: number; r?: number }>,
  width: number,
  height: number,
  padding = 30,
  minScale = 0.2,
  maxScale = 2,
): FitTransform {
  const pts = (points || []).filter(
    (p) => p && Number.isFinite(p.x) && Number.isFinite(p.y),
  );
  if (pts.length === 0 || !(width > 0) || !(height > 0)) return { k: 1, x: 0, y: 0 };

  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (const p of pts) {
    const r = Number.isFinite(p.r as number) ? Math.max(0, p.r as number) : 0;
    minX = Math.min(minX, p.x - r);
    minY = Math.min(minY, p.y - r);
    maxX = Math.max(maxX, p.x + r);
    maxY = Math.max(maxY, p.y + r);
  }
  const bw = maxX - minX;
  const bh = maxY - minY;
  const availW = Math.max(1, width - 2 * padding);
  const availH = Math.max(1, height - 2 * padding);

  let k = Math.min(availW / (bw || 1), availH / (bh || 1));
  if (!Number.isFinite(k) || k <= 0) k = 1;
  k = Math.max(minScale, Math.min(maxScale, k));

  const cx = (minX + maxX) / 2;
  const cy = (minY + maxY) / 2;
  return { k, x: width / 2 - k * cx, y: height / 2 - k * cy };
}

/**
 * The minimum on-screen pixel size a node label must render at, AFTER the
 * fit-to-extent zoom scale is applied. Labels live inside the zoom group `g`,
 * which computeFitTransform scales by `fit.k` — so a label authored at
 * `fontSize` px renders on-screen at `fontSize * fit.k` px. For a large graph
 * (e.g. a 6000-wide extent fitted into a ~1280px frame, k≈0.213) a 10px label
 * collapses to ~2px, and a caller `style.fontSize` of 4 renders unreadable 4px
 * glyphs even at k≈1 (D-122).
 */
export const FORCE_MIN_LABEL_ON_SCREEN_PX = 9;

/**
 * Compute the font-size (in user/pre-scale units) to APPLY to node labels so
 * that, once the fit-to-extent scale `fitK` is applied by the zoom group, the
 * on-screen size clears FORCE_MIN_LABEL_ON_SCREEN_PX. A caller-chosen larger
 * size is never shrunk; we only enlarge to meet the floor.
 *
 *   applied = max(base, floor / k)   →   on-screen = applied * k >= floor
 *
 * `fitK` is clamped to (0, 2] by computeFitTransform, so the applied size is
 * bounded (floor / 0.2 = 45px worst case). Pure/testable.
 */
export function effectiveLabelFontSize(
  baseFont: number,
  fitK: number,
  floorPx = FORCE_MIN_LABEL_ON_SCREEN_PX,
): number {
  const base = Number.isFinite(baseFont) && baseFont > 0 ? baseFont : 10;
  const k = Number.isFinite(fitK) && fitK > 0 ? fitK : 1;
  return Math.max(base, floorPx / k);
}

/**
 * Fraction of the shorter canvas dimension that a node radius may occupy. A
 * node radius past this is disproportionate to the drawing area regardless of
 * the absolute FORCE_MAX_NODE_RADIUS cap.
 */
export const FORCE_NODE_RADIUS_CANVAS_FRACTION = 0.18;

/**
 * Clamp a node radius to a fraction of the canvas rather than the fixed
 * FORCE_MAX_NODE_RADIUS constant. FORCE_MAX_NODE_RADIUS=200 permitted a 400px
 * diameter disc inside a 500px-tall default canvas; at high node counts a few
 * such clamped discs plus forceCollide(size+4) evict every other node off the
 * canvas, so almost no labels survive (D-123). The canvas-relative cap keeps
 * the largest disc proportionate to the drawing area. A small floor (12px)
 * ensures nodes stay visible on a tiny canvas, and the absolute
 * FORCE_MAX_NODE_RADIUS remains an upper bound. Pure/testable.
 */
export function clampNodeRadiusToCanvas(r: number, width: number, height: number): number {
  const rr = Number.isFinite(r) && r > 0 ? r : 0;
  const w = Number.isFinite(width) && width > 0 ? width : 0;
  const h = Number.isFinite(height) && height > 0 ? height : 0;
  const shorter = Math.min(w || Infinity, h || Infinity);
  const canvasCap = Number.isFinite(shorter)
    ? shorter * FORCE_NODE_RADIUS_CANVAS_FRACTION
    : FORCE_MAX_NODE_RADIUS;
  const cap = Math.max(12, Math.min(FORCE_MAX_NODE_RADIUS, canvasCap));
  return Math.min(rr, cap);
}

/**
 * Coerce an arbitrary value to a finite number, or return `undefined` when it
 * cannot be (non-numeric string, NaN, +/-Infinity, null, object). Numeric
 * strings ("400", "3.5") are accepted; "NaN"/"Infinity"/"-Infinity" and
 * anything else become `undefined`.
 */
export function toFiniteOrUndefined(v: any): number | undefined {
  if (typeof v === 'number') return Number.isFinite(v) ? v : undefined;
  if (typeof v === 'string') {
    const trimmed = v.trim();
    if (trimmed === '') return undefined;
    const n = Number(trimmed);
    return Number.isFinite(n) ? n : undefined;
  }
  return undefined;
}

/**
 * Sanitize force-directed nodes so no non-finite value can reach the d3-force
 * simulation. This is the whole-class guard for the "total render hang" family
 * (ledger Issue 25): a node whose fixed-position pin (`fx`/`fy`) is a non-finite
 * value — e.g. the JSON strings "Infinity"/"-Infinity"/"NaN", or a raw
 * Infinity/NaN — poisons d3's quadtree. `d3.forceManyBody` builds a quadtree via
 * `cover()`, which DOUBLES its extent in a `while` loop until it contains every
 * point; an Infinity coordinate can never be covered, so the loop spins forever
 * and the simulation never emits a frame (30s timeout, zero output). A NaN pin
 * silently corrupts every node position to NaN instead.
 *
 * Rules (pure, no DOM):
 *   - `fx`/`fy`: kept only if they coerce to a finite number; otherwise the pin
 *     is DROPPED (property removed) so the node participates as a free node.
 *   - `size`/`radius`: coerced to a finite number, forced non-negative, and
 *     clamped to FORCE_MAX_NODE_RADIUS; non-finite/absent left untouched so the
 *     render path applies its own default.
 *   - all other fields (id, group, label, color, ...) are preserved verbatim.
 *
 * Returns NEW node objects (does not mutate the input).
 */
export function sanitizeForceNodes<T extends Record<string, any>>(nodes: T[]): T[] {
  if (!Array.isArray(nodes)) return [];
  return nodes.map((raw) => {
    const n: Record<string, any> = { ...raw };

    // Fixed-position pins: drop any non-finite pin outright.
    if ('fx' in n) {
      const fx = toFiniteOrUndefined(n.fx);
      if (fx === undefined) delete n.fx;
      else n.fx = fx;
    }
    if ('fy' in n) {
      const fy = toFiniteOrUndefined(n.fy);
      if (fy === undefined) delete n.fy;
      else n.fy = fy;
    }

    // Radius-ish numeric fields: clamp to a sane finite range when present.
    for (const key of ['size', 'radius'] as const) {
      if (key in n) {
        const val = toFiniteOrUndefined(n[key]);
        if (val === undefined) {
          delete n[key]; // let the render path fall back to its default
        } else {
          n[key] = Math.min(FORCE_MAX_NODE_RADIUS, Math.max(0, val));
        }
      }
    }

    return n as T;
  });
}

/**
 * Recover a structured force-directed spec from a `definition`-as-JSON-string
 * wrapper.
 *
 * `render_diagram` (app/mcp/tools/diagram_render.py) always ships the real spec
 * as a STRING under `spec.definition`, with only `type` on the outer wrapper
 * (e.g. `{ type: "force-directed", definition: "{...nodes,links,layout...}" }`).
 * `isForceDirectedSpec`/`render` read `nodes`/`links`/`layout` off the top-level
 * object, so a wrapped spec exposes no arrays -> `canHandle` returns false ->
 * findPluginForSpec returns undefined -> the D3Renderer orchestrator busy-retries
 * to a ~30s timeout with ZERO output (ledger Issue 40; same contract-mismatch
 * class as chord#23 / joint#2 / network#11 / music#17). This ALSO masks the
 * Issue-25 non-converging-sim family, because no plugin ever matches to run it.
 *
 * This lifts the structured fields (nodes/links, or data.nodes/data.links, plus
 * layout and any optional style/width/height/charge-family params) from the
 * parsed definition onto a shallow copy so downstream code sees the arrays it
 * expects. If the spec is ALREADY structured, or `definition` is absent /
 * non-JSON / carries no force-directed content, the spec is returned unchanged
 * (guarded — never hijacks a non-force spec).
 *
 * Exported for regression testing.
 */
/**
 * Strip a leading/trailing markdown code fence and any surrounding prose from a
 * definition string. Model output frequently wraps the JSON payload in a
 * ```json ... ``` fence (D-024); the old recovery bailed on the first char not
 * being '{' and never saw the byte-valid JSON inside. Handles a matched fence,
 * an unmatched leading/trailing fence, and a leading language tag.
 * Pure/testable.
 */
export function stripDefinitionFence(raw: string): string {
  let t = String(raw).trim();
  const matched = /^```[a-zA-Z0-9_-]*\s*\n?([\s\S]*?)\n?```$/.exec(t);
  if (matched) return matched[1].trim();
  // Unmatched fences (leading or trailing only).
  t = t.replace(/^```[a-zA-Z0-9_-]*\s*/, '').replace(/```\s*$/, '');
  return t.trim();
}

/** Normalise smart/curly quotes to ASCII so a copy-pasted payload parses.
 *  json5 does NOT accept U+201C/U+201D/U+2018/U+2019 (D-024). Pure/testable. */
export function normalizeSmartQuotes(raw: string): string {
  return String(raw)
    .replace(/[\u201C\u201D\u201E\u201F]/g, '"')
    .replace(/[\u2018\u2019\u201A\u201B]/g, "'");
}

/**
 * Lenient parse of a JSON-ish object string. Tries strict JSON.parse first
 * (fast path, unchanged behaviour), then json5 (trailing commas, unquoted keys,
 * single quotes, comments) after stripping a markdown fence, normalising smart
 * quotes, and slicing to the outermost {...} so leading prose / trailing
 * semicolons are ignored. Returns the parsed object, or `undefined` when it is
 * unrecoverable. Pure/testable — no DOM.
 */
export function lenientParseObject(raw: any): any {
  if (typeof raw !== 'string') return undefined;
  const cleaned = normalizeSmartQuotes(stripDefinitionFence(raw)).trim();
  if (!cleaned) return undefined;
  const first = cleaned.indexOf('{');
  const last = cleaned.lastIndexOf('}');
  if (first === -1 || last === -1 || last < first) return undefined;
  const body = cleaned.slice(first, last + 1);
  try {
    return JSON.parse(body);
  } catch (_e) {
    /* fall through to json5 */
  }
  try {
    return JSON5.parse(body);
  } catch (_e2) {
    return undefined;
  }
}

/**
 * Recursively locate the object that carries the force-directed graph. The old
 * probe only looked at the top level or one `data` level, so a graph nested one
 * level too deep under an arbitrary wrapper key (e.g. { graph: { nodes,links } })
 * was invisible -> no plugin matched -> 30s empty-DOM hang (D-024). Depth-limited
 * DFS for the first object holding a `nodes` array; `links` (or `edges`) is read
 * from the same container. Pure/testable.
 */
export function findGraphContainer(
  obj: any,
  depth = 0,
): { container: any; nodes: any[]; links: any[] } | undefined {
  if (!obj || typeof obj !== 'object' || Array.isArray(obj) || depth > 4) return undefined;
  if (Array.isArray(obj.nodes)) {
    const links = Array.isArray(obj.links)
      ? obj.links
      : Array.isArray(obj.edges)
        ? obj.edges
        : [];
    return { container: obj, nodes: obj.nodes, links };
  }
  for (const key of Object.keys(obj)) {
    const found = findGraphContainer(obj[key], depth + 1);
    if (found) return found;
  }
  return undefined;
}

export function resolveForceDirectedSpec(spec: any): any {
  if (typeof spec !== 'object' || spec === null) return spec;

  // Already structured (nodes present at top level or under data)?
  const hasNodes = Array.isArray(spec.nodes) || Array.isArray(spec.data?.nodes);
  if (hasNodes) return spec;

  // Only attempt recovery from a `definition` string.
  if (typeof spec.definition !== 'string' || spec.definition.trim() === '') return spec;

  // Lenient parse: strict JSON first, then a fence/smart-quote/json5 recovery so
  // trailing commas, unquoted keys, single/smart quotes, comments and a leading
  // markdown fence no longer leave the spec node-less (-> unclaimable -> 30s
  // hang). D-024.
  const parsed = lenientParseObject(spec.definition);
  if (typeof parsed !== 'object' || parsed === null) return spec;

  // Recursively discover the nodes/links container (top level, `data`, or one
  // wrapper level deeper). Requires genuine force-directed content: without a
  // nodes array there is nothing to lay out, so leave the spec untouched for
  // another plugin (never hijacks a non-force spec).
  const graph = findGraphContainer(parsed);
  if (!graph) return spec;

  const resolved: any = { ...spec };
  resolved.nodes = graph.nodes;
  resolved.links = graph.links;
  // Tuning / geometry / style discriminators may live on the parsed root OR the
  // discovered container; prefer the container, fall back to the root.
  const pick = (key: string): any =>
    graph.container[key] !== undefined ? graph.container[key] : parsed[key];
  // The layout discriminator commonly lives INSIDE the stringified definition,
  // not on the wrapper; lift it so `isForceDirectedSpec` recognises the family.
  const layout = pick('layout');
  if (layout !== undefined) resolved.layout = layout;
  const parsedType = pick('type');
  if (parsedType !== undefined && resolved.type === undefined) resolved.type = parsedType;
  // Optional geometry / style / force-tuning fields, when present.
  for (const key of ['width', 'height', 'style', 'charge', 'collideRadius', 'linkDistance']) {
    const v = pick(key);
    if (v !== undefined) resolved[key] = v;
  }
  return resolved;
}

function isForceDirectedSpec(rawSpec: any): boolean {
  const spec = resolveForceDirectedSpec(rawSpec);
  if (typeof spec !== 'object' || spec === null) return false;

  // "d3" is a renderer-FAMILY name, not a concrete diagram type. A spec of
  // { type: "d3", layout: "force-directed"|"force", nodes, links } must map
  // onto this concrete plugin — otherwise no plugin's canHandle matches,
  // findPluginForSpec returns undefined, and the D3Renderer orchestrator
  // retries to a ~35s timeout (silent data loss / hang). See ledger Issue 3.
  const type = spec.type;
  const layout = spec.layout;
  const isForceType = type === 'force-directed' || type === 'force';
  // A bare { type: "d3", nodes, links } — the shape a user naively writes for a
  // network — carries NO layout hint. It used to be rejected to keep this plugin
  // from becoming a d3 catch-all, but that left it matching NO plugin at all:
  // findPluginForSpec returned undefined and the D3Renderer orchestrator busy-
  // retried to a ~30s empty-DOM timeout (D-015, silent data loss). The presence
  // of BOTH a nodes array AND a flat links (edge) array — enforced below — already
  // disambiguates the shape from the other d3-family layouts (chord needs a
  // matrix, tree/pack need `children`), so a missing OR force layout is safely
  // inferred as force-directed. A COMPETING explicit layout (chord/tree/radial/…)
  // is still rejected, so this never hijacks another engine's spec.
  const inferForce =
    layout === undefined ||
    layout === null ||
    layout === '' ||
    layout === 'force-directed' ||
    layout === 'force';
  const isD3Family = type === 'd3' && inferForce;
  if (!isForceType && !isD3Family) return false;

  // Nodes/links can be at top level or nested under data
  const nodes = spec.nodes || spec.data?.nodes;
  const links = spec.links || spec.data?.links;

  return Array.isArray(nodes) && Array.isArray(links) && nodes.length > 0;
}

export const forceDirectedPlugin: D3RenderPlugin = {
  name: 'force-directed',
  priority: 5,
  sizingConfig: {
    sizingStrategy: 'fixed',
    needsDynamicHeight: false,
    needsOverflowVisible: false,
    observeResize: false,
    containerStyles: {
      overflow: 'hidden',
    },
  },

  canHandle: isForceDirectedSpec,

  render: (container: HTMLElement, d3: any, rawSpec: any, isDarkMode: boolean): (() => void) => {
    // Recover a structured spec from a definition-as-JSON-string wrapper first
    // (render_diagram nests nodes/links/layout as a STRING under spec.definition;
    // without this, nodes/links are invisible and nothing renders). See Issue 40.
    const spec = resolveForceDirectedSpec(rawSpec);
    // Normalize spec: extract nodes/links from either location, then sanitize
    // node geometry. sanitizeForceNodes drops any non-finite fx/fy pin (the JSON
    // strings "Infinity"/"NaN", raw Infinity/NaN) — such a pin poisons d3's
    // forceManyBody quadtree cover() into an infinite doubling loop, hanging the
    // whole render to timeout with zero output. See ledger Issue 25.
    // Normalise endpoint/id aliases and array-index endpoints BEFORE sanitising
    // and filtering (D-124). Previously node ids came only from `n.id` and link
    // endpoints only from `source`/`target`, so a spec using `name`/`from`/`to`
    // (or numeric array-index endpoints) left every edge unresolved: with no link
    // force the charge repulsion then flung the now-unlinked nodes off-canvas —
    // a confident wrong picture with no signal. normalizeGraph back-fills ids
    // from name/label, maps from/to->source/target, resolves index endpoints,
    // and reports how many links still could not resolve.
    const norm = normalizeGraph<any, any>(
      spec.nodes || spec.data?.nodes || [],
      spec.links || spec.data?.links || [],
    );
    // Sanitize node geometry AFTER id normalisation: sanitizeForceNodes drops any
    // non-finite fx/fy pin (the JSON strings "Infinity"/"NaN", raw Infinity/NaN)
    // — such a pin poisons d3's forceManyBody quadtree cover() into an infinite
    // doubling loop, hanging the whole render to timeout with zero output. See
    // ledger Issue 25.
    const nodes: ForceNode[] = sanitizeForceNodes(norm.nodes);

    // normalizeGraph has already dropped links whose endpoints do not resolve to
    // a node (which also protects d3.forceLink().id() from its uncaught "node not
    // found" abort — see ledger Issue 3). Surface the drop instead of silently
    // rendering a scatter.
    const links: ForceLink[] = norm.links as ForceLink[];
    if (norm.dropped > 0) {
      // eslint-disable-next-line no-console
      console.warn(
        `[force-directed] dropped ${norm.dropped} link(s) with unresolved endpoints ` +
        `(no matching node id / index)`,
      );
    }

    const style: ForceStyle = spec.style || {};

    const width = spec.width || 700;
    const height = spec.height || 500;
    // Resolve every theme-dependent colour from the EFFECTIVE canvas, not a raw
    // isDarkMode flag: no self-painted background (inherit the page, D-019),
    // link stroke/opacity that clears 3:1 composited (D-017), and a
    // contrast-reconciled label colour (D-018).
    const colors = resolveForceColors(isDarkMode, style);
    const { effectiveBg, paintBg, linkStroke: linkColor, linkOpacity, labelColor, darkCanvas } = colors;
    const fontSize = style.fontSize || 10;

    /**
     * Resolve a node's fill against the effective canvas via resolveNodeFill:
     *   node.color -> style.nodeColors[group] -> style.nodeColor -> group palette,
     * with a caller colour contrast-reconciled (transparent/zero-alpha/token fall
     * back to the palette so geometry is never erased) and the palette branch
     * itself nudged to the graphical floor per-theme (D-018/D-020/D-023). The
     * previously-ignored uniform style.nodeColor is now honoured (D-121).
     */
    const getNodeColor = (d: ForceNode): string => resolveNodeFill(d, style, effectiveBg);

    // Effective draw radius: clamp the node radius to a fraction of the canvas
    // (not the fixed FORCE_MAX_NODE_RADIUS constant) so an oversized disc cannot,
    // together with forceCollide, evict every other node off the canvas (D-123).
    const radiusOf = (d: ForceNode): number =>
      clampNodeRadiusToCanvas(d.size || 8, width, height);

    // Clear container
    d3.select(container).selectAll('*').remove();

    const svg = d3.select(container)
      .append('svg')
      .attr('width', width)
      .attr('height', height)
      .attr('viewBox', [0, 0, width, height])
      .style('border-radius', '6px');
    // Only paint a background when the caller pins one; otherwise inherit the
    // page/theme surface so a dark canvas does not render a two-tone split panel
    // (the old #1a1a2e sat at ~1.06:1 against the ~#212121 page). D-019.
    if (paintBg) svg.style('background', paintBg);

    // Zoom group — all rendered content goes inside
    const g = svg.append('g');

    // Zoom / pan behaviour
    const zoom = d3.zoom()
      .scaleExtent([0.2, 5])
      .on('zoom', (event: any) => g.attr('transform', event.transform));
    svg.call(zoom);

    // Arrow marker for directed edges. markerUnits='userSpaceOnUse' fixes the
    // marker at a constant PIXEL size regardless of the edge's stroke-width — the
    // old default 'strokeWidth' units drew a giant arrowhead over the node centre
    // on heavy edges and a near-invisible one on thin edges (D-022). refX=10
    // places the arrow TIP at the line's end coordinate; the tick handler then
    // shortens each segment to the target node's rim (radius-aware), so the head
    // sits at the node boundary for any node size instead of at a fixed refX=20.
    svg.append('defs').append('marker')
      .attr('id', 'fd-arrow')
      .attr('viewBox', '0 -5 10 10')
      .attr('refX', 10)
      .attr('refY', 0)
      .attr('markerUnits', 'userSpaceOnUse')
      .attr('markerWidth', 10)
      .attr('markerHeight', 10)
      .attr('orient', 'auto')
      .append('path')
      .attr('d', 'M0,-5L10,0L0,5')
      .attr('fill', linkColor);

    // Force-tuning levers. resolveForceDirectedSpec lifts charge / linkDistance /
    // collideRadius off the definition onto the resolved spec, but render()
    // previously hardcoded .distance(80) / .strength(-200) / .radius(size+4) and
    // never read them, so the three knobs a user needs to fit a large graph were
    // silently ignored (D-120). Read them now (coerced to finite; else default).
    const linkDistance = toFiniteOrUndefined(spec.linkDistance) ?? 80;
    const chargeStrength = toFiniteOrUndefined(spec.charge) ?? -200;
    const collideRadius = toFiniteOrUndefined(spec.collideRadius);

    // Force simulation
    const simulation = d3.forceSimulation(nodes)
      .force('link', d3.forceLink(links).id((d: ForceNode) => d.id).distance(linkDistance))
      .force('charge', d3.forceManyBody().strength(chargeStrength))
      .force('center', d3.forceCenter(width / 2, height / 2))
      .force('collision', d3.forceCollide().radius((d: ForceNode) =>
        collideRadius !== undefined ? collideRadius : radiusOf(d) + 4));

    // Links
    const link = g.append('g')
      .attr('stroke', linkColor)
      .attr('stroke-opacity', linkOpacity)
      .selectAll('line')
      .data(links)
      .join('line')
      // Per-link stroke: a link's own `color` (a declared ForceLink option) was
      // dropped because stroke was set ONCE on the parent <g> from the global
      // linkColor, so per-edge colours / ok-warn-err semantics were lost (D-121).
      // resolveLinkStroke contrast-reconciles a per-link colour against the
      // effective canvas and falls back to the resolved default when absent.
      .attr('stroke', (d: any) => resolveLinkStroke(d, effectiveBg, linkOpacity, linkColor))
      .attr('stroke-width', (d: any) => Math.sqrt(d.value || 1))
      .attr('marker-end', 'url(#fd-arrow)');

    // Node groups (circle + label)
    const node = g.append('g')
      .selectAll('g')
      .data(nodes)
      .join('g')
      .call(drag(simulation, d3));

    // Circles
    node.append('circle')
      .attr('r', (d: ForceNode) => radiusOf(d))
      .attr('fill', getNodeColor)
      .attr('stroke', (d: ForceNode) => {
        // Node outline for definition against the CANVAS. A blind brighter()
        // stroke vanished on a light canvas (light node + brighter stroke);
        // instead push toward the canvas-opposite so the border reads in both
        // themes. D-018.
        const fill = getNodeColor(d);
        const c = d3.color(fill);
        if (!c) return darkCanvas ? '#ffffff88' : '#00000088';
        return (darkCanvas ? c.brighter(0.7) : c.darker(0.7)).toString();
      })
      .attr('stroke-width', 1.5);

    // Labels. Previously a blind fixed offset with the full string, no
    // truncation and no halo, so long labels ran off-screen / clipped mid-string
    // and dense labels overprinted each other, the node circles and the link
    // hairball into an unreadable smear (D-021). Now:
    //   - ellipsis-truncate to FORCE_MAX_LABEL_CHARS (full text kept in <title>),
    //   - paint a halo (stroke = the effective canvas, drawn UNDER the glyph via
    //     paint-order:stroke) so the label reads over whatever it overlaps.
    // Collision-aware re-placement is intentionally NOT attempted here: a
    // second label-layout pass in the headless warm-up is disproportionate and
    // would perturb the settled/fitted geometry; the halo + truncation restore
    // legibility without moving anything.
    const labelText = node.append('text')
      .text((d: ForceNode) => truncateLabel(String(d.label || d.id), FORCE_MAX_LABEL_CHARS))
      .attr('x', (d: ForceNode) => radiusOf(d) + 4)
      .attr('y', 3)
      .attr('fill', labelColor)
      .attr('stroke', effectiveBg)
      .attr('stroke-width', 3)
      .attr('stroke-linejoin', 'round')
      .attr('paint-order', 'stroke')
      .attr('font-size', `${fontSize}px`)
      .attr('font-family', 'system-ui, -apple-system, sans-serif')
      .attr('pointer-events', 'none');

    // Tooltip on hover
    node.append('title')
      .text((d: ForceNode) => d.label || d.id);

    // Tick handler — update positions every simulation step
    simulation.on('tick', () => {
      link
        .attr('x1', (d: any) => d.source.x)
        .attr('y1', (d: any) => d.source.y)
        // Shorten each segment to the target node's rim so the fixed-size
        // arrowhead (markerUnits=userSpaceOnUse, tip at the line end) sits at the
        // node boundary for ANY node radius, not over the centre (D-022).
        .attr('x2', (d: any) =>
          shortenToTarget(d.source.x, d.source.y, d.target.x, d.target.y, radiusOf(d.target)).x)
        .attr('y2', (d: any) =>
          shortenToTarget(d.source.x, d.source.y, d.target.x, d.target.y, radiusOf(d.target)).y);

      node.attr('transform', (d: any) => `translate(${d.x},${d.y})`);
    });

    // Warm up the simulation so the initial render isn't a messy blob
    // (300 ticks ≈ the default alphaMin threshold)
    simulation.alpha(1).restart();
    for (let i = 0; i < 300; i++) simulation.tick();
    // Trigger a final render with settled positions
    simulation.alpha(0.01).restart();

    // Fit-to-extent: the SVG is a fixed viewBox with overflow:hidden and never
    // fit the settled layout, so any graph larger than the box — plus
    // disconnected components thrown wide by charge repulsion and extreme aspect
    // ratios — was silently clipped or ejected off-canvas (D-016). Zoom-to-bounds
    // the settled extent so the whole graph is visible; applied THROUGH the zoom
    // behaviour so pan/zoom stays consistent from the fitted view.
    const fit = computeFitTransform(
      nodes.map((n) => ({ x: n.x as number, y: n.y as number, r: radiusOf(n) + 4 })),
      width,
      height,
    );
    svg.call(zoom.transform, d3.zoomIdentity.translate(fit.x, fit.y).scale(fit.k));

    // Labels live inside the zoom group scaled by fit.k, so a label authored at
    // `fontSize` px renders on-screen at `fontSize * fit.k` px — collapsing to a
    // sub-pixel smudge when a large extent is fitted into the frame, and unreadable
    // when the caller sets a tiny style.fontSize. Enlarge the applied size so the
    // on-screen size clears FORCE_MIN_LABEL_ON_SCREEN_PX; never shrink a larger
    // caller choice (D-122).
    labelText.attr('font-size', `${effectiveLabelFontSize(fontSize, fit.k)}px`);

    // Cleanup function — stop simulation when component unmounts
    return () => {
      simulation.stop();
    };
  },
};

/**
 * D3 drag behaviour for force-directed nodes.
 *
 * On drag start the node is pinned (fx/fy set) so it doesn't float
 * away.  On drag end it's un-pinned so the simulation can settle.
 */
function drag(simulation: any, d3: any) {
  function dragstarted(event: any) {
    if (!event.active) simulation.alphaTarget(0.3).restart();
    event.subject.fx = event.subject.x;
    event.subject.fy = event.subject.y;
  }

  function dragged(event: any) {
    event.subject.fx = event.x;
    event.subject.fy = event.y;
  }

  function dragended(event: any) {
    if (!event.active) simulation.alphaTarget(0);
    event.subject.fx = null;
    event.subject.fy = null;
  }

  return d3.drag()
    .on('start', dragstarted)
    .on('drag', dragged)
    .on('end', dragended);
}
