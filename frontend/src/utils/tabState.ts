/**
 * Per-tab state storage using sessionStorage.
 *
 * Falls back to reading from localStorage for one-time migration:
 * users upgrading from versions that used localStorage for per-tab
 * state will have their value migrated on first read.
 *
 * Per-TAB state (each browser tab is independent):
 *   - ZIYA_CURRENT_CONVERSATION_ID
 *   - ZIYA_CHECKED_FOLDERS
 *   - ZIYA_EXPANDED_FOLDERS
 *
 * Per-USER preferences stay in localStorage (shared across tabs):
 *   - ZIYA_TOP_DOWN_MODE, theme, panel width, etc.
 */

/**
 * Read a per-tab state value. Checks sessionStorage first, then
 * falls back to localStorage for migration from older versions.
 */
export function getTabState(key: string): string | null {
  const sessionValue = sessionStorage.getItem(key);
  if (sessionValue !== null) return sessionValue;

  // One-time migration: read from localStorage if it exists there
  const localValue = localStorage.getItem(key);
  if (localValue !== null) {
    // Migrate to sessionStorage for this tab
    sessionStorage.setItem(key, localValue);
    // Don't remove from localStorage — other tabs may still need to migrate
  }
  return localValue;
}

/**
 * Write a per-tab state value to sessionStorage only.
 */
export function setTabState(key: string, value: string): void {
  sessionStorage.setItem(key, value);
}
