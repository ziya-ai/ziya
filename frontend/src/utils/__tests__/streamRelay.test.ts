/**
 * Cross-tab mirror of a live stream.
 *
 * The bug: the owning tab posted 'streaming-chunk' only from the plain-text
 * branch of the SSE handler.  Tool starts, tool results, group ends and
 * every other mutation of the turn's content changed the owner's view and
 * posted nothing, so a mirroring tab showed the streaming mark with text
 * frozen at the last prose chunk.  A turn parked in tools (job_wait) could
 * stay frozen until the final flush at turn end.  Sending feedback "fixed"
 * it once, because the server's "Feedback received" line is a text frame.
 *
 * A tab that learned of the stream from a probe reply got the mark and no
 * content at all.
 *
 * Unit half: createStreamRelay (leading + trailing throttle).
 * Seam half: chatApi offers from the one funnel every mutation passes
 * through, closes the relay before streaming-ended, and the probe reply
 * carries content.  Source assertions, as in remoteStreamingSyncGate.test.ts.
 */
import * as fs from 'fs';
import * as path from 'path';
import { createStreamRelay } from '../streamRelay';

function fakeClock() {
  let t = 1_000_000;
  let nextId = 1;
  const timers = new Map<number, { at: number; fn: () => void }>();
  return {
    now: () => t,
    setTimeout: (fn: () => void, ms: number) => {
      const id = nextId++;
      timers.set(id, { at: t + ms, fn });
      return id;
    },
    clearTimeout: (h: unknown) => { timers.delete(h as number); },
    advance(ms: number) {
      t += ms;
      for (const [id, tm] of Array.from(timers.entries())) {
        if (tm.at <= t) { timers.delete(id); tm.fn(); }
      }
    },
    pending: () => timers.size,
  };
}

describe('createStreamRelay', () => {
  it('posts the first offer immediately', () => {
    const clock = fakeClock();
    const posted: string[] = [];
    const relay = createStreamRelay(c => posted.push(c), 300, clock);
    relay.offer('a');
    expect(posted).toEqual(['a']);
  });

  it('delivers the latest content after the interval when nothing else arrives', () => {
    // Prose chunk, then a tool block 10 ms later, then silence for the
    // length of the tool call.  The tool block must reach the mirror.
    const clock = fakeClock();
    const posted: string[] = [];
    const relay = createStreamRelay(c => posted.push(c), 300, clock);
    relay.offer('prose');
    clock.advance(10);
    relay.offer('prose+tool_start');
    clock.advance(10);
    relay.offer('prose+tool_start+more');
    expect(posted).toEqual(['prose']);          // throttled, not dropped
    clock.advance(300);
    expect(posted).toEqual(['prose', 'prose+tool_start+more']);
    expect(clock.pending()).toBe(0);
  });

  it('posts at most once per interval under a burst', () => {
    const clock = fakeClock();
    const posted: string[] = [];
    const relay = createStreamRelay(c => posted.push(c), 300, clock);
    for (let i = 0; i < 30; i++) { relay.offer('x'.repeat(i + 1)); clock.advance(20); }
    // 600 ms of offers: leading post, then one per 300 ms.
    expect(posted.length).toBeGreaterThanOrEqual(2);
    expect(posted.length).toBeLessThanOrEqual(3);
  });

  it('does not repost unchanged content', () => {
    const clock = fakeClock();
    const posted: string[] = [];
    const relay = createStreamRelay(c => posted.push(c), 300, clock);
    relay.offer('a');
    clock.advance(1000);
    relay.offer('a');
    clock.advance(1000);
    expect(posted).toEqual(['a']);
    relay.offer('ab');                          // positive control: a change still posts
    expect(posted).toEqual(['a', 'ab']);
  });

  it('close() cancels a pending trailing post and refuses later offers', () => {
    // A chunk posted after streaming-ended re-marks the conversation as
    // streaming in every mirror, with nothing left to release it.
    const clock = fakeClock();
    const posted: string[] = [];
    const relay = createStreamRelay(c => posted.push(c), 300, clock);
    relay.offer('a');
    clock.advance(10);
    relay.offer('ab');
    expect(clock.pending()).toBe(1);            // the trailing post was scheduled
    relay.close();
    clock.advance(1000);
    relay.offer('abc');
    clock.advance(1000);
    expect(posted).toEqual(['a']);
  });

  it('ignores empty content (the mirror drops it anyway)', () => {
    const clock = fakeClock();
    const posted: string[] = [];
    const relay = createStreamRelay(c => posted.push(c), 300, clock);
    relay.offer('');
    expect(posted).toEqual([]);
    relay.offer('a');
    expect(posted).toEqual(['a']);
  });
});

