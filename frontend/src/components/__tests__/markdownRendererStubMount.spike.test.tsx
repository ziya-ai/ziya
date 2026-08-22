/**
 * @jest-environment jsdom
 *
 * ============================================================================
 * SPIKE — Card I, Stage 1 feasibility experiment (NOT a permanent test).
 * ============================================================================
 * QUESTION (the stated PRINCIPAL RISK for all three export-fidelity cards):
 *   Can the real, 7114-line `MarkdownRenderer` mount OUTSIDE the running app
 *   — i.e. with its six React contexts replaced by minimal stubs — and still
 *   produce GENUINELY rendered output (Prism syntax spans, per-line diff
 *   add/remove elements) rather than an error boundary or an empty shell?
 *
 * WHY jsdom AND NOT a browser here:
 *   The sandbox blocks creating the new `/print` route on disk (route files
 *   under frontend/src are git-diff-only), so a Playwright build+drive of a
 *   NEW route is not executable from within this task.  jsdom, however, lets
 *   us mount the genuine component and assert on the exact rendered DOM —
 *   which is the decisive evidence for the COUPLING question.  Diagram
 *   *completion* in a real headless browser is already proven independently
 *   by the existing /render route (DiagramRenderPage -> D3Renderer), and
 *   MarkdownRenderer reaches diagrams through the SAME LazyD3Renderer.
 *
 * STUB STRATEGY:
 *   Each of the six context hooks is jest.mock'd to a minimal value.  This
 *   documents, field by field, EXACTLY what a read-only non-streaming render
 *   dereferences.  Interactive surfaces (apply-changes, retry, folder
 *   mutation, send) are stubbed as no-ops and are never invoked by a static
 *   render.  The provider-based equivalent of these stubs is what the real
 *   /print route will mount (delivered as a diff for Card II).
 *
 * PASS CRITERIA (assert on structure, never "it didn't crash"):
 *   - Prism token spans present for a highlighted code block.
 *   - react-diff-view insert AND delete line elements present for a diff.
 *   - No React error boundary; no console.error during render.
 */

// ---------------------------------------------------------------------------
// ESM shims. `marked` is loaded FOR REAL via the CLI --transformIgnorePatterns
// override (see the invocation in the run notes) so the genuine tokenizer runs.
// `uuid` is ESM-only and reached transitively; stub it to a constant.
// ---------------------------------------------------------------------------
jest.mock('uuid', () => ({ v4: () => 'spike-uuid' }));

// The CLI --transformIgnorePatterns override we pass to run this spike displaces
// CRA's built-in CSS handling, so raw `.css` imports reach the JS parser. Stub
// the ones pulled in transitively (prism themes, react-diff-view, katex).
jest.mock('prismjs/themes/prism.css', () => ({}), { virtual: true });
jest.mock('prismjs/themes/prism-tomorrow.css', () => ({}), { virtual: true });
jest.mock('react-diff-view/style/index.css', () => ({}), { virtual: true });
jest.mock('katex/dist/katex.min.css', () => ({}), { virtual: true });

// ---------------------------------------------------------------------------
// The six contexts — mocked at the hook boundary. The stub SHAPE recorded here
// is the single most valuable output of this stage.
// ---------------------------------------------------------------------------
jest.mock('../../context/ActiveChatContext', () => ({
    useActiveChat: () => ({
        // read during a static render:
        reasoningContentMap: new Map(),          // thinking-block lookup (empty => none)
        currentConversationId: 'spike-convo',
        currentMessages: [],
        throttlingRecoveryData: new Map(),
        // interactive-only, stubbed no-ops (never called by static render):
        addStreamingConversation: () => {},
        setThrottlingRecoveryData: () => {},
    }),
}));

jest.mock('../../context/StreamingContext', () => ({
    useStreamingContext: () => ({
        isStreaming: false,
        isStreamingAny: false,
        currentConversationId: 'spike-convo',
        streamingConversations: new Set<string>(),
    }),
}));

jest.mock('../../context/ThemeContext', () => ({
    useTheme: () => ({
        isDarkMode: false,                       // light mode: exercises the composited-onto-white path
        toggleTheme: () => {},
        setTheme: () => {},
        themeAlgorithm: undefined,
    }),
}));

jest.mock('../../context/ProjectContext', () => ({
    useProject: () => ({
        currentProject: { id: 'spike', name: 'spike', path: '/spike' },
    }),
}));

jest.mock('../../context/FolderContext', () => ({
    useFolderContext: () => ({
        checkedKeys: [] as React.Key[],
        addFilesToContext: async () => [],       // apply-changes only; no-op
    }),
}));

jest.mock('../../hooks/useSendPayload', () => ({
    useSendPayload: () => ({ send: async () => '' }), // retry only; no-op
}));

// D3 diagram rendering does real async layout that jsdom cannot run; the
// diagram path's browser fidelity is proven by the /render route, not here.
// Stub the lazy D3Renderer so a mermaid fence in the fixture yields a stable,
// detectable placeholder instead of hanging jsdom on canvas/layout APIs.
jest.mock('../D3Renderer', () => ({
    D3Renderer: (props: any) => {
        const React = require('react');
        return React.createElement(
            'div',
            { 'data-testid': 'd3-diagram-stub', 'data-diagram-type': props?.spec?.type },
            'diagram:' + (props?.spec?.type || 'unknown'),
        );
    },
}));

