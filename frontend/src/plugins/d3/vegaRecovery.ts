/**
 * G-21 — Vega-Lite recovery + theme helpers.
 *
 * Pure, side-effect-light utilities extracted so they can be unit-tested
 * independently of the (DOM + vega-embed) render path. Wired into
 * vegaLitePlugin.ts. Covers:
 *
 *   D-252  no tolerant/normalising parse before JSON.parse (fence + prose
 *          lead-in, smart quotes, trailing commas, unquoted keys, single
 *          quotes, trailing ';') and a valid-but-mis-nested mark.encoding.
 *   D-255  an unknown scale.scheme name crashes the whole render to a blank
 *          canvas — validate against the known-scheme registry and drop.
 *   D-256  a bare-array `data: [...]` is never normalised to
 *          `data: {values: [...]}`, yielding a silent empty chart.
 *   D-257  a colour resolved WITHOUT consulting the active theme lands on the
 *          dark background (invisible text marks / explicit near-black guide
 *          colours). Resolve defaults from the theme and reconcile explicit
 *          guide colours that are invisible on the effective canvas.
 */

import JSON5 from 'json5';

// ─────────────────────────────────────────────────────────────────────────
// D-252: tolerant parse
// ─────────────────────────────────────────────────────────────────────────

/** Strip a leading ```lang fence (and trailing ```), returning the body. */
export function stripSpecFences(raw: string): string {
  if (typeof raw !== 'string') return raw;
  const s = raw.trim();
  const fence = s.match(/^```[^\n]*\n([\s\S]*?)\n?```\s*$/);
  return fence ? fence[1].trim() : s;
}

/** Normalise typographic (smart) quotes to their ASCII equivalents. */
export function normalizeSmartQuotes(s: string): string {
  if (typeof s !== 'string') return s;
  return s
    .replace(/[\u201C\u201D\u201E\u201F\u2033\u2036]/g, '"')
    .replace(/[\u2018\u2019\u201A\u201B\u2032\u2035]/g, "'");
}

/**
 * Slice a string down to its outermost {...} object, discarding any prose
 * lead-in before the first '{' and any trailing tail after the last '}'
 * (e.g. a stray ';' or explanatory sentence). No-op if no braces are found.
 */
export function sliceToOutermostObject(s: string): string {
  if (typeof s !== 'string') return s;
  const start = s.indexOf('{');
  const end = s.lastIndexOf('}');
  return start >= 0 && end > start ? s.slice(start, end + 1) : s;
}

/**
 * Parse a candidate Vega-Lite spec string tolerantly. Order of attempts:
 *   1. strict JSON on the fence-stripped, smart-quote-normalised text
 *   2. strict JSON on the outermost {...} slice (drops prose / trailing ';')
 *   3. JSON5 on the slice, then on the whole (trailing commas, unquoted keys,
 *      single quotes, comments)
 * Throws the last error (a SyntaxError) if every attempt fails, so the caller's
 * existing try/catch can surface the styled error panel.
 */
export function tolerantParseVegaSpec(raw: string): any {
  if (typeof raw !== 'string') return raw;
  const normalized = normalizeSmartQuotes(stripSpecFences(raw));
  try {
    return JSON.parse(normalized);
  } catch (_) { /* fall through */ }

  const sliced = sliceToOutermostObject(normalized);
  if (sliced !== normalized) {
    try {
      return JSON.parse(sliced);
    } catch (_) { /* fall through */ }
  }
  try {
    return JSON5.parse(sliced);
  } catch (_) { /* fall through */ }
  // Last attempt on the un-sliced text; let this one throw on failure.
  return JSON5.parse(normalized);
}

/**
 * D-252 (rider): a valid spec that mis-nests `encoding` INSIDE the `mark`
 * object never draws. Hoist it to the sibling `spec.encoding` when the top
 * level has none. Mutates and returns the spec.
 */
export function hoistMarkEncoding(spec: any): any {
  if (
    spec && typeof spec === 'object' &&
    spec.mark && typeof spec.mark === 'object' && !Array.isArray(spec.mark) &&
    spec.mark.encoding && typeof spec.mark.encoding === 'object' &&
    !spec.encoding
  ) {
    spec.encoding = spec.mark.encoding;
    delete spec.mark.encoding;
  }
  return spec;
}

// ─────────────────────────────────────────────────────────────────────────
// D-255: unknown colour-scheme validation
// ─────────────────────────────────────────────────────────────────────────

