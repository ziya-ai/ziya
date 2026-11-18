/**
 * A model-written bead record must not reach the chat bubble.
 *
 * The server appends a store-sourced record to earlier assistant turns in
 * the prompt only (app/utils/bead_history_record.py).  The model sometimes
 * imitates that line at the end of its own reply; nothing on the way to
 * the browser removed it, so it rendered as plain text.
 *
 * Unit tests cover the strip; the last block asserts the SEAM, that
 * MarkdownRenderer calls it and exempts human messages.
 */
import * as fs from 'fs';
import * as path from 'path';
import { stripBeadRecords } from '../beadRecordStrip';

const OPEN = '\u2039bead_record\u203a';
const CLOSE = '\u2039/bead_record\u203a';
const LEAK = `${OPEN}Bead store: 2 bead(s) created in this turn were saved: `
    + `"remove dead tc-callee CSS" [bead_56b], "deck run pane" [bead_6ff].${CLOSE}`;

describe('stripBeadRecords', () => {
    it('removes the record and the blank lines before it', () => {
        expect(stripBeadRecords(`Done.\n\n${LEAK}`)).toBe('Done.');
    });

    it('removes a record in the middle and keeps both sides', () => {
        expect(stripBeadRecords(`before\n\n${LEAK}\n\nafter`)).toBe('before\n\nafter');
    });

    it('handles the ASCII bracket form', () => {
        expect(stripBeadRecords('a <bead_record>x</bead_record> b')).toBe('a  b');
    });

    it('hides an unclosed record at the end of streaming text', () => {
        // Mid-stream the closer has not arrived yet.
        expect(stripBeadRecords(`Done.\n\n${OPEN}Bead store: 2 bead(s) cre`)).toBe('Done.');
    });

    it('does not swallow later lines after an unclosed opener', () => {
        const text = `${OPEN}half\nreal prose`;
        expect(stripBeadRecords(text)).toContain('real prose');
    });

    it('leaves fenced and inline code alone', () => {
        const fenced = `text\n\`\`\`\n${LEAK}\n\`\`\`\nmore`;
        expect(stripBeadRecords(fenced)).toBe(fenced);
        const inline = `the \`${OPEN}…${CLOSE}\` line`;
        expect(stripBeadRecords(inline)).toBe(inline);
    });

    it('returns text without a record unchanged', () => {
        const text = 'nothing to see \u2039here\u203a';
        expect(stripBeadRecords(text)).toBe(text);
        expect(stripBeadRecords('')).toBe('');
    });
});

describe('seam: MarkdownRenderer applies the strip', () => {
    const src = fs.readFileSync(
        path.resolve(__dirname, '../../components/MarkdownRenderer.tsx'), 'utf8');

    it('imports and calls stripBeadRecords', () => {
        expect(src).toMatch(/import \{ stripBeadRecords \} from '\.\.\/utils\/beadRecordStrip'/);
        expect(src).toMatch(/processedMarkdown = stripBeadRecords\(processedMarkdown\)/);
    });

    it('exempts human messages, which may quote a record', () => {
        expect(src).toMatch(/role !== 'human'[^\n]*\n?[^\n]*stripBeadRecords\(/);
    });
});
