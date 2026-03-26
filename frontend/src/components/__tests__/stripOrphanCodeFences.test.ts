/**
 * Tests for the bare code fence stripping logic in MarkdownRenderer.
 *
 * The function is inlined in the useMemo lexer inside MarkdownRenderer.tsx,
 * so we replicate the algorithm here to unit-test it in isolation.
 */

/**
 * Strip bare code fences that wrap markdown prose instead of code.
 * Replicates the "Fix 4" block from MarkdownRenderer's preprocessing pipeline.
 */
function stripOrphanCodeFences(markdown: string): string {
    const fenceLines = markdown.split('\n');
    const fenceOutput: string[] = [];
    let fi = 0;

    while (fi < fenceLines.length) {
        const fLine = fenceLines[fi];
        const bareFenceMatch = fLine.match(/^(`{3,})\s*$/);

        if (bareFenceMatch) {
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

                if (!innerContent) { fi = closeIdx + 1; continue; }

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
            } else {
                const remainingContent = fenceLines.slice(fi + 1).join('\n').trim();
                const remainingIsMarkdown = /\*\*|^#{1,6}\s|^\d+\.|^[-*]\s/m.test(remainingContent);
                const remainingIsCode = fenceLines.slice(fi + 1).some(l => {
                    const t = l.trimStart();
                    return t.startsWith('import ') || t.startsWith('def ') ||
                           t.startsWith('function ') || t.startsWith('const ');
                });

                if (remainingIsMarkdown && !remainingIsCode) { fi++; continue; }
            }
        }

        fenceOutput.push(fLine);
        fi++;
    }

    return fenceOutput.join('\n');
}

describe('stripOrphanCodeFences', () => {
    it('unwraps markdown prose trapped between bare fence pairs', () => {
        const input = [
            '**Update 1**: First section.',
            '',
            '` ` ` `'.replace(/ /g, ''),
            '',
            '**Update 2**: Second section.',
            '',
            '` ` ` `'.replace(/ /g, ''),
            '',
            '**Update 3**: Third section.',
        ].join('\n');

        const result = stripOrphanCodeFences(input);

        expect(result).toContain('**Update 1**');
        expect(result).toContain('**Update 2**');
        expect(result).toContain('**Update 3**');
        expect(result).not.toMatch(/`{3,}/);
    });

    it('strips empty fence pairs entirely', () => {
        const fence = '`'.repeat(4);
        const input = `Before\n\n${fence}\n\n${fence}\n\nAfter`;
        const result = stripOrphanCodeFences(input);

        expect(result).toContain('Before');
        expect(result).toContain('After');
        expect(result).not.toMatch(/`{3,}/);
    });

    it('strips orphan trailing fence before markdown prose', () => {
        const fence = '`'.repeat(4);
        const input = [
            fence,
            '',
            'All three **updates** applied:',
            '',
            '1. **Section 7.5** — content',
        ].join('\n');

        const result = stripOrphanCodeFences(input);

        expect(result).toContain('**updates**');
        expect(result).toContain('**Section 7.5**');
        expect(result).not.toMatch(/`{3,}/);
    });

    it('preserves code blocks with language tags', () => {
        const input = [
            '` ` `python'.replace(/ /g, ''),
            'def hello():',
            '    return "world"',
            '` ` `'.replace(/ /g, ''),
        ].join('\n');

        const result = stripOrphanCodeFences(input);

        // Language-tagged fences are not bare — untouched
        expect(result).toBe(input);
    });

    it('preserves bare fences that actually wrap code', () => {
        const fence = '`'.repeat(3);
        const input = [
            fence,
            'const x = 42;',
            'function test() {',
            '    return x;',
            '}',
            fence,
        ].join('\n');

        const result = stripOrphanCodeFences(input);

        expect(result).toBe(input);
    });

    it('handles the exact user-reported pattern with numbered lists', () => {
        const fence = '`'.repeat(4);
        const input = [
            '**Update 1**: Add SLO numbers.',
            '',
            fence, '',
            '**Update 2**: Add measurement standard.',
            '',
            fence, '',
            '**Update 3**: Add reference.',
            '',
            fence, '',
            'All three updates applied:',
            '',
            '1. **Section 7.5** — content',
            '2. **Section 3.6** — content',
            '3. **Reference [15]** — content',
        ].join('\n');

        const result = stripOrphanCodeFences(input);

        expect(result).toContain('**Update 1**');
        expect(result).toContain('**Update 2**');
        expect(result).toContain('**Update 3**');
        expect(result).toContain('**Section 7.5**');
        expect(result).toContain('**Section 3.6**');
        expect(result).toContain('**Reference [15]**');
        expect(result).not.toMatch(/`{3,}/);
    });
});
