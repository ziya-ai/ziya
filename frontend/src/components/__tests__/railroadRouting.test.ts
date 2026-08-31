/**
 * Source-contract guard for railroad diagram routing — same philosophy as
 * latexFenceRouting.test.ts: both ends of a pipeline can support a type while
 * a middle routing step silently drops the fence to a code block, and no
 * rendering test can catch that.  Every hop a ```railroad fence crosses is
 * asserted here, so a partially-wired integration fails CI instead of
 * degrading quietly.
 */
import * as fs from 'fs';
import * as path from 'path';

import { VISUALIZATION_TYPES } from '../../constants/visualizationTypes';

const REPO = path.resolve(__dirname, '../../../..');
const read = (p: string) => fs.readFileSync(path.join(REPO, p), 'utf8');
const exists = (p: string) => fs.existsSync(path.join(REPO, p));

describe('railroad fence routing crosses every seam', () => {
    it('layout engine and plugin wrapper both exist', () => {
        expect(exists('frontend/src/utils/d3Plugins/railroadPlugin.ts')).toBe(true);
        expect(exists('frontend/src/plugins/d3/railroadPlugin.ts')).toBe(true);
    });

    it('MarkdownRenderer routes the fence language, declares the type, and renders the case', () => {
        const src = read('frontend/src/components/MarkdownRenderer.tsx');
        // The three hops inside one file: fence-language routing, the token
        // type union, and the render case.  Any one missing means raw JSON
        // shown to the user with no error anywhere.
        expect(src).toMatch(/lang === 'railroad'/);
        expect(src).toMatch(/case 'railroad':/);
        expect(src).toMatch(/'railroad' \|/);
    });

    it('the lazy plugin registry has a railroad entry', () => {
        const src = read('frontend/src/plugins/d3/registry.ts');
        expect(src).toMatch(/name: 'railroad-renderer'/);
        expect(src).toMatch(/import\('\.\/railroadPlugin'\)/);
    });

    it('visualizationTypes classifies railroad for capture/export', () => {
        // D3Renderer names its container after the plugin's spec type; a type
        // absent here misclassifies to the 'd3' fallback on capture.
        expect(VISUALIZATION_TYPES).toContain('railroad');
    });

    it('the Python exporter viz list includes railroad', () => {
        const src = read('app/utils/conversation_exporter.py');
        expect(src).toMatch(/'railroad'/);
    });

    it('the render_diagram MCP tool accepts railroad', () => {
        const src = read('app/mcp/tools/diagram_render.py');
        expect(src).toMatch(/"railroad"/);
    });

    it('the model is taught the vocabulary (prompt line + built-in skill)', () => {
        // Without these, both ends work but nothing ever emits the fence.
        expect(read('app/agents/prompts.py')).toMatch(/\(railroad\)/);
        expect(read('app/data/built_in_skills.py')).toMatch(/'railroad_diagrams'/);
    });
});
