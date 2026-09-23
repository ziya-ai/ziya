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
    // Unwrap a { type:'d3'|'basic-chart', definition:<spec> } envelope; the
    // inner definition is where the plugin-targeted width/height live.
    //
    // D-001 (basic-chart-w2-11, 3600x2600 blank): the render boundary
    // (app/services/diagram_renderer.py normalize_spec_definition) serialises an
    // object definition to a JSON STRING before it reaches the browser, so the
    // memoised container-style path (D3Renderer resolveContainerDimensions on
    // the RAW prop spec, not the unwrapped one) saw a string here, skipped the
    // object-only unwrap, read no top-level width/height and returned null. The
    // responsive default (height:400px) then clamped the tall canvas, cropping
    // the 2600px-tall chart to blank on capture — w2-08 (180px) only escaped
    // because it fit inside 400px. Parse a string definition too so explicit
    // dims survive the string-envelope shape as well as the object one.
    if (s.definition != null) {
        if (typeof s.definition === 'object') {
            s = s.definition;
        } else if (typeof s.definition === 'string') {
            try {
                const innerDef = JSON.parse(s.definition);
                if (innerDef && typeof innerDef === 'object') s = innerDef;
            } catch {
                // Non-JSON definition (e.g. a graphviz/mermaid source string) —
                // leave s as the envelope; it carries no plugin width/height.
            }
        }
    }
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

/**
 * Above this many px an explicit 'fixed'-plugin canvas is WIDER than the headless
 * capture frame (~632px) and must have its width adopted on the container
 * (D-043). Kept at the historical 600px chord baseline so normal/small canvases
 * (which already fit) take the unchanged path and stay byte-identical.
 */
export const FIXED_WIDTH_ADOPT_THRESHOLD_PX = 600;

/**
 * D-043 (regression): width-axis analog of ``needsDynamicHeight`` for a 'fixed'
 * plugin whose spec canvas is wider than the host capture frame.
 *
 * ``needsDynamicHeight`` gives the render container ``height:auto`` so a tall
 * canvas grows and is captured whole. There is no width equivalent: a 'fixed'
 * plugin (chord) leaves the inner render container at ``width:100%`` +
 * ``overflow:auto``, so a canvas WIDER than the ~632px capture frame
 * (chord-w1-14 860px, w2-10 1400px, w2-14 2000px) is clipped on the RIGHT — real
 * arcs and labels are lost. The capture-fit unclip in diagram_renderer.py only
 * relaxes the container's ANCESTORS; it never reaches this inner wrapper, and a
 * ``width:100%`` inner box collapses (rather than holding the canvas width) once
 * the ancestor shrink-wrap fires, so the clip persists.
 *
 * Adopting the explicit px width on the container makes it hold the full canvas
 * so the ancestor unclip then reveals it. Only fires for a 'fixed' plugin whose
 * spec supplies BOTH explicit numeric dimensions AND a width beyond the frame
 * threshold; every normal/small fixed canvas (and every non-'fixed' plugin)
 * returns null and is unchanged.
 *
 * Exported for regression testing.
 */
export function resolveFixedContainerWidth(
    spec: any,
    sizingStrategy: string | undefined,
): string | null {
    if (sizingStrategy !== 'fixed') return null;
    const dims = extractExplicitDimensions(spec);
    if (!dims) return null;
    if (dims.width <= FIXED_WIDTH_ADOPT_THRESHOLD_PX) return null;
    return `${dims.width}px`;
}

/**
 * D-043 (regression real cause): the width + overflow the imperative D3Renderer
 * effect writes onto the ACTUAL d3 render container (d3ContainerRef, where the
 * SVG lives). That effect runs AFTER React applies the containerStyles memo and
 * writes directly to ``container.style``, so it — not the memo — decides the
 * pixels at capture time.
 *
 * The pre-fix effect resolved width as ``explicitContainerDims ?? (isFlexible ?
 * '100%' : `${width}px`)`` and overflow as ``explicitContainerDims ? 'auto' :
 * 'hidden'``. resolveContainerDimensions returns null for a 'fixed' plugin, so
 * for chord that collapsed to the component ``width`` prop (default 600px) with
 * overflow:hidden — clipping the right edge of an explicit canvas wider than the
 * frame (chord-w1-14 860px, w2-10 1400px, w2-14 2000px). The JSX containerStyles
 * width fix never survived because this override clobbered it.
 *
 * This helper folds ``fixedContainerWidth`` (the width-axis analog of
 * needsDynamicHeight) into that decision: a wide 'fixed' canvas now holds its
 * full px width and scrolls (overflow:auto) instead of clipping. It is a strict
 * no-op for every other case — null fixedContainerWidth reproduces the old
 * width/overflow exactly. Purely a sizing/overflow decision with no color
 * input, so it is identical in light and dark themes.
 *
 * Exported for regression testing.
 */
export function resolveRenderContainerBox(args: {
    explicitContainerDims: { width: string; height: string } | null;
    fixedContainerWidth: string | null;
    isFlexible: boolean;
    fallbackWidthPx: number;
    needsOverflowVisible: boolean;
    willHaveError: boolean;
}): { width: string; overflow: 'visible' | 'auto' | 'hidden' } {
    const { explicitContainerDims, fixedContainerWidth, isFlexible, fallbackWidthPx, needsOverflowVisible, willHaveError } = args;
    const width = explicitContainerDims
        ? explicitContainerDims.width
        : (fixedContainerWidth
            ? fixedContainerWidth
            : (isFlexible ? '100%' : `${fallbackWidthPx}px`));
    const overflow: 'visible' | 'auto' | 'hidden' = (needsOverflowVisible || willHaveError)
        ? 'visible'
        : (explicitContainerDims || fixedContainerWidth ? 'auto' : 'hidden');
    return { width, overflow };
}
