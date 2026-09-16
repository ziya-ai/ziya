/**
 * Behavioural half of removeStreamingNoTimeDedup.test.ts.
 *
 * Models the two guard shapes against the same async-state/sync-ref pairing
 * ChatContext actually has (a state Set mirrored into a ref on commit) and
 * drives the remove → add → remove sequence that stuck a conversation in the
 * streaming set.  `withTimeDedup` reproduces the removed code path and is
 * expected to FAIL the sequence — that is the certified bug.  `wasStreamingOnly`
 * is the shape that remains in ChatContext and must pass it.
 */

/** Minimal stand-in for useState<Set>+useRef mirror: commit() mirrors state → ref. */
class StreamingModel {
  state = new Set<string>();
  ref = new Set<string>();
  commit() { this.ref = new Set(this.state); }
  add(id: string) { this.state.add(id); }
}

function withTimeDedup(m: StreamingModel, now: () => number) {
  const removed = new Map<string, number>();
  return (id: string) => {
    const t = removed.get(id);
    if (t !== undefined && now() - t < 2000) return;
    removed.set(id, now());
    if (!m.ref.has(id)) return;
    m.state.delete(id);
  };
}

function wasStreamingOnly(m: StreamingModel) {
  return (id: string) => {
    if (!m.ref.has(id)) return;
    m.state.delete(id);
  };
}

/** Stop → resend → Stop, all within 2 seconds, with a React commit between. */
function runSequence(make: (m: StreamingModel) => (id: string) => void) {
  let clock = 0;
  const m = new StreamingModel();
  const remove = make(m);
  (m as any).now = () => clock;

  m.add('c1'); m.commit();
  remove('c1'); clock += 100; m.commit();        // stop
  m.add('c1');  clock += 300; m.commit();        // resend
  remove('c1'); clock += 100; m.commit();        // stop again, 500ms after first
  return m.state.has('c1');
}

describe('remove → add → remove inside 2s', () => {
  it('the removed time-dedup shape leaves the conversation stuck streaming (the bug)', () => {
    const stuck = runSequence(m => withTimeDedup(m, () => (m as any).now()));
    expect(stuck).toBe(true);
  });

  it('the wasStreaming-only shape releases it', () => {
    const stuck = runSequence(m => wasStreamingOnly(m));
    expect(stuck).toBe(false);
  });

  it('wasStreaming-only is idempotent for redundant removes and non-streaming ids', () => {
    const m = new StreamingModel();
    const remove = wasStreamingOnly(m);
    remove('never');                       // no throw, no state change
    expect(m.state.size).toBe(0);
    m.add('c2'); m.commit();
    remove('c2'); remove('c2'); remove('c2');   // same tick: ref still has it
    expect(m.state.has('c2')).toBe(false);
  });
});
