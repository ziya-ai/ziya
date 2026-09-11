import type { MarkedOptions, Token } from 'marked';

/**
 * Escape raw HTML tokens inside thinking-block markdown.
 *
 * Reasoning text frequently contains literal markup the model is *talking
 * about* rather than emitting for display -- DrawIO XML, HTML snippets, XML
 * templates.  Left as raw HTML, marked passes it through to the DOM where the
 * browser tries to interpret unknown tags.  While streaming, each delta
 * flips partially-closed tags between "parsed as element" and "parsed as
 * text", which manifests as continuous flicker until the stream ends.
 *
 * Escaping every raw HTML token makes the output stable between partial and
 * complete states and renders the markup as the literal text the model
 * intended.  Fenced code blocks are unaffected: marked tokenises them as
 * `code`, not `html`.
 */
const escapeHtml = (s: string): string =>
    s
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');

export const neutraliseRawHtmlToken = (token: Token): void => {
    if (token.type !== 'html') return;
    const t = token as Token & { raw: string; text: string; block?: boolean };
    const escaped = escapeHtml(t.raw);
    t.text = escaped;
    // marked emits block-level html tokens verbatim via `text`; inline html
    // tokens likewise.  Overwriting `text` is sufficient for both.
    if (t.block) {
        // Preserve paragraph separation for block-level markup.
        t.text = `<p>${escaped.replace(/\n/g, '<br>')}</p>`;
    }
};

/**
 * Marked options used by the ThinkingBlock renderer.
 */
export const THINKING_MARKED_OPTIONS: MarkedOptions = {
    breaks: true,
    gfm: true,
    walkTokens: neutraliseRawHtmlToken,
};
