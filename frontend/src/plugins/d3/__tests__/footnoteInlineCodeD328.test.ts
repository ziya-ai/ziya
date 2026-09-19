/**
 * @jest-environment jsdom
 *
 * D-328 (chat-message w3-05): an inline `code` span inside a footnote DEFINITION
 * body was torn out of the footnote and leaked as an orphan literal paragraph.
 *
 * Root cause: the renderer applied the dialect transforms via
 *   applyOutsideFences(part => applyOutsideCodeSpans(part, convertMarkdownDialects))
 * so the inline-code guard split the segment at the `code` span BEFORE
 * convertFootnotes ran. The footnote definition line
 *   [^b]: Second footnote body with `code`.
 * was thereby truncated to "...with " and the `code` span re-emitted verbatim
 * as a sibling — an orphan literal after the footnotes section.
 *
 * Fix: convertMarkdownDialects now protects inline code spans INTERNALLY —
 * <details>/definition-list transforms run outside code spans, but footnotes
 * run on the full segment (definition bodies keep their trailing `code`), and
 * convertFootnotes skips references that fall inside code spans itself. The
 * renderer no longer wraps the call in applyOutsideCodeSpans.
 *
 * These assert the direction of the fix: WITHOUT it a footnote reference inside
 * a code span is wrongly numbered and a definition body's code is lost; WITH it
 * the reference stays literal and the definition body's code renders inside the
 * footnotes list item.
 */
import { convertFootnotes, convertMarkdownDialects } from '../../../utils/markdownDialects';
import { applyOutsideCodeSpans } from '../../../components/fenceScanner';

describe('D-328 footnote definition body inline code (w3-05)', () => {
    it('keeps a footnote definition body ending in `code` intact via convertFootnotes', () => {
        const src =
            'A ref[^b].\n\n' +
            '[^b]: Second footnote body with `code`.\n';
        const out = convertFootnotes(src);
        // reference became a numbered superscript anchor
        expect(out).toMatch(/<sup class="footnote-ref"><a href="#fn-b" id="fnref-b">1<\/a><\/sup>/);
        // the inline code inside the definition body renders inside the list item
        expect(out).toContain('<code>code</code>');
        // nothing leaked as a bare backtick code span after the section
        const afterSection = out.slice(out.indexOf('</section>'));
        expect(afterSection).not.toContain('`code`');
        expect(out).not.toContain('with `code`');
    });

    it('does not convert a footnote reference written inside an inline code span', () => {
        // Both a real reference and a literal `[^a]` written in backticks, with a
        // matching definition present. Only the real reference must convert; the
        // one in the code span stays byte-identical.
        const src =
            'Real ref[^a] and a literal `[^a]` token.\n\n' +
            '[^a]: The body.\n';
        const out = convertFootnotes(src);
        // exactly one superscript anchor (the real reference)
        const supCount = (out.match(/<sup class="footnote-ref">/g) || []).length;
        expect(supCount).toBe(1);
        // the code-span reference survived verbatim
        expect(out).toContain('`[^a]`');
    });

    it('end-to-end dialect pass no longer needs (and must not use) an outer code-span split', () => {
        const src =
            'A ref[^b].\n\n' +
            '[^b]: Second footnote body with `code`.\n';
        // The fixed wiring: convertMarkdownDialects on the full segment.
        const fixed = convertMarkdownDialects(src);
        expect(fixed).toContain('<code>code</code>');
        expect(fixed.slice(fixed.indexOf('</section>'))).not.toContain('`code`');

        // The pre-fix wiring (outer applyOutsideCodeSpans) demonstrably leaked
        // the span, so the two compositions differ — this is why the wrapper was
        // removed at the call site.
        const preFix = applyOutsideCodeSpans(src, convertMarkdownDialects);
        expect(preFix).not.toBe(fixed);
        // Pre-fix: the definition body was truncated so the code never rendered
        // and the span leaked as an orphan literal backtick run.
        expect(preFix).not.toContain('<code>code</code>');
        expect(preFix).toContain('`code`');
    });
});
