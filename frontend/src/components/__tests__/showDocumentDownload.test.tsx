/**
 * @jest-environment jsdom
 *
 * Seam: a show_document block can (1) download the WHOLE file and (2) show
 * the whole file inline on demand, when the model showed only a line range.
 *
 * Both go through GET /api/v1/drag/file (the existing confined file read).
 * Asserts on the mounted ToolBlock and on what reaches the browser: the
 * request URL, the download's filename and blob text, the block's
 * data-showing state and header.  DIRECTION: against the unpatched code the
 * header has neither a "Download file" nor a "Show whole file" button.
 *
 * Mocks mirror showDocumentDragIcon.test.tsx (MarkdownRenderer's context needs).
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
import { render, fireEvent, waitFor } from '@testing-library/react';
import { TextEncoder as NodeTextEncoder, TextDecoder as NodeTextDecoder } from 'util';
import { createHash, webcrypto } from 'crypto';
import {
  documentDownloadName, fullDocumentMarkdown, isPartialDocument,
} from '../../utils/documentDownload';

if (!(global as any).TextEncoder) (global as any).TextEncoder = NodeTextEncoder;
if (!(global as any).TextDecoder) (global as any).TextDecoder = NodeTextDecoder;
if (!(globalThis as any).crypto?.subtle) {
  Object.defineProperty(globalThis, 'crypto', { value: webcrypto, configurable: true });
}

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

const doc = (fields: string, body: string) => `<!-- ziya:show_document ${fields} -->\n${body}`;
const sha = (s: string) => createHash('sha256').update(Buffer.from(s, 'utf8')).digest('hex');

const readBlob = (b: Blob) => new Promise<string>((resolve, reject) => {
  const r = new FileReader();
  r.onload = () => resolve(String(r.result));
  r.onerror = reject;
  r.readAsText(b);
});

const WHOLE = '# Report\n\nline 1\nline 2\nline 3\nline 4\nline 5\nline 6\nline 7\n';
const SHOWN = '# Report\n\nline 1';

/** fetch stub for GET /api/v1/drag/file serving `text`, or failing. */
function serveFile(text: string | null, mime = 'text/markdown; charset=utf-8') {
  const fetchMock = jest.fn(async (_url: string, _init?: RequestInit) => {
    if (text === null) {
      return { ok: false, status: 404, headers: { get: () => null },
        json: async () => ({ detail: 'File not found' }) };
    }
    const bytes = new NodeTextEncoder().encode(text);
    return {
      ok: true, status: 200,
      headers: { get: (k: string) => (k.toLowerCase() === 'content-type' ? mime : null) },
      arrayBuffer: async () => bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength),
    };
  });
  (global as any).fetch = fetchMock;
  return fetchMock;
}

const partialCaption = (fileSha: string) =>
  `path="Docs/report.md" lines="1-3" total_lines="9" bytes="17" sha256="${fileSha}"`;

describe('pure helpers', () => {
  it('documentDownloadName uses the basename, and marks a partial range', () => {
    expect(documentDownloadName({ path: 'Docs/CLITasks.md', lines: '1-340', total_lines: '340' }))
      .toBe('CLITasks.md');
    expect(documentDownloadName({ path: 'Docs/big.md', lines: '100-200', total_lines: '900' }))
      .toBe('big.lines-100-200.md');
    expect(documentDownloadName({ path: 'notes', lines: '1-5', total_lines: '9' }))
      .toBe('notes.lines-1-5');
    expect(documentDownloadName({})).toBe('document.md');
  });

  it('isPartialDocument is true only for a strict sub-range', () => {
    expect(isPartialDocument({ lines: '1-9', total_lines: '9' })).toBe(false);
    expect(isPartialDocument({ lines: '2-9', total_lines: '9' })).toBe(true);
    expect(isPartialDocument({})).toBe(false);
  });

  it('fullDocumentMarkdown keeps markdown as markdown and code as a code block', () => {
    expect(fullDocumentMarkdown('# T\n\nx\n\n', '# T')).toBe('# T\n\nx');
    // Shown as code (show_document's tilde fence): the whole file is wrapped the same way.
    expect(fullDocumentMarkdown('a = 1\nb = 2\n', '~~~python\na = 1\n~~~'))
      .toBe('~~~python\na = 1\nb = 2\n~~~');
    // A tilde run inside the file lengthens the wrapping fence.
    expect(fullDocumentMarkdown('~~~~ x\n', '~~~text\n~~~~ x\n~~~'))
      .toBe('~~~~~text\n~~~~ x\n~~~~~');
  });
});

