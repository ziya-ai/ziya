/**
 * Structural guard: while a conversation is streaming, the sidebar row's
 * trailing time slot shows time since the last STREAM ACTIVITY (processing
 * state stamp), not lastAccessedAt.  lastAccessedAt only moves when a
 * message is appended, so for the whole of a long tool-heavy turn it points
 * at the previous turn's end -- tens of minutes stale while tool calls are
 * flowing.  Simply hiding it would lose the opposite signal: a turn that has
 * stalled should visibly age ("now" -> "7m" -> "23m").
 *
 * Static because the row lives inside the virtualized tree and needs the
 * full ChatContext stack to render; formatRelativeShort itself is covered
 * in utils/__tests__/relativeTime.test.ts.
 */
import * as fs from 'fs';
import * as path from 'path';

const read = (rel: string) =>
  fs.readFileSync(path.resolve(__dirname, '..', '..', rel), 'utf8')
    .split('\n').filter(l => !l.trim().startsWith('//')).join('\n');

const row = read('components/MUIChatHistory.tsx');
const chatCtx = read('context/ChatContext.tsx');
const activeCtx = read('context/ActiveChatContext.tsx');

function slice(code: string, start: string, end: string): string {
  const s = code.indexOf(start);
  expect(s).toBeGreaterThan(-1);
  const e = code.indexOf(end, s);
  expect(e).toBeGreaterThan(s);
  return code.slice(s, e);
}

describe('sidebar row time slot while streaming', () => {
  it('ChatContext exposes the processing activity stamp and refreshes it during a long text stream', () => {
    expect(chatCtx).toContain('getProcessingActivityAt: (conversationId: string) => number | undefined;');
    const upd = slice(chatCtx, 'const updateProcessingState = useCallback(', 'const next = new Map(prev);');
    // Same-state chunks still dedup, but not forever: the stamp refreshes once it is >=30s old.
    expect(upd).toContain("if (cur?.state === state && Date.now() - cur.lastUpdated < 30_000) return prev;");
    expect(upd).not.toContain('if (prev.get(conversationId)?.state === state) return prev;');
    expect(chatCtx).toContain('return processingStates.get(conversationId)?.lastUpdated;');
    // Published on the context value (both memo objects list it).
    expect(chatCtx.match(/^\s*getProcessingActivityAt,\s*$/gm)?.length).toBeGreaterThanOrEqual(2);
    // ActiveChatProvider is mounted with explicit props, not a spread of the
    // context value: a getter added to the type but not passed here is
    // undefined at runtime and the row render throws on the first streaming
    // conversation.
    // (The mount's props contain "=>" arrows, so the slice ends at the
    // neighbouring prop rather than at the tag's closing ">".)
    const mount = slice(chatCtx, '<ActiveChatProvider', 'updateProcessingState={updateProcessingState}');
    expect(mount).toContain('getProcessingActivityAt={getProcessingActivityAt}');
  });

  it('ActiveChatContext threads the getter through, including its memo deps', () => {
    expect(activeCtx).toContain('getProcessingActivityAt: value.getProcessingActivityAt,');
    expect(activeCtx).toContain('value.getProcessingActivityAt,');
  });

  it('the row receives activityAt only while streaming and renders it in the slot', () => {
    expect(row).toContain('getProcessingActivityAt,');
    expect(row).toContain('activityAt={isStreamingConv && convId ? getProcessingActivityAt(convId) : undefined}');
    const slot = slice(row, 'data-testid="chat-row-time"', '</Typography>');
    expect(slot).toContain('formatRelativeShort(isStreaming ? (activityAt || lastActiveAt) : lastActiveAt)');
    expect(slot).not.toMatch(/\{formatRelativeShort\(lastActiveAt\)\}/);
    const guard = slice(row, '{!isFolder && (isStreaming ? (activityAt || lastActiveAt) : lastActiveAt) ?', 'data-testid="chat-row-time"');
    expect(guard).toContain('Last activity ${formatAbsolute(activityAt || lastActiveAt)}');
  });

  it('a streaming row ticks so a stalled turn visibly ages', () => {
    const tick = slice(row, 'const [, forceTick] = useState(0);', '}, [isStreaming]);');
    expect(tick).toContain('if (!isStreaming) return;');
    expect(tick).toContain('setInterval(() => forceTick(n => n + 1), 30_000)');
    expect(tick).toContain('clearInterval(t)');
  });
});
