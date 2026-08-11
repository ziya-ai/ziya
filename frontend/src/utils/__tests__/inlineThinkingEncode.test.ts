/**
 * Tests for inline literal-tag thinking blocks.
 *
 * These cover the path where reasoning arrives as literal <thinking-data> /
 * <thinking> tags in the TEXT stream rather than as a discrete `thinking`
 * chunk: app/text_delta_processor.py's <reasoning> mapping,
 * app/agents/wrappers/nova_wrapper.py, and conversations recorded before the
 * sidecar existed.
 *
 * The regression being pinned had two user-visible halves:
 *
 *   1. Mid-stream, removeThinkingTags' regex requires the CLOSING tag, so an
 *      unclosed opener survived preprocessing and reached the lexer, rendering
 *      as literal text.
 *   2. When the close arrived, the regex matched and deleted the entire block
 *      without emitting any marker, so the reasoning vanished outright instead
 *      of collapsing into a ThinkingBlock.
 *
 * Two cases below exist because a standalone run of the proposed regexes
 * FAILED them, not by inspection:
 *
 *   - "fence inside reasoning": the shared outsideCodeBlocks helper swaps
 *     fenced blocks for \x00CODEBLOCK<n>\x00 and restores them after the
 *     transform. Encoding the payload inside the transform froze the
 *     placeholder into base64, where the restore pass could not reach it --
 *     reasoning containing a code fence rendered a literal placeholder.
 *   - "prose mention": the $ anchor alone does NOT prevent a mid-sentence
 *     mention from matching, because the tail pattern consumes ordinary prose
 *     to end-of-input. "the <thinking-data> tag is emitted by nova" matched and
 *     swallowed the rest of the line. The line-start anchor is what fixes it.
 */
import {
    encodeThinkingBlocks,
    decodeThinkingPayload,
    removeThinkingTags,
    INLINE_THINKING_MARKER_RE,
} from '../thinkingParser';

/** All markers in document order, decoded. */
const markers = (s: string): { content: string; complete: boolean }[] => {
    const re = new RegExp(INLINE_THINKING_MARKER_RE.source, 'g');
    const out: { content: string; complete: boolean }[] = [];
    let m: RegExpExecArray | null;
    while ((m = re.exec(s)) !== null) {
        out.push({
            content: m[1] ? decodeThinkingPayload(m[1]) : '',
            complete: m[2] === '1',
        });
    }
    return out;
};

const hasRawTag = (s: string): boolean => /<\/?thinking(-data)?>/.test(s);

/** Placeholder leakage, in the emitted text or frozen inside a payload. */
const hasPlaceholder = (s: string): boolean =>
    s.includes('\x00CODEBLOCK')
    || markers(s).some((m) => m.content.includes('\x00CODEBLOCK'));

describe('encodeThinkingBlocks - complete blocks', () => {
    it('replaces a complete thinking-data block with one marker', () => {
        const out = encodeThinkingBlocks(
            'before <thinking-data>some thought</thinking-data> after');
        expect(hasRawTag(out)).toBe(false);
        expect(markers(out)).toEqual([{ content: 'some thought', complete: true }]);
    });

    it('handles the <thinking> spelling emitted by nova_wrapper', () => {
        const out = encodeThinkingBlocks('a <thinking>nova reasoning</thinking> b');
        expect(hasRawTag(out)).toBe(false);
        expect(markers(out)).toEqual([{ content: 'nova reasoning', complete: true }]);
    });

    it('preserves surrounding prose', () => {
        const out = encodeThinkingBlocks(
            'PRECEDING <thinking-data>hidden</thinking-data> FOLLOWING');
        expect(out).toContain('PRECEDING');
        expect(out).toContain('FOLLOWING');
        expect(out).not.toContain('hidden');
    });

    it('does not let <thinking> and <thinking-data> match each other', () => {
        // A backreference is required: a bare alternation would pair
        // <thinking-data> with </thinking> and swallow the boundary between
        // two adjacent blocks of different spellings.
        const out = encodeThinkingBlocks(
            '<thinking-data>d</thinking-data> mid <thinking>t</thinking>');
        expect(markers(out).map((m) => m.content)).toEqual(['d', 't']);
        expect(out).toContain('mid');
    });

    it('encodes multiple blocks independently', () => {
        const out = encodeThinkingBlocks(
            '<thinking-data>first</thinking-data> A <thinking-data>second</thinking-data> B');
        expect(markers(out).map((m) => m.content)).toEqual(['first', 'second']);
        expect(out).toContain('A');
        expect(out).toContain('B');
    });

    it('handles an empty thinking block without leaving a raw tag', () => {
        const out = encodeThinkingBlocks('x <thinking-data></thinking-data> y');
        expect(hasRawTag(out)).toBe(false);
        expect(markers(out)).toEqual([{ content: '', complete: true }]);
    });
});

