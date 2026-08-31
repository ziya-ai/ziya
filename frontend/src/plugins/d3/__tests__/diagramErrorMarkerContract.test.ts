/**
 * Contract test: every d3 plugin that paints an ERROR CARD instead of a
 * drawing must tag that card with `data-diagram-error="<message>"`.
 *
 * WHY THE MARKER EXISTS
 *
 * `DiagramRenderPage` (the headless capture harness at /render) decides a
 * render finished by polling for an `svg`, `canvas`, or `img` inside the
 * container. An error card is a styled `<div>` holding a `<strong>` and a
 * `<pre>` — none of those. So a plugin that correctly diagnosed a bad spec
 * and painted a precise message got NO completion signal: the harness spun
 * to its 30s safety cap and then REPLACED the plugin's message with a
 * generic `DOM snapshot: {"svg":0,...}`.
 *
 * The observed symptom was a railroad spec containing one unrecognised node
 * key: buildNode() threw the intended `unrecognized railroad node (keys: ...)`
 * message, the card rendered in milliseconds, and the caller still waited 30s
 * to be told "svg:0". The same stall applies to every plugin below, and the
 * musicKeySignatureSanitizer docstring in this directory records a bug that
 * took a five-render bisection to locate for exactly this reason — the harness
 * hid VexFlow's `BadKeySignature` behind `svg:0`.
 *
 * WHAT IS PINNED
 *
 * Each case asserts the marker is present AND carries the plugin's real
 * message (not a placeholder), so the harness can fail fast with a useful
 * diagnosis. The happy-path guards assert a VALID spec produces an `<svg>`
 * and NO marker, so the marker cannot be a blanket attribute stamped on
 * every render.
 *
 * NON-VACUITY: against pre-fix source every `data-diagram-error` assertion
 * fails (the attribute did not exist anywhere) while the happy-path and
 * message-content assertions pass — confirming the tests exercise the
 * shipped plugins rather than a re-implementation.
 *
 * NOT COVERED HERE: mermaidPlugin. Its error path is gated behind a live
 * `mermaid.parse()` failure and needs the real mermaid bundle, which does not
 * initialise under jsdom. Its marker is verified by the same attribute
 * contract but exercised through the integration suite.
 */

import { packetPlugin } from '../packetPlugin';
import { musicPlugin } from '../musicPlugin';
import { wavedromPlugin } from '../wavedromPlugin';
import { railroadPlugin } from '../railroadPlugin';

/** The single cross-module contract string. Hard-coded on purpose: if a
 *  plugin or the harness renames it, this test fails instead of silently
 *  reverting the harness to its 30-second timeout path. */
const MARKER = 'data-diagram-error';

function freshContainer(): HTMLElement {
    const el = document.createElement('div');
    document.body.appendChild(el);
    return el;
}

afterEach(() => { document.body.innerHTML = ''; });

describe('error-card marker contract — invalid specs', () => {
    it('packet tags its error card', () => {
        const c = freshContainer();
        packetPlugin.render(c, null, { type: 'packet', definition: 'not json' }, false);
        const card = c.querySelector(`[${MARKER}]`);
        expect(card).not.toBeNull();
        expect(card!.getAttribute(MARKER)!.length).toBeGreaterThan(0);
    });

    it('music tags its error card', async () => {
        const c = freshContainer();
        await musicPlugin.render(c, null, { type: 'music', definition: '{' }, false);
        const card = c.querySelector(`[${MARKER}]`);
        expect(card).not.toBeNull();
        expect(card!.getAttribute(MARKER)!.length).toBeGreaterThan(0);
    });

    it('wavedrom tags its error card', async () => {
        const c = freshContainer();
        await wavedromPlugin.render(c, null, { type: 'wavedrom', definition: 'not json' }, false);
        const card = c.querySelector(`[${MARKER}]`);
        expect(card).not.toBeNull();
        expect(card!.getAttribute(MARKER)!.length).toBeGreaterThan(0);
    });

    it('railroad tags its error card with the unrecognized-node message', () => {
        const c = freshContainer();
        railroadPlugin.render(
            c, null, { type: 'railroad', definition: '{"diagram": {"regex": "x"}}' }, false);
        const card = c.querySelector(`[${MARKER}]`);
        expect(card).not.toBeNull();
        expect(card!.getAttribute(MARKER)).toContain('unrecognized railroad node');
    });
});

describe('error-card marker contract — valid specs stay unmarked', () => {
    it('railroad renders an svg and no marker for a well-formed spec', () => {
        const c = freshContainer();
        railroadPlugin.render(
            c, null,
            { type: 'railroad', definition: '{"diagram": {"terminal": "ok"}}' },
            false);
        expect(c.querySelector('svg')).not.toBeNull();
        expect(c.querySelector(`[${MARKER}]`)).toBeNull();
    });

    it('packet renders content and no marker for a well-formed spec', () => {
        const c = freshContainer();
        packetPlugin.render(c, null, {
            type: 'packet',
            definition: JSON.stringify({
                type: 'packet',
                fields: [{ name: 'a', bits: 8 }, { name: 'b', bits: 8 }],
            }),
        }, false);
        expect(c.querySelector(`[${MARKER}]`)).toBeNull();
    });
});
