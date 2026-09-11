/**
 * Shared color utility functions for D3 plugins
 * Consolidates duplicate implementations from graphviz, mermaid, and vega plugins
 */

import { hexToRgbSafe } from './d3Plugins/hexColor';

/**
 * Convert hex color to RGB components.
 *
 * Delegates to the shared, unit-tested `hexToRgbSafe` helper so that 3-digit
 * shorthand (`#000`, `#fff`), 4-digit shorthand (`#abcd`), 6-digit (`#aabbcc`)
 * and 8-digit RGBA (`#aabbccdd`) all parse. The previous inline implementation
 * matched 6-digit only, so Mermaid's `#000`/`#fff` output parsed as null and
 * bubbled up as a `COLOR-PARSE-FAIL` (contrast collapsed to 1), silently
 * disabling the text-contrast enhancement pass for shorthand-colored diagrams.
 */
export function hexToRgb(hex: string): { r: number; g: number; b: number } | null {
    return hexToRgbSafe(hex);
}

/**
 * G-19 / D-021 (D-129/D-130): parse an `hsl()` / `hsla()` colour string to an
 * RGB triple.
 *
 * The shared SVG contrast-remediation pass (`enhanceSVGVisibility` ->
 * `calculateContrastRatio` + `getOptimalTextColor`) previously understood only
 * hex and `rgb()`. Mermaid emits its THEME palette (journey bands, gitGraph
 * branch strokes, mindmap links, bar fills, quadrant fills) as `hsl(h, s%, l%)`
 * at render time, so an `hsl()` backdrop failed to parse: the ratio collapsed
 * to a degenerate `1` (COLOR-PARSE-FAIL) and `getOptimalTextColor` fell through
 * to its `#ffffff` default — a near-white label on a near-white theme fill in
 * LIGHT, and an unadapted foreground in DARK.
 *
 * Supports both comma-separated (`hsl(60, 80%, 85%)`) and CSS Level-4
 * whitespace-separated (`hsl(210 50% 25%)`) syntax; any alpha component is
 * ignored (solid fill). Returns `null` for anything that does not parse —
 * including a malformed component such as an upstream-mermaid `NaN` lightness
 * (`hsl(240, 100%, NaN%)`) — so the caller declines rather than fabricating a
 * colour.
 */
export function hslStringToRgb(color: string): { r: number; g: number; b: number } | null {
    if (!color) return null;
    const m = color.trim().match(/^hsla?\(([^)]+)\)$/i);
    if (!m) return null;
    // Split on commas, an alpha slash, or whitespace so both the legacy
    // comma syntax and the CSS Level-4 space syntax parse.
    const parts = m[1].split(/[,/]+|\s+/).map((s) => s.trim()).filter(Boolean);
    if (parts.length < 3) return null;
    const h = parseFloat(parts[0]);
    const s = parseFloat(parts[1]) / 100;
    const l = parseFloat(parts[2]) / 100;
    if ([h, s, l].some((n) => Number.isNaN(n))) return null;
    const hue = ((h % 360) + 360) % 360;
    const c = (1 - Math.abs(2 * l - 1)) * s;
    const x = c * (1 - Math.abs(((hue / 60) % 2) - 1));
    const mm = l - c / 2;
    let r = 0, g = 0, b = 0;
    if (hue < 60) [r, g, b] = [c, x, 0];
    else if (hue < 120) [r, g, b] = [x, c, 0];
    else if (hue < 180) [r, g, b] = [0, c, x];
    else if (hue < 240) [r, g, b] = [0, x, c];
    else if (hue < 300) [r, g, b] = [x, 0, c];
    else [r, g, b] = [c, 0, x];
    return {
        r: Math.round((r + mm) * 255),
        g: Math.round((g + mm) * 255),
        b: Math.round((b + mm) * 255),
    };
}

/**
 * Get luminance component for a color value (0-255)
 */
export function getLuminanceComponent(c: number): number {
    const normalized = c / 255;
    return normalized <= 0.03928
        ? normalized / 12.92
        : Math.pow((normalized + 0.055) / 1.055, 2.4);
}

/**
 * Calculate relative luminance of an RGB color
 * Returns value between 0 (darkest) and 1 (lightest)
 */
export function luminance(r: number, g: number, b: number): number {
    const rLum = getLuminanceComponent(r);
    const gLum = getLuminanceComponent(g);
    const bLum = getLuminanceComponent(b);
    return 0.2126 * rLum + 0.7152 * gLum + 0.0722 * bLum;
}

/**
 * Determine if a background color is light
 * Handles hex, rgb(), and named color formats
 */
