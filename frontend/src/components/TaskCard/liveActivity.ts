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

const MINUTE = 60;
const HOUR = 3600;
const DAY = 86400;
const WEEK = 7 * DAY;
// Calendar-inexact on purpose: these are coarse "how long ago" buckets
// for a run list, not date arithmetic.  A label reading 11mo for
// something 344 days old is fine; introducing a real calendar library
// to make it 11.3 would buy nothing at this precision.
const MONTH = 30 * DAY;
const YEAR = 365 * DAY;

/**
 * Format the elapsed time since the last executor activity.
 *
 * Buckets run all the way out to years.  Capping at hours (as this did)
 * meant the deck's run history reported a run from last spring as
 * "3020h ago" — technically true, unreadable, and the number the eye
 * cannot convert is exactly the one a history list exists to convey.
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
  if (ageS < MINUTE) return { label: `${Math.round(ageS)}s ago`, stale };
  if (ageS < HOUR) return { label: `${Math.round(ageS / MINUTE)}m ago`, stale };
  // Everything past an hour is stale for the heartbeat's purposes; the
  // unit only changes how the age reads, never that judgement.
  if (ageS < 23 * HOUR) {
    return { label: `${Math.round(ageS / HOUR)}h ago`, stale: true };
  }
  if (ageS < WEEK) return { label: `${Math.round(ageS / DAY)}d ago`, stale: true };
  if (ageS < MONTH) return { label: `${Math.round(ageS / WEEK)}w ago`, stale: true };
  if (ageS < YEAR) return { label: `${Math.round(ageS / MONTH)}mo ago`, stale: true };
  return { label: `${Math.round(ageS / YEAR)}y ago`, stale: true };
}
