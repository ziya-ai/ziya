/**
 * currentMessages must never present one conversation's messages while
 * currentConversationId names another.
 *
 * The defect this pins (cross-project conversation leak): during a project
 * switch, PROJECT_CLEAR empties `conversations` synchronously while the
 * preload re-points currentConversationId to the destination project's
 * saved conversation.  The memo's `conversations.length === 0` stability
 * branch then returned the stale ref — the PREVIOUS conversation's
 * messages — under the NEW id.  The transcript kept displaying the old
 * conversation, the user kept composing against it, and handleSend
 * packaged the old conversation's history under the new target id:
 * question and answer both landed in the wrong conversation (in the
 * wrong project), with the answer visibly reasoning from the source
 * conversation's context.
 *
 * The `!conv` branch was guarded against exactly this in an earlier fix
 * (its comment describes the leak verbatim), but the length===0 branch
 * sits above it and bypasses it on every project switch.
 *
 * Static wiring test (repo pattern, cf. replayedPrefixWiring.test.ts):
 * the defect class is a missing ownership check in specific stability
 * branches, so we assert the guard's presence at each branch.
 */

import * as fs from 'fs';
import * as path from 'path';

const SRC = fs.readFileSync(
  path.join(__dirname, '../ChatContext.tsx'),
  'utf8',
);

function memoBlock(): string {
  const start = SRC.indexOf('const currentMessages = useMemo(');
  expect(start).toBeGreaterThan(-1);
  const end = SRC.indexOf('}, [conversations, currentConversationId]);', start);
  expect(end).toBeGreaterThan(start);
  return SRC.slice(start, end);
}

describe('currentMessages stale-ref ownership', () => {
  it('declares an owner ref alongside currentMessagesRef', () => {
    expect(SRC).toMatch(/const currentMessagesOwnerRef = useRef<string>\(''\)/);
  });

  it('computes foreignness of the stale ref inside the memo', () => {
    expect(memoBlock()).toMatch(
      /currentMessagesOwnerRef\.current !== currentConversationId/,
    );
  });

  it('the empty-conversations branch clears a foreign ref instead of returning it', () => {
    const memo = memoBlock();
    // Everything before the conversation lookup is the empty/no-id branch.
    const emptyBranch = memo.slice(0, memo.indexOf('conversations.find'));
    expect(emptyBranch).toMatch(/refIsForeign/);
    expect(emptyBranch).toMatch(/currentMessagesRef\.current = \[\]/);
  });

  it('the missing-messages branch refuses a foreign ref', () => {
    const memo = memoBlock();
    const idx = memo.indexOf('if (!conv.messages)');
    expect(idx).toBeGreaterThan(-1);
    const branch = memo.slice(idx, memo.indexOf('const messages = conv.messages'));
    expect(branch).toMatch(/refIsForeign/);
  });

  it('records ownership once messages are resolved for the current id', () => {
    expect(memoBlock()).toMatch(
      /currentMessagesOwnerRef\.current = currentConversationId/,
    );
  });

  it('the orphaned-conversation branch resets ownership when it clears the ref', () => {
    const memo = memoBlock();
    const idx = memo.indexOf('if (!conv)');
    const branch = memo.slice(idx, memo.indexOf('if (!conv.messages)'));
    expect(branch).toMatch(/currentMessagesOwnerRef\.current = ''/);
  });

  // ── positive controls (must pass before AND after the fix) ──────────

  it('control: stale-ref stability is retained (same-conversation path)', () => {
    // The memo must still be able to return the ref for stability — the
    // fix scopes WHEN, it does not remove the mechanism.
    expect(memoBlock()).toMatch(/return currentMessagesRef\.current/);
  });

  it('control: the earlier orphaned-conversation guard still clears the ref', () => {
    expect(memoBlock()).toMatch(
      /if \(currentMessagesRef\.current\.length !== 0\) currentMessagesRef\.current = \[\];/,
    );
  });

  it('control: PROJECT_CLEAR still empties conversations on project switch', () => {
    // The trigger condition for the leak window — the fix must not be
    // "achieved" by removing the clear, which exists to stop stale
    // sidebar data from the previous project.
    // Anchor on the unique log string INSIDE the clear effect, not on the
    // first occurrence of "PROJECT_CLEAR" — the memo fix's own comment
    // mentions PROJECT_CLEAR hundreds of lines earlier in the file.
    const idx = SRC.indexOf('🧹 PROJECT_CLEAR: switching');
    expect(idx).toBeGreaterThan(-1);
    const region = SRC.slice(idx, idx + 400);
    expect(region).toMatch(/setConversations\(\[\]\)/);
  });
});
