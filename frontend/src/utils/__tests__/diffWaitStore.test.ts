/**
 * Awaiting-apply indicator (design/await-diff-apply.md).
 *
 * The backend holds a web-chat turn whose response contains diffs until they
 * are applied, and streams ``diff_wait`` events while it waits.  The store
 * turns those into per-conversation state; while a turn is waiting the
 * spinner becomes a chip and Stop is hidden.
 *
 * Store behaviour is tested directly.  The seams (chatApi routes the event,
 * StopStreamButton and StreamedContent read the store, the stream ending
 * clears it) are pinned statically, like the other component wiring tests
 * here: rendering them needs the full context stack.
 */
import * as fs from 'fs';
import * as path from 'path';

import { applyDiffWaitEvent, clearDiffWait, getDiffWait, getDiffWaitLookup, subscribeDiffWait } from '../diffWaitStore';

const read = (rel: string) => {
  const src = fs.readFileSync(path.resolve(__dirname, '..', '..', rel), 'utf8');
  return src.split('\n').filter(l => !l.trim().startsWith('//')).join('\n');
};

describe('diffWaitStore', () => {
  afterEach(() => { clearDiffWait('c1'); clearDiffWait('c2'); });

  it('waiting sets the state; a later waiting updates it', () => {
    applyDiffWaitEvent('c1', { type: 'diff_wait', state: 'waiting', total: 2, remaining: 2,
      remaining_paths: ['app/x.py', 'tests/test_x.py'] });
    expect(getDiffWait('c1')).toEqual({ total: 2, remaining: 2,
      remainingPaths: ['app/x.py', 'tests/test_x.py'] });
    applyDiffWaitEvent('c1', { state: 'waiting', total: 2, remaining: 1, remaining_paths: ['tests/test_x.py'] });
    expect(getDiffWait('c1')?.remaining).toBe(1);
  });

  it('done clears it, and only for its own conversation', () => {
    applyDiffWaitEvent('c1', { state: 'waiting', total: 1, remaining: 1, remaining_paths: ['a'] });
    applyDiffWaitEvent('c2', { state: 'waiting', total: 3, remaining: 3, remaining_paths: [] });
    applyDiffWaitEvent('c1', { state: 'done', outcome: 'complete', total: 1, remaining: 0 });
    expect(getDiffWait('c1')).toBeUndefined();
    expect(getDiffWait('c2')?.total).toBe(3);
  });

  it('tolerates malformed events', () => {
    applyDiffWaitEvent('', { state: 'waiting', total: 1 });
    applyDiffWaitEvent('c1', null);
    expect(getDiffWait('c1')).toBeUndefined();
    applyDiffWaitEvent('c1', { state: 'waiting', total: 'x', remaining_paths: 'nope' });
    expect(getDiffWait('c1')).toEqual({ total: 0, remaining: 0, remainingPaths: [] });
  });
});

describe('diffWaitStore: narrow notifications (apply-click stutter)', () => {
  // Each apply sends a new remaining count.  With one global version every
  // subscriber, including the whole conversation list, re-rendered per click.
  afterEach(() => { clearDiffWait('c1'); clearDiffWait('c2'); });
  const wait = (remaining: number, paths: string[]) =>
    ({ state: 'waiting', total: 3, remaining, remaining_paths: paths });

  it('the list lookup changes when a wait starts or ends, not on each apply', () => {
    const before = getDiffWaitLookup();
    applyDiffWaitEvent('c1', wait(3, ['a', 'b', 'c']));
    const started = getDiffWaitLookup();
    expect(started).not.toBe(before);
    expect(started('c1')).toBe(true);
    expect(started('c2')).toBe(false);
    applyDiffWaitEvent('c1', wait(2, ['b', 'c']));
    applyDiffWaitEvent('c1', wait(1, ['c']));
    expect(getDiffWaitLookup()).toBe(started);
    applyDiffWaitEvent('c1', { state: 'done', outcome: 'complete' });
    expect(getDiffWaitLookup()).not.toBe(started);
    expect(getDiffWaitLookup()('c1')).toBe(false);
  });

  it("one conversation's update leaves another's snapshot untouched", () => {
    applyDiffWaitEvent('c1', wait(3, ['a', 'b', 'c']));
    applyDiffWaitEvent('c2', wait(3, ['x', 'y', 'z']));
    const c1 = getDiffWait('c1');
    applyDiffWaitEvent('c2', wait(2, ['y', 'z']));
    expect(getDiffWait('c1')).toBe(c1);
    expect(getDiffWait('c2')?.remaining).toBe(2);
  });

  it('an event that changes nothing notifies nobody; a real change does', () => {
    let calls = 0;
    const off = subscribeDiffWait(() => { calls++; });
    applyDiffWaitEvent('c1', wait(2, ['b', 'c']));
    expect(calls).toBe(1);
    applyDiffWaitEvent('c1', wait(2, ['b', 'c']));
    applyDiffWaitEvent('c2', { state: 'done' });   // nothing to clear
    clearDiffWait('c2');
    expect(calls).toBe(1);
    applyDiffWaitEvent('c1', wait(1, ['c']));      // positive control
    expect(calls).toBe(2);
    off();
  });

  it('hooks read per-conversation snapshots, not a global version', () => {
    const src = read('utils/diffWaitStore.ts');
    expect(src).not.toMatch(/getVersion/);
    expect(src).toContain('useSyncExternalStore(subscribeDiffWait, snap, snap)');
    expect(src).toContain('useSyncExternalStore(subscribeDiffWait, getDiffWaitLookup, getDiffWaitLookup)');
  });
});

