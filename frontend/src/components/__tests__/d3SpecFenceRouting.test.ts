/**
 * Source-contract guard for the JSON-spec D3 fences (```chord,
 * ```force-directed, ```network) — same philosophy as railroadRouting.test.ts.
 *
 * Reported 2026-10-16: a ```chord fence with a valid {type, names, matrix}
 * spec rendered as plain text.  The chord plugin was registered, the fence
 * scanner treated 'chord' as atomic, and the system prompt taught the model
 * to emit it — but determineTokenType only routed the literal 'd3' language,
 * so the fence fell through to the generic 'code' case with no error anywhere.
 * Every hop is asserted here so a partially-wired language fails CI.
 */
import * as fs from 'fs';
import * as path from 'path';

import { D3_SPEC_FENCE_LANGS, d3TypeForFenceLang } from '../../utils/d3FenceLang';

const REPO = path.resolve(__dirname, '../../../..');
const read = (p: string) => fs.readFileSync(path.join(REPO, p), 'utf8');

describe('d3TypeForFenceLang', () => {
    it('maps every spec fence language to a plugin type, case-insensitively', () => {
        expect(d3TypeForFenceLang('chord')).toBe('chord');
        expect(d3TypeForFenceLang(' Chord ')).toBe('chord');
        expect(d3TypeForFenceLang('force-directed')).toBe('force-directed');
        expect(d3TypeForFenceLang('forcedirected')).toBe('force-directed');
        expect(d3TypeForFenceLang('network')).toBe('network');
    });

    it('declines everything else, including d3 itself and nullish input', () => {
        expect(d3TypeForFenceLang('d3')).toBeNull();
        expect(d3TypeForFenceLang('mermaid')).toBeNull();
        expect(d3TypeForFenceLang('json')).toBeNull();
        expect(d3TypeForFenceLang(null)).toBeNull();
        expect(d3TypeForFenceLang(undefined)).toBeNull();
    });
});

describe('JSON-spec D3 fences cross every seam', () => {
    const renderer = read('frontend/src/components/MarkdownRenderer.tsx');
    const scanner = read('frontend/src/components/fenceScanner.ts');

    it('MarkdownRenderer routes the spec languages to the d3 token type via the shared helper', () => {
        expect(renderer).toMatch(/import \{ d3TypeForFenceLang \} from '\.\.\/utils\/d3FenceLang'/);
        // Routing hop: the determineTokenType branch that returns 'd3'.
        expect(renderer).toMatch(/if \(lang === 'd3' \|\| d3TypeForFenceLang\(lang\) !== null\) return 'd3';/);
    });

    it('the d3 render case fills in a missing spec type from the fence language', () => {
        const start = renderer.indexOf("case 'd3':");
        const end = renderer.indexOf("case 'code':", start);
        expect(start).toBeGreaterThan(-1);
        expect(end).toBeGreaterThan(start);
        const d3Case = renderer.slice(start, end);
        expect(d3Case).toMatch(/d3TypeForFenceLang\(tokenWithText\.lang\)/);
        // The explicit chart alias still wins over the fence-language default.
        expect(d3Case).toMatch(/d3Spec\.chart\s*\|\|\s*d3TypeForFenceLang/);
    });

    it.each(D3_SPEC_FENCE_LANGS)('fenceScanner treats %s as an atomic fence language', (lang) => {
        const start = scanner.indexOf('const ATOMIC_FENCE_LANGS = new Set([');
        const end = scanner.indexOf(']);', start);
        expect(start).toBeGreaterThan(-1);
        expect(scanner.slice(start, end)).toContain(`'${lang}'`);
    });

    it('a registered plugin exists for every default type the helper hands out', () => {
        const registry = read('frontend/src/plugins/d3/registry.ts');
        expect(registry).toMatch(/name: 'chord-renderer'/);
        expect(registry).toMatch(/name: 'force-directed'/);
        expect(registry).toMatch(/name: 'network-diagram'/);
    });

    it('the model is taught the chord fence vocabulary', () => {
        expect(read('app/agents/prompts.py')).toMatch(/```chord fence/);
    });
});