describe('show_document block: Download file', () => {
  let created: Blob[];
  let clicked: string[];
  beforeEach(() => {
    created = [];
    clicked = [];
    (URL as any).createObjectURL = jest.fn((b: Blob) => { created.push(b); return 'blob:x'; });
    (URL as any).revokeObjectURL = jest.fn();
    jest.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      clicked.push(this.download);
    });
  });
  afterEach(() => jest.restoreAllMocks());

  it('downloads the WHOLE file even when only a range was shown', async () => {
    const fetchMock = serveFile(WHOLE);
    const { getByLabelText } = render(
      <ToolBlock toolName="show_document|Show Document"
        content={doc(partialCaption(sha(WHOLE)), SHOWN)} isDarkMode={false} />,
    );
    fireEvent.click(getByLabelText('Download file'));
    await waitFor(() => expect(clicked).toEqual(['report.md']));
    expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/drag/file?path=Docs%2Freport.md');
    expect(created[0].type).toMatch(/^text\/markdown/);
    expect(await readBlob(created[0])).toBe(WHOLE);
  });

  it('falls back to the shown text, named as a range, when the file cannot be read', async () => {
    serveFile(null);
    const ent = '&' + '#96;';
    const { getByLabelText } = render(
      <ToolBlock toolName="show_document|Show Document"
        content={doc(partialCaption(sha(WHOLE)), `${ent.repeat(3)}mermaid\ngraph TD\n${ent.repeat(3)}`)}
        isDarkMode={false} />,
    );
    fireEvent.click(getByLabelText('Download file'));
    await waitFor(() => expect(clicked).toEqual(['report.lines-1-3.md']));
    // Same body the block renders: nested fences restored.
    expect(await readBlob(created[0])).toBe('```mermaid\ngraph TD\n```');
  });
});

describe('show_document block: Show whole file', () => {
  it('is offered for a partial range and expands inline from the server', async () => {
    const fetchMock = serveFile(WHOLE);
    const { getByLabelText, getByTestId, queryByTestId } = render(
      <ToolBlock toolName="show_document|Show Document"
        content={doc(partialCaption(sha(WHOLE)), SHOWN)} isDarkMode={false} />,
    );
    const block = getByTestId('show-document');
    expect(block.getAttribute('data-showing')).toBe('shown');
    expect(block.textContent).toMatch(/lines 1-3 of 9/);
    fireEvent.click(getByLabelText('Show whole file'));
    await waitFor(() => expect(block.getAttribute('data-showing')).toBe('full'));
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(block.textContent).toMatch(/whole file, 9 lines/);
    expect(queryByTestId('show-document-changed')).toBeNull();
    // Collapse back to the shown range without refetching.
    fireEvent.click(getByLabelText('Show lines 1-3'));
    expect(block.getAttribute('data-showing')).toBe('shown');
    fireEvent.click(getByLabelText('Show whole file'));
    await waitFor(() => expect(block.getAttribute('data-showing')).toBe('full'));
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('says so when the file changed since it was shown', async () => {
    serveFile(WHOLE);
    const { getByLabelText, getByTestId } = render(
      <ToolBlock toolName="show_document|Show Document"
        content={doc(partialCaption(sha('an older version\n')), SHOWN)} isDarkMode={false} />,
    );
    fireEvent.click(getByLabelText('Show whole file'));
    await waitFor(() => expect(getByTestId('show-document-changed')).toBeTruthy());
  });

  it('is not offered when the whole file was already shown', () => {
    serveFile(WHOLE);
    const { queryByLabelText, getByLabelText } = render(
      <ToolBlock toolName="show_document|Show Document"
        content={doc('path="Docs/report.md" lines="1-9" total_lines="9"', WHOLE)} isDarkMode={false} />,
    );
    expect(queryByLabelText('Show whole file')).toBeNull();
    expect(getByLabelText('Download file')).toBeTruthy();
  });
});
