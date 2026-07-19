import { conversationToServerChat } from '../conversationSyncApi';

/**
 * Regression coverage for the folder "snap-back" bug.
 *
 * Folder membership is tracked by two names that must agree:
 *   - folderId : frontend source of truth (what a move patches)
 *   - groupId  : server storage name for the same concept
 *
 * A conversation record can carry a STALE groupId left over from an
 * earlier server read. Because read-back resolves `groupId || folderId`,
 * a stale groupId that disagrees with folderId wins and re-roots the
 * conversation. conversationToServerChat is the single push chokepoint
 * that must force groupId to mirror folderId so both sides stay
 * consistent and any pre-existing divergence self-heals on next push.
 */
describe('conversationToServerChat — folderId/groupId reconciliation', () => {
  const PROJECT_ID = 'proj-1';

  const base = (overrides: Record<string, any> = {}) => ({
    id: 'conv-1',
    title: 'Test conversation',
    messages: [],
    ...overrides,
  });

  it('overwrites a stale groupId when the conversation is moved into a folder', () => {
    // The core bug: move patched folderId=new but groupId stayed stale.
    const result = conversationToServerChat(
      base({ folderId: 'folder-new', groupId: 'folder-stale' }),
      PROJECT_ID,
    );
    expect(result.folderId).toBe('folder-new');
    expect(result.groupId).toBe('folder-new');
  });

  it('sends both null when moved to root, even with a stale groupId', () => {
    // Regression guard: move-to-root sets folderId===null (present).
    // A `??` fallback would wrongly resolve to the stale groupId and
    // snap the conversation back into the old folder.
    const result = conversationToServerChat(
      base({ folderId: null, groupId: 'folder-stale' }),
      PROJECT_ID,
    );
    expect(result.folderId).toBeNull();
    expect(result.groupId).toBeNull();
  });

  it('falls back to groupId when folderId is absent (server-hydrated record)', () => {
    // A record freshly read from the server may carry only groupId and
    // have no folderId yet (undefined). Here the fallback is correct.
    const result = conversationToServerChat(
      base({ groupId: 'folder-x' }),
      PROJECT_ID,
    );
    expect(result.folderId).toBe('folder-x');
    expect(result.groupId).toBe('folder-x');
  });

  it('yields null for both when neither folderId nor groupId is set', () => {
    const result = conversationToServerChat(base(), PROJECT_ID);
    expect(result.folderId).toBeNull();
    expect(result.groupId).toBeNull();
  });

  it('always emits folderId and groupId in agreement', () => {
    // The invariant the fix guarantees, across representative inputs.
    const cases = [
      { folderId: 'a', groupId: 'b' },
      { folderId: null, groupId: 'b' },
      { folderId: 'a' },
      { groupId: 'b' },
      {},
    ];
    for (const c of cases) {
      const result = conversationToServerChat(base(c), PROJECT_ID);
      expect(result.groupId).toBe(result.folderId);
    }
  });

  it('prefers an explicit folderId over a differing groupId', () => {
    const result = conversationToServerChat(
      base({ folderId: 'keep-me', groupId: 'ignore-me' }),
      PROJECT_ID,
    );
    expect(result.folderId).toBe('keep-me');
    expect(result.groupId).toBe('keep-me');
  });

  it('preserves unrelated fields and stamps the projectId', () => {
    const result = conversationToServerChat(
      base({ id: 'conv-42', title: 'Keep title', folderId: 'f1' }),
      PROJECT_ID,
    );
    expect(result.id).toBe('conv-42');
    expect(result.title).toBe('Keep title');
    expect(result.projectId).toBe(PROJECT_ID);
  });
});
