/**
 * safeExternalUrl — scheme-allowlist guard for URLs that flow into
 * window.open() / anchor href from untrusted sources.
 *
 * MCP registry service metadata (repository / security-review / homepage
 * URLs) is provider-supplied and reaches the UI unvalidated. Passing such a
 * string straight to window.open() lets a malicious registry entry ship a
 * `javascript:` / `data:` / `vbscript:` URL that executes in the app origin
 * the moment the user clicks the "View Repository" / "Security Review"
 * button — stored XSS [PenPal #90, CWE-200/CWE-79].
 *
 * Only http(s) is permitted. Anything else (including protocol-relative
 * `//host`, relative paths, and unparseable input) returns null so the
 * caller can decline to open it.
 */
const _ALLOWED_SCHEMES = new Set(['http:', 'https:']);

export function safeExternalUrl(raw: unknown): string | null {
    if (typeof raw !== 'string') return null;
    const trimmed = raw.trim();
    if (!trimmed) return null;
    let parsed: URL;
    try {
        // No base: a relative or protocol-relative value throws here and is
        // rejected, which is the desired behavior for an external link.
        parsed = new URL(trimmed);
    } catch {
        return null;
    }
    if (!_ALLOWED_SCHEMES.has(parsed.protocol.toLowerCase())) return null;
    return trimmed;
}

/**
 * Open an untrusted URL in a new tab, but only if it passes the scheme
 * allowlist. Uses noopener/noreferrer so the opened page cannot reach back
 * into the opener window. Returns true if the URL was opened.
 */
export function safeOpenExternal(raw: unknown): boolean {
    const url = safeExternalUrl(raw);
    if (!url) return false;
    window.open(url, '_blank', 'noopener,noreferrer');
    return true;
}
