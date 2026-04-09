/**
 * Tests for the double-tilde-only strikethrough override in MarkdownRenderer.
 *
 * The marked.js GFM mode by default treats both ~text~ (single tilde) and
 * ~~text~~ (double tilde) as strikethrough.  Single-tilde matching causes
 * false positives when tildes are used conversationally (e.g. "~32px",
 * "~10px").  The override restricts strikethrough to double tildes only.
 */

// Regex extracted from the marked.use() extension in MarkdownRenderer.tsx
const doubleTildeStart = /~~(?=[^\s~])/;
const doubleTildeTokenizer = /^~~(?=[^\s~])([\s\S]*?[^\s~])~~(?=[^~]|$)/;

describe('Double-tilde strikethrough override', () => {
    describe('start detection', () => {
        it('should detect ~~ at start of strikethrough', () => {
            expect(doubleTildeStart.test('~~deleted~~')).toBe(true);
        });

        it('should not detect single ~ as strikethrough start', () => {
            // A single ~ followed by a non-tilde is NOT a start
            const input = '~32px from left';
            const match = doubleTildeStart.exec(input);
            expect(match).toBeNull();
        });

        it('should not detect ~~ followed by whitespace', () => {
            expect(doubleTildeStart.test('~~ spaced')).toBe(false);
        });
    });

    describe('tokenizer matching', () => {
        it('should match ~~double tilde~~ strikethrough', () => {
            const match = doubleTildeTokenizer.exec('~~struck out~~');
            expect(match).not.toBeNull();
            expect(match![1]).toBe('struck out');
        });

        it('should match ~~multi word strikethrough~~', () => {
            const match = doubleTildeTokenizer.exec('~~several words here~~');
            expect(match).not.toBeNull();
            expect(match![1]).toBe('several words here');
        });

        it('should NOT match single tildes used as "approximately"', () => {
            // This is the exact bug: two conversational tildes in one paragraph
            const input = '~32px from left). Nudge right by ~10px';
            const match = doubleTildeTokenizer.exec(input);
            expect(match).toBeNull();
        });

        it('should NOT match a lone ~word~', () => {
            const match = doubleTildeTokenizer.exec('~approximate~');
            expect(match).toBeNull();
        });

        it('should NOT match ~~ followed by whitespace', () => {
            const match = doubleTildeTokenizer.exec('~~ not struck ~~');
            expect(match).toBeNull();
        });

        it('should NOT match ~~ ending with whitespace before closing ~~', () => {
            const match = doubleTildeTokenizer.exec('~~trailing space ~~');
            expect(match).toBeNull();
        });

        it('should handle ~~strikethrough~~ adjacent to other text', () => {
            const match = doubleTildeTokenizer.exec('~~gone~~ but not forgotten');
            expect(match).not.toBeNull();
            expect(match![1]).toBe('gone');
        });

        it('should not match triple tildes ~~~', () => {
            // ~~~ should not produce a match because the third ~ means
            // the closing delimiter is not followed by a non-tilde
            const match = doubleTildeTokenizer.exec('~~~nope~~~');
            expect(match).toBeNull();
        });
    });

    describe('real-world false positives', () => {
        it('should not match approximate values in technical descriptions', () => {
            const inputs = [
                'padding starts at ~32px from the left edge',
                'offset by ~10px to indicate nesting',
                'latency is ~5ms under load',
                'the buffer is ~64KB in size',
                'runs at ~60fps on modern hardware',
            ];
            for (const input of inputs) {
                const match = doubleTildeTokenizer.exec(input);
                expect(match).toBeNull();
            }
        });

        it('should not match two approximate values in same paragraph', () => {
            const input =
                'both start at ~32px from left). The user wants nudge right by ~10px so they read as children.';
            const match = doubleTildeTokenizer.exec(input);
            expect(match).toBeNull();
        });
    });
});
