/**
 * @jest-environment jsdom
 *
 * Unit tests for the shared sync request deadline (api/timedFetch.ts).
 *
 * WHY THIS EXISTS
 *
 * A browser `fetch` has no default timeout.  A request can sit pending
 * indefinitely — laptop sleep/wake, a proxy that half-closes, a network
 * change mid-flight — and the browser will never fail it for you.
 * ChatContext's periodic sync arms `periodicSyncInFlightRef` before its
 * `try` and clears it only in the `finally`, so a request that never
 * settles means the `finally` never runs and every subsequent 30s tick
 * returns at the in-flight guard.  Cross-instance updates then stop
 * arriving for the life of the page, with no error logged anywhere.
 *
 * These tests use REAL timers with small explicit budgets (20-40ms) rather
 * than fake timers, because the behaviour under test spans a `fetch`
 * promise, a body-stream promise and a `setTimeout` — interleaving those
 * three by hand under fake timers tests the harness more than the code.
 *
 * NOTE: requires the timedFetch.ts diff to be applied; this suite fails
 * with "module not found" against an unpatched tree.
 */
import {
  timedFetchJson,
  SyncTimeoutError,
  SyncHttpError,
} from '../timedFetch';

const origFetch = global.fetch;
afterEach(() => { global.fetch = origFetch; });

/** Models a real fetch that never settles until its signal aborts. */
const neverSettlingFetch = () => jest.fn((_url: string, init: RequestInit) =>
  new Promise((_resolve, reject) => {
    init.signal?.addEventListener('abort',
      () => reject(new DOMException('The operation was aborted.', 'AbortError')));
  }));

/** Models headers arriving promptly but the BODY stream stalling. */
const stalledBodyFetch = (onBodyAbort: () => void) =>
  jest.fn((_url: string, init: RequestInit) => Promise.resolve({
    ok: true,
    status: 200,
    statusText: 'OK',
    json: () => new Promise((_resolve, reject) => {
      init.signal?.addEventListener('abort', () => {
        onBodyAbort();
        reject(new DOMException('The operation was aborted.', 'AbortError'));
      });
    }),
  }));

describe('timedFetchJson — the deadline fires', () => {
  it('rejects with SyncTimeoutError when the request never settles', async () => {
    global.fetch = neverSettlingFetch() as any;
    await expect(timedFetchJson('/probe', {}, 20, 'probeCall'))
      .rejects.toBeInstanceOf(SyncTimeoutError);
  });

  it('names the call and the budget, so the log identifies the wedge', async () => {
    global.fetch = neverSettlingFetch() as any;
    // A bare "AbortError" in the console is indistinguishable from a
    // user-cancelled request; the label is what makes it diagnosable.
    await expect(timedFetchJson('/probe', {}, 20, 'listChats'))
      .rejects.toThrow(/listChats/);
    await expect(timedFetchJson('/probe', {}, 20, 'listChats'))
      .rejects.toThrow(/20/);
  });

  it('aborts a stalled BODY read, not only a stalled request', async () => {
    // The trap this guards: clearing the deadline as soon as the headers
    // arrive leaves `res.json()` unguarded, and a stalled response stream
    // hangs the await exactly as a stalled request does — same wedge, one
    // layer down.  The timer must span the body read.
    let bodyAborted = false;
    global.fetch = stalledBodyFetch(() => { bodyAborted = true; }) as any;
    await expect(timedFetchJson('/probe', {}, 25, 'probeCall'))
      .rejects.toBeInstanceOf(SyncTimeoutError);
    expect(bodyAborted).toBe(true);
  });

  it('passes an AbortSignal to fetch (mechanism is actually wired)', async () => {
    // Positive control: without a signal the abort could never take effect,
    // and every rejection above would be the harness rejecting itself.
    let seen: AbortSignal | undefined;
    global.fetch = jest.fn((_url: string, init: RequestInit) => {
      seen = init.signal ?? undefined;
      return Promise.resolve({ ok: true, status: 200, json: async () => ({}) });
    }) as any;
    await timedFetchJson('/probe', {}, 50, 'probeCall');
    expect(seen).toBeInstanceOf(AbortSignal);
  });
});

