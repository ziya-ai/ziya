/**
 * streamLagProbe — diagnostic accounting for the task-run WS event stream.
 *
 * Purpose: distinguish two failure modes that look identical in the UI
 * ("the status line is stale and events show up late"):
 *
 *   BACKLOG — frames arrive in order but the browser drains them slower
 *             than the backend produces them, so receipt lag grows
 *             without bound.  Fix is batching/throttling.
 *   DROPS   — frames arrive promptly but some never arrive at all, so
 *             lag stays near zero while counts come up short.  Fix is
 *             transport-level (reconnect, replay, relay buffering).
 *
 * Not all events are clocked.  ``task_progress`` / ``task_tool_call`` /
 * ``task_started`` / ``task_finished`` carry ``ts``; the block-executor
 * lifecycle events carry ``at``; and ``task_text_delta`` — by far the
 * highest-volume type — carries NO clock field at all.  Lag is therefore
 * reported only over clocked events, while volume is counted for every
 * event.  Unclocked volume is itself the signal: a backlog is produced
 * by text deltas, so growing lag alongside a large ``unclocked`` count
 * is the backlog signature.
 *
 * Pure by construction, so it is unit-testable without a WebSocket, a
 * React render, or a running backend.
 */

/** Per-run receipt-lag and volume accounting. */
export interface LagStats {
  /** Every event seen, clocked or not. */
  total: number;
  /** Events that carried a usable server clock (``ts`` or ``at``). */
  clocked: number;
  /** Events with no clock field — dominated by task_text_delta. */
  unclocked: number;
  /** Receipt lag (seconds) of the most recent clocked event. */
  lastLagS: number | null;
  /** Worst receipt lag (seconds) observed so far. */
  maxLagS: number | null;
  /** Lag of the FIRST clocked event, for growth comparison. */
  firstLagS: number | null;
  /** Event counts keyed by type. */
  byType: Record<string, number>;
}

export function emptyLagStats(): LagStats {
  return {
    total: 0, clocked: 0, unclocked: 0,
    lastLagS: null, maxLagS: null, firstLagS: null,
    byType: {},
  };
}

/**
 * Extract the server clock (epoch SECONDS) from an event, or null.
 *
 * ``ts`` is task_executor's field; ``at`` is block_executor's.  Both are
 * ``time.time()`` values, so both are epoch seconds and comparable.
 */
export function eventClockSeconds(evt: unknown): number | null {
  if (!evt || typeof evt !== 'object') return null;
  const e = evt as { ts?: unknown; at?: unknown };
  if (typeof e.ts === 'number' && Number.isFinite(e.ts)) return e.ts;
  if (typeof e.at === 'number' && Number.isFinite(e.at)) return e.at;
  return null;
}

/**
 * Receipt lag in seconds: how long after the server stamped the event we
 * are processing it.  Null when the event carries no clock.
 *
 * Clamped at zero: a client clock running slightly ahead of the server
 * would otherwise report negative lag and pollute the max.
 */
export function measureLag(
  evt: unknown, nowMs: number = Date.now(),
): number | null {
  const clock = eventClockSeconds(evt);
  if (clock == null) return null;
  return Math.max(0, nowMs / 1000 - clock);
}

/** Fold one event into the running stats.  Returns a NEW stats object. */
export function foldSample(
  stats: LagStats, evt: unknown, nowMs: number = Date.now(),
): LagStats {
  const type = (evt && typeof evt === 'object'
    && typeof (evt as { type?: unknown }).type === 'string')
    ? (evt as { type: string }).type
    : '(untyped)';
  const lag = measureLag(evt, nowMs);
  const byType = { ...stats.byType, [type]: (stats.byType[type] ?? 0) + 1 };
  if (lag == null) {
    return {
      ...stats,
      total: stats.total + 1,
      unclocked: stats.unclocked + 1,
      byType,
    };
  }
  return {
    total: stats.total + 1,
    clocked: stats.clocked + 1,
    unclocked: stats.unclocked,
    lastLagS: lag,
    maxLagS: stats.maxLagS == null ? lag : Math.max(stats.maxLagS, lag),
    firstLagS: stats.firstLagS == null ? lag : stats.firstLagS,
    byType,
  };
}

/** Growth threshold (s) above which lag is considered a real backlog. */
export const BACKLOG_LAG_THRESHOLD_S = 5;

/**
 * Interpret the stats.  Deliberately conservative: returns
 * ``'inconclusive'`` rather than guessing when there is not enough
 * clocked traffic to tell the two failure modes apart.
 */
export function classifyLag(
  stats: LagStats,
): 'backlog' | 'prompt' | 'inconclusive' {
  if (stats.clocked < 2 || stats.lastLagS == null || stats.firstLagS == null) {
    return 'inconclusive';
  }
  const grew = stats.lastLagS - stats.firstLagS;
  if (stats.lastLagS >= BACKLOG_LAG_THRESHOLD_S && grew > 0) return 'backlog';
  if (stats.lastLagS < BACKLOG_LAG_THRESHOLD_S) return 'prompt';
  return 'inconclusive';
}

/** One-line human summary for the console. */
export function formatLagStats(stats: LagStats): string {
  const fmt = (v: number | null) => (v == null ? '—' : `${v.toFixed(1)}s`);
  const top = Object.entries(stats.byType)
    .sort((a, b) => b[1] - a[1]).slice(0, 4)
    .map(([t, n]) => `${t}=${n}`).join(' ');
  return (
    `lag last=${fmt(stats.lastLagS)} max=${fmt(stats.maxLagS)} `
    + `first=${fmt(stats.firstLagS)} | ${stats.total} events `
    + `(${stats.clocked} clocked, ${stats.unclocked} unclocked) | `
    + `verdict=${classifyLag(stats)} | ${top}`
  );
}

/**
 * Whether the probe should log.  Off unless explicitly enabled, so this
 * costs nothing in normal use:
 *
 *     localStorage.setItem('ziya.debug.taskRunLag', '1')   // then reload
 *
 * Wrapped because localStorage throws in some privacy modes.
 */
export function isLagProbeEnabled(): boolean {
  try {
    return typeof localStorage !== 'undefined'
      && localStorage.getItem('ziya.debug.taskRunLag') === '1';
  } catch {
    return false;
  }
}

/** Log every Nth event so a chatty stream does not flood the console. */
export const LAG_LOG_EVERY = 25;
