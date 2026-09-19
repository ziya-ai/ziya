import { parseD3Spec } from './d3SpecParser';

/**
 * Unwrap a diagram envelope before plugin dispatch in D3Renderer.
 *
 * render_diagram / DiagramRenderPage / external callers wrap a spec as
 * `{ type: <fence-language>, definition: <spec> }`.  Only two fence languages
 * are unwrapped HERE:
 *
 *   - `type: 'd3'`          the generic d3 envelope; the inner definition is
 *                           the real plugin-targeted spec (bar, line, network,
 *                           force-directed, ... or a typeless {data:[...]}).
 *   - `type: 'basic-chart'` render_diagram may emit the engine id as the
 *                           top-level type.  basicChart.canHandle expects the
 *                           RAW inner spec and rejects the 'basic-chart'
 *                           envelope, so without this unwrap the spec matches
 *                           no plugin and the render hangs to the timeout
 *                           (D-320).
 *
 * The inner definition may be a JSON/JS string OR an already-parsed object.
 * The inner is substituted even when it has NO own `type`: a typeless
 * `{data:[{label,value}]}` is legitimately claimed by basicChart's shape-based
 * canHandle (D-321).  Requiring inner.type here left a typeless inner wrapped,
 * so it matched no plugin and hung.
 *
 * Every OTHER envelope-style engine (graphviz, d2, chord, drawio,
 * force-directed, railroad, packet, music, wavedrom, flamegraph) unwraps its
 * OWN `{type, definition}` envelope inside its canHandle/render, keyed to its
 * specific type.  Those are deliberately left wrapped here: unwrapping them
 * generically would strip the `type` the plugin dispatches on.  This is why
 * the outer type must be exactly 'd3' or 'basic-chart'.
 *
 * Returns the unwrapped inner spec, or the original spec when it is not an
 * unwrappable envelope (no-op is the common case).
 */
export function unwrapDiagramEnvelope(spec: any): any {
    if (
        typeof spec === 'object' &&
        spec !== null &&
        spec.definition != null &&
        (spec.type === 'd3' || spec.type === 'basic-chart')
    ) {
        let inner: any = spec.definition;
        if (typeof inner === 'string') {
            inner = parseD3Spec(inner);
        }
        if (inner && typeof inner === 'object') {
            return inner;
        }
    }
    return spec;
}
