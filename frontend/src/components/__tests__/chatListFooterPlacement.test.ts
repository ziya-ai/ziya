/**
 * Export/Import footer placement in the conversation list.
 *
 * Field case (2026-11-04): with the recency filter on, the list held a few
 * rows and the footer sat right under the last title instead of at the
 * bottom of the panel; at compact density the one-row-tall footer was
 * shorter than the buttons, which spilled over that title; and the buttons
 * were right-aligned against the scrollbar.
 */
import * as fs from 'fs';
import * as path from 'path';
import {
    chatListFooterBox, CHAT_LIST_FOOTER_MIN_HEIGHT,
} from '../../utils/chatListLayout';

describe('chatListFooterBox', () => {
    it('pins the footer to the bottom of the viewport when the rows are short', () => {
        const box = chatListFooterBox(6, 28, 600);
        expect(box.top + box.height).toBe(600);
        expect(box.top).toBeGreaterThan(6 * 28);
    });

    it('follows the last row when the rows fill the viewport', () => {
        expect(chatListFooterBox(40, 28, 600).top).toBe(40 * 28);
        // Rows end inside the viewport but the footer would not fit below them.
        expect(chatListFooterBox(20, 28, 580).top).toBe(20 * 28);
    });

    it('is never shorter than a button, and never shorter than a row', () => {
        expect(chatListFooterBox(3, 28, 600).height).toBe(CHAT_LIST_FOOTER_MIN_HEIGHT);
        expect(chatListFooterBox(3, 56, 600).height).toBe(56);
    });

    it('falls back to following the rows before the viewport is measured', () => {
        expect(chatListFooterBox(3, 28, 0).top).toBe(3 * 28);
    });
});

describe('wiring: MUIChatHistory draws the footer from chatListFooterBox, centered', () => {
    const src = fs.readFileSync(path.join(__dirname, '..', 'MUIChatHistory.tsx'), 'utf8');
    const start = src.indexOf('data-testid="chat-list-footer"');
    const block = src.slice(start, src.indexOf('</div>', start));

    it('the footer row exists and uses the helper with the measured viewport', () => {
        expect(start).toBeGreaterThan(-1);
        expect(src).toMatch(/chatListFooterBox\(flatNodes\.length,\s*rowHeight,\s*treeContainerHeight\)/);
        expect(block).toMatch(/top:\s*box\.top/);
        expect(block).toMatch(/height:\s*box\.height/);
    });

    it('centers the buttons', () => {
        expect(block).toMatch(/justifyContent:\s*'center'/);
        expect(block).not.toMatch(/flex-end/);
    });
});
