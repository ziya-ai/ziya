/**
 * Conversation-usage and run-resources fetchers (usageApi.ts).
 *
 * The Info tab used to call the plain conversation rollup, which a task
 * run's rows never matched.  These pin the two new routes and their
 * encoding, plus the run-scale duration formatter the panels share.
 */
import {
  fetchConversationUsage, fetchRunResources, formatDurationLong,
} from '../usageApi';

describe('conversation usage + run resources fetchers', () => {
  const realFetch = global.fetch;
  afterEach(() => { global.fetch = realFetch; });

  it('encodes ids and passes project_id only when given', async () => {
    const calls: string[] = [];
    global.fetch = jest.fn(async (url: string) => {
      calls.push(url);
      return { ok: true, json: async () => ({}) } as Response;
    }) as unknown as typeof fetch;
    await fetchConversationUsage('c/1', 'p 1');
    await fetchConversationUsage('c/1');
    await fetchRunResources('p 1', 'run/9');
    expect(calls).toEqual([
      '/api/usage/conversation/c%2F1?project_id=p%201',
      '/api/usage/conversation/c%2F1',
      '/api/v1/projects/p%201/task-runs/run%2F9/resources',
    ]);
  });

  it('surfaces the server detail on failure', async () => {
    global.fetch = jest.fn(async () =>
      ({ ok: false, status: 404, json: async () => ({ detail: 'Task run not found' }) } as Response),
    ) as unknown as typeof fetch;
    await expect(fetchRunResources('p', 'r')).rejects.toThrow('Task run not found');
  });
});

describe('formatDurationLong', () => {
  it('scales from ms to hours', () => {
    expect(formatDurationLong(null)).toBe('—');
    expect(formatDurationLong(850)).toBe('850ms');
    expect(formatDurationLong(42_000)).toBe('42s');
    expect(formatDurationLong(250_000)).toBe('4m 10s');
    expect(formatDurationLong(8_040_000)).toBe('2h 14m');
  });
});
