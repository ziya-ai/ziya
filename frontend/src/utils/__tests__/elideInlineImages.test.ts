import { elideInlineImages, INLINE_IMAGE_NOTE } from '../elideInlineImages';

const BIG = 'A'.repeat(200000);
const img = (mt = 'png') =>
    `<img src="data:image/${mt};base64,${BIG}" alt="Rendered diagram" style="max-width:100%;" />`;

describe('elideInlineImages', () => {
    it('replaces a multi-hundred-KB data-URI img tag with the note', () => {
        const out = elideInlineImages(`before\n\n${img()}\n\nafter`);
        expect(out).toContain(INLINE_IMAGE_NOTE);
        expect(out).toContain('before');
        expect(out).toContain('after');
        expect(out.length).toBeLessThan(1000);
        expect(out).not.toContain('base64');
    });

    it('replaces every tag, across media types and newlines inside the tag', () => {
        const multi = `<img\n src="data:image/jpeg;base64,${BIG}"\n alt="x">`;
        const out = elideInlineImages(`${img()}${multi}${img('webp')}`);
        expect(out.split(INLINE_IMAGE_NOTE).length - 1).toBe(3);
        expect(out).not.toContain('data:image');
    });

    it('leaves ordinary http images and plain text untouched', () => {
        const s = 'x <img src="https://example.com/a.png"> y';
        expect(elideInlineImages(s)).toBe(s);
        expect(elideInlineImages('')).toBe('');
    });

    it('matches the server-side note so replay text is identical', () => {
        const fs = require('fs'), path = require('path');
        const py = fs.readFileSync(
            path.join(__dirname, '../../../../app/utils/tool_history_rewrite.py'), 'utf8');
        const lead = 'Ziya: rendered image elided on replay.';
        expect(py).toContain(lead);
        expect(INLINE_IMAGE_NOTE).toContain(lead);
    });
});

describe('payload wiring (seam)', () => {
    const fs = require('fs'), path = require('path');
    const read = (rel: string) =>
        fs.readFileSync(path.join(__dirname, '../../', rel), 'utf8');

    it.each(['apis/chatApi.ts', 'components/TokenCountDisplay.tsx'])(
        '%s elides inline images from assistant content before POSTing', (rel) => {
            const src = read(rel);
            expect(src).toMatch(/import \{ elideInlineImages \} from '[./]+\/utils\/elideInlineImages'|import \{ elideInlineImages \} from '\.\.\/utils\/elideInlineImages'/);
            expect(src).toMatch(/elideInlineImages\(/);
        });
});