describe('timedFetchJson — the deadline is released', () => {
  it('clears its deadline after a successful read (no late abort)', async () => {
    // Without clearTimeout the controller fires after the call returned.
    // Harmless for an already-read body, but it leaks a timer per request
    // and would abort any signal a caller went on to reuse.
    let seen: AbortSignal | undefined;
    global.fetch = jest.fn((_url: string, init: RequestInit) => {
      seen = init.signal ?? undefined;
      return Promise.resolve({ ok: true, status: 200, json: async () => ({ v: 1 }) });
    }) as any;
    await expect(timedFetchJson('/probe', {}, 20, 'probeCall'))
      .resolves.toEqual({ v: 1 });
    await new Promise((r) => setTimeout(r, 60)); // 3x the budget
    expect(seen!.aborted).toBe(false);
  });

  it('clears its deadline after a non-2xx response too', async () => {
    let seen: AbortSignal | undefined;
    global.fetch = jest.fn((_url: string, init: RequestInit) => {
      seen = init.signal ?? undefined;
      return Promise.resolve({ ok: false, status: 503, statusText: 'Unavailable' });
    }) as any;
    await expect(timedFetchJson('/probe', {}, 20, 'probeCall')).rejects.toThrow();
    await new Promise((r) => setTimeout(r, 60));
    expect(seen!.aborted).toBe(false);
  });
});

describe('timedFetchJson — failure classification', () => {
  it('throws SyncHttpError carrying the status on a non-2xx', async () => {
    global.fetch = jest.fn(() =>
      Promise.resolve({ ok: false, status: 503, statusText: 'Unavailable' })) as any;
    let err: any;
    try { await timedFetchJson('/probe', {}, 50, 'probeCall'); } catch (e) { err = e; }
    expect(err).toBeInstanceOf(SyncHttpError);
    expect(err.status).toBe(503);
  });

  it('does NOT relabel a genuine transport error as a timeout', async () => {
    // "The server is unreachable" and "we gave up waiting" call for
    // different handling at the call sites; conflating them would make a
    // hard network failure look like a slow one.
    global.fetch = jest.fn(() =>
      Promise.reject(new TypeError('Failed to fetch'))) as any;
    let err: any;
    try { await timedFetchJson('/probe', {}, 50, 'probeCall'); } catch (e) { err = e; }
    expect(err).toBeInstanceOf(TypeError);
    expect(err).not.toBeInstanceOf(SyncTimeoutError);
  });

  it('does not relabel a caller-supplied abort as our timeout', async () => {
    // An externally-aborted request (component unmount) must not be
    // reported as a deadline breach — our timer never fired.
    global.fetch = jest.fn(() =>
      Promise.reject(new DOMException('Aborted', 'AbortError'))) as any;
    let err: any;
    try { await timedFetchJson('/probe', {}, 50, 'probeCall'); } catch (e) { err = e; }
    expect(err).not.toBeInstanceOf(SyncTimeoutError);
    expect((err as DOMException).name).toBe('AbortError');
  });

  it('resolves the parsed body on success (positive control)', async () => {
    global.fetch = jest.fn(() =>
      Promise.resolve({ ok: true, status: 200, json: async () => [{ id: 'a' }] })) as any;
    await expect(timedFetchJson('/probe', {}, 50, 'probeCall'))
      .resolves.toEqual([{ id: 'a' }]);
  });

  it('forwards the caller init (method, headers, body) unchanged', async () => {
    let init: RequestInit | undefined;
    global.fetch = jest.fn((_url: string, i: RequestInit) => {
      init = i;
      return Promise.resolve({ ok: true, status: 200, json: async () => ({}) });
    }) as any;
    await timedFetchJson('/probe', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Project-Root': '/p' },
      body: '{"ids":["a"]}',
    }, 50, 'probeCall');
    expect(init!.method).toBe('POST');
    expect((init!.headers as any)['X-Project-Root']).toBe('/p');
    expect(init!.body).toBe('{"ids":["a"]}');
  });
});
