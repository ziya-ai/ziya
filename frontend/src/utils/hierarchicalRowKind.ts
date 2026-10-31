/**
 * Whether one row of a hierarchical tool result (search hits etc.) is shown
 * as raw code in <pre><code> or rendered as markdown.
 *
 * Decided per row: one result can mix code hits with markdown notices such
 * as "*No snippets available*", so the first row cannot speak for the rest.
 * 'text' and 'markdown' (and an absent language) mean markdown.
 */
export function isRawCodeRow(row: { language?: string } | null | undefined): boolean {
    const lang = row?.language;
    return Boolean(lang) && lang !== 'text' && lang !== 'markdown';
}
