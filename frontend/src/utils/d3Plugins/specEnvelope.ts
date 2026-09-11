/**
 * Envelope unwrapping shared by the D3 plugin wrappers.
 *
 * Every wrapper receives one of THREE spec shapes, and the third is the one
 * that was mishandled everywhere until the timeline plugin's tests caught it:
 *
 *   1. `{ type, definition: '<fence text>' }`  -- what MarkdownRenderer sends;
 *      `definition` is always a string.
 *   2. a DIRECT spec (`{ type: 'packet', sections: [...] }`) with no
 *      `definition` key at all.
 *   3. `{ type, definition: { ... } }` -- an envelope whose definition is
 *      ALREADY AN OBJECT.  A ```d3 fence carrying nested JSON parses in
 *      D3Renderer before any plugin sees it, so this shape is reachable from
 *      ordinary model output.
 *
 * The historical unwrap
 *
 *     typeof rawSpec?.definition === 'string' ? rawSpec.definition : rawSpec
 *
 * handles 1 and 2 and hands the ENGINE the whole envelope for 3.  The engine
 * then rejects `type` as an unknown key or reports a missing required field --
 * an error that blames a spec that was perfectly fine -- or, where `canHandle`
 * shares the logic, the plugin is never selected and the renderer burns its
 * ~30s no-plugin timeout.
 *
 * The correct rule keys on the KEY, not the value's type: when `definition`
 * exists, it IS the definition.  No plugin vocabulary (packet sections,
 * wavedrom signal, railroad diagram/rules, flamegraph name/value, timeline
 * items) uses a `definition` field of its own, so a direct spec can never be
 * mistaken for an envelope.
 */
export function extractDefinition(rawSpec: any): any {
    return rawSpec !== null && typeof rawSpec === 'object' && 'definition' in rawSpec
        ? rawSpec.definition
        : rawSpec;
}

/**
 * Streaming guard for the wrappers that claim their spec by TYPE.
 *
 * A wrapper whose `canHandle` keys on `spec.type` is selected on the FIRST
 * streaming chunk, so `render()` runs against a truncated body and paints an
 * error card straight into the container -- once per chunk, until the fence
 * closes.  That card bypasses D3Renderer's own error suppression, which gates
 * only the renderError STATE it owns (`renderError && !isStreaming`), so the
 * user watches a red card flicker where every other diagram type shows
 * nothing.  The `isDefinitionComplete` hook that was meant to prevent this is
 * consulted by D3Renderer ONLY for a bare string spec, and a markdown fence
 * always arrives as an object envelope -- hence the guard lives here.
 *
 * Returns true when the body is still arriving AND has not yet parsed: skip
 * the render silently and retry on the next chunk.
 *
 * A body that PARSES but is invalid is deliberately NOT deferred.  That error
 * is real, and deferring it would hide it permanently: D3Renderer skips the
 * post-stream re-render when the definition text is unchanged (its spec hash
 * excludes the streaming flags), so the final frame would stay blank.
 */
export function isStreamingIncomplete(
    rawSpec: any,
    isComplete: (definition: string) => boolean,
): boolean {
    if (!rawSpec || typeof rawSpec !== 'object') return false;
    if (rawSpec.isStreaming !== true) return false;
    const definition = extractDefinition(rawSpec);
    // An object definition was parsed upstream; there is nothing to wait for.
    if (typeof definition !== 'string') return false;
    return !isComplete(definition);
}