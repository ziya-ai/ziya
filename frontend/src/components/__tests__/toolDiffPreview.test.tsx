/**
 * @jest-environment jsdom
 *
 * ToolDiffPreview (MarkdownRenderer.tsx) — the body of a `file write:` tool
 * block whose result is a patch.
 *
 * The seam: the tool block must route through the SAME renderer as a chat
 * diff (DiffViewWrapper -> DiffView -> DiffLine, Prism-highlighted, add/remove
 * colouring) with the Apply button suppressed, rather than through the
 * react-diff-view <Diff>/<Hunk> fallback that drew interleaved struck-through
 * lines with no syntax colouring.  DiffLine is the only path that stamps
 * data-testid="diff-line", so its presence is the discriminator; it is paired
 * with the caption and the add/remove lines so an empty render cannot pass.
 */

jest.mock('marked', () => {
  const m: any = (s: string) => s;
  m.lexer = () => [];
  m.parse = (s: string) => s;
  m.parseInline = (s: string) => s;
  m.walkTokens = () => {};
  m.setOptions = () => {};
  m.use = () => {};
  m.Lexer = class { static lex() { return []; } };
  return { marked: m, Lexer: m.Lexer, Tokens: {} };
});
jest.mock('uuid', () => ({ v4: () => 'tdp-uuid' }));
jest.mock('prismjs/themes/prism.css', () => ({}), { virtual: true });
jest.mock('prismjs/themes/prism-tomorrow.css', () => ({}), { virtual: true });
jest.mock('react-diff-view/style/index.css', () => ({}), { virtual: true });
jest.mock('katex/dist/katex.min.css', () => ({}), { virtual: true });

jest.mock('../../context/ActiveChatContext', () => {
  const chat = {
    reasoningContentMap: new Map(), currentConversationId: 'c', currentMessages: [],
    throttlingRecoveryData: new Map(), addStreamingConversation: () => {},
    setThrottlingRecoveryData: () => {},
  };
  return { useActiveChat: () => chat, useActiveChatOptional: () => chat };
});
jest.mock('../../context/StreamingContext', () => ({
  useStreamingContext: () => ({
    isStreaming: false, isStreamingAny: false, currentConversationId: 'c',
    streamingConversations: new Set<string>(),
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
import { render, waitFor } from '@testing-library/react';

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
const { ToolBlock } = require('../MarkdownRenderer');

// What mcpFormatter.formatFileWrite synthesises for a patch, and what
// chatApi persists as the tool fence body (re-routed to renderAs='diff' on
// reload by the non-JSON branch of formatFileWrite).
const PATCH_DIFF = [
  'diff --git a/app/x.py b/app/x.py',
  '--- a/app/x.py',
  '+++ b/app/x.py',
  '@@ -1,1 +1,1 @@',
  '-old_value = 1',
  '+new_value = 2',
].join('\n');

describe('file_write patch result in a tool block', () => {
  beforeEach(() => { (window as any).enableCodeApply = 'true'; });

  it('renders through the chat diff renderer (DiffLine), captioned, with no Apply button', async () => {
    const { container } = render(
      <ToolBlock toolName="mcp_file_write|file write: app/x.py" content={PATCH_DIFF} isDarkMode={false} />,
    );
    await waitFor(() => {
      expect(container.textContent).toContain('new_value = 2');
      expect(container.textContent).toContain('old_value = 1');
    }, { timeout: 8000 });

    // The chat diff renderer, not react-diff-view's <Hunk>.
    const lines = container.querySelectorAll('[data-testid="diff-line"]');
    expect(lines.length).toBeGreaterThanOrEqual(2);
    expect(container.querySelector('.diff-line-insert')).not.toBeNull();
    expect(container.querySelector('.diff-line-delete')).not.toBeNull();

    // Still says the write already happened, and offers nothing to apply.
    expect(container.textContent).toContain('already written to disk');
    expect(container.textContent).not.toMatch(/Apply Changes|Applied ✓/);
    expect(container.querySelector('.hunk-status-indicator')).toBeNull();
  });
});
