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
 * Age threshold (s) after which a running task reads as HUNG and the
 * tile offers force-stop even without a prior cancel.  Deliberately far
 * above STALE_AFTER_S: a legitimate ``npm run build`` under a long
 * shell grant is silent for minutes, and offering to interrupt it after
 * two would invite exactly the redo work force-stop is meant to save.
 */
export const HUNG_AFTER_S = 600;

/** True when the run has been silent long enough to offer force-stop. */
export function isHung(
  tsMs: number,
  nowMs: number = Date.now(),
): boolean {
  return Math.max(0, (nowMs - tsMs) / 1000) >= HUNG_AFTER_S;
}

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
  tsMs: number,
  nowMs: number = Date.now(),
): ActivityLabel {
  // Both arguments are epoch ms — the run record's unit (schema 2) and
  // what useTaskRunStream converts wire seconds to.
  const ageS = Math.max(0, (nowMs - tsMs) / 1000);
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
