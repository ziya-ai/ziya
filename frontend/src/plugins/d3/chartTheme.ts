/**
 * Theme-aware colour resolution and WCAG contrast helpers for basicChart.
 *
 * basicChart previously ignored the `isDarkMode` argument entirely and painted
 * axis text, marker strokes and data-labels with hardcoded light-tuned constants
 * ('#666', '#fff', black axes) that vanish on a dark surface (D-011). It also
 * handed caller-supplied `color` straight to the SVG `fill` attribute with no
 * validation, so 'transparent' / zero-alpha / unresolvable design-tokens erased
 * the geometry or fell back to SVG-initial black (D-012).
 *
 * These helpers resolve colours FROM the active theme (never a blind constant
 * swap) and clamp caller colours toward the WCAG graphical floor against the
 * theme's own background.
 */

/** Canonical chart surfaces used for contrast resolution. */
export const CHART_LIGHT_BG = '#ffffff';
export const CHART_DARK_BG = '#1e1e1e';

export interface ChartColors {
    /** Background used for contrast math (the effective chart surface). */
    bg: string;
    /** Axis lines + tick text. */
    axis: string;
    /** Data / point / bubble labels. */
    label: string;
    /** Marker halo stroke (equals the surface so overlapping markers separate). */
    markerStroke: string;
    /** Fallback series fill when the caller supplies none / an unusable colour. */
    seriesFallback: string;
    /** Default label/axis font size in px. */
    fontSize: number;
}

/**
 * Resolve chart colours from the active theme. Caller `style` overrides win
 * ONLY when they are present; every default is chosen per-theme so it satisfies
 * contrast against that theme's own background (see the table in the tests).
 */
export function resolveChartColors(isDarkMode: boolean, style: any = {}): ChartColors {
    // Effective surface: a caller-pinned background wins, else the theme surface.
    const rawBg = style?.background || (isDarkMode ? CHART_DARK_BG : CHART_LIGHT_BG);
    // Resolve the surface to a hex so the light/dark default choice AND contrast
    // maths run against the ACTUAL backdrop, not a raw isDarkMode flag — a caller
    // may pin a light panel under dark theme or vice-versa (D-006/w1-09: a
    // #f5f5f5 rect under dark theme left the dark-theme pale #cfcfcf ticks at
    // 1.43:1 on their own backdrop).
    const cbg = classifyColor(rawBg);
    const bgHex = cbg?.hex
        || (cbg?.named ? namedColorToHex(cbg.named) : null)
        || (isDarkMode ? CHART_DARK_BG : CHART_LIGHT_BG);
    const darkSurface = isDarkBackground(bgHex);

    const axisDefault = darkSurface ? '#cfcfcf' : '#333333';
    const labelDefault = darkSurface ? '#e0e0e0' : '#333333';
    return {
        bg: bgHex,
        // A caller axis/label colour is honoured verbatim when it clears the 4.5
        // text floor on the effective surface, and reconciled toward legibility
        // (identity preserved as far as possible) when it does not — D-159/w1-10:
        // an authored light-tuned #0b5394 is 7.84:1 on white (kept) but 2.13:1 on
        // the dark panel (nudged). Absent -> the surface-appropriate default,
        // which already clears the floor on that surface (D-006).
        axis: ensureReadableFill(style?.axisColor, bgHex, axisDefault, 4.5),
        label: ensureReadableFill(style?.labelColor, bgHex, labelDefault, 4.5),
        // Halo stroke = the effective surface, so adjacent/overlapping markers
        // read as separate shapes against whatever backdrop is in play.
        markerStroke: style?.markerStroke || bgHex,
        seriesFallback: 'steelblue',
        fontSize: typeof style?.fontSize === 'number' ? style.fontSize : 11,
    };
}

// ── contrast maths (WCAG 2.x) ────────────────────────────────────────────────

function srgbToLinear(c: number): number {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
}

