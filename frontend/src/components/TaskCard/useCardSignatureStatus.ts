/**
 * Signature/escalation status for one saved card, for any surface that
 * shows that card.
 *
 * Exists because the two inline tiles drifted: StagedCardTile fetched
 * scope-status and badged "Needs signing", while LaunchedCardTile — the
 * tile shown while a card RUNS and after it finishes — never asked at
 * all.  A clamped run therefore looked identical to an authorized one,
 * and the only way to discover the card needed signing was to leave the
 * card interface and come back so the deck list re-fetched.
 *
 * Centralised rather than copied into each tile: a per-surface copy is
 * how the drift happened in the first place.  Every consumer reads the
 * field the server designates as canonical (``anyNeedsSignature`` /
 * per-block ``needsSignature``), with the legacy ``!authorized``
 * fallback in ONE place.
 *
 * Refreshes on an explicit event as well as on mount, because signing
 * happens OUT OF BAND — the user runs `ziya-approve` in a terminal, so
 * no in-app action marks the transition and a mount-only fetch shows a
 * stale warning until the component happens to remount.
 */

import { useCallback, useEffect, useState } from 'react';
import { taskCardApi, type CardScopeStatus } from '../../services/taskCardApi';

/** Fired after any surface learns a card's signature state may have changed. */
export const CARD_SCOPE_REFRESH_EVENT = 'ziya:card-scope-refresh';

export interface CardSignatureStatus {
  status: CardScopeStatus | null;
  unsignedCount: number;
  needsSigning: boolean;
  refresh: () => void;
}

/** Count blocks needing signature, tolerating an older server response. */
export function countUnsigned(status: CardScopeStatus | null): number {
  if (!status) return 0;
  return (status.blocks ?? [])
    .filter(b => b.needsSignature ?? !b.authorized)
    .length;
}

export function useCardSignatureStatus(
  projectId: string | undefined,
  cardId: string | undefined,
): CardSignatureStatus {
  const [status, setStatus] = useState<CardScopeStatus | null>(null);
  const [nonce, setNonce] = useState(0);

  const refresh = useCallback(() => setNonce(n => n + 1), []);

  useEffect(() => {
    if (!projectId || !cardId) { setStatus(null); return; }
    let cancelled = false;
    taskCardApi.scopeStatus(projectId, cardId)
      .then(st => { if (!cancelled) setStatus(st); })
      // Advisory: a failed check must never block a run or break a tile.
      // It does mean no warning shows, which is why "unknown" is treated
      // as "no escalation" rather than inventing one.
      .catch(() => { if (!cancelled) setStatus(null); });
    return () => { cancelled = true; };
  }, [projectId, cardId, nonce]);

  // Cross-surface refresh: signing is out-of-band, so any surface that
  // suspects a change tells every other surface to re-check.
  useEffect(() => {
    const onRefresh = (e: Event) => {
      const detail = (e as CustomEvent).detail as { cardId?: string } | undefined;
      // No cardId means "all cards" (e.g. the deck reloaded).
      if (!detail?.cardId || detail.cardId === cardId) refresh();
    };
    window.addEventListener(CARD_SCOPE_REFRESH_EVENT, onRefresh);
    return () => window.removeEventListener(CARD_SCOPE_REFRESH_EVENT, onRefresh);
  }, [cardId, refresh]);

  const unsignedCount = countUnsigned(status);
  return {
    status,
    unsignedCount,
    needsSigning: status?.anyNeedsSignature ?? unsignedCount > 0,
    refresh,
  };
}
