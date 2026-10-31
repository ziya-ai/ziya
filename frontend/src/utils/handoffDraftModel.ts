/**
 * handoffDraftModel — which model writes the handoff draft.
 *
 * The drawer's "Ask the model to draft it" is a background turn through
 * /api/chat, which accepts a per-request model pin.  Drafting is a summary
 * task, so by default it runs on the portable `medium` tier (the standard
 * model) rather than whatever the conversation is pinned to — a frontier
 * pin would spend ~20x on a document the user then edits anyway.  The user
 * can pick another rung, or the conversation's own model, from a selector
 * on the drawer; the choice is remembered per browser.
 *
 * Tiers resolve server-side (/api/model-tiers, the same table the Task Card
 * ModelTierPicker reads) so the rung follows the model as models are
 * added or retired.  A failed resolution yields null and the caller falls
 * back to the conversation's pin.
 */

export type DraftTier = 'xsmall' | 'small' | 'medium' | 'large' | 'frontier';

export const DRAFT_TIER_ORDER: DraftTier[] = ['xsmall', 'small', 'medium', 'large', 'frontier'];
export const DRAFT_TIER_LABELS: Record<DraftTier, string> = {
  xsmall: 'XS', small: 'S', medium: 'M (standard)', large: 'L', frontier: 'Frontier',
};
/** The center rung: the standard model.  Drafting defaults here. */
export const DEFAULT_DRAFT_TIER: DraftTier = 'medium';

/** `null` means "the conversation's own model" (its pin or the server default). */
export type DraftTierChoice = DraftTier | null;

const STORAGE_KEY = 'ZIYA_HANDOFF_DRAFT_TIER';
const CONVERSATION_SENTINEL = 'conversation';

export function readDraftTier(): DraftTierChoice {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw === CONVERSATION_SENTINEL) return null;
    if (raw && (DRAFT_TIER_ORDER as string[]).includes(raw)) return raw as DraftTier;
  } catch { /* private mode / no storage */ }
  return DEFAULT_DRAFT_TIER;
}

export function writeDraftTier(choice: DraftTierChoice): void {
  try { localStorage.setItem(STORAGE_KEY, choice ?? CONVERSATION_SENTINEL); } catch { /* ignore */ }
}

export interface TierInfo {
  tier: DraftTier;
  resolved_model: string;
  /** True when a model is literally tagged with this tier; false for a nearest-rung fallback. */
  exact: boolean;
}

let _tiersPromise: Promise<{ endpoint: string; tiers: TierInfo[] }> | null = null;

/**
 * The tier table for the active endpoint.  Cached per page: the table is
 * static for a session.  A failed fetch clears the cache so the next open
 * retries instead of pinning the failure.
 */
export function fetchModelTiers(): Promise<{ endpoint: string; tiers: TierInfo[] }> {
  if (_tiersPromise) return _tiersPromise;
  _tiersPromise = (async () => {
    const cfg = await fetch('/api/config').then(r => (r.ok ? r.json() : null)).catch(() => null);
    const endpoint: string = cfg?.endpoint || 'bedrock';
    const data = await fetch(`/api/model-tiers?endpoint=${encodeURIComponent(endpoint)}`)
      .then(r => (r.ok ? r.json() : null));
    if (!data?.tiers) throw new Error('model tiers unavailable');
    return { endpoint, tiers: data.tiers as TierInfo[] };
  })();
  _tiersPromise.catch(() => { _tiersPromise = null; });
  return _tiersPromise;
}

/** Test seam. */
export function _resetTierCache(): void { _tiersPromise = null; }

/**
 * The concrete model name a tier currently resolves to, or null when the
 * choice is the conversation's own model or the table cannot be read.
 */
export async function resolveDraftTierModel(choice: DraftTierChoice): Promise<string | null> {
  if (!choice) return null;
  try {
    const { tiers } = await fetchModelTiers();
    return tiers.find(t => t.tier === choice)?.resolved_model || null;
  } catch {
    return null;
  }
}
