/**
 * @jest-environment jsdom
 *
 * A file-operation block's header offers its file as a draggable chip; other
 * tool blocks do not.  Asserted on the rendered chip, not on the parser alone.
 */
import React from 'react';
import { render } from '@testing-library/react';
import { FileDragChip, filePathFromHeader } from '../FileDragChip';

jest.mock('antd', () => ({ message: { warning: jest.fn() } }));

describe('filePathFromHeader', () => {
    it('names the file only for file read / write headers', () => {
        expect(filePathFromHeader('file write: Docs/x.md')).toBe('Docs/x.md');
        expect(filePathFromHeader('file read: app/utils/a b.py  ')).toBe('app/utils/a b.py');
        expect(filePathFromHeader('Shell: ls -la')).toBeNull();
        expect(filePathFromHeader('file write')).toBeNull();
        expect(filePathFromHeader(undefined)).toBeNull();
    });
});

describe('FileDragChip', () => {
    it('renders a draggable chip named after the file and wired to fileDrag', () => {
        const { container } = render(<FileDragChip path="Docs/plan.md" isDarkMode={false} />);
        const chip = container.querySelector('.file-drag-chip') as HTMLElement;
        expect(chip.textContent).toContain('plan.md');
        expect(chip.draggable).toBe(true);
        expect(chip.getAttribute('data-file-drag')).toBe('true');
    });
});
