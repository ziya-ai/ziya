/**
 * inlineStyleContrast — theme-aware contrast remediation for author-hardcoded
 * inline HTML styles in chat-message content (consolidated-backlog D-019,
 * signature `author-hardcoded-color-not-theme-normalized`).
 *
 * WHY.  A chat message can contain raw HTML with inline `style="color:…"` /
 * `style="background:…"` / `style="opacity:…"` declarations that bake a colour
 * chosen for ONE page (e.g. `#666666` muted text tuned for a white surface, or
 * a pale `#eef6fb` ramp).  The live chat renderer passes that HTML straight to
 * `dangerouslySetInnerHTML` with no theme awareness, so a light-tuned colour is
 * a ghost on the dark surface (`#aaaaaa` text = 2.32:1 on #ffffff) and a
 * dark-tuned or alpha colour composites below the floor (`opacity:0.35` text =
 * 1.98:1 on #ffffff, `rgba(120,120,120,0.5)` = 1.85–1.90:1 on either surface).
 * The mermaid `%%{init}%%` half of the same signature is handled separately by
 * `mermaidEnhancer.remediateInitThemeVariableContrast`; this module covers the
 * INLINE-HTML half.
 *
 * The correct shape for a theme fix (see `blockquoteTheme.ts`) is to resolve
 * the colour FROM the active theme rather than swap one constant for another.
 * Here we measure each author colour against the surface it is ACTUALLY shown
 * on — the element's own opaque background if it sets one (theme-independent),
 * otherwise the active theme's chat surface — and only when that pair fails the
 * WCAG-AA text floor do we replace the text colour with the black/white that
 * reads best on that background.  A colour that already clears the floor is
 * left byte-for-byte unchanged, which is what keeps the OTHER theme correct:
 * `#666666` is repaired on the dark surface (2.64:1 -> white 15.13:1) but
 * untouched on the light surface where it already reads (5.74:1).
 *
 * Surfaces match the existing D-011 blockquote fix: light #ffffff, dark #262626.
 */

import {
    calculateContrastRatio,
    getOptimalTextColor,
    hexToRgb,
    hslStringToRgb,
} from './colorUtils';

// The chat message surface each theme renders on (kept in step with
// blockquoteTheme.ts so contrast reasoning is consistent across the renderer).
const LIGHT_SURFACE = '#ffffff';
const DARK_SURFACE = '#262626';

// WCAG-AA normal-text floor. Below this a hardcoded colour is a legibility bug.
const TEXT_FLOOR = 4.5;
// Text dimmed below this by `opacity` blends into the surface; restore it.
const OPACITY_FLOOR = 0.7;

export function chatMessageSurface(isDarkMode: boolean): string {
    return isDarkMode ? DARK_SURFACE : LIGHT_SURFACE;
}

interface RGB { r: number; g: number; b: number; }

const NAMED: Record<string, string> = {
    white: '#ffffff', black: '#000000', red: '#ff0000', green: '#008000',
    blue: '#0000ff', yellow: '#ffff00', cyan: '#00ffff', magenta: '#ff00ff',
    gray: '#808080', grey: '#808080',
};

/**
 * Parse a colour string to an {rgb, a} pair, understanding hex (3/4/6/8-digit),
 * rgb()/rgba(), hsl()/hsla() and a few named colours. Returns null for anything
 * unresolvable (a `var(--x)` token, `currentColor`, a gradient) so the caller
 * DECLINES rather than fabricating a colour.
 */