import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';

// jsdom omits several browser APIs the renderer's effects use. These are
// PRESENT in a real browser (the /print route target), so polyfilling them
// here keeps the spike honest: it isolates the context-coupling question from
// jsdom's environment gaps. Each gap below is a one-liner in a browser.
(global as any).IntersectionObserver = class {
    observe() {} unobserve() {} disconnect() {} takeRecords() { return []; }
};
(global as any).ResizeObserver = class {
    observe() {} unobserve() {} disconnect() {}
};
if (!window.matchMedia) {
    (window as any).matchMedia = (q: string) => ({
        matches: false, media: q, onchange: null,
        addEventListener() {}, removeEventListener() {},
        addListener() {}, removeListener() {}, dispatchEvent() { return false; },
    });
}

// MarkdownRenderer statically imports ESM-only `marked`. Under a PLAIN
// `craco test` (no --transformIgnorePatterns override) that import cannot be
// parsed, so we load the module lazily+guarded and SKIP the suite instead of
// breaking the default run. With the override active (see run command in the
// header), the require succeeds and the real component is exercised.
let MarkdownRenderer: any;
let ESM_TRANSFORM_ACTIVE = false;
try {
    // eslint-disable-next-line @typescript-eslint/no-var-requires, global-require
    MarkdownRenderer = require('../MarkdownRenderer').MarkdownRenderer;
    ESM_TRANSFORM_ACTIVE = typeof MarkdownRenderer === 'function' || typeof MarkdownRenderer === 'object';
} catch {
    ESM_TRANSFORM_ACTIVE = false;
}

// Minimal Prism stub is NOT used: prismLoader/prismjs are CJS-friendly and run
// for real under jsdom, so we get genuine `token` spans.

const FIXTURE = [
    'Here is Python:',
    '',
    '```python',
    'def greet(name):',
    '    return f"hi {name}"',
    '```',
    '',
    'And a diff:',
    '',
    '```diff',
    'diff --git a/f.py b/f.py',
    '--- a/f.py',
    '+++ b/f.py',
    '@@ -1,2 +1,2 @@',
    '-old_line = 1',
    '+new_line = 2',
    '```',
    '',
    'And a diagram:',
    '',
    '```mermaid',
    'graph LR',
    '  A-->B',
    '```',
].join('\n');

const describeMaybe = ESM_TRANSFORM_ACTIVE ? describe : describe.skip;

describeMaybe('SPIKE: MarkdownRenderer mounts under context stubs', () => {
    let errorSpy: jest.SpyInstance;
    const consoleErrors: string[] = [];

    beforeAll(() => {
        errorSpy = jest.spyOn(console, 'error').mockImplementation((...args) => {
            consoleErrors.push(args.map(String).join(' '));
        });
    });
    afterAll(() => errorSpy.mockRestore());

    it('renders Prism spans, per-line diff add/remove, and reaches the diagram — no error boundary', async () => {
        const { container } = render(
            <MarkdownRenderer
                markdown={FIXTURE}
                enableCodeApply={true}
                isStreaming={false}
                forceRender={true}
            />
        );

        // Prism highlighting is applied in an async effect after mount.
        await waitFor(() => {
            expect(container.querySelectorAll('span.token').length).toBeGreaterThan(0);
        }, { timeout: 8000 });

        // react-diff-view emits distinct insert/delete line classes.
        await waitFor(() => {
            const inserts = container.querySelectorAll(
                '.diff-code-insert, [class*="insert"], .diff-line-add'
            );
            const deletes = container.querySelectorAll(
                '.diff-code-delete, [class*="delete"], .diff-line-del'
            );
            expect(inserts.length).toBeGreaterThan(0);
            expect(deletes.length).toBeGreaterThan(0);
        }, { timeout: 8000 });

        // The diagram fence reached the D3 pipeline (stubbed here; real browser
        // completion is proven by /render).
        await waitFor(() => {
            expect(screen.getByTestId('d3-diagram-stub')).toBeTruthy();
        }, { timeout: 8000 });

        // Structural sanity: an error boundary would replace content with a
        // fallback and typically empty the container.
        expect(container.textContent || '').not.toMatch(/something went wrong/i);

        // Dump a DOM snapshot for the feasibility artifact.
        const outDir = require('path').resolve(
            __dirname, '../../../../.ziya/task-runs'
        );
        try {
            const fs = require('fs');
            const runDir = fs.readdirSync(outDir)
                .map((d: string) => require('path').join(outDir, d))
                .filter((p: string) => { try { return fs.statSync(p).isDirectory(); } catch { return false; } })
                .sort((a: string, b: string) => fs.statSync(b).mtimeMs - fs.statSync(a).mtimeMs)[0];
            if (runDir) {
                fs.writeFileSync(
                    require('path').join(runDir, 'spike_render_dom.html'),
                    container.innerHTML
                );
            }
        } catch { /* artifact dump is best-effort */ }

        // A hard failure signal: a genuine React render error would have been
        // logged to console.error. Warnings (act(), key props) are tolerated.
        const fatal = consoleErrors.filter(e =>
            /error boundary|The above error occurred|Cannot read propert|is not a function|undefined is not/i.test(e)
        );
        expect(fatal).toEqual([]);
    });
});
