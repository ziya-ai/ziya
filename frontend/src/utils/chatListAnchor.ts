/**
 * Keep the selected conversation where it is on screen when the sidebar
 * re-sorts under it.
 *
 * Sending a message makes the conversation active (streaming) and bumps its
 * lastAccessedAt, so sortComparator moves it, and any folder holding it, to
 * the top of its group.  The virtual list keeps its pixel offset, so the row
 * the user was looking at slides away.  Given the row's index before and
 * after, return the scroll offset that puts it back at the same viewport
 * position, or null when nothing should change: the row did not move, is
 * not in the list, or was not visible beforehand (then the user was looking
 * elsewhere and their position wins).
 */
export interface AnchorInput {
  prevIndex: number;
  newIndex: number;
  scrollOffset: number;
  rowHeight: number;
  viewportHeight: number;
}

export function anchoredScrollOffset({
  prevIndex, newIndex, scrollOffset, rowHeight, viewportHeight,
}: AnchorInput): number | null {
  if (prevIndex < 0 || newIndex < 0 || prevIndex === newIndex) return null;
  if (rowHeight <= 0 || viewportHeight <= 0) return null;
  const rowTop = prevIndex * rowHeight - scrollOffset;
  if (rowTop + rowHeight <= 0 || rowTop >= viewportHeight) return null;
  return Math.max(0, newIndex * rowHeight - rowTop);
}

export interface ChatListAnchor {
  convId: string | null;
  index: number;
  expanded: ReadonlySet<string>;
}
