/**
 * WaveDrom (digital timing diagram) support utilities.
 *
 * Pure functions only — no DOM and no wavedrom import — so WaveJSON
 * parsing, spec validation, skin selection and the SVG style-scoping pass
 * are unit-testable in Node without the rendering dependency installed.
 * The plugin wrapper (plugins/d3/wavedromPlugin.ts) owns the actual
 * wavedrom render and mounting — the same split the packet and railroad
 * plugins use.
 */
import JSON5 from 'json5';

/** Remove a stray markdown fence around the body. */
function stripFence(text: string): string {
    const m = text.trim().match(/^```[a-zA-Z0-9_-]*\s*\n([\s\S]*?)\n?```\s*$/);
    return m ? m[1] : text;
}

/**
 * Parse WaveJSON.  WaveJSON is JSON5, not JSON: every example in the
 * WaveDrom docs uses unquoted keys and single-quoted strings
 * ({ signal: [{ name: 'clk', wave: 'p...' }] }), and models reproduce that
 * style faithfully, so vanilla JSON.parse would reject the COMMON case.
 * Returns undefined — never throws — for unusable input, so streaming
 * callers can use it as an is-it-complete probe.
 */
export function parseWaveJson(text: string): any | undefined {
    if (typeof text !== 'string') return undefined;
    try {
        return JSON5.parse(stripFence(text));
    } catch {
        return undefined;
    }
}

/**
 * Validate the top-level shape.  Returns a model-teachable error message,
 * or null when the spec is renderable.  Deliberately light: WaveDrom itself
 * is lenient about lane contents, and over-validating here would reject
 * specs the engine accepts.
 */
export function validateWaveDromSpec(raw: any): string | null {
    if (raw == null) return 'empty WaveJSON spec';
    if (typeof raw !== 'object' || Array.isArray(raw)) {
        return 'WaveJSON must be an object with a top-level "signal" '
            + '(timing), "reg" (bit field), or "assign" (logic) key';
    }
    if (raw.signal !== undefined) {
        if (!Array.isArray(raw.signal)) return '"signal" must be an array of lanes';
        return null;
    }
    if (raw.reg !== undefined) {
        if (!Array.isArray(raw.reg)) return '"reg" must be an array of fields';
        return null;
    }
    if (raw.assign !== undefined) {
        if (!Array.isArray(raw.assign)) return '"assign" must be an array';
        return null;
    }
    // The error is model-facing feedback: an LLM that wrote "signals" must
    // see its own key echoed to correct it on the next attempt.
    return 'WaveJSON needs a top-level "signal", "reg", or "assign" key '
        + `(got: ${Object.keys(raw).join(', ') || 'none'})`;
}

/**
 * Select the skin: an explicit config.skin the author chose wins when it
 * exists in the available set; otherwise the chat theme decides ('dark' is
 * a skin WaveDrom ships, so dark mode needs no post-hoc recoloring).
 * Returns a new object — the caller's spec is never mutated.
 */
export function applySkin(source: any, isDark: boolean,
                          skins: Record<string, unknown>): any {
    const requested = source?.config?.skin;
    const skin = (typeof requested === 'string' && skins[requested] !== undefined)
        ? requested
        : (isDark ? 'dark' : 'default');
    return { ...source, config: { ...source?.config, skin } };
}

/**
 * Rewrite one CSS rule list for the scope, recording every class name the
 * sheet targets.
 *
 * Class-bearing selectors are scoped by RENAMING the class token
 * (.s1 -> .<scopeId>-s1) rather than by prefixing an ancestor id.  That is
 * load-bearing, not stylistic: WaveDrom draws every waveform shape as a
 * <use> instance of a symbol in the skin's <defs>, and a browser does NOT
 * match ancestor-dependent selectors against use-shadow-tree content.  An
 * `#id .s1` rule therefore styles nothing inside a <use>, leaving every
 * lane shape at the SVG default of fill:black/stroke:none — a solid black
 * blob where the waveform should be.  A bare single-class selector DOES
 * match across that boundary, so the token is made unique instead: the rule
 * stays ancestor-free AND cannot reach another diagram.
 *
 * Selectors with no class (the skins' bare `text`) keep the ancestor-id
 * prefix — the shipped skins put no <text> inside <defs>, so those rules
 * never cross a shadow boundary.
 */
