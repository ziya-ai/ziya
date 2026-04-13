/**
 * HTML sanitization utilities.
 *
 * Used wherever user-controlled or server-controlled strings are
 * interpolated into HTML template literals (e.g. error messages,
 * tool display headers).
 */

/** Escape HTML special characters to prevent XSS.
 *
 * Single quotes are left unescaped — they are harmless in element text
 * content and only need escaping inside single-quoted attribute values
 * (which we don't use).  Escaping them causes visible &#039; artefacts
 * when the HTML passes through a markdown lexer before innerHTML render.
 */
export function escapeHtml(unsafe: string): string {
    return unsafe
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}
