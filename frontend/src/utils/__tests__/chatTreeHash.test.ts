import { computeStructuralHash, fnv1a } from '../chatTreeHash';

const conv = (over: Record<string, any> = {}) => ({
  id: 'c1', title: 'Chat', folderId: null, isActive: true,
  isGlobal: false, ...over,
});
const folder = (over: Record<string, any> = {}) => ({
  id: 'f1', name: 'Folder', parentId: null, isGlobal: false, ...over,
});

describe('fnv1a', () => {
  it('is deterministic for the same input', () => {
    const a = fnv1a(); a.add('hello'); a.add('world');
    const b = fnv1a(); b.add('hello'); b.add('world');
    expect(a.value()).toBe(b.value());
  });

  it('returns an unsigned 32-bit integer', () => {
    const h = fnv1a(); h.add('x');
    const v = h.value();
    expect(v).toBeGreaterThanOrEqual(0);
    expect(v).toBeLessThanOrEqual(0xffffffff);
    expect(Number.isInteger(v)).toBe(true);
  });

  it('differs for different content', () => {
    const a = fnv1a(); a.add('ab');
    const b = fnv1a(); b.add('ba');
    expect(a.value()).not.toBe(b.value());
  });
});

describe('computeStructuralHash', () => {
  it('is deterministic for identical inputs', () => {
    const f = [folder()];
    const c = [conv()];
    expect(computeStructuralHash(f, c)).toBe(computeStructuralHash(f, c));
  });

  it('hashes empty inputs to a stable value', () => {
    expect(computeStructuralHash([], [])).toBe(computeStructuralHash([], []));
  });

  // ── Regression: the "flags assigned don't show for a few minutes" bug ──
  // A newly-assigned flag drove a row badge but was omitted from the hash,
  // so the tree-build cache stayed valid and the badge rendered stale.
  it('changes when a flag is assigned to a conversation', () => {
    const before = computeStructuralHash([], [conv({ flags: [] })]);
    const after = computeStructuralHash([], [conv({ flags: ['important'] })]);
    expect(after).not.toBe(before);
  });

  it('changes when a second flag is added', () => {
    const one = computeStructuralHash([], [conv({ flags: ['a'] })]);
    const two = computeStructuralHash([], [conv({ flags: ['a', 'b'] })]);
    expect(two).not.toBe(one);
  });

  it('changes when a flag is removed', () => {
    const withFlag = computeStructuralHash([], [conv({ flags: ['a'] })]);
    const without = computeStructuralHash([], [conv({ flags: [] })]);
    expect(without).not.toBe(withFlag);
  });

  it('changes when a flagColor is set', () => {
    const before = computeStructuralHash([], [conv({ flagColor: null })]);
    const after = computeStructuralHash([], [conv({ flagColor: 'red' })]);
    expect(after).not.toBe(before);
  });

  it('changes when a flagColor is cleared', () => {
    const colored = computeStructuralHash([], [conv({ flagColor: 'red' })]);
    const cleared = computeStructuralHash([], [conv({ flagColor: null })]);
    expect(cleared).not.toBe(colored);
  });

  it('treats undefined flags/flagColor the same as empty/null', () => {
    const bare = computeStructuralHash([], [conv()]);
    const explicit = computeStructuralHash([], [conv({ flags: [], flagColor: null })]);
    expect(explicit).toBe(bare);
  });

  // ── Other rendered fields must also be reflected ──
  it('changes when the title changes', () => {
    const a = computeStructuralHash([], [conv({ title: 'A' })]);
    const b = computeStructuralHash([], [conv({ title: 'B' })]);
    expect(a).not.toBe(b);
  });

  it('changes when isGlobal toggles', () => {
    const off = computeStructuralHash([], [conv({ isGlobal: false })]);
    const on = computeStructuralHash([], [conv({ isGlobal: true })]);
    expect(off).not.toBe(on);
  });

  it('changes when the open-bead count changes', () => {
    const zero = computeStructuralHash([], [conv({ openBeadCount: 0 })]);
    const some = computeStructuralHash([], [conv({ openBeadCount: 3 })]);
    expect(zero).not.toBe(some);
  });

  it('changes when delegate status changes', () => {
    const a = computeStructuralHash([], [conv({ delegateMeta: { status: 'running' } })]);
    const b = computeStructuralHash([], [conv({ delegateMeta: { status: 'crystal' } })]);
    expect(a).not.toBe(b);
  });

  it('changes when a folder is renamed', () => {
    const a = computeStructuralHash([folder({ name: 'A' })], []);
    const b = computeStructuralHash([folder({ name: 'B' })], []);
    expect(a).not.toBe(b);
  });

  it('changes when a conversation moves folders', () => {
    const root = computeStructuralHash([], [conv({ folderId: null })]);
    const nested = computeStructuralHash([], [conv({ folderId: 'f1' })]);
    expect(root).not.toBe(nested);
  });

  // ── Regression: green "ready" checkmark resurrects on the row you leave ──
  // loadConversation clears hasUnreadResponse without bumping _version or
  // lastAccessedAt, so if the flag is not hashed the tree cache keeps the
  // stale `true` node.  The row hides it only while it is current; switching
  // away re-shows it until an unrelated sync forces a rebuild (~minutes).
  it('changes when hasUnreadResponse is cleared', () => {
    const unread = computeStructuralHash([], [conv({ hasUnreadResponse: true })]);
    const read = computeStructuralHash([], [conv({ hasUnreadResponse: false })]);
    expect(read).not.toBe(unread);
  });

  it('changes when hasUnreadResponse is set', () => {
    const read = computeStructuralHash([], [conv()]);
    const unread = computeStructuralHash([], [conv({ hasUnreadResponse: true })]);
    expect(unread).not.toBe(read);
  });

  it('treats undefined hasUnreadResponse the same as false', () => {
    const bare = computeStructuralHash([], [conv()]);
    const explicit = computeStructuralHash([], [conv({ hasUnreadResponse: false })]);
    expect(explicit).toBe(bare);
  });

  it('does NOT change for lastAccessedAt (sort-only, not structural)', () => {
    const a = computeStructuralHash([], [conv({ lastAccessedAt: 1 })]);
    const b = computeStructuralHash([], [conv({ lastAccessedAt: 999 })]);
    expect(a).toBe(b);
  });
});
