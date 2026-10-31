/**
 * Sidebar scroll anchoring: a re-sort that moves the selected conversation
 * (sending a message makes it active and bumps its timestamp) must keep it
 * at the same viewport position, and must not touch the scroll otherwise.
 */
import * as fs from 'fs';
import * as path from 'path';
import { anchoredScrollOffset } from '../chatListAnchor';
import { sortComparator } from '../chatTreeSort';

const H = 30;      // row height
const VIEW = 600;  // viewport: 20 rows

describe('anchoredScrollOffset', () => {
  it('keeps a visible row at the same viewport position when it moves', () => {
    // Row 500 shown 6 rows from the top (offset 494 rows), re-sorted to 200.
    const off = anchoredScrollOffset({
      prevIndex: 500, newIndex: 200, scrollOffset: 494 * H, rowHeight: H, viewportHeight: VIEW,
    });
    expect(off).toBe(194 * H);
    expect(200 * H - off!).toBe(500 * H - 494 * H);
  });

  it('moving to the very top clamps to 0 and the row stays visible', () => {
    expect(anchoredScrollOffset({
      prevIndex: 500, newIndex: 0, scrollOffset: 494 * H, rowHeight: H, viewportHeight: VIEW,
    })).toBe(0);
  });

  it('does nothing when the row did not move, is gone, or was off screen', () => {
    const base = { scrollOffset: 494 * H, rowHeight: H, viewportHeight: VIEW };
    expect(anchoredScrollOffset({ ...base, prevIndex: 500, newIndex: 500 })).toBeNull();
    expect(anchoredScrollOffset({ ...base, prevIndex: 500, newIndex: -1 })).toBeNull();
    expect(anchoredScrollOffset({ ...base, prevIndex: -1, newIndex: 3 })).toBeNull();
    // Above the viewport and below it.
    expect(anchoredScrollOffset({ ...base, prevIndex: 100, newIndex: 3 })).toBeNull();
    expect(anchoredScrollOffset({ ...base, prevIndex: 494 + 20, newIndex: 3 })).toBeNull();
    // Partially visible at the top edge still counts.
    expect(anchoredScrollOffset({ ...base, scrollOffset: 494 * H + 10, prevIndex: 494, newIndex: 10 }))
      .toBe(10 * H + 10);
  });
});

describe('the trigger: sending reorders the selected conversation', () => {
  // The reason the fix exists. If streaming stops floating a chat, the
  // anchor is harmless; this keeps the cause documented and checked.
  it('a streaming conversation sorts above a newer idle sibling', () => {
    const idle = { conversation: { id: 'a', lastAccessedAt: 2000 } };
    const sent = { conversation: { id: 'b', lastAccessedAt: 1000 } };
    const before = [idle, sent].sort((x, y) => sortComparator(x, y, new Map()));
    expect(before.map(n => n.conversation.id)).toEqual(['a', 'b']);
    const after = [idle, sent].sort((x, y) => sortComparator(x, y, new Map(), new Set(['b'])));
    expect(after.map(n => n.conversation.id)).toEqual(['b', 'a']);
  });
});

describe('seam: MUIChatHistory applies the anchor', () => {
  const src = fs.readFileSync(
    path.resolve(__dirname, '..', '..', 'components', 'MUIChatHistory.tsx'), 'utf8');

  it('calls anchoredScrollOffset from a layout effect and scrolls the list', () => {
    expect(src).toMatch(/import \{ anchoredScrollOffset, ChatListAnchor \} from '\.\.\/utils\/chatListAnchor'/);
    const m = src.match(/useLayoutEffect\(\(\) => \{[\s\S]*?\}, \[flatNodes, expandedSet, currentConversationId, searchQuery\]\);/);
    expect(m).not.toBeNull();
    expect(m![0]).toMatch(/anchoredScrollOffset\(/);
    expect(m![0]).toMatch(/virtualListRef\.current\.scrollTo\(offset\)/);
    // Expand/collapse and conversation switches are excluded.
    expect(m![0]).toMatch(/prev\.convId !== cid \|\| prev\.expanded !== expandedSet/);
  });

  it('keeps the scroll-offset ref current from onScroll', () => {
    expect(src).toMatch(/listScrollOffsetRef\.current = scrollOffset;/);
  });
});