// Vega scheme registry (categorical, sequential single/multi-hue, diverging,
// cyclical). Compared case-insensitively. An unrecognised NAMED scheme is a
// fatal Vega dataflow error ("Unrecognized scheme name"), so we drop it to the
// default rather than let one token collapse the whole chart to a blank canvas.
export const KNOWN_VEGA_SCHEMES = new Set<string>([
  // categorical
  'accent', 'category10', 'category20', 'category20b', 'category20c',
  'dark2', 'paired', 'pastel1', 'pastel2', 'set1', 'set2', 'set3',
  'tableau10', 'tableau20', 'observable10',
  // sequential single-hue
  'blues', 'greens', 'greys', 'oranges', 'purples', 'reds',
  // sequential multi-hue
  'turbo', 'viridis', 'inferno', 'magma', 'plasma', 'cividis',
  'bluegreen', 'bluepurple', 'greenblue', 'orangered', 'purplebluegreen',
  'purpleblue', 'purplered', 'redpurple', 'yellowgreenblue', 'yellowgreen',
  'yelloworangebrown', 'yelloworangered',
  'browns', 'tealblues', 'teals', 'warmgreys', 'goldgreen', 'goldorange',
  'goldred', 'lightgreyred', 'lightgreyteal', 'lightmulti', 'lightorange',
  'lighttealblue', 'darkblue', 'darkgold', 'darkgreen', 'darkmulti', 'darkred',
  // diverging
  'blueorange', 'brownbluegreen', 'purplegreen', 'pinkyellowgreen',
  'purpleorange', 'redblue', 'redgrey', 'redyellowblue', 'redyellowgreen',
  'spectral',
  // cyclical
  'rainbow', 'sinebow',
]);

/**
 * Walk the spec and drop any `scale.scheme` string that is neither a known
 * scheme name nor a hex value (hex schemes are handled separately by the arc
 * fixer). Returns the number of schemes dropped. Mutates the spec.
 */
export function validateColorSchemes(spec: any): number {
  let dropped = 0;
  const walk = (node: any): void => {
    if (!node || typeof node !== 'object') return;
    if (Array.isArray(node)) {
      node.forEach(walk);
      return;
    }
    if (
      node.scale && typeof node.scale === 'object' &&
      typeof node.scale.scheme === 'string'
    ) {
      const raw = node.scale.scheme.trim();
      const name = raw.toLowerCase();
      if (raw && !raw.startsWith('#') && !KNOWN_VEGA_SCHEMES.has(name)) {
        delete node.scale.scheme;
        dropped += 1;
      }
    }
    for (const k in node) {
      if (Object.prototype.hasOwnProperty.call(node, k)) walk(node[k]);
    }
  };
  walk(spec);
  return dropped;
}

// ─────────────────────────────────────────────────────────────────────────
// D-256: bare-array data normalisation
// ─────────────────────────────────────────────────────────────────────────

/**
 * A Vega-Lite `data` given as a bare array of row objects must be wrapped as
 * `{values: [...]}` or the chart draws axes with no data. Vega v5 NATIVE specs
 * legitimately use a top-level `data` ARRAY of named dataset objects, so those
 * are left untouched. Mutates and returns the spec.
 */
export function normalizeBareArrayData(spec: any): any {
  if (!spec || typeof spec !== 'object') return spec;
  const sch = typeof spec.$schema === 'string' ? spec.$schema : '';
  const isVega5 = (sch.includes('/vega/') && !sch.includes('/vega-lite/')) || Array.isArray(spec.marks);
  if (isVega5) return spec;

  // A Vega v5 dataset array element is a named source ({name, values|url|source|
  // transform}); a Vega-Lite bare-array row is a plain data object. Only wrap
  // when the elements look like data rows, never like dataset definitions.
  const looksLikeRows = (arr: any[]): boolean =>
    arr.length > 0 &&
    arr.every(e => e && typeof e === 'object' && !Array.isArray(e)) &&
    !arr.every(e =>
      typeof e.name === 'string' &&
      ('values' in e || 'source' in e || 'url' in e || 'transform' in e));

  const walk = (node: any): void => {
    if (!node || typeof node !== 'object' || Array.isArray(node)) return;
    if (Array.isArray(node.data) && looksLikeRows(node.data)) {
      node.data = { values: node.data };
    }
    for (const key of ['layer', 'vconcat', 'hconcat', 'concat']) {
      if (Array.isArray(node[key])) node[key].forEach(walk);
    }
    if (node.spec) walk(node.spec);
    if (node.facet && node.spec) walk(node.spec);
  };
  walk(spec);
  return spec;
}

