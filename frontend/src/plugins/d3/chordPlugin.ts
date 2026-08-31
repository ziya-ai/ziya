/**
 * Chord diagram plugin for D3 visualization.
 *
 * Handles specs with type "chord" or "chord-directed".  Renders a
 * circular chord layout showing flows between groups via d3.chord() /
 * d3.chordDirected() + d3.ribbon().
 *
 * Accepted spec shapes:
 *
 *   Links form (LLM-friendly, mirrors force-directed):
 *     {
 *       type: "chord",
 *       nodes: [{ id: "A", label?: "A", color?: "#abc" }, ...],
 *       links: [{ source: "A", target: "B", value: 10 }, ...],
 *       directed?: true,         // default true (uses chordDirected)
 *       style?: {...}
 *     }
 *
 *   Matrix form (direct d3 input):
 *     {
 *       type: "chord",
 *       matrix: [[0, 5, 2], [3, 0, 1], [4, 2, 0]],
 *       names?: ["A", "B", "C"],
 *       colors?: ["#abc", ...],
 *       directed?: true,
 *       style?: {...}
 *     }
 */
import { D3RenderPlugin } from '../../types/d3';
import { isDarkBackground, classifyColor, ensureReadableFill, contrastRatio, truncateLabel } from './chartTheme';
import { lenientParseObject } from './forceDirectedPlugin';

interface ChordNode {
  id: string;
  label?: string;
  color?: string;
}

interface ChordLink {
  source: string;
  target: string;
  value?: number;
}

interface ChordStyle {
  background?: string;
  ribbonOpacity?: number;
  hoverOpacity?: number;
  fadeOpacity?: number;
  labelColor?: string;
  fontSize?: number;
  arcStroke?: string;
}

/** Default categorical palette when no explicit colors are given. */
const DEFAULT_PALETTE = [
  '#4e79a7', '#f28e2b', '#e15759', '#76b7b2', '#59a14f',
  '#edc948', '#b07aa1', '#ff9da7', '#9c755f', '#bab0ac',
];

/**
 * The 148 CSS named colours -> hex. Used to (a) VALIDATE a caller colour name
 * and (b) resolve it to a hex so its contrast can be reconciled. A bare word
 * that is NOT a real CSS colour (a design-system token such as 'primary' or
 * 'accent') is absent from this table, so `normalizeChordColorToHex` returns
 * null for it and the fill falls back to the palette instead of the SVG initial
 * value BLACK (D-070).
 */
const CSS_NAMED_COLORS: Record<string, string> = {
  aliceblue:'#f0f8ff', antiquewhite:'#faebd7', aqua:'#00ffff', aquamarine:'#7fffd4',
  azure:'#f0ffff', beige:'#f5f5dc', bisque:'#ffe4c4', black:'#000000', blanchedalmond:'#ffebcd',
  blue:'#0000ff', blueviolet:'#8a2be2', brown:'#a52a2a', burlywood:'#deb887',
  cadetblue:'#5f9ea0', chartreuse:'#7fff00', chocolate:'#d2691e', coral:'#ff7f50',
  cornflowerblue:'#6495ed', cornsilk:'#fff8dc', crimson:'#dc143c', cyan:'#00ffff',
  darkblue:'#00008b', darkcyan:'#008b8b', darkgoldenrod:'#b8860b', darkgray:'#a9a9a9',
  darkgreen:'#006400', darkgrey:'#a9a9a9', darkkhaki:'#bdb76b', darkmagenta:'#8b008b',
  darkolivegreen:'#556b2f', darkorange:'#ff8c00', darkorchid:'#9932cc', darkred:'#8b0000',
  darksalmon:'#e9967a', darkseagreen:'#8fbc8f', darkslateblue:'#483d8b', darkslategray:'#2f4f4f',
  darkslategrey:'#2f4f4f', darkturquoise:'#00ced1', darkviolet:'#9400d3', deeppink:'#ff1493',
  deepskyblue:'#00bfff', dimgray:'#696969', dimgrey:'#696969', dodgerblue:'#1e90ff',
  firebrick:'#b22222', floralwhite:'#fffaf0', forestgreen:'#228b22', fuchsia:'#ff00ff',
  gainsboro:'#dcdcdc', ghostwhite:'#f8f8ff', gold:'#ffd700', goldenrod:'#daa520', gray:'#808080',
  green:'#008000', greenyellow:'#adff2f', grey:'#808080', honeydew:'#f0fff0', hotpink:'#ff69b4',
  indianred:'#cd5c5c', indigo:'#4b0082', ivory:'#fffff0', khaki:'#f0e68c', lavender:'#e6e6fa',
  lavenderblush:'#fff0f5', lawngreen:'#7cfc00', lemonchiffon:'#fffacd', lightblue:'#add8e6',
  lightcoral:'#f08080', lightcyan:'#e0ffff', lightgoldenrodyellow:'#fafad2', lightgray:'#d3d3d3',
  lightgreen:'#90ee90', lightgrey:'#d3d3d3', lightpink:'#ffb6c1', lightsalmon:'#ffa07a',
  lightseagreen:'#20b2aa', lightskyblue:'#87cefa', lightslategray:'#778899',
  lightslategrey:'#778899', lightsteelblue:'#b0c4de', lightyellow:'#ffffe0', lime:'#00ff00',
  limegreen:'#32cd32', linen:'#faf0e6', magenta:'#ff00ff', maroon:'#800000',
  mediumaquamarine:'#66cdaa', mediumblue:'#0000cd', mediumorchid:'#ba55d3',
  mediumpurple:'#9370db', mediumseagreen:'#3cb371', mediumslateblue:'#7b68ee',
  mediumspringgreen:'#00fa9a', mediumturquoise:'#48d1cc', mediumvioletred:'#c71585',
  midnightblue:'#191970', mintcream:'#f5fffa', mistyrose:'#ffe4e1', moccasin:'#ffe4b5',
  navajowhite:'#ffdead', navy:'#000080', oldlace:'#fdf5e6', olive:'#808000', olivedrab:'#6b8e23',
  orange:'#ffa500', orangered:'#ff4500', orchid:'#da70d6', palegoldenrod:'#eee8aa',
  palegreen:'#98fb98', paleturquoise:'#afeeee', palevioletred:'#db7093', papayawhip:'#ffefd5',
  peachpuff:'#ffdab9', peru:'#cd853f', pink:'#ffc0cb', plum:'#dda0dd', powderblue:'#b0e0e6',
  purple:'#800080', rebeccapurple:'#663399', red:'#ff0000', rosybrown:'#bc8f8f',
  royalblue:'#4169e1', saddlebrown:'#8b4513', salmon:'#fa8072', sandybrown:'#f4a460',
  seagreen:'#2e8b57', seashell:'#fff5ee', sienna:'#a0522d', silver:'#c0c0c0', skyblue:'#87ceeb',
  slateblue:'#6a5acd', slategray:'#708090', slategrey:'#708090', snow:'#fffafa',
  springgreen:'#00ff7f', steelblue:'#4682b4', tan:'#d2b48c', teal:'#008080', thistle:'#d8bfd8',
  tomato:'#ff6347', turquoise:'#40e0d0', violet:'#ee82ee', wheat:'#f5deb3', white:'#ffffff',
  whitesmoke:'#f5f5f5', yellow:'#ffff00', yellowgreen:'#9acd32',
};

