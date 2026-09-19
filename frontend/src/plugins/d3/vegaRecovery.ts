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
 * D-016: a model with a JS-authoring reflex separates top-level properties with
 * semicolons instead of commas (`"mark": "bar";`) and/or leaves a trailing `;`
 * after the closing brace (vega-lite-w4-08). A semicolon is INVALID JSON/JSON5
 * in every position, so a single stray `;` defeats even the tolerant JSON5
 * parse and the spec hangs unclaimed. Rewrite every `;` that sits OUTSIDE a
 * string literal to a comma; the outermost-object slice then discards the now
 * dangling trailing comma. Semicolons inside string values (labels, titles) are
 * preserved. Returns the input unchanged when it contains no bare semicolon, so
 * a valid spec is never perturbed. PURE + exported for unit testing.
 */
export function normalizeSemicolonSeparators(s: string): string {
  if (typeof s !== 'string' || s.indexOf(';') < 0) return s;
  let out = '';
  let inString = false;
  let quote = '';
  let escaped = false;
  for (let i = 0; i < s.length; i++) {
    const ch = s[i];
    if (inString) {
      out += ch;
      if (escaped) { escaped = false; continue; }
      if (ch === '\\') { escaped = true; continue; }
      if (ch === quote) inString = false;
      continue;
    }
    if (ch === '"' || ch === "'") { inString = true; quote = ch; out += ch; continue; }
    out += ch === ';' ? ',' : ch;
  }
  return out;
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
 * A generation cut off at its output ceiling typically ends with every
 * character it emitted intact but with the outer brackets never written — e.g.
 * a `vconcat` spec whose final `]}` is missing, which JSON5 reports as an
 * "invalid end of input" at exactly length+1. When the ONLY fault is unwritten
 * closers, the end of the fence implies them: return the text with the
 * outstanding `}`/`]` appended in the correct order.
 *
 * Deliberately conservative — returns null (no guess) when the text is already
 * balanced, when a bracket MISMATCHES its opener (real corruption, not
 * truncation), when it ends inside an unterminated string, or when there is no
 * object to close at all. Brackets inside string literals are not counted.
 */
export function closeUnbalancedBrackets(s: string): string | null {
  if (typeof s !== 'string') return null;
  const start = s.indexOf('{');
  if (start < 0) return null;
  const body = s.slice(start);

  const expected: string[] = [];
  let inString = false;
  let quote = '';
  let escaped = false;
  for (let i = 0; i < body.length; i++) {
    const ch = body[i];
    if (inString) {
      if (escaped) { escaped = false; continue; }
      if (ch === '\\') { escaped = true; continue; }
      if (ch === quote) inString = false;
      continue;
    }
    if (ch === '"' || ch === "'") { inString = true; quote = ch; continue; }
    if (ch === '{') expected.push('}');
    else if (ch === '[') expected.push(']');
    else if (ch === '}' || ch === ']') {
      if (expected.pop() !== ch) return null; // mismatched — do not guess
    }
  }
  // Nothing outstanding, or cut mid-string: not a closer-only truncation.
  if (inString || expected.length === 0) return null;

  // A truncation often lands just after a separator; a dangling comma would
  // defeat even JSON5 and carries no content of its own.
  let repaired = body.replace(/,\s*$/, '');
  for (let i = expected.length - 1; i >= 0; i--) repaired += expected[i];
  return repaired;
}

/**
 * Parse a candidate Vega-Lite spec string tolerantly. Order of attempts:
 *   1. strict JSON on the fence-stripped, smart-quote-normalised text
 *   2. strict JSON on the outermost {...} slice (drops prose / trailing ';')
 *   3. JSON5 on the slice, then on the whole (trailing commas, unquoted keys,
 *      single quotes, comments)
 *   4. the bracket-closed text, when the sole fault is closers the end of the
 *      fence implies (see closeUnbalancedBrackets)
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

  // D-016: semicolons used as statement separators (a JS-authoring reflex) are
  // invalid JSON/JSON5 in every position and defeat all attempts above. Rewrite
  // the bare (out-of-string) semicolons to commas, re-slice to drop any now
  // dangling trailing comma, and retry strict then JSON5. Only runs when a bare
  // ';' is actually present, so a valid spec never reaches this branch.
  const desemied = normalizeSemicolonSeparators(normalized);
  if (desemied !== normalized) {
    const dsliced = sliceToOutermostObject(desemied);
    try {
      return JSON.parse(dsliced);
    } catch (_) { /* fall through */ }
    try {
      return JSON5.parse(dsliced);
    } catch (_) { /* fall through */ }
  }

  // Truncated at the output ceiling: supply the closers the fence implies.
  const closed = closeUnbalancedBrackets(normalized);
  if (closed) {
    try {
      return JSON.parse(closed);
    } catch (_) { /* fall through */ }
    try {
      return JSON5.parse(closed);
    } catch (_) { /* fall through */ }
  }
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
// D-016: unresolvable theme directives + design-token colour strings
// ─────────────────────────────────────────────────────────────────────────

/**
 * The vega-embed theme registry (vega-themes) keys. An `embedOptions.theme`
 * that is NOT one of these is unknown to the runtime; kept in sync with
 * node_modules/vega-themes. Compared case-sensitively (theme keys are lower).
 */
export const KNOWN_VEGA_EMBED_THEMES = new Set<string>([
  'carbong10', 'carbong90', 'carbong100', 'carbonwhite', 'dark', 'excel',
  'fivethirtyeight', 'ggplot2', 'googlecharts', 'latimes', 'powerbi',
  'quartz', 'urbaninstitute', 'vox',
]);

// Keys whose value is a colour. A `$design-token` string here (e.g.
// '$surface', '$textPrimary') is not a colour the runtime can resolve: it lands
// as a literal invalid fill (rendered black/absent), so the theme's own colour
// never applies. Deleting it lets the active theme default fill the slot.
const COLOR_VALUED_KEYS = new Set<string>([
  'background', 'fill', 'color', 'stroke',
  'fillColor', 'strokeColor',
  'labelColor', 'titleColor', 'tickColor', 'domainColor', 'gridColor',
  'labelColour', 'titleColour',
]);

/**
 * D-016: neutralise theme directives and colour strings a model emits that the
 * Vega / vega-embed runtime cannot resolve, so the renderer's OWN theme applies
 * instead of a broken/ignored one (vega-lite-w4-15):
 *   1. a top-level `theme` key — Vega-Lite has no such property; it is inert at
 *      compile but signals a mis-transcribed embedOptions, so drop it.
 *   2. an `usermeta.embedOptions.theme` naming a theme NOT in the registry —
 *      vega-embed merges usermeta.embedOptions OVER the caller's options, so an
 *      unknown name silently overrides (and discards) the renderer's chosen
 *      theme. Drop only the unknown name; a valid author theme is preserved.
 *   3. any colour-position value that is a `$design-token` string (unresolvable
 *      → invalid fill). Delete it so the theme default colour is used.
 * Conservative: only `$`-prefixed colour strings and an UNKNOWN theme are
 * removed; every resolvable colour and valid theme is left untouched. Mutates +
 * returns the spec. PURE + exported for unit testing.
 */