export function isLightBackground(color: string): boolean {
    if (!color || color === 'transparent' || color === 'none') {
        return false;
    }

    // Parse color to RGB values
    let r = 0, g = 0, b = 0;

    // Handle hex format
    const hexMatch = color.match(/^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i);
    if (hexMatch) {
        r = parseInt(hexMatch[1], 16);
        g = parseInt(hexMatch[2], 16);
        b = parseInt(hexMatch[3], 16);
    }
    // Handle rgb() format
    else if (color.startsWith('rgb')) {
        const rgbMatch = color.match(/rgb\((\d+),\s*(\d+),\s*(\d+)\)/);
        if (rgbMatch) {
            r = parseInt(rgbMatch[1]);
            g = parseInt(rgbMatch[2]);
            b = parseInt(rgbMatch[3]);
        } else {
            return false;
        }
    }
    // Handle named colors
    else {
        const lightNamedColors = [
            'white', 'lightblue', 'lightgreen', 'lightyellow', 'lightgrey', 'lightgray', 'pink',
            'yellow', '#aed6f1', '#d4e6f1', '#d5f5e3', '#f5f5f5', '#e6e6e6', '#f0f0f0',
            '#ffffff', '#f8f9fa', '#e9ecef', '#dee2e6', '#ced4da', '#adb5bd'
        ];
        return lightNamedColors.some(c => c.toLowerCase() === color.toLowerCase());
    }

    // Calculate proper sRGB luminance
    const lum = luminance(r, g, b);

    // Use threshold where anything above 0.4 luminance is considered light
    return lum > 0.4;
}

/**
 * Get optimal text color (black or white) for a given background
 * Includes special handling for yellow and yellow-ish colors
 */
export function getOptimalTextColor(backgroundColor: string): string {
    const rgb = hexToRgb(backgroundColor);
    if (!rgb) {
        // If we can't parse as hex, try rgb() format
        const rgbMatch = backgroundColor.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/);
        if (rgbMatch) {
            const r = parseInt(rgbMatch[1]), g = parseInt(rgbMatch[2]), b = parseInt(rgbMatch[3]);
            return getOptimalTextColor(`#${r.toString(16).padStart(2, '0')}${g.toString(16).padStart(2, '0')}${b.toString(16).padStart(2, '0')}`);
        }
        // G-19 / D-021: try hsl()/hsla() — mermaid's theme palette (journey/
        // quadrant/gitGraph/mindmap/bar fills) is emitted this way at render time.
        const hslRgb = hslStringToRgb(backgroundColor);
        if (hslRgb) {
            return getOptimalTextColor(`#${hslRgb.r.toString(16).padStart(2, '0')}${hslRgb.g.toString(16).padStart(2, '0')}${hslRgb.b.toString(16).padStart(2, '0')}`);
        }
        return '#ffffff'; // Default to white for unparseable colors
    }

    // Calculate proper sRGB luminance
    const lum = luminance(rgb.r, rgb.g, rgb.b);
    
    // Special handling for yellow and light yellow (expanded to catch all variants)
    // Yellow has high R and G, with B significantly lower than both
    if (rgb.r > 180 && rgb.g > 180 && rgb.b < Math.min(rgb.r - 30, rgb.g - 30)) {
        return '#000000'; // Always use black on yellow/light yellow
    }
    
    // Special handling for light blue colors (including journey diagram blue #aed6f1)
    // These colors have high blue component and appear light, needing dark text
    if (rgb.b > 200 && rgb.r > 150 && rgb.g > 180) {
        return '#000000'; // Use black on light blue backgrounds
    }
    
    // Special handling for medium blue/cyan (high blue but appears light despite low luminance)
    // Blue coefficient in luminance is only 0.0722, so these colors appear darker than they look
    if (rgb.b > 180 && rgb.r > 100 && rgb.g > 100 && Math.abs(rgb.r - rgb.g) < 60) {
        return '#000000'; // Use black on light blue/cyan (baby blue)
    }
    
    // Special handling for grey colors (all channels similar, medium to high brightness)
    // Only use black on LIGHT greys (>160), otherwise use white
    if (Math.abs(rgb.r - rgb.g) < 30 && Math.abs(rgb.g - rgb.b) < 30) {
        return rgb.r > 160 ? '#000000' : '#ffffff';
    }
    
    // SIMPLE AGGRESSIVE RULE: If ANY channel is very bright, likely needs black text
    const maxChannel = Math.max(rgb.r, rgb.g, rgb.b);
    const minChannel = Math.min(rgb.r, rgb.g, rgb.b);
    if (minChannel > 100 && maxChannel > 180) {
        return '#000000'; // Light pastel colors need black text
    }
    
    // Use WCAG-based threshold as final fallback: luminance > 0.5 is considered light
    return lum > 0.5 ? '#000000' : '#ffffff';
}


/**
 * Calculate contrast ratio between two colors
 * Returns value >= 1 (1 = no contrast, 21 = maximum contrast)
 * WCAG AA requires 4.5:1 for normal text, 3:1 for large text
 * 
 * @param color1 - First color (hex or rgb)
 * @param color2 - Second color (hex or rgb)
 * @returns Contrast ratio between 1 and 21
 */