/**
 * Resolve an arbitrary caller colour string to a hex, or null when it should be
 * treated as ABSENT (fall back to the palette).
 *
 *   - 'transparent' / 'none' / '' / zero-alpha rgba()   -> null  (D-069: was
 *     handed to the SVG `fill` literally, compositing to the background exactly
 *     and erasing the arc + every ribbon targeting it)
 *   - design-system tokens: var(--x), $blue-500, theme.accent, whitespace, or a
 *     bare word that is not a real CSS colour name (e.g. 'primary')  -> null
 *     (D-070: was handed to `fill` unvalidated -> SVG initial value BLACK)
 *   - #hex / #rrggbb, rgb()/rgba() (alpha dropped)       -> hex
 *   - a valid CSS named colour                           -> its table hex
 *
 * Exported for regression testing.
 */
export function normalizeChordColorToHex(input: any): string | null {
  const c = classifyColor(input);
  if (!c) return null;
  if (c.hex) return c.hex;
  const named = (c.named as string).toLowerCase();
  return CSS_NAMED_COLORS[named] ?? null;
}

/**
 * Resolve a categorical arc/ribbon FILL guaranteed visible on the effective
 * canvas (D-052 / D-069 / D-070).
 *
 * `rawColor` is the caller-supplied colour for arc `index` (or undefined). It is
 * resolved as follows:
 *   1. transparent/none/token/invalid-name  -> absent -> the DEFAULT_PALETTE
 *      entry for this index (D-069/D-070: no more erased arcs, no more black).
 *   2. the chosen colour (caller hex OR the palette entry) is contrast-
 *      reconciled to the 3:1 GRAPHICAL floor against the effective canvas
 *      (D-052: the dark-tuned palette has 6/10 entries below 3:1 on white —
 *      #edc948 1.61, #ff9da7 1.98, ... — so light-theme arcs are nudged toward
 *      black until readable; on a dark canvas every palette entry already clears
 *      the floor and is returned UNCHANGED, so dark output does not regress).
 *
 * A valid caller hex that already clears the floor is returned verbatim
 * (identity preserved). Exported for regression testing.
 */
export function resolveChordFill(rawColor: any, index: number, effectiveBg: string): string {
  const n = DEFAULT_PALETTE.length;
  const paletteEntry = DEFAULT_PALETTE[((index % n) + n) % n];
  const hex = normalizeChordColorToHex(rawColor);
  const base = hex ?? paletteEntry;
  return ensureReadableFill(base, effectiveBg, paletteEntry, 3);
}

/**
 * Recover a structured chord spec from a `definition`-as-JSON-string wrapper.
 *
 * `render_diagram` (app/mcp/tools/diagram_render.py) always ships the real
 * chord JSON as a STRING under `spec.definition`, with only `type` on the
 * outer wrapper. The plugin's `isChordSpec`/`render` read `matrix`/`nodes`/
 * `links` off the top-level object, so a wrapped spec never matches any
 * plugin -> "No plugin found for spec: chord" -> retry-to-timeout, zero
 * output (Issue 23; same contract-mismatch class as joint#2 / network#11 /
 * music#17).
 *
 * This lifts the structured fields (matrix | nodes+links, plus optional
 * names/colors/directed/style/width/height) from the parsed definition onto
 * a shallow copy so downstream code sees the arrays it expects. If the spec
 * is already structured, or `definition` is absent / non-JSON / carries no
 * chord content, the spec is returned unchanged (guarded — never hijacks a
 * non-chord spec).
 *
 * Exported for regression testing.
 */
/**
 * Resolve a conflicting DUAL-SHAPE chord spec.
 *
 * A chord spec may legitimately arrive in EITHER the matrix form
 * (`matrix: number[][]`, direct d3 input) OR the links form
 * (`nodes` + `links`, the richer LLM-friendly NAMED form). Some inputs
 * (Issue 50) supply BOTH at once. The render path and `isChordSpec` both
 * test `Array.isArray(spec.matrix)` FIRST, so the matrix wins
 * unconditionally and the entire nodes/links structure — potentially dozens
 * of named nodes and links plus their colors/groups — is SILENTLY DROPPED,
 * leaving a diagram of bare numeric-index arcs (major silent data loss).
 *
 * The links form carries strictly MORE information than a bare numeric
 * matrix (named nodes, per-node colors, richer link structure), so when both
 * are present the links form is the safe choice: preferring it can at worst
 * re-derive an equivalent matrix, whereas preferring the matrix throws away
 * the named structure with no recovery. This helper DROPS the `matrix` field
 * when a NON-EMPTY links form (nodes.length > 0 AND links is an array)
 * co-exists with it, so downstream shape-selection consistently uses the
 * higher-information form.
 *
 * Guards (spec returned byte-identical, ref-equal): matrix-only specs,
 * links-only specs, specs whose nodes array is empty/absent, and non-object
 * specs are all left untouched — this resolves ONLY the genuine
 * both-present conflict, it is not a catch-all rewrite.
 *
 * Exported for regression testing.
 */
/**
 * Normalize `nodes` / `links` supplied as a KEYED OBJECT MAP into the array
 * form the links path expects.
 *
 * The links form is documented as `nodes: [{id}]` / `links: [{source,target}]`,
 * but LLMs (Issue 50) routinely key `nodes` by id:
 *   `nodes: { "Alpha": { color }, "Beta": { color }, ... }`
 * Every downstream consumer (`isChordSpec`, `buildMatrix`, the render path)
 * calls `Array.isArray(nodes)`; for an object map that is FALSE, so:
 *   - if a `matrix` is ALSO present, the matrix short-circuits and the named
 *     graph is silently dropped (Issue 50 headline), and
 *   - if no matrix is present, `isChordSpec` returns false -> "No compatible
 *     plugin found" -> silent retry-to-timeout, zero output.
 * Either way the entire named graph is lost.
 *
 * This converts an object-map `nodes` to `[{ id: <key>, ...value }]` (the map
 * KEY is the node identity that links reference, so it is forced as `id`),
 * and an object-map `links` to its `Object.values`. Arrays are returned
 * REF-EQUAL (untouched) so array-form specs are behavior-identical; this is a
 * shape-normalization gap fix, not a catch-all. Also normalizes the
 * `data.nodes` / `data.links` nesting.
 *
 * Exported for regression testing.
 */
