/**
 * @jest-environment jsdom
 */
/**
 * Regression coverage for importWithRetry (frontend/src/utils/lazyWithRetry.ts).
 *
 * Context: a stale browser build 404s a lazy webpack chunk with a
 * ChunkLoadError.  React.lazy imports were already guarded, but the folder-move
 * handler in ChatContext used a RUNTIME `await import(...)` which bypassed that
 * guard and threw the raw ChunkLoadError, silently killing the move
 * (observed as `ChunkLoadError: Loading chunk 26353 failed` → "Move failed").
 *
 * These tests assert the SEAM: a runtime import routed through importWithRetry
 * (a) succeeds normally, (b) on a ChunkLoadError triggers exactly one hard
 * reload and does NOT reject with the chunk error (self-heals), and
 * (c) does NOT swallow ordinary errors — those still propagate and never
 * trigger a reload.
 */

describe('importWithRetry', () => {
  let originalLocation: Location;
  let replaceMock: jest.Mock;

  beforeEach(() => {
    jest.resetModules();          // reset module-level `reloadTriggered`
    jest.useFakeTimers();         // freeze the 10s unload watchdog
    try { window.sessionStorage.clear(); } catch { /* jsdom */ }

    originalLocation = window.location;
    replaceMock = jest.fn();
    // location is non-configurable in jsdom; swap the whole object.
    delete (window as any).location;
    (window as any).location = {
      href: 'http://localhost:6969/',
      reload: jest.fn(),
      replace: replaceMock,
    } as any;
  });

  afterEach(() => {
    (window as any).location = originalLocation;
    jest.useRealTimers();
  });

  const load = () => require('../lazyWithRetry').importWithRetry as
    <M>(f: () => Promise<M>, retries?: number) => Promise<M>;

  const chunkError = () => {
    const e = new Error('Loading chunk 26353 failed.');
    e.name = 'ChunkLoadError';
    return e;
  };

  it('resolves with the module when the import succeeds (positive: path ran)', async () => {
    const importWithRetry = load();
    const mod = await importWithRetry(async () => ({ mutateConversationMeta: 'fn' }));
    expect(mod.mutateConversationMeta).toBe('fn');
    expect(replaceMock).not.toHaveBeenCalled();
  });

  it('on ChunkLoadError triggers exactly one hard reload and does not reject with the chunk error', async () => {
    const importWithRetry = load();

    let settled: 'resolved' | 'rejected' | 'pending' = 'pending';
    const p = importWithRetry(() => Promise.reject(chunkError()))
      .then(() => { settled = 'resolved'; })
      .catch(() => { settled = 'rejected'; });

    // Let the rejection propagate through the catch/recovery path.
    await Promise.resolve();
    await Promise.resolve();

    // Recovery reload fired once (cache-busted URL) — the seam that self-heals.
    expect(replaceMock).toHaveBeenCalledTimes(1);
    expect(replaceMock.mock.calls[0][0]).toContain('_cb=');

    // The promise stays pending through the unload window rather than
    // rejecting with ChunkLoadError (which is what killed the folder move).
    expect(settled).toBe('pending');

    // Silence the eventual watchdog rejection so it doesn't leak.
    p.catch(() => undefined);
  });

  it('propagates ordinary (non-chunk) errors without reloading (guard does not over-catch)', async () => {
    const importWithRetry = load();
    const boom = new Error('boom');
    await expect(importWithRetry(() => Promise.reject(boom), 0)).rejects.toThrow('boom');
    expect(replaceMock).not.toHaveBeenCalled();
  });
});
