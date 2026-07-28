/**
 * @jest-environment jsdom
 *
 * Tests for modelPins — the per-tab, in-memory conversation/project
 * model pin store (conversation / folder / project) plus the resolver
 * that layers tab pins over saved record prefs.
 */
import {
  setConversationModelPin, getConversationModelPin,
  setFolderModelPin, getFolderModelPin,
  setProjectModelPin, getProjectModelPin,
  resolveModelPin, clearContextModelPins, clearAllModelPins,
  MODEL_PIN_CHANGED_EVENT,
} from '../modelPins';

beforeEach(() => clearAllModelPins());

describe('modelPins store', () => {
  it('sets and gets a conversation pin', () => {
    setConversationModelPin('conv-1', 'sonnet4.5');
    expect(getConversationModelPin('conv-1')).toBe('sonnet4.5');
    expect(getConversationModelPin('conv-2')).toBeNull();
  });

  it('sets and gets a folder pin', () => {
    setFolderModelPin('fold-1', 'sonnet4.5');
    expect(getFolderModelPin('fold-1')).toBe('sonnet4.5');
    expect(getFolderModelPin('fold-2')).toBeNull();
  });

  it('sets and gets a project pin', () => {
    setProjectModelPin('proj-1', 'haiku4.5');
    expect(getProjectModelPin('proj-1')).toBe('haiku4.5');
    expect(getProjectModelPin('proj-2')).toBeNull();
  });

  it('resolves most-specific-wins: conversation → folder → project', () => {
    setProjectModelPin('proj-1', 'haiku4.5');
    setFolderModelPin('fold-1', 'sonnet4.5');
    setConversationModelPin('conv-1', 'opus4.5');
    expect(resolveModelPin({ conversationId: 'conv-1', folderId: 'fold-1', projectId: 'proj-1' }))
      .toEqual({ model: 'opus4.5', scope: 'conversation', persistent: false });
  });

  it('falls back to folder, then project', () => {
    setProjectModelPin('proj-1', 'haiku4.5');
    setFolderModelPin('fold-1', 'sonnet4.5');
    expect(resolveModelPin({ conversationId: 'conv-1', folderId: 'fold-1', projectId: 'proj-1' }))
      .toEqual({ model: 'sonnet4.5', scope: 'folder', persistent: false });
    setFolderModelPin('fold-1', null);
    expect(resolveModelPin({ conversationId: 'conv-1', folderId: 'fold-1', projectId: 'proj-1' }))
      .toEqual({ model: 'haiku4.5', scope: 'project', persistent: false });
  });

  it('returns null when nothing is pinned', () => {
    expect(resolveModelPin({ conversationId: 'conv-1', projectId: 'proj-1' })).toBeNull();
    expect(resolveModelPin({})).toBeNull();
  });

  it('resolves SAVED prefs when no tab pin exists', () => {
    expect(resolveModelPin({
      conversationId: 'conv-1', folderId: 'fold-1', projectId: 'proj-1',
      persisted: { project: 'haiku4.5' },
    })).toEqual({ model: 'haiku4.5', scope: 'project', persistent: true });
  });

  it('a TAB pin overrides a SAVED pref at the same level', () => {
    setConversationModelPin('conv-1', 'opus4.5');
    expect(resolveModelPin({
      conversationId: 'conv-1',
      persisted: { conversation: 'sonnet4.5' },
    })).toEqual({ model: 'opus4.5', scope: 'conversation', persistent: false });
  });

  it('a more-specific SAVED pref beats a less-specific TAB pin', () => {
    // saved conversation pref should win over a tab project pin
    setProjectModelPin('proj-1', 'haiku4.5');
    expect(resolveModelPin({
      conversationId: 'conv-1', projectId: 'proj-1',
      persisted: { conversation: 'opus4.5' },
    })).toEqual({ model: 'opus4.5', scope: 'conversation', persistent: true });
  });

  it('a null model clears the pin', () => {
    setConversationModelPin('conv-1', 'sonnet4.5');
    setConversationModelPin('conv-1', null);
    expect(getConversationModelPin('conv-1')).toBeNull();
    setFolderModelPin('fold-1', 'sonnet4.5');
    setFolderModelPin('fold-1', null);
    expect(getFolderModelPin('fold-1')).toBeNull();
    setProjectModelPin('proj-1', 'haiku4.5');
    setProjectModelPin('proj-1', null);
    expect(getProjectModelPin('proj-1')).toBeNull();
  });

  it('clearContextModelPins clears all three tab scopes for the context only', () => {
    setConversationModelPin('conv-1', 'a');
    setConversationModelPin('conv-2', 'b');
    setFolderModelPin('fold-1', 'e');
    setProjectModelPin('proj-1', 'c');
    setProjectModelPin('proj-2', 'd');
    clearContextModelPins('conv-1', 'fold-1', 'proj-1');
    expect(getConversationModelPin('conv-1')).toBeNull();
    expect(getFolderModelPin('fold-1')).toBeNull();
    expect(getProjectModelPin('proj-1')).toBeNull();
    // Other contexts untouched.
    expect(getConversationModelPin('conv-2')).toBe('b');
    expect(getProjectModelPin('proj-2')).toBe('d');
  });

  it('ignores empty / missing ids', () => {
    setConversationModelPin('', 'x');
    setConversationModelPin(null, 'x');
    setProjectModelPin(undefined, 'x');
    expect(resolveModelPin({ conversationId: '', projectId: '' })).toBeNull();
  });

  it('dispatches MODEL_PIN_CHANGED_EVENT on mutation', () => {
    const seen: number[] = [];
    const listener = () => seen.push(1);
    window.addEventListener(MODEL_PIN_CHANGED_EVENT, listener);
    try {
      setConversationModelPin('conv-1', 'sonnet4.5');
      setProjectModelPin('proj-1', 'haiku4.5');
      setFolderModelPin('fold-1', 'opus4.5');
      clearContextModelPins('conv-1', 'fold-1', 'proj-1');
      expect(seen.length).toBe(4);
    } finally {
      window.removeEventListener(MODEL_PIN_CHANGED_EVENT, listener);
    }
  });
});
