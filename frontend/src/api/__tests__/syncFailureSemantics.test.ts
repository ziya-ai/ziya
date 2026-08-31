/**
 * @jest-environment jsdom
 *
 * The failure semantics each sync call site depends on, and the structural
 * invariant that no request bypasses the deadline.
 *
 * WHY THE DISTINCTIONS MATTER
 *
 * `listChats` has two different failure modes that must NOT be collapsed:
 *
 *   - the server answered with a non-2xx  -> return [] (existing behaviour)
 *   - we could not ask at all             -> THROW
 *
 * An empty array is not a safe stand-in for "we could not ask".  The sync's
 * deletion pass treats any previously-seen conversation absent from the
 * server list as deleted-elsewhere, so a silently-empty listChats would
 * stage every such conversation for local removal.  Throwing instead routes
 * to syncWithServer's catch, which abandons the cycle and — critically —
 * still runs the `finally` that clears the in-flight flag.
 *
 * `timedFetch` is mocked here rather than exercised: the real budgets are
 * tens of seconds, and what is under test is how each call site CLASSIFIES
 * a failure, not the deadline itself (covered in timedFetch.test.ts).
 *
 * NOTE: requires the timedFetch.ts + conversationSyncApi.ts diffs; this
 * suite fails with "module not found" against an unpatched tree.
 */
import * as fs from 'fs';
import * as path from 'path';

jest.mock('../timedFetch', () => {
  const actual = jest.requireActual('../timedFetch');
  return { ...actual, timedFetchJson: jest.fn() };
});

import {
  timedFetchJson,
  SyncTimeoutError,
  SyncHttpError,
  LIST_TIMEOUT_MS,
  SINGLE_TIMEOUT_MS,
  BULK_TIMEOUT_MS,
} from '../timedFetch';
import { listChats, getChat, bulkGetChats } from '../conversationSyncApi';

const mockTimed = timedFetchJson as jest.Mock;

beforeEach(() => {
  mockTimed.mockReset();
  jest.spyOn(console, 'debug').mockImplementation(() => {});
  jest.spyOn(console, 'warn').mockImplementation(() => {});
});
afterEach(() => { jest.restoreAllMocks(); });

describe('listChats failure classification', () => {
  it('returns [] when the SERVER answered with an error status', async () => {
    mockTimed.mockRejectedValueOnce(new SyncHttpError('listChats', 500));
    await expect(listChats('proj-1')).resolves.toEqual([]);
  });

  it('RETHROWS a timeout instead of returning [] (deletion hazard)', async () => {
    // The load-bearing assertion.  Returning [] here would let the sync
    // proceed with an empty server view and stage every previously-seen
    // conversation for local deletion.
    mockTimed.mockRejectedValueOnce(new SyncTimeoutError('listChats', LIST_TIMEOUT_MS));
    await expect(listChats('proj-1')).rejects.toBeInstanceOf(SyncTimeoutError);
  });

  it('RETHROWS a transport error instead of returning []', async () => {
    mockTimed.mockRejectedValueOnce(new TypeError('Failed to fetch'));
    await expect(listChats('proj-1')).rejects.toBeInstanceOf(TypeError);
  });

  it('resolves the server list on success (positive control)', async () => {
    mockTimed.mockResolvedValueOnce([{ id: 'a' }, { id: 'b' }]);
    await expect(listChats('proj-1')).resolves.toHaveLength(2);
  });

  it('requests summaries by default and bodies only when asked', async () => {
    mockTimed.mockResolvedValue([]);
    await listChats('proj-1');
    expect(mockTimed.mock.calls[0][0]).toContain('include_messages=false');
    await listChats('proj-1', true);
    expect(mockTimed.mock.calls[1][0]).toContain('include_messages=true');
  });

  it('is issued under the list deadline', async () => {
    mockTimed.mockResolvedValueOnce([]);
    await listChats('proj-1');
    expect(mockTimed.mock.calls[0][2]).toBe(LIST_TIMEOUT_MS);
  });
});

