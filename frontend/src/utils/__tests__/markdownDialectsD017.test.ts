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

describe('D-017 regression: <details> body containing inline `code` (w3-06)', () => {
    // The w3-06 spec body is "Hidden body paragraph with `code` inside." — the
    // inline code span in the body is the trigger. convertMarkdownDialects must
    // normalise the WHOLE <details>..</details> widget even though its body
    // crosses an inline code span. The earlier pipeline ran normalizeDetailsBlocks
    // *inside* applyOutsideCodeSpans, which split the segment at the backtick so
    // the open tag and close tag landed in different segments and NEITHER matched
    // — the body then escaped the widget and rendered as an ordinary paragraph.
    const src =
        '### Raw HTML surfaces\n\n' +
        '<details>\n<summary>Click to expand details element</summary>\n\n' +
        'Hidden body paragraph with `code` inside.\n\n' +
        '</details>\n\n' +
        'Trailing paragraph.\n';

    it('keeps the code-bearing body INSIDE the collapsed widget', () => {
        const out = convertMarkdownDialects(src);
        // The whole widget must be one blank-line-free block...
        expect(out).toContain('<details><summary>Click to expand details element</summary>');
        expect(out).toContain('</details>');
        // ...with the body (including the rendered inline code) enclosed by it,
        // not leaked out as a sibling paragraph.
        const block = out.slice(out.indexOf('<details'), out.indexOf('</details>') + '</details>'.length);
        expect(block).toContain('<p>Hidden body paragraph with <code>code</code> inside.</p>');
        // No raw backticks survive anywhere (the code span was rendered, not torn out).
        expect(out).not.toContain('`code`');
        // No interior blank line, so marked keeps it as a single html token.
        expect(block).not.toMatch(/\n[ \t]*\n/);
    });

    it('still leaves a literal <details> written inside inline code untouched', () => {
        // A bare `<details>` mention in prose code has no closing tag in the
        // same span, so it must survive verbatim (no normalisation).
        const out = convertMarkdownDialects('Use `<details>` to make a collapsible block.\n');
        expect(out).toContain('`<details>`');
        expect(out).not.toContain('<summary>');
    });
});

describe('regression: raw HTML inside a <details> widget is preserved, not escaped', () => {
    // A styled error widget: <span>s in the summary, a styled <div> body.
    // normalizeDetailsBlocks used to escape every tag inside the widget into
    // literal "&lt;span&gt;" text and drop the details/summary attributes.
    const src =
        '<details style="margin: 16px 0; background: #FFF2F0;">\n' +
        '<summary style="cursor: pointer; font-weight: bold; color: #CF1322;">\n' +
        '<span>:x:</span>\n<span>Error Details</span>\n' +
        '<span style="font-weight: normal; opacity: 0.7;">(Click to expand)</span>\n' +
        '</summary>\n' +
        '<div style="white-space: pre-wrap; font-family: monospace; color: #8C1F1F;">\n' +
        'Error: An error occurred (AccessDeniedException) when calling the InvokeModel operation: ' +
        'User: arn:aws:sts::123456789012:assumed-role/Foo/bar is not authorized to perform: bedrock:InvokeModel\n' +
        '</div>\n</details>';

    it('keeps summary and body tags as real HTML', () => {
        const out = normalizeDetailsBlocks(src);
        // Positive: the widget was actually rewritten (one blank-line-free block).
        expect(out).toMatch(/^<details[^>]*>.*<\/details>$/s);
        expect(out).not.toMatch(/\n[ \t]*\n/);
        // No escaped tag soup anywhere in the output.
        expect(out).not.toContain('&lt;span');
        expect(out).not.toContain('&lt;div');
        expect(out).not.toContain('&lt;/');
        // The tags themselves survive.
        expect(out).toContain('<span>Error Details</span>');
        expect(out).toContain('<span style="font-weight: normal; opacity: 0.7;">(Click to expand)</span>');
        expect(out).toMatch(/<div style="white-space: pre-wrap;[^"]*">\s*Error: An error occurred/);
    });

    it('keeps the details/summary attributes so the widget styling is not lost', () => {
        const out = normalizeDetailsBlocks(src);
        expect(out).toMatch(/^<details style="margin: 16px 0; background: #FFF2F0;">/);
        expect(out).toContain('<summary style="cursor: pointer; font-weight: bold; color: #CF1322;">');
    });

    it('does not wrap a block-level body element in an invalid <p>', () => {
        const out = normalizeDetailsBlocks(src);
        expect(out).not.toContain('<p><div');
        expect(out).toContain('</div></details>');
    });

    it('still renders through the DOMPurify boundary with tags intact', () => {
        const clean = sanitizeModelHtml(normalizeDetailsBlocks(src));
        expect(clean).toContain('<summary');
        expect(clean).toContain('<span>Error Details</span>');
        expect(clean).toContain('bedrock:InvokeModel');
        expect(clean).not.toContain('&lt;span');
    });

    it('still escapes literal angle-bracket text that is not a tag', () => {
        const out = normalizeDetailsBlocks('<details><summary>s</summary>\n\na < b and b > c\n\n</details>');
        expect(out).toContain('a &lt; b and b &gt; c');
    });

    it('does not misread * or _ inside a tag attribute as emphasis', () => {
        const out = normalizeDetailsBlocks(
            '<details><summary>s</summary>\n\n<span style="font-family: a_b, c_d">x</span>\n\n</details>');
        expect(out).toContain('<span style="font-family: a_b, c_d">x</span>');
        expect(out).not.toContain('<em>');
    });
});
