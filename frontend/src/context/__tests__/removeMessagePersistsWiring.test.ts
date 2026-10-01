/**
 * Structural guard: deleting a message persists the deletion to IndexedDB.
 *
 * WHY THIS EXISTS
 *
 * The delete button (shown on muted messages) used to filter the message out
 * of React state with a bare setConversations call and fire the server
 * DELETE.  Nothing wrote the change to IndexedDB, so IDB kept the old
 * message list (with the message still there, muted) under its old
 * _version.  Every path that reads conversations back from IDB (the
 * periodic server sync, which starts from db.getConversationShells(), shell
 * hydration, cross-tab reloads, reopening the conversation) then restored the
 * deleted message: it vanished and came straight back.
 *
 * Fix shape: ChatContext.removeMessage mirrors toggleMessageMute (mutate +
 * queueSave in the same updater, with a _version bump), is published through
 * ActiveChatContext, and Conversation.tsx's handleDiscard calls it instead of
 * mutating state directly.
 *
 * WHY STATIC
 *
 * As with the sibling *Wiring tests, driving ChatProvider through IDB in
 * jsdom costs far more than the assertion is worth, which is "these pieces
 * are connected".  Each hop is asserted separately so a break names itself.
 */
import * as fs from 'fs';
import * as path from 'path';

const read = (...p: string[]) => fs.readFileSync(path.resolve(__dirname, '..', ...p), 'utf8');
const stripComments = (s: string) => s.split('\n').filter(l => !l.trim().startsWith('//')).join('\n');

const CTX = stripComments(read('ChatContext.tsx'));
const ACTIVE = stripComments(read('ActiveChatContext.tsx'));
const CONV = stripComments(read('..', 'components', 'Conversation.tsx'));

/** Source of a `const name = useCallback(...)` up to its dependency list. */
function callbackBody(src: string, name: string): string {
  const start = src.indexOf(`const ${name} = useCallback(`);
  expect(start).toBeGreaterThan(-1);
  const end = src.indexOf('}, [', start);
  expect(end).toBeGreaterThan(start);
  return src.slice(start, end);
}

describe('message delete persists to IndexedDB', () => {
  it('ChatContext.removeMessage drops the message, bumps _version and queueSaves it', () => {
    const body = callbackBody(CTX, 'removeMessage');
    expect(body).toContain('setConversations(prev =>');
    expect(body).toMatch(/messages:\s*conv\.messages\.filter\(/);
    expect(body).toContain('_version: Date.now()');
    expect(body).toMatch(/queueSave\(updated,\s*\{\s*changedIds:\s*\[conversationId\]\s*\}\)/);
  });

  it('ChatContext exposes removeMessage on its value and passes it to ActiveChatProvider', () => {
    expect(CTX).toMatch(/removeMessage:\s*\(conversationId: string, messageIndex: number\) => void;/);
    expect(CTX).toContain('removeMessage={removeMessage}');
  });

  it('ActiveChatContext declares and forwards removeMessage', () => {
    expect(ACTIVE).toMatch(/removeMessage:\s*\(conversationId: string, messageIndex: number\) => void;/);
    expect(ACTIVE).toContain('removeMessage: value.removeMessage,');
    expect(ACTIVE).toContain('value.removeMessage');
  });

  it('handleDiscard deletes through removeMessage, not a bare state update', () => {
    const body = callbackBody(CONV, 'handleDiscard');
    // Positive: the path that persists runs, and the server DELETE is still sent.
    expect(body).toContain('removeMessage(currentConversationId, actualIndex)');
    expect(body).toContain('deleteMessage(pid, currentConversationId, locator)');
    // Negative: no state-only filter left behind that would skip IDB.
    expect(body).not.toContain('setConversations(');
  });
});
