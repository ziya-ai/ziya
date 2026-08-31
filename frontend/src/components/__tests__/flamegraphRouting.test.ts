/**
 * Source-contract guard for flame graph routing — same philosophy as
 * latexFenceRouting / railroadRouting / wavedromRouting: both ends of the
 * pipeline can support a type while a middle routing step silently drops the
 * fence to a code block, and no rendering test can catch that.
 *
 * The dependency-declaration check matters here for the same reason it did
 * for wavedrom: the plugin lazily imports d3-flame-graph, so a missing
 * package.json entry fails only at RUNTIME, inside a dynamic import, as an
 * error card rather than a build failure.
 */
import * as fs from 'fs';
import * as path from 'path';

import { VISUALIZATION_TYPES } from '../../constants/visualizationTypes';

const REPO = path.resolve(__dirname, '../../../..');
const read = (p: string) => fs.readFileSync(path.join(REPO, p), 'utf8');
const exists = (p: string) => fs.existsSync(path.join(REPO, p));

describe('flamegraph fence routing crosses every seam', () => {
    it('support utilities and plugin wrapper both exist', () => {
        expect(exists('frontend/src/utils/d3Plugins/flamegraphPlugin.ts')).toBe(true);
        expect(exists('frontend/src/plugins/d3/flamegraphPlugin.ts')).toBe(true);
    });

    it('d3-flame-graph is a declared dependency', () => {
        const pkg = JSON.parse(read('frontend/package.json'));
        const deps = { ...pkg.dependencies, ...pkg.optionalDependencies };
        expect(deps['d3-flame-graph']).toBeDefined();
    });

    it('the untyped module has a declaration shim so the build typechecks', () => {
        expect(exists('frontend/src/types/flamegraph-shim.d.ts')).toBe(true);
        expect(read('frontend/src/types/flamegraph-shim.d.ts'))
            .toMatch(/declare module 'd3-flame-graph'/);
    });

    it('MarkdownRenderer routes the fence language, declares the type, and renders the case', () => {
        const src = read('frontend/src/components/MarkdownRenderer.tsx');
        expect(src).toMatch(/lang === 'flamegraph'/);
        expect(src).toMatch(/case 'flamegraph':/);
        expect(src).toMatch(/'flamegraph' \|/);
    });

    it('routes the collapsed-stacks alias, since that is the native profiler format', () => {
        // py-spy / perf / flamegraph.pl emit collapsed stacks; a user pasting
        // that output should not have to know it is "a flamegraph spec".
        const src = read('frontend/src/components/MarkdownRenderer.tsx');
        expect(src).toMatch(/'collapsed-stacks'|'flame-graph'/);
    });

    it('the lazy plugin registry has a flamegraph entry', () => {
        const src = read('frontend/src/plugins/d3/registry.ts');
        expect(src).toMatch(/name: 'flamegraph-renderer'/);
        expect(src).toMatch(/import\('\.\/flamegraphPlugin'\)/);
    });

    it('visualizationTypes classifies flamegraph for capture/export', () => {
        // d3-flame-graph renders a real <svg>, so visualizationCapture's
        // querySelector('svg') path works -- but only if the type is listed
        // here, otherwise it misclassifies to the 'd3' fallback.
        expect(VISUALIZATION_TYPES).toContain('flamegraph');
    });

    it('the Python exporter viz list includes flamegraph', () => {
        expect(read('app/utils/conversation_exporter.py')).toMatch(/'flamegraph'/);
    });

    it('the render_diagram MCP tool accepts flamegraph', () => {
        expect(read('app/mcp/tools/diagram_render.py')).toMatch(/"flamegraph"/);
    });

    it('the model is taught the vocabulary (prompt line + built-in skill)', () => {
        // Without these, both ends work but nothing ever emits the fence.
        expect(read('app/agents/prompts.py')).toMatch(/\(flamegraph\)/);
        expect(read('app/data/built_in_skills.py')).toMatch(/'flame_graphs'/);
    });
});
