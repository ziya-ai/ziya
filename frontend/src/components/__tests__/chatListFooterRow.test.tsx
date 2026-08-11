/**
 * Integration of the chat-list footer row and bottom-fade affordance.
 *
 * chatListLayout.test.ts covers the arithmetic in isolation.  These cover the
 * wiring that arithmetic exists to support, which is where the original bugs
 * actually lived:
 *
 *   1. The Export/Import footer must render INSIDE react-window's scroll
 *      container.  As a flex sibling below the list it stole height from the
 *      list's DOM box while react-window still believed it had the full
 *      measured height — clipping the last row — and it could not scroll with
 *      the content.
 *   2. The footer row index is past the end of the flattened node array, so
 *      the footer check must happen before that array is indexed.  Getting
 *      this backwards throws on every render.
 *   3. The bottom fade must be present exactly when content remains below the
 *      fold, and absent once scrolled to the bottom.
 *
 * This mirrors MUIChatHistory's list structure rather than mounting the
 * component itself, which requires the full provider stack.  The structural
 * contract under test — footer inside the scroller, guarded index, fade gated
 * on chatListHasMoreBelow — is the same one the component implements.
 */
import React, { useState } from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import { FixedSizeList } from 'react-window';
import {
    chatListItemCount,
    isChatListFooterRow,
    chatListHasMoreBelow,
} from '../../utils/chatListLayout';

const ROW = 36;

const VirtualRow = ({ index, style, data }: any) => data.renderRow(index, style);

/**
 * Stand-in for MUIChatHistory's tree container + FixedSizeList + footer row.
 * viewportHeight is passed explicitly, standing in for the measured
 * treeContainerHeight the real component gets from its ResizeObserver.
 */
const ListHarness: React.FC<{ nodeCount: number; viewportHeight: number }> = ({
    nodeCount,
    viewportHeight,
}) => {
    const [scrollOffset, setScrollOffset] = useState(0);
    const nodes = Array.from({ length: nodeCount }, (_, i) => ({ id: `conv-${i}` }));
    const itemCount = chatListItemCount(nodes.length);

    return (
        <div
            data-testid="tree-container"
            style={{ position: 'relative', height: viewportHeight, overflow: 'hidden' }}
        >
            <FixedSizeList
                height={viewportHeight}
                width={300}
                itemCount={itemCount}
                itemSize={ROW}
                className="chat-history-tree"
                onScroll={({ scrollOffset: o }) => setScrollOffset(o)}
                itemKey={(index) =>
                    isChatListFooterRow(index, nodes.length)
                        ? '__chat-list-footer__'
                        : nodes[index].id
                }
                itemData={{
                    renderRow: (index: number, style: React.CSSProperties) => {
                        // Checked BEFORE indexing nodes — this index is past its end.
                        if (isChatListFooterRow(index, nodes.length)) {
                            return (
                                <div style={style} data-testid="footer-row">
                                    <button>Export</button>
                                    <button>Import</button>
                                </div>
                            );
                        }
                        return (
                            <div style={style} data-testid={`row-${index}`}>
                                {nodes[index].id}
                            </div>
                        );
                    },
                }}
            >
                {VirtualRow}
            </FixedSizeList>
            {chatListHasMoreBelow(scrollOffset, itemCount, ROW, viewportHeight) && (
                <div data-chat-list-bottom-fade="true" data-testid="bottom-fade" />
            )}
        </div>
    );
};

/**
 * Drives a scroll on react-window's outer element.
 *
 * react-window clamps the offset it accepts:
 *   Math.max(0, Math.min(scrollTop, scrollHeight - clientHeight))
 * jsdom has no layout, so scrollHeight and clientHeight are both 0 and every
 * scroll would clamp back to 0.  All three must be stubbed for the scroll to
 * register.
 */
function scrollListTo(offset: number, viewportHeight: number, contentHeight: number) {
    const outer = document.querySelector('.chat-history-tree') as HTMLElement;
    const stub = (prop: string, value: number) =>
        Object.defineProperty(outer, prop, { value, writable: true, configurable: true });
    stub('scrollHeight', contentHeight);
    stub('clientHeight', viewportHeight);
    stub('scrollTop', offset);
    fireEvent.scroll(outer);
}

describe('chat list footer row', () => {
    it('renders the footer inside the scroll container, not as a sibling below it', () => {
        render(<ListHarness nodeCount={3} viewportHeight={400} />);

        const footer = screen.getByTestId('footer-row');
        const scroller = document.querySelector('.chat-history-tree') as HTMLElement;

        expect(scroller).toBeTruthy();
        // The whole point: if the footer were a flex sibling it would live
        // outside the scroller and steal height from it.
        expect(scroller.contains(footer)).toBe(true);
    });

    it('positions the footer after the last node row', () => {
        render(<ListHarness nodeCount={3} viewportHeight={400} />);

        const footer = screen.getByTestId('footer-row');
        // react-window absolutely positions rows by index * itemSize.
        expect(footer.style.top).toBe(`${3 * ROW}px`);
    });

    it('renders the footer without throwing when there are no nodes', () => {
        // Regression: index 0 is the footer here.  Indexing nodes[0] first
        // dereferences undefined and throws during render.
        expect(() => render(<ListHarness nodeCount={0} viewportHeight={400} />)).not.toThrow();
        expect(screen.getByTestId('footer-row')).toBeInTheDocument();
    });

    it('scrolls out of view with the content rather than staying pinned', () => {
        const content = chatListItemCount(40) * ROW;
        render(<ListHarness nodeCount={40} viewportHeight={400} />);

        // 40 rows + footer = 41 rows; the footer is far below the fold.
        expect(screen.queryByTestId('footer-row')).not.toBeInTheDocument();

        scrollListTo(content - 400, 400, content);
        expect(screen.getByTestId('footer-row')).toBeInTheDocument();
    });
});

describe('chat list bottom fade', () => {
    it('is absent when all content fits the viewport', () => {
        render(<ListHarness nodeCount={3} viewportHeight={400} />);
        expect(screen.queryByTestId('bottom-fade')).not.toBeInTheDocument();
    });

    it('is present at the top of an overflowing list', () => {
        render(<ListHarness nodeCount={40} viewportHeight={400} />);
        expect(screen.getByTestId('bottom-fade')).toBeInTheDocument();
    });

    it('is present when rows fill the viewport exactly and only the footer is below', () => {
        // The reported symptom: a row aligns flush with the container edge, so
        // nothing signals that anything remains below.
        const nodeCount = 10;
        render(<ListHarness nodeCount={nodeCount} viewportHeight={nodeCount * ROW} />);
        expect(screen.getByTestId('bottom-fade')).toBeInTheDocument();
    });

    it('clears once scrolled to the bottom', () => {
        const content = chatListItemCount(40) * ROW;
        render(<ListHarness nodeCount={40} viewportHeight={400} />);
        expect(screen.getByTestId('bottom-fade')).toBeInTheDocument();

        scrollListTo(content - 400, 400, content);
        expect(screen.queryByTestId('bottom-fade')).not.toBeInTheDocument();
    });

    it('reappears after scrolling back up from the bottom', () => {
        const content = chatListItemCount(40) * ROW;
        const maxScroll = content - 400;
        render(<ListHarness nodeCount={40} viewportHeight={400} />);

        scrollListTo(maxScroll, 400, content);
        expect(screen.queryByTestId('bottom-fade')).not.toBeInTheDocument();

        scrollListTo(maxScroll - ROW, 400, content);
        expect(screen.getByTestId('bottom-fade')).toBeInTheDocument();
    });
});
