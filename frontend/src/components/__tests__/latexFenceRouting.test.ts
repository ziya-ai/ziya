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

/** Languages routed into the LaTeX case by determineTokenType. */
function routedLangs(): string[] {
    const src = read('frontend/src/components/MarkdownRenderer.tsx');
    // The consolidated routing condition returning 'circuitikz'.
    const block = src.match(
        /if \(lang === 'circuitikz'[\s\S]{0,400}?return 'circuitikz';/);
    if (!block) throw new Error('LaTeX routing condition not found');
    return [...block[0].matchAll(/lang === '([a-z0-9-]+)'/g)].map(m => m[1]);
}

/** lang -> profile map inside the render case. */
function langToProfile(): Record<string, string> {
    const src = read('frontend/src/components/MarkdownRenderer.tsx');
    const block = src.match(
        /LATEX_LANG_TO_PROFILE:\s*Record<string, string>\s*=\s*\{([\s\S]*?)\}/);
    if (!block) throw new Error('LATEX_LANG_TO_PROFILE map not found');
    const out: Record<string, string> = {};
    for (const m of block[1].matchAll(/'([a-z0-9-]+)':\s*'([a-z0-9-]+)'/g)) {
        out[m[1]] = m[2];
    }
    return out;
}

describe('LaTeX fence routing is complete across all three layers', () => {
    it('routes every backend profile from a fence language', () => {
        const routed = routedLangs();
        for (const profile of backendProfiles()) {
            expect(routed).toContain(profile);
        }
    });

    it('maps every routed language to a real backend profile', () => {
        const profiles = backendProfiles();
        const map = langToProfile();
        for (const [lang, profile] of Object.entries(map)) {
            expect(profiles).toContain(profile);
        }
        // Every routed lang must either map to a profile or be the deliberate
        // ```latex code-block exception.
        for (const lang of routedLangs()) {
            if (lang === 'latex') continue;
            expect(Object.keys(map)).toContain(lang);
        }
    });

    it('declares every backend profile in the plugin canHandle set', () => {
        const src = read('frontend/src/plugins/d3/latexPlugin.ts');
        const set = src.match(/LATEX_TYPES\s*=\s*new Set\(\[([^\]]*)\]/);
        expect(set).toBeTruthy();
        const types = [...set![1].matchAll(/'([a-z0-9-]+)'/g)].map(m => m[1]);
        for (const profile of backendProfiles()) {
            expect(types).toContain(profile);
        }
    });

    it('self-test: the extractors actually find something', () => {
        expect(backendProfiles().length).toBeGreaterThanOrEqual(4);
        expect(routedLangs().length).toBeGreaterThanOrEqual(4);
        expect(Object.keys(langToProfile()).length).toBeGreaterThanOrEqual(4);
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
        expect(latexProfileForLang('pgfplots')).toBeNull();
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