export function sanitizeThemeTokens(spec: any): any {
  if (!spec || typeof spec !== 'object') return spec;

  // (1) top-level `theme` key (not a Vega-Lite property).
  if (typeof spec.theme === 'string') {
    delete spec.theme;
  }

  // (2) unknown usermeta.embedOptions.theme override.
  const embedOpts = spec.usermeta && typeof spec.usermeta === 'object'
    ? spec.usermeta.embedOptions
    : null;
  if (embedOpts && typeof embedOpts === 'object' && typeof embedOpts.theme === 'string') {
    if (!KNOWN_VEGA_EMBED_THEMES.has(embedOpts.theme)) {
      delete embedOpts.theme;
    }
  }

  // (3) `$design-token` colour strings in colour-valued keys.
  const walk = (node: any): void => {
    if (!node || typeof node !== 'object') return;
    if (Array.isArray(node)) { node.forEach(walk); return; }
    for (const k in node) {
      if (!Object.prototype.hasOwnProperty.call(node, k)) continue;
      const v = node[k];
      if (typeof v === 'string' && v.charAt(0) === '$' && COLOR_VALUED_KEYS.has(k)) {
        delete node[k];
      } else if (v && typeof v === 'object') {
        walk(v);
      }
    }
  };
  walk(spec);

  return spec;
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
// D-243: missing encoding-type inference
// ─────────────────────────────────────────────────────────────────────────

// A leading ISO-8601 date/datetime, e.g. "2024", "2024-05", "2024-05-01",
// "2024-05-01T09:30". Deliberately conservative: a plain integer like "700"
// is NOT matched (it has no separator), so a numeric string never mis-infers
// as temporal.
const ISO_DATE_LIKE_RE = /^\d{4}-\d{2}(-\d{2})?([T ]\d{2}:\d{2})?/;

function fieldValuesLookTemporal(vals: any[]): boolean {
  if (!vals.length) return false;
  if (!vals.every(v => typeof v === 'string')) return false;
  return vals.every(v => ISO_DATE_LIKE_RE.test(v) && !Number.isNaN(Date.parse(v)));
}

/**
 * D-243: when an encoding channel names a `field` but omits `type`, Vega-Lite
 * defaults the field to NOMINAL — so a numeric measure (e.g. `pop`) is drawn as
 * discrete bands with equal-height bars, destroying the quantitative comparison
 * the author intended (vega-lite-w4-07). Infer the type from the actual data:
 * an all-numeric field becomes `quantitative`, an all-ISO-date field becomes
 * `temporal`. A non-numeric / non-date field is LEFT UNTOUCHED — Vega-Lite's
 * own nominal default is already correct there, so this is a strict no-op
 * except where the default is demonstrably wrong. Channels that already carry a
 * `type`, or an `aggregate`/`bin`/`timeUnit` (which imply their own type), or
 * whose field is absent from the data, are never modified. Native Vega specs
 * (`marks[]` / a `/vega/` schema) are skipped entirely. Returns the number of
 * types filled in. Mutates the spec. PURE + exported for unit testing.
 */
export function inferEncodingTypes(spec: any): number {
  if (!spec || typeof spec !== 'object') return 0;
  const sch = typeof spec.$schema === 'string' ? spec.$schema : '';
  const isVega5 = (sch.includes('/vega/') && !sch.includes('/vega-lite/')) || Array.isArray(spec.marks);
  if (isVega5) return 0;

  let filled = 0;

  const inferForEncoding = (encoding: any, data: any): void => {
    if (!encoding || typeof encoding !== 'object') return;
    const rows = data && Array.isArray(data.values) ? data.values : [];
    for (const ch in encoding) {
      if (!Object.prototype.hasOwnProperty.call(encoding, ch)) continue;
      const enc = encoding[ch];
      if (!enc || typeof enc !== 'object' || Array.isArray(enc)) continue;
      // Already typed, or carries a transform that dictates its own type.
      if (enc.type || enc.aggregate || enc.bin || enc.timeUnit) continue;
      if (typeof enc.field !== 'string') continue;
      const vals = rows
        .map((r: any) => (r && typeof r === 'object' ? r[enc.field] : undefined))
        .filter((v: any) => v !== undefined && v !== null);
      if (!vals.length) continue; // no evidence — leave Vega's default in place
      if (vals.every((v: any) => typeof v === 'number' && Number.isFinite(v))) {
        enc.type = 'quantitative';
        filled += 1;
      } else if (fieldValuesLookTemporal(vals)) {
        enc.type = 'temporal';
        filled += 1;
      }
      // else: nominal default is already correct → leave untouched.
    }
  };

  const walk = (node: any, inheritedData: any): void => {
    if (!node || typeof node !== 'object' || Array.isArray(node)) return;
    const data = node.data || inheritedData;
    if (node.encoding) inferForEncoding(node.encoding, data);
    for (const key of ['layer', 'vconcat', 'hconcat', 'concat']) {
      if (Array.isArray(node[key])) node[key].forEach((c: any) => walk(c, data));
    }
    if (node.spec) walk(node.spec, data);
  };
  walk(spec, spec.data);
  return filled;
}

// ─────────────────────────────────────────────────────────────────────────
// D-257: theme colour reconciliation
// ─────────────────────────────────────────────────────────────────────────

// Minimal CSS colour-name table for the forms that actually surface as
// non-adapting guide colours ('black', greys, 'white'). Anything not resolved
// here (or as hex / rgb()) is left untouched — we only ever reconcile a colour
// we can measure.
// (D-275) The named-colour table was previously grayscale-only, so a chromatic
// CSS colour name (cornflowerblue, darkseagreen, lightgoldenrodyellow, …)
// resolved to null and ESCAPED every contrast reconciler: a pale named fill
// (lightgoldenrodyellow #fafad2 = 1.07:1 on white) or a near-invisible named
// guide colour could not be MEASURED, so it was left verbatim and vanished into
// the canvas (vega-w4-13 light). The full CSS Level-4 extended colour keyword
// set below lets resolveColorToRgb measure ANY named colour, so the existing
// guide / fill / range reconcilers can act on them in both themes.
const CSS_NAME_HEX: Record<string, string> = {
  aliceblue: '#f0f8ff', antiquewhite: '#faebd7', aqua: '#00ffff', aquamarine: '#7fffd4',
  azure: '#f0ffff', beige: '#f5f5dc', bisque: '#ffe4c4', black: '#000000',
  blanchedalmond: '#ffebcd', blue: '#0000ff', blueviolet: '#8a2be2', brown: '#a52a2a',
  burlywood: '#deb887', cadetblue: '#5f9ea0', chartreuse: '#7fff00', chocolate: '#d2691e',
  coral: '#ff7f50', cornflowerblue: '#6495ed', cornsilk: '#fff8dc', crimson: '#dc143c',
  cyan: '#00ffff', darkblue: '#00008b', darkcyan: '#008b8b', darkgoldenrod: '#b8860b',
  darkgray: '#a9a9a9', darkgrey: '#a9a9a9', darkgreen: '#006400', darkkhaki: '#bdb76b',
  darkmagenta: '#8b008b', darkolivegreen: '#556b2f', darkorange: '#ff8c00', darkorchid: '#9932cc',
  darkred: '#8b0000', darksalmon: '#e9967a', darkseagreen: '#8fbc8f', darkslateblue: '#483d8b',
  darkslategray: '#2f4f4f', darkslategrey: '#2f4f4f', darkturquoise: '#00ced1', darkviolet: '#9400d3',
  deeppink: '#ff1493', deepskyblue: '#00bfff', dimgray: '#696969', dimgrey: '#696969',
  dodgerblue: '#1e90ff', firebrick: '#b22222', floralwhite: '#fffaf0', forestgreen: '#228b22',
  fuchsia: '#ff00ff', gainsboro: '#dcdcdc', ghostwhite: '#f8f8ff', gold: '#ffd700',
  goldenrod: '#daa520', gray: '#808080', grey: '#808080', green: '#008000',
  greenyellow: '#adff2f', honeydew: '#f0fff0', hotpink: '#ff69b4', indianred: '#cd5c5c',
  indigo: '#4b0082', ivory: '#fffff0', khaki: '#f0e68c', lavender: '#e6e6fa',
  lavenderblush: '#fff0f5', lawngreen: '#7cfc00', lemonchiffon: '#fffacd', lightblue: '#add8e6',
  lightcoral: '#f08080', lightcyan: '#e0ffff', lightgoldenrodyellow: '#fafad2', lightgray: '#d3d3d3',
  lightgrey: '#d3d3d3', lightgreen: '#90ee90', lightpink: '#ffb6c1', lightsalmon: '#ffa07a',
  lightseagreen: '#20b2aa', lightskyblue: '#87cefa', lightslategray: '#778899', lightslategrey: '#778899',
  lightsteelblue: '#b0c4de', lightyellow: '#ffffe0', lime: '#00ff00', limegreen: '#32cd32',
  linen: '#faf0e6', magenta: '#ff00ff', maroon: '#800000', mediumaquamarine: '#66cdaa',
  mediumblue: '#0000cd', mediumorchid: '#ba55d3', mediumpurple: '#9370db', mediumseagreen: '#3cb371',
  mediumslateblue: '#7b68ee', mediumspringgreen: '#00fa9a', mediumturquoise: '#48d1cc',
  mediumvioletred: '#c71585', midnightblue: '#191970', mintcream: '#f5fffa', mistyrose: '#ffe4e1',
  moccasin: '#ffe4b5', navajowhite: '#ffdead', navy: '#000080', oldlace: '#fdf5e6',
  olive: '#808000', olivedrab: '#6b8e23', orange: '#ffa500', orangered: '#ff4500',
  orchid: '#da70d6', palegoldenrod: '#eee8aa', palegreen: '#98fb98', paleturquoise: '#afeeee',
  palevioletred: '#db7093', papayawhip: '#ffefd5', peachpuff: '#ffdab9', peru: '#cd853f',
  pink: '#ffc0cb', plum: '#dda0dd', powderblue: '#b0e0e6', purple: '#800080',
  rebeccapurple: '#663399', red: '#ff0000', rosybrown: '#bc8f8f', royalblue: '#4169e1',
  saddlebrown: '#8b4513', salmon: '#fa8072', sandybrown: '#f4a460', seagreen: '#2e8b57',
  seashell: '#fff5ee', sienna: '#a0522d', silver: '#c0c0c0', skyblue: '#87ceeb',
  slateblue: '#6a5acd', slategray: '#708090', slategrey: '#708090', snow: '#fffafa',
  springgreen: '#00ff7f', steelblue: '#4682b4', tan: '#d2b48c', teal: '#008080',
  thistle: '#d8bfd8', tomato: '#ff6347', turquoise: '#40e0d0', violet: '#ee82ee',
  wheat: '#f5deb3', white: '#ffffff', whitesmoke: '#f5f5f5', yellow: '#ffff00',
  yellowgreen: '#9acd32',
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
        if (rgb) {
          if (contrastRatio(rgb, bg) < 3) node[k] = readable;
        } else if (/^var\(|^--/.test(v.trim())) {
          // D-515: an unresolvable CSS custom-property colour token
          // (labelColor:"var(--ziya-text)" on vega-w4-14) reaches the runtime
          // verbatim and paints NO guide colour at all, so the axis labels are
          // absent in both themes. `currentColor` DOES resolve via SVG
          // inheritance, so it is deliberately left untouched; only a var()/
          // custom-property token is resolved to the themed readable value.
          node[k] = readable;
        }
      } else if (v && typeof v === 'object') {
        walk(v);
      }
    }
  };
  walk(spec);

  // D-317: an AUTHORED text-MARK colour (mark.color / mark.fill on a text mark,
  // or a text layer's encoding.color.value) is honoured verbatim by Vega and is
  // NOT in GUIDE_COLOR_KEYS, so a value legible in one theme (e.g. #333333 ink
  // authored for a white card) becomes invisible on the opposite theme's canvas
  // (#333333 on the #333 dark card = 1.00:1). Measure text-mark ink against the
  // effective canvas and nudge to the readable value only when it is sub-3:1.
  reconcileTextMarkColors(spec, bg, readable);

  // D-319: reconcileThemeColors never touched MARK FILLS. A pale authored fill
  // near the canvas luminance vanishes (bar fill #f4f4f4 on white), an
  // all-pastel scale.range dissolves on white, and a gradient stop sitting at
  // canvas luminance disappears. Nudge measurably-invisible (<3:1) solid fills
  // and gradient stops toward the readable side (hue-preserving), and replace a
  // fully-invisible categorical range with the theme's saturated palette.
  reconcileMarkFillsVsCanvas(spec, bg, darkCanvas);

  // D-275: reconcileMarkFillsVsCanvas only understands the Vega-LITE shape
  // (node.mark / node.encoding.color.scale.range). On the NATIVE Vega path the
  // categorical palette lives in a top-level scales[].range array and mark fills
  // in marks[].encode.<phase>.fill.value, so an all-pale native palette
  // (vega-w4-13: lightgoldenrodyellow 1.07:1 / gainsboro 1.37:1 on white) was
  // never reconciled. Nudge measurably-invisible native fills toward the
  // readable side, hue-preserving, in both themes; a legible fill is untouched.
  reconcileNativeVegaFills(spec, bg, darkCanvas);

  // D-318: in a layered arc/pie spec the TOP-LEVEL encoding.color channel is
  // shared to every layer, so a text-label layer that declares no colour of its
  // own inherits the SERIES colour and is drawn in the very slice colour it sits
  // on (invisible), or in the series colour on the canvas (low-contrast in one
  // theme). Break that inheritance: pin an explicit canvas-readable ink on
  // inheriting text layers so labels are never painted with their slice's fill.
  reconcileInheritedArcLabelColors(spec, darkCanvas);

  // D-503: a NON-text mark whose fill is a per-datum literal colour arriving
  // through a `color.field` with `scale:null` is drawn verbatim by Vega, so a
  // pale literal fill (#f7f7f2 = 1.07:1 on white) or a dark one (#1b2a41 =
  // 1.20:1 on the #333 card) vanishes into the canvas. reconcileMarkFillsVsCanvas
  // only inspects a solid `mark.fill` / `scale.range`, never these per-row
  // literal values, so those data marks were never contrast-checked. Nudge each
  // sub-3:1 literal fill toward the readable side of the canvas, hue-preserving.
  // Runs BEFORE the text-on-fill pass below so labels are reconciled against the
  // fill actually rendered.
  reconcileFieldDrivenFillsVsCanvas(spec, bg, darkCanvas);

  // D-318 (field-driven sub-case): value labels drawn INSIDE bars whose ink AND
  // fill are both data-driven literal colours (color.field, scale:null) can be
  // near-isoluminant with the very bar they sit on. That contrast is against the
  // MARK, not the canvas, so none of the reconcilers above see it. Reconcile
  // each row's text colour against its own fill colour (theme-independent).
  reconcileFieldDrivenTextOnFill(spec);

  // D-259: the boxplot composite mark's whisker/cap RULES keep a near-black
  // stroke that Vega's dark theme does not adapt (#000 on the #333 dark card =
  // 1.66:1). Theme those sub-mark strokes from the active canvas when the
  // author has not pinned them; the coloured box body is untouched.
  themeBoxplotStrokes(spec, readable, darkCanvas);

  // Guard against a no-op unused warning on readableRgb in strict builds.
  void readableRgb;
  return spec;
}

