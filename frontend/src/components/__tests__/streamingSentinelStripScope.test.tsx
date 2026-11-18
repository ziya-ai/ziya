/**
 * @jest-environment jsdom
 *
 * While a message is streaming, MarkdownRenderer hides a tool call that is
 * still arriving: everything from the opening tool sentinel to the end of the
 * text is removed and "Preparing tool execution..." is shown.  The test for
 * "is a call arriving" was `text.includes(<opening sentinel>)` over the whole
 * message, with no regard for where the sentinel sat.
 *
 * Reported 2026-11-16 (conversation 037f0aa2): a shell tool printed a source
 * file that mentions the sentinel (app/utils/precision_prompt_system.py).
 * From that tool block on, the streaming view dropped every later tool block
 * and the whole final answer, including its diff.  The turn was then held for
 * that diff to be applied ("Waiting for 1 of 1 diff to be applied"), so the
 * stream never ended and the committed render, which does not strip, never
 * replaced it: the server waited on a diff the browser was not showing.
 *
 * The assertions are on the text MarkdownRenderer hands to the lexer, i.e.
 * after all of its real preprocessing, for a message mounted as streaming.
 */
const mockLexInputs: string[] = [];
jest.mock('marked', () => {
  const m: any = (s: string) => s;
  m.lexer = (s: string) => {
    mockLexInputs.push(s);
    return [{ type: 'paragraph', raw: s, text: s, tokens: [{ type: 'text', raw: s, text: s }] }];
  };
  m.parse = (s: string) => s;
  m.setOptions = () => {};
  m.use = () => {};
  m.defaults = {};
  m.Lexer = class { static lex() { return []; } };
  return { marked: m, Lexer: m.Lexer, Tokens: {} };
});
jest.mock('uuid', () => ({ v4: () => 'sentinel-uuid' }));
jest.mock('prismjs/themes/prism.css', () => ({}), { virtual: true });
jest.mock('prismjs/themes/prism-tomorrow.css', () => ({}), { virtual: true });
jest.mock('react-diff-view/style/index.css', () => ({}), { virtual: true });
jest.mock('katex/dist/katex.min.css', () => ({}), { virtual: true });
jest.mock('../../context/ActiveChatContext', () => ({
  useActiveChatOptional: () => null,
  useActiveChat: () => ({
    reasoningContentMap: new Map(), currentConversationId: 'c', currentMessages: [],
    throttlingRecoveryData: new Map(), addStreamingConversation: () => {},
    setThrottlingRecoveryData: () => {},
  }),
}));
jest.mock('../../context/StreamingContext', () => ({
  useStreamingContext: () => ({
    isStreaming: true, isStreamingAny: true,
    currentConversationId: 'c', streamingConversations: new Set(['c']),
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
jest.mock('../../hooks/useSendPayload', () => ({ useSendPayload: () => ({ send: async () => '' }) }));

import React from 'react';
import { act, render } from '@testing-library/react';

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

// Assembled so this file can be printed by a tool without itself containing
// the sentinel.
const OPEN = '<' + 'TOOL_SENTINEL>';
const CLOSE = '</' + 'TOOL_SENTINEL>';
const F4 = '`'.repeat(4);
const F3 = '`'.repeat(3);
const PREPARING = 'Preparing tool execution';

const TOOL_BLOCK = [
  `${F4}tool:mcp_run_shell_command|Shell: sed -n 330,340p prompt.py|bash`,
  '$ sed -n 330,340p prompt.py',
  `    "Do NOT write ${OPEN}, <name>, <arguments>, or any XML tool-calling markup. "`,
  F4,
].join('\n');
const DIFF = [
  `${F3}diff`,
  'diff --git a/src.py b/src.py',
  '--- a/src.py',
  '+++ b/src.py',
  '@@ -1,2 +1,2 @@',
  ' keep = 0',
  '-old_line = 1',
  '+new_line = 2',
  F3,
].join('\n');

async function lexInput(markdown: string, streaming = true): Promise<string> {
  mockLexInputs.length = 0;
  await act(async () => {
    render(<MarkdownRenderer markdown={markdown} isStreaming={streaming} forceRender={streaming}
      enableCodeApply />);
  });
  expect(mockLexInputs.length).toBeGreaterThan(0);   // the path under test ran
  return mockLexInputs[mockLexInputs.length - 1];
}

describe('streaming: an arriving tool call is hidden, quoted sentinel text is not', () => {
  it('a tool result that quotes the sentinel does not hide the answer and diff after it', async () => {
    const out = await lexInput(`Looking.\n\n${TOOL_BLOCK}\n\nTHE_ANSWER one diff below.\n\n${DIFF}\n\nAFTER_DIFF.`);
    expect(out).toContain('THE_ANSWER');
    expect(out).toContain('diff --git a/src.py b/src.py');
    expect(out).toContain('AFTER_DIFF');
    expect(out).not.toContain(PREPARING);
  });

  it('prose that names the sentinel in inline code keeps what follows', async () => {
    const out = await lexInput(`The marker is \`${OPEN}\` in the prompt.\n\nSTILL_HERE.`);
    expect(out).toContain('STILL_HERE');
    expect(out).not.toContain(PREPARING);
  });

  it('a code block still being written that contains the sentinel is kept', async () => {
    const out = await lexInput(`Example:\n\n${F3}python\nPROMPT = "never write ${OPEN}"\nMORE_CODE = 1`);
    expect(out).toContain('MORE_CODE = 1');
    expect(out).not.toContain(PREPARING);
  });

  it('control: a tool call arriving in prose is still hidden', async () => {
    const out = await lexInput(`Before the call.\n\n${OPEN}\n<name>mcp_run_shell_command</name>\n<arguments>{"command": "ls`);
    expect(out).toContain('Before the call.');
    expect(out).toContain(PREPARING);
    expect(out).not.toContain('mcp_run_shell_command');
    expect(out).not.toContain(OPEN);
  });

  it('a quoted sentinel earlier does not move where an arriving call is cut', async () => {
    const out = await lexInput(
      `${TOOL_BLOCK}\n\nKEPT_PROSE between.\n\n${OPEN}\n<name>mcp_file_read</name>\n<arguments>{"path": "a`);
    expect(out).toContain('KEPT_PROSE between.');
    expect(out).toContain('sed -n 330,340p prompt.py');     // the real tool block survives
    expect(out).toContain(PREPARING);
    expect(out).not.toContain('mcp_file_read');
  });

  it('a finished call followed by prose is not treated as arriving', async () => {
    const out = await lexInput(`${OPEN}\nnot a parseable call\n${CLOSE}\n\nPROSE_AFTER_CALL.`);
    expect(out).toContain('PROSE_AFTER_CALL');
    expect(out).not.toContain(PREPARING);
  });

  it('a committed message is never cut', async () => {
    const out = await lexInput(`Before.\n\n${OPEN}\n<name>x</name>\n\nCOMMITTED_TAIL.`, false);
    expect(out).toContain('COMMITTED_TAIL');
    expect(out).not.toContain(PREPARING);
  });
});