describe('encodeThinkingBlocks - payload fidelity', () => {
    it('round-trips a code fence inside the reasoning', () => {
        // Regression: the placeholder used to protect fenced blocks was frozen
        // into the base64 payload, so the restore pass could never reach it and
        // a literal \x00CODEBLOCK0\x00 was rendered to the user.
        const inner = '## Heading\n\n- point one\n\n```js\nconst a = 1;\n```';
        const out = encodeThinkingBlocks(`<thinking-data>${inner}</thinking-data>`);
        expect(markers(out)).toEqual([{ content: inner, complete: true }]);
        expect(hasPlaceholder(out)).toBe(false);
    });

    it('round-trips two code fences inside the reasoning', () => {
        const inner = 'see:\n```py\nx = 1\n```\nand\n```py\ny = 2\n```';
        const out = encodeThinkingBlocks(`<thinking-data>${inner}</thinking-data>`);
        expect(markers(out)).toEqual([{ content: inner, complete: true }]);
    });

    it('round-trips a code fence inside an unclosed block mid-stream', () => {
        const inner = 'partial:\n```py\nx = 1\n```\nmore';
        const out = encodeThinkingBlocks(
            `<thinking-data>${inner}`, { streaming: true });
        expect(markers(out)).toEqual([{ content: inner, complete: false }]);
        expect(hasPlaceholder(out)).toBe(false);
    });

    it('round-trips non-ASCII content', () => {
        // Bare btoa throws above U+00FF, so the UTF-8 round-trip in the
        // encoder is load-bearing rather than decorative.
        const inner = 'Ω ≈ 3.14 — naïve café 日本語 🎼';
        const out = encodeThinkingBlocks(`<thinking-data>${inner}</thinking-data>`);
        expect(markers(out)).toEqual([{ content: inner, complete: true }]);
    });

    it('round-trips content containing angle brackets', () => {
        // Marker delimiters are U+27E8/U+27E9, not ASCII < >, so ordinary
        // brackets in the reasoning cannot terminate the marker early.
        const inner = 'if (a < b && c > d) { return <div/>; }';
        const out = encodeThinkingBlocks(`<thinking-data>${inner}</thinking-data>`);
        expect(markers(out)).toEqual([{ content: inner, complete: true }]);
    });

    it('emits a marker matched by the exported regex', () => {
        expect(encodeThinkingBlocks('<thinking-data>x</thinking-data>'))
            .toMatch(INLINE_THINKING_MARKER_RE);
    });
});

describe('encodeThinkingBlocks - unclosed opener (mid-stream)', () => {
    it('encodes a trailing unclosed opener at a line start', () => {
        // This is the renderer's state on every chunk between the opener
        // arriving and the close arriving -- symptom 1 of the regression.
        const out = encodeThinkingBlocks(
            'answer so far\n<thinking-data>partial reason', { streaming: true });
        expect(hasRawTag(out)).toBe(false);
        expect(markers(out)).toEqual([{ content: 'partial reason', complete: false }]);
    });

    it('encodes an opener at offset 0', () => {
        const out = encodeThinkingBlocks('<thinking-data>at very start', { streaming: true });
        expect(markers(out)).toEqual([{ content: 'at very start', complete: false }]);
    });

    it('encodes an indented opener at a line start', () => {
        const out = encodeThinkingBlocks('answer\n  <thinking-data>indented', { streaming: true });
        expect(markers(out)).toEqual([{ content: 'indented', complete: false }]);
    });

    it('marks a closed block complete even while streaming', () => {
        // A block collapses when ITS close arrives, not when the turn ends --
        // there is one block per tool-calling iteration.
        const out = encodeThinkingBlocks(
            '<thinking-data>done</thinking-data> now answering', { streaming: true });
        expect(markers(out)).toEqual([{ content: 'done', complete: true }]);
    });

    it('encodes a closed block and a following unclosed one in one pass', () => {
        const out = encodeThinkingBlocks(
            '<thinking-data>one</thinking-data> text\n<thinking-data>two so far',
            { streaming: true });
        expect(hasRawTag(out)).toBe(false);
        expect(markers(out)).toEqual([
            { content: 'one', complete: true },
            { content: 'two so far', complete: false },
        ]);
    });

    it('leaves an unclosed opener alone when not streaming', () => {
        // Deliberate: in committed content an unclosed opener is far more
        // likely to be prose about the tag than truncated reasoning.
        const src = 'discussing <thinking-data> in prose';
        expect(encodeThinkingBlocks(src)).toBe(src);
        expect(markers(encodeThinkingBlocks(src))).toEqual([]);
    });

    it('does not match a mid-sentence prose mention', () => {
        // The $ anchor alone did NOT prevent this: the tail pattern consumes
        // ordinary prose to end-of-input, so this input matched and swallowed
        // the rest of the line. The line-start anchor is the actual fix.
        const out = encodeThinkingBlocks(
            'the <thinking-data> tag is emitted by nova', { streaming: true });
        expect(markers(out)).toEqual([]);
        expect(out).toContain('emitted by nova');
    });

    it('does not match a mention that follows text on its own line', () => {
        const out = encodeThinkingBlocks(
            'A sentence.\nThe <thinking-data> tag matters.', { streaming: true });
        expect(markers(out)).toEqual([]);
    });
});

