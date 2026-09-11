/**
 * Regression test: railroad and wavedrom flashed an ERROR CARD on every
 * streaming chunk, where every other diagram type showed nothing until its
 * body was renderable.
 *
 * ROOT CAUSE
 *
 * Two layers, each individually reasonable:
 *
 *   1. Both wrappers claim their spec by TYPE (`canHandle: spec?.type ===
 *      'railroad'`), so the plugin is selected on the FIRST streaming chunk
 *      and render() runs against a truncated JSON body. It cannot parse, so
 *      renderError() paints a red card straight into the container -- once per
 *      chunk -- until the closing fence arrives and the body finally parses.
 *   2. D3Renderer DOES suppress diagram errors while streaming, but only the
 *      renderError STATE it owns (`renderError && !isStreaming && ...`). A card
 *      the plugin writes into the container bypasses that gate entirely.
 *
 * Both plugins already declared `isDefinitionComplete`, the hook meant to gate
 * exactly this, but D3Renderer only consults it when the spec arrives as a bare
 * STRING (`plugin?.isDefinitionComplete && typeof spec === 'string'`). A
 * markdown fence always arrives as an OBJECT envelope, so the hook was dead
 * code on the only path that matters.
 *
 * FIX
 *
 * Each wrapper applies its own completeness predicate at the top of render()
 * through the shared isStreamingIncomplete() guard, and returns silently while
 * the body is still arriving.
 *
 * SCOPE OF THE SUPPRESSION -- deliberately narrow. A body that PARSES but is
 * invalid still errors mid-stream, because D3Renderer skips the post-stream
 * re-render when the definition text is unchanged (its spec hash excludes the
 * streaming flags, D3Renderer.tsx:895). Deferring that error would hide it
 * permanently and leave a blank frame in place of the diagnosis.
 *
 * NON-VACUITY: each suppression case is paired with the SAME definition
 * rendered with isStreaming absent, which must still produce the card. Against
 * unpatched source the suppression assertions fail while the paired ones pass.
 */
import { railroadPlugin } from '../railroadPlugin';
import { wavedromPlugin } from '../wavedromPlugin';

const MARKER = '[data-diagram-error]';

/** Bodies truncated mid-token, i.e. what a chunk mid-stream actually holds. */
const PARTIAL_RAILROAD = '{"diagram": {"sequence": [{"terminal": "SE';
const PARTIAL_WAVEDROM = '{signal: [{name: "clk", wave: "p...';

function container(): HTMLElement {
    return document.createElement('div');
}

describe('railroad — streaming error cards', () => {
    it('paints no error card while a truncated body is still arriving', () => {
        const c = container();
        railroadPlugin.render(c, null, {
            type: 'railroad', definition: PARTIAL_RAILROAD, isStreaming: true,
        }, false);
        expect(c.querySelector(MARKER)).toBeNull();
        expect(c.innerHTML).toBe('');
    });

    it('paired guard: the same truncated body DOES error when not streaming', () => {
        const c = container();
        railroadPlugin.render(c, null, {
            type: 'railroad', definition: PARTIAL_RAILROAD,
        }, false);
        expect(c.querySelector(MARKER)).not.toBeNull();
    });

    it('still errors mid-stream for a body that parses but is invalid', () => {
        // Complete JSON, unrecognised node key: a real diagnosis that must not
        // be deferred -- the post-stream re-render is skipped when the
        // definition text has not changed, so a deferred error is a lost one.
        const c = container();
        railroadPlugin.render(c, null, {
            type: 'railroad', definition: '{"diagram": {"regex": "[0-9]+"}}',
            isStreaming: true,
        }, false);
        const card = c.querySelector(MARKER);
        expect(card).not.toBeNull();
        expect(card!.getAttribute('data-diagram-error'))
            .toContain('unrecognized railroad node');
    });

    it('renders normally mid-stream once the body is complete and valid', () => {
        const c = container();
        railroadPlugin.render(c, null, {
            type: 'railroad', definition: '{"diagram": {"terminal": "ok"}}',
            isStreaming: true,
        }, false);
        expect(c.querySelector('svg')).not.toBeNull();
        expect(c.querySelector(MARKER)).toBeNull();
    });
});

describe('wavedrom — streaming error cards', () => {
    it('paints no error card while a truncated body is still arriving', async () => {
        const c = container();
        await wavedromPlugin.render(c, null, {
            type: 'wavedrom', definition: PARTIAL_WAVEDROM, isStreaming: true,
        }, false);
        expect(c.querySelector(MARKER)).toBeNull();
        expect(c.innerHTML).toBe('');
    });

    it('paired guard: the same truncated body DOES error when not streaming', async () => {
        const c = container();
        await wavedromPlugin.render(c, null, {
            type: 'wavedrom', definition: PARTIAL_WAVEDROM,
        }, false);
        expect(c.querySelector(MARKER)).not.toBeNull();
    });

    it('still errors mid-stream for a body that parses but is invalid', async () => {
        // Parses as JSON5 but carries none of signal/reg/assign: a genuine
        // validation failure, so it must surface immediately, not be deferred.
        const c = container();
        await wavedromPlugin.render(c, null, {
            type: 'wavedrom', definition: '{"head": {"text": "x"}}',
            isStreaming: true,
        }, false);
        expect(c.querySelector(MARKER)).not.toBeNull();
    });
});

describe('the completeness predicates that back the guard', () => {
    it('railroad: truncated is incomplete, whole is complete', () => {
        expect(railroadPlugin.isDefinitionComplete!(PARTIAL_RAILROAD)).toBe(false);
        expect(railroadPlugin.isDefinitionComplete!(
            '{"diagram": {"terminal": "ok"}}')).toBe(true);
    });

    it('wavedrom: truncated is incomplete, whole is complete', () => {
        expect(wavedromPlugin.isDefinitionComplete!(PARTIAL_WAVEDROM)).toBe(false);
        expect(wavedromPlugin.isDefinitionComplete!(
            '{signal: [{name: "clk", wave: "p..."}]}')).toBe(true);
    });
});
