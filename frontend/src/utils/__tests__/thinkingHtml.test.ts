/**
 * Raw HTML inside reasoning ("thinking") blocks.
 *
 * WHY THIS FILE EXISTS
 * --------------------
 * ThinkingBlock renders reasoning text with `marked.parse` straight into
 * dangerouslySetInnerHTML.  marked passes raw HTML through, so a model that
 * reasons in DrawIO XML (`<mxfile><diagram>...<mxCell id="0"/>`) put hundreds
 * of unknown elements into the DOM -- and while streaming, every tag was
 * visible as escaped text until its `>` arrived and then vanished as an
 * element, one layout jump per delta, which read as whole-screen flicker.
 *
 * These tests drive the REAL marked lexer/parser with the walkTokens hook
 * ThinkingBlock now passes, because the defect lived in what marked does with
 * html tokens, not in the escaping helper alone.  The streaming-stability test
 * is the one that certifies the flicker is gone: it replays the reasoning
 * text prefix by prefix and asserts the visible text never shrinks.
 */
// marked 16 ships ESM-only from its package "exports"; the UMD build keeps this
// suite self-contained under the current jest transform config (same reasoning
// as thinkingMath.test.ts).
// eslint-disable-next-line @typescript-eslint/no-var-requires
const { marked } = require('marked/lib/marked.umd.js');

import {
    THINKING_MARKED_OPTIONS,
    THINKING_ALLOWED_TAGS,
    escapeHtmlText,
    normalizeAllowedThinkingHtml,
    neutralizeThinkingHtmlToken,
} from '../thinkingHtml';

/** Exactly what ThinkingBlock calls. */
function renderThinking(source: string): string {
    return marked.parse(source, THINKING_MARKED_OPTIONS) as string;
}

/** The pre-fix call, for the negative controls. */
function renderUnfixed(source: string): string {
    return marked.parse(source, { breaks: true, gfm: true }) as string;
}

