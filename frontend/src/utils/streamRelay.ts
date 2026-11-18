/**
 * Throttled relay of a live stream's content to other tabs.
 *
 * Leading and trailing: the first change is posted at once, later changes
 * at most once per interval, and the latest content is always delivered
 * once the interval has passed.  The trailing send is what matters for a
 * turn that appends a tool block and then goes quiet for the length of the
 * tool call: without it the mirror keeps whatever was last posted.
 *
 * Content is the full accumulated text, not a delta, so a skipped
 * intermediate value costs nothing.
 */
export interface StreamRelay {
  /** Offer the current content.  Cheap when it has not changed. */
  offer(content: string): void;
  /**
   * Stop relaying and cancel any pending trailing send.  Call before the
   * stream's end is announced: a chunk posted after 'streaming-ended'
   * re-marks the conversation as streaming in every mirroring tab, and
   * nothing would release it.
   */
  close(): void;
}

export interface StreamRelayClock {
  now(): number;
  setTimeout(fn: () => void, ms: number): unknown;
  clearTimeout(handle: unknown): void;
}

const systemClock: StreamRelayClock = {
  now: () => Date.now(),
  setTimeout: (fn, ms) => setTimeout(fn, ms),
  clearTimeout: handle => clearTimeout(handle as ReturnType<typeof setTimeout>),
};

export function createStreamRelay(
  post: (content: string) => void,
  intervalMs: number,
  clock: StreamRelayClock = systemClock,
): StreamRelay {
  let lastSent: string | null = null;
  let lastSentAt = -Infinity;
  let latest = '';
  let timer: unknown = null;
  let closed = false;

  const sendLatest = () => {
    if (closed || latest === lastSent) return;
    lastSent = latest;
    lastSentAt = clock.now();
    post(latest);
  };

  return {
    offer(content: string) {
      if (closed || !content) return;
      latest = content;
      if (content === lastSent) return;
      const wait = intervalMs - (clock.now() - lastSentAt);
      if (wait <= 0) { sendLatest(); return; }
      if (timer === null) {
        timer = clock.setTimeout(() => { timer = null; sendLatest(); }, wait);
      }
    },
    close() {
      closed = true;
      if (timer !== null) { clock.clearTimeout(timer); timer = null; }
    },
  };
}