/** Parse #rgb / #rrggbb to [r,g,b]; null if not a hex literal. */
function hexToRgb(hex: string): [number, number, number] | null {
    let h = hex.trim().replace(/^#/, '');
    if (h.length === 3) h = h.split('').map(ch => ch + ch).join('');
    if (h.length !== 6 || /[^0-9a-fA-F]/.test(h)) return null;
    return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)];
}

function relLuminance(rgb: [number, number, number]): number {
    const [r, g, b] = rgb.map(srgbToLinear);
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/** WCAG contrast ratio between two hex colours. Returns 1 if either is unparseable. */
export function contrastRatio(a: string, b: string): number {
    const ra = hexToRgb(a), rb = hexToRgb(b);
    if (!ra || !rb) return 1;
    const la = relLuminance(ra), lb = relLuminance(rb);
    const hi = Math.max(la, lb), lo = Math.min(la, lb);
    return (hi + 0.05) / (lo + 0.05);
}

function rgbToHex(rgb: [number, number, number]): string {
    return '#' + rgb.map(v => Math.max(0, Math.min(255, Math.round(v))).toString(16).padStart(2, '0')).join('');
}

function mix(a: [number, number, number], b: [number, number, number], t: number): [number, number, number] {
    return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];
}

/**
 * Classify a caller colour string.
 *  - 'transparent' / 'none' / '' / zero-alpha rgba()  -> null  (treat as absent)
 *  - design tokens: var(--x), $token, theme.accent, has whitespace -> null
 *  - #hex / #rrggbb  -> { hex }
 *  - rgb()/rgba() with alpha>0 -> { hex } (alpha dropped for contrast; opacity is separate)
 *  - a bare CSS keyword (e.g. 'red', 'steelblue') -> { named } (valid, contrast unknown)
 */
export function classifyColor(input: any): { hex?: string; named?: string } | null {
    if (typeof input !== 'string') return null;
    const s = input.trim();
    if (!s) return null;
    const lower = s.toLowerCase();
    if (lower === 'transparent' || lower === 'none') return null;
    // Unresolvable design-system tokens: CSS functions we can't resolve, sigils,
    // custom-prop refs, dotted namespaces, or anything with whitespace.
    if (/^var\(|^calc\(|^\$/.test(lower) || lower.includes('--') || /\s/.test(s) || /[a-z0-9]\.[a-z]/i.test(s)) {
        return null;
    }
    if (s[0] === '#') {
        return hexToRgb(s) ? { hex: s } : null;
    }
    const rgba = lower.match(/^rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([\d.]+)\s*)?\)$/);
    if (rgba) {
        const alpha = rgba[4] === undefined ? 1 : parseFloat(rgba[4]);
        if (!(alpha > 0)) return null; // fully transparent -> absent
        return { hex: rgbToHex([parseInt(rgba[1], 10), parseInt(rgba[2], 10), parseInt(rgba[3], 10)]) };
    }
    if (lower.startsWith('rgb')) return null; // malformed rgb(...)
    // Bare alphabetic keyword — assume a valid CSS named colour.
    if (/^[a-z]+$/i.test(s)) return { named: s };
    return null;
}

/**
 * Compact CSS named-colour → #rrggbb table. Additive: `classifyColor` keeps
 * returning `{ named }` for these (its pass-through contract is unchanged, so
 * basicChart / forceDirected are untouched); callers that need to REASON about a
 * named colour's contrast (e.g. the joint element-attrs merge, D-152) can
 * resolve it here first. Unknown names return null (caller passes them through).
 * Covers the CSS basic keywords plus the common extended names models emit.
 */
