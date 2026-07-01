import React from 'react';

const RELOAD_FLAG = '__ziya_chunk_reload';
const RELOAD_FLAG_TS = '__ziya_chunk_reload_ts';

export function lazyWithRetry<T extends React.ComponentType<any>>(
  factory: () => Promise<{ default: T }>,
  maxRetries = 2,
): React.LazyExoticComponent<T> {
  return React.lazy(() => retryImport(factory, maxRetries));
}

function wait(ms: number): Promise<void> {
  return new Promise(resolve => setTimeout(resolve, ms));
}

/**
 * Reload bypassing the HTTP cache for the HTML document.  A plain
 * window.location.reload() may serve a cached index.html / main.js that
 * still references a chunk hash deleted by the latest build, so the same
 * dead chunk is requested again and the reload is wasted.  Appending a
 * one-time cache-bust query param forces a distinct URL → cache miss on
 * the document → fresh bundle hashes.
 */
function hardReload(): void {
  try {
    const url = new URL(window.location.href);
    url.searchParams.set('_cb', String(Date.now()));
    window.location.replace(url.toString());
  } catch {
    // URL API unavailable or replace blocked — fall back to a plain reload.
    window.location.reload();
  }
}

async function retryImport<T extends React.ComponentType<any>>(
  factory: () => Promise<{ default: T }>,
  retries: number,
): Promise<{ default: T }> {
  // Clear a stale reload flag so a new session can still trigger a hard
  // reload if chunks keep failing.  Stale = set more than 30s ago, or
  // flag exists but no timestamp (written by old code without timestamp).
  try {
    const ts = sessionStorage.getItem(RELOAD_FLAG_TS);
    if (!ts || Date.now() - parseInt(ts, 10) > 30_000) {
      sessionStorage.removeItem(RELOAD_FLAG);
      sessionStorage.removeItem(RELOAD_FLAG_TS);
    }
  } catch { /* sessionStorage may be unavailable */ }

  for (let attempt = 0; attempt <= retries; attempt++) {
    try {
      return await factory();
    } catch (err) {
      const isChunkError = err instanceof Error &&
        (err.name === 'ChunkLoadError' || err.message.includes('Loading chunk'));

      if (isChunkError) {
        // Webpack marks failed chunks in its internal JSONP registry.
        // Retrying factory() won't issue a new network request — only a
        // full reload clears the internal chunk state.
        if (!sessionStorage.getItem(RELOAD_FLAG)) {
          sessionStorage.setItem(RELOAD_FLAG, '1');
          sessionStorage.setItem(RELOAD_FLAG_TS, String(Date.now()));
          hardReload();
          // Never-settling promise prevents React rendering the error
          // boundary during the brief window before the page unloads.
          return new Promise(() => {});
        }
        // Already reloaded once and still failing — genuine problem.
        throw err;
      }

      if (attempt === retries) throw err;
      await wait(2_000 * (attempt + 1));
    }
  }
  return factory();
}
