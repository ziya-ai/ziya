/**
 * D-018 (chat-message w3-10/11/14/15): reassemble inline styled HTML spans that
 * marked's inline lexer tears apart.
 *
 * When a model emits an inline styled element such as
 *
 *     <span style="background:#2e7d32;color:#fff">PASS</span>
 *
 * marked does NOT return it as one `html` token. Its inline lexer emits three
 * separate tokens — an `html` token holding only the OPENING tag
 * (`<span style="...">`), a `text` token holding `PASS`, and a second `html`
 * token holding the CLOSING tag (`</span>`). The renderer's `html` case then
 * wraps the lone opening tag in its own element; the browser auto-closes it as
 * an EMPTY styled box and the chip text lands as an unstyled sibling. The style
 * is thereby divorced from its text and the chip never renders as intended.
 * This is theme-independent and reproduces for every inline styled span and
 * HTML status badge.
 *
 * This pass runs over an inline token run BEFORE rendering and stitches a
 * dangling inline opener back together with the text/`br`/`codespan`/`escape`
 * tokens that follow it, up to its matching closing tag, into ONE balanced
 * inline `html` token (`_coalescedInline: true`). The renderer then emits that
 * balanced markup inside an inline `<span>`, so the style stays attached to the
 * text and adjacent chips flow inline.
 *
 * It is deliberately conservative:
 *   - it only STARTS a run at an INLINE (`block !== true`) `html` token that has
 *     a net-positive tag balance (a dangling opener); a balanced inline html
 *     token, and every block-level html token, is left untouched;
 *   - it only absorbs token types it can losslessly serialise back to HTML
 *     (`html`, `text`, `escape`, `br`, `codespan`, `space`). If it meets any
 *     other token (e.g. a nested `strong`/`em`/`link` with its own child
 *     tokens) before the opener closes, it ABORTS that run and leaves those
 *     tokens exactly as they were — never dropping or reordering content;
 *   - if the opener is never closed before the run ends, it likewise aborts.
 * A no-op returns the original array reference so non-HTML token lists pay
 * almost nothing.
 */

export interface CoalesceToken {
    type: string;
    text?: string;
    raw?: string;
    block?: boolean;
    tokens?: any[];
    [k: string]: any;
}

// HTML void elements never contribute to nesting depth.
const VOID_TAGS = new Set([
    'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input',
    'link', 'meta', 'param', 'source', 'track', 'wbr',
]);

function escapeText(s: string): string {
    return s
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;');
}

/**
 * Net open/close tag balance of an HTML fragment: +1 per non-void opening tag,
 * -1 per closing tag, 0 for void/self-closing tags. Comments are ignored.
 */
export function tagBalance(html: string): number {
    let depth = 0;
    const tagRe = /<(\/)?([a-zA-Z][a-zA-Z0-9-]*)\b[^>]*?(\/)?>/g;
    let m: RegExpExecArray | null;
    while ((m = tagRe.exec(html)) !== null) {
        const closing = !!m[1];
        const name = m[2].toLowerCase();
        const selfClosing = !!m[3];
        if (closing) {
            depth -= 1;
        } else if (selfClosing || VOID_TAGS.has(name)) {
            // no depth change
        } else {
            depth += 1;
        }
    }
    return depth;
}

/** Serialise one intermediate inline token back to raw HTML, or null if it
 * cannot be represented losslessly (which aborts the current run). */
function serializeInlineToken(tok: CoalesceToken): string | null {
    switch (tok.type) {
        case 'html':
            return tok.text ?? tok.raw ?? '';
        case 'text':
        case 'escape':
            return escapeText(tok.text ?? tok.raw ?? '');
        case 'br':
            return '<br/>';
        case 'space':
            return tok.raw ?? ' ';
        case 'codespan':
            return '<code>' + escapeText(tok.text ?? '') + '</code>';
        default:
            return null;
    }
}

/**
 * Merge dangling inline-HTML opener runs into single balanced inline `html`
 * tokens. Returns the original array reference when nothing was merged.
 */
export function coalesceInlineHtmlTokens(tokens: CoalesceToken[]): CoalesceToken[] {
    if (!Array.isArray(tokens) || tokens.length < 2) return tokens;

    // Quick reject: only act when an inline html opener with a dangling tag
    // actually exists in this run.
    let hasDangling = false;
    for (const t of tokens) {
        if (t && t.type === 'html' && t.block !== true && typeof t.text === 'string' && tagBalance(t.text) > 0) {
            hasDangling = true;
            break;
        }
    }
    if (!hasDangling) return tokens;

    const out: CoalesceToken[] = [];
    let i = 0;
    while (i < tokens.length) {
        const tok = tokens[i];
        const isDanglingOpener =
            tok && tok.type === 'html' && tok.block !== true &&
            typeof tok.text === 'string' && tagBalance(tok.text) > 0;

        if (!isDanglingOpener) {
            out.push(tok);
            i += 1;
            continue;
        }

        // Try to build a balanced run starting at i.
        let depth = tagBalance(tok.text as string);
        let buffer = tok.text as string;
        let j = i + 1;
        let aborted = false;
        while (j < tokens.length && depth > 0) {
            const piece = serializeInlineToken(tokens[j]);
            if (piece === null) {
                aborted = true;
                break;
            }
            buffer += piece;
            if (tokens[j].type === 'html') {
                depth += tagBalance(tokens[j].text ?? '');
            }
            j += 1;
        }

        if (aborted || depth !== 0) {
            // Could not close the opener cleanly: leave the opener as-is and
            // resume scanning at the next token so nothing is lost.
            out.push(tok);
            i += 1;
            continue;
        }

        out.push({
            type: 'html',
            text: buffer,
            raw: buffer,
            block: false,
            _coalescedInline: true,
        });
        i = j;
    }

    return out;
}
