import { parseThinkingContent, removeThinkingTags } from '../thinkingParser';

describe('parseThinkingContent', () => {
    it('parses thinking-data tags', () => {
        const content = 'before <thinking-data>some thought</thinking-data> after';
        const result = parseThinkingContent(content);
        expect(result).not.toBeNull();
        expect(result!.content).toBe('some thought');
    });

    it('parses thinking tags', () => {
        const content = 'before <thinking>some thought</thinking> after';
        const result = parseThinkingContent(content);
        expect(result).not.toBeNull();
        expect(result!.content).toBe('some thought');
    });

    it('returns null when no thinking tags', () => {
        expect(parseThinkingContent('no thinking here')).toBeNull();
    });

    it('prefers thinking-data over thinking tags', () => {
        const content = '<thinking-data>data-content</thinking-data> <thinking>tag-content</thinking>';
        const result = parseThinkingContent(content);
        expect(result!.content).toBe('data-content');
    });
});

describe('removeThinkingTags', () => {
    it('removes thinking-data tags', () => {
        const result = removeThinkingTags('before <thinking-data>thought</thinking-data> after');
        expect(result).toBe('before after');
    });

    it('removes thinking tags', () => {
        const result = removeThinkingTags('before <thinking>thought</thinking> after');
        expect(result).toBe('before after');
    });

    it('removes fence-based thinking blocks with 4 backticks', () => {
        const content = 'before\n````thinking:step-1\nthought content\n````\nafter';
        const result = removeThinkingTags(content);
        expect(result.trim()).toBe('before\nafter');
    });

    it('removes fence-based thinking blocks with 5+ backticks', () => {
        const content = 'before\n`````thinking:step-3\nthought with ```code``` inside\n`````\nafter';
        const result = removeThinkingTags(content);
        expect(result.trim()).toBe('before\nafter');
    });

    it('does not remove 3-backtick fences (not thinking blocks)', () => {
        const content = '```python\ncode\n```';
        const result = removeThinkingTags(content);
        expect(result).toBe('```python\ncode\n```');
    });

    it('preserves content around fence-based thinking blocks', () => {
        const content = '## Heading\n\n````thinking:step-1\nsome thought\n````\n\n## Next Section\n\n```python\nclass X: pass\n```';
        const result = removeThinkingTags(content);
        expect(result).toContain('## Heading');
        expect(result).toContain('## Next Section');
        expect(result).toContain('```python');
        expect(result).not.toContain('thinking:step');
        expect(result).not.toContain('some thought');
    });

    it('removes thinking blocks containing &#96; entities', () => {
        const content = '````thinking:step-1\n&#96;&#96;&#96;python reference\n````\n\n## Real Content';
        const result = removeThinkingTags(content);
        expect(result).not.toContain('&#96;');
        expect(result).toContain('## Real Content');
    });

    it('handles multiple thinking blocks', () => {
        const content = '````thinking:step-1\nfirst thought\n````\n\nsome content\n\n`````thinking:step-2\nsecond thought\n`````\n\nmore content';
        const result = removeThinkingTags(content);
        expect(result).not.toContain('first thought');
        expect(result).not.toContain('second thought');
        expect(result).toContain('some content');
        expect(result).toContain('more content');
    });
});
