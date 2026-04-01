/**
 * Tests for the copy-paste whitespace CSS fix.
 *
 * Problem: `.message .message-content` has `white-space: pre-wrap`, which is
 * inherited by `<p>`, `<li>`, headings, and `<blockquote>` elements produced
 * by the markdown renderer.  Under `pre-wrap` the browser's clipboard
 * serializer does not insert standard paragraph-break newlines between block
 * elements, so pasted text in Quip (and other apps) loses paragraph breaks.
 *
 * Fix: override `white-space: normal` on those block-level children.
 * Code blocks already carry their own `white-space: pre` styling.
 */

import * as fs from 'fs';
import * as path from 'path';

const CSS_PATH = path.resolve(__dirname, '../../index.css');
const css = fs.readFileSync(CSS_PATH, 'utf-8');

describe('copy-paste whitespace CSS', () => {
    test('.message-content container retains white-space: pre-wrap', () => {
        expect(css).toMatch(/\.message\s+\.message-content\s*\{[^}]*white-space\s*:\s*pre-wrap/);
    });

    // Each block-level element should appear in a selector that sets
    // white-space: normal.  The selectors are comma-separated across
    // multiple lines, so we search for the element name anywhere in the
    // combined selector list that precedes the `{ white-space: normal }` block.
    const blockElements = ['p', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'blockquote'];

    test.each(blockElements)(
        '.message .message-content %s has white-space: normal override',
        (element) => {
            // Match: .message .message-content <element> anywhere in a
            // comma-separated selector list followed by { white-space: normal }
            const re = new RegExp(
                '\\.message\\s+\\.message-content\\s+' + element + '\\b'
            );
            expect(css).toMatch(re);
        },
    );

    test('the override block sets white-space: normal', () => {
        // Find the rule block that contains .message-content p
        const overrideMatch = css.match(
            /\.message\s+\.message-content\s+p[\s\S]*?\{([^}]+)\}/,
        );
        expect(overrideMatch).not.toBeNull();
        expect(overrideMatch![1]).toMatch(/white-space\s*:\s*normal/);
    });

    test('pre/code elements are not overridden to white-space: normal', () => {
        // Code blocks must keep pre-wrap / pre — they should NOT appear
        // in the override selector list.
        const codeOverride = css.match(
            /\.message\s+\.message-content\s+(?:pre|code)\s*\{[^}]*white-space\s*:\s*normal/,
        );
        expect(codeOverride).toBeNull();
    });
});
