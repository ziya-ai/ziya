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

export type LineageKind = 'fork' | 'branch' | 'handoff';

export interface ForkLineageSource {
    id: string;
    lineageRootId?: string;
}

/**
 * Lineage fields a PLAIN fork stamps on its new record.  Spread this over
 * the `...source` copy in forkConversation so the fork:
 *
 *  - points at its source via branchedFrom (sidebar nesting + LineageBar,
 *    which previously only branch-from-bead got), with lineageKind='fork';
 *  - does NOT inherit the source's own seam fields — a fork of a branch
 *    would otherwise carry the branch's branchedFromLabel and appear to
 *    have been cut at that seam;
 *  - shares the lineage bead tree (b2): root = source's root, or the source
 *    itself when it is a trunk.  Flat, never chains;
 *  - is not itself handed off, whatever the source was.  The source's
 *    `handoff` document (if the source is a continuation) is deliberately
 *    left in place by the caller's spread — it is part of that
 *    conversation's standing context and a fork continues the same work.
 *
 * Returned as an object (not applied) so it stays pure and testable.
 */
export function forkLineageFields(source: ForkLineageSource): {
    branchedFrom: string;
    branchedAtMessageIndex: undefined;
    branchedFromLabel: undefined;
    lineageKind: LineageKind;
    lineageRootId: string;
    handedOffTo: undefined;
} {
    return {
        branchedFrom: source.id,
        branchedAtMessageIndex: undefined,
        branchedFromLabel: undefined,
        lineageKind: 'fork',
        lineageRootId: source.lineageRootId || source.id,
        handedOffTo: undefined,
    };
}

export interface HandoffChainLike {
    id: string;
    branchedFrom?: string;
    lineageKind?: string;
    handedOffTo?: string;
}

/**
 * Resolve handoff chains for the chat list (design/conversation-handoff.md,
 * "divergence nests, continuation chains").
 *
 * A segment link A→B exists only when all three fields agree: B.lineageKind
 * is 'handoff', B.branchedFrom is A, and A.handedOffTo is B.  The TAIL of a
 * chain — the newest segment, not itself linked forward — owns the row; the
 * map value is its predecessors, newest first.  A handoff child whose source
 * is missing, or which its source does not point forward to (a superseded
 * second continuation), is not a chain and falls through to ordinary
 * under-parent nesting.  Cycle-safe and depth-bounded.
 */
export function buildHandoffChains(conversations: HandoffChainLike[]): Map<string, string[]> {
    const byId = new Map(conversations.map(c => [c.id, c]));
    const isLink = (child: HandoffChainLike, parent: HandoffChainLike | undefined) =>
        !!parent && child.lineageKind === 'handoff' && child.branchedFrom === parent.id
        && parent.handedOffTo === child.id;

    // Sources of a confirmed link are never tails.
    const linkedForward = new Set<string>();
    for (const c of conversations) {
        if (c.lineageKind !== 'handoff' || !c.branchedFrom) continue;
        const p = byId.get(c.branchedFrom);
        if (isLink(c, p)) linkedForward.add(p!.id);
    }

    const chains = new Map<string, string[]>();
    for (const c of conversations) {
        if (c.lineageKind !== 'handoff' || linkedForward.has(c.id)) continue;
        const preds: string[] = [];
        const seen = new Set<string>([c.id]);
        let cur: HandoffChainLike = c;
        while (cur.branchedFrom && preds.length < 50) {
            const p = byId.get(cur.branchedFrom);
            if (!isLink(cur, p) || seen.has(p!.id)) break;
            seen.add(p!.id);
            preds.push(p!.id);
            cur = p!;
        }
        if (preds.length) chains.set(c.id, preds);
    }
    return chains;
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
