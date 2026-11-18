/**
 * Owner-leaves handoff and owner-ignores-mirror guard.
 *
 * Before: a tab mirroring another tab's stream learned the stream was over
 * only from that tab's 'streaming-ended'.  If the owner was closed or
 * reloaded mid-turn, nothing was posted and the mirror kept the streaming
 * mark, with frozen text, for the life of the page -- while the turn kept
 * running on the server.
 *
 * Now the owner posts 'streaming-orphaned' on pagehide for every stream it
 * owns; a mirror releases its mark and raises CHAT_TURN_ORPHANED_EVENT for
 * ChatTurnReattachWatcher (covered in chatTurnReattachWatcher.test.tsx).
 *
 * Reattach now marks the conversation as streaming, so two tabs can both
 * own the same turn for a moment (browser session restore).  A tab that
 * owns a stream must not let the other's 'streaming-state' / 'streaming-chunk'
 * reclassify it as a mirror: it would stop answering probes, and the other
 * tab's 'streaming-ended' would drop its mark mid-turn.
 *
 * Unit half: ownedStreamIds.  Seam half: source assertions on ChatContext
 * and projectSync, as in remoteStreamingSyncGate.test.ts.
 */
import * as fs from 'fs';
import * as path from 'path';
import * as reattach from '../chatTurnReattach';

const strip = (src: string) =>
  src.split('\n').filter(l => !l.trim().startsWith('//')).join('\n');
const read = (...p: string[]) =>
  strip(fs.readFileSync(path.resolve(__dirname, '..', '..', ...p), 'utf8'));

const ctx = read('context', 'ChatContext.tsx');

function slice(start: string, end: string): string {
  const s = ctx.indexOf(start);
  expect(s).toBeGreaterThan(-1);
  const e = ctx.indexOf(end, s + start.length);
  expect(e).toBeGreaterThan(s);
  return ctx.slice(s, e);
}

describe('ownedStreamIds', () => {
  const owned = (reattach as any).ownedStreamIds as
    (s: Set<string>, r: Set<string>) => string[];

  it('returns streams this tab drives, not ones it mirrors', () => {
    expect(owned(new Set(['mine', 'theirs']), new Set(['theirs']))).toEqual(['mine']);
  });

  it('is empty when every stream is mirrored, or there are none', () => {
    expect(owned(new Set(['theirs']), new Set(['theirs']))).toEqual([]);
    expect(owned(new Set(), new Set(['stale']))).toEqual([]);
  });
});

describe('the owner announces its departure', () => {
  it('pagehide posts streaming-orphaned for owned streams only', () => {
    const body = slice('const onPageHide = () => {', "window.addEventListener('pagehide', onPageHide);");
    expect(body).toContain(
      'ownedStreamIds(streamingConversationsRef.current, remoteStreamingIdsRef.current)');
    expect(body).toContain("projectSync.post('streaming-orphaned', { conversationId: id });");
    // Not streaming-ended: mirrors must be able to tell "finished" from
    // "still running, nobody driving it".
    expect(body).not.toContain("'streaming-ended'");
  });

  it('streaming-orphaned is a declared message type', () => {
    expect(read('utils', 'projectSync.ts')).toContain("| 'streaming-orphaned'");
  });
});

describe('a mirror releases an orphaned stream and hands it to the watcher', () => {
  const body = () => slice(
    'const handleStreamingOrphaned = (msg: any) => {',
    "projectSync.on('conversations-changed'");

  it('only for a stream it is mirroring', () => {
    expect(body()).toContain('!remoteStreamingIdsRef.current.has(id)) return;');
  });

  it('releases locally, without re-broadcasting', () => {
    const b = body();
    const del = b.indexOf('remoteStreamingIdsRef.current.delete(id);');
    const rm = b.indexOf('removeStreamingConversation(id, { broadcast: false });');
    expect(del).toBeGreaterThan(-1);
    expect(rm).toBeGreaterThan(del);
    expect(b).not.toContain('projectSync.post(');
  });

  it('raises the event the watcher listens for, after the release', () => {
    const b = body();
    const rm = b.indexOf('removeStreamingConversation(id, { broadcast: false });');
    const ev = b.indexOf('new CustomEvent(CHAT_TURN_ORPHANED_EVENT');
    expect(ev).toBeGreaterThan(rm);
    expect((reattach as any).CHAT_TURN_ORPHANED_EVENT).toBe('chatTurnOrphaned');
  });

  it('is subscribed and unsubscribed', () => {
    expect(ctx).toContain("projectSync.on('streaming-orphaned', handleStreamingOrphaned);");
    expect(ctx).toContain("projectSync.off('streaming-orphaned', handleStreamingOrphaned);");
  });
});

describe('a tab that owns a stream ignores another tab claiming it', () => {
  it('ownsStream means: streaming here, and not learned from another tab', () => {
    const decl = slice('const ownsStream = (id: string) =>', 'const handleStreamingChunk');
    expect(decl).toContain('streamingConversationsRef.current.has(id)');
    expect(decl).toContain('!remoteStreamingIdsRef.current.has(id)');
  });

  it('streaming-chunk: checked before the id can be recorded as remote', () => {
    const b = slice('const handleStreamingChunk = (msg: any) => {', 'const handleStreamingState');
    const guard = b.indexOf('if (ownsStream(conversationId)) return;');
    const add = b.indexOf('remoteStreamingIdsRef.current.add(conversationId);');
    expect(guard).toBeGreaterThan(-1);
    expect(add).toBeGreaterThan(guard);                 // positive: a mirror still records it
  });

  it('streaming-state: checked before the id can be recorded as remote', () => {
    const b = slice('const handleStreamingState = (msg: any) => {', 'const handleStreamingEnded');
    const guard = b.indexOf('if (ownsStream(conversationId)) return;');
    const add = b.indexOf('remoteStreamingIdsRef.current.add(conversationId);');
    expect(guard).toBeGreaterThan(-1);
    expect(add).toBeGreaterThan(guard);
  });
});