/** true when a colour string resolves and is invisible (< 3:1) on `bg`. */
function isInvisibleOn(color: unknown, bg: [number, number, number]): boolean {
  if (typeof color !== 'string') return false;
  const rgb = resolveColorToRgb(color);
  return !!rgb && contrastRatio(rgb, bg) < 3;
}

/**
 * D-518: true when a resolvable colour, COMPOSITED over `bg` at `alpha`
 * (a mark's fillOpacity/strokeOpacity), is invisible (< 3:1) on that canvas.
 * alpha == 1 reduces to {@link isInvisibleOn}.
 */
export function isInvisibleComposite(color: unknown, bg: [number, number, number], alpha = 1): boolean {
  if (typeof color !== 'string') return false;
  const rgb = resolveColorToRgb(color);
  if (!rgb) return false;
  const eff = alpha >= 1 ? rgb : compositeOver(rgb, bg, alpha);
  return contrastRatio(eff, bg) < 3;
}

/** Euclidean distance between two RGB triples (0-255). */
function rgbDistance(a: [number, number, number], b: [number, number, number]): number {
  return Math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2);
}

/**
 * D-504 (custom-range-series-indistinguishable): true when EVERY entry of a
 * categorical range resolves AND no two entries are mutually distinguishable —
 * a pair counts as distinguishable when it differs by >= 45 in RGB Euclidean
 * distance OR clears a 1.5:1 luminance contrast. An isoluminant near-mono
 * cluster (w3-06's 5 creams: max pairwise 36.6 RGB / 1.18:1) survives the
 * canvas-visibility swap in the theme where it happens to be visible (all 5 are
 * 10-12:1 on the #333 dark card), yet the grouped series and legend swatches
 * collapse onto one another. A real categorical palette (tableau10: > 200 RGB
 * apart) always has a distinguishable pair, so this never fires on a usable
 * palette. Requires >= 2 entries. PURE + exported for unit testing.
 */
export function isIndistinguishableRange(range: any[]): boolean {
  if (!Array.isArray(range) || range.length < 2) return false;
  const rgbs = range.map(c => (typeof c === 'string' ? resolveColorToRgb(c) : null));
  if (rgbs.some(r => !r)) return false; // an unresolvable entry — do not judge
  for (let i = 0; i < rgbs.length; i++) {
    for (let j = i + 1; j < rgbs.length; j++) {
      if (rgbDistance(rgbs[i]!, rgbs[j]!) >= 45 || contrastRatio(rgbs[i]!, rgbs[j]!) >= 1.5) {
        return false;
      }
    }
  }
  return true;
}

/** Serialise [r,g,b] (0-255) to #rrggbb. */
function rgbToHex([r, g, b]: [number, number, number]): string {
  const h = (v: number) => Math.max(0, Math.min(255, Math.round(v))).toString(16).padStart(2, '0');
  return `#${h(r)}${h(g)}${h(b)}`;
}

/** Alpha-composite a foreground colour over a background (both 0-255 triples). */
export function compositeOver(
  fg: [number, number, number],
  bg: [number, number, number],
  alpha: number,
): [number, number, number] {
  const a = Math.max(0, Math.min(1, alpha));
  return [
    fg[0] * a + bg[0] * (1 - a),
    fg[1] * a + bg[1] * (1 - a),
    fg[2] * a + bg[2] * (1 - a),
  ];
}

/**
 * D-319 / D-518: nudge an invisible fill toward the readable side of the canvas
 * while preserving its hue direction — blend toward WHITE on a dark canvas,
 * toward BLACK on a light one, in 10% steps until the WCAG floor (3:1) is
 * cleared or the endpoint is reached. Keeps the mark visible without collapsing
 * it onto a single ink constant (a pale blue stays bluish, just darker/lighter).
 *
 * D-518: an `alpha` < 1 (a mark's fillOpacity/strokeOpacity) is composited over
 * the canvas before measuring, so a fill that is opaque-legible but dissolves
 * once its low opacity blends it into the canvas (w3-07's 0.55-opacity points)
 * is still nudged until the COMPOSITED colour clears the floor. alpha == 1 is
 * byte-for-byte the original behaviour.
 */