export function calculateContrastRatio(color1: string, color2: string): number {
    // Handle various color formats
    const parseColor = (color: string): { r: number; g: number; b: number } | null => {
        // Handle named colors
        const namedColors: Record<string, string> = {
            'white': '#ffffff',
            'black': '#000000',
            'red': '#ff0000',
            'green': '#008000',
            'blue': '#0000ff',
            'yellow': '#ffff00',
            'cyan': '#00ffff',
            'magenta': '#ff00ff',
            'gray': '#808080',
            'grey': '#808080',
            'transparent': '#ffffff',
            'none': '#ffffff'
        };
        
        // Convert named color to hex
        const normalizedColor = color.toLowerCase().trim();
        if (namedColors[normalizedColor]) {
            color = namedColors[normalizedColor];
        }
        
        // Try hex first
        const hexResult = hexToRgb(color);
        if (hexResult) return hexResult;

        // Try rgb() format
        const rgbMatch = color.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/);
        if (rgbMatch) {
            return {
                r: parseInt(rgbMatch[1]),
                g: parseInt(rgbMatch[2]),
                b: parseInt(rgbMatch[3])
            };
        }

        // G-19 / D-021: try hsl()/hsla() (mermaid theme palette at render time).
        // A malformed component (e.g. an upstream `NaN` lightness) parses to
        // null so the caller declines rather than fabricating a colour.
        const hslRgb = hslStringToRgb(color);
        if (hslRgb) return hslRgb;

        return null;
    };

    const rgb1 = parseColor(color1);
    const rgb2 = parseColor(color2);

    if (!rgb1 || !rgb2) {
        console.warn('🔍 COLOR-PARSE-FAIL:', {
            color1, color2, rgb1, rgb2
        });
        return 1;
    }

    const lum1 = luminance(rgb1.r, rgb1.g, rgb1.b);
    const lum2 = luminance(rgb2.r, rgb2.g, rgb2.b);

    const lighter = Math.max(lum1, lum2);
    const darker = Math.min(lum1, lum2);

    return (lighter + 0.05) / (darker + 0.05);
}

/**
 * Parse a solid colour string (hex, rgb()/rgba(), or a few common names) to RGB.
 * Used to average SVG gradient stops; returns null for anything unparseable.
 */
function paintToRgb(color: string): { r: number; g: number; b: number } | null {
    if (!color) return null;
    const c = color.trim();
    const hex = hexToRgb(c);
    if (hex) return hex;
    const m = c.match(/rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)/i);
    if (m) return { r: +m[1], g: +m[2], b: +m[3] };
    // G-19 / D-021: hsl()/hsla() gradient stops (mermaid palette) resolve too.
    const hsl = hslStringToRgb(c);
    if (hsl) return hsl;
    const named: Record<string, string> = {
        white: '#ffffff', black: '#000000', red: '#ff0000', green: '#008000',
        blue: '#0000ff', yellow: '#ffff00', cyan: '#00ffff', magenta: '#ff00ff',
        gray: '#808080', grey: '#808080',
    };
    if (named[c.toLowerCase()]) return hexToRgb(named[c.toLowerCase()]);
    return null;
}

/**
 * D-288 (mermaid gantt `fill: currentColor`): the CSS context keywords
 * `currentColor` and `inherit` are NOT colours the contrast math can parse.
 * When the headless renderer leaves them unresolved on a `<text>` fill they
 * reach `calculateContrastRatio` verbatim and collapse it to a degenerate `1`
 * (the recurring `COLOR-PARSE-FAIL {color1: currentColor, color2: #ffffff}`),
 * so every gantt task label is treated as invisible in a single pass and the
 * whole chart's text is force-recoloured. `currentColor` is defined to be the
 * element's computed `color`; `inherit` the inherited value. This helper
 * performs exactly that context resolution — mirroring how chartTheme/d2/
 * graphviz already decline these tokens — so the caller measures a real
 * colour instead of failing open.
 *
 * Returns:
 *   - null  when `token` is NOT a context keyword (caller keeps its own value);
 *   - the parseable `computedColor` when the keyword resolves to a real colour;
 *   - `fallback` when the keyword cannot be resolved to anything parseable.
 * Pure and theme-agnostic: identical logic runs in whatever theme was rendered.
 */
export function resolveContextColorToken(
    token: string | null | undefined,
    computedColor: string | null | undefined,
    fallback: string,
): string | null {
    if (!token) return null;
    const t = token.trim().toLowerCase();
    if (t !== 'currentcolor' && t !== 'inherit') return null;
    // currentColor / inherit both resolve to the element's computed `color`.
    const resolved = (computedColor || '').trim();
    // A computed value that is itself an unresolved keyword (or empty) is no
    // better than what we started with -> use the theme-appropriate fallback.
    if (resolved && resolved.toLowerCase() !== 'currentcolor'
        && resolved.toLowerCase() !== 'inherit') {
        return resolved;
    }
    return fallback;
}

