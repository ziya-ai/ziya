/**
 * Regression test: a diagram plugin that REJECTS its spec renders an error
 * card, but the headless render harness stalled for the full 30s safety
 * timeout and then reported a generic message that discarded the plugin's
 * actual diagnosis.
 *
 * ROOT CAUSE
 *
 * Two layers, each individually correct, combined into a 30s stall:
 *
 *   1. railroadPlugin.render() catches a spec error and calls renderError(),
 *      which paints a styled <div> card carrying a specific, actionable
 *      message -- e.g. "unrecognized railroad node (keys: regex); expected
 *      one of: terminal, nonterminal, comment, skip, sequence, choice, ...".
 *      This layer worked correctly.
 *   2. DiagramRenderPage's MutationObserver defines render completion as
 *      "an svg | canvas | img | .vega-embed appeared inside the container".
 *      An error card contains NONE of those, so the observer kept polling
 *      until the safety timeout fired, discarded the plugin's message, and
 *      reported `Render timeout after 30000ms (type=railroad). DOM snapshot:
 *      {"svg":0,"canvas":0,"img":0,...}` instead.
 *
 * Observed: passing `{"regex": "..."}` as a node type cost 30s of wall clock
 * and returned no hint that the node KEY was the problem. The plugin knew
 * exactly what was wrong; the harness threw that knowledge away.
 *
 * This is not railroad-specific -- musicPlugin, packetPlugin, wavedromPlugin
 * and mermaidPlugin all paint the same shape of card and stalled identically.
 * The fix is therefore in the shared contract, not in one plugin's parser.
 *
 * FIX
 *
 * renderError() tags the card with `data-diagram-error="<message>"`, set via
 * setAttribute and NOT string-interpolated into the innerHTML template: the
 * message embeds model-authored JSON keys, which would need attribute-level
 * quote escaping to be injection-safe inside a quoted attribute value. The
 * observer checks for `[data-diagram-error]` before its svg/canvas/img poll
 * and fails fast, surfacing the plugin's message verbatim.
 *
 * NON-VACUITY: against the pre-fix source the attribute does not exist, so
 * the first assertion fails. The valid-spec case pins that a good render does
 * NOT carry the marker, so the fix cannot be satisfied by tagging everything.
 */
import { railroadPlugin } from '../railroadPlugin';

/** The exact selector DiagramRenderPage's observer polls for. */
const ERROR_CARD_SELECTOR = '[data-diagram-error]';

function renderInto(definition: string): HTMLElement {
    const container = document.createElement('div');
    (railroadPlugin as any).render(
        container, null, { type: 'railroad', definition }, false);
    return container;
}

describe('railroad error card — fail-fast marker', () => {
    it('tags an unrecognized node type with the plugin message', () => {
        const c = renderInto('{"diagram": {"regex": "[0-9]+"}}');
        const card = c.querySelector(ERROR_CARD_SELECTOR);
        expect(card).not.toBeNull();
        const msg = card!.getAttribute('data-diagram-error') || '';
        expect(msg).toContain('unrecognized railroad node');
        expect(msg).toContain('regex');     // names the offending key
        expect(msg).toContain('terminal');  // and the accepted vocabulary
    });

    it('tags malformed JSON too', () => {
        const c = renderInto('{"diagram": {"terminal"');
        const card = c.querySelector(ERROR_CARD_SELECTOR);
        expect(card).not.toBeNull();
        expect(card!.getAttribute('data-diagram-error')).toContain('JSON');
    });

    it('renders no marker for a VALID spec (guard: not a catch-all)', () => {
        const c = renderInto('{"diagram": {"terminal": "@@"}}');
        expect(c.querySelector(ERROR_CARD_SELECTOR)).toBeNull();
        expect(c.querySelector('svg')).not.toBeNull();
    });

    it('error card lacks every element the observer waits for', () => {
        // The property that made the stall possible. Documents WHY the marker
        // is needed, rather than asserting the old behaviour is merely gone.
        const c = renderInto('{"diagram": {"regex": "x"}}');
        expect(c.querySelector('svg')).toBeNull();
        expect(c.querySelector('canvas')).toBeNull();
        expect(c.querySelector('img')).toBeNull();
    });

    it('marker survives a message containing a double quote', () => {
        // setAttribute (not template interpolation) is what makes this safe:
        // the offending key is echoed into the message verbatim, so a naive
        // `data-diagram-error="${message}"` would break out of the attribute.
        const c = renderInto('{"diagram": {"we\\"ird": 1}}');
        const card = c.querySelector(ERROR_CARD_SELECTOR);
        expect(card).not.toBeNull();
        expect(card!.getAttribute('data-diagram-error')).toContain('we"ird');
    });
});
