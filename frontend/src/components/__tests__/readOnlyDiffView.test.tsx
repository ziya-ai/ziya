/**
 * @jest-environment jsdom
 *
 * ReadOnlyDiffView (MarkdownRenderer.tsx) — the chat diff renderer the
 * consent panel uses to show a file_write awaiting approval.
 *
 * Mounts the REAL DiffViewWrapper -> DiffView path, so it asserts the readOnly
 * prop actually reaches the place the Apply button and hunk pills are drawn,
 * not just that a prop exists.  Paired with a positive assertion that the
 * diff body rendered (add and remove lines), so "no Apply button" cannot pass
 * on an empty render.
 */

// The lexer hands back whatever the control test queues, so a chat diff fence
// can be rendered through the normal (applyable) path without the real marked.
const mockLexTokens: any[] = [];
jest.mock('marked', () => {
  const m: any = (s: string) => s;
  m.lexer = () => mockLexTokens.splice(0);
  m.parse = (s: string) => s;
  m.setOptions = () => {};
  m.use = () => {};
  m.Lexer = class { static lex() { return []; } };
  return { marked: m, Lexer: m.Lexer, Tokens: {} };
});
jest.mock('uuid', () => ({ v4: () => 'ro-uuid' }));
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
const { ReadOnlyDiffView } = require('../MarkdownRenderer');

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

describe('ReadOnlyDiffView', () => {
  beforeEach(() => { (window as any).enableCodeApply = 'true'; });

  it('renders the diff with add/remove lines but no Apply button or hunk pills', async () => {
    const { container } = render(<ReadOnlyDiffView diff={DIFF} elementId="consent-x" />);
    await waitFor(() => {
      expect(container.textContent).toContain('new_line = 2');
      expect(container.textContent).toContain('old_line = 1');
    }, { timeout: 8000 });
    expect(container.querySelector('[class*="insert"]')).not.toBeNull();
    expect(container.querySelector('[class*="delete"]')).not.toBeNull();
    // The file name header the chat diff shows is still there.
    expect(container.textContent).toContain('src.py');
    expect(container.textContent).not.toMatch(/Apply Changes|Applied ✓/);
    expect(container.querySelector('.hunk-status-indicator')).toBeNull();
  });

  // Control: the same diff as an ordinary chat fence DOES get the Apply
  // button and hunk pills, so the absence above is the readOnly prop and not
  // a render that never reached the header.
  it('control: the same diff in a chat message shows Apply and hunk pills', async () => {
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const { MarkdownRenderer } = require('../MarkdownRenderer');
    mockLexTokens.push({ type: 'code', lang: 'diff', text: DIFF, raw: '```diff\n' + DIFF + '```' });
    const { container } = render(
      <MarkdownRenderer markdown={'```diff\n' + DIFF + '```'} enableCodeApply isStreaming={false} forceRender />,
    );
    await waitFor(() => {
      expect(container.textContent).toContain('new_line = 2');
      expect(container.textContent).toMatch(/Apply Changes/);
    }, { timeout: 8000 });
    expect(container.querySelector('.hunk-status-indicator')).not.toBeNull();
  });
});