/**
 * Resolve an SVG paint-server reference (`fill="url(#id)"`) to a representative
 * solid hex by averaging the referenced gradient's <stop> colours (D-053 /
 * graphviz-w1-13). Graphviz emits colorList / gradient fills this way; the
 * contrast math cannot parse a paint reference, so `findElementBackground`
 * previously handed `url(#...)` to `getOptimalTextColor`, which FAILS OPEN to
 * white and overrode the authored dark fontcolor (white-on-light-gradient).
 * Returns the input unchanged when it is not a url() reference, and null when
 * the reference cannot be resolved (caller then falls back to the page bg
 * rather than to white).
 */
export function resolveGradientPaint(value: string | null, context: Element | null): string | null {
    if (!value) return value;
    const ref = value.match(/url\(\s*["']?#([^"')\s]+)["']?\s*\)/);
    if (!ref) return value;
    const id = ref[1];
    let grad: Element | null = null;
    const root = context
        ? ((context as unknown as SVGGraphicsElement).ownerSVGElement
            || (typeof context.closest === 'function' ? context.closest('svg') : null))
        : null;
    if (root && typeof (root as Element).querySelector === 'function') {
        try { grad = (root as Element).querySelector(`[id="${id}"]`); } catch { grad = null; }
    }
    if (!grad && context && context.ownerDocument) {
        grad = context.ownerDocument.getElementById(id);
    }
    if (!grad) return null;
    const stops = grad.getElementsByTagName('stop');
    if (!stops || stops.length === 0) return null;
    let r = 0, g = 0, b = 0, n = 0;
    for (let i = 0; i < stops.length; i++) {
        const st = stops[i];
        let sc = st.getAttribute('stop-color');
        if (!sc) {
            const sm = (st.getAttribute('style') || '').match(/stop-color:\s*([^;]+)/i);
            if (sm) sc = sm[1];
        }
        const rgb = sc ? paintToRgb(sc) : null;
        if (rgb) { r += rgb.r; g += rgb.g; b += rgb.b; n++; }
    }
    if (n === 0) return null;
    const h = (x: number) => Math.round(x / n).toString(16).padStart(2, '0');
    return `#${h(r)}${h(g)}${h(b)}`;
}

/**
 * G-25c219 / D-100 — composite a shape fill over the page background by its
 * `fill-opacity` before it is used as a contrast backdrop.
 *
 * The drawio swimlane branch paints lane fills at `fillOpacity=20` (→
 * `fill-opacity="0.2"`), so the lane title actually sits on a 20% composite of
 * the fill over the themed canvas (e.g. #dae8fc@20% over the dark page ≈
 * #50586a), on which the plugin's white title is readable (~7:1). But if the
 * enhancer measures the label against the OPAQUE fill (#dae8fc), white reads
 * 1.3:1 and the net FLIPS it to black — black on the real composite is ~2.9:1,
 * invisible. Reading the shape's fill-opacity and compositing here keeps the
 * backdrop equal to what is actually painted, in whatever theme was rendered.
 *
 * A fully-opaque fill (no fill-opacity, or 1) is returned unchanged, so no
 * existing opaque-fill output is affected. An unparseable fill/bg is left as-is.
 */
function compositeShapeFillOverBg(fill: string, shape: Element, bgHex: string): string {
    if (!fill || fill === 'none' || fill.indexOf('url(') !== -1) return fill;
    // fill-opacity may be a presentation attribute or an inline/computed style.
    let raw = shape.getAttribute('fill-opacity');
    if (raw == null) {
        try {
            const cs = (shape as SVGElement).style?.fillOpacity
                || window.getComputedStyle(shape).fillOpacity;
            if (cs) raw = cs;
        } catch { /* jsdom / no computed style — fall through */ }
    }
    if (raw == null || raw === '') return fill;
    const a = parseFloat(raw);
    if (!isFinite(a) || a >= 1) return fill; // opaque → unchanged
    if (a <= 0) return bgHex;                // fully transparent → the canvas
    const fg = paintToRgb(fill);
    const bg = paintToRgb(bgHex);
    if (!fg || !bg) return fill;             // can't parse → leave as-is
    const blend = (f: number, b: number) => Math.round(f * a + b * (1 - a));
    const h = (x: number) => Math.max(0, Math.min(255, x)).toString(16).padStart(2, '0');
    return `#${h(blend(fg.r, bg.r))}${h(blend(fg.g, bg.g))}${h(blend(fg.b, bg.b))}`;
}

/**
 * Find background color for an SVG element by searching parents and siblings
 * Uses multiple strategies to detect the actual background color
 * 
 * @param element - The element to find background for
 * @param defaultBg - Fallback background color
 * @returns The detected background color or default
 */
