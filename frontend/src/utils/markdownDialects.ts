/**
 * Less-travelled markdown dialect transforms for the chat-message renderer
 * (D-017 w3-05 / w3-06).
 *
 * The chat renderer parses with `marked` (GFM) and renders the resulting
 * token tree to React.  Several standard-but-non-GFM constructs have no marked
 * tokenizer, so their source leaked through verbatim:
 *
 *   - Footnote references `[^id]` and their `[^id]: body` definitions.
 *   - PHP-Markdown-Extra definition lists (`Term` / `: definition`).
 *   - Raw `<details>` blocks whose body is separated from the `<summary>` by a
 *     blank line — CommonMark ends the HTML block at that blank line, so marked
 *     splits the widget open tag from its body and the body escapes the widget.
 *
 * Rather than add three bespoke marked extensions (each needing its own
 * renderTokens case), these transforms normalise the constructs to *raw HTML*
 * that the existing raw-HTML token path already renders through DOMPurify.
 * The HTML is emitted WITHOUT internal blank lines so marked keeps each as a
 * single block/inline `html` token (a blank line would re-split it).
 *
 * Every transform is a pure `string -> string` and is content-agnostic; the
 * caller applies them OUTSIDE fenced code blocks / inline code spans (so a
 * literal `[^1]`, `: ` line, or `<details>` written in code survives), and
 * before the markdown link/bracket ReDoS guards (footnote syntax contains
 * `[`).  Inline markdown inside a footnote/definition/summary body is rendered
 * with a small self-contained inline converter (inline code, bold, italic,
 * links), so inline markup inside them still renders.  The converter is
 * intentionally dependency-free (no `marked` import) so this module stays a
 * pure, unit-testable string transform.
 */

function escapeHtml(s: string): string {
    return s
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;');
}

/**
 * Render a single line of inline markdown for an embedded footnote/definition/
 * summary body, collapsing to one line so the surrounding raw-HTML block stays
 * one marked token.  Handles inline code, bold, italic and links; inline code
 * spans are protected so their contents are not re-parsed.  Anything else is
 * emitted as escaped text.
 */