describe('encodeThinkingBlocks - code block protection', () => {
    it('leaves tags inside a closed fenced block untouched', () => {
        const content = '```ts\nconst t = "<thinking-data>x</thinking-data>";\n```';
        expect(encodeThinkingBlocks(content)).toBe(content);
        expect(encodeThinkingBlocks(content, { streaming: true })).toBe(content);
    });

    it('encodes outside a fence while preserving content inside it', () => {
        const out = encodeThinkingBlocks(
            '<thinking-data>real</thinking-data>\n\n```ts\n// <thinking-data>doc</thinking-data>\n```');
        expect(markers(out)).toEqual([{ content: 'real', complete: true }]);
        expect(out).toContain('// <thinking-data>doc</thinking-data>');
    });

    it('does not corrupt a diff that adds a thinking tag', () => {
        const content = '```diff\n+<thinking-data>added</thinking-data>\n```';
        expect(encodeThinkingBlocks(content)).toBe(content);
    });

    it('documents the unclosed-fence gap rather than asserting safety', () => {
        // An unterminated fence has no closer for the protection regex to pair
        // with, so a tag at a line start inside one is still eligible while
        // streaming. Recorded as a real limitation, not a passing guarantee:
        // if a future change closes it, this test fails and the note is
        // revisited. Requires streaming + unterminated fence + line-start tag.
        const content = '```ts\n<thinking-data>';
        expect(encodeThinkingBlocks(content, { streaming: true })).not.toBe(content);
    });
});

describe('encodeThinkingBlocks - pass-through', () => {
    it('returns content unchanged when no thinking tags are present', () => {
        const content = '# Title\n\nSome prose with `code` and a [link](x).';
        expect(encodeThinkingBlocks(content)).toBe(content);
    });

    it('handles empty input', () => {
        expect(encodeThinkingBlocks('')).toBe('');
    });

    it('does not react to the sidecar positional marker', () => {
        // Discrete-chunk reasoning uses ⟨THINKING:turn:idx⟩ and resolves from
        // session state; it must not be confused with the embedded form.
        const content = 'answer \u27E8THINKING:abc1:0\u27E9 more';
        expect(encodeThinkingBlocks(content)).toBe(content);
        expect(markers(content)).toEqual([]);
    });
});

describe('interaction with removeThinkingTags', () => {
    it('leaves nothing for removeThinkingTags to delete', () => {
        // Ordering guard: the renderer encodes first, then strips. If the strip
        // still found the block it would delete the reasoning -- symptom 2.
        const stripped = removeThinkingTags(
            encodeThinkingBlocks('a <thinking-data>keep me</thinking-data> b'));
        expect(markers(stripped)).toEqual([{ content: 'keep me', complete: true }]);
    });

    it('still strips fence-based thinking blocks after encoding', () => {
        // removeThinkingTags stays in the pipeline solely for the
        // ````thinking: fences from mcpToolHandlers.ts, which the encoder
        // deliberately does not touch.
        const content = '````thinking:step-1\nfence thought\n````\n\n## Answer';
        const out = removeThinkingTags(encodeThinkingBlocks(content));
        expect(out).not.toContain('fence thought');
        expect(out).toContain('## Answer');
    });

    it('preserves both forms in one message', () => {
        const content =
            '<thinking-data>tag thought</thinking-data>\n\n'
            + '````thinking:step-1\nfence thought\n````\n\nFinal answer.';
        const out = removeThinkingTags(encodeThinkingBlocks(content));
        expect(markers(out)).toEqual([{ content: 'tag thought', complete: true }]);
        expect(out).not.toContain('fence thought');
        expect(out).toContain('Final answer.');
    });
});

describe('regression: no raw tag reaches the renderer', () => {
    // Any opener surviving preprocessing reaches the lexer and renders
    // literally -- the reported symptom. These assert the invariant directly.
    const cases: [string, string, boolean][] = [
        ['mid-stream opener', 'text\n<thinking-data>reason', true],
        ['closed block streaming', 'text <thinking-data>reason</thinking-data> more', true],
        ['closed block committed', 'text <thinking-data>reason</thinking-data> more', false],
        ['nova closed block', 'text <thinking>reason</thinking> more', false],
        ['two closed blocks', '<thinking-data>a</thinking-data> x <thinking-data>b</thinking-data>', true],
    ];

    it.each(cases)('%s leaves no literal tag', (_label, input, streaming) => {
        expect(hasRawTag(encodeThinkingBlocks(input, { streaming }))).toBe(false);
    });

    it.each(cases)('%s does not discard the reasoning', (_label, input, streaming) => {
        const found = markers(encodeThinkingBlocks(input, { streaming }));
        expect(found.length).toBeGreaterThan(0);
        expect(found.every((m) => m.content.length > 0)).toBe(true);
    });
});
