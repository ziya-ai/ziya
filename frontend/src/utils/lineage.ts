/**
 * Lineage-chain resolution for branched conversations (bead-branching,
 * see design/bead-branching.md).
 *
 * A conversation created by "split from here" carries branchedFrom (parent
 * conversation id) + branchedFromLabel (the bead content at the seam).  The
 * lineage bar shows the breadcrumb back to trunk, which needs each ancestor's
 * *title* — not stored on the branch, so we resolve it by walking the
 * branchedFrom chain through the conversations list.
 *
 * Pure + dependency-light (minimal local interface, mirroring folderUtil's
 * GlobalChainFolder convention) so it is unit-testable without React or the
 * full Conversation type.  Cycle-safe (visited set) and depth-bounded.
 */

export interface LineageConversationLike {
    id: string;
    title?: string;
    branchedFrom?: string;
    branchedFromLabel?: string;
}

export interface LineageNode {
    id: string;
    title: string;
    resolved: boolean;            // false = ancestor not loaded (placeholder)
    branchedFromLabel?: string;   // the seam bead label this node branched from
}

/**
 * Build the lineage chain for a conversation, ordered trunk → … → current.
 *
 * Returns [] when currentId isn't in the list, and a single-element chain for
 * a trunk (unbranched) conversation — callers render the bar only when the
 * chain has more than one node.  An ancestor referenced by branchedFrom but
 * absent from the list yields a best-effort placeholder node (resolved:false)
 * so the bar still shows "branched from …" and the return click can lazy-load
 * it.
 */
export function buildLineageChain(
    currentId: string,
    conversations: LineageConversationLike[],
    maxDepth = 50,
): LineageNode[] {
    const byId = new Map(conversations.map(c => [c.id, c]));
    const current = byId.get(currentId);
    if (!current) return [];

    const chain: LineageNode[] = [];
    const visited = new Set<string>();
    let node: LineageConversationLike | undefined = current;
    let depth = 0;

    while (node && depth < maxDepth) {
        if (visited.has(node.id)) break;   // cycle guard
        visited.add(node.id);
        chain.unshift({
            id: node.id,
            title: node.title || 'Untitled',
            resolved: true,
            branchedFromLabel: node.branchedFromLabel,
        });
        const parentId = node.branchedFrom;
        if (!parentId) break;
        const parent = byId.get(parentId);
        if (!parent) {
            // Ancestor not loaded (cross-project / not yet synced).  Best-effort
            // placeholder so the bar still renders a return link.
            chain.unshift({ id: parentId, title: 'Parent conversation', resolved: false });
            break;
        }
        node = parent;
        depth++;
    }
    return chain;
}
