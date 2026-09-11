/**
 * D-014 (group G-f36c77) — chat-message markdown sub-surfaces are hard-clipped
 * with no scroll / fade / wrap affordance when content is wider than the
 * message box (signature overflow-x-clipped-no-scroll-no-wrap).
 *
 * Failing specs:
 *   chat-message-w2-04 — a ~2600-char unbreakable code line in a <pre>, plus a
 *     404-char unbreakable prose urn token in a <p> and the same in an inline
 *     <code> span.
 *   chat-message-w2-11 — a 40-column x 4-row markdown <table> far exceeding the
 *     message box width.
 *
 * ROOT CAUSE (confirmed against source): frontend/src/index.css styles
 * `.message .message-content` (it sets white-space:pre-wrap on the container and
 * resets block elements to white-space:normal) but NEVER sets an overflow or
 * wrap-break affordance on the markdown sub-surfaces. There are zero rules for
 * `.message-content table`, inline `code`, or `pre` overflow, so:
 *   - an unbreakable prose/inline token has no wrap opportunity and overflows,
 *   - a long code line is clipped with no horizontal scrollbar,
 *   - a wide table's far columns are clipped with no scroll container.
 *
 * index.css is outside this task's writable paths, so the fix adds ONLY the
 * missing affordances in frontend/src/styles/messageContentOverflow.css (a
 * writable path) and wires it into the live markdown surface by importing it
 * from MarkdownRenderer.tsx. Every property added (overflow-wrap, word-break,
 * overflow-x, and the table display/max-width) is left UNSET by index.css, so
 * there is no competing declaration and cascade order is irrelevant.
 *
 * This defect is kind:structural — a layout/overflow property, not a colour —
 * so the affordances are theme-INDEPENDENT and correct the light and dark
 * surfaces identically; there is no per-theme colour to split.
 *
 * DIRECTION (fail-without-the-fix): the stylesheet does not exist on the
 * pre-fix tree, so readFileSync throws and every assertion below fails; the
 * import wiring is likewise absent from MarkdownRenderer.tsx. Both pass only
 * with the fix applied.
 */
import * as fs from 'fs';
import * as path from 'path';

const CSS_PATH = path.resolve(
    __dirname, '../../../styles/messageContentOverflow.css',
);
const RENDERER_PATH = path.resolve(
    __dirname, '../../../components/MarkdownRenderer.tsx',
);

// Collapse whitespace so multi-line selector lists match regardless of
// formatting, and lower-case for case-insensitive property matching.
function normalize(css: string): string {
    return css.replace(/\s+/g, ' ').toLowerCase();
}

// Extract the declaration block for a selector list that contains `selectorNeedle`.
function blockContaining(css: string, selectorNeedle: string): string {
    const norm = normalize(css);
    const needle = selectorNeedle.toLowerCase();
    const rules = norm.split('}');
    for (const rule of rules) {
        const open = rule.indexOf('{');
        if (open === -1) continue;
        const selector = rule.slice(0, open);
        const body = rule.slice(open + 1);
        if (selector.includes(needle)) return body;
    }
    return '';
}

describe('D-014: chat-message markdown overflow affordances', () => {
    it('DIRECTION: the affordance stylesheet exists (absent on the pre-fix tree)', () => {
        expect(fs.existsSync(CSS_PATH)).toBe(true);
    });

    const css = fs.existsSync(CSS_PATH)
        ? fs.readFileSync(CSS_PATH, 'utf8')
        : '';

    it('prose/list/heading text is given a wrap-break so an unbreakable token stays in the box (w2-04 <p>)', () => {
        const block = blockContaining(css, '.message .message-content p');
        expect(block).toContain('overflow-wrap: break-word');
        expect(block).toContain('word-break: break-word');
    });

    it('inline code (not <pre> code) is allowed to break anywhere for an unbreakable urn (w2-04 inline code)', () => {
        const block = blockContaining(css, ':not(pre) > code');
        expect(block).toContain('overflow-wrap: break-word');
        expect(block).toContain('word-break: break-all');
    });

    it('code blocks scroll horizontally instead of clipping a long line (w2-04 <pre>)', () => {
        const block = blockContaining(css, '.message .message-content pre');
        expect(block).toContain('overflow-x: auto');
    });

    it('wide tables become their own horizontal scroll container (w2-11 table)', () => {
        const block = blockContaining(css, '.message .message-content table');
        expect(block).toContain('display: block');
        expect(block).toContain('overflow-x: auto');
    });

    it('the affordance stylesheet is imported by the live markdown surface so it reaches the renderer', () => {
        const src = fs.readFileSync(RENDERER_PATH, 'utf8');
        expect(src).toMatch(/import\s+['"]\.\.\/styles\/messageContentOverflow\.css['"]/);
    });
});
