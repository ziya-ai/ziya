/**
 * Hashing for MUIChatHistory's tree-build memo cache.
 *
 * The sidebar memoizes its assembled conversation tree and reuses the
 * cached result whenever the STRUCTURAL hash (below) and a separate sort
 * hash are both unchanged.  The structural hash MUST therefore incorporate
 * every conversation/folder field that affects how a row renders.
 *
 * If a rendered field is omitted from this hash, changing it leaves the
 * cache valid: the fast-exit returns the stale cached tree and the row
 * renders stale until an UNRELATED change (a periodic-sync _version bump,
 * a project switch, …) forces a rebuild.  That was the "assigned flags
 * don't appear for a few minutes" bug — \`flags\`/\`flagColor\` drove a row
 * badge but weren't hashed.
 *
 * Extracted from the inline memo so this "field omitted from the hash"
 * bug class is unit-testable.
 */

/** FNV-1a 32-bit incremental string hasher. */
export const fnv1a = () => {
  let h = 0x811c9dc5;
  return {
    add(s: string) {
      for (let i = 0; i < s.length; i++) {
        h ^= s.charCodeAt(i);
        h = Math.imul(h, 0x01000193);
      }
    },
    value() { return h >>> 0; },
  };
};

/**
 * Hash the identity + rendered-badge state of the folder/conversation
 * inputs.  Order-sensitive: it mirrors the caller's forEach iteration
 * order over its already-sorted arrays.
 *
 * Inputs are loosely typed (\`any[]\`) to match the memo's existing casts;
 * every field read here is one the row renderer also reads.
 */
export function computeStructuralHash(folders: any[], conversations: any[]): number {
  const sh = fnv1a();
  folders.forEach(f => {
    sh.add(f.id || '');
    sh.add(f.name || '');
    sh.add(f.parentId || '');
    sh.add(f.isGlobal ? 'g' : '');
    sh.add(f.taskPlan?.source_conversation_id || '');
  });
  conversations.forEach(c => {
    sh.add(c.id || '');
    sh.add(c.title || '');
    sh.add(c.folderId || '');
    sh.add(c.isActive === false ? '0' : '1');
    sh.add(c.isGlobal ? 'g' : '');
    sh.add(c.delegateMeta?.status || '');
    sh.add(String(c.openBeadCount || 0));
    sh.add(String(c.openWorkItemCount || 0));
    sh.add(c.branchedFrom || '');
    sh.add((c.flags || []).join(','));
    sh.add(c.flagColor || '');
  });
  return sh.value();
}
