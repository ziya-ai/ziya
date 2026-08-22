import { normalizeMermaidThemeToLight } from '../mermaidThemeNormalize';

describe('normalizeMermaidThemeToLight (PDF-02: dark diagram theme leak)', () => {
    it('rewrites a baked dark theme to the light default', () => {
        const input = "```mermaid\n%%{init: {'theme':'dark'}}%%\ngraph LR\n A-->B\n```";
        const out = normalizeMermaidThemeToLight(input);
        expect(out).toContain("theme':'default'");
        expect(out).not.toMatch(/theme['"]?\s*:\s*['"]dark['"]/);
        // diagram structure is untouched
        expect(out).toContain('graph LR');
        expect(out).toContain('A-->B');
    });

    it('handles double-quoted themes and strips baked dark themeVariables', () => {
        const input =
            '%%{init: {"theme":"dark", "themeVariables": {"mainBkg":"#000","textColor":"#fff"}}}%%\ngraph TD';
        const out = normalizeMermaidThemeToLight(input);
        // theme value normalized to 'default' (lead quote style is preserved,
        // so "theme": stays double-quoted while the value becomes 'default')
        expect(out).toMatch(/theme["']?\s*:\s*'default'/);
        expect(out).not.toMatch(/theme['"]?\s*:\s*['"]dark['"]/);
        expect(out).not.toContain('themeVariables');
        expect(out).not.toContain('#000');
        expect(out).toContain('graph TD');
    });

    it('normalizes forest/neutral/base as well as dark', () => {
        for (const t of ['forest', 'neutral', 'base', 'dark']) {
            const out = normalizeMermaidThemeToLight(`%%{init: {'theme':'${t}'}}%%\ngraph LR`);
            expect(out).toContain("theme':'default'");
        }
    });

    it('preserves non-theme init config (layout directives untouched)', () => {
        const input = "%%{init: {'flowchart':{'curve':'basis'},'theme':'dark'}}%%\ngraph LR";
        const out = normalizeMermaidThemeToLight(input);
        expect(out).toContain("'flowchart':{'curve':'basis'}");
        expect(out).toContain("theme':'default'");
    });

    it('is idempotent and a no-op on already-light specs', () => {
        const light = "%%{init: {'theme':'default'}}%%\ngraph LR";
        expect(normalizeMermaidThemeToLight(light)).toBe(light);
        const once = normalizeMermaidThemeToLight("%%{init: {'theme':'dark'}}%%\ngraph LR");
        expect(normalizeMermaidThemeToLight(once)).toBe(once);
    });

    it('leaves content with no mermaid directive unchanged', () => {
        const text = 'Some prose with no diagrams and a graph LR mention.';
        expect(normalizeMermaidThemeToLight(text)).toBe(text);
    });

    it('does not touch non-init directives', () => {
        const input = '%%{wrap}%%\nsequenceDiagram\n A->>B: hi';
        expect(normalizeMermaidThemeToLight(input)).toBe(input);
    });

    it('normalizes every diagram in a multi-diagram document', () => {
        const input = [
            "```mermaid",
            "%%{init: {'theme':'dark'}}%%",
            "graph LR\n A-->B",
            "```",
            "```mermaid",
            "%%{init: {'theme':'dark'}}%%",
            "graph TD\n C-->D",
            "```",
        ].join('\n');
        const out = normalizeMermaidThemeToLight(input);
        expect(out.match(/theme':'default'/g)?.length).toBe(2);
        expect(out).not.toMatch(/theme['"]?\s*:\s*['"]dark['"]/);
    });
});
