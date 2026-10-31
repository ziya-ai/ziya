/**
 * scheduleApi paths and bodies must match app/api/schedules.py.  The
 * backend tests pin the server side; this pins the client side of the
 * same seam.
 */
import { scheduleApi } from '../scheduleApi';

type Call = { url: string; init?: RequestInit };

let calls: Call[];
const respond = (status: number, body?: unknown) =>
  jest.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    return {
      ok: status >= 200 && status < 300,
      status,
      json: async () => body,
    } as unknown as Response;
  });

beforeEach(() => { calls = []; });

describe('scheduleApi', () => {
  it('lists a project\'s schedules', async () => {
    (global as any).fetch = respond(200, { registrations: [], unregistered: [], scheduler: {} });
    const r = await scheduleApi.list('p 1');
    expect(calls[0].url).toBe('/api/v1/projects/p%201/schedules');
    expect(r.registrations).toEqual([]);
  });

  it('registers with an optional cadence', async () => {
    (global as any).fetch = respond(201, { card_id: 'c' });
    await scheduleApi.register('p', 'c', { cadence: { schedule_mode: 'daily_at', schedule_daily_at: '03:15' } });
    expect(calls[0].url).toBe('/api/v1/projects/p/schedules/c');
    expect(calls[0].init?.method).toBe('POST');
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({
      cadence: { schedule_mode: 'daily_at', schedule_daily_at: '03:15' },
    });
    await scheduleApi.register('p', 'c');
    expect(JSON.parse(String(calls[1].init?.body))).toEqual({});
  });

  it('patches enabled and keeps an explicit null max_runs in the body', async () => {
    (global as any).fetch = respond(200, { card_id: 'c' });
    await scheduleApi.update('p', 'c', { enabled: false });
    expect(calls[0].init?.method).toBe('PATCH');
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({ enabled: false });
    await scheduleApi.update('p', 'c', { cadence: { schedule_mode: 'interval', schedule_max_runs: null } });
    expect(JSON.parse(String(calls[1].init?.body)).cadence).toHaveProperty('schedule_max_runs', null);
  });

  it('unregisters with DELETE and tolerates the 204', async () => {
    (global as any).fetch = respond(204);
    await expect(scheduleApi.unregister('p', 'c')).resolves.toBeUndefined();
    expect(calls[0].init?.method).toBe('DELETE');
  });

  it('surfaces the server detail on failure', async () => {
    (global as any).fetch = respond(409, { detail: 'Card is already registered' });
    await expect(scheduleApi.register('p', 'c')).rejects.toThrow('Card is already registered');
  });

  it('fires a card now through the scheduler path', async () => {
    (global as any).fetch = respond(202, { run_id: 'r1', card_id: 'c' });
    const r = await scheduleApi.fire('p', 'c');
    expect(calls[0].url).toBe('/api/v1/projects/p/schedules/c/fire');
    expect(calls[0].init?.method).toBe('POST');
    expect(r.run_id).toBe('r1');
  });

  it('reads the scheduler lock holder', async () => {
    (global as any).fetch = respond(200, { held: true });
    await scheduleApi.schedulerStatus();
    expect(calls[0].url).toBe('/api/v1/scheduler/status');
  });
});