const strip = (src: string) =>
  src.split('\n').filter(l => !l.trim().startsWith('//')).join('\n');

describe('chatApi relays every content mutation, not only prose', () => {
  const api = strip(fs.readFileSync(
    path.resolve(__dirname, '..', '..', 'apis', 'chatApi.ts'), 'utf8'));

  it('offers to the relay from flushStreamedContent, ahead of the already-scheduled return', () => {
    const start = api.indexOf('const flushStreamedContent = () => {');
    expect(start).toBeGreaterThan(-1);
    const body = api.slice(start, api.indexOf('};', start));
    const offer = body.indexOf('crossTabRelay.offer(currentContent);');
    const scheduled = body.indexOf('if (_streamFallbackId !== null) return;');
    expect(offer).toBeGreaterThan(-1);
    expect(scheduled).toBeGreaterThan(-1);
    // After the early return it would run once per 150 ms flush window at
    // best, and from a timer -- which a hidden owner tab throttles.
    expect(offer).toBeLessThan(scheduled);
  });

  it('the relay posts streaming-chunk for this conversation', () => {
    const start = api.indexOf('const crossTabRelay = createStreamRelay(');
    expect(start).toBeGreaterThan(-1);
    const decl = api.slice(start, start + 300);
    expect(decl).toContain("projectSync.post('streaming-chunk', { conversationId, content })");
    expect(decl).toContain('BROADCAST_INTERVAL_MS');
  });

  it('the prose-only inline broadcast is gone', () => {
    expect(api).not.toContain('lastBroadcastTime');
  });

  it('closes the relay before streaming-ended can be posted', () => {
    const start = api.indexOf('removeStreamingConversation = (id: string) => {');
    expect(start).toBeGreaterThan(-1);
    const body = api.slice(start, api.indexOf('};', start));
    const close = body.indexOf('crossTabRelay.close();');
    const end = body.indexOf('endStreamingConversation(id);');
    expect(close).toBeGreaterThan(-1);
    expect(end).toBeGreaterThan(close);
  });
});

describe('a probe reply carries the content, not just the mark', () => {
  const ctx = strip(fs.readFileSync(
    path.resolve(__dirname, '..', '..', 'context', 'ChatContext.tsx'), 'utf8'));

  it('handleStreamingProbe posts streaming-chunk with the owner\'s current text', () => {
    const start = ctx.indexOf('const handleStreamingProbe = (msg: any) => {');
    expect(start).toBeGreaterThan(-1);
    const body = ctx.slice(start, ctx.indexOf("projectSync.on('conversations-changed'", start));
    const state = body.indexOf("projectSync.post('streaming-state'");
    const chunk = body.indexOf("projectSync.post('streaming-chunk'");
    expect(state).toBeGreaterThan(-1);
    expect(chunk).toBeGreaterThan(state);
    expect(body).toContain('streamedContentMapRef.current.get(id)');
    // Still owner-only: a mirroring tab must not answer.
    expect(body).toContain('if (remoteStreamingIdsRef.current.has(id)) return;');
  });

  it('the ref tracks live state', () => {
    expect(ctx).toMatch(/const streamedContentMapRef = useRef\(streamedContentMap\);/);
    expect(ctx).toContain('streamedContentMapRef.current = streamedContentMap;');
  });
});
