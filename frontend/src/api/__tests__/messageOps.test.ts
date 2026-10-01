/**
 * Message-level operations (rewind / delete / patch) and the locator.
 *
 * bulk-sync merges by message id and can never remove a message or change
 * its content (app/utils/chat_message_ops.py), so every shrink or edit must
 * reach the server through these calls.  Before they existed, deletes and
 * edit-and-resubmit changed only the browser's copy (see the 2026-09-25
 * audit in design/conversation-single-writer.md).  The server half is
 * covered by tests/test_chat_message_endpoints.py; this pins the wire shape
 * the router expects, so the two halves meet.
 */
import {
  messageLocator, ownerProjectId, rewindMessages, deleteMessage, patchMessage,
} from '../conversationSyncApi';

type Call = { url: string; init: RequestInit };
let calls: Call[];

function mockFetch(status = 200, body: any = { ok: true, _version: 42, messageCount: 1 }) {
  (global as any).fetch = jest.fn(async (url: string, init: RequestInit) => {
    calls.push({ url, init });
    return {
      ok: status >= 200 && status < 300,
      status,
      statusText: String(status),
      json: async () => body,
    } as any;
  });
}

beforeEach(() => { calls = []; mockFetch(); });

describe('messageLocator', () => {
  it('carries the id plus the legacy fallback fields', () => {
    const loc = messageLocator([{ id: 'm1', role: 'human', content: 'hello' }], 0);
    expect(loc).toEqual({ messageId: 'm1', index: 0, role: 'human', contentHead: 'hello', contentLength: 5 });
  });

  it('counts code points, not UTF-16 units, to match the server (Python len)', () => {
    const loc = messageLocator([{ role: 'human', content: 'a😀b' }], 0);
    expect(loc.contentLength).toBe(3);
    expect(loc.messageId).toBeUndefined();
  });

  it('caps the head at 200 code points', () => {
    const loc = messageLocator([{ role: 'assistant', content: 'x'.repeat(500) }], 0);
    expect(Array.from(loc.contentHead!).length).toBe(200);
    expect(loc.contentLength).toBe(500);
  });
});

describe('ownerProjectId', () => {
  it("prefers the conversation's own project (global chats live at home)", () => {
    expect(ownerProjectId({ projectId: 'home' }, 'viewing')).toBe('home');
    expect(ownerProjectId(undefined, 'viewing')).toBe('viewing');
  });

  it('falls back to the project ProjectContext publishes on window', () => {
    // MessageActions must not subscribe to useProject (MessageActions.test),
    // so this fallback is the only way it learns the viewing project.
    (window as any).__ZIYA_CURRENT_PROJECT_ID__ = 'published';
    try {
      expect(ownerProjectId(undefined)).toBe('published');
      expect(ownerProjectId({ projectId: 'home' })).toBe('home');
    } finally {
      delete (window as any).__ZIYA_CURRENT_PROJECT_ID__;
    }
  });
});

describe('wire shape', () => {
  const loc = { messageId: 'm2', index: 2, role: 'human', contentHead: 'q', contentLength: 1 };

  it('rewind POSTs locator + edit to /chats/{id}/rewind', async () => {
    const r = await rewindMessages('p1', 'c1', loc, { text: 'edited', images: null });
    expect(r).toMatchObject({ ok: true, _version: 42 });
    expect(calls[0].url).toBe('/api/v1/projects/p1/chats/c1/rewind');
    expect(calls[0].init.method).toBe('POST');
    expect(JSON.parse(calls[0].init.body as string)).toEqual({ ...loc, text: 'edited', images: null });
  });

  it('delete uses the id in the path, "_" when the message has none', async () => {
    await deleteMessage('p1', 'c1', loc);
    await deleteMessage('p1', 'c1', { ...loc, messageId: undefined });
    expect(calls[0].url).toBe('/api/v1/projects/p1/chats/c1/messages/m2');
    expect(calls[1].url).toBe('/api/v1/projects/p1/chats/c1/messages/_');
    expect(calls[0].init.method).toBe('DELETE');
  });

  it('patch sends content and images', async () => {
    await patchMessage('p1', 'c1', loc, { content: 'fixed', images: null });
    expect(calls[0].init.method).toBe('PATCH');
    expect(JSON.parse(calls[0].init.body as string)).toMatchObject({ content: 'fixed', images: null });
  });

  it('reports 404 (chat not synced yet) without throwing', async () => {
    mockFetch(404, { detail: 'Chat not found' });
    const r = await deleteMessage('p1', 'c1', loc);
    expect(r).toEqual({ ok: false, status: 404 });
  });

  it('does nothing without a project id', async () => {
    const r = await rewindMessages(undefined, 'c1', loc);
    expect(r).toEqual({ ok: false, status: 0 });
    expect(calls).toHaveLength(0);
  });
});