export function normalizeChordCollections(spec: any): any {
  if (typeof spec !== 'object' || spec === null) return spec;

  const isPlainMap = (v: any): boolean =>
    v !== null && typeof v === 'object' && !Array.isArray(v);

  const nodesToArray = (nodes: any): any[] =>
    Object.keys(nodes).map((key) => {
      const val = nodes[key];
      return isPlainMap(val) ? { ...val, id: key } : { id: key };
    });

  const linksToArray = (links: any): any[] => Object.values(links);

  let changed = false;
  const next: any = { ...spec };

  if (isPlainMap(spec.nodes)) { next.nodes = nodesToArray(spec.nodes); changed = true; }
  if (isPlainMap(spec.links)) { next.links = linksToArray(spec.links); changed = true; }

  if (isPlainMap(spec.data)) {
    const d = spec.data;
    if (isPlainMap(d.nodes) || isPlainMap(d.links)) {
      const nd: any = { ...d };
      if (isPlainMap(d.nodes)) nd.nodes = nodesToArray(d.nodes);
      if (isPlainMap(d.links)) nd.links = linksToArray(d.links);
      next.data = nd;
      changed = true;
    }
  }

  return changed ? next : spec;
}

export function resolveChordShapeConflict(spec: any): any {
  if (typeof spec !== 'object' || spec === null) return spec;
  const hasMatrix = Array.isArray(spec.matrix) && spec.matrix.length > 0
    && Array.isArray(spec.matrix[0]);
  if (!hasMatrix) return spec;
  const nodes = spec.nodes || spec.data?.nodes;
  const links = spec.links || spec.data?.links;
  const hasLinksForm = Array.isArray(nodes) && nodes.length > 0
    && Array.isArray(links);
  if (!hasLinksForm) return spec;
  // Both present -> prefer the richer links form; drop the conflicting matrix
  // so the render path takes the nodes/links branch instead of short-circuiting
  // on the matrix.
  const { matrix, ...rest } = spec;
  return rest;
}

export function resolveChordSpec(spec: any): any {
  if (typeof spec !== 'object' || spec === null) return spec;

  // Normalize object-map `nodes`/`links` -> array form first (Issue 50), then
  // resolve a conflicting dual-shape (both `matrix` AND `nodes`+`links`) so
  // downstream shape-selection uses the higher-information links form rather
  // than silently dropping it. Order matters: the conflict resolver requires
  // an ARRAY links form, which the normalization guarantees.
  spec = unwrapChordContainer(spec);
  spec = normalizeChordCollections(spec);
  spec = resolveChordShapeConflict(spec);

  // Already structured (matrix form OR links form)?
  const hasMatrix = Array.isArray(spec.matrix) && spec.matrix.length > 0
    && Array.isArray(spec.matrix[0]);
  const hasNodes = Array.isArray(spec.nodes) || Array.isArray(spec.data?.nodes);
  const hasLinks = Array.isArray(spec.links) || Array.isArray(spec.data?.links);
  if (hasMatrix || (hasNodes && hasLinks)) return spec;

  // Only attempt recovery from a `definition` string.
  if (typeof spec.definition !== 'string' || spec.definition.trim() === '') return spec;

  // Lenient recovery (D-064 / D-065). The old code (a) bailed when the first
  // non-space char was not '{', which rejected a markdown-fenced definition
  // (```json ... ```) outright, and (b) used a BARE strict JSON.parse whose
  // catch returned the spec unchanged — so a definition with trailing commas,
  // unquoted keys, single quotes, comments or smart quotes never parsed. Either
  // way the wrapped spec stayed unclaimable -> "No plugin found for spec: chord"
  // -> orchestrator retry-to-timeout with zero output. lenientParseObject (the
  // shared D-024 helper) strips a fence, normalises smart quotes, slices to the
  // outermost {...} (dropping leading prose / trailing ';') and falls back to
  // JSON5 when strict JSON fails; it returns undefined when unrecoverable, so a
  // genuinely non-JSON definition (e.g. "graph TD; A-->B") still leaves the spec
  // untouched and the plugin correctly declines it.
  let parsed: any = lenientParseObject(spec.definition);
  if (typeof parsed !== 'object' || parsed === null) return spec;

  // Same object-map normalization + dual-shape conflict resolution for a
  // wrapped/parsed definition.
  parsed = unwrapChordContainer(parsed);
  parsed = normalizeChordCollections(parsed);
  parsed = resolveChordShapeConflict(parsed);

  const pMatrix = Array.isArray(parsed.matrix) && parsed.matrix.length > 0
    && Array.isArray(parsed.matrix[0]);
  const pNodes = Array.isArray(parsed.nodes) || Array.isArray(parsed.data?.nodes);
  const pLinks = Array.isArray(parsed.links) || Array.isArray(parsed.data?.links);
  // Requires genuine chord content: a matrix, or nodes (+links, defaulted below).
  if (!pMatrix && !pNodes) return spec;

  const resolved: any = { ...spec };
  if (pMatrix) {
    resolved.matrix = parsed.matrix;
    if (parsed.names !== undefined) resolved.names = parsed.names;
    if (parsed.colors !== undefined) resolved.colors = parsed.colors;
  } else {
    resolved.nodes = parsed.nodes || parsed.data?.nodes;
    resolved.links = parsed.links || parsed.data?.links
      || (Array.isArray(parsed.edges) ? parsed.edges : []);
  }
  if (parsed.directed !== undefined) resolved.directed = parsed.directed;
  if (parsed.width !== undefined) resolved.width = parsed.width;
  if (parsed.height !== undefined) resolved.height = parsed.height;
  if (parsed.style !== undefined) resolved.style = parsed.style;
  return resolved;
}

/**
 * Coerce an arbitrary link `value` (or matrix cell) to a finite, non-negative
 * number suitable for d3.chord().
 *
 * d3.chord() computes arc angles from a running sum of the matrix; a single
 * NaN (from a string like `"not-a-number"` or a value d3 can't add), Infinity,
 * or a negative flow poisons that sum and every downstream arc/ribbon path
 * becomes `MNaN,NaN` -> the entire diagram silently vanishes (Issue 10 matrix
 * form). This maps NaN/Infinity/-Infinity/negative -> 0 and keeps every finite
 * non-negative magnitude (including huge 1e15 and tiny 1e-300, which d3 handles
 * proportionally). `null`/`undefined` fall back to the caller's default.
 *
 * Exported for regression testing.
 */
export function coerceFlowValue(raw: any, fallback: number = 1): number {
  // Strip thousands separators / stray whitespace from a STRING magnitude
  // before Number() (D-068): a model writing the dominant flow as "1,200"
  // otherwise hits Number("1,200")=NaN -> mapped to 0, so the very flow the
  // spec makes largest silently disappears while its comma-free siblings
  // coerce fine. Numeric inputs are untouched (byte-identical).
  let val: any = raw ?? fallback;
  if (typeof val === 'string') val = val.replace(/[,\s_]/g, '');
  const v = Number(val);
  return Number.isFinite(v) && v >= 0 ? v : 0;
}

