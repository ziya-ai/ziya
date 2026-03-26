/**
 * Tests for the fence leak reabsorption regexes (Pass 1 and Pass 2)
 * in MarkdownRenderer's lexing pipeline.
 *
 * These regexes are designed to catch content that "leaks" outside a
 * prematurely-closed code fence. They must NOT match backtick sequences
 * that are part of a longer fence (e.g. ```` used by tool blocks).
 */
describe('Fence leak reabsorption regexes', () => {
    // Pass 1: Mid-message — short orphaned content between consecutive fences
    // Fixed regex with negative lookbehind to skip 4+ backtick fences
    const pass1Regex = /(?<!`)```([ \t]*\n)((?:[^\n]{0,80}\n){1,5})((?<!`)```[a-zA-Z])/g;

    // Pass 2: End-of-string — short orphaned content after last closing fence
    // Fixed regex with negative lookbehind to skip 4+ backtick fences
    const pass2Regex = /(?<!`)```([ \t]*\n)((?:[^\n]{0,80}\n?){1,5})$/;

    describe('Pass 1 (mid-message)', () => {
        it('should match legitimate 3-backtick fence with leaked content', () => {
            const input = '```\nleaked line\n```diff\n-old\n+new\n```';
            const matches = [...input.matchAll(pass1Regex)];
            expect(matches.length).toBe(1);
            expect(matches[0][2].trim()).toBe('leaked line');
        });

        it('should NOT match when ``` is part of a 4-backtick fence (````)', () => {
            const input = '````\nshort line\n```diff\n-old\n+new\n```';
            const matches = [...input.matchAll(pass1Regex)];
            expect(matches.length).toBe(0);
        });

        it('should NOT match tool block closing fence followed by text and another tool block', () => {
            const input = [
                '````tool:mcp_run_shell_command|Shell: first|bash',
                'output1',
                '````',
                'Text between tools',
                '````tool:mcp_run_shell_command|Shell: second|bash',
                'output2',
                '````',
            ].join('\n');
            const matches = [...input.matchAll(pass1Regex)];
            expect(matches.length).toBe(0);
        });
    });

    describe('Pass 2 (end-of-string)', () => {
        it('should match legitimate 3-backtick fence with leaked content at end', () => {
            const input = 'some text\n```\nleaked tail\n```';
            const match = pass2Regex.exec(input);
            expect(match).not.toBeNull();
        });

        it('should NOT match when ``` is part of a 4-backtick fence (````)', () => {
            const input = [
                '````tool:mcp_run_shell_command|Shell: cmd|bash',
                '$ echo hello',
                'hello',
                '````',
                '',
                '`I need to delete lines 3368 through 3645',
                '```',
            ].join('\n');
            const match = pass2Regex.exec(input);
            expect(match).toBeNull();
        });

        it('should NOT match 5-backtick fences either', () => {
            const input = '`````\nleaked\n```';
            const match = pass2Regex.exec(input);
            expect(match).toBeNull();
        });

        it('should match when 3-backtick fence is preceded by newline (not backtick)', () => {
            const input = 'text\n```\nleaked tail';
            const match = pass2Regex.exec(input);
            expect(match).not.toBeNull();
        });

        it('should match when 3-backtick fence is at start of string', () => {
            const input = '```\nleaked tail';
            const match = pass2Regex.exec(input);
            expect(match).not.toBeNull();
        });
    });
});
