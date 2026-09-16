/**
 * The sidebar's current-conversation consistency check must not report the
 * designed "blank chat" state as corruption.
 *
 * WHY THIS EXISTS
 *
 * ChatContext mints a bare conversation id when a project has no
 * conversations and only creates the Conversation object on the first
 * message (addMessageToConversation's `[...prev, {id: conversationId, ...}]`
 * branch).  A selected id with NO record is therefore expected, as it is
 * while a project-switch preload or server sync is still landing.
 *
 * treeDataRaw used to `console.error('HISTORY_CORRUPTION ...')` twice on
 * EVERY render for that state — observed as an uninterrupted stream of
 * errors across three project switches in a tab whose real fault (a stuck
 * remote streaming id gating sync) was buried under the noise.  The one
 * state that IS inconsistent — the id is present but `isActive === false`
 * (the user is looking at a soft-deleted chat) — was logged with the same
 * message, so the two could not be told apart.
 *
 * Static, like the sibling *Wiring tests: rendering MUIChatHistory needs the
 * full provider stack.  A behavioural check of the classification lives in
 * currentConversationConsistencyLogic.test.ts.
 */
import * as fs from 'fs';
import * as path from 'path';

const SRC = fs.readFileSync(
  path.resolve(__dirname, '..', 'MUIChatHistory.tsx'), 'utf8');
const code = SRC.split('\n').filter(l => !l.trim().startsWith('//')).join('\n');

describe('current-conversation consistency check in treeDataRaw', () => {
  const start = code.indexOf('!activeConversations.some(c => c.id === currentConversationId)');
  const block = code.slice(start, start + 1400);

  it('is wired (positive: the check still exists)', () => {
    expect(start).toBeGreaterThan(-1);
  });

  it('reports HISTORY_CORRUPTION only for a present-but-inactive record', () => {
    // The error must sit under a branch that first found the record.
    const rec = block.indexOf('const record = safeConversations.find(c => c.id === currentConversationId)');
    const err = block.indexOf("console.error('🚨 HISTORY_CORRUPTION: Current conversation is present but inactive:'");
    expect(rec).toBeGreaterThan(-1);
    expect(err).toBeGreaterThan(rec);
    expect(block.slice(rec, err)).toMatch(/if \(record\)/);
  });

  it('does not log the absent-id state as an error', () => {
    expect(block).not.toContain("console.error('🚨 HISTORY_CORRUPTION: Current conversation missing from active list:'");
    expect(block).toContain("console.debug('📝 Current conversation not yet in list");
  });

  it('dedups both reports per id via module-level sets that are cleared once the id is consistent', () => {
    expect(code).toContain('const reportedMissingCurrentIds = new Set<string>();');
    expect(code).toContain('const reportedInactiveCurrentIds = new Set<string>();');
    expect(block).toContain('reportedInactiveCurrentIds.add(currentConversationId)');
    expect(block).toContain('reportedMissingCurrentIds.add(currentConversationId)');
    // Cleared when the id is back in the active list so a later recurrence
    // is reported again rather than swallowed forever.
    expect(block).toContain('reportedMissingCurrentIds.delete(currentConversationId)');
    expect(block).toContain('reportedInactiveCurrentIds.delete(currentConversationId)');
  });
});
