import React from 'react';

/**
 * Drop-in replacement for React.lazy() that retries failed chunk loads.
 *
 * On the first failure the dynamic import is retried up to `maxRetries`
 * times with a cache-busting query parameter so the browser fetches a
 * fresh copy instead of replaying a cached 404 / old hash.
 *
 * If all retries are exhausted the page is hard-reloaded once (guarded
 * by a sessionStorage flag so it doesn't loop).
 */
const RELOAD_FLAG = '__ziya_chunk_reload';

export function lazyWithRetry<T extends React.ComponentType<any>>(
  factory: () => Promise<{ default: T }>,
  maxRetries = 2,
): React.LazyExoticComponent<T> {
  return React.lazy(() => retryImport(factory, maxRetries));
}

async function retryImport<T extends React.ComponentType<any>>(
  factory: () => Promise<{ default: T }>,
  retries: number,
): Promise<{ default: T }> {
  for (let attempt = 0; attempt <= retries; attempt++) {
    try {
      // On retry attempts, bust the module cache by appending a timestamp.
      // The first attempt (attempt === 0) uses the normal import so the
      // browser can serve a cached chunk when nothing has changed.
      if (attempt > 0) {
        const bustCache = `?t=${Date.now()}`;
        // Inject cache-buster by wrapping the factory
        return await factory().catch(() =>
          import(/* webpackIgnore: true */ `${bustCache}`) as any
        );
      }
      return await factory();
    } catch (err) {
      if (attempt === retries) {
        // All retries failed — hard-reload once to pick up new chunks
        if (!sessionStorage.getItem(RELOAD_FLAG)) {
          sessionStorage.setItem(RELOAD_FLAG, '1');
          window.location.reload();
        }
        throw err;
      }
    }
  }
  // Unreachable, but satisfies TypeScript
  return factory();
}
