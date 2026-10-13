/**
 * @jest-environment jsdom
 *
 * Seam test for DraggableTable: it must actually wire attachTableDrag so a
 * press on the table away from its text arms the table as the drag source,
 * and the drag that follows publishes the wrapped table with the markdown
 * prop as the text/plain payload. There is deliberately no handle: the
 * gesture is the affordance.
 */

import React from 'react';
import { render } from '@testing-library/react';
import { DraggableTable } from '../DraggableTable';

const md = '| a | b |\n|---|---|\n| 1 | 2 |';

function mount() {
    const { container } = render(
        <DraggableTable markdown={md} isDarkMode={false}>
            <table><tbody><tr><td>1</td><td>2</td></tr></tbody></table>
        </DraggableTable>,
    );
    const table = container.querySelector('table') as HTMLTableElement;
    (table as any).getBoundingClientRect = () => ({ width: 200, height: 40, top: 0, left: 0, right: 200, bottom: 40, x: 0, y: 0, toJSON() {} });
    return { container, table };
}

describe('DraggableTable', () => {
    it('renders no handle and leaves the table non-draggable at rest so text selects', () => {
        const { container, table } = mount();
        expect(container.querySelector('[data-table-drag-handle]')).toBeNull();
        expect(table.draggable).toBe(false);
        // The wrapper hugs the table rather than spanning the column.
        const wrap = container.querySelector('.draggable-table') as HTMLElement;
        // jsdom's CSSOM drops `width: fit-content` as an unknown value (React
        // writes styles through the CSSOM), so the shrink-wrap itself cannot be
        // observed here; assert the sibling declarations from the same rule.
        expect(wrap.style.maxWidth).toBe('100%');
        expect(wrap.style.position).toBe('relative');
        // Nothing enclosing the text may be draggable at rest: Chromium starts
        // a drag for any press inside a draggable ancestor, which stopped
        // click-and-sweep selection inside the table and skipped the table
        // when a selection swept across it.  Only the table itself is armed,
        // per press.
        expect(wrap.draggable).toBe(false);
        expect(wrap.hasAttribute('data-table-drag')).toBe(false);
    });

    it('a press on the table away from text arms the table itself as the drag source', () => {
        const { table } = mount();

        // jsdom has no caret API, so every press reads as off-text.
        table.querySelector('td')!.dispatchEvent(
            Object.assign(new Event('pointerdown', { bubbles: true }), { button: 0, clientX: 5, clientY: 5 }));
        expect(table.draggable).toBe(true);

        const data: Record<string, string> = {};
        const ev = new Event('dragstart', { bubbles: true, cancelable: true }) as any;
        ev.dataTransfer = { effectAllowed: '', setData: (k: string, v: string) => { data[k] = v; } };
        table.dispatchEvent(ev);
        expect(data['DownloadURL']).toMatch(/^image\/(png|svg\+xml):table-/);
        expect(data['text/html']).toMatch(/^<table[\s\S]*<td[^>]*>1<\/td>/);
        expect(data['text/plain']).toBe(md);

        table.dispatchEvent(new Event('pointerup', { bubbles: true }));
        expect(table.draggable).toBe(false);
    });
});
