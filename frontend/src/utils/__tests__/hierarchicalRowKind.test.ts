import * as fs from 'fs';
import * as path from 'path';
import { isRawCodeRow } from '../hierarchicalRowKind';

describe('isRawCodeRow', () => {
    it('shows rows with a code language as raw code', () => {
        expect(isRawCodeRow({ language: 'python' })).toBe(true);
        expect(isRawCodeRow({ language: 'plaintext' })).toBe(true);
    });

    it('renders text, markdown and unlabelled rows as markdown', () => {
        expect(isRawCodeRow({ language: 'text' })).toBe(false);
        expect(isRawCodeRow({ language: 'markdown' })).toBe(false);
        expect(isRawCodeRow({})).toBe(false);
        expect(isRawCodeRow(null)).toBe(false);
    });

    it('decides per row, so a mixed list keeps each row\'s kind', () => {
        const rows = [{ language: 'markdown' }, { language: 'python' }, { language: 'text' }];
        expect(rows.map(isRawCodeRow)).toEqual([false, true, false]);
    });

    it('is what MarkdownRenderer uses for each row, not row 0 for all', () => {
        const src = fs.readFileSync(
            path.join(__dirname, '../../components/MarkdownRenderer.tsx'),
            'utf8',
        );
        expect(src).toContain('isRawCodeRow(result)');
        expect(src).not.toMatch(/hierarchicalResults\[0\]\.language/);
    });
});