export function nudgeFillForContrast(hex: string, bg: [number, number, number], darkCanvas: boolean, alpha = 1): string {
  const base = resolveColorToRgb(hex);
  if (!base) return hex;
  const target = darkCanvas ? 255 : 0;
  const eff = (c: [number, number, number]): [number, number, number] =>
    alpha >= 1 ? c : compositeOver(c, bg, alpha);
  let cur: [number, number, number] = [base[0], base[1], base[2]];
  for (let f = 0.1; f <= 1.0001 && contrastRatio(eff(cur), bg) < 3; f += 0.1) {
    cur = [
      base[0] + (target - base[0]) * f,
      base[1] + (target - base[1]) * f,
      base[2] + (target - base[2]) * f,
    ];
  }
  return rgbToHex(cur);
}

/**
 * D-317: reconcile AUTHORED text-mark ink against the effective canvas. Only a
 * measurably invisible (< 3:1) colour is touched, so a deliberately legible
 * author ink is preserved on BOTH themes (a #333 label stays #333 on a white
 * card and only becomes readable light ink once the canvas is dark). Walks the
 * spec tree (layer/concat/facet) and covers mark.color, mark.fill and a text
 * layer's encoding.color.value. Mutates spec.
 */
export function reconcileTextMarkColors(spec: any, bg: [number, number, number], readable: string): any {
  const visit = (node: any): void => {
    if (!node || typeof node !== 'object') return;
    if (Array.isArray(node)) { node.forEach(visit); return; }
    const markType = typeof node.mark === 'string' ? node.mark : node.mark?.type;
    if (markType === 'text' && node.mark && typeof node.mark === 'object') {
      for (const key of ['color', 'fill']) {
        if (isInvisibleOn(node.mark[key], bg)) node.mark[key] = readable;
      }
    }
    if (markType === 'text' && node.encoding?.color && typeof node.encoding.color === 'object') {
      if (isInvisibleOn(node.encoding.color.value, bg)) node.encoding.color.value = readable;
    }
    for (const k of ['layer', 'vconcat', 'hconcat', 'concat']) {
      if (Array.isArray(node[k])) node[k].forEach(visit);
    }
    if (node.spec) visit(node.spec);
  };
  visit(spec);
  return spec;
}

/**
 * D-517 (authored-text-mark-fill-not-theme-reconciled): reconcile an AUTHORED
 * constant text ink on the NATIVE Vega path against the effective theme canvas.
 *
 * {@link reconcileTextMarkColors} only walks the Vega-LITE shape (node.mark /
 * node.encoding.color); {@link reconcileNativeVegaFills} deliberately SKIPS text
 * marks. So a native text mark's `encode.<phase>.fill.value` reached the runtime
 * verbatim, and an author ink chosen for one polarity vanished on the other
 * theme's surface: #333333 value labels (w3-09) survive their authored #fafafa
 * card but hit 1.00:1 once the wrong-polarity card is dropped under the dark
 * theme; #eeeeee labels (w3-10) hit 1.16:1 on white; a #b03a2e annotation
 * (w3-03) is 2.10:1 on the #333 dark card; an rgba(20,20,20,0.9) caption (w4-12)
 * is 1.42:1 dark; a #000000 @0.45 overlay annotation (w3-11) composites to
 * 1.35:1 dark / 3.35:1 light. None reach the 4.5 WCAG text floor.
 *
 * For each native text mark, resolve the constant `fill.value` (hex / rgb /
 * rgba / keyword), composite it over the effective canvas at its own
 * fillOpacity (folding in any rgba() alpha), and — when the composite is below
 * the 4.5 text floor — repaint it with the themed readable ink at full opacity
 * (#e8e8e8 on a dark canvas = 10.31:1, #333333 on a light canvas = 12.63:1, so
 * the replacement itself always clears the floor). A `fill.signal` (a
 * backdrop-relative label already rewritten by reconcileVegaTextLabelContrast)
 * and a fill that already clears the floor on THIS canvas are left byte-for-byte
 * unchanged, so a legible chart on either theme is never recoloured — a #b03a2e
 * label (6.02:1 on white) is kept in light mode and only lifted in dark.
 * Mutates spec; PURE and exported for unit testing. Returns the rewrite count.
 */
export function reconcileNativeVegaTextInk(spec: any, isDarkMode: boolean): number {
  if (!spec || typeof spec !== 'object' || !Array.isArray(spec.marks)) return 0;
  const TEXT_FLOOR = 4.5;
  const bgSource =
    (typeof spec.background === 'string' && spec.background) ||
    (spec.config && typeof spec.config === 'object' && typeof spec.config.background === 'string' &&
      spec.config.background) ||
    undefined;
  const bg = resolveEffectiveBg(bgSource, isDarkMode);
  const readable = relLuminance(bg) < 0.5 ? '#e8e8e8' : '#333333';
  const num = (x: any): number | undefined => (typeof x === 'number' ? x : undefined);
  // Alpha carried INSIDE an rgba()/rgb() literal (the 4th channel), folded into
  // the mark's fillOpacity so the composited colour we measure is what renders.
  const cssAlpha = (s: string): number => {
    const m = s.trim().toLowerCase()
      .match(/^rgba?\(\s*[\d.]+%?[,\s]+[\d.]+%?[,\s]+[\d.]+%?[,\s/]+([\d.]+)%?\s*\)$/);
    if (m) {
      let a = parseFloat(m[1]);
      if (s.includes('%') && /%\s*\)$/.test(s.trim())) a = a / 100;
      return Number.isFinite(a) ? Math.max(0, Math.min(1, a)) : 1;
    }
    return 1;
  };
  let rewritten = 0;
  const walk = (marks: any): void => {
    if (!Array.isArray(marks)) return;
    for (const m of marks) {
      if (!m || typeof m !== 'object') continue;
      if (m.type === 'text' && m.encode && typeof m.encode === 'object' && !Array.isArray(m.encode)) {
        for (const phase of Object.values(m.encode)) {
          if (!phase || typeof phase !== 'object') continue;
          const p = phase as any;
          const def = p.fill;
          if (!def || typeof def !== 'object' || Array.isArray(def)) continue;
          // Only a constant literal ink. A `signal` (backdrop-relative label) or
          // a gradient object is left for its own reconciler / the runtime.
          if (typeof def.value !== 'string') continue;
          const rgb = resolveColorToRgb(def.value);
          if (!rgb) continue;
          const markOpacity = num(p.opacity?.value);
          const alpha = (num(p.fillOpacity?.value) ?? markOpacity ?? 1) * cssAlpha(def.value);
          const eff = alpha >= 1 ? rgb : compositeOver(rgb, bg, alpha);
          if (contrastRatio(eff, bg) < TEXT_FLOOR) {
            def.value = readable;
            // Text must be opaque to read: clear the dimming opacity that made
            // the ink vanish (a #000 @0.45 overlay annotation, w3-11).
            if (p.fillOpacity && typeof p.fillOpacity === 'object') p.fillOpacity.value = 1;
            rewritten += 1;
          }
        }
      }
      if (Array.isArray(m.marks)) walk(m.marks);
    }
  };
  walk(spec.marks);
  return rewritten;
}

/**
 * D-319: reconcile MARK FILLS that vanish into the canvas. For every non-text
 * mark:
 *   - a solid string fill/color invisible (< 3:1) on the canvas is nudged
 *     hue-preserving to the readable side;
 *   - gradient stops that sit at canvas luminance are nudged the same way;
 *   - a categorical encoding.color.scale.range whose entries are ALL invisible
 *     is swapped for the theme's saturated palette prefix (a genuinely
 *     unusable palette on this canvas, not a legible author choice).
 * Legible fills, and a range with even one visible entry, are untouched — so a
 * correct chart on either theme is never recoloured. Mutates spec.
 */
export function reconcileMarkFillsVsCanvas(spec: any, bg: [number, number, number], darkCanvas: boolean): any {
  const nudge = (hex: string) => nudgeFillForContrast(hex, bg, darkCanvas);
  const visit = (node: any): void => {
    if (!node || typeof node !== 'object') return;
    if (Array.isArray(node)) { node.forEach(visit); return; }
    const markType = typeof node.mark === 'string' ? node.mark : node.mark?.type;
    if (markType && markType !== 'text' && node.mark && typeof node.mark === 'object') {
      for (const key of ['fill', 'color']) {
        const c = node.mark[key];
        if (typeof c === 'string') {
          if (isInvisibleOn(c, bg)) node.mark[key] = nudge(c);
        } else if (c && typeof c === 'object' && Array.isArray(c.stops)) {
          for (const st of c.stops) {
            if (st && isInvisibleOn(st.color, bg)) st.color = nudge(st.color);
          }
        }
      }
    }
    const range = node.encoding?.color?.scale?.range;
    if (Array.isArray(range) && range.length > 0) {
      // A categorical range is unusable either because every entry is invisible
      // on THIS canvas (D-319), or because the entries — though visible — are
      // mutually indistinguishable, an isoluminant near-mono cluster whose
      // grouped series/legend swatches collapse together in the theme where the
      // range happens to be visible (D-504, w3-06 dark). Either way, swap for
      // the theme's saturated hue-separable palette. A palette with even one
      // visible-and-distinguishable pair is left verbatim.
      if (range.every((x: any) => isInvisibleOn(x, bg)) || isIndistinguishableRange(range)) {
        node.encoding.color.scale.range = SATURATED_CATEGORY_10.slice(0, Math.min(range.length, SATURATED_CATEGORY_10.length));
      }
    }
    for (const k of ['layer', 'vconcat', 'hconcat', 'concat']) {
      if (Array.isArray(node[k])) node[k].forEach(visit);
    }
    if (node.spec) visit(node.spec);
  };
  visit(spec);
  return spec;
}

