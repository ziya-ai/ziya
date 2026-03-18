/**
 * HTML sanitization utilities.
 *
 * Used wherever user-controlled or server-controlled strings are
 * interpolated into HTML template literals (e.g. error messages,
 * tool display headers).
 */

/** Escape HTML special characters to prevent XSS. */
export function escapeHtml(unsafe: string): string {
    return unsafe
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}
