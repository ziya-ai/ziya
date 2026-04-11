/**
 * Tests for the preprocessing regex that inserts blank lines before
 * code fences that are concatenated directly to preceding text.
 *
 * LLMs sometimes omit the newline before a code fence entirely,
 * producing output like "for a log scale:```vega-lite".
 * The marked.js tokenizer requires code fences at the start of a line
 * with a preceding blank line, so these get parsed as paragraph text.
 *
 * The fix in MarkdownRenderer.tsx inserts \n\n before the fence.
 */

/**
 * Replicate the production preprocessing regex.
 * Must stay in sync with MarkdownRenderer.tsx.
 */
function fixConcatenatedFences(input: string): string {
    return input.replace(/([^\n`])(`{3,}[a-zA-Z][a-zA-Z0-9_-]*)/g, '$1\n\n$2');
}

describe('code fence splice fix', () => {
    it('inserts blank line when fence is glued to text (no newline)', () => {
        const input = 'for a log scale:```vega-lite\n{"mark":"bar"}\n```';
        const result = fixConcatenatedFences(input);
        expect(result).toContain('for a log scale:\n\n```vega-lite');
    });

    it('inserts blank line for triple-backtick with language tag', () => {
        const input = 'here is code:```python\nprint("hi")\n```';
        const result = fixConcatenatedFences(input);
        expect(result).toContain('code:\n\n```python');
    });

    it('does not modify fences that already start on their own line', () => {
        const input = 'some text\n```python\nprint("hi")\n```';
        const result = fixConcatenatedFences(input);
        // The \n before ``` is not matched by [^\n`], so no change
        expect(result).toBe(input);
    });

    it('does not modify bare fences (no language tag)', () => {
        const input = 'some text:```\nplain code\n```';
        const result = fixConcatenatedFences(input);
        // Bare fences don't have [a-zA-Z] after ```, so no match
        expect(result).toBe(input);
    });

    it('does not modify longer fence sequences (e.g. ````)', () => {
        const input = '````python\ncode\n````';
        const result = fixConcatenatedFences(input);
        // First char is ` which is excluded by [^\n`]
        expect(result).toBe(input);
    });

    it('handles multiple concatenated fences in same content', () => {
        const input = 'Option A:```python\nx = 1\n```\n\nOption B:```javascript\ny = 2\n```';
        const result = fixConcatenatedFences(input);
        expect(result).toContain('Option A:\n\n```python');
        expect(result).toContain('Option B:\n\n```javascript');
    });

    it('handles fence after colon with no space', () => {
        const input = 'scale:```vega-lite\n{}\n```';
        const result = fixConcatenatedFences(input);
        expect(result).toContain('scale:\n\n```vega-lite');
    });

    it('does not break tool fences (tool: prefix)', () => {
        // tool: fences are handled by a different code path;
        // the regex should still insert the blank line since the
        // fence must start on its own line regardless
        const input = 'result:```tool:mcp_search\ncontent\n```';
        const result = fixConcatenatedFences(input);
        expect(result).toContain('result:\n\n```tool');
    });
});