/**
 * Coerce a canvas width/height to a positive finite number (D-068).
 *
 * width/height supplied as STRINGS ("600", "1,200", "800px") escaped numeric
 * coercion: `spec.width || 600` returned the string verbatim, which then
 * propagated into the SVG sizing / viewBox arithmetic at the wrong scale and
 * (with the fixed overflow:hidden container) clipped the bottom of the ring.
 * This parses a numeric string (stripping thousands separators, whitespace and
 * a trailing `px`), returns a genuine number when positive, and otherwise falls
 * back. A number input that is already positive/finite is returned untouched
 * (byte-identical for the common numeric-default case).
 *
 * Exported for regression testing.
 */
export function coerceChordDimension(raw: any, fallback: number = 600): number {
  if (typeof raw === 'number') return Number.isFinite(raw) && raw > 0 ? raw : fallback;
  if (typeof raw === 'string') {
    const v = Number(raw.replace(/[,\s_]/g, '').replace(/px$/i, ''));
    if (Number.isFinite(v) && v > 0) return v;
  }
  return fallback;
}

/**
 * Fit a caller-supplied `names` list to exactly `n` entries (D-067).
 *
 * The matrix branch previously accepted `names` ONLY when `.length === n`
 * exactly, so a 5-entry list against a 6×6 matrix was discarded WHOLESALE and
 * every group degraded to a bare numeric index — an off-by-one cost 100% of the
 * naming, silently. This instead PADS a short list (extra slots take their
 * positional index as the label) and TRUNCATES a long one, preserving every
 * name the caller did supply. Blank/absent entries also fall back to the index.
 *
 * Exported for regression testing.
 */
export function fitChordNames(names: any, n: number): string[] {
  const out = Array.from({ length: n }, (_, i) => String(i));
  if (Array.isArray(names)) {
    for (let i = 0; i < n && i < names.length; i++) {
      const v = names[i];
      if (v !== undefined && v !== null && String(v) !== '') out[i] = String(v);
    }
  }
  return out;
}

/**
 * Fit a caller-supplied `colors` list to exactly `n` entries (D-067).
 *
 * Same length-mismatch discard bug as `fitChordNames`: a short/long `colors`
 * list was replaced wholesale. This keeps every colour the caller supplied
 * (truncating a long list, padding a short one with `undefined`); each
 * `undefined` slot is later resolved to the categorical palette AND contrast-
 * reconciled by `resolveChordFill`, so a padded slot never imports an
 * unreconciled low-contrast palette entry.
 *
 * Exported for regression testing.
 */
export function fitChordColors(colors: any, n: number): (string | undefined)[] {
  const out: (string | undefined)[] = new Array(n).fill(undefined);
  if (Array.isArray(colors)) {
    for (let i = 0; i < n && i < colors.length; i++) out[i] = colors[i];
  }
  return out;
}

/**
 * Scale the default label font size with the canvas (D-063).
 *
 * fontSize defaulted to a fixed 11px regardless of canvas size, so on a large
 * canvas (chord-w2-14: 2000px) it fell to ~0.55% of width and, after the
 * capture's shrink-to-fit downscale, rendered as an unreadable ~7px smudge even
 * though the ring geometry scaled up with the canvas. This scales the DEFAULT
 * proportionally to the shorter canvas dimension (11px at the historical 600px
 * baseline), floored at 11px so canvases ≤600px are byte-identical, and capped
 * at 32px so an extreme canvas doesn't produce absurd type. An explicit caller
 * `style.fontSize` (> 0) is always honoured verbatim.
 *
 * Exported for regression testing.
 */
export function chordFontSize(explicit: number | undefined, minDim: number): number {
  if (typeof explicit === 'number' && explicit > 0) return explicit;
  const scaled = (minDim > 0 ? minDim : 600) * (11 / 600);
  return Math.round(Math.max(11, Math.min(scaled, 32)));
}

/**
 * Hoist a chord graph nested one level under a wrapper key (D-066).
 *
 * `resolveChordSpec`/`isChordSpec` probe `matrix`/`nodes`/`links` at the top
 * level or under `data` only. When a model nests the whole graph one level too
 * deep — `{ type:'chord', spec:{ nodes, links } }` (also `chart`/`diagram`/
 * `config`/`graph`) — shape discovery finds nothing, `isChordSpec` returns
 * false, no plugin claims the spec and the host retries to a 30s timeout with
 * zero output. This lifts the inner graph fields up to the top level (keeping
 * the outer `type`/`width`/… where the inner object doesn't override them) when
 * the top level has no graph of its own. `data` is deliberately NOT unwrapped
 * here — it is already probed downstream — so `data`-form specs stay
 * byte-identical.
 *
 * Exported for regression testing.
 */
const CHORD_WRAPPER_KEYS = ['spec', 'chart', 'diagram', 'config', 'graph', 'payload'];
export function unwrapChordContainer(spec: any): any {
  if (typeof spec !== 'object' || spec === null) return spec;
  const hasTop = Array.isArray(spec.matrix) || Array.isArray(spec.nodes) || Array.isArray(spec.links)
    || Array.isArray(spec.data?.nodes) || Array.isArray(spec.data?.links);
  if (hasTop) return spec;
  for (const key of CHORD_WRAPPER_KEYS) {
    const inner = spec[key];
    if (inner && typeof inner === 'object' && !Array.isArray(inner)
        && (Array.isArray(inner.matrix) || Array.isArray(inner.nodes)
            || Array.isArray(inner.links) || Array.isArray(inner.data?.nodes))) {
      return { ...spec, ...inner };
    }
  }
  return spec;
}

function isChordSpec(spec: any): boolean {
  const resolved = resolveChordSpec(spec);
  if (typeof resolved !== 'object' || resolved === null) return false;
  const type = resolved.type;
  if (type !== 'chord' && type !== 'chord-directed') return false;

  // Matrix form
  if (Array.isArray(resolved.matrix) && resolved.matrix.length > 0
      && Array.isArray(resolved.matrix[0])) {
    return true;
  }

  // Links form (also accepts data.nodes / data.links)
  const nodes = resolved.nodes || resolved.data?.nodes;
  const links = resolved.links || resolved.data?.links;
  return Array.isArray(nodes) && Array.isArray(links) && nodes.length > 0;
}

/**
 * Normalize a links-form `nodes` array to `ChordNode` objects.
 *
 * The links form is documented to accept `nodes: [{ id, label?, color? }]`,
 * but LLMs (and this stress corpus, Issue 38) routinely pass `nodes` as a
 * flat array of plain STRINGS: `["Alpha", "Beta", "Gamma"]`. Downstream,
 * `buildMatrix` indexes by `node.id` and the render path maps `node.label`/
 * `node.color`; for a string node every one of those is `undefined`, so the
 * index map collapses to a single `undefined -> i` entry, EVERY link's
 * `idx.get(link.source)` returns undefined, every link is skipped, the matrix
 * is all-zero, and d3.chord() emits a blank canvas — total, silent data loss.
 *
 * This coerces each entry to a `{ id }` object:
 *   - a string       -> { id: string }
 *   - a number/bool   -> { id: String(value) }  (defensive; JSON shorthand)
 *   - an object with a usable id/label/name/key -> { id, label?, color? }
 *
 * Already-object nodes with an `id` are preserved (spread) so object-form
 * specs are behavior-identical — this is a normalization gap fix, not a
 * catch-all rewrite. Entries with no derivable id (null/undefined/empty
 * object) fall back to their positional index as the id so they still occupy
 * an arc slot rather than corrupting the index map.
 *
 * Exported for regression testing.
 */