/** Text a user would see: tags removed, entities decoded. */
function visibleText(html: string): string {
    return html
        .replace(/<!--[\s\S]*?-->/g, '')
        .replace(/<[^>]*>/g, '')
        .replace(/&lt;/g, '<')
        .replace(/&gt;/g, '>')
        .replace(/&quot;/g, '"')
        .replace(/&#39;/g, "'")
        .replace(/&amp;/g, '&');
}

const DRAWIO_REASONING = [
    'Let me write the DrawIO template. A simple vertical panel:',
    '<mxfile><diagram name="Ch N — Title"><mxGraphModel dx="..." dy="..." grid="0" pageWidth="700" pageHeight="900">',
    '<root>',
    '<mxCell id="0"/>',
    '<mxCell id="1" parent="0"/>',
    '<!-- title -->',
    '<mxCell id="t" value="Chapter N: TITLE" style="...;fontSize=20;..." vertex="1" parent="1"><mxGeometry x="20" y="20" width="660" height="60" as="geometry"/></mxCell>',
    '<!-- caption -->',
    '</root></mxGraphModel></diagram></mxfile>',
    '',
    "I'll instruct workers to build 3-6 scene elements.",
].join('\n');

describe('neutralizeThinkingHtmlToken — reasoning that contains XML', () => {
    it('renders unknown tags as literal, visible markup (no DOM elements)', () => {
        const html = renderThinking(DRAWIO_REASONING);
        // No element with an unknown name survives into the output HTML.
        expect(html).not.toMatch(/<mxfile|<diagram|<mxGraphModel|<root|<mxCell|<mxGeometry/);
        // ...but the user can still read the XML the model was reasoning about.
        const seen = visibleText(html);
        expect(seen).toContain('<mxfile><diagram name="Ch N — Title">');
        expect(seen).toContain('<mxCell id="0"/>');
        expect(seen).toContain('</root></mxGraphModel></diagram></mxfile>');
        // The prose around it is untouched.
        expect(seen).toContain("I'll instruct workers to build 3-6 scene elements.");
    });

    it('escapes HTML comments instead of hiding them', () => {
        const html = renderThinking(DRAWIO_REASONING);
        expect(html).not.toContain('<!-- title -->');
        expect(visibleText(html)).toContain('<!-- title -->');
    });

    it('negative control: without the hook the same tags pass through as elements', () => {
        const html = renderUnfixed(DRAWIO_REASONING);
        expect(html).toContain('<mxfile><diagram');
        expect(html).toContain('<mxCell id="0"/>');
        expect(html).toContain('<!-- title -->');
    });
});

describe('streaming stability — the flicker', () => {
    /**
     * Replay the reasoning text as it would arrive, a few characters per
     * delta, and count the deltas on which the visible text got SHORTER.
     * Pre-fix, every tag boundary is one such event (text -> element).
     */
    function countVisibleShrinks(render: (s: string) => string, step = 4): number {
        let prev = '';
        let shrinks = 0;
        for (let i = 40; i <= DRAWIO_REASONING.length; i += step) {
            const v = visibleText(render(DRAWIO_REASONING.slice(0, i)));
            if (v.length < prev.length - 3) shrinks++;
            prev = v;
        }
        return shrinks;
    }

    it('visible text is monotonic while a tag is being streamed in', () => {
        expect(countVisibleShrinks(renderThinking)).toBe(0);
    });

    it('negative control: the unfixed path flips text in and out of view on every tag', () => {
        // Certifies the test measures the real defect: this must be well
        // above zero on the pre-fix render, or the assertion above is vacuous.
        expect(countVisibleShrinks(renderUnfixed)).toBeGreaterThan(5);
    });
});

describe('allowlisted inline formatting still renders', () => {
    it('keeps <b>, <br>, <code> etc. as bare tags', () => {
        const html = renderThinking('a <b>bold</b> word<br>next line');
        expect(html).toContain('<b>bold</b>');
        expect(html).toContain('<br>');
        expect(html).not.toContain('&lt;b&gt;');
    });

    it('strips attributes from allowlisted tags so no handler can ride along', () => {
        const html = renderThinking('x <b onclick="alert(1)" class="y">bold</b>');
        expect(html).toContain('<b>bold</b>');
        expect(html).not.toContain('onclick');
    });

    it('escapes a non-allowlisted tag even when it looks like formatting', () => {
        const html = renderThinking('x <img src=x onerror=alert(1)> y <span style="color:red">z</span>');
        expect(html).not.toMatch(/<img|<span/);
        expect(html).toContain('&lt;img src=x onerror=alert(1)&gt;');
        expect(visibleText(html)).toContain('<span style="color:red">z</span>');
    });

    it('a block-level allowlisted tag (an <hr> on its own line) still renders', () => {
        expect(renderThinking('above\n\n<hr>\n\nbelow')).toContain('<hr>');
    });

    it('a block-level unknown tag is wrapped as an escaped paragraph', () => {
        const html = renderThinking('above\n\n<div class="x">\ninner\n</div>\n\nbelow');
        expect(html).not.toContain('<div');
        expect(html).toMatch(/<p>&lt;div class=&quot;x&quot;&gt;<br>inner<br>&lt;\/div&gt;<\/p>/);
    });
});

describe('code is never double-escaped', () => {
    it('fenced XML stays a single-escaped code block', () => {
        const html = renderThinking('```xml\n<mxfile/>\n```\n');
        expect(html).toContain('<pre><code class="language-xml">&lt;mxfile/&gt;');
        expect(html).not.toContain('&amp;lt;');
    });

    it('an inline code span keeps its single escaping', () => {
        const html = renderThinking('use `<code>` here');
        expect(html).toContain('<code>&lt;code&gt;</code>');
        expect(html).not.toContain('&amp;lt;');
    });
});

describe('helpers', () => {
    it('escapeHtmlText escapes the four significant characters', () => {
        expect(escapeHtmlText('<a href="x">&</a>')).toBe('&lt;a href=&quot;x&quot;&gt;&amp;&lt;/a&gt;');
    });

    it('normalizeAllowedThinkingHtml returns null for comments, unknown tags, and tagless text', () => {
        expect(normalizeAllowedThinkingHtml('<!-- c -->')).toBeNull();
        expect(normalizeAllowedThinkingHtml('<mxCell id="0"/>')).toBeNull();
        expect(normalizeAllowedThinkingHtml('plain')).toBeNull();
    });

    it('normalizeAllowedThinkingHtml lower-cases and strips attributes of allowed tags', () => {
        expect(normalizeAllowedThinkingHtml('<B class="z">x</B>')).toBe('<b>x</b>');
        expect(normalizeAllowedThinkingHtml('<BR/>')).toBe('<br>');
    });

    it('neutralizeThinkingHtmlToken leaves non-html tokens alone', () => {
        const tok: any = { type: 'text', raw: '<x>', text: '<x>' };
        neutralizeThinkingHtmlToken(tok);
        expect(tok.text).toBe('<x>');
    });

    it('the allowlist is inline-only: no container, link, or media tags', () => {
        for (const t of ['div', 'span', 'p', 'a', 'img', 'table', 'script', 'style', 'iframe']) {
            expect(THINKING_ALLOWED_TAGS.has(t)).toBe(false);
        }
    });
});
