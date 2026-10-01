/**
 * @jest-environment jsdom
 *
 * PIN: a list item that contains inline math still renders its OTHER inline
 * markdown (bold, italic, code spans), and math nested inside a bold span is
 * rendered rather than lost.
 *
 * Observed defect (PDF export of a 25-page report, 2026-09-30): every tight
 * list item containing `$…$` printed `**bold**`, `*italic*` and `` `code` ``
 * with their markers verbatim, and `**… $w$ …**` lost the `$w$`.  Paragraphs
 * with the same content were fine.
 *
 * Mechanism: marked emits a tight list item as a `text` token whose `.text`
 * is the RAW inline markdown and whose `.tokens` are the parsed children
 * (strong / codespan / text).  The `text` case in renderTokens checked for a
 * math marker BEFORE checking for nested tokens, so the raw string was
 * handed to renderWithInlineMath and the markdown around the math was
 * emitted as literal text.  Paragraph tokens never carry a raw `.text` with
 * children in the same way, which is why they were unaffected.
 *
 * Like the other real-`marked` suites, this file is only meaningful under the
 * ESM transform override:
 *   CI=true npx craco test listItemInlineMathMarkdown --watchAll=false \
 *     --transformIgnorePatterns "node_modules/(?!(marked|uuid|react-diff-view)/)"
 * Under a plain `craco test` it SELF-SKIPS.
 */
jest.mock('uuid', () => ({ v4: () => 'li-math-uuid' }));
jest.mock('prismjs/themes/prism.css', () => ({}), { virtual: true });
jest.mock('prismjs/themes/prism-tomorrow.css', () => ({}), { virtual: true });
jest.mock('react-diff-view/style/index.css', () => ({}), { virtual: true });
jest.mock('katex/dist/katex.min.css', () => ({}), { virtual: true });

jest.mock('../../context/ActiveChatContext', () => ({
    useActiveChat: () => ({
        reasoningContentMap: new Map(),
        currentConversationId: 'li-math-convo',
        currentMessages: [],
        throttlingRecoveryData: new Map(),
        addStreamingConversation: () => {},
        setThrottlingRecoveryData: () => {},
    }),
}));
jest.mock('../../context/StreamingContext', () => ({
    useStreamingContext: () => ({
        isStreaming: false, isStreamingAny: false,
        currentConversationId: 'li-math-convo', streamingConversations: new Set(),
    }),
}));
jest.mock('../../context/ThemeContext', () => ({
    useTheme: () => ({ isDarkMode: false, toggleTheme: () => {}, setTheme: () => {}, themeAlgorithm: undefined }),
}));
jest.mock('../../context/ProjectContext', () => ({
    useProject: () => ({ currentProject: { id: 'li', name: 'li', path: '/li' } }),
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

(global as any).IntersectionObserver = class { observe() {} unobserve() {} disconnect() {} takeRecords() { return []; } };
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
    const marked = require('marked').marked;
    MarkdownRenderer = require('../MarkdownRenderer').MarkdownRenderer;
    ACTIVE = typeof marked?.lexer === 'function' && !!MarkdownRenderer;
} catch {
    ACTIVE = false;
}

const maybe = ACTIVE ? it : it.skip;

const mathOriginals = (root: Element) =>
    Array.from(root.querySelectorAll('[data-math-original]')).map(el => el.getAttribute('data-math-original'));

describe('inline math inside list items keeps the surrounding inline markdown', () => {
    maybe('tight list item: bold, italic and code around $x$ are styled, not printed as markers', async () => {
        const md = '- **bold** and *italic* and `code` then $x$ tail\n- plain item';
        const { container } = render(
            <MarkdownRenderer markdown={md} enableCodeApply={true} isStreaming={false} forceRender={true} />
        );
        await waitFor(() => {
            expect(container.querySelector('[data-math-original], .katex-error')).toBeTruthy();
        }, { timeout: 15000 });

        const items = Array.from(container.querySelectorAll('li'));
        expect(items.length).toBe(2);
        const first = items[0];
        expect(first.querySelector('strong')?.textContent).toBe('bold');
        expect(first.querySelector('em')?.textContent).toBe('italic');
        expect(first.querySelector('code')?.textContent).toBe('code');
        // No raw markdown markers leaked as literal text.
        expect(first.textContent).not.toContain('**');
        expect(first.textContent).not.toContain('`');
        expect(first.textContent).not.toMatch(/\*italic\*/);
        expect(mathOriginals(first)).toContain('x');
        expect(container.querySelectorAll('.katex-error').length).toBe(0);
    });

    maybe('math nested inside a bold span in a list item is rendered inside the <strong>', async () => {
        const md = '- NP FPGA memory bounds **$w$ from above** here.';
        const { container } = render(
            <MarkdownRenderer markdown={md} enableCodeApply={true} isStreaming={false} forceRender={true} />
        );
        await waitFor(() => {
            expect(container.querySelector('[data-math-original], .katex-error')).toBeTruthy();
        }, { timeout: 15000 });

        const li = container.querySelector('li')!;
        const strong = li.querySelector('strong');
        expect(strong).toBeTruthy();
        expect(mathOriginals(strong!)).toEqual(['w']);
        expect(strong!.textContent).toContain('from above');
        expect(li.textContent).not.toContain('**');
        expect(container.querySelectorAll('.katex-error').length).toBe(0);
    });

    maybe('paragraph: bold and code around $x$ keep their styling (previously flattened silently)', async () => {
        const md = 'Lead **bold** and `code` then $x$ tail.';
        const { container } = render(
            <MarkdownRenderer markdown={md} enableCodeApply={true} isStreaming={false} forceRender={true} />
        );
        await waitFor(() => {
            expect(container.querySelector('[data-math-original], .katex-error')).toBeTruthy();
        }, { timeout: 15000 });
        const p = container.querySelector('p')!;
        expect(p.querySelector('strong')?.textContent).toBe('bold');
        expect(p.querySelector('code')?.textContent).toBe('code');
        expect(mathOriginals(p)).toEqual(['x']);
        expect(p.textContent).not.toContain('**');
        expect(container.querySelectorAll('.katex-error').length).toBe(0);
    });

    maybe('control: a list item with math but no other inline markdown still renders the math', async () => {
        const md = '- silence is $d_B$ long';
        const { container } = render(
            <MarkdownRenderer markdown={md} enableCodeApply={true} isStreaming={false} forceRender={true} />
        );
        await waitFor(() => {
            expect(container.querySelector('[data-math-original], .katex-error')).toBeTruthy();
        }, { timeout: 15000 });
        const li = container.querySelector('li')!;
        expect(mathOriginals(li)).toEqual(['d_B']);
        expect(li.textContent).toContain('silence is');
        expect(li.textContent).toContain('long');
    });
});
