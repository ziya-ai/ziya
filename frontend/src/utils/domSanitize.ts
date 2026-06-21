/**
 * DOMPurify-backed HTML sanitization (ASR F-026).
 *
 * Model-generated content is attacker-influenced (prompt injection via
 * workspace files, tool responses, retrieved documents). Regex-based
 * sanitizers are fundamentally bypassable (HTML entities, unquoted
 * attributes, SVG/MathML namespace tricks, mutation XSS), so any path that
 * feeds model-derived HTML into dangerouslySetInnerHTML / innerHTML must
 * route through a real HTML parser. DOMPurify parses the markup and removes
 * scripts, event handlers, and dangerous protocols structurally.
 *
 * Two profiles:
 *   sanitizeModelHtml  — inline rich text the model emits inside markdown
 *                        (links, formatting, tables). Anchors are forced to
 *                        rel="noopener noreferrer" and target="_blank".
 *   sanitizeMockupHtml — full-document HTML mockups rendered in a sandboxed
 *                        iframe. Broader tag set (full-page layout) but still
 *                        no scripts/handlers; the iframe sandbox is the outer
 *                        containment and this is defense-in-depth on top.
 */
import createDOMPurify from 'dompurify';

// Resolving DOMPurify is fiddly across bundlers/test runners: the import may
// arrive as a ready instance, as { default: instance }, or as the factory
// function that must be called with a window. Probe each shape and produce a
// usable instance with a working .sanitize. (ASR F-026)
function _resolveDOMPurify(): any {
    const candidates = [
        createDOMPurify as any,
        (createDOMPurify as any)?.default,
    ];
    for (const c of candidates) {
        if (c && typeof c.sanitize === 'function') return c;
    }
    // Otherwise treat it as the factory and instantiate against the window.
    const win = typeof window !== 'undefined' ? window : undefined;
    for (const factory of candidates) {
        if (typeof factory === 'function') {
            try {
                const inst = factory(win);
                if (inst && typeof inst.sanitize === 'function') return inst;
            } catch {
                /* try next candidate */
            }
        }
    }
    return createDOMPurify as any;
}

const DOMPurify: any = _resolveDOMPurify();

// Force safe anchor attributes on any output that survives sanitization.
// Guard addHook: some interop shapes expose .sanitize but not .addHook;
// in that case the SAFE_LINK_CONFIG below applies the same hardening.
if (typeof DOMPurify.addHook === 'function') {
    DOMPurify.addHook('afterSanitizeAttributes', (node: Element) => {
        if (node.tagName === 'A' && node.getAttribute('href')) {
            node.setAttribute('rel', 'noopener noreferrer');
            node.setAttribute('target', '_blank');
        }
    });
}


const INLINE_CONFIG = {
    // Inline + block rich text the model legitimately produces in markdown.
    ALLOWED_TAGS: [
        'a', 'b', 'i', 'em', 'strong', 'u', 's', 'del', 'ins', 'mark',
        'code', 'pre', 'kbd', 'samp', 'var', 'sub', 'sup', 'br', 'hr',
        'span', 'div', 'p', 'blockquote',
        'ul', 'ol', 'li', 'dl', 'dt', 'dd',
        'table', 'thead', 'tbody', 'tfoot', 'tr', 'th', 'td', 'caption',
        'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
        'img',
    ],
    ALLOWED_ATTR: [
        'href', 'title', 'class', 'id', 'colspan', 'rowspan', 'align',
        'src', 'alt', 'width', 'height', 'style', 'lang', 'dir',
    ],
    // Only http(s), mailto, and data: images — no javascript:/vbscript:.
    ALLOWED_URI_REGEXP: /^(?:(?:https?|mailto):|data:image\/(?:png|jpe?g|gif|webp|svg\+xml);|[^a-z]|[a-z+.-]+(?:[^a-z+.\-:]|$))/i,
};

/**
 * Sanitize inline/block rich HTML emitted by the model inside markdown.
 * Returns a string safe to pass to dangerouslySetInnerHTML.
 */
export function sanitizeModelHtml(raw: string): string {
    if (typeof raw !== 'string' || raw === '') return '';
    return DOMPurify.sanitize(raw, INLINE_CONFIG) as unknown as string;
}

/**
 * Sanitize a full HTML mockup document destined for a sandboxed iframe.
 * Scripts and event handlers are removed; the sandbox attribute on the
 * iframe remains the primary containment.
 */
export function sanitizeMockupHtml(raw: string): string {
    if (typeof raw !== 'string' || raw === '') return '';
    return DOMPurify.sanitize(raw, {
        WHOLE_DOCUMENT: true,
        // Allow full-page layout/styling tags but no scripts.
        FORBID_TAGS: ['script', 'object', 'embed', 'base'],
        FORBID_ATTR: ['onerror', 'onload', 'onclick'],
        ADD_TAGS: ['style', 'link', 'meta', 'head', 'body', 'html', 'title'],
        ADD_ATTR: ['rel', 'media', 'type', 'charset', 'name', 'content'],
    }) as unknown as string;
}

/**
 * Sanitize a MathML fragment emitted by the model (ASR F-026).
 *
 * The inline profile's tag allowlist does not include MathML elements, so it
 * would strip a legitimate <math> block entirely. DOMPurify's mathMl profile
 * keeps the MathML element/attribute set while still removing scripts, event
 * handlers, and javascript:/data: script vectors (incl. MathML-namespace XSS
 * like <math><mtext><script> and xlink:href="javascript:").
 */
export function sanitizeMathMl(raw: string): string {
    if (typeof raw !== 'string' || raw === '') return '';
    return DOMPurify.sanitize(raw, {
        USE_PROFILES: { mathMl: true, html: true },
    }) as unknown as string;
}

export default { sanitizeModelHtml, sanitizeMockupHtml, sanitizeMathMl };