export function normalizeChordNodes(nodes: any[]): ChordNode[] {
  if (!Array.isArray(nodes)) return [];
  return nodes.map((node, i): ChordNode => {
    if (typeof node === 'string') {
      return { id: node };
    }
    if (typeof node === 'number' || typeof node === 'boolean') {
      return { id: String(node) };
    }
    if (node !== null && typeof node === 'object') {
      // Prefer an explicit id; fall back to common aliases, then index.
      const rawId = node.id ?? node.name ?? node.key ?? node.label;
      const id = rawId === undefined || rawId === null || rawId === ''
        ? String(i)
        : String(rawId);
      const out: ChordNode = { ...node, id };
      if (node.label !== undefined) out.label = String(node.label);
      if (node.color !== undefined) out.color = node.color;
      return out;
    }
    // null / undefined / other -> positional placeholder so the arc slot
    // is preserved and the index map stays 1:1 with node order.
    return { id: String(i) };
  });
}

/**
 * Build an N×N flow matrix from nodes + links.  Node order is preserved
 * (it determines arc placement around the circle).  Missing source/target
 * IDs are silently skipped — the diagram renders what it can.
 */
function buildMatrix(nodes: ChordNode[], links: ChordLink[]): number[][] {
  const n = nodes.length;
  const idx = new Map<string, number>();
  nodes.forEach((node, i) => idx.set(node.id, i));
  const matrix: number[][] = Array.from({ length: n }, () => new Array(n).fill(0));
  for (const link of links) {
    const s = idx.get(link.source);
    const t = idx.get(link.target);
    if (s === undefined || t === undefined) continue;
    matrix[s][t] += coerceFlowValue(link.value, 1);
  }
  return matrix;
}

/** Coerce every cell of a matrix-form input to a finite non-negative number. */
function sanitizeMatrix(matrix: any[][]): number[][] {
  return matrix.map(row =>
    Array.isArray(row) ? row.map(cell => coerceFlowValue(cell, 0)) : []);
}

/**
 * Resolve the default group-label colour from the EFFECTIVE canvas luminance
 * rather than the raw isDarkMode flag (D-053).
 *
 * The old code derived labelColor from isDarkMode independently of the resolved
 * background, so a caller who pinned a light `style.background` under dark theme
 * (e.g. '#f7f7f7') got the dark default '#e0e0e0' painted on a near-white panel
 * = 1.23:1, all labels erased. Choosing from the resolved background instead
 * gives '#333333' on '#f7f7f7' = 11.79:1 while still returning '#e0e0e0' on a
 * genuinely dark canvas ('#1a1a2e' = 12.92:1). An explicit caller labelColor is
 * honoured verbatim (its own contrast reconciliation is D-054 / a separate group).
 *
 * Exported for regression testing.
 */
export function resolveChordLabelColor(style: ChordStyle, effectiveBg: string): string {
  const themeDefault = isDarkBackground(effectiveBg) ? '#e0e0e0' : '#333333';
  if (style && style.labelColor) {
    // A caller labelColor is no longer passed through verbatim (D-054 / D-070).
    // An unresolvable token/invalid name (e.g. 'token.text.primary') -> the
    // theme default (was handed to SVG `fill` -> initial value BLACK: #000000
    // on #1a1a2e = 1.23:1). A resolvable colour is contrast-reconciled to the
    // 4.5 TEXT floor against the effective canvas, so a light-tuned label under
    // a dark canvas (#5a5a5a 2.47:1, dimgray #696969 3.11:1) is nudged readable
    // instead of ghosting out.
    const hex = normalizeChordColorToHex(style.labelColor);
    if (hex === null) return themeDefault;
    return ensureReadableFill(hex, effectiveBg, themeDefault, 4.5);
  }
  return themeDefault;
}

/**
 * Resolve the default arc/ribbon boundary stroke (D-051).
 *
 * The old default WAS the background colour ('#ffffff' light / '#0d0d1a' dark),
 * so the stroke never delimited an arc and any fill near the surface luminance
 * became boundary-less: '#ffffff' on '#ffffff' = 1.00:1, '#0d0d1a' on '#1a1a2e'
 * = 1.13:1. This returns a neutral stroke that clears the 3:1 graphical floor
 * against the EFFECTIVE canvas: '#555555' on light (7.46:1) / '#cfcfcf' on dark
 * (10.95:1). An explicit caller arcStroke is honoured verbatim.
 *
 * Exported for regression testing.
 */
export function resolveChordArcStroke(style: ChordStyle, effectiveBg: string): string {
  if (style && style.arcStroke) return style.arcStroke;
  return isDarkBackground(effectiveBg) ? '#cfcfcf' : '#555555';
}

/**
 * N-aware inter-arc pad angle (D-056).
 *
 * padAngle is a per-GROUP constant; total inter-arc padding is padAngle*N. A
 * fixed 0.05 sums past the full 2*pi circle at N>=126 (0.05*126 = 6.30 > 6.283),
 * starving every arc to ~0px width so the diagram silently renders no arcs at
 * all. Cap the TOTAL padding to ~20% of the circle (0.4*pi rad) so arcs always
 * keep the remaining ~80%: min(0.05, 0.4*pi / N). For N<=25 this stays 0.05, so
 * small/normal diagrams are byte-identical.
 *
 * Exported for regression testing.
 */
export function chordPadAngle(nGroups: number): number {
  return nGroups > 0 ? Math.min(0.05, (Math.PI * 0.4) / nGroups) : 0.05;
}

/**
 * Ribbon boundary stroke width; drops to 0 past the sub-pixel onset (D-057).
 *
 * At low edge count a thin contrasting stroke delimits overlapping ribbons, but
 * once ribbons go sub-pixel (high edge count) the 0.5px stroke DOMINATES the
 * shape: with the old bg-coloured stroke it erased the ribbons entirely in light
 * and composited to a solid near-black disc in dark (w2-08 = 2450/2450 ribbons
 * lost). Past ~50 ribbons the stroke is removed so dense ribbons render as their
 * (opacity-blended) fill instead of a stroke smear.
 *
 * Exported for regression testing.
 */
export function chordRibbonStrokeWidth(edgeCount: number): number {
  return edgeCount <= 50 ? 0.5 : 0;
}