/**
 * D-275: native-Vega analogue of {@link reconcileMarkFillsVsCanvas}. The
 * Vega-Lite reconciler only walks `mark`/`encoding`/`layer`; a native Vega spec
 * carries its categorical palette in a top-level `scales[].range` array and its
 * mark fills in `marks[].encode.<phase>.fill.value`, neither of which that
 * reconciler sees. Nudge each measurably-invisible (< 3:1 on the canvas) colour
 * toward the readable side, hue-preserving, in BOTH themes:
 *   - every string entry of a `scales[].range` array (categorical palette);
 *   - a constant `fill.value` / `stroke.value` on a non-text mark's encode set.
 * A colour that already clears the contrast floor is left byte-for-byte
 * unchanged, so a legible spec on either theme is never recoloured. Mutates and
 * returns spec.
 */
export function reconcileNativeVegaFills(spec: any, bg: [number, number, number], darkCanvas: boolean): any {
  if (!spec || typeof spec !== 'object') return spec;
  const nudge = (c: string): string => (isInvisibleOn(c, bg) ? nudgeFillForContrast(c, bg, darkCanvas) : c);

  // (1) categorical scale.range palettes.
  if (Array.isArray(spec.scales)) {
    for (const sc of spec.scales) {
      if (sc && typeof sc === 'object' && Array.isArray(sc.range)) {
        sc.range = sc.range.map((v: any) => (typeof v === 'string' ? nudge(v) : v));
      }
    }
  }

  // (2) constant fill/stroke on native mark encode sets (never a text mark —
  // those are handled by reconcileTextMarkColors / config.text default).
  //
  // D-518: the original pass reconciled only a constant string `fill.value`. A
  // native mark also collapses into the canvas through (a) a constant
  // `stroke.value` (w3-12 #cccccc = 1.61:1 on white), (b) a GRADIENT fill/stroke
  // object whose stops sit at canvas luminance (w3-12 linear gradient fading to
  // #fff), and (c) a partial `fillOpacity`/`strokeOpacity` that blends an
  // opaque-legible colour into the canvas (w3-07's 0.55-opacity #34495e points).
  // Reconcile all three: nudge measurably-invisible (composited) fills AND
  // strokes, including gradient stops, hue-preserving in both themes; a colour
  // that already clears the floor at its effective opacity is untouched.
  const walkMarks = (marks: any): void => {
    if (!Array.isArray(marks)) return;
    for (const m of marks) {
      if (!m || typeof m !== 'object') continue;
      if (m.type !== 'text' && m.encode && typeof m.encode === 'object') {
        for (const phase of Object.values(m.encode)) {
          if (!phase || typeof phase !== 'object') continue;
          const p = phase as any;
          const num = (x: any): number | undefined => (typeof x === 'number' ? x : undefined);
          const markOpacity = num(p.opacity?.value);
          const fillAlpha = num(p.fillOpacity?.value) ?? markOpacity ?? 1;
          const strokeAlpha = num(p.strokeOpacity?.value) ?? markOpacity ?? 1;
          for (const [key, alpha] of [['fill', fillAlpha], ['stroke', strokeAlpha]] as Array<[string, number]>) {
            const def = p[key];
            if (!def || typeof def !== 'object') continue;
            if (typeof def.value === 'string') {
              if (isInvisibleComposite(def.value, bg, alpha)) {
                def.value = nudgeFillForContrast(def.value, bg, darkCanvas, alpha);
                // D-519 (low-opacity-hairline-stroke-dissolves): a hue-preserving
                // colour nudge cannot rescue a STROKE whose opacity is so low
                // that even a fully readable ink composites below the floor
                // (w2-14's #888888 @0.25 reference rule tops out at 1.83:1 on
                // white / 2.20:1 on the #333 card once blackened/whitened). When
                // the nudged stroke STILL dissolves at its authored opacity,
                // raise `strokeOpacity` to the least value at which the nudged
                // colour clears 3:1 — a visible hairline instead of an absent
                // one. Stroke-only: fill alpha is left untouched so an intended
                // translucent area/overlap blend (w3-11's 0.30 areas) is kept.
                if (key === 'stroke' && alpha < 1 && isInvisibleComposite(def.value, bg, alpha)) {
                  const nrgb = resolveColorToRgb(def.value);
                  if (nrgb) {
                    let raised = alpha;
                    for (let a = alpha; a <= 1.0001; a += 0.02) {
                      if (contrastRatio(compositeOver(nrgb, bg, a), bg) >= 3) { raised = Math.min(1, a); break; }
                      raised = Math.min(1, a);
                    }
                    if (raised > alpha) {
                      if (!p.strokeOpacity || typeof p.strokeOpacity !== 'object') p.strokeOpacity = {};
                      p.strokeOpacity.value = Math.round(raised * 100) / 100;
                    }
                  }
                }
              }
            } else if (def.value && typeof def.value === 'object' && Array.isArray(def.value.stops)) {
              for (const st of def.value.stops) {
                if (st && typeof st.color === 'string' && isInvisibleComposite(st.color, bg, alpha)) {
                  st.color = nudgeFillForContrast(st.color, bg, darkCanvas, alpha);
                }
              }
            }
          }
        }
      }
      if (Array.isArray(m.marks)) walkMarks(m.marks);
    }
  };
  walkMarks(spec.marks);
  return spec;
}

/**
 * D-318: in a top-level LAYERED arc/pie spec, the shared `encoding.color`
 * channel is applied to every layer, so a text-label layer that declares no
 * colour of its own inherits the SERIES colour scale — the label is then drawn
 * in the exact colour of the slice it sits on (invisible) or in the series
 * colour against the canvas (low-contrast on one theme). Pin an explicit
 * canvas-readable ink (as encoding.color.value) on any text layer that would
 * otherwise inherit the shared colour channel, so labels are never painted with
 * their own slice's fill. Only fires when (a) the spec is layered, (b) an arc
 * layer is present, (c) a shared field/value colour channel exists, and (d) the
 * text layer has no colour of its own — an authored per-label colour is kept.
 * Mutates spec.
 */
export function reconcileInheritedArcLabelColors(spec: any, darkCanvas: boolean): any {
  if (!spec || typeof spec !== 'object' || !Array.isArray(spec.layer)) return spec;
  const shared = spec.encoding?.color;
  const sharedIsChannel = shared && typeof shared === 'object' && (shared.field !== undefined || shared.value !== undefined);
  if (!sharedIsChannel) return spec;
  const hasArc = spec.layer.some((l: any) => (typeof l?.mark === 'string' ? l.mark : l?.mark?.type) === 'arc');
  if (!hasArc) return spec;
  const readable = darkCanvas ? '#f0f0f0' : '#222222';
  for (const layer of spec.layer) {
    const markType = typeof layer?.mark === 'string' ? layer.mark : layer?.mark?.type;
    if (markType !== 'text') continue;
    const own = layer.encoding?.color;
    const ownIsChannel = own && typeof own === 'object' && (own.field !== undefined || own.value !== undefined);
    if (ownIsChannel) continue; // author pinned a label colour — respect it
    layer.encoding = layer.encoding && typeof layer.encoding === 'object' ? layer.encoding : {};
    layer.encoding.color = { value: readable };
  }
  return spec;
}

/**
 * D-318 (text-on-mark-contrast-not-reconciled, field-driven sub-case): a text
 * layer draws value labels INSIDE a filled mark, and BOTH the fill and the text
 * ink are DATA-DRIVEN literal colours (`encoding.color.field` with `scale:null`,
 * or an omitted scale over a field whose values are all colour strings). Vega
 * honours those hex values verbatim, so a row whose text colour is
 * near-isoluminant with its own bar fill (e.g. #222 label on a #1b2a41 bar, or
 * #fff label on a #f7f7f2 bar) is unreadable — and, crucially, the contrast is
 * against the MARK the label sits on, NOT the canvas, so the theme-vs-canvas
 * reconcilers never see it. For each shared data row, measure the text colour
 * against the fill colour of the SAME row; when it is sub-3:1, repaint that
 * row's text value with pure black or white — whichever maximises contrast on
 * that fill. Theme-independent (the label sits on the mark, not the page), and
 * only invisible combinations are touched, so a legibly-authored on-bar label
 * is preserved on both themes. Mutates + returns the spec.
 */
