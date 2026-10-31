/**
 * @jest-environment jsdom
 *
 * A committed diff must not remount when another turn starts or stops
 * streaming.
 *
 * DiffViewWrapper picked between two render branches with different element
 * trees using the GLOBAL StreamingContext.isStreaming.  That flag flips for
 * every message in the conversation at send and at stream end, so every
 * committed diff in history was torn down and rebuilt twice per turn.  The
 * memory probe (scripts/mem_probe/probe.mjs --domtrace) measured it on a
 * 100-message conversation: 12 diff-bearing messages, ~92 K elements
 * replaced, CDP Nodes 65 K -> 139 K at send and -> 180 K at stream end, with
 * no change to what the document showed.
 *
 * The real StreamingProvider is used (not a mocked hook) so the flag change
 * reaches DiffViewWrapper through its context subscription past memo, exactly
 * as in the app.  The test renders through MarkdownRenderer -> renderTokens ->
 * DiffToken -> DiffViewWrapper, the path Conversation.tsx uses for history.
 */

const mockLexTokens: any[] = [];
jest.mock('marked', () => {
  const m: any = (s: string) => s;
  m.lexer = () => mockLexTokens.splice(0);
  m.parse = (s: string) => s;
  m.setOptions = () => {};
  m.use = () => {};
  m.defaults = {};
  m.Lexer = class { static lex() { return []; } };
  return { marked: m, Lexer: m.Lexer, Tokens: {} };
});
jest.mock('uuid', () => ({ v4: () => 'remount-uuid' }));
jest.mock('prismjs/themes/prism.css', () => ({}), { virtual: true });
jest.mock('prismjs/themes/prism-tomorrow.css', () => ({}), { virtual: true });
jest.mock('react-diff-view/style/index.css', () => ({}), { virtual: true });
jest.mock('katex/dist/katex.min.css', () => ({}), { virtual: true });

jest.mock('../../context/ActiveChatContext', () => ({
  useActiveChat: () => ({
    reasoningContentMap: new Map(), currentConversationId: 'c', currentMessages: [],
    throttlingRecoveryData: new Map(), addStreamingConversation: () => {},
    setThrottlingRecoveryData: () => {},
  }),
}));
jest.mock('../../context/ThemeContext', () => ({
  useTheme: () => ({ isDarkMode: false, toggleTheme: () => {}, setTheme: () => {} }),
}));
jest.mock('../../context/ProjectContext', () => ({
  useProject: () => ({ currentProject: { id: 'p', name: 'p', path: '/p' } }),
}));
jest.mock('../../context/FolderContext', () => ({
  useFolderContext: () => ({ checkedKeys: [], addFilesToContext: async () => [] }),
}));
jest.mock('../../hooks/useSendPayload', () => ({
  useSendPayload: () => ({ send: async () => '' }),
}));

import React from 'react';
import { act, render, waitFor } from '@testing-library/react';
import { StreamingProvider } from '../../context/StreamingContext';

(global as any).IntersectionObserver = class {
  observe() {} unobserve() {} disconnect() {} takeRecords() { return []; }
};
(global as any).ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
if (!window.matchMedia) {
  (window as any).matchMedia = (q: string) => ({
    matches: false, media: q, onchange: null, addEventListener() {}, removeEventListener() {},
    addListener() {}, removeListener() {}, dispatchEvent() { return false; },
  });
}

// eslint-disable-next-line @typescript-eslint/no-var-requires
const { MarkdownRenderer } = require('../MarkdownRenderer');

const DIFF = [
  'diff --git a/src.py b/src.py',
  '--- a/src.py',
  '+++ b/src.py',
  '@@ -1,2 +1,2 @@',
  ' keep = 0',
  '-old_line = 1',
  '+new_line = 2',
  '',
].join('\n');
const FENCE = '```diff\n' + DIFF + '```';

const streamingSet = (on: boolean) => new Set<string>(on ? ['c'] : []);

const Tree: React.FC<{ globalStreaming: boolean }> = ({ globalStreaming }) => (
  <StreamingProvider
    isStreaming={globalStreaming}
    isStreamingAny={globalStreaming}
    currentConversationId="c"
    streamingConversations={streamingSet(globalStreaming)}
  >
    {/* A committed history message: Conversation.tsx passes isStreaming={false}. */}
    <MarkdownRenderer markdown={FENCE} enableCodeApply isStreaming={false} />
  </StreamingProvider>
);

// The rendered row for the inserted line.  Matched on the row container, not
// a leaf: Prism splits the line into token spans ("=" and "2" are their own
// elements), so no single leaf holds the whole text once highlighting runs.
const lineNode = (root: HTMLElement): Element | null =>
  Array.from(root.querySelectorAll('.diff-line-content')).find(
    el => (el.textContent || '').replace(/\s+/g, ' ').includes('new_line = 2'),
  ) || null;

describe('committed diff vs. the global streaming flag', () => {
  beforeEach(() => { (window as any).enableCodeApply = 'true'; });

  it('keeps the same DOM when another turn starts and ends streaming', async () => {
    mockLexTokens.push({ type: 'code', lang: 'diff', text: DIFF, raw: FENCE });
    const { container, rerender } = render(<Tree globalStreaming={false} />);

    await waitFor(() => expect(lineNode(container)).not.toBeNull(), { timeout: 8000 });
    // Settle the wrapper's parse effect so parsedFilesRef is populated: the
    // streaming branch is only taken once it holds files.
    await act(async () => { await new Promise(r => setTimeout(r, 50)); });
    const before = lineNode(container)!;
    const wrapperBefore = container.querySelector('[id^="diff-view-wrapper-"]');
    expect(wrapperBefore).not.toBeNull();

    // Next turn starts: the conversation is streaming, this message is not.
    rerender(<Tree globalStreaming={true} />);
    await act(async () => { await new Promise(r => setTimeout(r, 50)); });
    expect(lineNode(container)).not.toBeNull();            // still rendered
    expect(container.contains(before)).toBe(true);          // same node, not rebuilt
    expect(container.querySelector('[id^="diff-view-wrapper-"]')).toBe(wrapperBefore);

    // Turn ends.
    rerender(<Tree globalStreaming={false} />);
    await act(async () => { await new Promise(r => setTimeout(r, 50)); });
    expect(container.contains(before)).toBe(true);
    expect(container.querySelector('[id^="diff-view-wrapper-"]')).toBe(wrapperBefore);
  });
});