const CSS_NAMED_COLORS: Record<string, string> = {
    black: '#000000', silver: '#c0c0c0', gray: '#808080', grey: '#808080',
    white: '#ffffff', maroon: '#800000', red: '#ff0000', purple: '#800080',
    fuchsia: '#ff00ff', magenta: '#ff00ff', green: '#008000', lime: '#00ff00',
    olive: '#808000', yellow: '#ffff00', navy: '#000080', blue: '#0000ff',
    teal: '#008080', aqua: '#00ffff', cyan: '#00ffff', orange: '#ffa500',
    // common extended keywords
    aliceblue: '#f0f8ff', beige: '#f5f5dc', brown: '#a52a2a', chocolate: '#d2691e',
    coral: '#ff7f50', crimson: '#dc143c', darkblue: '#00008b', darkgray: '#a9a9a9',
    darkgrey: '#a9a9a9', darkgreen: '#006400', darkorange: '#ff8c00', darkred: '#8b0000',
    dodgerblue: '#1e90ff', firebrick: '#b22222', forestgreen: '#228b22', gold: '#ffd700',
    goldenrod: '#daa520', greenyellow: '#adff2f', hotpink: '#ff69b4', indigo: '#4b0082',
    khaki: '#f0e68c', lavender: '#e6e6fa', lightblue: '#add8e6', lightcoral: '#f08080',
    lightgoldenrodyellow: '#fafad2', lightgray: '#d3d3d3', lightgrey: '#d3d3d3',
    lightgreen: '#90ee90', lightpink: '#ffb6c1', lightyellow: '#ffffe0', limegreen: '#32cd32',
    mediumblue: '#0000cd', midnightblue: '#191970', navajowhite: '#ffdead', orangered: '#ff4500',
    orchid: '#da70d6', palegreen: '#98fb98', peru: '#cd853f', pink: '#ffc0cb',
    plum: '#dda0dd', rebeccapurple: '#663399', royalblue: '#4169e1', saddlebrown: '#8b4513',
    salmon: '#fa8072', seagreen: '#2e8b57', sienna: '#a0522d', skyblue: '#87ceeb',
    slateblue: '#6a5acd', slategray: '#708090', slategrey: '#708090', springgreen: '#00ff7f',
    steelblue: '#4682b4', tan: '#d2b48c', tomato: '#ff6347', turquoise: '#40e0d0',
    violet: '#ee82ee', wheat: '#f5deb3', whitesmoke: '#f5f5f5', yellowgreen: '#9acd32',
    dimgray: '#696969', dimgrey: '#696969', darkslategray: '#2f4f4f', darkslategrey: '#2f4f4f',
    cornflowerblue: '#6495ed', mediumseagreen: '#3cb371', indianred: '#cd5c5c',
};

/**
 * Resolve a bare CSS colour keyword to a #rrggbb hex, or null if not a known
 * name. Case-insensitive; trims whitespace. Does NOT touch hex/rgb inputs
 * (those already have a hex via classifyColor).
 */
export function namedColorToHex(name: any): string | null {
    if (typeof name !== 'string') return null;
    const key = name.trim().toLowerCase();
    return CSS_NAMED_COLORS[key] ?? null;
}

/**
 * Return a fill/stroke colour guaranteed usable on `bgHex`:
 *  - unusable / absent / token  -> `fallback`
 *  - a hex/rgb colour below `minRatio` against the surface is nudged toward the
 *    surface's opposite until it clears the floor (identity preserved as far as
 *    possible); if it cannot, `fallback` is used.
 *  - a named CSS colour is passed through unchanged (contrast uncomputable).
 */
export function ensureReadableFill(input: any, bgHex: string, fallback: string, minRatio = 3): string {
    const c = classifyColor(input);
    if (!c) return fallback;
    if (c.named) return c.named;
    const hex = c.hex!;
    if (contrastRatio(hex, bgHex) >= minRatio) return hex;
    const rgb = hexToRgb(hex)!;
    const bgRgb = hexToRgb(bgHex);
    const towardWhite = bgRgb ? relLuminance(bgRgb) < 0.5 : true;
    const target: [number, number, number] = towardWhite ? [255, 255, 255] : [0, 0, 0];
    for (let t = 0.2; t <= 1.0001; t += 0.2) {
        const candidate = rgbToHex(mix(rgb, target, t));
        if (contrastRatio(candidate, bgHex) >= minRatio) return candidate;
    }
    return fallback;
}