export function findElementBackground(element: Element, defaultBg: string = '#ffffff'): string {
    let backgroundColor: string | null = null;

    // Strategy 0: Handle HTML elements inside SVG foreignObject
    // HTML elements (div, span) can't find SVG parent groups via closest('g')
    // because closest() doesn't cross namespace boundaries.
    // Walk up manually to find the foreignObject, then its SVG parent group.
    let ancestor: Element | null = element;
    while (ancestor && ancestor.tagName !== 'foreignObject') {
        ancestor = ancestor.parentElement;
    }
    if (ancestor && ancestor.tagName === 'foreignObject') {
        // Found foreignObject - look for sibling shapes in the SVG parent group
        const svgParent = ancestor.parentElement;
        // Walk up through parent groups (MaxGraph nests foreignObject inside
        // label groups that may not contain the shape rect directly)
        let searchParent = svgParent;
        for (let depth = 0; searchParent && depth < 4; depth++) {
            // Mermaid 11 applies `style X fill:...` / classDef fills as a
            // style="fill:... !important" attribute on the shape, not as a
            // fill attribute, and hexagon/diamond nodes are <polygon>s. Read
            // the computed fill so authored light fills are measured against
            // instead of falling through to the (dark) page background.
            const candidates = searchParent.querySelectorAll(
                'rect, ellipse, polygon, circle, path'
            );
            for (let i = 0; i < candidates.length; i++) {
                const bgShape = candidates[i];
                // Skip the label's own zero-size backing rect (no geometry).
                if (bgShape.tagName === 'rect' && !bgShape.getAttribute('width')) continue;
                const fillAttr = bgShape.getAttribute('fill');
                if (fillAttr === 'none') continue;
                let fill: string | null = null;
                try {
                    const computed = window.getComputedStyle(bgShape).fill;
                    if (computed && computed !== 'none' && computed !== 'rgba(0, 0, 0, 0)') {
                        fill = computed;
                    }
                } catch { /* fall through to attribute */ }
                if (!fill && fillAttr) fill = fillAttr;
                if (fill && fill.indexOf('url(') !== -1) {
                    fill = resolveGradientPaint(fill, bgShape);
                }
                if (fill && fill !== 'none') {
                    // D-100: honour fill-opacity so a 20% swimlane fill is
                    // measured as the composite it actually paints, not opaque.
                    return compositeShapeFillOverBg(fill, bgShape, defaultBg);
                }
            }
            searchParent = searchParent.parentElement;
        }
    }

    // Strategy 1: Check for fill attribute on parent elements (Graphviz nodes)
    // In Graphviz, text elements are inside <g> elements that have the fill color
    const parentGroup = element.closest('g');
    if (parentGroup) {
        // First, check if the parent group itself has a fill
        const parentFill = parentGroup.getAttribute('fill');
        if (parentFill && parentFill !== 'none') {
            console.log('Found parent group fill:', parentFill);
            return parentFill;
        }
        
        // Next, check for background shapes BEFORE the text element
        // These are rendered first and provide the background
        const backgroundShape = parentGroup.querySelector('rect, ellipse, polygon, circle, path[fill]:not([fill="none"])');
        if (backgroundShape) {
            const fill = backgroundShape.getAttribute('fill');
            const computedFill = window.getComputedStyle(backgroundShape).fill;
            backgroundColor = (computedFill && computedFill !== 'none' && computedFill !== 'rgb(0, 0, 0)')
                ? computedFill
                : fill;

            // D-053: a gradient/paint-server fill (url(#id)) cannot be used as a
            // contrast background; resolve it to the averaged gradient stops so
            // the enhancer measures against the real surface instead of failing
            // open to white text over the authored fontcolor. An unresolvable
            // reference becomes null -> we fall through to the page bg (not white).
            if (backgroundColor && backgroundColor.indexOf('url(') !== -1) {
                backgroundColor = resolveGradientPaint(backgroundColor, backgroundShape);
            }

            if (backgroundColor && backgroundColor !== 'none') {
                console.log('Found background shape fill:', backgroundColor);
                // D-100: composite by the shape's fill-opacity (see helper).
                return compositeShapeFillOverBg(backgroundColor, backgroundShape, defaultBg);
            }
        }
    }

    // Strategy 2: For Graphviz, check siblings for filled shapes at the same level
    if (parentGroup) {
        const siblings = parentGroup.querySelectorAll('ellipse[fill]:not([fill="none"]), polygon[fill]:not([fill="none"]), path[fill]:not([fill="none"])');
        if (siblings.length > 0) {
            const firstSiblingFill = siblings[0].getAttribute('fill');
            if (firstSiblingFill && firstSiblingFill !== 'none') {
                // D-053: resolve a gradient paint reference to a solid surface here too.
                const resolvedSibling = firstSiblingFill.indexOf('url(') !== -1
                    ? resolveGradientPaint(firstSiblingFill, siblings[0])
                    : firstSiblingFill;
                if (resolvedSibling && resolvedSibling !== 'none') {
                    console.log('Found sibling shape fill:', resolvedSibling);
                    return resolvedSibling;
                }
            }
        }
    }

    // Strategy 3: Check computed background from CSS
    if (!backgroundColor) {
        const computedBg = window.getComputedStyle(element).backgroundColor;
        if (computedBg && computedBg !== 'rgba(0, 0, 0, 0)' && computedBg !== 'transparent') {
            backgroundColor = computedBg;
        }
    }

    // Strategy 4: For legend items, use page background (text should contrast with page, not color box)
    if (!backgroundColor && parentGroup) {
        if (parentGroup.classList.contains('legend') ||
            parentGroup.closest('.legend') ||
            element.closest('.legend')) {
            return defaultBg;
        }
    }

    return backgroundColor || defaultBg;
}

