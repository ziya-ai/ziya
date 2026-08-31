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
