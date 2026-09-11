/**
 * @jest-environment jsdom
 *
 * D-017 (chat-message w3-05 / w3-06): less-travelled markdown dialect coverage.
 *
 * These assert the direction of the fix: WITHOUT the transforms the constructs
 * leak their raw source (`[^a]`, `Term\n: def`, a blank-line-split <details>);
 * WITH them they normalise to raw HTML the renderer's raw-HTML path accepts,
 * and the inline-semantic tags they (and w3-05/w3-06) rely on survive the
 * DOMPurify boundary that the render pre-gate must stay in sync with.
 */
import {
    convertFootnotes,
    convertDefinitionLists,
    normalizeDetailsBlocks,
    convertMarkdownDialects,
} from '../markdownDialects';
import { sanitizeModelHtml } from '../domSanitize';

describe('D-017 footnotes (w3-05)', () => {
    const src =
        'Footnote reference here[^a] and a second one[^b].\n\n' +
        '[^a]: First footnote body.\n' +
        '[^b]: Second footnote body with `code`.\n';

    it('leaks raw footnote source before conversion', () => {
        // Precondition: the raw input contains the un-rendered footnote syntax.
        expect(src).toContain('[^a]');
        expect(src).toContain('[^a]: First footnote body.');
    });

    it('rewrites references to numbered <sup> anchors and appends a section', () => {
        const out = convertFootnotes(src);
        // References became inline superscript anchors, numbered by first use.
        expect(out).toMatch(/<sup class="footnote-ref"><a href="#fn-a" id="fnref-a">1<\/a><\/sup>/);
        expect(out).toMatch(/<sup class="footnote-ref"><a href="#fn-b" id="fnref-b">2<\/a><\/sup>/);
        // Definition lines are gone from the body.
        expect(out).not.toContain('[^a]: First footnote body.');
        // A single-line footnotes section was appended (no interior blank line).
        expect(out).toContain('<section class="footnotes">');
        expect(out).toContain('<li id="fn-a">');
        // Inline markdown inside the body is rendered (backtick code).
        expect(out).toContain('<code>code</code>');
        const section = out.slice(out.indexOf('<section'));
        expect(section).not.toMatch(/\n[ \t]*\n/); // stays one marked block token
    });

    it('leaves an orphan reference (no matching definition) untouched', () => {
        const orphan = 'See[^missing] only.';
        expect(convertFootnotes(orphan)).toBe(orphan);
    });
});

describe('D-017 definition lists (w3-05)', () => {
    const src = 'Term A\n: Definition of A\n\nTerm B\n: Definition of B\n';

    it('leaks raw definition-list source before conversion', () => {
        expect(src).toContain(': Definition of A');
    });

    it('converts term / ": def" pairs to <dl><dt><dd>', () => {
        const out = convertDefinitionLists(src);
        expect(out).toContain('<dl><dt>Term A</dt><dd>Definition of A</dd></dl>');
        expect(out).toContain('<dl><dt>Term B</dt><dd>Definition of B</dd></dl>');
        expect(out).not.toContain(': Definition of A');
    });

    it('does not misfire on ordinary prose or list items', () => {
        const prose = 'A normal sentence.\nAnother normal sentence.\n';
        expect(convertDefinitionLists(prose)).toBe(prose);
        const list = '- item one\n- item two\n';
        expect(convertDefinitionLists(list)).toBe(list);
    });
});

describe('D-017 raw <details> blank-line splitting (w3-06)', () => {
    const src =
        '<details>\n<summary>Click to expand details element</summary>\n\n' +
        'Hidden body paragraph with `code` inside.\n\n</details>\n';

    it('the raw source has a blank line between summary and body (the split trigger)', () => {
        const between = src.slice(src.indexOf('</summary>'), src.indexOf('Hidden'));
        expect(between).toMatch(/\n[ \t]*\n/);
    });

    it('collapses the widget into one blank-line-free block with rendered body', () => {
        const out = normalizeDetailsBlocks(src);
        expect(out).toContain('<details><summary>Click to expand details element</summary>');
        expect(out).toContain('<p>Hidden body paragraph with <code>code</code> inside.</p>');
        expect(out).toContain('</details>');
        // No interior blank line survives, so marked keeps it as one html token.
        const block = out.slice(out.indexOf('<details'), out.indexOf('</details>') + 10);
        expect(block).not.toMatch(/\n[ \t]*\n/);
    });
});

describe('D-017 inline semantic tags survive the sanitizer (pre-gate sync)', () => {
    // The render pre-gate (knownHtmlTags) must stay in sync with what DOMPurify
    // keeps; these tags are what w3-05/w3-06 depend on.
    it.each([
        ['sub', '<sub>2</sub>'],
        ['sup', '<sup>2</sup>'],
        ['kbd', '<kbd>Ctrl</kbd>'],
        ['mark', '<mark>hi</mark>'],
        ['abbr', '<abbr title="x">API</abbr>'],
        ['small', '<small>small</small>'],
        ['section', '<section class="footnotes"><ol><li>x</li></ol></section>'],
    ])('keeps <%s>', (tag, html) => {
        expect(sanitizeModelHtml(html)).toContain(`<${tag}`);
    });
});

describe('D-017 combined dialect pass is fence-agnostic input->HTML', () => {
    it('applies footnotes + deflists + details together', () => {
        const out = convertMarkdownDialects(
            'Ref[^a].\n\nTerm\n: Def\n\n<details>\n<summary>S</summary>\n\nBody\n\n</details>\n\n[^a]: body\n'
        );
        expect(out).toContain('<sup class="footnote-ref">');
        expect(out).toContain('<dl><dt>Term</dt><dd>Def</dd></dl>');
        expect(out).toContain('<details><summary>S</summary>');
    });
});