function scopeCss(css: string, scopeId: string, classNames: Set<string>): string {
    return css.replace(/([^{}]+)(\{)/g, (_m, selectors: string, brace: string) => {
        const scoped = selectors.split(',').map(s => {
            const sel = s.trim();
            // @-rules pass through untouched (the shipped skins have none;
            // this is defensive, not load-bearing).
            if (!sel || sel.startsWith('@')) return s;
            if (/\.[A-Za-z_-][\w-]*/.test(sel)) {
                return sel.replace(/\.([A-Za-z_-][\w-]*)/g, (_c, name: string) => {
                    classNames.add(name);
                    return `.${scopeId}-${name}`;
                });
            }
            return `#${scopeId} ${sel}`;
        }).join(', ');
        return scoped + brace;
    });
}

/**
 * Apply scopeCss's class renaming to the markup's class attributes.
 *
 * Only tokens the stylesheet actually targets are renamed, so identity
 * classes the page may key on (WaveDrom's own `class="WaveDrom"`, which no
 * skin rule targets) survive untouched.
 */
function renameClassAttrs(svg: string, scopeId: string,
                          classNames: Set<string>): string {
    if (classNames.size === 0) return svg;
    return svg.replace(/(\sclass=)(["'])([^"']*)\2/g,
        (_m, prefix: string, quote: string, value: string) => {
            const mapped = value.trim().split(/\s+/)
                .map(t => (classNames.has(t) ? `${scopeId}-${t}` : t))
                .join(' ');
            return `${prefix}${quote}${mapped}${quote}`;
        });
}

/**
 * Scope a WaveDrom SVG's embedded stylesheet to the diagram itself.
 *
 * WaveDrom's skin embeds a <style> element with BARE selectors (text{...},
 * .s1{...}), and <style> inside inline SVG is NOT scoped in HTML — the
 * rules are document-global, so unscoped they restyle every other SVG on
 * the page (mermaid labels, vega axes, other diagrams' text).  This gives
 * the root <svg> a unique id, prefixes class-free selectors with it, and
 * uniquely renames the class selectors (see scopeCss for why the two are
 * treated differently).  Inline styling (the railroad engine's approach) is
 * not available here because the skin, not us, authors the CSS.
 */
export function scopeSvgStyles(svg: string, scopeId: string): string {
    if (typeof svg !== 'string') return svg;
    let out = svg;
    const root = out.match(/<svg\b[^>]*>/);
    if (root && root.index !== undefined) {
        let tag = root[0];
        tag = /\sid="[^"]*"/.test(tag)
            ? tag.replace(/\sid="[^"]*"/, ` id="${scopeId}"`)
            : tag.replace('<svg', `<svg id="${scopeId}"`);
        out = out.slice(0, root.index) + tag
            + out.slice(root.index + root[0].length);
    }
    const classNames = new Set<string>();
    out = out.replace(/(<style\b[^>]*>)([\s\S]*?)(<\/style>)/g,
        (_m, open, css, close) => open + scopeCss(css, scopeId, classNames) + close);
    return renameClassAttrs(out, scopeId, classNames);
}
/**
 * The surface WaveDrom draws onto in dark mode.  Matches
 * `.dark .d3-container` so the backdrop is seamless with the container.
 */
const DARK_SURFACE = '#1f1f1f';

/** Light ink for markup WaveDrom hardcodes to black (the bitfield path). */
const DARK_INK = '#e0e0e0';

/**
 * Annotation colour for dark mode.  This is the dark skin's OWN `.info`
 * colour, so recolouring WaveDrom's hardcoded light-mode blue follows the
 * skin author's stated dark-mode intent rather than inventing a colour.
 */
const DARK_EDGE = '#b8fffc';

/**
 * Make a WaveDrom SVG legible on a dark surface.
 *
 * WaveDrom hardcodes light-mode assumptions that no skin overrides, so
 * several paths need repair, each reachable only in dark mode:
 *
 *  - The timing path (`signal`) always emits a backdrop
 *    `<rect style="stroke:none;fill:white">` — lib/insert-svg-template.js
 *    sets it unconditionally, regardless of skin.  The dark skin then
 *    paints WHITE ink onto it: white on white, invisible.  The backdrop is
 *    recoloured rather than removed so the diagram carries its own surface
 *    instead of depending on whatever sits behind it.
 *
 *  - Node markers and edge labels sit on an occluding chip drawn with a
 *    SEPARATE inline literal, `fill:#FFF`.  Its text declares no fill and so
 *    inherits the dark skin's white `text` rule: white glyphs on a white
 *    chip, i.e. an edge label that renders as a blank box.  Recolouring the
 *    chip (not the text) keeps the ink light and matches the backdrop rule.
 *
 *  - Edge lines and their arrowhead/arrowtail markers hardcode the LIGHT
 *    skin's blue inline (`#0041c4`, `#00F`) in stroke AND fill positions,
 *    ignoring the skin entirely.  On `#1f1f1f` that is a ~2:1 contrast
 *    hairline; markers and lines are remapped together so an arrowhead
 *    cannot end up a different colour from the line it terminates.
 *
 *  - The bitfield path (`reg`) emits no <style> at all and styles itself
 *    with inline attributes, hardcoding stroke="black" and leaving <text>
 *    to inherit — also black.  On a dark container that is near-invisible.
 *    Its coloured field rects carry their own fill in a style attribute
 *    (fill-opacity:0.1;fill:#ff0000) and are deliberately left alone.
 *
 * Every rewrite here targets MARKUP only: the skin's <style> contents are
 * masked out first and restored byte-for-byte afterwards.  Without that mask
 * the chip rule (`fill:#FFF`) ALSO matches the skin's own
 * `text{fill:#ffffff}` declaration and silently recolours every label to the
 * surface colour — converting an invisible-white label into an
 * invisible-dark one.  Hex rewrites are additionally end-anchored so the
 * dark skin's inline `stroke:#fff400` cannot be mangled into `#1f1f1f400`.
 *
 * Light mode is returned untouched: it renders correctly today, so there
 * is nothing to fix and no reason to risk it.
 */
export function themeSvgSurface(svg: string, isDark: boolean): string {
    if (typeof svg !== 'string' || !isDark) return svg;
    // Presence of a skin stylesheet must be read BEFORE masking.
    const hasStyle = /<style\b/.test(svg);
    const sheets: string[] = [];
    let out = svg.replace(/(<style\b[^>]*>)([\s\S]*?)(<\/style>)/g,
        (_m, open: string, css: string, close: string) => {
            sheets.push(css);
            return `${open}\u0000STYLE${sheets.length - 1}\u0000${close}`;
        });
    // The backdrop rect only exists on the timing path.
    out = out.replace(/(<rect\b[^>]*\sstyle=")stroke:none;fill:white(")/g,
        `$1stroke:none;fill:${DARK_SURFACE}$2`);
    // Only the bitfield path uses inline stroke attributes; the timing
    // path strokes exclusively via skin classes, so this cannot reach it.
    out = out.replace(/(\sstroke=")black(")/g, `$1${DARK_INK}$2`);
    // Occluding chips behind node markers and edge labels.
    out = out.replace(/fill:#(?:[fF]{6}|[fF]{3})(?![0-9a-fA-F])/g,
        `fill:${DARK_SURFACE}`);
    // Hardcoded light-skin annotation blue, in stroke and fill alike.
    out = out.replace(/(fill|stroke):#0041c4(?![0-9a-fA-F])/g,
        `$1:${DARK_EDGE}`);
    out = out.replace(/(fill|stroke):#00[fF](?![0-9a-fA-F])/g,
        `$1:${DARK_EDGE}`);
    // A skin stylesheet already sets a text fill; its absence identifies
    // the bitfield path, whose <text> would otherwise inherit black.
    if (!hasStyle) {
        const root = out.match(/<svg\b[^>]*>/);
        if (root && root.index !== undefined && !/\sfill="/.test(root[0])) {
            const tag = root[0].replace('<svg', `<svg fill="${DARK_INK}"`);
            out = out.slice(0, root.index) + tag
                + out.slice(root.index + root[0].length);
        }
    }
    return out.replace(/\u0000STYLE(\d+)\u0000/g,
        (_m, i: string) => sheets[Number(i)]);
}
