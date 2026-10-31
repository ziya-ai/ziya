/**
 * Fence languages whose body is a JSON spec for one of the D3 engines.
 *
 * The system prompt teaches the model to emit ```chord (and the fence
 * scanner already treats chord / force-directed / network as atomic), but a
 * language the renderer does not route falls through to the plain 'code'
 * case and the user sees raw JSON with no error anywhere.  Every entry here
 * is routed to the 'd3' token type, and the mapped value is the spec `type`
 * to assume when the body omits one (```chord with just names + matrix).
 * An explicit `type` inside the body always wins.
 */
const FENCE_LANG_TO_D3_TYPE: Record<string, string> = {
  chord: 'chord',
  'chord-directed': 'chord-directed',
  'force-directed': 'force-directed',
  forcedirected: 'force-directed',
  network: 'network',
};

export const D3_SPEC_FENCE_LANGS: readonly string[] = Object.keys(FENCE_LANG_TO_D3_TYPE);

/**
 * The D3 spec type implied by a fence language, or null when the language is
 * not one of the JSON-spec D3 fences.  Case- and whitespace-insensitive.
 */
export function d3TypeForFenceLang(lang: string | null | undefined): string | null {
  if (!lang) return null;
  return FENCE_LANG_TO_D3_TYPE[lang.toLowerCase().trim()] ?? null;
}
