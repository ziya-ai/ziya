/**
 * Structural guard: the server sync is re-run when streaming ends.
 *
 * WHY THIS EXISTS
 *
 * The sidebar's per-conversation openBeadCount is refreshed ONLY by
 * syncWithServer, and syncWithServer bails out for the entire duration of
 * any streaming turn in the tab (its own state is authoritative while a
 * response is in flight).  Bead status changes are made BY the model,
 * mid-turn — so the moments beads change are exactly the moments the poll
 * is suppressed, and the sidebar stayed stale for up to 30s after the turn
 * ended (observed: chat list amber "2" while the conversation's beads were
 * all completed).
 *
 * Fix shape: syncWithServer is published through a ref, and an effect that
 * watches streamingConversations fires the ref'd sync on the >0 → 0 edge.
 *
 * WHY STATIC
 *
 * As with the sibling *Wiring tests, driving ChatProvider through a real
 * stream start/stop in jsdom would need IDB, the project store and a live
 * server mock for an assertion whose content is "these three statements
 * are wired to each other".  Asserting the seams directly is cheaper and
 * also catches the likeliest regression: someone rewriting the effect and
 * dropping the ref publication, leaving the edge effect calling null.
 */
import * as fs from 'fs';
import * as path from 'path';

const CTX = fs.readFileSync(
  path.resolve(__dirname, '..', 'ChatContext.tsx'), 'utf8');

const code = CTX.split('\n').filter(l => !l.trim().startsWith('//')).join('\n');

describe('syncWithServer re-runs on the streaming → idle edge', () => {
  it('declares a ref to hold the latest syncWithServer closure', () => {
    expect(code).toMatch(/const syncWithServerRef = useRef<\(\(\) => Promise<void>\) \| null>\(null\)/);
  });

  it('publishes syncWithServer into the ref inside the sync effect', () => {
    // Must be assigned AFTER the closure is defined and BEFORE the effect
    // returns its interval cleanup, i.e. between these two anchors.
    const def = code.indexOf('const syncWithServer = async () => {');
    const interval = code.indexOf('setInterval(syncWithServer, 30_000)');
    expect(def).toBeGreaterThan(-1);
    expect(interval).toBeGreaterThan(def);
    const between = code.slice(def, interval);
    expect(between).toContain('syncWithServerRef.current = syncWithServer;');
  });

  it('clears the ref in the sync effect cleanup so a stale project sync cannot fire', () => {
    const interval = code.indexOf('setInterval(syncWithServer, 30_000)');
    const cleanup = code.slice(interval, interval + 400);
    expect(cleanup).toContain('syncWithServerRef.current = null;');
  });

  it('has an effect that fires the ref on the >0 → 0 streaming transition', () => {
    const start = code.indexOf('const wasStreaming = prevStreamingCountRef.current > 0;');
    expect(start).toBeGreaterThan(-1);
    const effect = code.slice(start, start + 600);
    expect(effect).toContain('streamingConversations.size === 0');
    expect(effect).toContain('syncWithServerRef.current?.()');
    // The edge detector must update its memory every run, or it fires once.
    expect(effect).toContain('prevStreamingCountRef.current = streamingConversations.size;');
  });

  it('the edge effect is declared AFTER the ref-mirror effect so the sync sees size 0', () => {
    // syncWithServer's first guard is `streamingConversationsRef.current.size > 0`.
    // That ref is mirrored from state in an earlier effect; React runs
    // effects in declaration order within a commit, so the edge effect must
    // come later or the sync it triggers would bail on the stale ref.
    const mirror = code.indexOf('streamingConversationsRef.current = streamingConversations;');
    const edge = code.indexOf('const wasStreaming = prevStreamingCountRef.current > 0;');
    expect(mirror).toBeGreaterThan(-1);
    expect(edge).toBeGreaterThan(mirror);
  });
});
