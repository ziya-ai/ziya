/**
 * Header width vs. padding seam.
 *
 * index.css sizes .app-header at width: 100% of the viewport.  The project
 * has no global `box-sizing: border-box` reset, so any horizontal padding a
 * later stylesheet adds to .app-header is ADDED to that 100% and the header
 * overflows the viewport.  The right-hand control cluster is anchored with
 * `right: 10px` relative to the header's padding box, so the overflow shows
 * up as the New-chat caret being clipped at the screen edge.
 *
 * jsdom does not do layout, so this is a static check on the stylesheets:
 * every .app-header rule that sets horizontal padding must also opt into
 * border-box.  It fails against theme-tokens.css without the box-sizing line.
 */
import * as fs from 'fs';
import * as path from 'path';

const read = (rel: string) => fs.readFileSync(path.join(__dirname, '..', '..', rel), 'utf8');

/** Return the declaration blocks of every rule whose selector list targets .app-header itself. */
function appHeaderBlocks(css: string): string[] {
    const blocks: string[] = [];
    const re = /([^{}]+)\{([^{}]*)\}/g;
    let m: RegExpExecArray | null;
    while ((m = re.exec(css)) !== null) {
        const selectors = m[1].split(',').map(s => s.trim());
        // `.app-header` or `body.dark .app-header`, but not `.app-header h2` etc.
        if (selectors.some(s => /(^|\s)\.app-header$/.test(s))) blocks.push(m[2]);
    }
    return blocks;
}

describe('.app-header sizing', () => {
    const indexCss = read('index.css');
    const tokensCss = read('styles/theme-tokens.css');

    it('index.css still sizes the header at width: 100% (precondition for the seam)', () => {
        expect(appHeaderBlocks(indexCss).some(b => /width:\s*100%/.test(b))).toBe(true);
    });

    it('every rule adding horizontal padding to .app-header also sets box-sizing: border-box', () => {
        const padded = [...appHeaderBlocks(indexCss), ...appHeaderBlocks(tokensCss)]
            .filter(b => /padding(-left|-right)?\s*:/.test(b) && !/padding:\s*0\s*;/.test(b));
        expect(padded.length).toBeGreaterThan(0); // the theme-tokens padding-left rule must be found
        for (const b of padded) {
            expect(b).toMatch(/box-sizing:\s*border-box/);
        }
    });
});
