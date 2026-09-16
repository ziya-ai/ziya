/**
 * Structural guard: a stream owned by ANOTHER tab must never pin this tab's
 * server sync shut.
 *
 * WHY THIS EXISTS
 *
 * Observed in production (2026-09-14): a tab opened while a sibling tab was
 * mid-stream in project P received that stream's `streaming-chunk` over the
 * P BroadcastChannel and added the conversation id to `streamingConversations`.
 * The user then switched projects, which calls projectSync.join(Q) and LEAVES
 * channel P.  The owning tab's `streaming-ended` was broadcast on P and never
 * received.  Nothing else ever removes an id from the set, so
 * `streamingConversationsRef.current.size > 0` stayed true — and syncWithServer
 * has a bare `return` on that condition, with no log line.  Every sync, in
 * every project, for the life of the page, exited silently.  The sidebar
 * showed only folder names; the only conversation that ever appeared was the
 * remote one, arriving via the shared IndexedDB rather than via sync.
 * A React-fiber probe on the stuck tab confirmed the set held exactly the
 * sibling tab's conversation id.
 *
 * Fix shape (three seams, all in ChatContext.tsx):
 *   1. `remoteStreamingIdsRef` records ids learned from BroadcastChannel.
 *   2. The sync guard filters those out — only LOCALLY owned streams skip
 *      the poll — and logs when it does skip.
 *   3. The cross-tab effect's cleanup (channel leave) drops remote ids from
 *      `streamingConversations` directly, NOT via removeStreamingConversation
 *      (which would re-broadcast `streaming-ended` on the channel being left
 *      and terminate the owning tab's live stream).
 *
 * WHY STATIC
 *
 * Same reasoning as the sibling *Wiring tests: driving ChatProvider through a
 * real cross-tab stream and a project switch in jsdom would need IDB, the
 * project store, BroadcastChannel and a live server mock, for an assertion
 * whose content is "these statements are wired to each other".  Every
 * assertion below FAILS against the pre-fix file.
 */
import * as fs from 'fs';
import * as path from 'path';

const CTX = fs.readFileSync(
  path.resolve(__dirname, '..', 'ChatContext.tsx'), 'utf8');

const code = CTX.split('\n').filter(l => !l.trim().startsWith('//')).join('\n');

function sliceBetween(startAnchor: string, endAnchor: string): string {
  const s = code.indexOf(startAnchor);
  const e = code.indexOf(endAnchor, s);
  expect(s).toBeGreaterThan(-1);
  expect(e).toBeGreaterThan(s);
  return code.slice(s, e);
}

describe('remote-owned streams do not gate syncWithServer', () => {
  it('declares a ref for ids learned from other tabs', () => {
    expect(code).toMatch(/const remoteStreamingIdsRef = useRef<Set<string>>\(new Set\(\)\)/);
  });

  it('the sync guard filters remote ids out before deciding to skip', () => {
    const sync = sliceBetween(
      'const syncWithServer = async () => {',
      'const serverChats = await syncApi.listChats(projectId, false);',
    );
    // The old bare guard must be gone: it treated every id as locally owned.
    expect(sync).not.toContain('if (streamingConversationsRef.current.size > 0) return;');
    // The new guard consults remote provenance.
    expect(sync).toContain('remoteStreamingIdsRef.current.has(id)');
    expect(sync).toMatch(/if \(localStreaming\.length > 0\) \{/);
  });

  it('the guard is no longer silent when it skips', () => {
    const sync = sliceBetween(
      'const syncWithServer = async () => {',
      'const serverChats = await syncApi.listChats(projectId, false);',
    );
    const guard = sync.indexOf('if (localStreaming.length > 0) {');
    expect(guard).toBeGreaterThan(-1);
    const body = sync.slice(guard, guard + 300);
    expect(body).toMatch(/console\.debug\(`📡 SERVER_SYNC: skipping/);
    expect(body).toContain('return;');
  });
});

describe('cross-tab handlers record remote provenance', () => {
  const effect = () => sliceBetween(
    'projectSync.join(currentProject.id);',
    "projectSync.on('conversations-changed', handleConversationsChanged);",
  );

  it('streaming-chunk marks the id as remote before adding it to the set', () => {
    const h = effect();
    const chunk = h.indexOf('const handleStreamingChunk = (msg: any) => {');
    const add = h.indexOf('remoteStreamingIdsRef.current.add(conversationId);', chunk);
    const setState = h.indexOf('setStreamingConversations(prev =>', chunk);
    expect(chunk).toBeGreaterThan(-1);
    expect(add).toBeGreaterThan(chunk);
    expect(setState).toBeGreaterThan(add);
  });

  it('streaming-state (non-idle) marks the id as remote', () => {
    const h = effect();
    const state = h.indexOf('const handleStreamingState = (msg: any) => {');
    const ended = h.indexOf('const handleStreamingEnded = (msg: any) => {');
    expect(state).toBeGreaterThan(-1);
    expect(ended).toBeGreaterThan(state);
    expect(h.slice(state, ended)).toContain('remoteStreamingIdsRef.current.add(conversationId);');
  });

  it('streaming-ended clears remote provenance before removing the stream', () => {
    const h = effect();
    const ended = h.indexOf('const handleStreamingEnded = (msg: any) => {');
    expect(ended).toBeGreaterThan(-1);
    const body = h.slice(ended, ended + 250);
    const del = body.indexOf('remoteStreamingIdsRef.current.delete(msg.conversationId);');
    const rm = body.indexOf('removeStreamingConversation(msg.conversationId);');
    expect(del).toBeGreaterThan(-1);
    expect(rm).toBeGreaterThan(del);
  });
});

describe('leaving a channel drops the streams learned on it', () => {
  // From the effect's cleanup opener to the first listener detach.
  const cleanup = () => {
    const anchor = 'pendingChunk = null;';
    // Two occurrences exist (rAF callback and cleanup); we want the one
    // inside `return () => {`.
    const ret = code.indexOf("projectSync.on('streaming-ended', handleStreamingEnded);");
    expect(ret).toBeGreaterThan(-1);
    const s = code.indexOf(anchor, ret);
    const e = code.indexOf("projectSync.off('conversations-changed', handleConversationsChanged);", s);
    expect(s).toBeGreaterThan(ret);
    expect(e).toBeGreaterThan(s);
    return code.slice(s, e);
  };

  it('clears the remote ref and removes those ids from streamingConversations', () => {
    const c = cleanup();
    expect(c).toContain('remote.clear();');
    expect(c).toContain('setStreamingConversations(prev => {');
    expect(c).toContain('dropped.forEach(id => next.delete(id));');
  });

  it('also releases their streamed content and processing state', () => {
    const c = cleanup();
    expect(c).toContain('setStreamedContentMap(prev => {');
    expect(c).toContain('setProcessingStates(prev => {');
  });

  it('does NOT route through removeStreamingConversation (would broadcast a false streaming-ended)', () => {
    const c = cleanup();
    expect(c).not.toContain('removeStreamingConversation(');
    expect(c).not.toContain("projectSync.post('streaming-ended'");
  });
});
