/**
 * Layout math for the virtualized conversation list (MUIChatHistory).
 *
 * The Export/Import footer is rendered as a real row inside react-window's
 * itemCount rather than as a flex sibling below the list.  As a sibling it
 * stole height from the list's DOM box while react-window still believed it
 * had the full container height, which clipped the last row and produced a
 * wrong scroll extent.  As a virtual row it scrolls with the content and the
 * list owns the entire measured viewport.
 */

/** Number of synthetic rows appended after the flattened tree nodes. */
export const CHAT_LIST_FOOTER_ROWS = 1;

/**
 * Total react-window itemCount for a given number of flattened tree nodes,
 * including the footer row.
 */
export function chatListItemCount(nodeCount: number): number {
    const nodes = Math.max(0, nodeCount);
    return nodes + CHAT_LIST_FOOTER_ROWS;
}

/**
 * True when a row index addresses the footer rather than a tree node.
 *
 * Must be checked BEFORE indexing the flattened node array — the footer
 * index is out of that array's bounds.
 */
export function isChatListFooterRow(index: number, nodeCount: number): boolean {
    return index >= Math.max(0, nodeCount);
}

/**
 * True when content remains below the visible fold, i.e. the bottom fade
 * affordance should be shown.
 *
 * Guards two edge cases:
 *   - viewportHeight of 0 (first paint, before the ResizeObserver fires):
 *     reports false rather than flashing a fade over an unmeasured list.
 *   - sub-pixel remainders at the true bottom: a half-pixel of residual
 *     scroll extent must not flicker the fade on and off.
 */
export function chatListHasMoreBelow(
    scrollOffset: number,
    itemCount: number,
    rowHeight: number,
    viewportHeight: number,
): boolean {
    if (!(viewportHeight > 0) || !(rowHeight > 0) || itemCount <= 0) return false;
    const totalHeight = itemCount * rowHeight;
    const remaining = totalHeight - (Math.max(0, scrollOffset) + viewportHeight);
    return remaining > 0.5;
}
