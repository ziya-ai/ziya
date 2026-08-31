/**
 * modelSyncService — aligns each tab's view of the SERVER-GLOBAL model
 * with reality.
 *
 * The lower-left label (FolderTree) and capability checks cache the
 * global model per tab, but /api/set-model can be called from ANY
 * session, and the only change signal is a window-local 'modelChanged'
 * CustomEvent in the tab that made the change.  Unpinned conversations
 * send no modelSelection, so they silently query whatever the server's
 * global model is NOW — while the label shows a stale snapshot.
 *
 * Two reconciliation layers:
 *
 *  1. Revalidation — re-fetch /api/current-model on window focus,
 *     visibility→visible, and a slow interval while visible.
 *  2. Per-response report — /api/chat responds with X-Ziya-Model /
 *     X-Ziya-Model-Source headers naming the model that actually
 *     streams.  A 'global'-sourced value that differs from the baseline
 *     proves drift on the very request it affected (chatApi calls
 *     reportStreamModel before reading the stream body).
 *
 * On drift this dispatches 'modelChanged' with detail
 * { source: 'external-sync' }:
 *   • FolderTree re-fetches the display name and pulses the label
 *   • SendChatContainer / EditSection re-check vision capability
 *   • ChatContext does NOT inject a "Model changed" conversation notice
 *     (its handler requires previousModel/newModel in the detail) —
 *     external changes are surfaced visually only, by design.
 */

export const EXTERNAL_MODEL_SYNC_SOURCE = 'external-sync';

const POLL_INTERVAL_MS = 30_000;

let lastKnownAlias: string | null = null;
let started = false;
let inFlight = false;

function dispatchDrift(model: string, previous: string | null): void {
  window.dispatchEvent(new CustomEvent('modelChanged', {
    detail: { source: EXTERNAL_MODEL_SYNC_SOURCE, model, previous },
  }));
}

async function fetchGlobalAlias(): Promise<string | null> {
  try {
    const resp = await fetch('/api/current-model');
    if (!resp.ok) return null;
    const data = await resp.json();
    const alias = data?.model_alias || data?.model_id;
    return typeof alias === 'string' && alias ? alias : null;
  } catch {
    return null; // transient network error — the next trigger retries
  }
}

/** Re-fetch the global model; dispatch if it drifted from the baseline. */
export async function syncNow(): Promise<void> {
  if (inFlight) return;
  inFlight = true;
  try {
    const alias = await fetchGlobalAlias();
    if (!alias) return;
    const prev = lastKnownAlias;
    lastKnownAlias = alias;
    if (prev !== null && prev !== alias) {
      console.info(`modelSync: global model drift detected (${prev} → ${alias})`);
      dispatchDrift(alias, prev);
    }
  } finally {
    inFlight = false;
  }
}

/**
 * Authoritative per-response report from the X-Ziya-Model header.
 * Only 'global'-sourced values participate: a pinned request reports
 * the pin, which says nothing about the server-global model.
 */
export function reportStreamModel(model: string | null, source: string | null): void {
  if (!model || source !== 'global') return;
  const prev = lastKnownAlias;
  if (prev === model) return;
  lastKnownAlias = model;
  if (prev !== null) {
    console.info(`modelSync: response reported global model ${model} (label had ${prev})`);
    dispatchDrift(model, prev);
  }
}

/** Adopt a new baseline without dispatching (local change already handled). */
async function refreshBaselineQuietly(): Promise<void> {
  const alias = await fetchGlobalAlias();
  if (alias) lastKnownAlias = alias;
}

/** Idempotent; safe to call from any always-mounted component. */
export function startModelSync(): void {
  if (started || typeof window === 'undefined') return;
  started = true;

  void syncNow(); // establish the baseline

  window.addEventListener('focus', () => { void syncNow(); });
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') void syncNow();
  });

  // A modelChanged we didn't dispatch means THIS tab committed a change
  // (ModelConfigButton).  The UI is already updated; just move the
  // baseline so the next sync doesn't re-report it as drift.
  window.addEventListener('modelChanged', (e: Event) => {
    const detail = (e as CustomEvent).detail;
    if (detail && detail.source === EXTERNAL_MODEL_SYNC_SOURCE) return;
    void refreshBaselineQuietly();
  });

  setInterval(() => {
    if (document.visibilityState === 'visible') void syncNow();
  }, POLL_INTERVAL_MS);
}

/** Test hooks — reset module state between unit tests. */
export function _resetForTest(): void {
  lastKnownAlias = null;
  started = false;
  inFlight = false;
}

export function _getLastKnownAlias(): string | null {
  return lastKnownAlias;
}
