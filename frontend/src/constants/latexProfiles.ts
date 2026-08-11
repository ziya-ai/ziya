/**
 * Canonical frontend registry of server-rendered LaTeX diagram profiles.
 *
 * Mirror of ``PROFILES`` in app/services/latex_profiles.py.  That file is the
 * ultimate authority (it owns the preamble, packages and TeX Live deps); this
 * one exists so the four frontend sites that must agree about LaTeX fences
 * read from ONE place instead of four independent literals.
 *
 * The cross-layer guard test (components/__tests__/latexFenceRouting.test.ts)
 * asserts this list and the Python registry agree, so drift fails CI rather
 * than silently degrading a fence to a code block.
 *
 * Why this file exists at all: ```chemfig once rendered as literal source
 * while BOTH ends of the pipeline supported it, because the middle routing
 * step was missing the language.  Nothing failed loudly.  Adding a profile
 * previously meant editing six places; it now means editing this map.
 */

/** Fence language -> backend profile key. */
export const LATEX_LANG_TO_PROFILE: Readonly<Record<string, string>> = {
  'tikz': 'tikz',
  'circuitikz': 'circuitikz',
  'latex-circuit': 'circuitikz',
  'chemfig': 'chemfig',
  'tikz-cd': 'tikz-cd',
} as const;

/**
 * Backend profile keys, derived from the map above.
 *
 * Deliberately derived rather than written out: a hand-maintained second list
 * is exactly the duplication this module removes.
 */
export const LATEX_PROFILE_KEYS: readonly string[] =
  [...new Set(Object.values(LATEX_LANG_TO_PROFILE))];

/**
 * Every fence language routed to the LaTeX renderer, including ``latex``.
 *
 * ``latex`` is present for ROUTING but absent from LATEX_LANG_TO_PROFILE: a
 * bare ```latex block is usually raw math or prose, and compiling it inside a
 * circuitikz environment would fail, so it deliberately falls back to a code
 * block.  Keeping it here documents the exception instead of leaving it as an
 * unexplained asymmetry between two lists.
 */
export const LATEX_FENCE_LANGS: readonly string[] = [
  ...Object.keys(LATEX_LANG_TO_PROFILE),
  'latex',
];

/** Whether a normalised fence language routes to the LaTeX renderer. */
export function isLatexFenceLang(lang: string): boolean {
  return LATEX_FENCE_LANGS.includes(lang.toLowerCase().trim());
}

/**
 * Resolve a fence language to its backend profile, or null.
 *
 * Null means "route to a code block", which is the correct outcome for
 * ``latex`` and for any unrecognised language.
 */
export function latexProfileForLang(lang: string): string | null {
  return LATEX_LANG_TO_PROFILE[lang.toLowerCase().trim()] ?? null;
}
