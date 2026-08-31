/**
 * Source-contract guard: every server-rendered LaTeX profile must be routed
 * from its fence language all the way to the plugin.
 *
 * This exists because ```chemfig rendered as literal source in the chat while
 * BOTH ends of the pipeline supported it: the backend profile registry had a
 * chemfig entry, latexPlugin's LATEX_TYPES had chemfig, and only the middle
 * routing step in determineTokenType was missing it.  Nothing failed loudly —
 * the fence just fell through to the generic 'code' case.  A test that renders
 * a diagram cannot catch this; only a cross-layer consistency check can.
 *
 * The original version of this test checked three sites and missed three.  All
 * three uncovered ones were found to be WRONG: visualizationTypes.ts and both
 * lists in conversation_exporter.py named only 'circuitikz', so a rendered
 * chemfig/tikz/tikz-cd diagram misclassified on export.  The lists are now
 * derived from a shared registry, and these tests assert the derivation holds
 * rather than re-checking literals that no longer exist.
 */
import * as fs from 'fs';
import * as path from 'path';

import {
    LATEX_PROFILE_KEYS,
    isLatexFenceLang,
    latexProfileForLang,
} from '../../constants/latexProfiles';
import { VISUALIZATION_TYPES } from '../../constants/visualizationTypes';

const REPO = path.resolve(__dirname, '../../../..');

const read = (p: string) => fs.readFileSync(path.join(REPO, p), 'utf8');

/** Profile keys declared by the backend registry. */
function backendProfiles(): string[] {
    const src = read('app/services/latex_profiles.py');
    // Match the `"key": LatexProfile(` entries of the PROFILES dict.
    return [...src.matchAll(/^\s{4}"([a-z0-9-]+)":\s*LatexProfile\(/gm)]
        .map(m => m[1]);
}

/**
 * Both MarkdownRenderer.tsx and latexPlugin.ts were refactored to delegate to
 * the shared registry (constants/latexProfiles.ts) instead of keeping their
 * own literal lang lists/maps -- see that file's docstring. What must hold
 * now is DELEGATION: these two sites call into the registry rather than
 * re-implementing it, so a profile added to the registry is automatically
 * picked up everywhere without a fourth hand-edited site reappearing.
 */

/** Whether MarkdownRenderer's LaTeX routing step delegates to the registry. */
function markdownRendererDelegatesRouting(): boolean {
    const src = read('frontend/src/components/MarkdownRenderer.tsx');
    return /isLatexFenceLang\s*\(\s*lang\s*\)/.test(src)
        && /return 'circuitikz';/.test(src);
}

/** Whether MarkdownRenderer's render case resolves the profile via the registry. */
function markdownRendererDelegatesProfileLookup(): boolean {
    const src = read('frontend/src/components/MarkdownRenderer.tsx');
    return /latexProfileForLang\s*\(/.test(src);
}

/** Whether latexPlugin's canHandle set is built from the registry, not a literal. */
function latexPluginDelegatesTypeSet(): { built: boolean; literal: RegExpMatchArray | null } {
    const src = read('frontend/src/plugins/d3/latexPlugin.ts');
    const built = /LATEX_TYPES\s*=\s*new Set\(\s*LATEX_PROFILE_KEYS\s*\)/.test(src)
        && /import\s*\{[^}]*LATEX_PROFILE_KEYS[^}]*\}\s*from\s*['"].*latexProfiles['"]/.test(src);
    // A literal array (e.g. new Set(['circuitikz', 'tikz', ...])) would mean
    // the delegation regressed back into a fourth hand-maintained list.
    const literal = src.match(/LATEX_TYPES\s*=\s*new Set\(\[([^\]]*)\]/);
    return { built, literal };
}

describe('LaTeX fence routing delegates to the shared registry (no literal re-lists)', () => {
    it('MarkdownRenderer routes fence languages via isLatexFenceLang', () => {
        expect(markdownRendererDelegatesRouting()).toBe(true);
    });

    it('MarkdownRenderer resolves the backend profile via latexProfileForLang', () => {
        expect(markdownRendererDelegatesProfileLookup()).toBe(true);
    });

    it('MarkdownRenderer imports the routing/profile helpers from the registry', () => {
        const src = read('frontend/src/components/MarkdownRenderer.tsx');
        expect(src).toMatch(
            /import\s*\{[^}]*isLatexFenceLang[^}]*latexProfileForLang[^}]*\}\s*from\s*['"].*latexProfiles['"]|import\s*\{[^}]*latexProfileForLang[^}]*isLatexFenceLang[^}]*\}\s*from\s*['"].*latexProfiles['"]/
        );
    });

    it('latexPlugin builds its canHandle set from LATEX_PROFILE_KEYS, not a literal list', () => {
        const { built, literal } = latexPluginDelegatesTypeSet();
        expect(built).toBe(true);
        expect(literal).toBeNull();
    });

    it('self-test: the delegation checks actually run against non-trivial source', () => {
        const rendererSrc = read('frontend/src/components/MarkdownRenderer.tsx');
        const pluginSrc = read('frontend/src/plugins/d3/latexPlugin.ts');
        expect(rendererSrc.length).toBeGreaterThan(1000);
        expect(pluginSrc.length).toBeGreaterThan(200);
    });
});

describe('shared LaTeX registry agrees with the backend', () => {
    it('declares exactly the backend profile keys, no more and no fewer', () => {
        // Extra keys are as harmful as missing ones: a frontend-only profile
        // routes a fence to /api/render-latex, which then rejects it as an
        // unknown type after a round-trip.
        expect([...LATEX_PROFILE_KEYS].sort())
            .toEqual([...backendProfiles()].sort());
    });

    it('routes every profile key as a fence language of the same name', () => {
        for (const key of LATEX_PROFILE_KEYS) {
            expect(latexProfileForLang(key)).toBe(key);
        }
    });

    it('keeps bare ```latex routed but unmapped', () => {
        // The deliberate exception: routed so it is recognised, unmapped so it
        // degrades to a code block instead of being compiled as a circuit.
        expect(isLatexFenceLang('latex')).toBe(true);
        expect(latexProfileForLang('latex')).toBeNull();
    });

    it('normalises case and surrounding whitespace', () => {
        expect(latexProfileForLang('  ChemFig ')).toBe('chemfig');
        expect(isLatexFenceLang('TIKZ-CD')).toBe(true);
    });

    it('rejects unknown languages rather than guessing', () => {
        expect(latexProfileForLang('wavedrom')).toBeNull();
        expect(isLatexFenceLang('python')).toBe(false);
    });
});

describe('export-side lists include every LaTeX profile', () => {
    it('visualizationTypes contains every profile key', () => {
        // D3Renderer names the container after the PROFILE, so a profile
        // absent here misclassifies to the 'd3' fallback on capture.
        for (const key of LATEX_PROFILE_KEYS) {
            expect(VISUALIZATION_TYPES).toContain(key);
        }
    });

    it('the Python exporter no longer hardcodes a LaTeX list', () => {
        const src = read('app/utils/conversation_exporter.py');
        expect(src).toContain('from app.services.latex_profiles import PROFILES');
        // The second literal list at the old line 898 must be gone.
        expect(src).not.toMatch(/viz_types\s*=\s*\[/);
    });
});