/**
 * True when `bgHex` is a dark surface (relative luminance < 0.5), so foreground
 * defaults can be chosen from the EFFECTIVE canvas rather than a raw isDarkMode
 * flag (which is wrong whenever a caller pins a light panel under dark theme or
 * vice-versa). Non-hex input falls back to `false` (assume light).
 */
export function isDarkBackground(bgHex: string): boolean {
    const rgb = hexToRgb(bgHex);
    if (!rgb) return false;
    return relLuminance(rgb) < 0.5;
}

/**
 * Composite `fgHex` over `bgHex` at alpha `a` (0..1) and return the resulting
 * opaque hex. Used to reason about the ACTUAL rendered colour of a
 * partially-transparent stroke/fill (e.g. a link drawn with stroke-opacity),
 * whose on-screen contrast is against the composite, not the nominal colour.
 * Returns `fgHex` unchanged if either colour is not a hex literal.
 */
export function compositeOver(fgHex: string, bgHex: string, a: number): string {
    const fg = hexToRgb(fgHex), bg = hexToRgb(bgHex);
    if (!fg || !bg) return fgHex;
    const t = Math.max(0, Math.min(1, a));
    return rgbToHex([
        fg[0] * t + bg[0] * (1 - t),
        fg[1] * t + bg[1] * (1 - t),
        fg[2] * t + bg[2] * (1 - t),
    ]);
}

// ── band-axis label fitting (D-007) ──────────────────────────────────────────

export interface BandLabelPlan {
    rotate: boolean;
    /** Keep every Nth tick (1 = keep all). */
    keepEvery: number;
    /** Max characters before ellipsis truncation (Infinity = no truncation). */
    maxChars: number;
    /** Bottom margin (px) to reserve so rotated/kept labels are not clipped. */
    reservedBottom: number;
}

/** Truncate with a single-character ellipsis. */
export function truncateLabel(label: string, maxChars: number): string {
    if (!isFinite(maxChars) || label.length <= maxChars) return label;
    if (maxChars <= 1) return '\u2026';
    return label.slice(0, maxChars - 1) + '\u2026';
}

/**
 * Decide rotation / thinning / truncation for a band (categorical) x-axis so
 * dense or long category labels stay legible instead of over-printing into a
 * smear or overhanging the plot ends.
 */
export function planBandLabels(
    labels: string[],
    plotWidth: number,
    fontSize = 11,
    baseBottom = 30,
): BandLabelPlan {
    const n = labels.length;
    if (n === 0 || plotWidth <= 0) {
        return { rotate: false, keepEvery: 1, maxChars: Infinity, reservedBottom: baseBottom };
    }
    const charPx = Math.max(4, fontSize * 0.6);
    const slot = plotWidth / n;                       // px available per category
    const maxLen = labels.reduce((m, l) => Math.max(m, (l || '').length), 0);
    const horizontalRoom = Math.floor(slot / charPx); // chars that fit upright in one slot

    // Thin ticks when even a single character will not fit per slot.
    const minSlot = charPx + 2;
    const keepEvery = slot < minSlot ? Math.ceil(minSlot / slot) : 1;

    // Rotate when horizontal labels would collide (the whole label does not fit
    // in its slot). Kept-every thinning widens the effective slot.
    const effectiveSlot = slot * keepEvery;
    const rotate = maxLen * charPx > effectiveSlot * 0.95;

    if (!rotate) {
        // Upright: truncate to what fits in the (thinned) slot.
        const maxChars = keepEvery > 1 ? Math.max(1, Math.floor((effectiveSlot * 0.95) / charPx)) : Infinity;
        return { rotate: false, keepEvery, maxChars, reservedBottom: baseBottom };
    }
    // Rotated -45deg: labels descend diagonally; cap length so they do not run
    // off the bottom, and reserve headroom proportional to the kept length.
    const maxChars = Math.min(maxLen, 16);
    const reservedBottom = Math.max(baseBottom, Math.round(maxChars * charPx * 0.72) + 12);
    return { rotate: true, keepEvery, maxChars, reservedBottom };
}
