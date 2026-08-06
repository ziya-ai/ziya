/**
 * Dark-mode recolouring for server-rendered LaTeX (dvisvgm) SVG output.
 *
 * WHY THIS IS NOT enhanceSVGVisibility
 * ------------------------------------
 * The shared helper inspects each element's OWN fill/stroke attributes.  That
 * is the wrong model for dvisvgm output, verified by probing real renders:
 *
 *   - <path> elements carry NO stroke attribute and only fill='none'.
 *   - Colour lives on ancestor <g fill='#000' stroke='#000'> elements.
 *   - <text> elements carry no fill at all; they are styled by CSS class.
 *
 * So the helper never saw the black ink (measured contrast of #000 on the
 * #1f1f1f diagram background: 1.27:1 -- invisible), and where it did fire it
 * did harm: it forced stroke-width="2" onto every path, destroying the 0.4pt
 * and 0.8pt hairlines TeX chose deliberately, and stamped a stroke onto
 * filled arrowhead paths that are meant to have none.
 *
 * Remapping colour ATTRIBUTES wherever they appear -- including on <g> -- fixes
 * the whole tree in one pass through inheritance, and touches no geometry.
 */

/** Diagram background, matching D3Renderer's container. */
const DARK_BG = '#1f1f1f';

/**
 * Ink for TeX's default black.  13.21:1 on DARK_BG, mirroring the 19.73:1 that
 * black achieves on the light background -- legible without the harshness of
 * pure white.
 */
const DARK_INK = '#e6e6e6';

/** Minimum contrast for a non-black authored colour.  WCAG graphics floor. */
const MIN_CONTRAST = 3.0;

/** Values meaning "TeX default black", which becomes DARK_INK. */
const BLACK = new Set(['#000', '#000000', 'black', 'rgb(0,0,0)']);

function parseHex(value: string): [number, number, number] | null {
    const m = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(value.trim());
    if (!m) return null;
    let h = m[1];
    if (h.length === 3) h = h.split('').map((c) => c + c).join('');
    return [
        parseInt(h.slice(0, 2), 16),
        parseInt(h.slice(2, 4), 16),
        parseInt(h.slice(4, 6), 16),
    ];
}

const toHex = (rgb: [number, number, number]): string =>
    '#' + rgb.map((c) => Math.max(0, Math.min(255, Math.round(c)))
        .toString(16).padStart(2, '0')).join('');

/** WCAG relative luminance. */
function luminance([r, g, b]: [number, number, number]): number {
    const f = (c: number) => {
        const s = c / 255;
        return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
    };
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
}

function contrast(a: [number, number, number], b: [number, number, number]): number {
    const la = luminance(a);
    const lb = luminance(b);
    return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
}

function rgbToHsl([r, g, b]: [number, number, number]): [number, number, number] {
    const rn = r / 255;
    const gn = g / 255;
    const bn = b / 255;
    const max = Math.max(rn, gn, bn);
    const min = Math.min(rn, gn, bn);
    const l = (max + min) / 2;
    if (max === min) return [0, 0, l];
    const d = max - min;
    const s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
    let h: number;
    if (max === rn) h = ((gn - bn) / d + (gn < bn ? 6 : 0)) / 6;
    else if (max === gn) h = ((bn - rn) / d + 2) / 6;
    else h = ((rn - gn) / d + 4) / 6;
    return [h, s, l];
}

function hslToRgb([h, s, l]: [number, number, number]): [number, number, number] {
    if (s === 0) {
        const v = l * 255;
        return [v, v, v];
    }
    const q = l < 0.5 ? l * (1 + s) : l + s - l * s;
    const p = 2 * l - q;
    const chan = (t: number): number => {
        let x = t;
        if (x < 0) x += 1;
        if (x > 1) x -= 1;
        if (x < 1 / 6) return p + (q - p) * 6 * x;
        if (x < 1 / 2) return q;
        if (x < 2 / 3) return p + (q - p) * (2 / 3 - x) * 6;
        return p;
    };
    return [chan(h + 1 / 3) * 255, chan(h) * 255, chan(h - 1 / 3) * 255];
}

/**
 * Raise a colour's lightness until it clears MIN_CONTRAST on DARK_BG, holding
 * hue and saturation fixed.
 *
 * Hue preservation is the point: an author who wrote \draw[red] means red, and
 * flattening every colour to one ink would discard information the diagram is
 * using to distinguish signals.  Verified hue-stable -- pure blue #00f (1.92:1,
 * failing) becomes #5252ff (3.15:1) at an unchanged 240 degrees.
 */
function ensureContrast(rgb: [number, number, number]): [number, number, number] {
    const bg = parseHex(DARK_BG) as [number, number, number];
    if (contrast(rgb, bg) >= MIN_CONTRAST) return rgb;
    const [h, s, l] = rgbToHsl(rgb);
    for (let step = 1; step <= 100; step += 1) {
        const cand = hslToRgb([h, s, Math.min(1, l + step * 0.01)]);
        if (contrast(cand, bg) >= MIN_CONTRAST) return cand;
    }
    return parseHex(DARK_INK) as [number, number, number];
}

/** Map one attribute value, or null to leave it untouched. */
function mapColor(value: string): string | null {
    const v = value.trim().toLowerCase();
    // "none" is load-bearing: dvisvgm marks stroke-only paths fill='none'.
    // Recolouring it would flood every outlined glyph and circuit body.
    if (!v || v === 'none' || v === 'transparent' || v.startsWith('url(')) return null;
    if (BLACK.has(v)) return DARK_INK;
    const rgb = parseHex(v);
    if (!rgb) return null;              // named/unparseable colour: leave alone
    const fixed = ensureContrast(rgb);
    return fixed === rgb ? null : toHex(fixed);
}

