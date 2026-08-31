/**
 * Source-contract guard for WaveDrom timing-diagram routing — same philosophy
 * as latexFenceRouting.test.ts and railroadRouting.test.ts: both ends of a
 * pipeline can support a type while a middle routing step silently drops the
 * fence to a code block, and no rendering test can catch that.  Every hop a
 * ```wavedrom fence crosses is asserted here, so a partially-wired
 * integration fails CI instead of degrading quietly.
 *
 * One hop is unique to this integration: wavedrom is a real npm dependency
 * (unlike railroad's first-party engine), so the dependency declaration is a
 * seam too — a plugin importing an undeclared package builds locally off a
 * hoisted transitive copy and breaks on a clean install.
 */
import * as fs from 'fs';
import * as path from 'path';

import { VISUALIZATION_TYPES } from '../../constants/visualizationTypes';

const REPO = path.resolve(__dirname, '../../../..');
const read = (p: string) => fs.readFileSync(path.join(REPO, p), 'utf8');
const exists = (p: string) => fs.existsSync(path.join(REPO, p));

describe('wavedrom fence routing crosses every seam', () => {
    it('support utilities, plugin wrapper, and type shim all exist', () => {
        expect(exists('frontend/src/utils/d3Plugins/wavedromPlugin.ts')).toBe(true);
        expect(exists('frontend/src/plugins/d3/wavedromPlugin.ts')).toBe(true);
        // wavedrom ships no TypeScript declarations; without the shim the
        // lazy import fails the typecheck and the plugin never builds.
        expect(exists('frontend/src/types/wavedrom-shim.d.ts')).toBe(true);
    });

    it('package.json declares wavedrom and json5 as real dependencies', () => {
        // json5 is imported directly by the support utilities (WaveJSON is
        // JSON5); relying on the copy hoisted from wavedrom's own deps works
        // until a lockfile change silently stops hoisting it.
        const pkg = JSON.parse(read('frontend/package.json'));
        expect(pkg.dependencies.wavedrom).toBeDefined();
        expect(pkg.dependencies.json5).toBeDefined();
    });

    it('MarkdownRenderer routes the fence language, declares the type, and renders the case', () => {
        const src = read('frontend/src/components/MarkdownRenderer.tsx');
        // The three hops inside one file: fence-language routing, the token
        // type union, and the render case.  Any one missing means raw JSON
        // shown to the user with no error anywhere.
        expect(src).toMatch(/lang === 'wavedrom'/);
        expect(src).toMatch(/case 'wavedrom':/);
        expect(src).toMatch(/'wavedrom' \|/);
    });

    it('the lazy plugin registry has a wavedrom entry', () => {
        const src = read('frontend/src/plugins/d3/registry.ts');
        expect(src).toMatch(/name: 'wavedrom-renderer'/);
        expect(src).toMatch(/import\('\.\/wavedromPlugin'\)/);
    });

    it('visualizationTypes classifies wavedrom for capture/export', () => {
        // D3Renderer names its container after the plugin's spec type; a type
        // absent here misclassifies to the 'd3' fallback on capture.
        expect(VISUALIZATION_TYPES).toContain('wavedrom');
    });

    it('the Python exporter viz list includes wavedrom', () => {
        const src = read('app/utils/conversation_exporter.py');
        expect(src).toMatch(/'wavedrom'/);
    });

    it('the render_diagram MCP tool accepts wavedrom', () => {
        const src = read('app/mcp/tools/diagram_render.py');
        expect(src).toMatch(/"wavedrom"/);
    });

    it('the model is taught the vocabulary (prompt line + built-in skill)', () => {
        // Without these, both ends work but nothing ever emits the fence.
        expect(read('app/agents/prompts.py')).toMatch(/\(wavedrom\)/);
        expect(read('app/data/built_in_skills.py')).toMatch(/'timing_diagrams'/);
    });
});