describe('getChat failure classification', () => {
  it('returns null when the server answered with an error status', async () => {
    mockTimed.mockRejectedValueOnce(new SyncHttpError('getChat', 404));
    await expect(getChat('proj-1', 'c1')).resolves.toBeNull();
  });

  it('rethrows a timeout so the caller can distinguish it from "absent"', async () => {
    // getChat is awaited inside syncWithServer's post-sync rehydrate; a
    // null there reads as "the server has no such chat", which would be a
    // lie about a conversation the user is looking at.
    mockTimed.mockRejectedValueOnce(new SyncTimeoutError('getChat', SINGLE_TIMEOUT_MS));
    await expect(getChat('proj-1', 'c1')).rejects.toBeInstanceOf(SyncTimeoutError);
  });

  it('resolves the chat on success (positive control)', async () => {
    mockTimed.mockResolvedValueOnce({ id: 'c1', messages: [{ role: 'human' }] });
    await expect(getChat('proj-1', 'c1')).resolves.toMatchObject({ id: 'c1' });
  });
});

describe('bulkGetChats failure classification', () => {
  it('returns null on a timeout, so the ids retry next cycle', async () => {
    // bulkGetChats documents a single all-failures-are-null contract, and
    // the caller counts a null chunk as failed WITHOUT marking the ids
    // fetched — which is exactly the retry behaviour a timeout wants.
    mockTimed.mockRejectedValueOnce(new SyncTimeoutError('bulkGetChats', BULK_TIMEOUT_MS));
    await expect(bulkGetChats('proj-1', ['a', 'b'])).resolves.toBeNull();
  });

  it('returns null on an error status', async () => {
    mockTimed.mockRejectedValueOnce(new SyncHttpError('bulkGetChats', 500));
    await expect(bulkGetChats('proj-1', ['a'])).resolves.toBeNull();
  });

  it('resolves the payload on success (positive control)', async () => {
    mockTimed.mockResolvedValueOnce({ chats: [{ id: 'a' }], missing: ['b'] });
    await expect(bulkGetChats('proj-1', ['a', 'b']))
      .resolves.toEqual({ chats: [{ id: 'a' }], missing: ['b'] });
  });

  it('short-circuits an empty id list without issuing a request', async () => {
    await expect(bulkGetChats('proj-1', [])).resolves.toEqual({ chats: [], missing: [] });
    expect(mockTimed).not.toHaveBeenCalled();
  });

  it('is issued under the bulk deadline', async () => {
    mockTimed.mockResolvedValueOnce({ chats: [], missing: [] });
    await bulkGetChats('proj-1', ['a']);
    expect(mockTimed.mock.calls[0][2]).toBe(BULK_TIMEOUT_MS);
  });
});

describe('deadline budgets', () => {
  it('keeps the list deadline under the 30s poll interval', () => {
    // A listChats still in flight when the next tick fires can only lose to
    // that tick, so waiting past the interval buys nothing and extends the
    // window in which the in-flight flag is held.
    expect(LIST_TIMEOUT_MS).toBeLessThan(30_000);
    expect(LIST_TIMEOUT_MS).toBeGreaterThan(5_000);
  });

  it('allows the body fetch more room than the summary fetch', () => {
    // bulk-get legitimately transfers megabytes; the summary fetch does not.
    expect(BULK_TIMEOUT_MS).toBeGreaterThan(LIST_TIMEOUT_MS);
  });

  it('bounds every budget so none is effectively infinite', () => {
    for (const ms of [LIST_TIMEOUT_MS, SINGLE_TIMEOUT_MS, BULK_TIMEOUT_MS]) {
      expect(Number.isFinite(ms)).toBe(true);
      expect(ms).toBeGreaterThan(0);
      expect(ms).toBeLessThanOrEqual(120_000);
    }
  });
});

describe('structural invariant — no request escapes the deadline', () => {
  /** Source with `//` line comments stripped, so prose cannot satisfy a match. */
  const codeOf = (rel: string): string => {
    const src = fs.readFileSync(path.resolve(__dirname, '..', rel), 'utf8');
    return src.split('\n').filter((l) => !l.trim().startsWith('//')).join('\n');
  };

  it.each(['conversationSyncApi.ts', 'folderSyncApi.ts'])(
    '%s issues no bare fetch()', (mod) => {
      // The whole defect class is "one call site was missed".  Asserting the
      // module-wide absence catches a future addition, which a per-function
      // test never would.
      expect(codeOf(mod)).not.toMatch(/\bawait\s+fetch\s*\(/);
      expect(codeOf(mod)).not.toMatch(/[^.\w]fetch\s*\(/);
    });

  it.each(['conversationSyncApi.ts', 'folderSyncApi.ts'])(
    '%s routes requests through timedFetchJson (positive control)', (mod) => {
      // Pairs with the absence check above: without this, deleting every
      // request from the module would also pass.
      expect(codeOf(mod)).toMatch(/timedFetchJson\s*(<[^>]*>)?\s*\(/);
    });
});
