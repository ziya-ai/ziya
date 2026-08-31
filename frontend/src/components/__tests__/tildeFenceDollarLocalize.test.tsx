/**
 * @jest-environment jsdom
 *
 * DIAGNOSTIC (spec3-d1 localization): mount the REAL MarkdownRenderer on the
 * spec-3 tilde-fence snippet and inspect the rendered DOM to determine WHERE
 * the literal `$$a^2 + b^2 = c^2$$` inside a `~~~text` fence goes.
 *
 * Runs only under the ESM transform override (like escapedDollarMarkdownPipeline);
 * self-skips under the default runner.
 *   CI=true npx craco test tildeFenceDollarLocalize --watchAll=false \
 *     --transformIgnorePatterns "node_modules/(?!(marked|uuid|react-diff-view)/)"
 */
jest.mock('uuid', () => ({ v4: () => 'tilde-uuid' }));
jest.mock('prismjs/themes/prism.css', () => ({}), { virtual: true });
jest.mock('prismjs/themes/prism-tomorrow.css', () => ({}), { virtual: true });
jest.mock('react-diff-view/style/index.css', () => ({}), { virtual: true });
jest.mock('katex/dist/katex.min.css', () => ({}), { virtual: true });

jest.mock('../../context/ActiveChatContext', () => ({
    useActiveChat: () => ({
        reasoningContentMap: new Map(),
        currentConversationId: 'tilde-convo',
        currentMessages: [],
        throttlingRecoveryData: new Map(),
        addStreamingConversation: () => {},
        setThrottlingRecoveryData: () => {},
    }),
}));
jest.mock('../../context/StreamingContext', () => ({
    useStreamingContext: () => ({
        isStreaming: false, isStreamingAny: false,
        currentConversationId: 'tilde-convo', streamingConversations: new Set(),
    }),
}));
jest.mock('../../context/ThemeContext', () => ({
    useTheme: () => ({ isDarkMode: true, toggleTheme: () => {}, setTheme: () => {}, themeAlgorithm: undefined }),
}));
jest.mock('../../context/ProjectContext', () => ({
    useProject: () => ({ currentProject: { id: 't', name: 't', path: '/t' } }),
}));
jest.mock('../../context/FolderContext', () => ({
    useFolderContext: () => ({ checkedKeys: [], addFilesToContext: async () => [] }),
}));
jest.mock('../../hooks/useSendPayload', () => ({ useSendPayload: () => ({ send: async () => '' }) }));
jest.mock('../D3Renderer', () => ({
    D3Renderer: () => {
        const React = require('react');
        return React.createElement('div', { 'data-testid': 'd3-diagram-stub' }, 'diagram');
    },
}));

import React from 'react';
import { render, waitFor } from '@testing-library/react';

(global as any).IntersectionObserver = class {
    _cb: any;
    constructor(cb: any) { this._cb = cb; }
    observe(el: any) { this._cb && this._cb([{ isIntersecting: true, target: el, intersectionRatio: 1 }], this); }
    unobserve() {}
    disconnect() {}
    takeRecords() { return []; }
};
(global as any).ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
if (!window.matchMedia) {
    (window as any).matchMedia = (q: string) => ({
        matches: false, media: q, onchange: null,
        addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {}, dispatchEvent() { return false; },
    });
}

let MarkdownRenderer: any;
let ACTIVE = false;
try {
    // eslint-disable-next-line @typescript-eslint/no-var-requires, global-require
    MarkdownRenderer = require('../MarkdownRenderer').MarkdownRenderer;
    ACTIVE = !!MarkdownRenderer;
} catch {
    ACTIVE = false;
}
const maybe = ACTIVE ? it : it.skip;

const TILDE_SNIPPET = [
    '~~~text',
    'This is not math: $$a^2 + b^2 = c^2$$ stays literal.',
    '~~~',
].join('\n');