/**
 * Universal SVG visibility enhancer
 * Works with ANY SVG diagram (Mermaid, Graphviz, DrawIO, Vega, etc.)
 * Fixes text, shapes, and lines to ensure proper contrast
 * 
 * @param svgElement - The SVG element to enhance
 * @param isDarkMode - Whether dark mode is active
 * @param options - Optional configuration
 * @returns Statistics about elements fixed
 */
export interface VisibilityEnhancerOptions {
    /** Minimum contrast ratio (default: 3.0 for accessibility) */
    minContrast?: number;
    /**
     * Minimum contrast ratio required for TEXT specifically (default: falls back
     * to minContrast). Text needs the WCAG 4.5:1 floor, not the 3:1 graphic
     * floor: a label sitting at ~3.1:1 on a mid-tone fill (e.g. a mermaid
     * mindmap ROOT circle, D-154 w1-11) is above minContrast so the generic
     * pass leaves it, yet it fails the text floor. Callers that render text on
     * arbitrary fills (mermaid) pass 4.5 here; shapes/lines keep minContrast.
     */
    textMinContrast?: number;
    /** Skip elements with these classes */
    skipClasses?: string[];
    /** Skip elements matching these selectors */
    skipSelectors?: string[];
    /** Debug logging */
    debug?: boolean;
}

