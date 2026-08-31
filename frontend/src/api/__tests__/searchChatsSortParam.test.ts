/**
 * @jest-environment jsdom
 *
 * Transport seam: the search sort mode must reach the server's query string.
 *
 * WHY THIS EXISTS
 *
 * Conversation search ordering is chosen in the sidebar (MUIChatHistory's sort
 * selector), threaded through db.searchConversations, and finally has to land
 * in the URL that searchChats() builds.  The server honours a `sort` query
 * parameter and is tested for it separately (tests/test_api_chats_search.py),
 * and the ranking maths is tested on both sides
 * (tests/test_chat_search_scoring_parity.py).  Neither of those notices if
 * this hop silently drops the value.
 *
 * That is the expensive failure mode here: a `sort` option that is accepted by
 * every layer, type-checks everywhere, and is simply never put on the wire.
 * The selector would visibly change, a request would go out, results would
 * come back — ordered by the server default every time.  Nothing errors.
 *
 * These tests assert on the URL string actually handed to `fetch`, which is
 * the outermost observable surface of this module.
 */
import { searchChats } from '../conversationSyncApi';

const origFetch = global.fetch;
afterEach(() => {
  global.fetch = origFetch;
  delete (window as any).__ZIYA_CURRENT_PROJECT_PATH__;
});

/** Captures the URL passed to fetch and returns an empty result list. */
const capturingFetch = (body: unknown = []) =>
  jest.fn((_url: string, _init?: RequestInit) => Promise.resolve({
    ok: true,
    status: 200,
    json: () => Promise.resolve(body),
  } as unknown as Response));

/** Parse the query string off the single URL fetch was called with. */
const queryOf = (spy: jest.Mock): URLSearchParams => {
  expect(spy).toHaveBeenCalledTimes(1);
  const url = String(spy.mock.calls[0][0]);
  const qIndex = url.indexOf('?');
  expect(qIndex).toBeGreaterThan(-1);
  return new URLSearchParams(url.slice(qIndex + 1));
};

describe('searchChats — sort mode reaches the query string', () => {
  const PROJECT_ID = 'proj-1';

  it.each(['relevance', 'newest', 'oldest'] as const)(
    'puts sort=%s on the wire', async (mode) => {
      const spy = capturingFetch();
      global.fetch = spy as any;

      await searchChats(PROJECT_ID, 'quota', { sort: mode });

      expect(queryOf(spy).get('sort')).toBe(mode);
    });

  it('defaults to relevance when no sort is supplied', async () => {
    // The default must be explicit on the wire rather than omitted: the
    // server's own default is relevance, but relying on that means a change
    // to the server default silently changes the client's behaviour.
    const spy = capturingFetch();
    global.fetch = spy as any;

    await searchChats(PROJECT_ID, 'quota');

    expect(queryOf(spy).get('sort')).toBe('relevance');
  });

  it('sends the sort mode alongside, not instead of, the other parameters', async () => {
    // Guards against a params rewrite that drops an existing field while
    // adding sort — every one of these is load-bearing for the request.
    const spy = capturingFetch();
    global.fetch = spy as any;

    await searchChats(PROJECT_ID, 'quota policy', {
      sort: 'oldest',
      allProjects: true,
      caseSensitive: true,
      maxSnippetLength: 400,
    });

    const params = queryOf(spy);
    expect(params.get('q')).toBe('quota policy');
    expect(params.get('sort')).toBe('oldest');
    expect(params.get('all_projects')).toBe('true');
    expect(params.get('case_sensitive')).toBe('true');
    expect(params.get('max_snippet_length')).toBe('400');
  });

  it('targets the search endpoint for the requested project', async () => {
    // Positive control: without this, every assertion above would still pass
    // if the URL were malformed or pointed somewhere else entirely.
    const spy = capturingFetch();
    global.fetch = spy as any;

    await searchChats(PROJECT_ID, 'quota', { sort: 'newest' });

    const url = String(spy.mock.calls[0][0]);
    expect(url.split('?')[0]).toBe(`/api/v1/projects/${PROJECT_ID}/chats/search`);
  });

  it('still returns null (fallback signal) on transport failure', async () => {
    // The sort parameter must not have changed the failure contract: a null
    // return is what routes db.searchConversations to its local IndexedDB
    // scan.  If this started throwing, search would break outright when the
    // server is briefly unreachable instead of degrading.
    global.fetch = jest.fn(() => Promise.reject(new Error('network down'))) as any;

    await expect(searchChats(PROJECT_ID, 'quota', { sort: 'newest' }))
      .resolves.toBeNull();
  });

  it('returns the server payload unchanged when the request succeeds', async () => {
    // Ordering is the server's job in this path; the client must not re-sort
    // or reshape what came back, or the two would fight.
    const payload = [
      { conversationId: 'b', relevanceScore: 2, lastActivityAt: 9 },
      { conversationId: 'a', relevanceScore: 9, lastActivityAt: 1 },
    ];
    global.fetch = capturingFetch(payload) as any;

    const result = await searchChats(PROJECT_ID, 'quota', { sort: 'newest' });

    expect(result).toEqual(payload);
    expect((result ?? []).map((r: any) => r.conversationId)).toEqual(['b', 'a']);
  });
});