function inlineToHtml(md: string): string {
    const trimmed = md.replace(/\s+/g, ' ').trim();
    if (trimmed === '') return '';

    // Protect inline code spans first so emphasis/link syntax inside them is
    // left literal, then splice them back after the other passes.
    const codeSpans: string[] = [];
    let s = trimmed.replace(/`([^`]+)`/g, (_m, code: string) => {
        codeSpans.push(`<code>${escapeHtml(code)}</code>`);
        return `\u0000CODE${codeSpans.length - 1}\u0000`;
    });

    s = escapeHtml(s);

    // Links [text](url) — url restricted to http(s)/mailto/# to avoid js: vectors
    // (DOMPurify is still the authoritative boundary downstream).
    s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+|mailto:[^\s)]+|#[^\s)]*)\)/g,
        (_m, text: string, href: string) => `<a href="${href}">${text}</a>`);

    // Bold then italic (bold first so ** is not eaten by the * rule).
    s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    s = s.replace(/__([^_]+)__/g, '<strong>$1</strong>');
    s = s.replace(/(^|[^*])\*([^*\s][^*]*?)\*(?!\*)/g, '$1<em>$2</em>');
    s = s.replace(/(^|[^_])_([^_\s][^_]*?)_(?!_)/g, '$1<em>$2</em>');

    // Restore code spans.
    s = s.replace(/\u0000CODE(\d+)\u0000/g, (_m, i: string) => codeSpans[Number(i)]);
    return s;
}

/**
 * Convert footnote references and definitions to inline `<sup>` anchors plus a
 * trailing `<section class="footnotes">` list.  Only footnotes that are both
 * referenced AND defined are numbered (by order of first reference), matching
 * the common Markdown-Extra behaviour; an orphan reference or definition is
 * left untouched so nothing is silently dropped.
 */
export function convertFootnotes(segment: string): string {
    if (typeof segment !== 'string' || segment.indexOf('[^') === -1) {
        return segment;
    }

    // Collect definitions: a line `[^id]: body...` (body is the rest of line).
    const defRe = /^[ \t]*\[\^([^\]\s]+)\]:[ \t]+(.+?)[ \t]*$/gm;
    const defs = new Map<string, string>();
    let m: RegExpExecArray | null;
    while ((m = defRe.exec(segment)) !== null) {
        if (!defs.has(m[1])) defs.set(m[1], m[2]);
    }
    if (defs.size === 0) return segment;

    // Remove the definition lines from the body.
    let body = segment.replace(defRe, '').replace(/\n{3,}/g, '\n\n');

    // Number references in order of first appearance, but only if defined.
    const order: string[] = [];
    const numberFor = new Map<string, number>();
    const refRe = /\[\^([^\]\s]+)\](?!:)/g;
    body = body.replace(refRe, (whole, id: string) => {
        if (!defs.has(id)) return whole; // orphan reference — leave as source
        let n = numberFor.get(id);
        if (n === undefined) {
            order.push(id);
            n = order.length;
            numberFor.set(id, n);
        }
        return `<sup class="footnote-ref"><a href="#fn-${escapeHtml(id)}" id="fnref-${escapeHtml(id)}">${n}</a></sup>`;
    });

    if (order.length === 0) return segment; // no reference resolved to a def

    // Build the footnotes section as a SINGLE-LINE html block (no blank lines,
    // so marked keeps it as one block-level html token).
    const items = order
        .map((id) => {
            const inner = inlineToHtml(defs.get(id) as string);
            return `<li id="fn-${escapeHtml(id)}">${inner} <a href="#fnref-${escapeHtml(id)}" class="footnote-backref">\u21a9</a></li>`;
        })
        .join('');
    const section = `<section class="footnotes"><hr/><ol>${items}</ol></section>`;

    return `${body.replace(/\s+$/, '')}\n\n${section}\n`;
}

/**
 * Convert PHP-Markdown-Extra definition lists to `<dl>` blocks.  A term line
 * (not a list item, heading, blockquote, or itself a definition line)
 * immediately followed by one or more `: definition` lines becomes
 * `<dl><dt>term</dt><dd>def</dd>...</dl>` on a single line.
 */
export function convertDefinitionLists(segment: string): string {
    if (typeof segment !== 'string' || segment.indexOf('\n:') === -1) {
        return segment;
    }
    const groupRe =
        /^(?![ \t]*(?:[-*+>#]|\d+\.)|[ \t]*:)([^\n][^\n]*)\n((?:[ \t]*:[ \t]+[^\n]*\n?)+)/gm;

    return segment.replace(groupRe, (_whole, term: string, defsBlock: string) => {
        const dt = `<dt>${inlineToHtml(term)}</dt>`;
        const dds = defsBlock
            .split('\n')
            .map((l) => l.replace(/^[ \t]*:[ \t]+/, ''))
            .filter((l) => l.trim() !== '')
            .map((d) => `<dd>${inlineToHtml(d)}</dd>`)
            .join('');
        if (dds === '') return _whole;
        return `<dl>${dt}${dds}</dl>\n`;
    });
}

/**
 * Normalise raw `<details>` blocks so the whole widget (summary + body) is a
 * single blank-line-free html block, preventing CommonMark from splitting the
 * body out at an interior blank line where it would escape the widget.  The
 * summary and body inline markdown are rendered via marked.parseInline.
 */
export function normalizeDetailsBlocks(segment: string): string {
    if (typeof segment !== 'string' || segment.toLowerCase().indexOf('<details') === -1) {
        return segment;
    }
    const detailsRe = /<details\b([^>]*)>([\s\S]*?)<\/details>/gi;
    return segment.replace(detailsRe, (_whole, attrs: string, inner: string) => {
        let summaryHtml = '';
        let rest = inner;
        const sumMatch = inner.match(/<summary\b[^>]*>([\s\S]*?)<\/summary>/i);
        if (sumMatch) {
            summaryHtml = `<summary>${inlineToHtml(sumMatch[1])}</summary>`;
            rest = inner.slice(0, sumMatch.index) + inner.slice((sumMatch.index || 0) + sumMatch[0].length);
        }
        // Body: split on blank lines into paragraphs, render each inline.
        const bodyHtml = rest
            .split(/\n[ \t]*\n/)
            .map((p) => inlineToHtml(p))
            .filter((p) => p !== '')
            .map((p) => `<p>${p}</p>`)
            .join('');
        const safeAttrs = /\bopen\b/i.test(attrs) ? ' open' : '';
        return `<details${safeAttrs}>${summaryHtml}${bodyHtml}</details>`;
    });
}

/** Apply all dialect transforms to one non-fence markdown segment. */
export function convertMarkdownDialects(segment: string): string {
    let out = normalizeDetailsBlocks(segment);
    out = convertFootnotes(out);
    out = convertDefinitionLists(out);
    return out;
}
