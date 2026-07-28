/**
 * Staleness + age presentation for the Bead Backlog Browser.
 * Thresholds are presentation-only and not configurable in v1.
 */

export const STALE_AMBER_MS = 7 * 24 * 60 * 60 * 1000;   // >= 7d  -> amber tint
export const STALE_WARN_MS = 14 * 24 * 60 * 60 * 1000;   // >= 14d -> warning marker

export type StalenessLevel = 'fresh' | 'amber' | 'warn';

export function stalenessLevel(ageMs: number): StalenessLevel {
  if (ageMs >= STALE_WARN_MS) return 'warn';
  if (ageMs >= STALE_AMBER_MS) return 'amber';
  return 'fresh';
}

export function stalenessMarker(ageMs: number): string {
  return stalenessLevel(ageMs) === 'warn' ? '\u26A0' : '';
}

export function stalenessColor(ageMs: number, isDarkMode: boolean): string | null {
  const level = stalenessLevel(ageMs);
  if (level === 'warn') return isDarkMode ? '#ef4444' : '#dc2626';
  if (level === 'amber') return isDarkMode ? '#f59e0b' : '#d97706';
  return null;
}

export function formatAge(ageMs: number): string {
  const days = Math.floor(ageMs / 86400000);
  if (days >= 1) return `${days}d`;
  const hours = Math.floor(ageMs / 3600000);
  if (hours >= 1) return `${hours}h`;
  const mins = Math.floor(ageMs / 60000);
  if (mins >= 1) return `${mins}m`;
  return 'just now';
}

export const STATUS_GLYPH: Record<string, string> = {
  parked: '\u25D0',
  abandoned: '\u2717',
  active: '\u25B6',
  completed: '\u2713',
};

// Canonical definition moved to utils/composerInject (it now has a real
// consumer outside this module); re-exported for back-compat.
export { COMPOSER_INJECT_EVENT } from '../../utils/composerInject';
export const BACKLOG_COUNT_EVENT = 'ziya:backlog-count';
