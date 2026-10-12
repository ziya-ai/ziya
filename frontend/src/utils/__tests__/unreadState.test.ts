/**
 * Persisting "this conversation has been read".
 *
 * Observed defect (2026-10-05): a conversation's green "ready" checkmark came
 * back after a page reload even though it had been opened and read.
 * loadConversation cleared hasUnreadResponse in React state only, and IDB
 * writes are per-changed-id (queueSave FAST_PATH), so the cleared flag was
 * saved only if that SAME conversation was later saved for another reason.
 * Open, read, and don't reply: IDB, and the localStorage shell snapshot a
 * reload seeds from, kept `true` indefinitely.
 *
 * The fix persists the flag for that one record without bumping _version or
 * lastAccessedAt.  Bumping either would re-sort the sidebar on every click,
 * and _version also has to keep matching the server copy.
 *
 * DIRECTION: utils/unreadState.ts does not exist before the fix, so this
 * file fails to compile on the unpatched tree.  The seam tests fail if the
 * helpers exist but db.ts / loadConversation do not use them.
 */
import * as fs from 'fs';
import * as path from 'path';
import { withUnreadCleared, clearUnreadInShellSnapshot } from '../unreadState';

describe('withUnreadCleared', () => {
  it('returns a copy with the flag cleared when it was set', () => {
    const rec = { id: 'c1', hasUnreadResponse: true, messages: [] };
    const out = withUnreadCleared(rec);
    expect(out).not.toBeNull();
    expect(out!.hasUnreadResponse).toBe(false);
    expect(out!.id).toBe('c1');
  });

  it('does not mutate its input', () => {
    const rec = { id: 'c1', hasUnreadResponse: true };
    withUnreadCleared(rec);
    expect(rec.hasUnreadResponse).toBe(true);
  });

  it('preserves _version and lastAccessedAt (no sort or sync churn)', () => {
    const rec = { id: 'c1', hasUnreadResponse: true, _version: 123, lastAccessedAt: 456 };
    const out = withUnreadCleared(rec)!;
    expect(out._version).toBe(123);
    expect(out.lastAccessedAt).toBe(456);
  });

  it('returns null when there is nothing to change', () => {
    expect(withUnreadCleared({ id: 'c1', hasUnreadResponse: false })).toBeNull();
    expect(withUnreadCleared({ id: 'c1' })).toBeNull();
    expect(withUnreadCleared(null)).toBeNull();
    expect(withUnreadCleared(undefined)).toBeNull();
  });
});

describe('clearUnreadInShellSnapshot', () => {
  const snap = (shells: any[], ts = 1000) => JSON.stringify({ ts, shells });

  it('clears the flag on the target shell only', () => {
    const raw = snap([
      { id: 'a', hasUnreadResponse: true },
      { id: 'b', hasUnreadResponse: true },
    ]);
    const out = clearUnreadInShellSnapshot(raw, 'a');
    expect(out).not.toBeNull();
    const parsed = JSON.parse(out!);
    const byId = Object.fromEntries(parsed.shells.map((s: any) => [s.id, s]));
    expect(byId.a.hasUnreadResponse).toBe(false);
    expect(byId.b.hasUnreadResponse).toBe(true);
  });

  it('keeps the snapshot timestamp (does not extend its validity)', () => {
    const out = clearUnreadInShellSnapshot(snap([{ id: 'a', hasUnreadResponse: true }], 4242), 'a');
    expect(JSON.parse(out!).ts).toBe(4242);
  });

  it('returns null when nothing changes', () => {
    expect(clearUnreadInShellSnapshot(snap([{ id: 'a', hasUnreadResponse: false }]), 'a')).toBeNull();
    expect(clearUnreadInShellSnapshot(snap([{ id: 'b', hasUnreadResponse: true }]), 'a')).toBeNull();
  });

  it('returns null for a missing or malformed snapshot', () => {
    expect(clearUnreadInShellSnapshot(null, 'a')).toBeNull();
    expect(clearUnreadInShellSnapshot('not json', 'a')).toBeNull();
    expect(clearUnreadInShellSnapshot(JSON.stringify({ ts: 1 }), 'a')).toBeNull();
  });
});

// ── Seams: the helpers must actually be on the path ─────────────────────
const SRC = path.resolve(__dirname, '..', '..');
const read = (rel: string) => fs.readFileSync(path.join(SRC, rel), 'utf8');

describe('wiring', () => {
  const DB = read('utils/db.ts');
  const start = DB.indexOf('async markConversationRead(');
  const end = DB.indexOf('async deleteConversation(', start);
  const body = start >= 0 && end > start ? DB.slice(start, end) : '';

  it('db declares markConversationRead on the DB interface and the class', () => {
    expect(DB).toMatch(/markConversationRead\(id: string\): Promise<boolean>;/);
    expect(body.length).toBeGreaterThan(0);
  });

  it('db.markConversationRead patches IDB, the shell cache and the LS snapshot', () => {
    expect(body).toContain('withUnreadCleared(');
    expect(body).toContain('store.put(');
    expect(body).toContain('this.shellCache');
    expect(body).toContain('clearUnreadInShellSnapshot(');
    expect(body).toContain('this.shellCacheLSKey');
  });

  it('db.markConversationRead does not stamp _version or lastAccessedAt', () => {
    expect(body).not.toMatch(/_version\s*:/);
    expect(body).not.toMatch(/lastAccessedAt\s*:/);
  });

  it('loadConversation persists the read flag when it was set', () => {
    const CTX = read('context/ChatContext.tsx');
    const s = CTX.indexOf('const loadConversation = useCallback');
    const e = CTX.indexOf('const loadConversationAndScrollToMessage', s);
    const fn = CTX.slice(s, e);
    expect(s).toBeGreaterThanOrEqual(0);
    expect(fn).toMatch(/if \(convEntry\?\.hasUnreadResponse\)/);
    expect(fn).toContain('db.markConversationRead(conversationId)');
  });
});