/**
 * Clamp the outer/inner ring radii so a small custom canvas never produces a
 * NEGATIVE radius (D-059).
 *
 * The ring is sized as `min(width,height)*0.5 - 60` (60px reserved around the
 * circle for labels), with the inner radius 18px inside it. For any custom
 * canvas below ~120px on its smaller side that outer radius goes NEGATIVE, and
 * d3.arc()/d3.ribbon() fed a negative radius emit arc paths d3 cannot resolve
 * (NaN path data); the renderer then busy-retries to its ~30s timeout with an
 * empty canvas. This floors the outer radius at 10px and the inner radius at
 * 1px (always strictly inside the outer), so a tiny canvas renders a small but
 * VALID ring instead of nothing. For every canvas at/above the normal size the
 * arithmetic is unchanged: at outerRadius >= 19 the inner radius is exactly
 * `outerRadius - 18` as before, so normal diagrams are byte-identical.
 *
 * Exported for regression testing.
 */
export function chordRadii(width: number, height: number, gutter: number = 60): { outerRadius: number; innerRadius: number } {
  const rawOuter = Math.min(width, height) * 0.5 - gutter;
  const outerRadius = Math.max(rawOuter, 10);
  const innerRadius = Math.max(1, outerRadius - 18);
  return { outerRadius, innerRadius };
}

/**
 * Clamp the effective ribbon fill-opacity to a legibility floor (D-055).
 *
 * `ribbonOpacity` was taken verbatim from the caller (`style.ribbonOpacity ??
 * 0.7`) and applied as fill-opacity to the ribbons — which ARE the data. The
 * default 0.7 already composited below the 3:1 graphical floor vs the
 * background (worst reconciled palette entry ~2.1:1 light / ~2.5:1 dark), and a
 * caller value like 0.25 (chord-w1-12) collapsed ribbons to a ~1.1–1.3:1 ghost
 * wash in BOTH themes. Floor the effective opacity at 0.6 so ribbons never
 * become a near-invisible wash; a caller value at/above the floor (incl. the
 * 0.7 default) is honoured unchanged, so normal diagrams are byte-identical.
 * The hard per-ribbon contrast guarantee is provided by `chordRibbonFill`
 * (which nudges the fill so the COMPOSITED ribbon clears 3:1 at this opacity).
 *
 * Exported for regression testing.
 */
export function chordRibbonOpacity(requested?: number): number {
  const v = (typeof requested === 'number' && isFinite(requested)) ? requested : 0.7;
  return Math.max(0.6, Math.min(1, v));
}

