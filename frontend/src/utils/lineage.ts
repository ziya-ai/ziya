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

export interface HandoffOrdinalLike {
    id: string;
    branchedFrom?: string;
    lineageKind?: string;
    handedOffTo?: string;
}

export interface SegmentOrdinal {
    index: number;   // 1-based position in the chain
    total: number;   // chain length
}

/**
 * Position-in-chain for every segment of every handoff chain
 * (design/conversation-handoff.md, "Chat list").  Rows are NOT collapsed —
 * each segment stays visible, nested under its source like a fork — the
 * ordinal is what makes the order legible (1/3 ⇢, ⛓ 2/3, ⛓ 3/3).
 *
 * A link A→B requires all three fields to agree: B.lineageKind='handoff',
 * B.branchedFrom=A, A.handedOffTo=B.  A superseded second continuation
 * (its source points elsewhere) is not part of the chain and gets no
 * ordinal.  A continuation whose source is not loaded is numbered as the
 * tail of a 2-segment chain so it is still marked.  Cycle-safe.
 */
export function handoffSegmentOrdinals(
    conversations: HandoffOrdinalLike[],
): Map<string, SegmentOrdinal> {
    const byId = new Map(conversations.map(c => [c.id, c]));
    const isLink = (parent: HandoffOrdinalLike | undefined, child: HandoffOrdinalLike | undefined): boolean =>
        !!parent && !!child && child.lineageKind === 'handoff'
        && child.branchedFrom === parent.id && parent.handedOffTo === child.id;

    const out = new Map<string, SegmentOrdinal>();
    for (const c of conversations) {
        if (c.lineageKind !== 'handoff' || !c.branchedFrom) continue;
        const parent = byId.get(c.branchedFrom);
        if (!parent) {
            if (!out.has(c.id)) out.set(c.id, { index: 2, total: 2 });
            continue;
        }
        if (!isLink(parent, c)) continue;   // superseded continuation — not a chain link
        // Only start a walk from the chain's ROOT (a parent that is not
        // itself a linked continuation); deeper links are reached by the walk.
        const grandparent = parent.branchedFrom ? byId.get(parent.branchedFrom) : undefined;
        if (parent.lineageKind === 'handoff' && isLink(grandparent, parent)) continue;
        const chain: string[] = [parent.id];
        const seen = new Set<string>(chain);
        let cur: HandoffOrdinalLike | undefined = parent;
        while (cur && chain.length < 50) {
            const next = cur.handedOffTo ? byId.get(cur.handedOffTo) : undefined;
            if (!next || !isLink(cur, next) || seen.has(next.id)) break;
            seen.add(next.id);
            chain.push(next.id);
            cur = next;
        }
        chain.forEach((id, i) => out.set(id, { index: i + 1, total: chain.length }));
    }
    return out;
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
