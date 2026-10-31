/**
 * The "📁 Drop inside folder" overlay cannot outlive the drag.
 *
 * Observed 2026-10-25: after dropping a conversation into a folder, the
 * green dashed overlay stayed on the folder row until reload.  endCustomDrag
 * awaits the move before clearing isDragging; in that window the document
 * mousemove listener and the tree scroll listener are still attached with a
 * closure that still reads "dragging", so a pointer move (or the list
 * scrolling as the moved row re-rendered) drew a fresh overlay AFTER
 * endCustomDrag's final sweep.  Nothing removed it once the listeners were
 * torn down.
 *
 * Two layers, both asserted here:
 *   1. dragSettlingRef — set at the top of endCustomDrag (called
 *      synchronously from mouseup, before the awaited move), checked by
 *      mousemove so a stale handler draws nothing, reset at the next drag.
 *   2. The effect teardown sweeps any .drop-insertion-line as a backstop.
 *
 * Source-level because the drag machinery is closed over inside one effect
 * of a large component.  Each assertion names a hop a regression would cut.
 */
import * as fs from 'fs';
import * as path from 'path';

const SRC = fs.readFileSync(path.join(__dirname, '..', 'MUIChatHistory.tsx'), 'utf8');

function between(start: string, end: string): string {
    const s = SRC.indexOf(start);
    expect(s).toBeGreaterThan(-1);
    const e = SRC.indexOf(end, s + start.length);
    expect(e).toBeGreaterThan(s);
    return SRC.slice(s, e);
}

describe('drag-settling guard', () => {
    it('is declared as a ref, so stale closures read it live', () => {
        expect(SRC).toMatch(/const dragSettlingRef = useRef\(false\)/);
    });

    it('is cleared when a new drag starts', () => {
        const start = between('const startCustomDrag = useCallback(', 'setCustomDragState({');
        expect(start).toMatch(/dragSettlingRef\.current = false/);
    });

    it('is set in endCustomDrag BEFORE the awaited move', () => {
        const end = between('const endCustomDrag = useCallback(', 'await handleMoveConversation');
        expect(end).toMatch(/dragSettlingRef\.current = true/);
    });

    it('endCustomDrag is called synchronously from mouseup, so the guard is up before any later mousemove', () => {
        const up = between('const handleMouseUp = (e: MouseEvent) => {', '\n    };');
        expect(up).toMatch(/endCustomDrag\(targetNodeId/);
        expect(up).not.toMatch(/await endCustomDrag|setTimeout\(.*endCustomDrag/);
    });

    it('short-circuits mousemove before any overlay is drawn', () => {
        const move = between('const handleMouseMove = (e: MouseEvent) => {', "insertionLine.textContent = '📁 Drop inside folder'");
        const guard = move.indexOf('if (dragSettlingRef.current) return;');
        expect(guard).toBeGreaterThan(-1);
        // The guard precedes the first DOM write (ghost position update).
        expect(guard).toBeLessThan(move.indexOf('ghostElement.style.left'));
    });
});

describe('effect teardown backstop', () => {
    it('removes stray insertion lines when the drag listeners are detached', () => {
        const teardown = between("document.removeEventListener('mouseup', handleMouseUp);", '};');
        expect(teardown).toMatch(/querySelectorAll(<HTMLElement>)?\('\.drop-insertion-line'\)\s*\.forEach\(\w+ => \w+\.remove\(\)\)/);
    });

    it('positive control: the overlay is still drawn during a live drag', () => {
        expect(SRC).toContain("insertionLine.textContent = '📁 Drop inside folder'");
        expect(SRC).toContain("insertionLine.className = 'drop-insertion-line'");
    });
});
