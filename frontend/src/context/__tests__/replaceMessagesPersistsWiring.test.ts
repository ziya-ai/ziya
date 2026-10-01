/**
 * Structural guard: every message-list rewrite persists to IndexedDB and
 * records what it removed.
 *
 * WHY THIS EXISTS
 *
 * Resubmit (Conversation.handleResubmit), edit-in-place
 * (EditSection.handleSave) and edit-and-resubmit (EditSection.handleSubmit)
 * each rewrote the message list with a bare setConversations call.  Nothing
 * wrote the change to IndexedDB, so any IDB read-back (sync shells,
 * hydration, cross-tab reload, reopening the chat) could restore the old
 * text or the truncated tail — the same defect as the delete button
 * (see removeMessagePersistsWiring.test.ts).  Resubmit only appeared to work
 * because the send that follows usually saves.
 *
 * Fix shape: ChatContext.replaceMessages (updater + queueSave + local
 * tombstones for dropped ids), published through ActiveChatContext, used by
 * all three call sites.  removeMessage also records tombstones so another
 * tab's count guard adopts the shorter list (utils/syncMerge.ts).
 */
import * as fs from 'fs';
import * as path from 'path';

const read = (...p: string[]) => fs.readFileSync(path.resolve(__dirname, '..', ...p), 'utf8');
const stripComments = (s: string) => s.split('\n').filter(l => !l.trim().startsWith('//')).join('\n');

const CTX = stripComments(read('ChatContext.tsx'));
const ACTIVE = stripComments(read('ActiveChatContext.tsx'));
const CONV = stripComments(read('..', 'components', 'Conversation.tsx'));
const EDIT = stripComments(read('..', 'components', 'EditSection.tsx'));

function slice(src: string, startMarker: string, endMarker: string): string {
  const start = src.indexOf(startMarker);
  expect(start).toBeGreaterThan(-1);
  const end = src.indexOf(endMarker, start);
  expect(end).toBeGreaterThan(start);
  return src.slice(start, end);
}

describe('message-list rewrites persist to IndexedDB', () => {
  it('ChatContext.replaceMessages applies the updater, tombstones drops, bumps _version, queueSaves', () => {
    const body = slice(CTX, 'const replaceMessages = useCallback(', '}, [queueSave]);');
    expect(body).toContain('update(conv.messages)');
    // Drops are computed by id-or-fingerprint (syncMerge.droppedMessages), not
    // by id alone: an id-only filter left id-less legacy messages untombstoned
    // and the IDB guards then restored the pre-edit tail on every save.
    expect(body).toContain('syncMerge.droppedMessages(conv.messages, next)');
    expect(body).toContain('syncMerge.addTombstones(conv, dropped)');
    expect(body).toContain('_version: Date.now()');
    expect(body).toMatch(/queueSave\(updated,\s*\{\s*changedIds:\s*\[conversationId\]\s*\}\)/);
  });

  it('removeMessage records a tombstone for the deleted message', () => {
    const body = slice(CTX, 'const removeMessage = useCallback(', '}, [queueSave]);');
    expect(body).toContain('[syncMerge.TOMBSTONE_FIELD]: syncMerge.addTombstones(');
  });

  it('replaceMessages is exposed and forwarded through ActiveChatContext', () => {
    expect(CTX).toContain('replaceMessages={replaceMessages}');
    expect(ACTIVE).toContain('replaceMessages: value.replaceMessages,');
    expect(ACTIVE).toContain('value.replaceMessages');
  });

  it('handleResubmit truncates through replaceMessages, still rewinds the server', () => {
    const body = slice(CONV, 'const handleResubmit = useCallback(', '}, [');
    expect(body).toContain('replaceMessages(currentConversationId, () => truncatedMessages)');
    expect(body).toContain('rewindMessages(pid, currentConversationId, locator)');
    expect(body).not.toContain('setConversations(');
  });

  it('EditSection.handleSave edits through replaceMessages, still PATCHes the server', () => {
    const body = slice(EDIT, 'const handleSave = async () => {', 'const handleCancel = () => {');
    expect(body).toContain('replaceMessages(currentConversationId,');
    expect(body).toContain('patchMessage(');
    expect(body).not.toContain('setConversations(');
  });

  it('EditSection.handleSubmit truncates through replaceMessages before the rewind', () => {
    const body = slice(EDIT, 'const handleSubmit = async () => {', 'rewindMessages(');
    expect(body).toContain('replaceMessages(currentConversationId, () => truncatedMessages');
    expect(body).not.toContain('setConversations(');
  });
});