export function reconcileFieldDrivenTextOnFill(spec: any): any {
  if (!spec || typeof spec !== 'object' || !Array.isArray(spec.layer)) return spec;
  const values = spec.data?.values;
  if (!Array.isArray(values) || values.length === 0) return spec;

  // A colour encoding carries LITERAL colours (not a category→palette map) when
  // scale is explicitly null, or scale is absent and every value of the field
  // resolves to a colour. A real categorical field ("dark-1", "Alpha") fails
  // the all-colours test and is skipped, so this never hijacks a scaled channel.
  const literalColorField = (layer: any): string | null => {
    const col = layer?.encoding?.color;
    if (!col || typeof col !== 'object' || typeof col.field !== 'string') return null;
    if (col.scale !== null && col.scale !== undefined) return null;
    const f = col.field;
    const seen = values
      .map((r: any) => (r && typeof r === 'object' ? r[f] : undefined))
      .filter((v: any) => v !== undefined && v !== null);
    if (seen.length === 0) return null;
    return seen.every((v: any) => typeof v === 'string' && resolveColorToRgb(v)) ? f : null;
  };

  let textField: string | null = null;
  let fillField: string | null = null;
  for (const layer of spec.layer) {
    const markType = typeof layer?.mark === 'string' ? layer.mark : layer?.mark?.type;
    const f = literalColorField(layer);
    if (!f) continue;
    if (markType === 'text') { if (!textField) textField = f; }
    else if (!fillField) { fillField = f; }
  }
  if (!textField || !fillField || textField === fillField) return spec;

  const WHITE: [number, number, number] = [255, 255, 255];
  const BLACK: [number, number, number] = [0, 0, 0];
  for (const row of values) {
    if (!row || typeof row !== 'object') continue;
    const tRgb = typeof row[textField] === 'string' ? resolveColorToRgb(row[textField]) : null;
    const fRgb = typeof row[fillField] === 'string' ? resolveColorToRgb(row[fillField]) : null;
    if (!tRgb || !fRgb) continue;
    if (contrastRatio(tRgb, fRgb) < 3) {
      row[textField] = contrastRatio(WHITE, fRgb) >= contrastRatio(BLACK, fRgb) ? '#ffffff' : '#000000';
    }
  }
  return spec;
}

/**
 * D-503 (field-driven-literal-fill-not-reconciled-vs-canvas): a NON-text mark
 * (bar/point/area/…) whose fill comes from a `color.field` with `scale:null`
 * (or an absent scale over a field whose values are all colour strings) is
 * painted with those literal hex values verbatim. When a value is
 * near-isoluminant with the canvas — #f7f7f2 (1.07:1) / #eef3f7 (1.12:1) on the
 * white light card, #1b2a41 (1.20:1) / #2e1a47 (1.16:1) on the #333 dark card —
 * the mark disappears. {@link reconcileMarkFillsVsCanvas} only understands a
 * solid `mark.fill` or a `scale.range` array, so these per-row literal fills
 * were never contrast-checked. For each shared data row, nudge a fill colour
 * that is sub-3:1 on the effective canvas toward the readable side
 * (hue-preserving, via {@link nudgeFillForContrast}); a fill that already clears
 * the floor is left byte-for-byte unchanged, so a legible spec on either theme
 * is untouched. Only fires for a literal-colour field on a non-text mark, so a
 * scaled categorical channel is never hijacked. Mutates + returns the spec.
 */
export function reconcileFieldDrivenFillsVsCanvas(spec: any, bg: [number, number, number], darkCanvas: boolean): any {
  if (!spec || typeof spec !== 'object') return spec;
  const values = spec.data?.values;
  if (!Array.isArray(values) || values.length === 0) return spec;

  // A colour encoding carries LITERAL colours (not a category→palette map) when
  // scale is explicitly null/absent AND every value of the field resolves to a
  // colour string. A real categorical field ("dark-1", "Alpha") fails the
  // all-colours test and is skipped, so a scaled channel is never touched.
  const literalColorField = (layer: any): string | null => {
    const col = layer?.encoding?.color ?? layer?.encoding?.fill;
    if (!col || typeof col !== 'object' || typeof col.field !== 'string') return null;
    if (col.scale !== null && col.scale !== undefined) return null;
    const f = col.field;
    const seen = values
      .map((r: any) => (r && typeof r === 'object' ? r[f] : undefined))
      .filter((v: any) => v !== undefined && v !== null);
    if (seen.length === 0) return null;
    return seen.every((v: any) => typeof v === 'string' && resolveColorToRgb(v)) ? f : null;
  };

  const nudged = new Map<string, string>();
  const fixFieldOnNode = (layer: any): void => {
    const markType = typeof layer?.mark === 'string' ? layer.mark : layer?.mark?.type;
    if (markType === 'text') return; // text ink is handled by the text-on-fill pass
    const f = literalColorField(layer);
    if (!f) return;
    for (const row of values) {
      if (!row || typeof row !== 'object') continue;
      const c = row[f];
      if (typeof c !== 'string') continue;
      const rgb = resolveColorToRgb(c);
      if (!rgb) continue;
      if (contrastRatio(rgb, bg) >= 3) continue;
      let out = nudged.get(c);
      if (out === undefined) { out = nudgeFillForContrast(c, bg, darkCanvas); nudged.set(c, out); }
      row[f] = out;
    }
  };

  fixFieldOnNode(spec);
  if (Array.isArray(spec.layer)) for (const layer of spec.layer) fixFieldOnNode(layer);
  return spec;
}

/**
 * D-319 (authored-fill-vanishes-into-canvas, gradient sub-case): a model emits
 * bogus colour-NAME string VALUES ("rainbow", "gradient", "multicolor",
 * "#green") that Vega cannot resolve; the original inline fix serialised the
 * spec and replaced every occurrence of those tokens, which ALSO renamed the
 * object KEY of a legitimate gradient fill — `{"gradient":"linear",...}` became
 * `{"#4ecdc4":"linear",...}`, a fill object with no `gradient` key, so the mark
 * rendered with NO fill and vanished (w3-07, both themes). Restricting the
 * name→colour substitution to a VALUE position (immediately after a `:`) leaves
 * object keys — and therefore valid gradient objects — intact. Colour-name
 * *values* like `"fill":"gradient"` are still corrected. Mutates via a
 * round-trip; on parse failure the original spec is returned unchanged.
 */
export function fixBogusColorNameValues(spec: any): any {
  if (!spec || typeof spec !== 'object') return spec;
  let s = JSON.stringify(spec);
  // "#green" → "green": a hashed CSS colour name is only ever a value.
  s = s.replace(
    /"#(green|red|orange|blue|yellow|purple|black|white|gray|grey|cyan|magenta|pink|brown|violet|indigo|gold|silver)"/gi,
    '"$1"',
  );
  // Bogus palette-name VALUES → concrete colours. Anchored to a `:` so a
  // same-spelled object key (notably a gradient fill's "gradient" key) is safe.
  s = s.replace(/:\s*"rainbow"/gi, ':"#ff6b6b"');
  s = s.replace(/:\s*"gradient"/gi, ':"#4ecdc4"');
  s = s.replace(/:\s*"multicolor"/gi, ':"#45b7d1"');
  try {
    return JSON.parse(s);
  } catch {
    return spec;
  }
}

/**
 * D-258: drop an authored `background` whose polarity is opposite to the active
 * theme so the theme's own surface shows through. Only fires when the colour is
 * resolvable AND clearly the wrong side (a light card under dark theme, or a
 * dark card under light theme); a background close to the theme surface, an
 * unresolvable value, or `transparent`/`null` is left untouched. Mutates spec.
 */
// True when `background` is a resolvable colour of the WRONG polarity for the
// active theme (a light card under the dark theme, or a dark card under the
// light theme). Only measurable colours are judged; an unresolvable value is
// left alone.
function isWrongPolarityBackground(background: any, isDarkMode: boolean): boolean {
  const rgb = typeof background === 'string' ? resolveColorToRgb(background) : null;
  if (!rgb) return false;
  const bgIsLight = relLuminance(rgb) >= 0.5;
  return (isDarkMode && bgIsLight) || (!isDarkMode && !bgIsLight);
}

