/**
 * Regression tests for PenPal #160 (CWE-79, HIGH): graphvizPlugin.ts,
 * mermaidPlugin.ts, and d2Plugin.ts interpolated spec.definition (raw
 * LLM-supplied diagram text) and error.message directly into innerHTML
 * template literals with no escaping, in both the error-display and
 * "View Source" paths.
 *
 * These plugins drive real DOM/canvas rendering (mermaid.initialize,
 * @viz-js/viz async imports, etc.) that is impractical to fully execute
 * in jest without heavy mocking. Following this repo's established
 * convention for large non-pure rendering modules (see
 * toolBlockRegex.test.tsx's docstring), these tests read the actual
 * source and assert every risky interpolation site is wrapped in
 * escapeHtml(...) — so a future edit that reintroduces an unescaped
 * ${spec.definition} or ${error.message} inside an innerHTML template
 * literal fails this suite immediately.
 */

import * as fs from 'fs';
import * as path from 'path';

function readSource(relPath: string): string {
    return fs.readFileSync(path.join(__dirname, '..', relPath), 'utf-8');
}

/**
 * Find every line that assigns to `.innerHTML` (directly, or via a
 * template literal that continues across lines up to the closing
 * backtick) and, within that assignment's text, check for a bare
 * (unwrapped) `${...spec.definition...}` or `${...error...message...}`
 * interpolation. This deliberately does NOT flag the same identifiers
 * appearing in console.log/debug lines, boolean checks, etc. — those
 * are not innerHTML sinks and are not part of the vulnerability class
 * (PenPal #160 is specifically about innerHTML template literals).
 */
function assertAllDefinitionAndErrorInterpolationsEscaped(source: string, fileLabel: string) {
    const unescaped: string[] = [];
    // Split the source into "innerHTML = `...`" assignment blocks. This
    // regex is deliberately permissive about what's between the backticks
    // (non-greedy across newlines) so multi-line template literals are
    // captured as a single block to scan.
    const innerHtmlAssignmentRe = /\.innerHTML\s*=\s*`([\s\S]*?)`/g;
    let blockMatch: RegExpExecArray | null;
    const interpolationRe = /\$\{([^}]*(?:spec\.definition|error[^}]*message)[^}]*)\}/g;
    while ((blockMatch = innerHtmlAssignmentRe.exec(source)) !== null) {
        const block = blockMatch[1];
        let interpMatch: RegExpExecArray | null;
        while ((interpMatch = interpolationRe.exec(block)) !== null) {
            if (!interpMatch[1].includes('escapeHtml(')) {
                unescaped.push(interpMatch[0]);
            }
        }
    }
    expect({ file: fileLabel, unescapedSites: unescaped }).toEqual({
        file: fileLabel,
        unescapedSites: [],
    });
}

describe('Diagram plugin XSS regression (PenPal #160)', () => {
    it('graphvizPlugin.ts escapes every spec.definition/error.message innerHTML interpolation', () => {
        const source = readSource('graphvizPlugin.ts');
        expect(source).toContain("import { escapeHtml } from '../../utils/htmlSanitize';");
        assertAllDefinitionAndErrorInterpolationsEscaped(source, 'graphvizPlugin.ts');
    });

    it('mermaidPlugin.ts escapes every spec.definition/error.message innerHTML interpolation', () => {
        const source = readSource('mermaidPlugin.ts');
        expect(source).toContain("import { escapeHtml } from '../../utils/htmlSanitize';");
        assertAllDefinitionAndErrorInterpolationsEscaped(source, 'mermaidPlugin.ts');
    });

    it('d2Plugin.ts escapes every spec.definition/error.message innerHTML interpolation', () => {
        const source = readSource('d2Plugin.ts');
        expect(source).toContain("import { escapeHtml } from '../../utils/htmlSanitize';");
        assertAllDefinitionAndErrorInterpolationsEscaped(source, 'd2Plugin.ts');
    });

    it('sanity check: the detector actually flags an unescaped sink (non-tautological)', () => {
        // Prove the regex/assertion helper is not vacuously passing by
        // feeding it a deliberately-unescaped snippet shaped like the
        // pre-fix vulnerable code.
        const vulnerable = 'container.innerHTML = `<pre><code>${spec.definition}</code></pre>`;';
        expect(() =>
            assertAllDefinitionAndErrorInterpolationsEscaped(vulnerable, 'synthetic-vulnerable-snippet')
        ).toThrow();
    });
});
