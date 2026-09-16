/**
 * D-001: resolve the width/height a d3 plugin's render() is called with.
 *
 * Defect (basic-chart-w2-08..11 and every d3 plugin): D3Renderer built the
 * plugin arg as `{ ...spec, width: width || 600, height: height || 400 }`,
 * spreading the spec first and then UNCONDITIONALLY overwriting width/height
 * with the component's own props (which default to 600x400). A spec that
 * explicitly requested `width`/`height` (110x80, 220x2400, 3600x2600, ...) had
 * those silently discarded, so every chart rendered at the same ~600x400 shape
 * regardless of the requested canvas — the caller could not enlarge the canvas
 * to relieve density, nor request a portrait aspect.
 *
 * Fix: an explicit numeric, positive spec dimension WINS; otherwise fall back to
 * the renderer's container-derived width/height (themselves defaulting to
 * 600x400). This is a strict no-op whenever the spec omits dimensions — the
 * overwhelmingly common case — so charts that never asked for a size are
 * unchanged, and only a spec that actually requested a size sees new behaviour.
 * (`estimateDiagramSize` in D3Renderer already prefers explicit spec dims for
 * layout reservation; this aligns the value passed to the plugin with it.)
 */
export function resolvePluginDimensions(
    spec: any,
    fallbackWidth: number | undefined,
    fallbackHeight: number | undefined,
): { width: number; height: number } {
    const specW = spec && typeof spec.width === 'number' && spec.width > 0 ? spec.width : undefined;
    const specH = spec && typeof spec.height === 'number' && spec.height > 0 ? spec.height : undefined;
    return {
        width: specW ?? (fallbackWidth || 600),
        height: specH ?? (fallbackHeight || 400),
    };
}

/**
 * The width/height keys D3Renderer spreads onto the plugin argument.
 *
 * For most plugins the spec is an envelope and `width`/`height` are the
 * plugin canvas, so resolvePluginDimensions applies. For a plugin whose spec
 * IS the rendered document (ownsSpecDimensions), those keys belong to the
 * document: an inline Vega-Lite spec with `width: 'container'` was being
 * rewritten to 600 (a string is not a positive number, so the fallback won),
 * and one with no height had `height: 400` injected — which the plugin then
 * read as an AUTHORED height and skipped its height derivation for.
 *
 * Returns nothing for such a plugin so the spread leaves the document's own
 * keys — present or absent — exactly as written.
 */
export function pluginDimensionProps(
    plugin: { ownsSpecDimensions?: boolean } | null | undefined,
    spec: any,
    fallbackWidth: number | undefined,
    fallbackHeight: number | undefined,
): { width?: number; height?: number } {
    if (plugin?.ownsSpecDimensions) return {};
    return resolvePluginDimensions(spec, fallbackWidth, fallbackHeight);
}

/** Keys D3Renderer adds to the plugin argument that are not part of any document. */
export const RENDERER_ENVELOPE_KEYS = [
    'type', 'isStreaming', 'forceRender', 'definition', 'isMarkdownBlockClosed', 'containerWidth',
] as const;

/**
 * D-001 (remaining container-clamp cause): read a spec's EXPLICIT, positive
 * numeric width/height, tolerating the two envelope shapes the renderer accepts
 * — a raw JSON string, and the `{ type:'d3', definition:<spec> }` wrapper used
 * by DiagramRenderPage / external callers. Returns null unless BOTH dimensions
 * are explicit positive numbers, so a spec that never asked for a size is a
 * strict no-op.
 */
export function extractExplicitDimensions(spec: any): { width: number; height: number } | null {
    let s = spec;
    if (typeof s === 'string') {
        try {
            s = JSON.parse(s);
        } catch {
            return null;
        }
    }
    if (!s || typeof s !== 'object') return null;
    // Unwrap a { type:'d3', definition:<spec> } envelope; the inner definition
    // is where the plugin-targeted width/height live.
    if (s.definition && typeof s.definition === 'object') s = s.definition;
    const w = typeof s.width === 'number' && s.width > 0 ? s.width : undefined;
    const h = typeof s.height === 'number' && s.height > 0 ? s.height : undefined;
    if (w !== undefined && h !== undefined) return { width: w, height: h };
    return null;
}

/**
 * D-001: decide the CONTAINER dimensions for a spec-driven canvas.
 *
 * The plugin already receives the right width/height (via resolvePluginDimensions
 * above) and writes them onto the SVG — but for a non-'fixed' plugin the
 * D3Renderer container was hard-clamped to its sizingConfig default
 * (basic-chart: width 100%, height 400px, overflow hidden), so a requested
 * 220x2400 / 3600x2600 canvas was silently cropped to ~600x400 on capture. The
 * requested dimensions were therefore still ignored at the container layer even
 * though the SVG carried them.
 *
 * When a spec supplies EXPLICIT dimensions and the plugin is not 'fixed'
 * (i.e. a responsive/auto chart), honour them on the container so the full
 * requested canvas is laid out and captured. 'fixed' plugins already receive the
 * explicit props unchanged, and a spec without explicit dims returns null so the
 * responsive default is preserved verbatim.
 *
 * A plugin that ownsSpecDimensions is excluded: for it, `width`/`height` are
 * document properties (a Vega-Lite plot-area size), not a canvas. Pinning the
 * container to them is actively wrong — with width:'container' Vega measures
 * that pinned box, emits an SVG of exactly that width, and once the container
 * is later released to 100% the responsive `svg { width:100% }` rule scales the
 * SVG (and its viewBox content) up to fill it, leaving an oversized chart and a
 * chart-sized blank beneath it once the wrappers adopt the inflated height.
 */
export function resolveContainerDimensions(
    spec: any,
    sizingStrategy: string | undefined,
    plugin?: { ownsSpecDimensions?: boolean } | null,
): { width: string; height: string } | null {
    if (sizingStrategy === 'fixed') return null;
    if (plugin?.ownsSpecDimensions) return null;
    const dims = extractExplicitDimensions(spec);
    if (!dims) return null;
    return { width: `${dims.width}px`, height: `${dims.height}px` };
}