describe('wiring', () => {
  it('chatApi routes diff_wait to the store and does not treat it as content', () => {
    const src = read('apis/chatApi.ts');
    const at = src.indexOf("unwrappedData.type === 'diff_wait'");
    expect(at).toBeGreaterThan(-1);
    const block = src.slice(at, at + 200);
    expect(block).toContain('applyDiffWaitEvent(conversationId, unwrappedData)');
    expect(block).toContain('return;');
  });

  it('StopStreamButton becomes "Stop waiting" while waiting, on the same stop path', () => {
    // A reply no longer drops the unapplied diffs from the wait, so the
    // wait needs a way out that is not "apply everything".
    const src = read('components/StopStreamButton.tsx');
    expect(src).toContain('useDiffWait(conversationId)');
    expect(src).not.toMatch(/if \(!isStreaming \|\| diffWait\)/);
    const at = src.indexOf('if (diffWait) {');
    expect(at).toBeGreaterThan(src.indexOf('const handleStopStream'));
    const block = src.slice(at, src.indexOf('const statusLabel'));
    expect(block).toContain('data-testid="stop-diff-wait"');
    expect(block).toContain('onClick={handleStopStream}');
    expect(block).toContain('Stop waiting');
  });

  it('the sidebar row shows a check, not Processing, while a turn waits', () => {
    const store = read('utils/diffWaitStore.ts');
    expect(store).toContain('export function useDiffWaitLookup');
    const src = read('components/MUIChatHistory.tsx');
    expect(src).toContain('const isAwaitingApply = useDiffWaitLookup();');
    // Only a live stream counts: a stale entry must not outlive its stream.
    expect(src).toMatch(/const rowAwaitingApply = isStreamingConv && isAwaitingApply\(convId\);/);
    expect(src).toContain('isStreaming={isStreamingConv && !rowAwaitingApply}');
    expect(src).toContain('awaitingApply={rowAwaitingApply}');
    expect(src).toContain('data-testid="chat-row-awaiting-apply"');
  });

  it('StreamedContent shows the chip instead of the spinner and clears on stream end', () => {
    const src = read('components/StreamedContent.tsx');
    expect(src).toContain('useDiffWait(currentConversationId)');
    expect(src).toMatch(/const LoadingIndicator = \(\) => isWaitingForApply \?/);
    expect(src).toContain('Waiting for {diffWait!.remaining} of {diffWait!.total} diff');
    expect(src).toContain('clearDiffWait(currentConversationId)');
  });

  it('the chip is mounted during a hold even though the processing state is idle', () => {
    // The hold starts after the response has streamed, when processingState
    // is 'idle'; the indicator's own gate hid it, so the chip never showed.
    const src = read('components/StreamedContent.tsx');
    const at = src.indexOf('<LoadingIndicator />');
    expect(at).toBeGreaterThan(-1);
    const gate = src.slice(src.lastIndexOf('{streamingConversations.has(currentConversationId) &&', at), at);
    expect(gate).toMatch(/!error && \(isWaitingForApply \|\|/);
    // Positive control: the ordinary activity gate is still there.
    expect(gate).toContain("processingState !== 'idle'");
  });

  it('the chip names the files still to apply', () => {
    const src = read('components/StreamedContent.tsx');
    expect(src).toContain('data-testid="diff-wait-chip"');
    expect(src).toMatch(/diffWait!\.remainingPaths\.map\(p => p\.split\('\/'\)\.pop\(\)\)/);
  });

  it('the server forwards the event the store consumes', () => {
    const server = fs.readFileSync(path.resolve(__dirname, '..', '..', '..', '..', 'app', 'server.py'), 'utf8');
    expect(server).toContain("chunk.get('type') == 'diff_wait'");
  });
});
