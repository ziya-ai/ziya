import { type EmbedOptions } from 'vega-embed';
import { D3RenderPlugin } from '../../types/d3';
import { getZoomScript } from '../../utils/popupScriptUtils';
import { sanitizeVegaSpec } from './vegaGraphSanitizer';
import { tolerantParseVegaSpec, KNOWN_VEGA_SCHEMES } from './vegaRecovery';
import { classifyColor, isDarkBackground } from './chartTheme';

/**
 * Full Vega renderer plugin.
 *
 * vega-embed already ships in the bundle (used by the Vega-Lite plugin) and
 * accepts both Vega and Vega-Lite specs via its `mode` option.  This thin
 * plugin gives the D3Renderer a path to render full Vega specs — unlocking
 * hierarchical layouts (sunburst, treemap, tree, circle-pack), force graphs,
 * geographic projections, word clouds, contour plots, and everything else in
 * the Vega transform/mark catalogue — without touching vegaLitePlugin.ts.
 */

// Detect a full-Vega spec (as opposed to Vega-Lite or other diagram types).
const isVegaSpec = (spec: any): boolean => {
  if (!spec || typeof spec !== 'object') return false;

  // (D-273) Vega-Lite BODY defers to the Vega-Lite plugin even when a
  // `$schema` substring or explicit marker says "vega". A full Vega spec drives
  // its scenegraph from a `marks` ARRAY; a Vega-Lite spec uses a SINGULAR
  // `mark` together with `encoding`. If we see the Vega-Lite discriminator and
  // NO `marks` array, this plugin must NOT claim it — otherwise the Vega
  // runtime is handed a body with no marks/scales and paints a SILENT BLANK
  // CANVAS (vega-w4-07). Returning false lets vega-lite-renderer (priority 8)
  // claim and render it correctly.
  if (
    spec.mark && !Array.isArray(spec.mark) &&
    spec.encoding && typeof spec.encoding === 'object' &&
    !Array.isArray(spec.marks)
  ) {
    return false;
  }

  // Explicit type marker (simplest path for LLM-generated specs)
  if (spec.type === 'vega') return true;

  // $schema that says "vega" but NOT "vega-lite"
  if (
    spec.$schema &&
    typeof spec.$schema === 'string' &&
    spec.$schema.includes('/vega/') &&
    !spec.$schema.includes('vega-lite')
  ) {
    return true;
  }

  // Structural detection: Vega uses `marks` (array), VL uses `mark` (singular)
  if (Array.isArray(spec.marks) && Array.isArray(spec.data)) return true;

  // Vega specs with `signals` + `scales` + `marks` are unambiguously Vega
  if (spec.signals && spec.scales && spec.marks) return true;

  return false;
};

/**
 * Rewrite Vega v5 JS-style method calls to Vega v6 function-call equivalents.
 * Vega v6 dropped MemberExpression callees in its expression evaluator.
 *   arr.join(sep)       → join(arr, sep)
 *   str.slice(a,b)      → slice(str, a, b)
 *   str.split(sep)      → split(str, sep)
 *   str.replace(a,b)    → replace(str, a, b)
 *   str.indexOf(v)      → indexof(str, v)
 *   str.includes(v)     → indexof(str, v) >= 0
 *   str.toLowerCase()   → lower(str)
 *   str.toUpperCase()   → upper(str)
 *   str.trim()          → trim(str)
 *   arr.reverse()       → reverse(arr)
 */
