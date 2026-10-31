/**
 * @jest-environment jsdom
 *
 * bulkSync batches by serialized size, and a chat the server refuses as too
 * large (413) fails alone.
 *
 * Regression: batching was by count only (50).  One conversation large
 * enough to push its batch past the server's 20 MB RequestSizeMiddleware
 * limit made the whole POST 413, so every chat in that batch -- including a
 * brand-new chat, whose GET then 404'd -- stayed unsynced, on every poll.
 */
import {
  bulkSync, planBulkSyncBatches, utf8ByteLength, ServerChat,
} from '../conversationSyncApi';

const chat = (id: string, size = 10): ServerChat => ({
  id, title: id, createdAt: 1, lastActiveAt: 1,
  messages: [{ id: `${id}-m`, role: 'human', content: 'x'.repeat(size) }],
});

/** fetch stub: 413 when the body exceeds `limit` bytes, else ack every chat. */
function serverWithLimit(limit: number) {
  const bodies: string[] = [];
  (global as any).fetch = jest.fn(async (_url: string, init: RequestInit) => {
    const body = String(init.body);
    bodies.push(body);
    if (utf8ByteLength(body) > limit) {
      return { ok: false, status: 413, statusText: 'Request Entity Too Large', json: async () => ({}) };
    }
    const n = JSON.parse(body).chats.length;
    return { ok: true, status: 200, json: async () => ({ created: n, updated: 0, skipped: 0, errors: [] }) };
  });
  return bodies;
}

beforeEach(() => jest.spyOn(console, 'error').mockImplementation(() => {}));
afterEach(() => jest.restoreAllMocks());

describe('planBulkSyncBatches', () => {
  it('splits on bytes as well as count, and isolates an oversized chat', () => {
    const small = [chat('a'), chat('b'), chat('c')];
    expect(planBulkSyncBatches(small).map(b => b.ids)).toEqual([['a', 'b', 'c']]);

    const big = chat('BIG', 5000);
    const batches = planBulkSyncBatches([chat('a'), big, chat('b')], 2000);
    expect(batches.map(b => b.ids)).toEqual([['a'], ['BIG'], ['b']]);

    const many = Array.from({ length: 120 }, (_, i) => chat(`c${i}`));
    expect(planBulkSyncBatches(many).map(b => b.ids.length)).toEqual([50, 50, 20]);
  });

  it('counts UTF-8 bytes, not UTF-16 code units', () => {
    expect(utf8ByteLength('a')).toBe(1);
    expect(utf8ByteLength('é')).toBe(2);
    expect(utf8ByteLength('€')).toBe(3);
    expect(utf8ByteLength('🚀')).toBe(4);
  });
});

describe('bulkSync against a size-limited server', () => {
  it('body is byte-identical to JSON.stringify({chats})', async () => {
    const bodies = serverWithLimit(1e9);
    const chats = [chat('a'), chat('b')];
    await bulkSync('p', chats);
    expect(bodies).toEqual([JSON.stringify({ chats })]);
  });

  it('an oversized chat fails alone; the new chat beside it still syncs', async () => {
    // Server limit below the big chat but above the small ones together.
    serverWithLimit(3000);
    const res = await bulkSync('p', [chat('new-chat'), chat('BIG', 5000), chat('other')]);
    expect(res.created).toBe(2);
    expect(res.errors).toHaveLength(1);
    expect(res.errors[0].id).toBe('BIG');
    expect(res.errors[0].error).toMatch(/413.*request size limit/);
  });

  it('halves a batch the server refuses even when the planner thought it fit', async () => {
    // Every chat fits alone; any two together exceed the limit.
    const one = utf8ByteLength(JSON.stringify({ chats: [chat('a', 400)] }));
    const bodies = serverWithLimit(one + 50);
    const res = await bulkSync('p', ['a', 'b', 'c', 'd'].map(id => chat(id, 400)));
    expect(res.errors).toEqual([]);
    expect(res.created).toBe(4);
    expect(bodies.length).toBeGreaterThan(1);
  });

  it('a non-413 failure reports every chat in the batch by id', async () => {
    (global as any).fetch = jest.fn(async () => ({ ok: false, status: 500, statusText: '', json: async () => ({}) }));
    const res = await bulkSync('p', [chat('a'), chat('b')]);
    expect(res.errors.map(e => e.id)).toEqual(['a', 'b']);
  });
});
