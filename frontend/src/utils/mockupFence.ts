/**
 * Parsing for the html-mockup fence info string.
 *
 * The block serves two unrelated jobs. Its original one is collaborative UX
 * work, where the frame, the source view and the pop-out are the point. Its
 * other one — inlining a specific graphic mid-discussion, because no
 * diagram renderer covers the shape — wants none of that: the chrome is
 * louder than the figure it wraps.
 *
 * A variant modifier in the info string distinguishes them:
 *
 *     ```
 *     ```html-mockup figure     → figure  (bare graphic, no chrome)
 *
 * Everything that keys off the fence language must therefore read the FIRST
 * whitespace-delimited token rather than the whole info string, or a fence
 * carrying a modifier silently degrades to a plain code block.
 */

export type MockupVariant = 'mockup' | 'figure';

/** Fence languages that render as an HTML mockup. */
const MOCKUP_LANGS = new Set(['html-mockup', 'ui-mockup', 'mockup']);

/**
 * Modifiers that select the chrome-free presentation. Several spellings are
 * accepted because the model writes this from a prompt description, not from
 * a schema, and a near-miss would fall back to full chrome — the wrong
 * default for a figure and a confusing one to debug from the rendered output.
 */
const FIGURE_MODIFIERS = new Set([
    'figure', 'inline', 'bare', 'nochrome', 'no-chrome', 'plain',
]);

/**
 * The base language of a fence info string — its first whitespace-delimited
 * token, lowercased.
 *
 * CommonMark defines the info string as everything after the opening run, of
 * which only the first word is conventionally the language. Callers that
 * matched the full string worked only because no fence carried a modifier.
 */
export function fenceBaseLang(info?: string | null): string {
    if (!info) return '';
    return info.trim().split(/\s+/, 1)[0].toLowerCase();
}

/** The modifier tokens following the language, lowercased. */
export function fenceModifiers(info?: string | null): string[] {
    if (!info) return [];
    return info.trim().split(/\s+/).slice(1).map(t => t.toLowerCase());
}

export interface MockupFence {
    isMockup: boolean;
    variant: MockupVariant;
}

/**
 * Classify a fence info string as a mockup and resolve its variant.
 *
 * A non-mockup language always reports variant 'mockup'; callers gate on
 * `isMockup` first, and returning a variant for a language that will never
 * reach the renderer would invite reading it without that check.
 */
export function parseMockupFence(info?: string | null): MockupFence {
    const base = fenceBaseLang(info);
    if (!MOCKUP_LANGS.has(base)) return { isMockup: false, variant: 'mockup' };
    const isFigure = fenceModifiers(info).some(m => FIGURE_MODIFIERS.has(m));
    return { isMockup: true, variant: isFigure ? 'figure' : 'mockup' };
}