export function rewriteMethodCallsInExpr(expr: string): string {
  const METHOD_MAP: Array<[string, (lhs: string, args: string) => string]> = [
    ['join',        (e, a) => a ? `join(${e}, ${a})` : `join(${e})`],
    ['slice',       (e, a) => a ? `slice(${e}, ${a})` : `slice(${e})`],
    ['split',       (e, a) => a ? `split(${e}, ${a})` : `split(${e})`],
    ['replace',     (e, a) => `replace(${e}, ${a})`],
    ['indexOf',     (e, a) => `indexof(${e}, ${a})`],
    ['includes',    (e, a) => `indexof(${e}, ${a}) >= 0`],
    ['toLowerCase', (e, _) => `lower(${e})`],
    ['toUpperCase', (e, _) => `upper(${e})`],
    ['trim',        (e, _) => `trim(${e})`],
    ['reverse',     (e, _) => `reverse(${e})`],
  ];
  let result = expr;
  let changed = true;
  while (changed) {
    changed = false;
    for (const [method, rewriter] of METHOD_MAP) {
      const searchStr = `.${method}(`;
      const dotIdx = result.indexOf(searchStr);
      if (dotIdx === -1) continue;
      const prevChar = result[dotIdx - 1];
      let lhsStart = -1;
      if (prevChar === ')') {
        let depth = 1, i = dotIdx - 2;
        while (i >= 0 && depth > 0) {
          if (result[i] === ')') depth++;
          else if (result[i] === '(') depth--;
          i--;
        }
        i++;
        while (i > 0 && /[\w$.]/.test(result[i - 1])) i--; // (D-279) cross '.' so a dotted member path (datum.name) is kept whole
        lhsStart = i;
      } else if (/[\w$'"]/.test(prevChar)) {
        let i = dotIdx - 1;
        if (result[i] === "'" || result[i] === '"') {
          const q = result[i]; i--;
          while (i >= 0 && result[i] !== q) i--;
          lhsStart = i;
        } else {
          while (i > 0 && /[\w$.]/.test(result[i - 1])) i--; // (D-279) cross '.' so a dotted member path (datum.name) is kept whole
          lhsStart = i;
        }
      }
      if (lhsStart === -1) continue;
      const lhs = result.slice(lhsStart, dotIdx);
      let depth = 1, j = dotIdx + searchStr.length;
      while (j < result.length && depth > 0) {
        if (result[j] === '(') depth++;
        else if (result[j] === ')') depth--;
        j++;
      }
      const args = result.slice(dotIdx + searchStr.length, j - 1);
      result = result.slice(0, lhsStart) + rewriter(lhs, args) + result.slice(j);
      changed = true; break;
    }
  }
  return result;
}

/**
 * Rewrite Vega v5 let() bindings to inline expressions for v6 compatibility.
 * let(x = defExpr, bodyExpr)  →  bodyExpr with every \bx\b replaced by (defExpr)
 * Handles arbitrarily nested parens in both the definition and body.
 */
function rewriteLetExpressions(expr: string): string {
  let result = expr;
  let changed = true;
  while (changed) {
    changed = false;
    const letIdx = result.indexOf('let(');
    if (letIdx === -1) break;
    let i = letIdx + 4;
    while (i < result.length && result[i] === ' ') i++;
    const varStart = i;
    while (i < result.length && /[\w$]/.test(result[i])) i++;
    const varName = result.slice(varStart, i);
    if (!varName) break;
    while (i < result.length && result[i] === ' ') i++;
    if (result[i] !== '=') break;
    i++;
    while (i < result.length && result[i] === ' ') i++;
    let depth = 0;
    const defStart = i;
    while (i < result.length) {
      if (result[i] === '(' || result[i] === '[') depth++;
      else if ((result[i] === ')' || result[i] === ']') && depth > 0) depth--;
      else if (result[i] === ',' && depth === 0) break;
      else if ((result[i] === ')' || result[i] === ']') && depth === 0) break;
      i++;
    }
    if (result[i] !== ',') break;
    const definition = result.slice(defStart, i).trim();
    i++;
    while (i < result.length && result[i] === ' ') i++;
    const bodyStart = i;
    depth = 0;
    while (i < result.length) {
      if (result[i] === '(' || result[i] === '[') depth++;
      else if (result[i] === ')' || result[i] === ']') {
        if (depth === 0) break;
        depth--;
      }
      i++;
    }
    const body = result.slice(bodyStart, i);
    const expanded = body.replace(new RegExp(`\\b${varName}\\b`, 'g'), `(${definition})`);
    result = result.slice(0, letIdx) + expanded + result.slice(i + 1);
    changed = true;
  }
  return result;
}

const VEGA_EXPR_KEYS = new Set([
  'update', 'calculate', 'test', 'expr', 'signal', 'filter', 'where',
]);
function rewriteV5Expressions(obj: any): any {
  if (!obj || typeof obj !== 'object') return obj;
  if (Array.isArray(obj)) return obj.map(rewriteV5Expressions);
  const out: any = {};
  for (const [k, v] of Object.entries(obj)) {
    out[k] = (VEGA_EXPR_KEYS.has(k) && typeof v === 'string')
      ? rewriteLetExpressions(rewriteMethodCallsInExpr(v))
      : rewriteV5Expressions(v);
  }
  return out;
}

/**
 * (D-274) Drop any unrecognised named colour scheme from a FULL-VEGA spec.
 *
 * An unknown scheme name (e.g. a bespoke `{scheme:'ziyaDark'}`) is a FATAL Vega
 * dataflow error ("Unrecognized scheme name") thrown INSIDE the running view —
 * after vegaEmbed() has already resolved — so it escapes render()'s try/catch
 * and leaves a blank canvas the harness reports as a successful render. Dropping
 * the token lets Vega fall back to its default scheme (a visible chart).
 *
 * NOTE: the sibling `validateColorSchemes` in vegaRecovery.ts only inspects the
 * Vega-LITE `scale.scheme` shape; a full-Vega spec carries the scheme at
 * `scales[].range.scheme`, so this walker covers BOTH forms. A `#hex` value or
 * a recognised scheme name is left untouched (no-op for a correct spec). PURE +
 * exported for unit testing. Returns the number of schemes dropped.
 */
export function sanitizeVegaSchemes(spec: any): number {
  let dropped = 0;
  const dropIfUnknown = (holder: any): void => {
    if (holder && typeof holder === 'object' && typeof holder.scheme === 'string') {
      const raw = holder.scheme.trim();
      if (raw && !raw.startsWith('#') && !KNOWN_VEGA_SCHEMES.has(raw.toLowerCase())) {
        delete holder.scheme;
        dropped += 1;
      }
    }
  };
  const walk = (node: any): void => {
    if (!node || typeof node !== 'object') return;
    if (Array.isArray(node)) { node.forEach(walk); return; }
    // Full-Vega form: scales[].range.scheme. Vega-Lite form: <channel>.scale.scheme.
    if (node.range && typeof node.range === 'object') dropIfUnknown(node.range);
    if (node.scale && typeof node.scale === 'object') dropIfUnknown(node.scale);
    for (const k in node) {
      if (Object.prototype.hasOwnProperty.call(node, k)) walk(node[k]);
    }
  };
  walk(spec);
  return dropped;
}

/**
 * (D-282) Canonical d3-scale-chromatic colour arrays for the small categorical
 * schemes, IN VEGA'S OWN ORDER, so that replacing `{scheme:name}` with this
 * explicit array is byte-for-byte identical for any ordinal domain that fits
 * inside the scheme (domain[i] -> array[i] is exactly what `{scheme}` produces).
 * Only schemes whose exact ordering is known are listed; an unknown scheme is
 * left as `{scheme}` (no-op) so we can never silently recolour a spec.
 */
const CATEGORICAL_SCHEME_COLORS: Record<string, string[]> = {
  category10: ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd', '#8c564b', '#e377c2', '#7f7f7f', '#bcbd22', '#17becf'],
  tableau10: ['#4e79a7', '#f28e2c', '#e15759', '#76b7b2', '#59a14f', '#edc949', '#af7aa1', '#ff9da7', '#9c755f', '#bab0ab'],
  category20: ['#1f77b4', '#aec7e8', '#ff7f0e', '#ffbb78', '#2ca02c', '#98df8a', '#d62728', '#ff9896', '#9467bd', '#c5b0d5', '#8c564b', '#c49c94', '#e377c2', '#f7b6d2', '#7f7f7f', '#c7c7c7', '#bcbd22', '#dbdb8d', '#17becf', '#9edae5'],
};

/** HSL -> #rrggbb (deterministic, for generating extra distinct hues). */
function hslToHex(h: number, s: number, l: number): string {
  h = ((h % 360) + 360) % 360;
  const c = (1 - Math.abs(2 * l - 1)) * s;
  const x = c * (1 - Math.abs(((h / 60) % 2) - 1));
  const m = l - c / 2;
  let r = 0, g = 0, b = 0;
  if (h < 60) { r = c; g = x; } else if (h < 120) { r = x; g = c; }
  else if (h < 180) { g = c; b = x; } else if (h < 240) { g = x; b = c; }
  else if (h < 300) { r = x; b = c; } else { r = c; b = x; }
  const to = (v: number) => Math.round((v + m) * 255).toString(16).padStart(2, '0');
  return `#${to(r)}${to(g)}${to(b)}`;
}

/**
 * Extend a base categorical palette to `target` entries by appending
 * golden-angle-rotated hues after the base colours. The base prefix is
 * untouched, so output is identical for domains that fit the base.
 */
export function buildExtendedCategoricalPalette(base: string[], target = 40): string[] {
  const out = base.slice();
  let hue = 20;
  let i = 0;
  while (out.length < target) {
    hue = (hue + 137.508) % 360;
    // alternate lightness so adjacent generated hues stay distinguishable
    out.push(hslToHex(hue, 0.62, i % 2 === 0 ? 0.58 : 0.40));
    i += 1;
  }
  return out;
}

/**
 * (D-282) Stop an ordinal colour scale from RECYCLING its scheme when the
 * (data-driven) domain exceeds the scheme length. Vega maps domain[i] ->
 * scheme[i % len], so a 40-series chart on category10 gives series 0/10/20/30
 * the IDENTICAL colour — hue stops being an identifier and the legend becomes
 * misleading. Because the domain here is produced by transforms (sequence/
 * formula) it is unknowable before the view runs, so we cannot "detect" the
 * overflow statically. Instead we replace `{scheme:name}` with an explicit,
 * longer range that BEGINS with the scheme's own colours in order: identical
 * output for any domain that fit the scheme (<= base length), and distinct
 * colours instead of a silent recycle for larger domains (up to 40).
 *
 * Guarded so it can only ever be a no-op or an output-preserving extension:
 *  - only ORDINAL scales,
 *  - only a range that is EXACTLY `{scheme:<known small categorical>}` (no
 *    `count`/`extent`/other keys — those signal author intent we must respect),
 *  - only schemes whose canonical colour ordering is known.
 * PURE + exported for unit testing. Returns the number of scales extended.
 */
export function extendRecycledOrdinalSchemes(spec: any): number {
  if (!spec || typeof spec !== 'object' || !Array.isArray(spec.scales)) return 0;
  let extended = 0;
  for (const sc of spec.scales) {
    if (!sc || typeof sc !== 'object' || sc.type !== 'ordinal') continue;
    const range = sc.range;
    if (!range || typeof range !== 'object' || Array.isArray(range)) continue;
    const keys = Object.keys(range);
    if (keys.length !== 1 || keys[0] !== 'scheme' || typeof range.scheme !== 'string') continue;
    const base = CATEGORICAL_SCHEME_COLORS[range.scheme.trim().toLowerCase()];
    if (!base) continue;
    sc.range = buildExtendedCategoricalPalette(base, 40);
    extended += 1;
  }
  return extended;
}

/**
 * (D-273) Rewrite the two mechanical Vega v2 dialect shapes to their v3+ form
 * so a v2-authored spec renders instead of dying with an internal
 * "Cannot read properties of undefined" TypeError that names neither field:
 *
 *   marks[].properties  →  marks[].encode        (recursively, incl. group marks)
 *   axes[].type 'x'/'y' →  axes[].orient 'bottom'/'left'
 *
 * Both rewrites are NO-OPS for a modern (v3–v6) spec: current Vega marks never
 * carry a `properties` key and axes never carry `type:'x'|'y'`, so a correct
 * spec is returned byte-for-byte unchanged. PURE + exported for unit testing.
 */
export function rewriteVegaV2Dialect(spec: any): any {
  if (!spec || typeof spec !== 'object') return spec;

  // v2 axes are keyed by the dimension (`type:'x'|'y'`); v3+ use `orient`.
  if (Array.isArray(spec.axes)) {
    for (const ax of spec.axes) {
      if (ax && typeof ax === 'object' && !ax.orient && (ax.type === 'x' || ax.type === 'y')) {
        ax.orient = ax.type === 'x' ? 'bottom' : 'left';
        delete ax.type;
      }
    }
  }

  // v2 marks describe their visual properties under `properties`; v3+ use
  // `encode`. Recurse so nested group-mark children are rewritten too.
  const walkMarks = (marks: any): void => {
    if (!Array.isArray(marks)) return;
    for (const m of marks) {
      if (!m || typeof m !== 'object') continue;
      if (m.properties && typeof m.properties === 'object' && !m.encode) {
        m.encode = m.properties;
        delete m.properties;
      }
      if (Array.isArray(m.marks)) walkMarks(m.marks);
    }
  };
  walkMarks(spec.marks);
  return spec;
}

/* ---------------------------------------------------------------------------
 * G-34 Vega preprocessing helpers (theme + recovery)
 * ------------------------------------------------------------------------- */

// Light CSS named backgrounds we can recognise without a full name→hex table.
const VEGA_LIGHT_NAMED_BG = new Set([
  'white', 'whitesmoke', 'snow', 'ivory', 'ghostwhite', 'floralwhite',
  'seashell', 'honeydew', 'azure', 'aliceblue', 'mintcream', 'lavenderblush',
  'oldlace', 'linen', 'cornsilk', 'beige', 'lightyellow', 'lightgoldenrodyellow',
  'lightcyan', 'lavender', 'gainsboro',
]);

/**
 * True when `color` resolves to a LIGHT surface. Hex / rgb() are resolved by
 * relative luminance (>=0.5 is light); a bare CSS name is matched against the
 * small light-name set above. Unresolvable input returns false (treated as
 * "not known-light", so nothing is changed on its behalf).
 * PURE + exported for unit testing.
 */
export function isLightColor(color: any): boolean {
  const c = classifyColor(color);
  if (!c) return false;
  if (c.hex) return !isDarkBackground(c.hex);
  if (c.named) return VEGA_LIGHT_NAMED_BG.has(c.named.toLowerCase());
  return false;
}

/**
 * (D-287) In DARK mode an authored light top-level `background` is honoured
 * verbatim by the runtime while vega-embed's 'dark' theme independently whitens
 * every guide (axis / legend / title). The result is guide text painted
 * white-on-white (measured 1.00:1): a coloured grid with no axes, no scale and
 * no legend, while the data marks survive. Removing a *light* authored
 * background in dark mode lets the dark theme's own dark panel colour apply, so
 * the whitened guides regain contrast against a dark surface.
 *
 *  - LIGHT mode: never touched (author background kept verbatim).
 *  - DARK mode + authored DARK background: kept (no whiteout to fix).
 *  - DARK mode + authored LIGHT background: removed (the defect case).
 *
 * PURE + exported for unit testing.
 */
export function reconcileVegaThemeBackground(spec: any, isDarkMode: boolean): any {
  if (!spec || typeof spec !== 'object') return spec;
  if (isDarkMode && spec.background !== undefined && isLightColor(spec.background)) {
    delete spec.background;
  }
  return spec;
}

/**
 * (D-278) Detect the LEGACY sunburst spec that {@link filterVegaChromeMarks}
 * was written for. The original chrome-strip fired on any spec containing a
 * data-bound `arc` mark — but a data-bound arc is exactly what EVERY pie/donut/
 * sunburst has, so a plain donut's centre-total text mark and any group-mark
 * legend on an arc chart were silently deleted (vega-w1-05's "340 total").
 *
 * A genuine Vega sunburst derives its arcs from a hierarchical PARTITION (or
 * STRATIFY) data transform; a plain pie/donut uses a `pie` transform and may
 * carry a legitimate static centre-total text mark. Keying the strip on the
 * partition/stratify signature (in addition to the data-bound arc) fires it
 * only on the legacy sunburst and leaves donut annotations intact.
 * PURE + exported for unit testing.
 */
export function isLegacySunburstSpec(spec: any): boolean {
  if (!spec || typeof spec !== 'object' || !Array.isArray(spec.marks)) return false;
  const hasDataArc = spec.marks.some(
    (m: any) => m && typeof m === 'object' && m.type === 'arc' && m.from?.data,
  );
  if (!hasDataArc) return false;
  const data = spec.data;
  if (!Array.isArray(data)) return false;
  return data.some(
    (d: any) =>
      d && Array.isArray(d.transform) &&
      d.transform.some(
        (t: any) => t && (t.type === 'partition' || t.type === 'stratify'),
      ),
  );
}

const VEGA_LIFECYCLE_KEYS = new Set(['enter', 'update', 'exit', 'hover']);

/**
 * (D-275) Vega mark visual channels must live under an encode LIFECYCLE set
 * (`enter` / `update` / `exit` / `hover`). A common near-miss puts the channels
 * (x / y / fill / …) DIRECTLY under `encode`; Vega finds no lifecycle set,
 * emits NO warning and draws ZERO marks — a fully-furnished empty chart that
 * reads as a real chart of no data (vega-w4-09). If a mark's `encode` holds
 * channel-shaped keys and NONE of the four lifecycle keys, wrap it in
 * `{update: …}`. A correct spec (any encode with a lifecycle key) is left
 * byte-for-byte unchanged. PURE + exported for unit testing.
 */
export function normalizeVegaEncodeLifecycle(spec: any): any {
  if (!spec || typeof spec !== 'object') return spec;
  const walk = (marks: any): void => {
    if (!Array.isArray(marks)) return;
    for (const m of marks) {
      if (!m || typeof m !== 'object') continue;
      const enc = m.encode;
      if (enc && typeof enc === 'object' && !Array.isArray(enc)) {
        const keys = Object.keys(enc);
        if (keys.length > 0 && !keys.some((k) => VEGA_LIFECYCLE_KEYS.has(k))) {
          m.encode = { update: enc };
        }
      }
      if (Array.isArray(m.marks)) walk(m.marks);
    }
  };
  walk(spec.marks);
  return spec;
}

const VEGA_X_CHANNELS = new Set(['x', 'x2', 'xc', 'width']);
const VEGA_Y_CHANNELS = new Set(['y', 'y2', 'yc', 'height']);
const VEGA_DATA_MARK_TYPES = new Set([
  'rect', 'symbol', 'line', 'area', 'arc', 'rule', 'trail', 'point', 'bar', 'path', 'shape',
]);

/**
 * (D-275) Light, conservative defaulting for the "every optional-but-assumed
 * field omitted" shape (vega-w4-15) that errors on the FIRST omission only
 * ("Undefined data set name: t"), so each omission would otherwise need its own
 * round trip. Three inferences, each guarded so a correct spec is untouched:
 *
 *   1. Unnamed dataset naming — if scales/marks reference a data NAME that no
 *      dataset defines AND there is exactly ONE unnamed dataset, give it that
 *      name.
 *   2. Scale range inference — a range-less scale that a mark uses to drive an
 *      x-channel gets `range:'width'`; a y-channel gets `range:'height'`.
 *   3. Mark data binding — a from-less DATA mark (rect/symbol/… , never a
 *      static text/annotation) whose encode references a scale is clearly
 *      data-driven, so bind it to the sole dataset.
 *
 * Every branch no-ops unless its precise precondition holds. PURE + exported.
 */
export function applyVegaMinimalDefaults(spec: any): any {
  if (!spec || typeof spec !== 'object') return spec;

  const data = Array.isArray(spec.data) ? spec.data : [];
  const definedNames = new Set<string>(
    data.filter((d: any) => d && typeof d.name === 'string' && d.name).map((d: any) => d.name),
  );

  // Collect referenced dataset names (scale domains + mark.from.data).
  const referenced: string[] = [];
  const collectRef = (name: any) => {
    if (typeof name === 'string' && name && !referenced.includes(name)) referenced.push(name);
  };
  if (Array.isArray(spec.scales)) {
    for (const s of spec.scales) if (s?.domain?.data) collectRef(s.domain.data);
  }
  const walkFrom = (marks: any): void => {
    if (!Array.isArray(marks)) return;
    for (const m of marks) {
      if (m?.from?.data) collectRef(m.from.data);
      if (Array.isArray(m?.marks)) walkFrom(m.marks);
    }
  };
  walkFrom(spec.marks);

  // (1) name a single unnamed dataset that something references but nothing defines.
  const undefinedRefs = referenced.filter((n) => !definedNames.has(n));
  const unnamed = data.filter((d: any) => d && !d.name);
  if (undefinedRefs.length >= 1 && unnamed.length === 1) {
    unnamed[0].name = undefinedRefs[0];
    definedNames.add(undefinedRefs[0]);
  }

  const soleDataName: string | undefined =
    definedNames.size === 1
      ? (Array.from(definedNames)[0] as string)
      : data.length === 1 && data[0]?.name
        ? data[0].name
        : undefined;

  // Build scale -> axis(x/y) map from mark encode channels.
  const scaleAxis: Record<string, 'x' | 'y'> = {};
  const scanEnc = (marks: any): void => {
    if (!Array.isArray(marks)) return;
    for (const m of marks) {
      const enc = m?.encode;
      if (enc && typeof enc === 'object') {
        for (const set of Object.values(enc)) {
          if (!set || typeof set !== 'object') continue;
          for (const [ch, def] of Object.entries(set as any)) {
            const scaleName = (def as any)?.scale;
            if (typeof scaleName === 'string') {
              if (VEGA_X_CHANNELS.has(ch)) scaleAxis[scaleName] = 'x';
              else if (VEGA_Y_CHANNELS.has(ch)) scaleAxis[scaleName] = 'y';
            }
          }
        }
      }
      if (Array.isArray(m?.marks)) scanEnc(m.marks);
    }
  };
  scanEnc(spec.marks);

  // (2) infer a missing scale range from the channel it drives.
  if (Array.isArray(spec.scales)) {
    for (const s of spec.scales) {
      if (s && typeof s === 'object' && s.range === undefined && s.name && scaleAxis[s.name]) {
        s.range = scaleAxis[s.name] === 'x' ? 'width' : 'height';
      }
    }
  }

  // (3) bind a from-less DATA mark that references a scale to the sole dataset.
  if (soleDataName) {
    const bind = (marks: any): void => {
      if (!Array.isArray(marks)) return;
      for (const m of marks) {
        if (m && typeof m === 'object' && VEGA_DATA_MARK_TYPES.has(m.type) && !m.from) {
          let usesScale = false;
          const enc = m.encode;
          if (enc && typeof enc === 'object') {
            for (const set of Object.values(enc)) {
              if (set && typeof set === 'object') {
                for (const def of Object.values(set as any)) {
                  if ((def as any)?.scale) usesScale = true;
                }
              }
            }
          }
          if (usesScale) m.from = { data: soleDataName };
        }
        if (Array.isArray(m?.marks)) bind(m.marks);
      }
    };
    bind(spec.marks);
  }
  return spec;
}

/**
 * Decide which marks to keep when rendering a Vega spec.
 *
 * HISTORY / Issue 15: the plugin was originally written around one specific
 * "sunburst" spec whose title/footer/legend were authored as static `text`
 * marks + `group` (legend) marks alongside the data-bound `arc` marks. Those
 * chrome marks are rendered as HTML outside the SVG, so the plugin stripped
 * `group` and static (non-data-bound) `text` marks from the Vega spec to avoid
 * double-rendering them. That strip was applied UNCONDITIONALLY to every Vega
 * spec — so ANY spec that legitimately uses `group` marks (faceting, layering,
 * nested-group layouts, legends) or static `text` marks had part or ALL of its
 * scenegraph silently deleted. Issue 15's 5-level group nesting lost 100% of
 * its marks → empty container → the headless harness screenshotted the
 * surrounding SPA instead of a chart.
 *
 * GENERAL FIX: only perform the chrome-strip for the sunburst SIGNATURE — a
 * spec that actually contains a data-bound `arc` mark. Every other spec keeps
 * its marks verbatim. As a belt-and-suspenders guard, if the strip would empty
 * the mark list, the original marks are returned unchanged (a chart is never
 * silently reduced to nothing).
 *
 * PURE + exported so it can be unit-tested without a DOM.
 */
export function filterVegaChromeMarks(marks: any): any {
  if (!Array.isArray(marks)) return marks;
  // Sunburst signature: at least one data-bound arc mark. Absent that, this is
  // a general Vega spec and stripping group/static-text marks would corrupt or
  // erase it — so leave the marks untouched.
  const hasDataArc = marks.some(
    (m) => m && typeof m === 'object' && m.type === 'arc' && m.from?.data,
  );
  if (!hasDataArc) return marks;

  const filtered = marks.filter((mark: any) => {
    if (!mark || typeof mark !== 'object') return true;
    // Keep arc and text-on-arc marks; remove standalone text and group (legend) marks
    if (mark.type === 'arc') return true;
    if (mark.type === 'text' && mark.from?.data) return true; // text labels on data arcs
    // Remove static text marks (title, footer) and group marks (legend)
    if (mark.type === 'text' && !mark.from?.data) return false;
    if (mark.type === 'group') return false;
    return true;
  });
  // Never let the chrome-strip empty the scenegraph.
  return filtered.length > 0 ? filtered : marks;
}

/**
 * Escape a string for safe inclusion in SVG/HTML text content.
 * Prevents a malformed-spec error message (which can contain `<`, `>`, `&`,
 * quotes, or even a `javascript:`-shaped fragment lifted from the offending
 * spec) from breaking the placeholder markup or injecting nodes.
 */
export function escapeForSvgText(s: string): string {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

/**
 * Build the SVG markup for a Vega render-error placeholder.
 *
 * PURE + exported for unit testing (no DOM required). The key contract this
 * satisfies: the returned markup contains a real `<svg>` element. The headless
 * render harness (DiagramRenderPage) detects render completion via a
 * MutationObserver watching for an `<svg>`/`<canvas>`/`<img>` inside the
 * container. When the Vega runtime throws synchronously on a malformed spec
 * (e.g. `Expression parse error: 1 +++ 2`), the plugin previously let the
 * error propagate to D3Renderer, which discarded the (detached) render
 * container and only set React error state the harness could NOT observe — so
 * the ONLY terminal path was the 30s safety watchdog: a ~1s-known error
 * decayed into a 30s "timeout-no-output" with no surfaced message. Emitting an
 * error-placeholder SVG instead makes the failure a fast, terminal, visible
 * outcome.
 */
export function buildVegaErrorSvgMarkup(message: string, isDarkMode: boolean): string {
  const bg = isDarkMode ? '#2a1215' : '#fff2f0';
  const border = isDarkMode ? '#a61d24' : '#ffccc7';
  const fg = isDarkMode ? '#ff7875' : '#cf1322';
  const sub = isDarkMode ? '#d9a7a7' : '#a8071a';
  const safe = escapeForSvgText(message);
  // Wrap long messages onto multiple <tspan> lines (~64 chars/line) so the
  // full parse error stays legible.
  const raw = String(message);
  const lines: string[] = [];
  const CHUNK = 64;
  for (let i = 0; i < raw.length && lines.length < 8; i += CHUNK) {
    lines.push(escapeForSvgText(raw.slice(i, i + CHUNK)));
  }
  if (lines.length === 0) lines.push(safe);
  const tspans = lines
    .map((ln, idx) => `<tspan x="20" dy="${idx === 0 ? 0 : 18}">${ln}</tspan>`)
    .join('');
  const height = 70 + lines.length * 18;
  return (
    `<svg xmlns="http://www.w3.org/2000/svg" width="100%" viewBox="0 0 640 ${height}" ` +
    `role="img" aria-label="Vega render error" data-vega-error="true" ` +
    `style="max-width:100%;font-family:system-ui,-apple-system,sans-serif;display:block">` +
    `<rect x="1" y="1" width="638" height="${height - 2}" rx="6" fill="${bg}" stroke="${border}" stroke-width="1.5"/>` +
    `<text x="20" y="28" fill="${fg}" font-size="14" font-weight="bold">⚠ Vega render error</text>` +
    `<text x="20" y="52" fill="${sub}" font-size="12">${tspans}</text>` +
    `</svg>`
  );
}

/**
 * Paint a Vega render-error placeholder into the container.
 * DOM-touching wrapper around {@link buildVegaErrorSvgMarkup}. Returns the
 * error message (for logging/testing convenience).
 */
export function renderVegaErrorPlaceholder(
  container: HTMLElement,
  err: unknown,
  isDarkMode: boolean,
): string {
  const message =
    err instanceof Error ? err.message : (typeof err === 'string' ? err : 'Vega render failed');
  try {
    container.innerHTML = buildVegaErrorSvgMarkup(message, isDarkMode);
  } catch {
    /* container may be a non-standard element in tests; ignore */
  }
  return message;
}

/**
 * (D-286) Build the vega-embed options, injecting a readable default text-mark
 * fill in DARK mode.
 *
 * vega-embed's built-in 'dark' theme restyles GUIDES (axis / legend / title
 * labels) but NEVER touches raw {type:'text'} marks — those keep Vega's default
 * mark fill #000000, which measures 1.66:1 on the ~#333 dark panel, so any
 * spec whose annotation layer is a text mark loses that layer entirely in dark.
 * Merging a `config.text.fill` over the named theme fixes every such spec at
 * once (#e6e6e6 = 10.12:1 on #333333, well above the 4.5 text floor). This is a
 * DEFAULT only — a spec that sets its own text-mark fill still wins.
 *
 * (D-280/D-281) Both themes also carry a `config.axis` default (labelOverlap +
 * labelLimit:0) so dense band axes thin instead of smear and distinct long
 * labels stop truncating to identical prefixes; see the inline note. These are
 * config defaults too, so a spec's explicit axis props override them and any
 * chart whose labels neither collide nor overrun 180px is unchanged.
 *
 * PURE + exported for unit testing (no DOM / no vega-embed instance required).
 */
export function buildVegaEmbedOptions(isDarkMode: boolean): EmbedOptions {
  const opts: EmbedOptions = {
    mode: 'vega' as const,
    actions: false,
    theme: isDarkMode ? 'dark' : undefined,
    renderer: 'svg',
    scaleFactor: 1,
  };
  // (D-280/D-281) Axis DEFAULTS injected for BOTH themes:
  //   labelOverlap:true — a high-cardinality band axis (400 bands / 900px, 120
  //     labels / 840px) otherwise emits EVERY tick label and smears them into an
  //     unreadable solid strip; Vega defaults labelOverlap to false for band/
  //     point scales, so nothing thins them. `true` hides only labels that
  //     actually collide — a no-op on any axis whose labels already fit.
  //   labelLimit:0 — Vega's default 180px labelLimit truncates long category
  //     names to a ~35-char ellipsised prefix BEFORE they reach the axis, so two
  //     distinct names sharing a long prefix collapse to the byte-identical
  //     string and the axis stops identifying its rows. 0 = no truncation, so
  //     distinct labels stay distinct. Only affects labels that exceeded 180px.
  // These live in `config.axis` (DEFAULTS), so a spec's own `axes[]` properties
  // still win, and ordinary charts — whose labels neither collide nor overrun
  // 180px — render byte-for-byte as before.
  const config: Record<string, any> = { axis: { labelOverlap: true, labelLimit: 0 } };
  if (isDarkMode) {
    // Readable default text-mark fill in dark (raw {type:'text'} marks are never
    // restyled by vega-embed's 'dark' theme; #e6e6e6 = 10.12:1 on #333333).
    config.text = { fill: '#e6e6e6' };
  }
  opts.config = config;
  return opts;
}

/**
 * (D-277) Decide whether to RE-TICK the Vega view at the delivered size instead
 * of uniformly scaling the authored geometry (which shrinks type to 1-4px above
 * the container, or magnifies label collisions ~18x below it).
 *
 * Keys on the AUTHORED canvas being intrinsically un-scalable into a normal
 * content column — extremely wide/large (>maxLegibleWidth: w2-07 2400px,
 * w2-10 3600px → titles/ticks land sub-4px) or extremely small
 * (<minLegibleWidth: w2-09 70×45 → 24 labels magnified into a scribble). Normal
 * specs (minLegibleWidth..maxLegibleWidth) return null and are left EXACTLY as
 * before, so ordinary charts (and the regression set) do not change. When it
 * fires, the caller re-runs the view at these dims so Vega re-ticks/re-lays text
 * at real pixel size, preserving the authored aspect ratio.
 *
 * PURE + exported for unit testing.
 */
export function computeReTickDimensions(
  authoredW: number,
  authoredH: number,
  containerW: number,
  opts?: { minLegibleWidth?: number; maxLegibleWidth?: number; fallbackWidth?: number },
): { width: number; height: number } | null {
  const minW = opts?.minLegibleWidth ?? 200;
  const maxW = opts?.maxLegibleWidth ?? 1600;
  const fallbackW = opts?.fallbackWidth ?? 700;
  if (!(authoredW > 0) || !(authoredH > 0)) return null;
  // Normal authored width → leave untouched (no re-tick, no regression).
  if (authoredW >= minW && authoredW <= maxW) return null;
  const target = containerW > 0 ? containerW : fallbackW;
  const targetW = Math.min(maxW, Math.max(minW, Math.round(target)));
  const targetH = Math.max(1, Math.round(authoredH * (targetW / authoredW)));
  return { width: targetW, height: targetH };
}

/**
 * (D-283) Resolve the responsive viewBox for the rendered SVG.
 *
 * postRenderSizing normally uses getBBox() so rotated axis labels that overflow
 * the authored viewport a LITTLE are not clipped (D-276). But a geographic spec
 * (mercator + world graticule) projects a scenegraph that overflows the authored
 * canvas by MANY multiples; getBBox then expands the viewBox to that world-sized
 * extent and the intended regional map shrinks to a few percent (the "flood +
 * bbox-shrink"). When the measured content bbox exceeds the authored viewport by
 * more than `floodFactor`, honour the authored viewport (the autosize:'none'
 * intent) and signal a CLIP so the world-scale spill is cut to the canvas
 * instead of shrinking the whole chart. A small overflow (< floodFactor) keeps
 * the D-276 getBBox+overflow-visible behaviour unchanged.
 *
 * PURE + exported for unit testing.
 */
export function resolveVegaViewBox(
  authoredW: number,
  authoredH: number,
  bboxX: number,
  bboxY: number,
  bboxW: number,
  bboxH: number,
  opts?: { floodFactor?: number },
): { x: number; y: number; w: number; h: number; clip: boolean } {
  const floodFactor = opts?.floodFactor ?? 3;
  const useBBox = { x: bboxX, y: bboxY, w: bboxW, h: bboxH, clip: false };
  if (!(authoredW > 0) || !(authoredH > 0) || !(bboxW > 0) || !(bboxH > 0)) return useBBox;
  const overflow = Math.max(bboxW / authoredW, bboxH / authoredH);
  if (overflow > floodFactor) {
    return { x: 0, y: 0, w: authoredW, h: authoredH, clip: true };
  }
  return useBBox;
}

export const vegaPlugin: D3RenderPlugin = {
  name: 'vega-renderer',
  // Higher than vega-lite-renderer (8) so we claim full Vega specs first.
  // VL's canHandle won't match these anyway ($schema check, singular `mark`),
  // but the priority ordering makes the intent explicit.
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
      overflow: 'visible',
    },
  },

  canHandle: (spec: any): boolean => {
    // Handle string specs that might be JSON. (D-272) Use the tolerant parser
    // so a near-miss shape (trailing comma, unquoted keys, single/smart quotes,
    // ```json fence, comments, trailing ';') is still recognised as a Vega spec
    // rather than being declined at a bare JSON.parse and left to hang.
    if (typeof spec === 'string') {
      try {
        return isVegaSpec(tolerantParseVegaSpec(spec));
      } catch {
        return false;
      }
    }
    // Handle wrapper objects with a definition field
    if (spec?.type === 'vega' && spec?.definition) return true;
    return isVegaSpec(spec);
  },

  render: async (
    container: HTMLElement,
    _d3: any,
    spec: any,
    isDarkMode: boolean,
  ): Promise<void> => {
    // FAIL-FAST GUARD (Issue 15): the Vega runtime throws synchronously on a
    // malformed spec (e.g. an invalid signal expression `1 +++ 2`). If that
    // error propagates out of this render(), D3Renderer discards the detached
    // render container and only sets React error state — which the headless
    // harness (DiagramRenderPage) cannot observe, so its ONLY terminal path is
    // the 30s safety watchdog. Result: an error KNOWN in ~1s decays into a 30s
    // "timeout-no-output" with no surfaced message. By catching here and
    // painting an error-placeholder <svg> into the container, the harness's
    // MutationObserver sees the <svg> immediately → fast, terminal, visible
    // failure carrying the precise Vega message. General: covers EVERY
    // synchronous/async Vega error (parse errors, bad projections, scale math),
    // not just this expression case.
    try {
    const vegaEmbedModule = await import('vega-embed');
    const vegaEmbed = vegaEmbedModule.default;

    // Resolve the actual Vega spec from possible wrapper formats.
    // (D-272) Tolerant parse ahead of JSON.parse: strips a ```json fence,
    // normalises smart quotes, slices to the outermost {...} (drops prose /
    // trailing ';') and falls back to JSON5 (trailing commas, unquoted keys,
    // single quotes, comments). It throws only when truly unrecoverable, and
    // that throw is caught by this render()'s outer try/catch and painted as a
    // styled error placeholder — so error SURFACING is preserved while six
    // previously-fatal near-miss shapes now recover into real charts.
    let vegaSpec: any;
    if (typeof spec === 'string') {
      vegaSpec = tolerantParseVegaSpec(spec);
    } else if (spec.definition && typeof spec.definition === 'string') {
      vegaSpec = tolerantParseVegaSpec(spec.definition);
    } else if (spec.definition && typeof spec.definition === 'object') {
      vegaSpec = spec.definition;
    } else {
      // Clone and strip our internal properties
      const { type, isStreaming, isMarkdownBlockClosed, forceRender, ...rest } = spec;
      vegaSpec = rest;
    }

    // Normalise schema — any older Vega schema (v2..v5) must point to v6 to
    // match the installed runtime. (D-273) Widened from a v5-only test to "any
    // /vega/ schema that isn't already v6" so a v2 dialect spec no longer keeps
    // a stale schema URL that trips a version mismatch before the mechanical
    // v2→v3 rewrites below can help it.
    if (!vegaSpec.$schema || (typeof vegaSpec.$schema === 'string' &&
        vegaSpec.$schema.includes('/vega/') &&
        !vegaSpec.$schema.includes('vega-lite') && !vegaSpec.$schema.includes('v6'))) {
      vegaSpec.$schema = 'https://vega.github.io/schema/vega/v6.json';
    }
    // (D-273) Rewrite the mechanical Vega v2 dialect shapes (marks.properties →
    // encode, axes.type → orient) BEFORE the runtime sees them. No-op for v3–v6.
    vegaSpec = rewriteVegaV2Dialect(vegaSpec);
    // Rewrite v5 expression syntax to v6 function-call form
    vegaSpec = rewriteV5Expressions(vegaSpec);

    // (D-275) Recover two unambiguous-intent shapes the runtime silently drops:
    // channels placed directly under `encode` (no lifecycle set → zero marks),
    // and specs with every optional-but-assumed field omitted (unnamed dataset,
    // range-less scale, from-less data mark). Both no-op for a correct spec.
    vegaSpec = normalizeVegaEncodeLifecycle(vegaSpec);
    vegaSpec = applyVegaMinimalDefaults(vegaSpec);

    // (D-287) In dark mode, drop a *light* authored top-level background so the
    // dark theme's own dark panel applies and its whitened guides regain
    // contrast (a light bg + dark-theme-whitened guides = white-on-white). Light
    // mode and dark-on-dark are untouched.
    vegaSpec = reconcileVegaThemeBackground(vegaSpec, isDarkMode);

    // (Issue 34) Sanitize degenerate graph/geometry data BEFORE the runtime
    // touches it: drop force-`link` links whose endpoint doesn't resolve to a
    // node (d3-force throws `node not found` → blank canvas) and GeoJSON
    // features with null geometry/coordinates (d3-geo throws per feature). Both
    // are no-ops for specs without force/geoshape transforms.
    try {
      sanitizeVegaSpec(vegaSpec);
    } catch { /* never let sanitization itself break a render */ }

    // (D-274) Drop any unrecognised named colour scheme (e.g. a bespoke
    // `{scheme:'ziyaDark'}`) BEFORE the runtime touches it. An unknown scheme
    // name is a fatal Vega dataflow error ("Unrecognized scheme name") thrown
    // INSIDE the running view — after vegaEmbed() has already resolved — so it
    // escapes render()'s try/catch entirely and leaves a blank canvas the
    // harness reports as a SUCCESSFUL render. Dropping it lets Vega fall back to
    // its default scheme (a visible chart) instead. No-op for known schemes.
    try {
      sanitizeVegaSchemes(vegaSpec);
    } catch { /* scheme validation must never itself break a render */ }

    // (D-282) Stop an ordinal colour scale from silently recycling its scheme
    // when a (transform-generated, so statically-unknowable) domain exceeds the
    // scheme length — hue would stop identifying series and the legend would
    // mislead. Replaces `{scheme:<small categorical>}` with an explicit range
    // that begins with that scheme's own colours (identical for small domains,
    // distinct instead of recycled for large ones). No-op for other schemes.
    try {
      extendRecycledOrdinalSchemes(vegaSpec);
    } catch { /* palette extension must never itself break a render */ }

    container.innerHTML = '';
    container.style.position = 'relative';

    // --- Build HTML chrome (title, legend, footer) outside the SVG ---
    // This avoids coordinate issues when postRenderSizing rewrites the viewBox.
    const title = vegaSpec.title?.text || '';
    const titleEl = document.createElement('div');
    titleEl.style.cssText = `text-align:center; font-size:16px; font-weight:bold; color:${isDarkMode ? '#ddd' : '#333'}; padding:8px 0 4px; font-family:system-ui,-apple-system,sans-serif;`;
    titleEl.textContent = title || '';
    if (title) container.appendChild(titleEl);
    // Remove title from spec so Vega doesn't also render it
    delete vegaSpec.title;

    const legendEl = document.createElement('div');
    legendEl.style.cssText = `position:absolute; top:${title ? '36px' : '8px'}; right:12px; z-index:10; font-size:12px; font-family:system-ui,-apple-system,sans-serif; color:${isDarkMode ? '#bbb' : '#555'}; line-height:1.8;`;
    // Build legend from marks if spec has a known color scheme
    // (populated below after we inspect the data)
    container.appendChild(legendEl);

    // (D-285) The 'Hover any section for line details' footer is DEMO furniture
    // (it only ever updates from a `hoveredMove` signal that the legacy chess
    // spec declares). Injecting it under every chart printed a factually-false
    // caption beneath bar charts, histograms, maps and treemaps, and on the two
    // silent-blank specs it was the ONLY ink — making a blank read as "rendered".
    // Gate it on the spec ACTUALLY declaring the hover signal it reflects; when
    // shown, raise the light colour to the 4.5 text floor (#767676 = 4.54:1 on
    // white; dark #888 = 4.54:1 on the #212121 page). Non-hover specs get no
    // footer at all.
    const declaresHoverFooter =
      Array.isArray(vegaSpec.signals) &&
      vegaSpec.signals.some((s: any) => s && s.name === 'hoveredMove');
    let footerEl: HTMLDivElement | null = null;
    if (declaresHoverFooter) {
      footerEl = document.createElement('div');
      footerEl.style.cssText = `text-align:center; font-size:13px; font-style:italic; color:${isDarkMode ? '#888' : '#767676'}; padding:4px 0 8px; font-family:system-ui,-apple-system,sans-serif;`;
      footerEl.textContent = 'Hover any section for line details';
      container.appendChild(footerEl);
    }

    const renderDiv = document.createElement('div');
    // (D-276) overflow:visible, not hidden — a hidden wrapper HARD-CLIPS any
    // content whose authored aspect exceeds the container aspect (radial/tall/
    // wide specs lost their extremities). With preserveAspectRatio 'xMidYMid
    // meet' the SVG already letterboxes to fit the width; letting it overflow
    // the wrapper (rather than being clipped by it) is what makes the fit
    // aspect-preserving instead of destructive.
    renderDiv.style.cssText = 'width:100%; max-width:100%; overflow:visible; box-sizing:border-box;';
    container.appendChild(renderDiv);

    // --- Strip title/legend/footer chrome marks ONLY for the LEGACY sunburst ---
    // (Issue 15 / D-278) The chrome-strip fires ONLY on a genuine sunburst
    // signature (a data-bound arc PLUS a partition/stratify hierarchy transform).
    // A plain pie/donut — same data-bound arc, but no hierarchy transform — is
    // NOT stripped, so its static centre-total text mark and any group-mark
    // legend survive. Every non-sunburst spec keeps its full scenegraph.
    if (vegaSpec.marks && Array.isArray(vegaSpec.marks) && isLegacySunburstSpec(vegaSpec)) {
      vegaSpec.marks = filterVegaChromeMarks(vegaSpec.marks);
    }

    // --- Strip signals that drove the removed footer text ---
    if (vegaSpec.signals && Array.isArray(vegaSpec.signals)) {
      // Keep signals but we'll use them for the HTML footer via the Vega view API
    }

    // (D-286) Dark branch injects a readable default text-mark fill so raw
    // {type:'text'} annotation marks (which vega-embed's 'dark' theme never
    // restyles) are visible on the dark panel instead of default-black 1.66:1.
    const embedOptions: EmbedOptions = buildVegaEmbedOptions(isDarkMode);

    const result = await Promise.race([
      vegaEmbed(renderDiv, vegaSpec, embedOptions),
      new Promise<never>((_, reject) =>
        setTimeout(() => reject(new Error('Vega render timeout after 15 seconds')), 15000),
      ),
    ]);

    // Store view reference for cleanup
    (container as any)._vegaView = result.view;

    // (D-274) Route errors raised by the RUNNING view (dataflow / async
    // reactive re-runs) into the same error placeholder the setup path uses.
    // render()'s outer try/catch only wraps the awaited embed + synchronous
    // setup; a rejection raised later by the view (e.g. an unresolved scheme
    // token or a transform TypeError) is NOT on that path, so without this
    // listener it escapes entirely — the harness sees the earlier successful
    // embed and reports a SUCCESSFUL render while the user gets an empty panel.
    try {
      if (result.view && typeof result.view.addEventListener === 'function') {
        result.view.addEventListener('error', (evtErr: unknown) => {
          renderVegaErrorPlaceholder(container, evtErr, isDarkMode);
        });
      }
    } catch { /* view error API varies by Vega version; never fail the render */ }

    // Wire up hover signal to HTML footer
    try {
      if (footerEl) {
        result.view.addSignalListener('hoveredMove', (_name: string, value: any) => {
          if (footerEl) footerEl.textContent = value || 'Hover any section for line details';
        });
      }
    } catch { /* signal may not exist in all specs */ }

    // Populate legend from data analysis
    const dataValues = vegaSpec.data?.[0]?.values || [];
    const hasTraps = dataValues.some((d: any) => d.trap);
    const hasGambits = dataValues.some((d: any) => d.gambit);
    const hasAdv = dataValues.some((d: any) => d.adv !== undefined);
    if (hasTraps || hasGambits || hasAdv) {
      const items: string[] = [];
      if (hasAdv) {
        items.push(`<span style="display:inline-block;width:14px;height:14px;background:hsl(145,75%,42%);border-radius:3px;vertical-align:middle;margin-right:6px"></span> ✅ White winning`);
        items.push(`<span style="display:inline-block;width:14px;height:14px;background:hsl(145,25%,22%);border-radius:3px;vertical-align:middle;margin-right:6px"></span> Equal position`);
      }
      if (hasGambits) {
        items.push(`<span style="display:inline-block;width:14px;height:14px;background:hsl(215,65%,42%);border-radius:3px;vertical-align:middle;margin-right:6px"></span> ⚔ White gambit`);
      }
      if (hasTraps) {
        items.push(`<span style="display:inline-block;width:14px;height:14px;background:hsl(0,65%,40%);border-radius:3px;vertical-align:middle;margin-right:6px"></span> 🪤 Black's mistake`);
      }
      legendEl.innerHTML = items.join('<br>');
    }

    // (D-277) Re-tick the view at the DELIVERED size for an intrinsically
    // un-scalable authored canvas (extremely wide/large or tiny). Uniform
    // geometry scaling would otherwise shrink type to 1-4px (w2-07 2400px,
    // w2-10 3600px) or magnify label collisions ~18x (w2-09 70x45). Keys on the
    // AUTHORED spec dims (persist across the repeated postRenderSizing runs);
    // no-op — hence no regression — for normal-sized specs. Re-running the view
    // makes Vega re-tick / re-lay text at real pixel size, aspect preserved.
    const authoredSpecW = typeof vegaSpec.width === 'number' ? vegaSpec.width : 0;
    const authoredSpecH = typeof vegaSpec.height === 'number' ? vegaSpec.height : 0;
    try {
      const containerW0 = container.getBoundingClientRect().width || 0;
      const retick = computeReTickDimensions(authoredSpecW, authoredSpecH, containerW0);
      if (retick && result.view && typeof result.view.width === 'function') {
        result.view.width(retick.width).height(retick.height);
        if (typeof result.view.runAsync === 'function') await result.view.runAsync();
        else if (typeof result.view.run === 'function') result.view.run();
      }
    } catch { /* re-tick is best-effort; never break a render */ }

    // --- Post-render sizing: make SVG responsive and expand parents ---
    const postRenderSizing = () => {
      const svg = renderDiv.querySelector('svg');
      const vegaEmbedEl = renderDiv.querySelector('.vega-embed') as HTMLElement;
      if (!svg) return;

      // Use getBBox() to measure the ACTUAL rendered content including
      // rotated text labels that overflow the declared viewBox.
      let svgW = 0, svgH = 0, bboxX = 0, bboxY = 0;
      try {
        const bbox = (svg as unknown as SVGGraphicsElement).getBBox();
        svgW = bbox.width;
        svgH = bbox.height;
        bboxX = bbox.x;
        bboxY = bbox.y;
      } catch {
        // getBBox can fail if SVG isn't rendered yet; fall back to viewBox
        const viewBox = svg.getAttribute('viewBox');
        if (viewBox) {
          const parts = viewBox.split(/[\s,]+/).map(Number);
          svgW = parts[2] || 0;
          svgH = parts[3] || 0;
        }
      }
      if (!svgH) svgH = parseFloat(svg.getAttribute('height') || '0');
      if (!svgW) svgW = parseFloat(svg.getAttribute('width') || '0');

      // (D-283) Resolve the viewBox. Normally the full getBBox extent (so
      // rotated labels that overflow a LITTLE are not clipped — D-276). But a
      // geographic spec (mercator + world graticule) overflows the authored
      // canvas by many multiples; getBBox would then shrink the intended
      // regional map to a few percent. When the content floods far beyond the
      // authored viewport, honour the authored viewport and CLIP the spill.
      const vb = resolveVegaViewBox(authoredSpecW, authoredSpecH, bboxX, bboxY, svgW, svgH);
      const usedW = vb.w, usedH = vb.h;

      // Set viewBox so ALL content is visible (or, for a flood, the authored
      // viewport), then make the SVG scale responsively within its container.
      svg.setAttribute('viewBox', `${vb.x} ${vb.y} ${vb.w} ${vb.h}`);
      svg.removeAttribute('width');
      svg.removeAttribute('height');
      svg.style.width = '100%';
      svg.style.height = 'auto';
      svg.setAttribute('preserveAspectRatio', 'xMidYMid meet');
      svg.style.display = 'block';
      // (D-276) overflow:visible for the normal path — 'xMidYMid meet' already
      // letterboxes the scenegraph to fit, so edge content must be shown, never
      // sliced. (D-283) but for a world-scale FLOOD we clip to the authored
      // canvas so the spill does not paint over the whole panel.
      svg.style.overflow = vb.clip ? 'hidden' : 'visible';

      // Size the container to the actual content aspect ratio (using real bbox).
      // Height tracks the intrinsic bbox aspect so the reserved box MATCHES what
      // 'meet' scales the SVG to (containerW × aspect) — i.e. the container is
      // fitted to the aspect-preserved SVG, it does not force a mismatched box
      // that would then clip. Guard a degenerate/zero bbox so we never collapse.
      const containerW = container.getBoundingClientRect().width || usedW;
      const aspect = (usedW > 0 && usedH > 0) ? usedH / usedW : 1;
      const neededH = Math.ceil(containerW * aspect) + 40;

      renderDiv.style.height = `${neededH}px`;
      container.style.minHeight = `${neededH}px`;
      if (vegaEmbedEl) {
        vegaEmbedEl.style.width = '100%';
        vegaEmbedEl.style.height = `${neededH}px`;
        // (D-276) visible for the normal path; (D-283) clipped for a flood.
        vegaEmbedEl.style.overflow = vb.clip ? 'hidden' : 'visible';
      }

      // Walk up parent chain and expand any constraining containers
      let parent = container.parentElement;
      let levelsWalked = 0;
      while (parent && levelsWalked < 5) {
        levelsWalked++;
        if (parent.classList.contains('d3-container') || parent.hasAttribute('data-visualization-type')) {
          const parentH = parent.getBoundingClientRect().height;
          if (parentH < neededH + 20) {
            (parent as HTMLElement).style.height = 'auto';
            (parent as HTMLElement).style.minHeight = `${neededH + 20}px`;
            (parent as HTMLElement).style.maxHeight = 'none';
            (parent as HTMLElement).style.overflow = 'visible';
          }
        }
        parent = parent.parentElement;
      }
    };

    // Run sizing immediately and again after a short delay for late-layout cases
    postRenderSizing();
    setTimeout(postRenderSizing, 150);
    setTimeout(() => {
      postRenderSizing();
      // Signal completion after sizing is stable
      container.dispatchEvent(
        new CustomEvent('vega-render-complete', { detail: { success: true } }),
      );
    }, 300);

    // Also observe resize to keep sizing correct on window changes
    const resizeObserver = new ResizeObserver(() => postRenderSizing());
    resizeObserver.observe(container);
    // Store for cleanup by D3Renderer
    (container as any)._vegaResizeObserver = resizeObserver;

    // --- Action buttons (Open / Save / Source) ---
    const actions = document.createElement('div');
    actions.className = 'diagram-actions';
    actions.style.cssText =
      'position:absolute; top:-4px; right:8px; z-index:1000; opacity:0; transition:opacity 0.2s;';
    container.style.position = 'relative';

    const mkBtn = (label: string, cls: string): HTMLButtonElement => {
      const b = document.createElement('button');
      b.innerHTML = label;
      b.className = `diagram-action-button ${cls}`;
      return b;
    };

    // Open in popout
    const openBtn = mkBtn('↗️ Open', 'vega-open-button');
    openBtn.onclick = () => {
      const svg = container.querySelector('svg');
      if (!svg) return;
      const svgData = new XMLSerializer().serializeToString(svg);
      const html = `<!DOCTYPE html><html><head><meta charset="UTF-8"><title>Vega Visualization</title>
<style>body{margin:0;display:flex;flex-direction:column;height:100vh;background:${isDarkMode ? '#212529' : '#f8f9fa'};font-family:system-ui}
.toolbar{background:${isDarkMode ? '#343a40' : '#f1f3f5'};border-bottom:1px solid ${isDarkMode ? '#495057' : '#dee2e6'};padding:8px;display:flex;justify-content:space-between}
.toolbar button{background:#4361ee;color:#fff;border:none;border-radius:4px;padding:6px 12px;cursor:pointer;margin-right:8px}
.container{flex:1;display:flex;justify-content:center;align-items:center;overflow:auto;padding:20px}
svg{max-width:100%;max-height:100%;height:auto;width:auto}</style></head>
<body><div class="toolbar"><div><button onclick="zoomIn()">Zoom In</button><button onclick="zoomOut()">Zoom Out</button><button onclick="resetZoom()">Reset</button></div>
<div><button onclick="downloadSvg()">Download SVG</button></div></div>
<div class="container">${svgData}</div>
<script>${getZoomScript()}
function downloadSvg(){const s=new XMLSerializer().serializeToString(document.querySelector('svg'));const b=new Blob([s],{type:'image/svg+xml'});const u=URL.createObjectURL(b);const a=document.createElement('a');a.href=u;a.download='vega-${Date.now()}.svg';a.click();setTimeout(()=>URL.revokeObjectURL(u),1000)}
</script></body></html>`;
      const blob = new Blob([html], { type: 'text/html' });
      const url = URL.createObjectURL(blob);
      const w = window.open(url, 'VegaVis', 'width=900,height=700,resizable=yes,scrollbars=yes');
      if (w) w.focus();
      setTimeout(() => URL.revokeObjectURL(url), 10000);
    };
    actions.appendChild(openBtn);

    // Save SVG
    const saveBtn = mkBtn('💾 Save', 'vega-save-button');
    saveBtn.onclick = () => {
      const svg = container.querySelector('svg');
      if (!svg) return;
      const data = new XMLSerializer().serializeToString(svg);
      const blob = new Blob([`<?xml version="1.0" encoding="UTF-8"?>\n${data}`], { type: 'image/svg+xml' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `vega-visualization-${Date.now()}.svg`;
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    };
    actions.appendChild(saveBtn);

    // View source
    const srcBtn = mkBtn('📝 Source', 'vega-source-button');
    let showingSrc = false;
    srcBtn.onclick = () => {
      showingSrc = !showingSrc;
      srcBtn.innerHTML = showingSrc ? '🎨 View' : '📝 Source';
      if (showingSrc) {
        renderDiv.style.display = 'none';
        const pre = document.createElement('pre');
        pre.className = 'vega-source-view';
        pre.style.cssText = `background:${isDarkMode ? '#1f1f1f' : '#f6f8fa'};padding:16px;border-radius:4px;overflow:auto;max-height:80vh;margin:0;color:${isDarkMode ? '#e6e6e6' : '#24292e'};font-size:13px;line-height:1.45;`;
        pre.textContent = JSON.stringify(vegaSpec, null, 2);
        container.appendChild(pre);
      } else {
        container.querySelector('.vega-source-view')?.remove();
        renderDiv.style.display = '';
      }
    };
    actions.appendChild(srcBtn);

    container.insertBefore(actions, container.firstChild);
    container.addEventListener('mouseenter', () => (actions.style.opacity = '1'));
    container.addEventListener('mouseleave', () => (actions.style.opacity = '0'));
    } catch (err) {
      // Paint a terminal error-placeholder SVG so the harness detects a
      // completed (failed) render in ~1s instead of hanging to the 30s
      // watchdog. Do NOT re-throw: an error propagated out of render() is
      // exactly what caused the silent 30s timeout-no-output for Issue 15.
      const msg = renderVegaErrorPlaceholder(container, err, isDarkMode);
      // eslint-disable-next-line no-console
      console.error('Vega render error (surfaced as placeholder):', msg);
    }
  },
};
