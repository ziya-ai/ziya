/**
 * D-018 (chat-message w3-10/11/14/15, group G-a4f055): inline styled HTML spans
 * are torn apart by marked's inline lexer.
 *
 * marked returns an inline `<span style="...">chip</span>` as THREE tokens — a
 * dangling opening-tag html token, a text token, and a closing-tag html token
 * (verified out-of-band with marked 16.4.2). The renderer's html case rendered
 * the lone opener on its own, the browser auto-closed it as an EMPTY styled box,
 * and the chip text landed as an unstyled sibling: styled chips and HTML status
 * badges never rendered as intended, in BOTH themes.
 *
 * coalesceInlineHtmlTokens stitches such a run back into one balanced inline
 * html token (`_coalescedInline: true`) whose serialised HTML carries the style
 * AND the text, which the renderer then emits inside an inline <span>.
 *
 * DIRECTION: the import is of a module that does not exist in unpatched source,
 * so this whole suite fails to load against pre-fix code and passes with it.
 * The defect is structural and theme-independent (a pure token transform that
 * runs before any colour resolution), so the both-theme obligation is
 * discharged at the shared render stage — the reassembled markup is identical
 * in light and dark.
 */
import {
    coalesceInlineHtmlTokens,
    tagBalance,
    CoalesceToken,
} from '../../../utils/inlineHtmlCoalesce';

// The exact token stream marked emits for an inline styled span
// `<span style="...">PASS</span>` (opening tag / text / closing tag).
function tornSpan(style: string, text: string): CoalesceToken[] {
    return [
        { type: 'html', raw: `<span style="${style}">`, text: `<span style="${style}">`, block: false },
        { type: 'text', raw: text, text },
        { type: 'html', raw: '</span>', text: '</span>', block: false },
    ];
}

describe('D-018: tagBalance detects dangling inline openers', () => {
    it('a lone opening tag is net-positive, a closing tag net-negative, a balanced pair zero', () => {
        expect(tagBalance('<span style="color:#fff">')).toBe(1);
        expect(tagBalance('</span>')).toBe(-1);
        expect(tagBalance('<span>x</span>')).toBe(0);
    });
    it('void/self-closing tags do not change depth', () => {
        expect(tagBalance('<br/>')).toBe(0);
        expect(tagBalance('text <br> more')).toBe(0);
        expect(tagBalance('<img src="x">')).toBe(0);
    });
});

describe('D-018: torn-apart inline styled spans are reassembled', () => {
    it('merges opener + text + closer into ONE balanced inline html token', () => {
        const merged = coalesceInlineHtmlTokens(tornSpan('background:#2e7d32;color:#ffffff', 'PASS'));
        expect(merged).toHaveLength(1);
        const t = merged[0];
        expect(t.type).toBe('html');
        expect((t as any)._coalescedInline).toBe(true);
        expect(t.block).toBe(false);
        // The style and the chip text now live in ONE token, balanced.
        expect(t.text).toBe('<span style="background:#2e7d32;color:#ffffff">PASS</span>');
        expect(tagBalance(t.text as string)).toBe(0);
    });

    it('the merged token carries BOTH the style attribute AND the text (proves they are no longer divorced)', () => {
        const merged = coalesceInlineHtmlTokens(tornSpan('color:#444444;background:#f5f5f5', 'inline chip'));
        const html = merged[0].text as string;
        expect(html).toContain('style="color:#444444;background:#f5f5f5"');
        expect(html).toContain('inline chip');
        // and the text is INSIDE the span, not a trailing sibling
        expect(html).toMatch(/<span[^>]*>inline chip<\/span>/);
    });

    it('reassembles a run of adjacent badges separated by a <br> (w3-14 shape)', () => {
        const tokens: CoalesceToken[] = [
            ...tornSpan('background:#2e7d32;color:#ffffff', 'PASS'),
            { type: 'br', raw: '\n' },
            ...tornSpan('background:#c62828;color:#000000', 'FAIL'),
        ];
        const merged = coalesceInlineHtmlTokens(tokens);
        // Two balanced chips + the untouched <br> between them.
        const chips = merged.filter((t) => (t as any)._coalescedInline);
        expect(chips).toHaveLength(2);
        expect(chips[0].text).toBe('<span style="background:#2e7d32;color:#ffffff">PASS</span>');
        expect(chips[1].text).toBe('<span style="background:#c62828;color:#000000">FAIL</span>');
        expect(merged.some((t) => t.type === 'br')).toBe(true);
    });

    it('escapes text content when serialising it back into the span', () => {
        const merged = coalesceInlineHtmlTokens([
            { type: 'html', raw: '<span style="color:#333">', text: '<span style="color:#333">', block: false },
            { type: 'text', raw: 'a < b & c', text: 'a < b & c' },
            { type: 'html', raw: '</span>', text: '</span>', block: false },
        ]);
        expect(merged[0].text).toBe('<span style="color:#333">a &lt; b &amp; c</span>');
    });
});

describe('D-018: the pass never loses or corrupts content', () => {
    it('leaves a token list with no dangling inline opener untouched (same reference)', () => {
        const tokens: CoalesceToken[] = [
            { type: 'text', raw: 'hello', text: 'hello' },
            { type: 'html', raw: '<span>x</span>', text: '<span>x</span>', block: false }, // already balanced
        ];
        expect(coalesceInlineHtmlTokens(tokens)).toBe(tokens);
    });

    it('does not merge a BLOCK-level html token (block: true)', () => {
        const tokens: CoalesceToken[] = [
            { type: 'html', raw: '<div style="background:#111">', text: '<div style="background:#111">', block: true },
            { type: 'text', raw: 'body', text: 'body' },
        ];
        // block html is left as-is (its own balanced block handling applies).
        expect(coalesceInlineHtmlTokens(tokens)).toBe(tokens);
    });

    it('aborts (loses nothing) when an opener meets an un-serialisable token before closing', () => {
        const strong: CoalesceToken = { type: 'strong', tokens: [{ type: 'text', text: 'bold' }] } as any;
        const tokens: CoalesceToken[] = [
            { type: 'html', raw: '<span style="color:#333">', text: '<span style="color:#333">', block: false },
            strong,
            { type: 'html', raw: '</span>', text: '</span>', block: false },
        ];
        const out = coalesceInlineHtmlTokens(tokens);
        // No coalesced token was produced; every original token survives in order.
        expect(out.some((t) => (t as any)._coalescedInline)).toBe(false);
        expect(out).toHaveLength(3);
        expect(out[1]).toBe(strong);
    });

    it('aborts when the opener is never closed (leaves the opener in place)', () => {
        const tokens: CoalesceToken[] = [
            { type: 'html', raw: '<span style="color:#333">', text: '<span style="color:#333">', block: false },
            { type: 'text', raw: 'unterminated', text: 'unterminated' },
        ];
        const out = coalesceInlineHtmlTokens(tokens);
        expect(out.some((t) => (t as any)._coalescedInline)).toBe(false);
        expect(out).toHaveLength(2);
    });
});
