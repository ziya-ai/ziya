/**
 * Sort comparator for the chat-history sidebar tree.
 *
 * Extracted from MUIChatHistory so the ordering rules are unit-testable
 * without rendering the (heavy, non-pure) component.  Shared by both the
 * full tree rebuild and the sort-only fast path.
 *
 * Ordering tiers (highest first):
 *   1. Pinned folders
 *   2. Actively-processing conversations — currently streaming a response
 *      or running a task card.  An active conversation must never sort
 *      below an idle sibling regardless of timestamps: idle rows can
 *      legitimately carry newer timestamps (server sync bumps
 *      lastActiveAt, cross-tab touches bump lastAccessedAt), which
 *      previously let idle conversations sort above the one that was
 *      visibly showing "Processing…".  A folder containing an active
 *      descendant anywhere in its subtree (streaming or running a task
 *      card several levels down, e.g. inside a collapsed folder) also
 *      floats to this tier via the caller-computed `hasActiveDescendant`
 *      flag, so the containing folder rises with it rather than requiring
 *      the user to expand every folder to notice the activity.
 *   3. Delegate orchestrator ordering (when both sides carry delegateMeta)
 *   4. Activity time (folders: rolled-up lastActivityTime; conversations:
 *      max of lastAccessedAt / lastActiveAt / taskPlan boost), newest first
 *   5. Folders above conversations, then timestamp / id tiebreak
 */
export function sortComparator(
  a: any,
  b: any,
  taskPlanBoost: Map<string, number>,
  activeIds?: Set<string>,
): number {
  if (a.isPinned && !b.isPinned) return -1;
  if (!a.isPinned && b.isPinned) return 1;

  // Active-processing tier: a conversation currently streaming or running
  // a task always outranks anything that isn't (except pinned folders).
  // Folders participate via a precomputed `hasActiveDescendant` flag (set
  // by the caller's roll-up pass) rather than walking children here, so
  // this comparator stays O(1) per call.
  const isActive = (item: any): boolean =>
    !!(activeIds && item.conversation?.id && activeIds.has(item.conversation.id))
    || item.hasActiveDescendant === true;
  const aActive = isActive(a);
  const bActive = isActive(b);
  if (aActive && !bActive) return -1;
  if (bActive && !aActive) return 1;

  const aDel = a.delegateMeta;
  const bDel = b.delegateMeta;
  if (aDel && bDel) {
    if (aDel.role === 'orchestrator' && bDel.role !== 'orchestrator') return -1;
    if (bDel.role === 'orchestrator' && aDel.role !== 'orchestrator') return 1;
    return (a.conversation?.lastAccessedAt ?? 0) - (b.conversation?.lastAccessedAt ?? 0);
  }

  const getTime = (item: any) => {
    if (item.folder) return item.lastActivityTime > 0 ? item.lastActivityTime : item.createdAt;
    const ct = item.conversation?.lastAccessedAt ?? 0;
    const boost = item.conversation?.id ? (taskPlanBoost.get(item.conversation.id) || 0) : 0;
    // Server summaries use lastActiveAt; IDB-hydrated conversations use lastAccessedAt.
    const serverTs = item.conversation?.lastActiveAt ?? 0;
    return Math.max(ct, serverTs, boost);
  };
  const aT = getTime(a), bT = getTime(b);
  if (aT > 0 && bT > 0) return bT - aT;
  if (aT > 0) return -1;
  if (bT > 0) return 1;

  if (a.folder && !b.folder) return -1;
  if (!a.folder && b.folder) return 1;
  if (!a.folder && !b.folder) {
    const aA = a.conversation?.lastAccessedAt ?? 0;
    const bA = b.conversation?.lastAccessedAt ?? 0;
    if (aA > 0 && bA > 0) return bA - aA;
    if (aA > 0) return -1;
    if (bA > 0) return 1;
    return a.conversation?.id?.localeCompare(b.conversation?.id) || 0;
  }
  return 0;
}
