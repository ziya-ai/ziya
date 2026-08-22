/**
 * Regression tests for the Mermaid post-render visibility guard.
 * Author-specified text colors must survive rendering, whether they are
 * declared with classDef or per-node style syntax.
 */
import * as fs from 'fs';
import * as path from 'path';
import { shouldEnhanceMermaidVisibility } from '../mermaidPlugin';

describe('shouldEnhanceMermaidVisibility', () => {
    it('is wired into the rendered-diagram visibility decision', () => {
        const source = fs.readFileSync(path.join(__dirname, '..', 'mermaidPlugin.ts'), 'utf-8');

        expect(source).toMatch(/if \(shouldEnhanceMermaidVisibility\(rawDefinition\)\) \{\s*const runVisibilityFix/);
    });

    it('recognizes the exact dark-fill/light-text classDef regression', () => {
        const definition = [
            'flowchart LR',
            '  A["Bug 1"] --> B["Fixed"]',
            '  classDef bad fill:#3b1d1d,stroke:#c0392b,color:#f5d5d0;',
            '  classDef ok fill:#1d3b28,stroke:#27ae60,color:#d0f0dc;',
            '  class A bad',
            '  class B ok',
        ].join('\n');

        expect(shouldEnhanceMermaidVisibility(definition)).toBe(false);
    });

    it('recognizes an explicit text color in a per-node style declaration', () => {
        const definition = [
            'flowchart LR',
            '  A --> B',
            '  style A fill:#3b1d1d,stroke:#c0392b,color:#f5d5d0',
        ].join('\n');

        expect(shouldEnhanceMermaidVisibility(definition)).toBe(false);
    });

    it('does not combine a color token elsewhere with an uncolored classDef', () => {
        const definition = [
            'flowchart LR',
            '  %% color: appears outside a style declaration',
            '  A --> B',
            '  classDef bad fill:#3b1d1d,stroke:#c0392b;',
            '  class A bad',
        ].join('\n');

        expect(shouldEnhanceMermaidVisibility(definition)).toBe(true);
    });

    it('runs enhancement when no classDef or style sets a text color', () => {
        const definition = [
            'flowchart LR',
            '  A --> B',
            '  classDef plain fill:#ffffff,stroke:#333333;',
        ].join('\n');

        expect(shouldEnhanceMermaidVisibility(definition)).toBe(true);
    });
});