export function reconcileBackground(spec: any, isDarkMode: boolean): any {
  if (!spec || typeof spec !== 'object') return spec;
  // Wrong polarity: light background while the theme is dark, or dark
  // background while the theme is light. Drop it so the theme's own surface
  // applies and the guide-colour walk measures against the real canvas.
  if (isWrongPolarityBackground(spec.background, isDarkMode)) {
    delete spec.background;
  }
  // D-316 (config-background-polarity-unreconciled): Vega honours
  // `config.background` as the canvas fill exactly like a top-level
  // `background`, but the original reconcile only inspected the top level. A
  // spec that pins BOTH (w3-08 sets `background:"#fff"` AND
  // `config.background:"#fff"`) therefore kept a light canvas under the dark
  // theme even after the top-level drop, which collapsed the dark theme's
  // white guide titles onto white (titleColor #fff on #fff = 1.00:1). Drop a
  // wrong-polarity config.background the same way; a matching-polarity one is
  // left untouched, so a correct spec on either theme is never altered.
  if (spec.config && typeof spec.config === 'object' &&
      isWrongPolarityBackground(spec.config.background, isDarkMode)) {
    delete spec.config.background;
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
 *
 * Concat specs (`vconcat`/`hconcat`/`concat`) are preserved for the same
 * reason as `layer`: concat panels share scales by default, so an explicit
 * domain in one panel silently swallows another panel's categories — the
 * marks render with an undefined fill and vanish. `resolve.scale.*
 * = "independent"` is the only correction for that, and it is not part of
 * the faceted `spec.spec` hang this function guards against.
 */
export function sanitizeResolveScale(spec: any): any {
  if (!spec || typeof spec !== 'object') return spec;
  const isComposite =
    Array.isArray(spec.layer) ||
    Array.isArray(spec.vconcat) ||
    Array.isArray(spec.hconcat) ||
    Array.isArray(spec.concat);
  if (spec.resolve && spec.resolve.scale && !isComposite) {
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

/**
 * The vega-embed 'excel' theme's `range.category` (the LIGHT theme base), IN
 * EXACT ORDER. Kept in sync with node_modules/vega-themes so that an extended
 * palette whose PREFIX is this array is byte-for-byte identical to the shipped
 * light theme for any ordinal domain that fits inside 10 (domain[i] → base[i]
 * is exactly what the theme produces). Used by applyCategoricalPaletteFix to
 * extend the range for a categorical channel whose cardinality is unknowable
 * statically (data-driven sequence/transform/url) without recolouring a
 * genuine ≤10-series light spec (D-265).
 */
export const EXCEL_CATEGORY_10: string[] = [
  '#4572a7', '#aa4643', '#8aa453', '#71598e', '#4598ae',
  '#d98445', '#94aace', '#d09393', '#b9cc98', '#a99cbc',
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
 * WCAG contrast floor every GENERATED categorical swatch must clear on the
 * ACTIVE theme canvas. 3.0 is the WCAG non-text/UI floor; we target a hair
 * above (3.2) so an 8-bit-rounded swatch still measures ≥3.0 after quantisation.
 */
export const CATEGORY_CONTRAST_FLOOR = 3.2;

/** The theme card backgrounds a categorical swatch is composited over: the
 *  Vega 'excel' light card (#ffffff) and the 'dark' card (#333333). */
const LIGHT_CANVAS_RGB: [number, number, number] = [255, 255, 255];
const DARK_CANVAS_RGB: [number, number, number] = [51, 51, 51];

/**
 * D-253 (theme/contrast): resolve a generated swatch's LIGHTNESS from the
 * active theme so it clears CATEGORY_CONTRAST_FLOOR against that theme's
 * canvas, HUE and SATURATION preserved. The fixed L/S tiers below sweep the
 * whole hue wheel at one lightness, so the yellow/lime band sinks to ~1.7:1 on
 * white and the blue-violet band to ~1.9:1 on #333 — a theme-blind constant,
 * not a theme-resolved value. Binary-search lightness toward the readable side
 * (darker on the white card, lighter on the #333 card) until the ROUNDED hex
 * clears the floor; a swatch already clearing it is returned unchanged. Hue is
 * untouched so the palette stays hue-separable. PURE + exported for testing.
 */
export function clampLightnessForContrast(
  h: number,
  s: number,
  l: number,
  darkCanvas: boolean,
): number {
  const bg = darkCanvas ? DARK_CANVAS_RGB : LIGHT_CANVAS_RGB;
  const ratioAt = (ll: number): number => {
    const rgb = resolveColorToRgb(hslToHex(h, s, ll));
    return rgb ? contrastRatio(rgb, bg) : 0;
  };
  if (ratioAt(l) >= CATEGORY_CONTRAST_FLOOR) return l;
  // Readable side: on the light card darker (lower L) raises contrast; on the
  // dark card lighter (higher L) does. Binary-search the boundary, converging
  // onto the side that satisfies the floor.
  let lo: number, hi: number;
  if (darkCanvas) { lo = l; hi = 1; } else { lo = 0; hi = l; }
  for (let i = 0; i < 24; i++) {
    const mid = (lo + hi) / 2;
    const ok = ratioAt(mid) >= CATEGORY_CONTRAST_FLOOR;
    if (darkCanvas) { if (ok) hi = mid; else lo = mid; }
    else { if (ok) lo = mid; else hi = mid; }
  }
  return darkCanvas ? hi : lo;
}

/**
 * Generate `n` perceptually-distinct categorical colours by EVEN hue spacing
 * (guaranteed distinct — hue step 360/n) across three lightness/saturation
 * tiers, tuned so neighbouring hues stay perceptually separable (ΔE) on the
 * active canvas. Used by the KNOWN-cardinality (>10) branch (D-265); its ΔE
 * tuning is intentionally NOT WCAG-lightness-clamped, because forcing every
 * hue to a fixed contrast floor collapses the tier lightness spread and drops
 * neighbour ΔE below the confusable line at moderate n. Injective for any n.
 * Returns exactly `n` colours (min 1).
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
 * A data-driven categorical colour field (produced by a `data.sequence`
 * generator or a `transform` calculate) has no distinct value we can count
 * statically — analyzeCategoricalColor reports cardinality 0 for it. This
 * returns an UPPER BOUND on that cardinality from the data object: the row
 * count of inline `data.values`, or the length of a `data.sequence`. Distinct
 * colour values can never exceed the row count, so a palette sized to this
 * bound is guaranteed injective. Returns 0 when no bound is derivable (e.g.
 * data.url). PURE + exported for unit testing.
 */
export function estimateDataDrivenCardinality(data: any): number {
  if (!data || typeof data !== 'object') return 0;
  const vals = (data as any).values;
  if (Array.isArray(vals)) return vals.length;
  const seq = (data as any).sequence;
  if (seq && typeof seq.start === 'number' && typeof seq.stop === 'number') {
    const step = typeof seq.step === 'number' && seq.step !== 0 ? seq.step : 1;
    const n = Math.ceil((seq.stop - seq.start) / step);
    return n > 0 ? n : 0;
  }
  return 0;
}

/**
 * Largest data-driven upper-bound estimate we TRUST as a palette size. Above
 * this the sequence is so long that its row count massively overshoots the
 * likely distinct-series count (e.g. a 2000-row sequence rendering 20 series),
 * so sizing to it would emit a needlessly huge range; we fall back to the
 * fixed CATEGORY_EXTEND_TARGET instead, whose golden-angle prefix already
 * stays injective for the common ≤40-series case. Bounds are only used when
 * they land in (CATEGORY_EXTEND_TARGET, MAX_ESTIMATED_CATEGORY] — i.e. a
 * modest generator like `sequence{stop:50}` that genuinely needs >40 colours.
 */
export const MAX_ESTIMATED_CATEGORY = 64;

/**
 * Inspect a spec's colour encoding. Returns whether it is a CATEGORICAL colour
 * channel (nominal/ordinal, or an untyped non-quantitative field), whether the
 * author has already pinned an explicit colour scale (range/scheme), the
 * distinct-value cardinality when knowable (explicit scale.domain length else a
 * distinct count over inline data rows, else 0), an UPPER-BOUND estimate for
 * the data-driven (unknowable) case, and the effective mark opacity. Scans the
 * top-level encoding and, if absent there, the first layer that carries a
 * colour channel.
 */
export function analyzeCategoricalColor(spec: any): {
  isCategorical: boolean;
  hasExplicitColors: boolean;
  cardinality: number;
  estimatedCardinality: number;
  opacity: number;
} {
  const none = { isCategorical: false, hasExplicitColors: false, cardinality: 0, estimatedCardinality: 0, opacity: 1 };
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
  const dataObj =
    (container?.data && typeof container.data === 'object' && container.data) ||
    (spec.data && typeof spec.data === 'object' && spec.data) ||
    null;
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

  // Upper bound for the data-driven (unknowable) case: distinct colour values
  // can never exceed the backing row/sequence count, so a palette sized to
  // this stays injective. When a direct distinct count is available it is the
  // exact figure and wins.
  const estimatedCardinality =
    cardinality > 0 ? cardinality : estimateDataDrivenCardinality(dataObj);

  // Effective mark opacity: explicit mark.opacity, else an opacity encoding
  // value, else 1.
  let opacity = 1;
  const mark = container?.mark ?? spec.mark;
  if (mark && typeof mark === 'object' && typeof mark.opacity === 'number') {
    opacity = mark.opacity;
  } else if (enc.opacity && typeof enc.opacity === 'object' && typeof enc.opacity.value === 'number') {
    opacity = enc.opacity.value;
  }

  return { isCategorical, hasExplicitColors, cardinality, estimatedCardinality, opacity };
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
/** How far an unknown-cardinality categorical range is extended (matches the
 *  full-Vega native path's cap in vegaPlugin.buildExtendedCategoricalPalette). */
export const CATEGORY_EXTEND_TARGET = 40;

/**
 * Extend a base categorical palette to `target` entries by appending
 * canvas-biased hues after the base colours. The base PREFIX is left
 * untouched, so rendering is byte-for-byte identical for any ordinal domain
 * that fits inside `base`; only larger domains — which would otherwise recycle
 * — receive the generated tail.
 *
 * D-253 (regression): the tail was previously grown by GOLDEN-ANGLE
 * accumulation (hue += 137.508°). That keeps a healthy min-separation up to a
 * ~30-entry tail, but past it the accumulated hues start landing near earlier
 * ones — at a 40-entry tail (target 50, vega-lite-w2-12) two generated colours
 * fall ~10 RGB units apart, reading as the SAME swatch: a perceptual recycle
 * even though every hex is unique. Specs needing ≤40 total (w2-11/13) stayed
 * clear, which is why only w2-12 regressed.
 *
 * The tail is now spaced EVENLY over the known tail count (hue step
 * 360/tailCount), which is deterministic and never clusters — a 40-entry tail
 * gets a uniform 9° gap versus golden-angle's ~4.7° worst case — with a
 * three-tier lightness/saturation rotation (mirroring generateCategoricalPalette)
 * so hues that wrap back near each other still separate by lightness. The tail
 * is injective and stays perceptually separable for the full modest-cardinality
 * range (up to MAX_ESTIMATED_CATEGORY). PURE + exported for unit testing.
 */
export function extendCategoricalPalette(
  base: string[],
  target: number,
  darkCanvas: boolean,
): string[] {
  const out = base.slice();
  const total = Math.max(base.length, Math.floor(target));
  const tailCount = total - out.length;
  if (tailCount <= 0) return out;
  // Three tiers rotate lightness/saturation so evenly-spaced hues that come
  // back near each other after a full wrap still separate by lightness.
  const L = darkCanvas ? [0.62, 0.72, 0.54] : [0.45, 0.34, 0.55];
  const S = darkCanvas ? [0.70, 0.85, 0.62] : [0.72, 0.88, 0.60];
  for (let i = 0; i < tailCount; i++) {
    // Even hue spacing over the tail: uniform 360/tailCount gap, no clustering.
    const hue = (20 + (i * 360) / tailCount) % 360;
    const t = i % 3;
    // D-253: clamp the generated tail's lightness to clear the contrast floor
    // on the active canvas (hue preserved), so the tail never introduces the
    // pale/low-contrast members that the fixed-lightness ramp did.
    const l = clampLightnessForContrast(hue, S[t], L[t], darkCanvas);
    out.push(hslToHex(hue, S[t], l));
  }
  return out;
}

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
  } else if (info.cardinality === 0) {
    // D-265 (data-driven) — the colour field is produced by a sequence /
    // transform / data.url, so its cardinality is UNKNOWABLE statically and
    // the 10-entry theme range would silently recycle for >10 series (the
    // vega-lite-w2-04/11/12/13 case). Inject a range that BEGINS with the
    // active theme's own 10 colours — byte-identical output for any ≤10-series
    // spec, and keeping few-series data-driven specs (w3-05: 3 series, w3-12:
    // 2) on their well-separated theme hues rather than a target-spaced ramp —
    // and stays injective beyond it. A low-opacity light canvas needs the
    // saturated base (D-260), so the prefix follows the theme-resolved choice.
    const base =
      (!isDarkMode && info.opacity < CATEGORY_LOW_OPACITY)
        ? SATURATED_CATEGORY_10
        : isDarkMode
          ? SATURATED_CATEGORY_10
          : EXCEL_CATEGORY_10;
    // Size to the modest data-driven upper bound when known (>40, ≤64) so a
    // 50-slot sequence stays injective; a huge sequence keeps the default.
    const est = info.estimatedCardinality;
    const target =
      est > CATEGORY_EXTEND_TARGET && est <= MAX_ESTIMATED_CATEGORY
        ? est
        : CATEGORY_EXTEND_TARGET;
    // D-253: the generated TAIL is contrast-clamped (see extendCategoricalPalette)
    // so the extension never introduces sub-3:1 members the way the old
    // fixed-lightness tail did (w2-04/w2-12 pastels).
    palette = extendCategoricalPalette(base, target, isDarkMode);
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

/**
 * D-258 (regression): effective mark-opacity floor for a LOW-OPACITY
 * CATEGORICAL scatter under the LIGHT theme.
 *
 * applyCategoricalPaletteFix gives the light theme the saturated tableau10 base
 * (D-260) so the LEGEND swatches stay hue-separable, but that alone does not
 * carry the PLOT. Compositing a semi-transparent mark over the light canvas is
 *   out = 255*(1 - a) + c*a
 * which floods every channel toward 255 and compresses chroma far more than the
 * same mark composited over the dark theme's near-black canvas. Measured on the
 * 5-group saturated palette at opacity 0.35: min between-group ΔE(CIE76) is
 * 16.6 on black (dark passes) but only 12.4 on white — the groups wash back
 * together under heavy overplot even though the swatches themselves differ.
 * WCAG luminance contrast is ~1.0 in BOTH themes here (the hues are equal-
 * lightness, distinct only in chroma), so it is not the governing metric;
 * between-group ΔE is.
 *
 * Lifting the effective light opacity to LIGHT_CATEGORY_OPACITY_FLOOR restores
 * the on-white composite to dark parity (min-ΔE 16.2 at 0.45 vs dark's 16.6)
 * while keeping the translucency the "low-opacity survival" spec intends. The
 * fix is THEME-RESOLVED: it fires only in the light theme (dark already
 * survives at the authored opacity and is never touched, so the two themes stay
 * a matched pair), only for a genuinely categorical colour channel, and only
 * when the authored effective opacity is below the floor — a fully-opaque spec,
 * or one whose author already set opacity at/above the floor, is left as-is.
 */
export const LIGHT_CATEGORY_OPACITY_FLOOR = 0.45;

/**
 * Raise the effective mark opacity of a low-opacity light-theme categorical
 * spec to LIGHT_CATEGORY_OPACITY_FLOOR (see the constant's note for the ΔE
 * rationale). Locates the colour-bearing container the same way
 * analyzeCategoricalColor does (top-level, else the first layer carrying a
 * colour channel) and writes the floor onto whichever opacity carrier the mark
 * uses: an object mark's `opacity`, a string mark promoted to an object, or an
 * `encoding.opacity.value`. Returns the applied opacity, else null. Mutates
 * spec. PURE-ish (single spec mutation) + exported for unit testing.
 */
export function liftLowOpacityLightCategorical(spec: any, isDarkMode: boolean): number | null {
  if (!spec || typeof spec !== 'object' || isDarkMode) return null;
  const info = analyzeCategoricalColor(spec);
  if (!info.isCategorical) return null;
  if (!(info.opacity < LIGHT_CATEGORY_OPACITY_FLOOR)) return null;

  // Resolve the same container analyzeCategoricalColor measured: top-level
  // encoding if it carries colour/fill, else the first layer that does.
  let container: any = spec;
  const topEnc = spec.encoding && typeof spec.encoding === 'object' ? spec.encoding : null;
  if (!(topEnc && (topEnc.color || topEnc.fill)) && Array.isArray(spec.layer)) {
    for (const layer of spec.layer) {
      if (layer && layer.encoding && (layer.encoding.color || layer.encoding.fill)) {
        container = layer;
        break;
      }
    }
  }

  const floor = LIGHT_CATEGORY_OPACITY_FLOOR;
  const enc = container.encoding && typeof container.encoding === 'object' ? container.encoding : null;

  // An explicit opacity ENCODING value takes precedence in analyzeCategoricalColor
  // only when the mark carries no numeric opacity, so mirror that order here.
  const mark = container.mark ?? spec.mark;
  if (mark && typeof mark === 'object' && typeof mark.opacity === 'number') {
    mark.opacity = floor;
    return floor;
  }
  if (enc && enc.opacity && typeof enc.opacity === 'object' && typeof enc.opacity.value === 'number') {
    enc.opacity.value = floor;
    return floor;
  }
  // A string mark ('point') with no opacity anywhere reads as opacity 1 in
  // analyzeCategoricalColor, so this branch is only reached when info.opacity
  // came from a numeric source above; guard anyway by promoting a string mark.
  if (typeof mark === 'string') {
    container.mark = { type: mark, opacity: floor };
    return floor;
  }
  if (mark && typeof mark === 'object') {
    mark.opacity = floor;
    return floor;
  }
  return null;
}
