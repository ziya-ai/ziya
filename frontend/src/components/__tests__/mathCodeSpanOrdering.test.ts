/**
 * Ordering contract: math preprocessing must not reach inside inline code spans.
 *
 * WHY THIS FILE EXISTS
 * --------------------
 * The math passes in MarkdownRenderer run BEFORE marked's lexer, and they split
 * their input on FENCED blocks only. Inline code spans were not excluded, so
 * writing `$x$` in backticks produced a marker (inline) or an encoded <div>
 * (display) INSIDE the code span, and the user saw renderer internals instead
 * of the literal text they typed:
 *
 *   inline : "use `$\#_E$` here"  -> "use `⟨MATH_INLINE_B64:XCNfRQ==⟩` here"
 *   display: "use `$$x$$` here"   -> "use `<div class=math-display-encoded …>` here"
 *
 * This is an ORDERING bug, distinct from the marker-ENCODING bug covered by
 * utils/__tests__/inlineMathMarker.test.ts. Base64 encoding made it more
 * visible (an opaque blob rather than near-correct LaTeX) but did not cause it.
 *
 * The tests below mirror the renderer's real pass structure — outer fence
 * split, inner code-span guard — so they pin the composition of the two
 * guards, which is where a regression would actually land.
 */
// eslint-disable-next-line @typescript-eslint/no-var-requires
const { marked } = require('marked/lib/marked.umd.js');

import { applyOutsideCodeSpans } from '../fenceScanner';
import {
    processInlineMath,
    decodeInlineMathMarker,
    isInlineMathMarker,
    MATH_INLINE_MARKER_PREFIX,
    MATH_INLINE_MARKER_SPLIT_RE,
} from '../../utils/inlineMathClassifier';

/** Mirrors the renderer's inline pass: fence split (outer), span guard (inner). */
function inlinePass(md: string): string {
    return md.split(/(```[^\n]*\n[\s\S]*?```)/g).map((part, idx) => {
        if (idx % 2 === 1 && part.startsWith('```')) return part;
        return applyOutsideCodeSpans(part, processInlineMath);
    }).join('');
}

/** Mirrors the renderer's display pass with the same two guards. */
function displayPass(md: string): string {
    const encode = (s: string) => btoa(unescape(encodeURIComponent(s)));
    return md.split(/(```[^\n]*\n[\s\S]*?```)/g).map((part, idx) => {
        if (idx % 2 === 1 && part.startsWith('```')) return part;
        return applyOutsideCodeSpans(part, seg =>
            seg.replace(/\$\$([\s\S]*?)\$\$/g, (_m, inner) =>
                `<div class="math-display-encoded" data-math="${encode(inner.trim())}"></div>`));
    }).join('');
}

const markerCount = (s: string): number =>
    s.split(MATH_INLINE_MARKER_PREFIX).length - 1;

describe('inline math respects code spans', () => {
    it('leaves $...$ inside a code span byte-identical', () => {
        const md = 'use `$\\#_E$` here';
        expect(inlinePass(md)).toBe(md);
    });

    it('still converts real math on the same line as a code span', () => {
        const out = inlinePass('math $x$ and code `$y$`');
        expect(markerCount(out)).toBe(1);
        expect(out).toContain('`$y$`');
    });

    it('is order-independent (code span before the math)', () => {
        const out = inlinePass('code `$y$` then math $x$');
        expect(markerCount(out)).toBe(1);
        expect(out).toContain('`$y$`');
    });

    it('respects a multi-backtick span containing a lone tick', () => {
        const md = 'a ``$x$ has ` tick`` b';
        expect(inlinePass(md)).toBe(md);
    });

    it('does not reach into fenced blocks (pre-existing guard still holds)', () => {
        const md = 'before $x$\n```js\nconst a = "$y$";\n```\nafter $z$';
        const out = inlinePass(md);
        expect(out).toContain('const a = "$y$";');
        expect(markerCount(out)).toBe(2);
    });

    it('does not double-process a code span nested inside a fence', () => {
        const md = '```md\nuse `$x$` inline\n```';
        expect(inlinePass(md)).toBe(md);
    });

    it('through the real lexer: the span stays code, the loose math renders', () => {
        const out = inlinePass('use `$\\#_E$` and math $\\#_E$');
        const tokens = marked.lexer(out)[0].tokens;

        const codespans = tokens
            .filter((t: any) => t.type === 'codespan')
            .map((t: any) => t.text);
        expect(codespans).toEqual(['$\\#_E$']);

        const joined = tokens.map((t: any) => t.text || '').join('');
        const decoded = joined.split(MATH_INLINE_MARKER_SPLIT_RE)
            .filter((p: string) => p && isInlineMathMarker(p))
            .map((p: string) => decodeInlineMathMarker(p));
        expect(decoded).toEqual(['\\#_E']);
    });
});

describe('display math respects code spans', () => {
    it('leaves $$...$$ inside a code span byte-identical', () => {
        const md = 'use `$$x$$` here';
        expect(displayPass(md)).toBe(md);
    });

    it('still converts display math outside the span on the same line', () => {
        const out = displayPass('a $$x$$ b `$$y$$` c');
        expect(out).toContain('data-math=');
        expect(out).toContain('`$$y$$`');
        // Exactly one encoded div: the one outside the code span.
        expect(out.split('math-display-encoded').length - 1).toBe(1);
    });
});