function parseHex6(hex: string): [number, number, number] | null {
  const m = /^#([0-9a-fA-F]{6})$/.exec(typeof hex === 'string' ? hex.trim() : '');
  if (!m) return null;
  const n = parseInt(m[1], 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

function toHex6(rgb: number[]): string {
  return '#' + rgb.map(v => Math.max(0, Math.min(255, Math.round(v))).toString(16).padStart(2, '0')).join('');
}

/**
 * Ribbon fill guaranteed to clear the 3:1 graphical floor once composited over
 * the effective canvas at `opacity` (D-055).
 *
 * The arc `colors[]` are reconciled to 3:1 as a SOLID fill (resolveChordFill),
 * but ribbons are painted at fill-opacity < 1, and compositing a colour toward
 * the background always LOWERS its contrast — a 3:1-solid fill drops well below
 * 3:1 once blended (opacity alone can never restore it: even at 0.9 several
 * reconciled palette entries stay ~2.7:1 on white). So the arc's reconciled
 * colour is used verbatim when its COMPOSITE already clears the floor
 * (preserving the arc↔ribbon colour association, hue untouched), and only when
 * the composite is short is the fill nudged toward the surface-opposite
 * (lightness only, hue preserved) until the composited ribbon clears 3:1. Non-
 * hex/degenerate input is returned unchanged (degrades safely).
 *
 * Exported for regression testing.
 */
export function chordRibbonFill(baseHex: string, bg: string, opacity: number, minRatio: number = 3): string {
  const base = parseHex6(baseHex);
  const bgc = parseHex6(bg);
  if (!base || !bgc) return baseHex;
  const a = Math.max(0, Math.min(1, opacity));
  const composite = (rgb: number[]): string =>
    toHex6([rgb[0] * a + bgc[0] * (1 - a), rgb[1] * a + bgc[1] * (1 - a), rgb[2] * a + bgc[2] * (1 - a)]);
  if (contrastRatio(composite(base), bg) >= minRatio) return baseHex;
  const tgt = isDarkBackground(bg) ? [255, 255, 255] : [0, 0, 0];
  for (let t = 0.2; t <= 1.0001; t += 0.2) {
    const cand = [base[0] + (tgt[0] - base[0]) * t, base[1] + (tgt[1] - base[1]) * t, base[2] + (tgt[2] - base[2]) * t];
    if (contrastRatio(composite(cand), bg) >= minRatio) return toHex6(cand);
  }
  return toHex6(tgt);
}

/**
 * Cap an extreme canvas aspect ratio for the (inherently circular) chord ring
 * (D-062).
 *
 * outerRadius is bound by the SMALLER dimension, so a wildly wide/tall canvas
 * (chord-w2-12: 1800×200) leaves the ring a tiny stamp marooned in a vast empty
 * strip; when that strip is scaled to fit a viewport the whole image — labels
 * included — is downscaled to illegibility. A true circle cannot use the long
 * axis (an elliptical layout would be a disproportionate rewrite), but the
 * wasted long-axis extent can be trimmed: cap the longer side to
 * `maxAspect × shorter` so the ring sits in a squarer frame and suffers far
 * less capture downscale. Only fires for aspects beyond `maxAspect` (2.5:1);
 * every normal square-ish chord is returned unchanged.
 *
 * Exported for regression testing.
 */
export function chordCanvasSize(width: number, height: number, maxAspect: number = 2.5): { width: number; height: number } {
  const w = width > 0 ? width : 600;
  const h = height > 0 ? height : 600;
  const short = Math.min(w, h);
  const long = Math.max(w, h);
  if (long <= short * maxAspect) return { width: w, height: h };
  const capped = Math.round(short * maxAspect);
  return w >= h ? { width: capped, height: h } : { width: w, height: capped };
}

/**
 * Reserve a label gutter proportional to the longest label (D-060).
 *
 * The gutter was a hardcoded 60px, so only ~52px of radial room existed at ANY
 * canvas size and 60+ char labels were clipped ASYMMETRICALLY at the viewBox
 * edge (right-hand labels lose tails, left-hand lose heads — node identities
 * unrecoverable; chord-w2-09). Grow the gutter to fit the longest label
 * (≈0.6em per char + padding), capped at 30% of the smaller dimension so the
 * ring keeps ≥70% of its radius, and never shrink below the historical 60px
 * floor for canvases ≥200px — so short-label diagrams (the common case) keep
 * the exact 60px gutter and are byte-identical.
 *
 * Exported for regression testing.
 */
export function chordLabelGutter(names: string[], fontSize: number, minDim: number): number {
  const floor = Math.min(60, Math.max(20, minDim * 0.30));
  if (!Array.isArray(names) || names.length === 0) return floor;
  const longest = names.reduce((m, s) => Math.max(m, (s == null ? '' : String(s)).length), 0);
  const approx = longest * fontSize * 0.6 + 10;
  const cap = Math.max(floor, minDim * 0.30);
  return Math.max(floor, Math.min(approx, cap));
}

/**
 * Max characters that fit in a `gutter` of radial room at `fontSize` before an
 * ellipsis is needed (D-060). Pairs with `chordLabelGutter`: the gutter grows
 * to fit the longest label up to its cap, so truncation only bites labels that
 * exceed even the capped gutter.
 *
 * Exported for regression testing.
 */
export function chordLabelMaxChars(gutter: number, fontSize: number): number {
  return Math.max(1, Math.floor((gutter - 8) / (fontSize * 0.6)));
}

/**
 * Thin (cull) labels above a density threshold so a crowded ring shows every
 * k-th label instead of an unreadable band of overlapping glyphs (D-061).
 *
 * Labels are rotated radially, so adjacent labels are separated by the
 * tangential pitch `2π·outerRadius / N`. Once that pitch drops below ~1.5×
 * fontSize the rotated bounding boxes fan and abut into an illegible smear
 * (onset between N=80 legible and N=100). Keep every ⌈need/pitch⌉-th label so
 * the shown labels stay separated; the full name of every group (culled or not)
 * remains available in the arc's <title> tooltip. Returns 1 (keep all) for any
 * normal-density ring, so small diagrams are unchanged.
 *
 * Exported for regression testing.
 */
export function chordLabelKeepEvery(nGroups: number, outerRadius: number, fontSize: number): number {
  if (nGroups <= 0 || outerRadius <= 0) return 1;
  const pitch = (2 * Math.PI * outerRadius) / nGroups;
  const need = fontSize * 1.5;
  return pitch >= need ? 1 : Math.max(1, Math.ceil(need / pitch));
}

export const chordPlugin: D3RenderPlugin = {
  name: 'chord-renderer',
  priority: 5,
  sizingConfig: {
    sizingStrategy: 'fixed',
    // Follow the SVG's own (possibly custom) height rather than being pinned to
    // a fixed pixel height (D-058). D3Renderer applies this flag as
    // `container.style.height = needsDynamicHeight ? 'auto' : '${height}px'` and
    // `maxHeight = needsDynamicHeight ? 'none' : 'unset'` (D3Renderer.tsx). With
    // the old `false`, a spec.height taller than the fixed container height was
    // clipped by the container's overflow:hidden; `true` lets the container
    // shrink-wrap the SVG so a custom height is honoured. For matched sizes the
    // auto height resolves to the same pixels, so normal diagrams are unchanged.
    needsDynamicHeight: true,
    needsOverflowVisible: false,
    observeResize: false,
    containerStyles: { overflow: 'hidden' },
  },

  canHandle: isChordSpec,

  render: (container: HTMLElement, d3: any, rawSpec: any, isDarkMode: boolean): (() => void) => {
    // Recover a structured spec from a definition-as-JSON-string wrapper.
    const spec = resolveChordSpec(rawSpec);
    const style: ChordStyle = spec.style || {};
    // Cap an extreme canvas aspect for the circular ring so it isn't a tiny
    // stamp marooned in a vast empty strip that the capture then downscales to
    // illegibility (D-062). No-op for normal square-ish canvases (aspect ≤2.5).
    // Coerce string dimensions ("600", "1,200", "800px") to numbers before the
    // aspect cap so they scale/clip correctly (D-068), then cap an extreme
    // aspect (D-062).
    const { width, height } = chordCanvasSize(
      coerceChordDimension(spec.width, 600),
      coerceChordDimension(spec.height, 600),
    );
    const bg = style.background || (isDarkMode ? '#1a1a2e' : '#ffffff');
    // Resolve foreground defaults from the EFFECTIVE canvas luminance, not the
    // raw isDarkMode flag. A caller may pin a light panel under dark theme (or
    // vice-versa) via style.background; label and stroke contrast must track the
    // surface actually painted, not the page theme. Old code derived labelColor
    // from isDarkMode, so a light style.background under dark theme flipped the
    // labels to #e0e0e0 and erased them (#e0e0e0 on #f7f7f7 = 1.23:1) (D-053).
    const labelColor = resolveChordLabelColor(style, bg);
    // Scale the DEFAULT label font size with the canvas so type stays legible
    // after the capture downscale on a large canvas (D-063); floored at the
    // historical 11px (≤600px canvases unchanged). An explicit style.fontSize is
    // honoured verbatim.
    const fontSize = chordFontSize(style.fontSize, Math.min(width, height));
    // Floor the effective ribbon fill-opacity so ribbons (the data) never
    // collapse to an invisible wash (D-055). A caller value ≥0.6 (incl. the 0.7
    // default) is unchanged; a sub-floor request (chord-w1-12's 0.25) is raised.
    const ribbonOpacity = chordRibbonOpacity(style.ribbonOpacity);
    const hoverOpacity = style.hoverOpacity ?? 0.95;
    const fadeOpacity = style.fadeOpacity ?? 0.1;
    const arcStroke = resolveChordArcStroke(style, bg);

    // Resolve names, colors, and the flow matrix from either input shape.
    // `rawColors` holds the caller-supplied colour candidate per index (or
    // undefined). Final fills are resolved from it below via `resolveChordFill`,
    // which drops transparent/token/invalid values back to the palette (D-069/
    // D-070) and contrast-reconciles the chosen colour to the effective canvas
    // (D-052) — so the palette fallback is applied at resolution time, not baked
    // in here.
    let matrix: number[][];
    let names: string[];
    let rawColors: (string | undefined)[];

    if (Array.isArray(spec.matrix)) {
      matrix = sanitizeMatrix(spec.matrix);
      const n = matrix.length;
      // Fit names/colors to n rather than discarding a length-mismatched list
      // wholesale (D-067): a short list is padded (extra slots take their index
      // as the label / the palette as the fill), a long one truncated, so an
      // off-by-one no longer erases 100% of the supplied naming.
      names = fitChordNames(spec.names, n);
      rawColors = fitChordColors(spec.colors, n);
    } else {
      const nodes: ChordNode[] = normalizeChordNodes(spec.nodes || spec.data?.nodes || []);
      const links: ChordLink[] = (spec.links || spec.data?.links || []).map((l: any) => ({ ...l }));
      matrix = buildMatrix(nodes, links);
      names = nodes.map(node => node.label || node.id);
      rawColors = nodes.map(node => node.color);
    }

    // Resolve every arc/ribbon fill: absent/transparent/token/invalid -> the
    // categorical palette entry for that index (D-069/D-070), then contrast-
    // reconcile to the 3:1 graphical floor against the effective canvas so a
    // dark-tuned palette entry (6/10 below floor on white) is nudged readable in
    // light (D-052) while dark output is unchanged (every entry already clears
    // the floor on the dark canvas).
    const colors: string[] = rawColors.map((c, i) => resolveChordFill(c, i, bg));

    // Default to directed (matches the user's chordDirected expectation
    // and is the more common case for flow diagrams).  Pass directed:false
    // for symmetric chord layouts.
    const directed = spec.directed !== false;
    const chordLayout = directed ? d3.chordDirected() : d3.chord();
    // padAngle is a per-GROUP constant; total inter-arc padding is padAngle*N.
    // A fixed 0.05 sums past the full 2*pi circle at N>=126, starving every arc
    // to ~0px width so the diagram silently renders no arcs at all (D-056: w2-03
    // N=126, w2-15 N=150, w2-05 N=300). Cap the TOTAL padding to ~20% of the
    // circle so arcs always keep the remaining ~80%: padAngle = min(0.05,
    // 0.4*pi / N). For N<=25 this is still 0.05 (small diagrams unchanged).
    chordLayout.padAngle(chordPadAngle(matrix.length)).sortSubgroups(d3.descending);
    if (directed) chordLayout.sortChords(d3.descending);

    const chords = chordLayout(matrix);

    // Sizing — reserve a label gutter proportional to the longest label so long
    // labels are no longer clipped asymmetrically at the viewBox edge (D-060);
    // the gutter stays at the historical 60px for short-label diagrams. Radii
    // are clamped so a small custom canvas (<~120px) never yields a negative
    // radius that would emit NaN arc paths and hang the renderer (D-059).
    const minDim = Math.min(width, height);
    const labelGutter = chordLabelGutter(names, fontSize, minDim);
    const { outerRadius, innerRadius } = chordRadii(width, height, labelGutter);
    // Ellipsis-truncate labels that still exceed the (capped) gutter, and thin
    // the labels shown once the ring is too dense for them to stay separated
    // (D-060 / D-061). Full names remain in the arc <title> tooltips.
    const labelMaxChars = chordLabelMaxChars(labelGutter, fontSize);
    const labelKeepEvery = chordLabelKeepEvery(matrix.length, outerRadius, fontSize);

    // Clear container
    d3.select(container).selectAll('*').remove();

    const svg = d3.select(container)
      .append('svg')
      .attr('width', width)
      .attr('height', height)
      .attr('viewBox', [-width / 2, -height / 2, width, height])
      .style('background', bg)
      .style('border-radius', '6px');

    const arc = d3.arc().innerRadius(innerRadius).outerRadius(outerRadius);
    const ribbon = directed
      ? d3.ribbonArrow().radius(innerRadius - 1).padAngle(1 / innerRadius)
      : d3.ribbon().radius(innerRadius - 1);

    // Group arcs (the outer ring segments)
    const group = svg.append('g')
      .selectAll('g')
      .data(chords.groups)
      .join('g');

    group.append('path')
      .attr('fill', (d: any) => colors[d.index])
      .attr('stroke', arcStroke)
      .attr('stroke-width', 1)
      .attr('d', arc as any);

    // Group labels — placed just outside the arc, rotated to be readable.
    group.append('text')
      .each((d: any) => { d.angle = (d.startAngle + d.endAngle) / 2; })
      .attr('dy', '0.35em')
      .attr('transform', (d: any) =>
        `rotate(${(d.angle * 180 / Math.PI - 90)}) `
        + `translate(${outerRadius + 8}) `
        + `${d.angle > Math.PI ? 'rotate(180)' : ''}`)
      .attr('text-anchor', (d: any) => d.angle > Math.PI ? 'end' : null)
      .attr('fill', labelColor)
      .attr('font-size', `${fontSize}px`)
      .attr('font-family', 'system-ui, -apple-system, sans-serif')
      // Thin dense rings (show every k-th label) and ellipsis-truncate labels
      // that overrun the gutter; the full name stays in the arc <title> (D-060/
      // D-061). keepEvery=1 and a gutter that fits the label => no change.
      .text((d: any) =>
        (labelKeepEvery > 1 && (d.index % labelKeepEvery) !== 0)
          ? ''
          : truncateLabel(names[d.index], labelMaxChars));

    // Tooltip on the arc itself (totals in/out).
    group.append('title').text((d: any) => {
      const outgoing = matrix[d.index].reduce((a, b) => a + b, 0);
      const incoming = matrix.reduce((sum, row) => sum + row[d.index], 0);
      return `${names[d.index]}\nout: ${outgoing}\nin: ${incoming}`;
    });

    // Ribbon boundary stroke width. At low edge count a thin contrasting stroke
    // delimits overlapping ribbons, but once ribbons go sub-pixel (high edge
    // count) the 0.5px stroke DOMINATES the shape: with the old bg-coloured
    // stroke it erased the ribbons entirely in light and composited to a solid
    // near-black disc in dark (D-057: w2-08 = 2450/2450 ribbons lost). Drop the
    // stroke past the sub-pixel onset (~50 ribbons) so dense ribbons render as
    // their (opacity-blended) fill instead of a stroke smear; below it, keep the
    // thin contrasting stroke for separation.
    const ribbonStroke = chordRibbonStrokeWidth(chords.length);

    // Ribbon fills: the arc's reconciled colour is reused verbatim when it still
    // clears 3:1 once composited at the ribbon opacity (arc↔ribbon association
    // preserved), and only nudged toward the surface-opposite when the composite
    // would fall below the floor — so ribbons stay ≥3:1 vs the canvas in BOTH
    // themes instead of ghosting out at low opacity (D-055).
    const ribbonColors: string[] = colors.map(c => chordRibbonFill(c, bg, ribbonOpacity));

    // Ribbons (the chords themselves)
    const ribbons = svg.append('g')
      .attr('fill-opacity', ribbonOpacity)
      .selectAll('path')
      .data(chords)
      .join('path')
      .attr('d', ribbon as any)
      .attr('fill', (d: any) => ribbonColors[d.target.index])
      .attr('stroke', arcStroke)
      .attr('stroke-width', ribbonStroke);

    ribbons.append('title').text((d: any) =>
      `${names[d.source.index]} → ${names[d.target.index]}: ${d.source.value}`
      + (d.source.value !== d.target.value
        ? `\n${names[d.target.index]} → ${names[d.source.index]}: ${d.target.value}`
        : ''));

    // Hover behaviour — fade ribbons not connected to the hovered group.
    group.on('mouseover', function (this: any, _evt: any, hovered: any) {
      ribbons.attr('fill-opacity', (d: any) =>
        d.source.index === hovered.index || d.target.index === hovered.index
          ? hoverOpacity
          : fadeOpacity);
    }).on('mouseout', function () {
      ribbons.attr('fill-opacity', ribbonOpacity);
    });

    // No simulation to clean up — return a no-op so the host's
    // cleanup contract is honoured.
    return () => { /* nothing to tear down */ };
  },
};