/**
 * Recolour a dvisvgm SVG for dark mode in place.  No-op when isDarkMode is
 * false, so a light-theme render keeps TeX's own black.
 *
 * Deliberately touches ONLY fill/stroke attribute values -- never
 * stroke-width, never geometry -- because TeX's hairline weights are part of
 * the engraving and the previous implementation's stroke-width="2" override is
 * exactly what smeared the output.
 */
export function applyLatexDarkTheme(
    svgEl: SVGElement | null,
    isDarkMode: boolean,
): { remapped: number } {
    if (!svgEl || !isDarkMode) return { remapped: 0 };
    let remapped = 0;

    // Root-level default so anything inheriting (notably <text>, which
    // dvisvgm styles by CSS class with no fill of its own) picks up the ink.
    svgEl.setAttribute('fill', DARK_INK);
    svgEl.setAttribute('stroke', DARK_INK);

    for (const el of Array.from(svgEl.querySelectorAll('*'))) {
        // <glyph>/<font-face> live in <defs> and describe outlines, not ink.
        const tag = el.tagName.toLowerCase();
        if (tag === 'glyph' || tag === 'font' || tag === 'font-face') continue;
        for (const attr of ['fill', 'stroke'] as const) {
            const current = el.getAttribute(attr);
            if (!current) continue;
            const next = mapColor(current);
            if (next) {
                el.setAttribute(attr, next);
                remapped += 1;
            }
        }
    }
    return { remapped };
}

/** CSS reference pixels per PostScript point.  dvisvgm emits pt. */
const PX_PER_PT = 96 / 72;

/** Absolute-length units that may appear on the root element. */
const PT_PER_UNIT: Record<string, number> = {
    pt: 1, px: 72 / 96, in: 72, cm: 72 / 2.54, mm: 72 / 25.4, pc: 12,
};

/** Parse an SVG length to points, or null when relative/unparseable. */
function toPoints(value: string | null): number | null {
    if (!value) return null;
    const m = /^\s*(-?[\d.]+)\s*([a-z%]*)\s*$/i.exec(value);
    if (!m) return null;
    const n = parseFloat(m[1]);
    if (!Number.isFinite(n) || n <= 0) return null;
    // A bare number is px, per the SVG spec.
    const unit = (m[2] || 'px').toLowerCase();
    // A percentage is already container-relative, so there is no intrinsic
    // size to recover; such an element is left alone rather than guessed at.
    const factor = PT_PER_UNIT[unit];
    return factor === undefined ? null : n * factor;
}

/**
 * Give a dvisvgm SVG its natural on-screen size, bounded by its container.
 *
 * The defect this replaces: mount() removed BOTH `width` and `height`, keeping
 * only the viewBox.  An SVG with no width and a viewBox defaults to
 * `width: 100%`, so every diagram was stretched to the full chat column
 * whatever its real size.  Measured against an ~820px column:
 *
 *     benzene ring            52.2 x 60.2 pt  ->  70 x 80 px  =  11.8x upscale
 *     EAS reaction scheme    185.3 x 63.4 pt  -> 247 x 85 px  =   3.3x
 *     Fischer esterification 443   x 54   pt  -> 591 x 72 px  =   1.4x
 *
 * A single small structure is the worst case, which is why the symptom read as
 * "renders as a huge image" rather than as mild over-scaling.
 *
 * Keeping dvisvgm's own `width="52.2pt"` is not the fix either: an absolute
 * width overflows a narrow viewport, which is presumably what the original
 * removal was guarding against.  So the intrinsic size becomes an UPPER BOUND
 * -- natural width, `max-width:100%` to shrink, `height:auto` to hold the
 * aspect ratio while shrinking.
 *
 * Touches only the root element's presentation, never inner geometry and never
 * stroke-width: TeX's hairline weights are part of the engraving, and the
 * previous code path's `stroke-width="2"` override over an inherited 0.4 was a
 * 5x thickening stacked on top of the upscale.
 */
export function sizeLatexSvg(svgEl: SVGElement | null): { widthPx: number | null } {
    if (!svgEl) return { widthPx: null };

    const widthPt = toPoints(svgEl.getAttribute('width'));
    const heightPt = toPoints(svgEl.getAttribute('height'));

    // The viewBox carries the coordinate system and is what allows the element
    // to scale at all; without one, max-width would clip rather than shrink.
    if (!svgEl.getAttribute('viewBox') && widthPt && heightPt) {
        svgEl.setAttribute('viewBox', `0 0 ${widthPt} ${heightPt}`);
    }

    // Attributes are dropped so the CSS below is authoritative -- otherwise a
    // width="52.2pt" attribute and a width:70px style disagree.
    svgEl.removeAttribute('width');
    svgEl.removeAttribute('height');
    svgEl.setAttribute('preserveAspectRatio', 'xMidYMid meet');

    const style = (svgEl as unknown as HTMLElement).style;
    style.maxWidth = '100%';
    style.height = 'auto';

    if (widthPt === null) {
        // No intrinsic width to honour.  Fall back to the previous behaviour
        // rather than inventing a size.
        style.width = '100%';
        return { widthPx: null };
    }

    const widthPx = Math.round(widthPt * PX_PER_PT);
    style.width = `${widthPx}px`;
    return { widthPx };
}