function toRgba(color: string): { rgb: RGB; a: number } | null {
    if (!color) return null;
    const c0 = color.trim().toLowerCase();
    if (!c0 || c0 === 'transparent') return c0 === 'transparent'
        ? { rgb: { r: 0, g: 0, b: 0 }, a: 0 } : null;
    const c = NAMED[c0] || c0;

    const m8 = c.match(/^#([0-9a-f]{8})$/i);
    if (m8) {
        const h = m8[1];
        return {
            rgb: { r: parseInt(h.slice(0, 2), 16), g: parseInt(h.slice(2, 4), 16), b: parseInt(h.slice(4, 6), 16) },
            a: parseInt(h.slice(6, 8), 16) / 255,
        };
    }
    const m4 = c.match(/^#([0-9a-f]{4})$/i);
    if (m4) {
        const h = m4[1];
        const e = (x: string) => parseInt(x + x, 16);
        return { rgb: { r: e(h[0]), g: e(h[1]), b: e(h[2]) }, a: e(h[3]) / 255 };
    }
    const hex = hexToRgb(c);
    if (hex) return { rgb: hex, a: 1 };

    const mr = c.match(/^rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*(?:,\s*([\d.]+)\s*)?\)$/i);
    if (mr) {
        return {
            rgb: { r: +mr[1], g: +mr[2], b: +mr[3] },
            a: mr[4] !== undefined ? +mr[4] : 1,
        };
    }
    const hsl = hslStringToRgb(c);
    if (hsl) return { rgb: hsl, a: 1 };
    return null;
}

function over(fg: RGB, a: number, bg: RGB): RGB {
    const clamp = (x: number) => Math.max(0, Math.min(255, Math.round(x)));
    return {
        r: clamp(fg.r * a + bg.r * (1 - a)),
        g: clamp(fg.g * a + bg.g * (1 - a)),
        b: clamp(fg.b * a + bg.b * (1 - a)),
    };
}

function toHex(c: RGB): string {
    const h = (x: number) => Math.max(0, Math.min(255, x)).toString(16).padStart(2, '0');
    return `#${h(c.r)}${h(c.g)}${h(c.b)}`;
}

/**
 * Resolve a possibly-translucent colour to the solid hex it composites to over
 * `bgHex`, folding in an extra element `opacity` multiplier. Returns null when
 * the colour cannot be parsed (caller declines).
 */
function resolveSolid(color: string, bgHex: string, opacity = 1): string | null {
    const c = toRgba(color);
    if (!c) return null;
    const bg = hexToRgb(bgHex) || { r: 255, g: 255, b: 255 };
    return toHex(over(c.rgb, c.a * opacity, bg));
}

function styleMap(el: Element): Record<string, string> {
    const s = el.getAttribute('style') || '';
    const out: Record<string, string> = {};
    s.split(';').forEach((decl) => {
        const i = decl.indexOf(':');
        if (i > 0) {
            const k = decl.slice(0, i).trim().toLowerCase();
            const v = decl.slice(i + 1).trim();
            if (k) out[k] = v;
        }
    });
    return out;
}

function serializeStyle(sm: Record<string, string>): string {
    return Object.entries(sm).map(([k, v]) => `${k}: ${v}`).join('; ');
}

/**
 * Extract a solid-colour token from a `background` / `background-color` value,
 * approximating a gradient by its first colour stop. Returns null for values
 * with no resolvable colour (e.g. `url(...)`), so the surface shows through.
 */
function backgroundColorOf(sm: Record<string, string>): string | null {
    const v = sm['background-color'] || sm['background'] || null;
    if (!v) return null;
    const m = v.match(/#[0-9a-fA-F]{3,8}|rgba?\([^)]*\)|hsla?\([^)]*\)|\b(?:white|black|gray|grey)\b/);
    return m ? m[0] : null;
}

/**
 * The solid surface the element's text is actually shown on: its own opaque
 * background if it sets one, otherwise the nearest ancestor background, folding
 * translucent layers over the theme surface at the bottom.
 */
function effectiveBg(el: Element, container: Element, surface: string): string {
    const chain: Element[] = [];
    let cur: Element | null = el;
    while (cur && cur !== container) {
        chain.push(cur);
        cur = cur.parentElement;
    }
    chain.reverse(); // outermost ancestor first, the element itself last
    let bg = surface;
    for (const node of chain) {
        const raw = backgroundColorOf(styleMap(node));
        if (raw) {
            const solid = resolveSolid(raw, bg, 1);
            if (solid) bg = solid;
        }
    }
    return bg;
}

function hasDirectText(el: Element): boolean {
    for (let i = 0; i < el.childNodes.length; i++) {
        const n = el.childNodes[i];
        if (n.nodeType === 3 /* text */ && (n.textContent || '').trim()) return true;
    }
    return false;
}

function remediateElement(el: Element, container: Element, surface: string): boolean {
    const sm = styleMap(el);
    const hasColor = 'color' in sm;
    const hasOpacity = 'opacity' in sm;
    if (!hasColor && !hasOpacity) return false;

    const opacity = hasOpacity ? parseFloat(sm['opacity']) : 1;
    const bgHex = effectiveBg(el, container, surface);
    let changed = false;

    if (hasColor) {
        const solidText = resolveSolid(sm['color'], bgHex, Number.isNaN(opacity) ? 1 : opacity);
        // Decline when unresolvable (var()/currentColor) — leave the author value.
        if (solidText && calculateContrastRatio(solidText, bgHex) < TEXT_FLOOR) {
            sm['color'] = getOptimalTextColor(bgHex);
            if (!Number.isNaN(opacity) && opacity < 1) sm['opacity'] = '1';
            changed = true;
        }
    } else if (hasOpacity && !Number.isNaN(opacity) && opacity < OPACITY_FLOOR && hasDirectText(el)) {
        // Low-opacity text with an inherited (theme) colour blends into the
        // surface; restore full opacity so the theme colour reads at strength.
        sm['opacity'] = '1';
        changed = true;
    }

    if (changed) el.setAttribute('style', serializeStyle(sm));
    return changed;
}

/**
 * Rewrite inline-style colours in a chat-message HTML fragment that fail the
 * WCAG-AA text floor against the surface they are shown on, resolving the
 * replacement from the active theme. Idempotent (repaired colours clear the
 * floor and pass through unchanged); a no-op when the fragment has no inline
 * `style` or when no DOM is available.
 */
export function remediateInlineStyleContrast(html: string, isDarkMode: boolean): string {
    if (!html || html.indexOf('style') === -1) return html;
    if (typeof document === 'undefined') return html;
    const surface = chatMessageSurface(isDarkMode);
    const container = document.createElement('div');
    container.innerHTML = html;
    const styled = container.querySelectorAll('[style]');
    let anyChanged = false;
    for (let i = 0; i < styled.length; i++) {
        try {
            if (remediateElement(styled[i], container, surface)) anyChanged = true;
        } catch {
            /* per-element: never let one malformed style abort the render */
        }
    }
    // Preserve the input byte-for-byte when nothing needed repair, so a spec
    // whose colours already read is untouched (and the DOM round-trip cannot
    // reformat it).
    return anyChanged ? container.innerHTML : html;
}

export default remediateInlineStyleContrast;
