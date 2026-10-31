/**
 * @jest-environment jsdom
 *
 * A collapsed tool block does no rendering work on its raw body.
 *
 * GenericToolBlock showed only the digest preview while collapsed, but its
 * Prism and marked.parse effects ran on mount regardless, and the header
 * effect inline-parsed `summary || content` -- the whole payload when there
 * was no summary -- for a header only the hierarchical-results branch shows.
 * Every tool result in history paid full highlight/parse cost on mount and
 * kept the HTML string in state.  The work now waits for the first expand.
 *
 * Shell / shadow tools render through TerminalPane instead (no Prism), so
 * the fixtures use a generic tool name to reach GenericToolBlock.
 */
// Plain arrays, not jest.fn: CRA's jest config sets resetMocks, which wipes
// a jest.fn implementation before each test (parseInline then returned
// undefined and the component called .then on it).
const mockParseCalls: string[] = [];
const mockParseInlineCalls: string[] = [];
const mockHighlightCalls: string[] = [];
jest.mock('marked', () => {
    const m: any = (s: string) => s;
    m.lexer = () => [];
    m.parse = (s: string) => { mockParseCalls.push(s); return s; };
    m.parseInline = (s: string) => { mockParseInlineCalls.push(s); return s; };
    m.setOptions = () => {};
    m.use = () => {};
    m.defaults = {};
    m.Lexer = class { static lex() { return []; } };
    return { marked: m, Lexer: m.Lexer, Tokens: {} };
});
jest.mock('../../utils/prismLoader', () => ({ loadPrismLanguage: async () => {} }));
jest.mock('uuid', () => ({ v4: () => 'u' }));
jest.mock('react-diff-view/style/index.css', () => ({}), { virtual: true });
jest.mock('katex/dist/katex.min.css', () => ({}), { virtual: true });
jest.mock('../../context/ActiveChatContext', () => ({
    useActiveChat: () => ({
        currentConversationId: 'c', currentMessages: [], throttlingRecoveryData: new Map(),
        addStreamingConversation: () => {}, setThrottlingRecoveryData: () => {},
    }),
}));
jest.mock('../../context/ThemeContext', () => ({
    useTheme: () => ({ isDarkMode: true, toggleTheme: () => {}, setTheme: () => {} }),
}));
jest.mock('../../context/ProjectContext', () => ({
    useProject: () => ({ currentProject: { id: 'p', name: 'p', path: '/p' } }),
}));
jest.mock('../../context/FolderContext', () => ({
    useFolderContext: () => ({ checkedKeys: [], addFilesToContext: async () => [] }),
}));
jest.mock('../../hooks/useSendPayload', () => ({ useSendPayload: () => ({ send: async () => '' }) }));
jest.mock('../../utils/shadowPresence', () => ({
    useShadowPresence: () => ({ inPlay: false, key: '' }),
    markShadowUsed: () => {},
}));

import React from 'react';
import * as fs from 'fs';
import * as path from 'path';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';

(global as any).IntersectionObserver = class { observe() {} unobserve() {} disconnect() {} takeRecords() { return []; } };
(global as any).ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
if (!window.matchMedia) {
    (window as any).matchMedia = (q: string) => ({
        matches: false, media: q, onchange: null, addEventListener() {}, removeEventListener() {},
        addListener() {}, removeListener() {}, dispatchEvent() { return false; },
    });
}

// eslint-disable-next-line @typescript-eslint/no-var-requires
const { ToolBlock } = require('../MarkdownRenderer');

beforeEach(() => {
    mockParseCalls.length = 0;
    mockParseInlineCalls.length = 0;
    mockHighlightCalls.length = 0;
    (window as any).Prism = {
        languages: { python: {} },
        highlight: (code: string) => { mockHighlightCalls.push(code); return '<span class="token marker-hl">HL</span>'; },
    };
});

const flush = () => act(async () => { await new Promise(r => setTimeout(r, 0)); });
const expand = () => fireEvent.click(screen.getByText(/▶ Expand/));

const PY = Array.from({ length: 60 }, (_, i) => `value_${i} = compute(${i})`).join('\n');
const MD = Array.from({ length: 40 }, (_, i) => `**item ${i}** see [link](http://x/${i})`).join('\n');

describe('collapsed tool block', () => {
    it('does not highlight until expanded, then highlights once and shows it', async () => {
        const { container } = render(
            <ToolBlock toolName="mcp_custom_lookup|Lookup: cat big.py|python" content={PY} isDarkMode />);
        await flush();
        // The collapsed path did run: the expand control is there.
        expect(screen.getByText(/▶ Expand/)).toBeTruthy();
        expect(mockHighlightCalls).toHaveLength(0);
        expand();
        await waitFor(() => expect(mockHighlightCalls).toHaveLength(1));
        await waitFor(() => expect(container.querySelector('.marker-hl')).not.toBeNull());
    });

    it('does not markdown-parse the body until expanded', async () => {
        render(<ToolBlock toolName="mcp_custom_lookup|Lookup: echo" content={MD} isDarkMode />);
        await flush();
        expect(screen.getByText(/▶ Expand/)).toBeTruthy();
        expect(mockParseCalls).toHaveLength(0);
        expand();
        await waitFor(() => expect(mockParseCalls).toContain(MD));
    });

    it('never inline-parses the payload for a header only hierarchical results show', async () => {
        // No summary (short, uncollapsed), so the old fallback parsed `content`.
        const SHORT = '**done** see [x](http://x)';
        render(<ToolBlock toolName="mcp_custom_lookup|Lookup: echo" content={SHORT} isDarkMode />);
        await flush();
        expect(mockParseInlineCalls).not.toContain(SHORT);
        // Positive: the block rendered its body (markdown path ran on mount).
        expect(mockParseCalls).toContain(SHORT);
    });

    it('a short result that is never collapsed still highlights on mount', async () => {
        const { container } = render(
            <ToolBlock toolName="mcp_custom_lookup|Lookup: cat a.py|python" content="x = 1" isDarkMode />);
        await waitFor(() => expect(mockHighlightCalls).toHaveLength(1));
        await waitFor(() => expect(container.querySelector('.marker-hl')).not.toBeNull());
    });
});

describe('wiring', () => {
    const src = fs.readFileSync(path.join(__dirname, '..', 'MarkdownRenderer.tsx'), 'utf8');
    it('keeps the size cap for expanded results', () => {
        expect(src).toMatch(/const TOOL_HIGHLIGHT_MAX_CHARS = 150_000;/);
    });
    it('gates both body effects on bodyShown', () => {
        expect(src).toMatch(/if \(shouldRenderAsMarkdown && bodyShown\)/);
        expect(src).toMatch(/if \(hasSyntaxHighlighting && bodyShown\)/);
    });
});
