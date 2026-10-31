/**
 * @jest-environment jsdom
 *
 * Parallel tool groups render as ONE container that holds their members.
 *
 * Previously the group was a separate "⫽ N reads in parallel" block placed
 * before the members, and its <!-- TOOL_GROUP:id --> anchor comment fell
 * through renderTokens' html case and printed as literal text.  Pinned here:
 * the ToolGroup states, and the end-to-end seam through MarkdownRenderer
 * (tokens -> groupToolTokens -> ToolGroup -> member ToolBlocks inside it,
 * markers invisible).
 */
const mockLexTokens: any[] = [];
jest.mock('marked', () => {
    const m: any = (s: string) => s;
    m.lexer = () => mockLexTokens.splice(0);
    m.parse = (s: string) => s;
    m.parseInline = (s: string) => s;
    m.setOptions = () => {};
    m.use = () => {};
    m.defaults = {};
    m.Lexer = class { static lex() { return []; } };
    return { marked: m, Lexer: m.Lexer, Tokens: {} };
});
jest.mock('uuid', () => ({ v4: () => 'group-uuid' }));
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
import { render, waitFor } from '@testing-library/react';
import { StreamingProvider } from '../../context/StreamingContext';
import { ToolGroup } from '../ToolGroup';

(global as any).IntersectionObserver = class { observe() {} unobserve() {} disconnect() {} takeRecords() { return []; } };
(global as any).ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
if (!window.matchMedia) {
    (window as any).matchMedia = (q: string) => ({
        matches: false, media: q, onchange: null, addEventListener() {}, removeEventListener() {},
        addListener() {}, removeListener() {}, dispatchEvent() { return false; },
    });
}

// eslint-disable-next-line @typescript-eslint/no-var-requires
const { MarkdownRenderer } = require('../MarkdownRenderer');

describe('ToolGroup states', () => {
    it('running: progress and count', () => {
        const { getByTestId, getByRole } = render(
            <ToolGroup size={4} done={1} end={null} streaming isDarkMode><div>child</div></ToolGroup>);
        expect(getByTestId('tool-group').getAttribute('data-state')).toBe('running');
        expect(getByTestId('tool-group').textContent).toContain('1 / 4 done');
        expect(getByRole('progressbar').getAttribute('aria-valuenow')).toBe('1');
    });
    it('done: summary with the saving, no progress bar', () => {
        const { getByTestId, queryByRole } = render(
            <ToolGroup size={4} done={4} end={{ wall_ms: 2400, serial_ms: 6300 }} streaming={false} isDarkMode={false}>
                <div>child</div>
            </ToolGroup>);
        const el = getByTestId('tool-group');
        expect(el.getAttribute('data-state')).toBe('done');
        expect(el.textContent).toContain('✓ 4 calls in parallel');
        expect(el.textContent).toContain('saved 3.9 s');
        expect(queryByRole('progressbar')).toBeNull();
    });
    it('a committed group that never closed reads as stopped, not running', () => {
        const { getByTestId } = render(
            <ToolGroup size={3} done={1} end={null} streaming={false} isDarkMode><div>child</div></ToolGroup>);
        expect(getByTestId('tool-group').getAttribute('data-state')).toBe('interrupted');
        expect(getByTestId('tool-group').textContent).toContain('1 / 3 finished');
    });
});

describe('MarkdownRenderer seam', () => {
    beforeEach(() => { (window as any).enableCodeApply = 'true'; });

    it('puts the members inside one container and shows no marker text', async () => {
        const fence = (n: string, body: string) => ({ type: 'code', lang: `tool:mcp_${n}|⫽ ${n}|text`, text: body, raw: body });
        mockLexTokens.push(
            { type: 'html', text: '<!-- TOOL_GROUP:legacy01 -->\n', raw: '' },
            { type: 'paragraph', text: 'before', raw: 'before', tokens: [{ type: 'text', text: 'before', raw: 'before' }] },
            { type: 'html', text: '<!-- TOOL_GROUP_START:g1|2 -->\n', raw: '' },
            fence('alpha', '⏳ Running...'),
            fence('beta', 'beta result'),
            { type: 'html', text: '<!-- TOOL_GROUP_END:g1|2|2400|6300 -->\n', raw: '' },
        );
        const { container } = render(
            <StreamingProvider isStreaming={false} isStreamingAny={false} currentConversationId="c" streamingConversations={new Set()}>
                <MarkdownRenderer markdown="x" enableCodeApply isStreaming={false} />
            </StreamingProvider>);
        await waitFor(() => expect(container.querySelector('[data-testid="tool-group"]')).not.toBeNull());
        const groups = container.querySelectorAll('[data-testid="tool-group"]');
        expect(groups).toHaveLength(1);
        const group = groups[0];
        expect(group.getAttribute('data-state')).toBe('done');
        // both members are INSIDE the container, the paragraph is not
        expect(group.textContent).toContain('alpha');
        expect(group.textContent).toContain('beta');
        expect(group.textContent).not.toContain('before');
        expect(container.textContent).toContain('before');
        // no marker leaks as text, legacy or new
        expect(container.textContent).not.toMatch(/TOOL_GROUP|<!--/);
    });
});
