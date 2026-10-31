/**
 * @jest-environment jsdom
 *
 * Seam: a show_document block's 📄 is a drag source for the shown file.
 *
 * DocumentBlock (MarkdownRenderer.tsx) used to print the glyph as plain
 * text; only the `file write:` header chip could be dragged out.  Asserts on
 * the mounted ToolBlock, not on FileDragIcon alone: the caption's `path`
 * must reach the icon.  DIRECTION: against the unpatched code the header
 * contains no `.file-drag-icon` and nothing draggable.
 *
 * Mocks mirror toolDiffPreview.test.tsx (MarkdownRenderer's context needs).
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
jest.mock('uuid', () => ({ v4: () => 'sdd-uuid' }));
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
import { render } from '@testing-library/react';

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

const caption = (fields: string) => `<!-- ziya:show_document ${fields} -->\n# Report\n\nbody`;

describe('show_document block: the 📄 glyph drags the shown file', () => {
  it('mounts a draggable icon wired to the caption path', () => {
    const content = caption('path="Docs/report.csv" lines="1-3" total_lines="3" bytes="12" sha256="abcdef123456"');
    const { container } = render(
      <ToolBlock toolName="show_document|Show Document" content={content} isDarkMode={false} />,
    );
    const block = container.querySelector('[data-testid="show-document"]') as HTMLElement;
    expect(block).not.toBeNull();
    const icon = block.querySelector('.file-drag-icon') as HTMLElement;
    expect(icon).not.toBeNull();
    expect(icon.textContent).toBe('📄');
    expect(icon.draggable).toBe(true);
    expect(icon.getAttribute('data-file-drag')).toBe('true');
    expect(icon.getAttribute('title')).toContain('report.csv');
    // The path text beside it is not itself the drag source.
    expect(block.querySelector('strong')!.textContent).toBe('Docs/report.csv');
    expect((block.querySelector('strong') as HTMLElement).draggable).toBe(false);
  });

  it('keeps an inert glyph when the caption names no path', () => {
    const { container } = render(
      <ToolBlock toolName="show_document|Show Document" content={caption('bytes="12"')} isDarkMode={false} />,
    );
    const block = container.querySelector('[data-testid="show-document"]') as HTMLElement;
    expect(block.querySelector('.file-drag-icon')).toBeNull();
    expect(block.textContent).toContain('📄');
    expect(block.querySelector('[data-file-drag]')).toBeNull();
  });
});