describe('spec3-d1 localize: tilde-fenced $$ in the real component DOM', () => {
    maybe('reports where the literal $$ goes', async () => {
        const { container } = render(
            <MarkdownRenderer markdown={TILDE_SNIPPET} enableCodeApply={true} isStreaming={false} forceRender={true} />
        );
        // Give the async render + any lazy math a moment.
        await waitFor(() => {
            expect(container.querySelector('pre, code, .math-display-block, .math-display-encoded, [data-math-original]')).toBeTruthy();
        }, { timeout: 15000 });
        // Extra settle for lazy KaTeX.
        await new Promise((r) => setTimeout(r, 300));

        const html = container.innerHTML;
        const codeText = Array.from(container.querySelectorAll('pre, code')).map(e => e.textContent).join(' || ');
        const diagnostics = {
            hasLiteralDollar: html.includes('$$a^2 + b^2 = c^2$$') || codeText.includes('$$a^2 + b^2 = c^2$$'),
            hasA2Text: codeText.includes('a^2 + b^2 = c^2') || html.includes('a^2 + b^2 = c^2'),
            mathDisplayBlockDivs: container.querySelectorAll('.math-display-block').length,
            mathDisplayEncodedDivs: container.querySelectorAll('.math-display-encoded').length,
            katex: container.querySelectorAll('.katex').length,
            katexError: container.querySelectorAll('.katex-error').length,
            hasMATH_DISPLAY_marker: html.includes('MATH_DISPLAY:'),
            preCount: container.querySelectorAll('pre').length,
            codeText: codeText.slice(0, 400),
        };
        // eslint-disable-next-line no-console
        console.log('TILDE_LOCALIZE_DIAGNOSTICS ' + JSON.stringify(diagnostics, null, 2));
        // The intent: the literal must be present in the code block.
        expect(diagnostics.hasLiteralDollar).toBe(true);
    });

    maybe('SETTLED path (forceRender=false, IntersectionObserver fires) also keeps the literal', async () => {
        const { container } = render(
            <MarkdownRenderer markdown={TILDE_SNIPPET} enableCodeApply={true} isStreaming={false} forceRender={false} />
        );
        await waitFor(() => {
            expect(container.querySelector('pre, code, .math-display-block, .math-display-encoded, [data-math-original]')).toBeTruthy();
        }, { timeout: 15000 });
        await new Promise((r) => setTimeout(r, 300));

        const html = container.innerHTML;
        const codeText = Array.from(container.querySelectorAll('pre, code')).map(e => e.textContent).join(' || ');
        const diagnostics = {
            path: 'settled(forceRender=false)',
            hasLiteralDollar: html.includes('$$a^2 + b^2 = c^2$$') || codeText.includes('$$a^2 + b^2 = c^2$$'),
            mathDisplayBlockDivs: container.querySelectorAll('.math-display-block').length,
            mathDisplayEncodedDivs: container.querySelectorAll('.math-display-encoded').length,
            katex: container.querySelectorAll('.katex').length,
            katexError: container.querySelectorAll('.katex-error').length,
            hasMATH_DISPLAY_marker: html.includes('MATH_DISPLAY:'),
            preCount: container.querySelectorAll('pre').length,
            codeText: codeText.slice(0, 400),
        };
        // eslint-disable-next-line no-console
        console.log('TILDE_LOCALIZE_SETTLED ' + JSON.stringify(diagnostics, null, 2));
        expect(diagnostics.hasLiteralDollar).toBe(true);
    });

    // Faithful to spec-3: a ```diff fence immediately precedes the ~~~ fence,
    // plus surrounding currency/mhchem prose. Rules out cross-fence interaction
    // that the isolated snippet cannot surface.
    const SPEC3_CONTEXT = [
        'The sulfate ion is $\\ce{SO4^2-}$ in solution.',
        '',
        '```diff',
        '- old = $price * qty',
        '+ new = $price * qty * $discount',
        '```',
        '',
        '~~~text',
        'This is not math: $$a^2 + b^2 = c^2$$ stays literal.',
        '~~~',
        '',
        'It costs $100 for the base and $200 for the upgrade.',
    ].join('\n');

    maybe('spec-3 diff+tilde context: settled path keeps the tilde literal AND diff literal', async () => {
        const { container } = render(
            <MarkdownRenderer markdown={SPEC3_CONTEXT} enableCodeApply={true} isStreaming={false} forceRender={false} />
        );
        await waitFor(() => {
            expect(container.querySelector('pre, code, [data-math-original]')).toBeTruthy();
        }, { timeout: 15000 });
        await new Promise((r) => setTimeout(r, 300));

        const html = container.innerHTML;
        const codeText = Array.from(container.querySelectorAll('pre, code')).map(e => e.textContent).join(' || ');
        const diagnostics = {
            path: 'spec3-context(settled)',
            tildeLiteralPresent: html.includes('$$a^2 + b^2 = c^2$$') || codeText.includes('$$a^2 + b^2 = c^2$$'),
            diffLiteralPresent: codeText.includes('$price') || html.includes('$price'),
            mathDisplayBlockDivs: container.querySelectorAll('.math-display-block').length,
            mathDisplayEncodedDivs: container.querySelectorAll('.math-display-encoded').length,
            katex: container.querySelectorAll('.katex').length,
            katexError: container.querySelectorAll('.katex-error').length,
            preCount: container.querySelectorAll('pre').length,
            codeText: codeText.slice(0, 500),
        };
        // eslint-disable-next-line no-console
        console.log('TILDE_LOCALIZE_SPEC3CTX ' + JSON.stringify(diagnostics, null, 2));
        expect(diagnostics.tildeLiteralPresent).toBe(true);
        expect(diagnostics.diffLiteralPresent).toBe(true);
    });
});
