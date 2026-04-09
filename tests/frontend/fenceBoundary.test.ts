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

/**
 * Tests for the "Fix 4" bare-fence stripping logic in MarkdownRenderer.
 *
 * Fix 4 strips bare code fences (``` with no language tag) when the inner
 * content looks like markdown prose rather than code.  The extracted function
 * mirrors the inline block in MarkdownRenderer's lexing pipeline.
 */
describe('Fix 4: bare fence prose stripping', () => {
    /**
     * Re-implementation of the Fix 4 logic so we can unit-test it in isolation.
     */
    function stripBareProseFences(input: string): string {
        const fenceLines = input.split('\n');
        const fenceOutput: string[] = [];
        let fi = 0;
        let insideLangFence = false;
        let langFenceLen = 0;

        while (fi < fenceLines.length) {
            const fLine = fenceLines[fi];
            const bareFenceMatch = fLine.match(/^(`{3,})\s*$/);

            if (!insideLangFence) {
                const langFenceMatch = fLine.match(/^(`{3,})\S/);
                if (langFenceMatch) {
                    insideLangFence = true;
                    langFenceLen = langFenceMatch[1].length;
                    fenceOutput.push(fLine);
                    fi++;
                    continue;
                }
            }

            if (bareFenceMatch && insideLangFence && bareFenceMatch[1].length >= langFenceLen) {
                insideLangFence = false;
                langFenceLen = 0;
                fenceOutput.push(fLine);
                fi++;
                continue;
            }

            if (bareFenceMatch && !insideLangFence) {
                const fLen = bareFenceMatch[1].length;
                let closeIdx = -1;
                for (let fj = fi + 1; fj < fenceLines.length; fj++) {
                    const closeMatch = fenceLines[fj].match(/^(`{3,})\s*$/);
                    if (closeMatch && closeMatch[1].length >= fLen) {
                        closeIdx = fj;
                        break;
                    }
                }

                if (closeIdx !== -1) {
                    const innerLines = fenceLines.slice(fi + 1, closeIdx);
                    const innerContent = innerLines.join('\n').trim();

                    if (!innerContent) {
                        fi = closeIdx + 1;
                        continue;
                    }

                    const looksLikeMarkdown = /\*\*|^#{1,6}\s|^\d+\.|^[-*]\s|^>\s/m.test(innerContent);
                    const looksLikeCode = innerContent.split('\n').some(l => {
                        const t = l.trimStart();
                        return (
                            t.startsWith('import ') || t.startsWith('from ') ||
                            t.startsWith('def ') || t.startsWith('class ') ||
                            t.startsWith('function ') || t.startsWith('const ') ||
                            t.startsWith('let ') || t.startsWith('var ') ||
                            t.startsWith('return ') || t.startsWith('if (') ||
                            t.startsWith('for ') || t.startsWith('while ') ||
                            /^[a-z_]+\s*[=(]/.test(t) || /^\s*[{}]\s*$/.test(t) ||
                            t.startsWith('diff --git') || t.startsWith('--- a/') ||
                            t.startsWith('+++ b/')
                        );
                    });

                    if (looksLikeMarkdown && !looksLikeCode) {
                        fenceOutput.push(...innerLines);
                        fi = closeIdx + 1;
                        continue;
                    }

                    // Keep block intact and skip past closing fence
                    fenceOutput.push(fLine);
                    fenceOutput.push(...innerLines);
                    fenceOutput.push(fenceLines[closeIdx]);
                    fi = closeIdx + 1;
                    continue;
                } else {
                    const remainingContent = fenceLines.slice(fi + 1).join('\n').trim();
                    const remainingIsMarkdown = /\*\*|^#{1,6}\s|^\d+\.|^[-*]\s/m.test(remainingContent);
                    const remainingIsCode = fenceLines.slice(fi + 1).some(l => {
                        const t = l.trimStart();
                        return t.startsWith('import ') || t.startsWith('def ') ||
                            t.startsWith('function ') || t.startsWith('const ');
                    });

                    if (remainingIsMarkdown && !remainingIsCode) {
                        fi++;
                        continue;
                    }
                }
            }

            fenceOutput.push(fLine);
            fi++;
        }

        return fenceOutput.join('\n');
    }

    it('should strip bare fences wrapping markdown prose', () => {
        const input = '```\n**bold text** and a list:\n- item 1\n- item 2\n```';
        const result = stripBareProseFences(input);
        expect(result).not.toContain('```');
        expect(result).toContain('**bold text**');
    });

    it('should keep bare fences wrapping non-markdown content', () => {
        const input = '```\nxilinx-xdma-pcie: Slave unsupported request\nKernel panic\n```';
        const result = stripBareProseFences(input);
        expect(result).toBe(input);
    });

    it('should NOT strip closing fence when markdown follows (regression)', () => {
        // Exact pattern from the rendering bug: a bare code block with
        // non-markdown content, followed by markdown with bold/headings/lists.
        const input = [
            '```',
            'xilinx-xdma-pcie 400000000.axi-pcie: Slave unsupported request',
            'SError Interrupt on CPU1 -- marvell-scpu-to process',
            'Kernel panic - not syncing: Asynchronous SError Interrupt',
            '```',
            '',
            'This is a known class of issue.',
            '',
            '### Key Findings',
            '',
            '1. **Narrowed the crash location**: It happens during the path.',
        ].join('\n');

        const result = stripBareProseFences(input);

        // The bare code block must be preserved intact
        expect(result).toContain('```\nxilinx-xdma-pcie');
        expect(result).toContain('Asynchronous SError Interrupt\n```');

        // The closing fence must exist — count opening and closing fences
        const fenceLines = result.split('\n').filter(l => /^```\s*$/.test(l));
        expect(fenceLines.length).toBe(2); // one open, one close

        // Markdown after the code block must remain outside
        expect(result).toContain('### Key Findings');
        expect(result).toContain('1. **Narrowed the crash location**');
    });

    it('should preserve language-tagged fences regardless of content', () => {
        const input = '```python\nprint("hello")\n```';
        const result = stripBareProseFences(input);
        expect(result).toBe(input);
    });

    it('should handle 4-backtick tool fences without interference', () => {
        const input = [
            '````tool:mcp_test|Header|text',
            '{"key": "value"}',
            '````',
            '',
            '```',
            'plain log output here',
            '```',
            '',
            '### Summary with **bold**',
        ].join('\n');

        const result = stripBareProseFences(input);

        // Tool block preserved
        expect(result).toContain('````tool:mcp_test|Header|text');
        // Bare code block preserved (content is not markdown)
        expect(result).toContain('```\nplain log output here\n```');
        // Markdown after code block preserved
        expect(result).toContain('### Summary with **bold**');
    });
});