// ─────────────────────────────────────────────────────────────────────────
// D-257: theme colour reconciliation
// ─────────────────────────────────────────────────────────────────────────

// Minimal CSS colour-name table for the forms that actually surface as
// non-adapting guide colours ('black', greys, 'white'). Anything not resolved
// here (or as hex / rgb()) is left untouched — we only ever reconcile a colour
// we can measure.
const CSS_NAME_HEX: Record<string, string> = {
  black: '#000000', white: '#ffffff', gray: '#808080', grey: '#808080',
  dimgray: '#696969', dimgrey: '#696969', darkgray: '#a9a9a9', darkgrey: '#a9a9a9',
  lightgray: '#d3d3d3', lightgrey: '#d3d3d3', silver: '#c0c0c0', gainsboro: '#dcdcdc',
  whitesmoke: '#f5f5f5', snow: '#fffafa', ivory: '#fffff0',
};

/** Resolve a colour string to [r,g,b] (0-255) or null if unresolvable. */
export function resolveColorToRgb(c: string): [number, number, number] | null {
  if (typeof c !== 'string') return null;
  let s = c.trim().toLowerCase();
  if (CSS_NAME_HEX[s]) s = CSS_NAME_HEX[s];
  if (s.startsWith('#')) {
    const h = s.slice(1);
    if (/^[0-9a-f]{3}$/.test(h)) {
      return [parseInt(h[0] + h[0], 16), parseInt(h[1] + h[1], 16), parseInt(h[2] + h[2], 16)];
    }
    if (/^[0-9a-f]{6}$/.test(h)) {
      return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)];
    }
    return null;
  }
  const m = s.match(/^rgba?\(\s*([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)/);
  if (m) return [Math.round(+m[1]), Math.round(+m[2]), Math.round(+m[3])];
  return null;
}

export function relLuminance([r, g, b]: [number, number, number]): number {
  const f = (v: number) => {
    const c = v / 255;
    return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
  };
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
}

/** WCAG contrast ratio between two resolved colours. */
export function contrastRatio(a: [number, number, number], b: [number, number, number]): number {
  const la = relLuminance(a), lb = relLuminance(b);
  const hi = Math.max(la, lb), lo = Math.min(la, lb);
  return (hi + 0.05) / (lo + 0.05);
}

// Guide colour keys that Vega honours verbatim and does NOT theme-adapt when
// set explicitly. A near-black value here is invisible on a dark canvas (and a
// near-white value invisible on a light canvas).
const GUIDE_COLOR_KEYS = new Set([
  'labelColor', 'titleColor', 'tickColor', 'domainColor', 'gridColor',
]);

/**
 * Resolve the effective canvas colour: an explicit, resolvable spec.background,
 * else the theme surface (#333333 for the Vega 'dark' theme card, #ffffff for
 * the light 'excel' theme).
 */
export function resolveEffectiveBg(background: any, isDarkMode: boolean): [number, number, number] {
  if (typeof background === 'string') {
    const rgb = resolveColorToRgb(background);
    if (rgb) return rgb;
  }
  return isDarkMode ? [51, 51, 51] : [255, 255, 255];
}

/**
 * D-257: resolve theme-blind colours from the ACTIVE theme rather than a
 * constant.
 *   (locus 2) text MARKS that declare no colour render near-black; set a
 *             themed `config.text.fill` default (author/encoding colour still
 *             overrides it).
 *   (locus 3) an explicit guide colour (labelColor/titleColor/…) that is
 *             invisible on the effective canvas (< 3:1) is nudged to the themed
 *             readable value. Only colours we can actually measure are touched,
 *             so intentional, legible author colours are preserved on BOTH
 *             themes.
 * Mutates and returns the spec.
 */
export function reconcileThemeColors(spec: any, isDarkMode: boolean): any {
  if (!spec || typeof spec !== 'object') return spec;

  // D-258: an authored background of the WRONG polarity for the active theme
  // (a light card under the dark theme, or a dark card under the light theme)
  // renders as a glaring/misfit slab AND collapses the theme's guide-title
  // colour onto it (e.g. dark theme sets titleColor:#ffffff, which then lands
  // white-on-#fff = 1.00:1). Drop it so the theme's own surface applies; the
  // guide-colour walk below then measures against the real canvas.
  reconcileBackground(spec, isDarkMode);

  const bg = resolveEffectiveBg(spec.background, isDarkMode);
  const darkCanvas = relLuminance(bg) < 0.5;
  const readable = darkCanvas ? '#e8e8e8' : '#333333';
  const readableRgb = resolveColorToRgb(readable)!;

  // locus 2 — default text-mark fill from the theme (only when the author has
  // not pinned one). Does not override an explicit mark/encoding colour.
  spec.config = spec.config && typeof spec.config === 'object' ? spec.config : {};
  const textCfg = spec.config.text && typeof spec.config.text === 'object' ? spec.config.text : {};
  if (textCfg.fill === undefined && textCfg.color === undefined) {
    spec.config.text = { ...textCfg, fill: readable };
  }

  // locus 3 — reconcile explicit, measurable, invisible guide colours.
  const walk = (node: any): void => {
    if (!node || typeof node !== 'object') return;
    if (Array.isArray(node)) {
      node.forEach(walk);
      return;
    }
    for (const k in node) {
      if (!Object.prototype.hasOwnProperty.call(node, k)) continue;
      const v = node[k];
      if (typeof v === 'string' && GUIDE_COLOR_KEYS.has(k)) {
        const rgb = resolveColorToRgb(v);
        if (rgb && contrastRatio(rgb, bg) < 3) {
          node[k] = readable;
        }
      } else if (v && typeof v === 'object') {
        walk(v);
      }
    }
  };
  walk(spec);

  // D-259: the boxplot composite mark's whisker/cap RULES keep a near-black
  // stroke that Vega's dark theme does not adapt (#000 on the #333 dark card =
  // 1.66:1). Theme those sub-mark strokes from the active canvas when the
  // author has not pinned them; the coloured box body is untouched.
  themeBoxplotStrokes(spec, readable, darkCanvas);

  // Guard against a no-op unused warning on readableRgb in strict builds.
  void readableRgb;
  return spec;
}

/**
 * D-258: drop an authored `background` whose polarity is opposite to the active
 * theme so the theme's own surface shows through. Only fires when the colour is
 * resolvable AND clearly the wrong side (a light card under dark theme, or a
 * dark card under light theme); a background close to the theme surface, an
 * unresolvable value, or `transparent`/`null` is left untouched. Mutates spec.
 */
export function reconcileBackground(spec: any, isDarkMode: boolean): any {
  if (!spec || typeof spec !== 'object') return spec;
  const rgb = typeof spec.background === 'string' ? resolveColorToRgb(spec.background) : null;
  if (!rgb) return spec;
  const lum = relLuminance(rgb);
  const bgIsLight = lum >= 0.5;
  // Wrong polarity: light background while the theme is dark, or dark
  // background while the theme is light.
  if ((isDarkMode && bgIsLight) || (!isDarkMode && !bgIsLight)) {
    delete spec.background;
  }
  return spec;
}

/**
 * D-259: set the boxplot composite sub-mark strokes (whisker/cap `rule`, cap
 * `ticks`, and the `median` line) to a canvas-readable colour on a dark canvas,
 * without overriding an author-supplied stroke. No-op on a light canvas (the
 * default near-black strokes already clear the floor there) and for non-boxplot
 * specs. Mutates spec.
 */
export function themeBoxplotStrokes(spec: any, readable: string, darkCanvas: boolean): any {
  if (!spec || typeof spec !== 'object' || !darkCanvas) return spec;
  const markType = typeof spec.mark === 'string' ? spec.mark : spec.mark?.type;
  if (markType !== 'boxplot') return spec;
  spec.config = spec.config && typeof spec.config === 'object' ? spec.config : {};
  const bp = spec.config.boxplot && typeof spec.config.boxplot === 'object' ? spec.config.boxplot : {};
  const themeSub = (key: string) => {
    const sub = bp[key] && typeof bp[key] === 'object' ? bp[key] : {};
    if (sub.stroke === undefined && sub.color === undefined) {
      bp[key] = { ...sub, stroke: readable };
    }
  };
  themeSub('rule');
  themeSub('ticks');
  themeSub('median');
  spec.config.boxplot = bp;
  return spec;
}

/**
 * D-262: strip `resolve.scale` only where it hangs / mislayouts the renderer,
 * but PRESERVE it for a top-level LAYERED chart — that is the legitimate
 * dual-axis case (bar + line with `resolve.scale.y = "independent"`), where
 * dropping it collapses the second series onto the first axis' domain. The
 * hang the blanket delete guarded against was the faceted/repeated `spec.spec`
 * case, which is still stripped. Mutates spec.
 */
export function sanitizeResolveScale(spec: any): any {
  if (!spec || typeof spec !== 'object') return spec;
  if (spec.resolve && spec.resolve.scale && !Array.isArray(spec.layer)) {
    delete spec.resolve;
  }
  if (spec.spec && spec.spec.resolve && spec.spec.resolve.scale) {
    delete spec.spec.resolve;
  }
  return spec;
}

/**
 * Fix 17 / D-261: enhance an arc/pie/donut chart with per-slice text labels.
 * LLMs put the meaningful label only in the (hover-only) tooltip, leaving the
 * rendered chart as unlabeled wedges. This converts the spec to a layered
 * chart (arc layer + text layer) and flags long descriptive fields for a
 * post-render HTML panel.
 *
 * D-261 radial-placement fix: the text mark is given an ABSOLUTE `radius`
 * (outerRadius + offset) and its theta channel is forced `stack:true`. The
 * previous `radiusOffset`-only form left the labels at the donut centre — with
 * no base `radius` encoding Vega placed every label at radius 0, piling all
 * category names into one glyph stack in the hole. An explicit radius spreads
 * them to each slice's centroid angle just outside the ring.
 *
 * Pure + theme-aware (label colour resolved from the effective canvas). Mutates
 * nothing (returns a new spec or the original when not applicable).
 */
export function enhanceArcChartsWithTextLabels(spec: any, isDarkMode: boolean): any {
  if (!spec || typeof spec !== 'object') return spec;
  const markType = typeof spec.mark === 'string' ? spec.mark : spec.mark?.type;
  if (markType !== 'arc') return spec;
  if (!spec.encoding?.theta) return spec;
  if (spec.layer) return spec; // already layered — don't double-process

  const colorField = spec.encoding?.color?.field;
  if (!colorField || !spec.data?.values || spec.data.values.length === 0) return spec;

  // Detect descriptive text fields (strings longer than 25 chars)
  const firstRow = spec.data.values[0];
  const descriptiveFields = Object.keys(firstRow).filter(key => {
    if (key === colorField) return false;
    const val = firstRow[key];
    return typeof val === 'string' && val.length > 25;
  });

  const outerRadius = spec.mark?.outerRadius || 90;
  // D-261: place labels on a ring just OUTSIDE the arcs. An absolute radius is
  // required — radiusOffset alone (with no base radius) collapses to the centre.
  const labelRadius = outerRadius + Math.max(15, Math.round(outerRadius * 0.18));

  // Build text-label encoding: reuse theta (forced stacked so labels align to
  // each slice's centroid angle) and theta2 if present.
  const textEncoding: any = {
    theta: { ...spec.encoding.theta, stack: true },
    text: { field: colorField, type: 'nominal' },
  };
  if (spec.encoding.theta2) {
    textEncoding.theta2 = { ...spec.encoding.theta2 };
  }

  // Contrasting label colour from the effective canvas (D-257 companion): an
  // explicit light spec.background => dark ink, else follow the active theme.
  const bg = (spec.background || '').toLowerCase();
  const isLightBg = bg ? (bg === '#ffffff' || bg.startsWith('#f')) : !isDarkMode;
  textEncoding.color = { value: isLightBg ? '#333333' : '#eeeeee' };

  // Arc layer keeps the full original encoding
  const arcLayer = { mark: spec.mark, encoding: { ...spec.encoding } };

  // Text layer positioned on the label ring outside the arcs
  const textLayer = {
    mark: {
      type: 'text',
      radius: labelRadius,
      fontSize: 12,
      fontWeight: 'bold',
    },
    encoding: textEncoding,
  };

  const { mark: _mark, encoding: _enc, ...rest } = spec;
  const layeredSpec: any = {
    ...rest,
    layer: [arcLayer, textLayer],
  };

  if (descriptiveFields.length > 0) {
    layeredSpec.__arcDescriptiveFields = descriptiveFields;
    layeredSpec.__arcColorField = colorField;
    layeredSpec.__arcColorScale = spec.encoding?.color?.scale;
  }

  return layeredSpec;
}

// ── G-72 / D-260, D-265: categorical colour-range adequacy ─────────────────
// Two failures share the theme category palette as their root cause:
//   D-265 (both themes, structural): the categorical encoding stops being
//         injective past 10 series because BOTH active palettes are exactly 10
//         long — the light 'excel' theme's `range.category` has 10 entries and
//         the Vega 'dark' theme falls through to the 10-colour 'tableau10'
//         default — so 12/20/30 series alias 2-to-1 / repeat and the legend
//         can no longer identify a line or band.
//   D-260 (light only, theme): the excel `range.category` is a MUTED 10-colour
//         set; at low mark opacity over #fff its already-low chroma collapses
//         toward the same pale point (measured ~1.15:1 luminance / ΔE≈8 between
//         groups at opacity 0.35), destroying the colour channel. The dark
//         theme passes the identical spec because it uses saturated tableau10,
//         whose hues stay separable when composited (ΔE≈12 at 0.35).
// The remedy is theme-resolved (not a constant swap): supply an adequate,
// canvas-appropriate categorical range only when the spec actually needs it,
// and never when the author has pinned their own colours.

/**
 * Vega's default saturated 'tableau10' scheme — exactly what the Vega 'dark'
 * theme already falls through to (and passes with). Used to give the LIGHT
 * 'excel' theme the same hue-separable palette when its muted range would
 * dissolve at low mark opacity (D-260).
 */
export const SATURATED_CATEGORY_10: string[] = [
  '#4c78a8', '#f58518', '#e45756', '#72b7b2', '#54a24b',
  '#eeca3b', '#b279a2', '#ff9da6', '#9d755d', '#bab0ac',
];

/** HSL (h∈[0,360), s,l∈[0,1]) → #rrggbb. */
export function hslToHex(h: number, s: number, l: number): string {
  h = ((h % 360) + 360) % 360;
  s = Math.min(1, Math.max(0, s));
  l = Math.min(1, Math.max(0, l));
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
  const to = (v: number) => {
    const n = Math.round((v + m) * 255);
    return Math.min(255, Math.max(0, n)).toString(16).padStart(2, '0');
  };
  return `#${to(r)}${to(g)}${to(b)}`;
}

/**
 * Generate `n` perceptually-distinct categorical colours by EVEN hue spacing
 * (guaranteed distinct — hue step 360/n) across three lightness/saturation
 * tiers. Lightness is biased toward the active canvas so every entry stays
 * visible on it (a saturated palette cannot clear a WCAG floor on BOTH a white
 * and a dark card at once — the tiers are picked so the min contrast on the
 * ACTIVE background is comparable to the shipped 10-colour scheme). Injective
 * for any n; used when domain cardinality exceeds the 10-entry theme palettes
 * (D-265). Returns exactly `n` colours (min 1).
 */
export function generateCategoricalPalette(n: number, darkCanvas: boolean): string[] {
  const count = Math.max(1, Math.floor(n));
  // Three tiers rotate lightness/saturation so hues that come back near each
  // other after a full wrap still separate by lightness.
  const L = darkCanvas ? [0.62, 0.72, 0.54] : [0.45, 0.34, 0.55];
  const S = darkCanvas ? [0.70, 0.85, 0.62] : [0.72, 0.88, 0.60];
  const out: string[] = [];
  for (let i = 0; i < count; i++) {
    const hue = (i * 360) / count;
    const t = i % 3;
    out.push(hslToHex(hue, S[t], L[t]));
  }
  return out;
}

/**
 * Inspect a spec's colour encoding. Returns whether it is a CATEGORICAL colour
 * channel (nominal/ordinal, or an untyped non-quantitative field), whether the
 * author has already pinned an explicit colour scale (range/scheme), the
 * distinct-value cardinality when knowable (explicit scale.domain length else a
 * distinct count over inline data rows, else 0), and the effective mark
 * opacity. Scans the top-level encoding and, if absent there, the first layer
 * that carries a colour channel.
 */
export function analyzeCategoricalColor(spec: any): {
  isCategorical: boolean;
  hasExplicitColors: boolean;
  cardinality: number;
  opacity: number;
} {
  const none = { isCategorical: false, hasExplicitColors: false, cardinality: 0, opacity: 1 };
  if (!spec || typeof spec !== 'object') return none;

  // Locate the colour channel and the data rows that back it.
  let enc = spec.encoding && typeof spec.encoding === 'object' ? spec.encoding : null;
  let container: any = spec;
  if (!(enc && (enc.color || enc.fill)) && Array.isArray(spec.layer)) {
    for (const layer of spec.layer) {
      if (layer && layer.encoding && (layer.encoding.color || layer.encoding.fill)) {
        enc = layer.encoding;
        container = layer;
        break;
      }
    }
  }
  if (!enc) return none;
  const color = enc.color || enc.fill;
  if (!color || typeof color !== 'object') return none;

  // A quantitative / temporal colour uses a continuous ramp, not a category
  // range — leave it alone.
  const type = color.type;
  if (type === 'quantitative' || type === 'temporal') return none;
  if (color.aggregate && type !== 'nominal' && type !== 'ordinal') {
    // aggregated numeric colour → continuous
    return none;
  }
  const isCategorical =
    type === 'nominal' || type === 'ordinal' ||
    (!type && typeof color.field === 'string');
  if (!isCategorical) return none;

  const scale = color.scale && typeof color.scale === 'object' ? color.scale : null;
  const hasExplicitColors = !!(scale && (Array.isArray(scale.range) || typeof scale.scheme === 'string'));

  // Cardinality: explicit domain length wins; else distinct field values in
  // inline data rows (top-level or the colour channel's own view).
  let cardinality = 0;
  if (scale && Array.isArray(scale.domain)) {
    cardinality = scale.domain.length;
  } else if (typeof color.field === 'string') {
    const rows =
      (container?.data && Array.isArray(container.data.values) && container.data.values) ||
      (spec.data && Array.isArray(spec.data.values) && spec.data.values) ||
      null;
    if (rows) {
      const seen = new Set<any>();
      for (const row of rows) {
        if (row && row[color.field] !== undefined && row[color.field] !== null) {
          seen.add(row[color.field]);
        }
      }
      cardinality = seen.size;
    }
  }

  // Effective mark opacity: explicit mark.opacity, else an opacity encoding
  // value, else 1.
  let opacity = 1;
  const mark = container?.mark ?? spec.mark;
  if (mark && typeof mark === 'object' && typeof mark.opacity === 'number') {
    opacity = mark.opacity;
  } else if (enc.opacity && typeof enc.opacity === 'object' && typeof enc.opacity.value === 'number') {
    opacity = enc.opacity.value;
  }

  return { isCategorical, hasExplicitColors, cardinality, opacity };
}

/** Low-opacity threshold below which the muted excel range dissolves. */
export const CATEGORY_LOW_OPACITY = 0.6;

/**
 * G-72: ensure the categorical colour range is adequate for the spec, writing
 * a themed `config.range.category` ONLY when needed:
 *   • D-265 (both themes): cardinality > 10 → an injective generated range of
 *     exactly `cardinality` distinct colours (biased to the active canvas).
 *   • D-260 (light only): light theme + low mark opacity → the saturated
 *     tableau10 range so hues stay separable when composited over #fff.
 * Never touches an author-pinned colour scale (range/scheme) or an author
 * `config.range.category`, a continuous colour, or a spec with no categorical
 * colour channel. Mutates spec.config and returns the palette applied, else
 * null.
 */
export function applyCategoricalPaletteFix(spec: any, isDarkMode: boolean): string[] | null {
  if (!spec || typeof spec !== 'object') return null;
  // Respect an author-supplied category range.
  if (spec.config?.range?.category) return null;

  const info = analyzeCategoricalColor(spec);
  if (!info.isCategorical || info.hasExplicitColors) return null;

  let palette: string[] | null = null;
  if (info.cardinality > SATURATED_CATEGORY_10.length) {
    // D-265 — palette too short to be injective in EITHER theme.
    palette = generateCategoricalPalette(info.cardinality, isDarkMode);
  } else if (!isDarkMode && info.opacity < CATEGORY_LOW_OPACITY) {
    // D-260 — the muted light range dissolves at low opacity; dark is already
    // fine (saturated tableau10 fallback), so this branch is light-only.
    palette = SATURATED_CATEGORY_10;
  }
  if (!palette) return null;

  spec.config = spec.config && typeof spec.config === 'object' ? spec.config : {};
  spec.config.range = spec.config.range && typeof spec.config.range === 'object' ? spec.config.range : {};
  spec.config.range.category = palette;
  return palette;
}
