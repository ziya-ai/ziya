/**
 * liveActivity — pure helpers for the running-tile progress surface.
 *
 * Extracted from TaskCardInlineTile so the age-label logic is unit
 * testable without rendering the tile.
 */

export interface ActivityLabel {
  label: string;
  /** True when the run has been silent long enough to look hung. */
  stale: boolean;
}

/** Age threshold (s) after which a running task reads as stalled. */
export const STALE_AFTER_S = 120;

/**
 * Format the elapsed time since the last executor activity.
 *
 * @param tsSeconds epoch seconds of last activity (server clock)
 * @param nowMs     current time in ms (injectable for tests)
 */
export function formatLastActivity(
  tsSeconds: number,
  nowMs: number = Date.now(),
): ActivityLabel {
  const ageS = Math.max(0, nowMs / 1000 - tsSeconds);
  const stale = ageS >= STALE_AFTER_S;
  if (ageS < 10) return { label: 'active now', stale };
  if (ageS < 60) return { label: `${Math.round(ageS)}s ago`, stale };
  if (ageS < 3600) {
    return { label: `${Math.round(ageS / 60)}m ago`, stale };
  }
  return { label: `${Math.round(ageS / 3600)}h ago`, stale: true };
}
