/**
 * Layout math for the virtualized conversation list (MUIChatHistory).
 *
 * These cover the three regressions the helper exists to prevent:
 *   1. The Export/Import footer must occupy a real row in react-window's
 *      itemCount, otherwise it cannot scroll with the list.
 *   2. Footer detection must be checked BEFORE indexing the flattened node
 *      array — the footer index is out of that array's bounds.
 *   3. The bottom fade must appear exactly when content remains below the
 *      fold, and must not flicker on a sub-pixel remainder at the bottom.
 */
import {
    CHAT_LIST_FOOTER_ROWS,
    chatListItemCount,
    isChatListFooterRow,
    chatListHasMoreBelow,
} from '../../utils/chatListLayout';

const ROW = 36;

describe('chatListItemCount', () => {
    it('reserves a row for the footer', () => {
        expect(chatListItemCount(0)).toBe(CHAT_LIST_FOOTER_ROWS);
        expect(chatListItemCount(5)).toBe(5 + CHAT_LIST_FOOTER_ROWS);
    });

    it('does not go negative on a nonsensical node count', () => {
        expect(chatListItemCount(-3)).toBe(CHAT_LIST_FOOTER_ROWS);
    });
});

describe('isChatListFooterRow', () => {
    it('treats only the index past the last node as the footer', () => {
        expect(isChatListFooterRow(0, 3)).toBe(false);
        expect(isChatListFooterRow(2, 3)).toBe(false);
        expect(isChatListFooterRow(3, 3)).toBe(true);
    });

    it('is the footer for index 0 when there are no nodes', () => {
        expect(isChatListFooterRow(0, 0)).toBe(true);
    });

    it('covers every index produced by chatListItemCount', () => {
        const nodeCount = 4;
        const total = chatListItemCount(nodeCount);
        const footerIndices: number[] = [];
        for (let i = 0; i < total; i++) {
            if (isChatListFooterRow(i, nodeCount)) footerIndices.push(i);
        }
        // Exactly one footer row, and it is the last index.
        expect(footerIndices).toEqual([total - 1]);
    });
});

describe('chatListHasMoreBelow', () => {
    it('is false when all content fits in the viewport', () => {
        // 3 rows + footer = 4 rows = 144px, viewport 400px.
        expect(chatListHasMoreBelow(0, chatListItemCount(3), ROW, 400)).toBe(false);
    });

    it('is true at the top of an overflowing list', () => {
        // 40 rows + footer = 1476px, viewport 400px.
        expect(chatListHasMoreBelow(0, chatListItemCount(40), ROW, 400)).toBe(true);
    });

    it('is false once scrolled to the true bottom', () => {
        const itemCount = chatListItemCount(40);
        const maxScroll = itemCount * ROW - 400;
        expect(chatListHasMoreBelow(maxScroll, itemCount, ROW, 400)).toBe(false);
    });

    it('does not flicker on a sub-pixel remainder near the bottom', () => {
        const itemCount = chatListItemCount(40);
        const maxScroll = itemCount * ROW - 400;
        expect(chatListHasMoreBelow(maxScroll - 0.4, itemCount, ROW, 400)).toBe(false);
    });

    it('is still true one full row from the bottom', () => {
        const itemCount = chatListItemCount(40);
        const maxScroll = itemCount * ROW - 400;
        expect(chatListHasMoreBelow(maxScroll - ROW, itemCount, ROW, 400)).toBe(true);
    });

    it('is false before the viewport has been measured', () => {
        // Guards the first paint, where the ResizeObserver has not fired yet.
        expect(chatListHasMoreBelow(0, chatListItemCount(40), ROW, 0)).toBe(false);
    });

    it('reports content below when the footer alone overflows', () => {
        // The exact regression case: rows fill the viewport to the pixel, so
        // the visible bottom row aligns flush with the edge and the only
        // thing below the fold is the footer.  Without the fade this looks
        // like the end of the list.
        const nodeCount = 10;
        const viewport = nodeCount * ROW; // rows fit exactly; footer does not
        expect(chatListHasMoreBelow(0, chatListItemCount(nodeCount), ROW, viewport)).toBe(true);
    });
});