export function enhanceSVGVisibility(
    svgElement: SVGElement,
    isDarkMode: boolean,
    options: VisibilityEnhancerOptions = {}
): { textFixed: number; shapesFixed: number; linesFixed: number } {
    const {
        minContrast = 3.0,
        textMinContrast,
        skipClasses = [],
        skipSelectors = [],
        debug = false
    } = options;

    // Text uses the WCAG 4.5:1 floor when the caller asks for it; otherwise it
    // inherits the graphic minContrast so existing callers are byte-unchanged.
    const textFloor = textMinContrast ?? minContrast;

    const log = debug ? console.log : () => { };
    const pageBg = isDarkMode ? '#2e3440' : '#ffffff';
    const defaultTextColor = isDarkMode ? '#eceff4' : '#333333';
    const defaultStrokeColor = isDarkMode ? '#88c0d0' : '#333333';

    // Resolve a readable text colour for a measured background: start from the
    // tuned heuristic, but if it fails to clear the text floor fall back to the
    // genuinely max-contrast of black/white (the heuristic can pick the LOWER
    // contrast option on mid-tone fills — e.g. black on a mid-blue mindmap root
    // at ~3.1:1 where white reaches ~6:1). Pure improvement: only ever raises
    // contrast, and identical logic runs in whatever theme was rendered.
    const resolveReadableText = (bg: string): string => {
        let c = getOptimalTextColor(bg);
        if (calculateContrastRatio(c, bg) < textFloor) {
            const cBlack = calculateContrastRatio('#000000', bg);
            const cWhite = calculateContrastRatio('#ffffff', bg);
            c = cWhite >= cBlack ? '#ffffff' : '#000000';
        }
        return c;
    };

    let textFixed = 0;
    let shapesFixed = 0;
    let linesFixed = 0;

    log('🔍 UNIVERSAL-SVG-FIX: Starting visibility enhancement');

    // FIX 1a: Check for foreignObject HTML text (Mermaid v10+)
    const foreignObjects = svgElement.querySelectorAll('foreignObject');
    foreignObjects.forEach((fo) => {
        const htmlElements = fo.querySelectorAll('div, span, p');
        htmlElements.forEach((htmlEl) => {
            const textContent = htmlEl.textContent?.trim();
            if (!textContent) return;

            // Only fix the innermost text-bearing element, not wrapper divs.
            // MaxGraph nests 3 divs inside foreignObject; fixing all of them
            // causes font rendering artifacts. Skip if this div has child divs
            // that also contain the same text (i.e., this is a wrapper).
            const childDivs = htmlEl.querySelectorAll('div, span, p');
            if (childDivs.length > 0) return;

            let backgroundColor = findElementBackground(htmlEl, pageBg);
            // G-8a093a / D-153: mermaid can emit a MALFORMED fill such as
            // `hsl(240, 100%, NaN%)` (e.g. the quadrantChart quadrant fill in
            // the light theme). That parses to null, so `calculateContrastRatio`
            // collapses to a degenerate 1 (which still trips the fix path) while
            // `getOptimalTextColor` blindly defaults to WHITE — repainting a
            // white label white and leaving it invisible on the light canvas.
            // When the resolved background is unparseable, fall back to the THEME
            // page background so the readable-text decision stays correct on
            // BOTH themes (black on #ffffff = 21:1; white on #2e3440 = 11.6:1).
            if (!paintToRgb(backgroundColor)) backgroundColor = pageBg;
            const optimalColor = getOptimalTextColor(backgroundColor);
            const currentStyle = window.getComputedStyle(htmlEl);
            const currentColor = currentStyle.color || defaultTextColor;

            const contrast = calculateContrastRatio(currentColor, backgroundColor);
            const textInvisible = currentColor === backgroundColor;

            log(`🔍 HTML-TEXT: "${textContent.substring(0, 30)}" contrast=${contrast.toFixed(2)} current=${currentColor} bg=${backgroundColor}`);

            if (contrast < textFloor || textInvisible) {
                const applied = resolveReadableText(backgroundColor);
                (htmlEl as HTMLElement).style.setProperty('color', applied, 'important');
                textFixed++;
                log(`🔧 HTML text fix: "${textContent.substring(0, 30)}" -> ${applied}`);
            }
        });
    });

    // FIX 1b: SVG text elements (older Mermaid, Graphviz, etc.)
    const textElements = svgElement.querySelectorAll('text');
    textElements.forEach((textEl) => {
        // Skip if in skip list
        if (skipClasses.some(cls => textEl.classList.contains(cls))) return;
        if (skipSelectors.some(sel => textEl.matches(sel))) return;

        const textContent = textEl.textContent?.trim();
        if (!textContent) return;

        let backgroundColor = findElementBackground(textEl, pageBg);
        // G-8a093a / D-153: guard against a MALFORMED element fill such as the
        // quadrantChart light-theme quadrant fill `hsl(240, 100%, NaN%)`. It
        // parses to null, collapsing calculateContrastRatio() to a degenerate 1
        // while getOptimalTextColor() defaults to WHITE — so a white quadrant
        // point label would be repainted white and stay invisible on the light
        // canvas (~1.04:1). Falling back to the THEME page background keeps the
        // readable-text decision correct on BOTH themes.
        if (!paintToRgb(backgroundColor)) backgroundColor = pageBg;
        const optimalColor = getOptimalTextColor(backgroundColor);
        
        // CRITICAL: Use computed style if no fill attribute (ER diagrams use CSS classes)
        const fillAttr = textEl.getAttribute('fill');
        let currentColor = fillAttr || window.getComputedStyle(textEl).fill || defaultTextColor;
        // D-288: mermaid gantt task labels carry `fill: currentColor`. When the
        // headless renderer leaves that keyword unresolved it reaches the
        // contrast math verbatim (COLOR-PARSE-FAIL -> degenerate ratio 1),
        // making every label look invisible. Resolve currentColor/inherit to
        // the element's computed `color` (its CSS meaning) before measuring.
        let computedColorProp: string | null = null;
        try { computedColorProp = window.getComputedStyle(textEl).color || null; } catch { /* no view */ }
        const ctxResolved = resolveContextColorToken(currentColor, computedColorProp, defaultTextColor);
        if (ctxResolved) currentColor = ctxResolved;
        
        // CRITICAL DEBUG: Log every text element analysis
        log(`🔍 TEXT-ANALYSIS: "${textContent.substring(0, 30)}"`, {
            currentColor,
            backgroundColor,
            optimalColor,
            pageBg,
            defaultTextColor,
            element: textEl.tagName,
            parentClass: textEl.parentElement?.getAttribute('class'),
            hasComputedStyle: !!window.getComputedStyle(textEl).fill
        });
        
        // Check if current color has sufficient contrast
        const contrast = calculateContrastRatio(currentColor, backgroundColor);
        
        // Check if text exactly matches background (truly invisible)
        const textInvisible = currentColor === backgroundColor;
        
        // CRITICAL DEBUG: Log contrast analysis before fix decision
        log(`🔍 CONTRAST-CHECK: "${textContent.substring(0, 30)}"`, {
            contrast: contrast.toFixed(2),
            minContrast,
            textInvisible,
            willFix: contrast < minContrast || textInvisible
        });
        
        if (contrast < textFloor || textInvisible) {
            const applied = resolveReadableText(backgroundColor);
            textEl.setAttribute('fill', applied);
            (textEl as SVGElement).style.setProperty('fill', applied, 'important');
            textFixed++;
            log(`🔧 Text fix: "${textContent.substring(0, 30)}" -> ${applied} (was ${currentColor}, contrast: ${contrast.toFixed(2)})`);
        }
    });

    // FIX 2: ALL shapes - ensure visible strokes ONLY (preserve fill colors)
    const shapes = svgElement.querySelectorAll('rect, ellipse, polygon, circle, path[fill]');
    shapes.forEach((shape) => {
        const fill = shape.getAttribute('fill');
        const stroke = shape.getAttribute('stroke');

        // Only fix strokes for shapes that have an explicit stroke or should have one
        // Don't add strokes to pie slices (path with fill but no stroke)
        const isPieSlice = shape.tagName === 'path' && 
                          fill && fill !== 'none' && 
                          !stroke;
        
        if (!isPieSlice) {
            // Fix invisible strokes (but don't add strokes where none exist)
            if (stroke && (stroke === 'none' || 
                (isDarkMode && (stroke === '#000000' || stroke === pageBg)) ||
                (!isDarkMode && (stroke === '#ffffff' || stroke === 'white')))) {
                shape.setAttribute('stroke', defaultStrokeColor);
                shapesFixed++;
                log(`🔧 Shape stroke fix: ${stroke} -> ${defaultStrokeColor}`);
            }
        }
        // REMOVED: Fill color modification - preserve user's color choices
    });

    // FIX 3: ALL lines and connection paths - ensure visible strokes
    // CRITICAL: Be VERY aggressive - catch ALL path elements that might be lines
    // This includes journey diagrams, flowcharts, sequence diagrams, etc.
    const lines = svgElement.querySelectorAll(
        'line, ' +
        'path[d]:not([fill]), ' +
        'path[fill="none"], ' +
        'path.path, ' +
        'path[class*="journey"], ' +
        'path[class*="line"], ' +
        'path[stroke]');  // ANY path with a stroke attribute
    
    log(`🔍 LINE-SCAN: Found ${lines.length} line elements to check`);
    
    lines.forEach((line) => {
        const stroke = line.getAttribute('stroke');
        const currentStrokeWidth = parseFloat(line.getAttribute('stroke-width') || '0');

        // Fix invisible strokes or strokes that match background
        const strokeInvisible = !stroke || stroke === 'none' ||
            stroke === pageBg ||
            (isDarkMode && (stroke === '#000000' || stroke === 'black' || stroke === '#2e3440')) ||
            (!isDarkMode && (stroke === '#ffffff' || stroke === 'white'));

        // CRITICAL: Also check for low-contrast grey strokes in dark mode
        // Grey (#808080, #999999, etc.) is invisible on dark grey backgrounds
        const isLowContrastGrey = isDarkMode && stroke && (
            stroke.toLowerCase() === '#808080' ||
            stroke.toLowerCase() === '#999999' ||
            stroke.toLowerCase() === '#666666' ||
            stroke.toLowerCase() === 'grey' ||
            stroke.toLowerCase() === 'gray'
        );

        if (strokeInvisible) {
            line.setAttribute('stroke', defaultStrokeColor);
            line.setAttribute('stroke-width', '2');
            linesFixed++;
            log(`🔧 Line fix: invisible stroke -> ${defaultStrokeColor}`);
        } else if (isLowContrastGrey) {
            // Fix low-contrast grey lines in dark mode
            line.setAttribute('stroke', defaultStrokeColor);
            (line as SVGElement).style.setProperty('stroke', defaultStrokeColor, 'important');
            if (currentStrokeWidth < 1.5) {
                line.setAttribute('stroke-width', '2');
            }
            linesFixed++;
            log(`🔧 Line fix: low-contrast grey (${stroke}) -> ${defaultStrokeColor}`);
        } else if (
            stroke &&
            !stroke.startsWith('url(') &&
            calculateContrastRatio(stroke, pageBg) < minContrast
        ) {
            // D-153 / w1-11: a connector stroke that IS a real colour but sits
            // below the 3:1 graphical floor against the render canvas. mermaid's
            // mindmap draws its link ribbons in the per-branch pastel palette
            // (e.g. #ffff99 / #ccff99 at ~1.05-1.15:1 on white); those are neither
            // "invisible" nor grey, so the branches above miss them and the
            // ribbons render near-invisible in the LIGHT theme. `lineColor` in
            // themeVariables does not reach mindmap edges, so the repair must be
            // post-render. Measure against the ACTUAL page background and, when a
            // stroke fails the floor, repaint it with the theme-resolved
            // defaultStrokeColor (#333333 on white = 12.63:1; #88c0d0 on #2e3440
            // = ~7:1) — a pure contrast RAISE that is correct on both themes, so
            // a legible edge is never touched and dark is unaffected (its pastel
            // ribbons already clear the floor on the dark canvas).
            line.setAttribute('stroke', defaultStrokeColor);
            (line as SVGElement).style.setProperty('stroke', defaultStrokeColor, 'important');
            if (currentStrokeWidth < 1.5) {
                line.setAttribute('stroke-width', '1.5');
            }
            linesFixed++;
            log(`🔧 Line fix: below-floor stroke (${stroke} on ${pageBg}) -> ${defaultStrokeColor}`);
        } else if (currentStrokeWidth < 0.5) {
            // Stroke exists but is too thin to see
            line.setAttribute('stroke-width', '1.5');
            linesFixed++;
            log(`🔧 Line fix: stroke too thin -> 1.5px`);
        }
    });

    log(`🔍 UNIVERSAL-SVG-FIX: Enhanced ${textFixed} text, ${shapesFixed} shapes, ${linesFixed} lines`);

    return { textFixed, shapesFixed, linesFixed };
}
