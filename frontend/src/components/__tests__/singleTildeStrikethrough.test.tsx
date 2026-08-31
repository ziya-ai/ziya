/**
 * @jest-environment jsdom
 *
 * PIN: conversational single tildes never render as strikethrough.
 *
 * DEFECT: "...dominated by 'Data Integrity' (~56%) and 'Service
 * Availability' (~79%)..." rendered with a strikethrough from the first
 * parenthetical to the second — the two `~` marks were paired as GFM
 * single-tilde strikethrough.
 *
 * ROOT CAUSE (verified empirically against marked 16.x): MarkdownRenderer
 * registers a double-tilde-only `del` tokenizer via `marked.use()`, but
 * `marked.lexer(src, opts)` — unlike `marked.parse(src, opts)` — REPLACES
 * the instance defaults with `opts` instead of merging.  The main token
 * pipeline lexed with a bare options object (`{ ...markedOptions, breaks }`),
 * silently dropping every use() override and reviving the built-in
 * single-tilde rule.  The fix spreads `marked.defaults` first.
 *
 * Like the other real-`marked` suites, this file is only meaningful under
 * the ESM transform override:
 *   CI=true npx craco test singleTildeStrikethrough --watchAll=false \
 *     --transformIgnorePatterns "node_modules/(?!(marked|uuid|react-diff-view)/)"
 * Under a plain `craco test` the ESM import cannot be parsed, so the suite
 * SELF-SKIPS rather than breaking the default run.
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
    useTheme: () => ({ isDarkMode: false, toggleTheme: () => {}, setTheme: () => {}, themeAlgorithm: undefined }),
}));
jest.mock('../../context/ProjectContext', () => ({
    useProject: () => ({ currentProject: { id: 'tilde', name: 'tilde', path: '/tilde' } }),
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
import { render } from '@testing-library/react';

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
    MarkdownRenderer = require('../MarkdownRenderer').MarkdownRenderer;
    ACTIVE = !!MarkdownRenderer;
} catch {
    ACTIVE = false;
}

const maybe = ACTIVE ? it : it.skip;

describe('single-tilde text is never strikethrough; double-tilde still is', () => {
    const SAMPLE =
        'dominated by "Data Integrity" (~56%) and "Service Availability" (~79%) issues';

    maybe('the exact reported sample renders with NO <del>', () => {
        const { container } = render(
            <MarkdownRenderer markdown={SAMPLE} enableCodeApply={false}
                isStreaming={false} forceRender={true} />
        );
        expect(container.querySelector('del')).toBeNull();
        // Positive control that the content actually rendered: both approx
        // percentages survive as literal text.
        expect(container.textContent).toContain('(~56%)');
        expect(container.textContent).toContain('(~79%)');
    });

    maybe('double-tilde strikethrough still works (the override is active, not disabled)', () => {
        const { container } = render(
            <MarkdownRenderer markdown={'keep ~~this struck~~ text'}
                enableCodeApply={false} isStreaming={false} forceRender={true} />
        );
        const del = container.querySelector('del');
        expect(del).not.toBeNull();
        expect(del!.textContent).toBe('this struck');
    });
});
