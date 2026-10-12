/**
 * Gate for the token bar's "⇢ Hand off…" affordance
 * (design/conversation-handoff.md).
 *
 * The client's estimate describes the NEXT prompt as the bar can see it
 * (files, chat, tool schemas, learned system overhead).  The server records
 * what the provider actually billed on the LAST turn (`contextPressure` on
 * the /handoff GET), which includes the tool results that turn accumulated
 * mid-stream.  Whichever is higher decides — a low server figure must never
 * hide a button the client would show, and vice versa.
 */
export type HandoffPressureTier = 'none' | 'warning' | 'danger';

export const HANDOFF_WARNING_RATIO = 0.7;
export const HANDOFF_DANGER_RATIO = 0.9;

export function handoffPressureTier(
  clientTokens: number,
  tokenLimit: number,
  serverPressure: number | null | undefined,
): HandoffPressureTier {
  const client = tokenLimit > 0 ? clientTokens / tokenLimit : 0;
  const server = typeof serverPressure === 'number' && isFinite(serverPressure) ? serverPressure : 0;
  const ratio = Math.max(client, server);
  if (ratio >= HANDOFF_DANGER_RATIO) return 'danger';
  if (ratio >= HANDOFF_WARNING_RATIO) return 'warning';
  return 'none';
}

export type HandoffAffordance = HandoffPressureTier | 'draft';

/**
 * What the token bar shows.  A living draft is an offer on its own, below
 * the pressure gate: the model writes one when asked or when it judges the
 * work worth carrying forward, and says so.  Gating only on pressure hid
 * the one conversation-view entry point to a draft the user was told about.
 * Pressure tiers still win, because they carry the urgency.
 */
export function handoffAffordance(
  tier: HandoffPressureTier, hasDraft: boolean,
): HandoffAffordance {
  if (tier !== 'none') return tier;
  return hasDraft ? 'draft' : 'none';
}
