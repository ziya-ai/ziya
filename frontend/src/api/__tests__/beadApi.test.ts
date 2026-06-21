/**
 * Tests for the forkFromBead API client (beadApi.ts) — the typed wrapper
 * over POST /api/v1/projects/{pid}/chats/{cid}/beads/fork that the
 * "split from here" bead-popover action calls.  See design/bead-branching.md.
 *
 * This is the one unit-verifiable slice of step 3 (the React rendering is
 * confirmed visually, not here).  Mocks global.fetch in the jsdom harness.
 */
import { forkFromBead } from '../beadApi';

describe('forkFromBead', () => {
  const origFetch = global.fetch;
  afterEach(() => {
    global.fetch = origFetch;
    delete (window as any).__ZIYA_CURRENT_PROJECT_ID__;
    delete (window as any).__ZIYA_CURRENT_PROJECT_PATH__;
  });

  test('POSTs to the fork endpoint with the bead id and returns parsed JSON', async () => {
    (window as any).__ZIYA_CURRENT_PROJECT_ID__ = 'proj-1';
    const payload = {
      ok: true,
      new_chat_id: 'new-123',
      branchedFrom: 'src-1',
      branchedAtMessageIndex: 3,
      branchedFromLabel: 'microburst drops',
      message_count: 3,
      inherited_bead_count: 2,
    };
    const fetchMock = jest.fn().mockResolvedValue({ ok: true, json: async () => payload });
    global.fetch = fetchMock as any;

    const res = await forkFromBead('src-1', 'bead-x');

    expect(res).toEqual(payload);
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe('/api/v1/projects/proj-1/chats/src-1/beads/fork');
    expect(opts.method).toBe('POST');
    expect(JSON.parse(opts.body)).toEqual({ bead_id: 'bead-x' });
  });

  test('includes X-Project-Root header when the path global is set', async () => {
    (window as any).__ZIYA_CURRENT_PROJECT_ID__ = 'proj-1';
    (window as any).__ZIYA_CURRENT_PROJECT_PATH__ = '/home/u/proj';
    const fetchMock = jest.fn().mockResolvedValue({ ok: true, json: async () => ({}) });
    global.fetch = fetchMock as any;

    await forkFromBead('src-1', 'bead-x');

    expect(fetchMock.mock.calls[0][1].headers['X-Project-Root']).toBe('/home/u/proj');
  });

  test('throws with the status code on a non-ok response', async () => {
    global.fetch = jest.fn().mockResolvedValue({ ok: false, status: 400 }) as any;
    await expect(forkFromBead('src-1', 'bead-x')).rejects.toThrow(/Fork from bead failed: 400/);
  });

  test('defaults the project id to "default" when the global is unset', async () => {
    const fetchMock = jest.fn().mockResolvedValue({ ok: true, json: async () => ({}) });
    global.fetch = fetchMock as any;

    await forkFromBead('src-1', 'bead-x');

    expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/projects/default/chats/src-1/beads/fork');
  });
});
