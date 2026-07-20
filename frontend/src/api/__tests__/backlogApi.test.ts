/**
 * Tests for the backlogApi client (design/bead-backlog-browser.md).
 * Follows the mocked-fetch style of beadApi.test.ts.
 */
import { getBacklog, setBeadStatus } from '../backlogApi';

describe('backlogApi', () => {
  const origFetch = global.fetch;
  afterEach(() => {
    global.fetch = origFetch;
    delete (window as any).__ZIYA_CURRENT_PROJECT_ID__;
    delete (window as any).__ZIYA_CURRENT_PROJECT_PATH__;
  });

  describe('getBacklog', () => {
    test('GETs the backlog endpoint with the default parked status', async () => {
      (window as any).__ZIYA_CURRENT_PROJECT_ID__ = 'proj-1';
      const payload = { items: [], counts: { parked: 0, abandoned: 0 }, scanned_chats: 3 };
      const fetchMock = jest.fn().mockResolvedValue({ ok: true, json: async () => payload });
      global.fetch = fetchMock as any;

      const res = await getBacklog('proj-1');

      expect(res).toEqual(payload);
      const [url, opts] = fetchMock.mock.calls[0];
      expect(url).toBe('/api/v1/projects/proj-1/backlog?status=parked');
      expect(opts.method).toBeUndefined();
    });

    test('includes a custom status param, URL-encoded for comma-separated values', async () => {
      const fetchMock = jest.fn().mockResolvedValue({ ok: true, json: async () => ({}) });
      global.fetch = fetchMock as any;

      await getBacklog('proj-1', { status: 'parked,abandoned' });

      const [url] = fetchMock.mock.calls[0];
      expect(url).toBe('/api/v1/projects/proj-1/backlog?status=parked%2Cabandoned');
    });

    test('falls back to the project id global when projectId arg is empty', async () => {
      (window as any).__ZIYA_CURRENT_PROJECT_ID__ = 'proj-9';
      const fetchMock = jest.fn().mockResolvedValue({ ok: true, json: async () => ({}) });
      global.fetch = fetchMock as any;

      await getBacklog('');

      const [url] = fetchMock.mock.calls[0];
      expect(url).toBe('/api/v1/projects/proj-9/backlog?status=parked');
    });

    test('defaults project id to "default" when no global is set', async () => {
      const fetchMock = jest.fn().mockResolvedValue({ ok: true, json: async () => ({}) });
      global.fetch = fetchMock as any;

      await getBacklog('');

      const [url] = fetchMock.mock.calls[0];
      expect(url).toBe('/api/v1/projects/default/backlog?status=parked');
    });

    test('includes X-Project-Root header when the path global is set', async () => {
      (window as any).__ZIYA_CURRENT_PROJECT_PATH__ = '/home/u/proj';
      const fetchMock = jest.fn().mockResolvedValue({ ok: true, json: async () => ({}) });
      global.fetch = fetchMock as any;

      await getBacklog('proj-1');

      expect(fetchMock.mock.calls[0][1].headers['X-Project-Root']).toBe('/home/u/proj');
    });

    test('404 degrades to an empty backlog response rather than throwing', async () => {
      global.fetch = jest.fn().mockResolvedValue({ ok: false, status: 404 }) as any;

      const res = await getBacklog('proj-1');

      expect(res).toEqual({ items: [], counts: { parked: 0, abandoned: 0 }, scanned_chats: 0 });
    });

    test('throws on a non-404 non-ok response', async () => {
      global.fetch = jest.fn().mockResolvedValue({ ok: false, status: 500 }) as any;

      await expect(getBacklog('proj-1')).rejects.toThrow(/Get backlog failed: 500/);
    });
  });

  describe('setBeadStatus', () => {
    test('POSTs to the status endpoint with the chat id, bead id, and status body', async () => {
      (window as any).__ZIYA_CURRENT_PROJECT_ID__ = 'proj-1';
      const payload = { ok: true, bead: { id: 'bead-x', status: 'abandoned' } };
      const fetchMock = jest.fn().mockResolvedValue({ ok: true, json: async () => payload });
      global.fetch = fetchMock as any;

      const res = await setBeadStatus('proj-1', 'chat-1', 'bead-x', 'abandoned');

      expect(res).toEqual(payload);
      const [url, opts] = fetchMock.mock.calls[0];
      expect(url).toBe('/api/v1/projects/proj-1/chats/chat-1/beads/bead-x/status');
      expect(opts.method).toBe('POST');
      expect(JSON.parse(opts.body)).toEqual({ status: 'abandoned' });
    });

    test('supports flipping back to parked (restore)', async () => {
      const fetchMock = jest.fn().mockResolvedValue({ ok: true, json: async () => ({ ok: true }) });
      global.fetch = fetchMock as any;

      await setBeadStatus('proj-1', 'chat-1', 'bead-x', 'parked');

      const [, opts] = fetchMock.mock.calls[0];
      expect(JSON.parse(opts.body)).toEqual({ status: 'parked' });
    });

    test('throws with the status code on a non-ok response', async () => {
      global.fetch = jest.fn().mockResolvedValue({ ok: false, status: 400 }) as any;

      await expect(setBeadStatus('proj-1', 'chat-1', 'bead-x', 'abandoned')).rejects.toThrow(
        /Set bead status failed: 400/
      );
    });
  });
});
