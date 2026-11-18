/**
 * A response that ends without a single data frame must not look like
 * nothing happened.
 *
 * Observed 2026-11-16: the server-side turn died before publishing a frame
 * (an outage raised as a BaseException passed every handler), so POST
 * /api/chat returned 200 with an empty event stream.  The message turned
 * into an orange Retry label with no banner, no toast and no console error.
 * The server paths are fixed (tests/test_infra_error_backstops.py); this is
 * the client's own net for the next cause nobody has thought of.
 *
 * Two layers:
 *   - the predicate, driven directly;
 *   - the seam: chatApi counts data frames after the heartbeat skip and asks
 *     the predicate in the read loop's finally block.  Source-level, like
 *     the other chatApi tests: sendPayload cannot be driven without the
 *     whole streaming stack.
 */
import * as fs from 'fs';
import * as path from 'path';
import { isSilentEmptyStream, SILENT_EMPTY_STREAM_MESSAGE } from '../silentEmptyStream';

const base = {
    dataFrames: 0, content: '', errorShown: false, aborted: false, reattach: false,
};

describe('isSilentEmptyStream', () => {
    it('is true when the stream ended with no data frame at all', () => {
        expect(isSilentEmptyStream(base)).toBe(true);
    });

    it.each([
        ['a frame arrived (done, stream_end, tool, anything)', { dataFrames: 1 }],
        ['content was streamed', { content: 'hi' }],
        ['an error was already shown', { errorShown: true }],
        ['the user stopped the turn', { aborted: true }],
        ['a reattach found nothing left to replay', { reattach: true }],
    ])('is false when %s', (_label, over) => {
        expect(isSilentEmptyStream({ ...base, ...over })).toBe(false);
    });

    it('the message says what to look at', () => {
        expect(SILENT_EMPTY_STREAM_MESSAGE).toMatch(/server log/i);
        expect(SILENT_EMPTY_STREAM_MESSAGE).toMatch(/credentials/i);
    });
});

describe('chatApi wiring', () => {
    const SRC = fs.readFileSync(
        path.resolve(__dirname, '..', '..', 'apis', 'chatApi.ts'), 'utf8');

    it('counts data frames after the heartbeat skip', () => {
        const fn = SRC.indexOf('const processSingleDataMessage = (data: string) => {');
        expect(fn).toBeGreaterThan(-1);
        const body = SRC.slice(fn, fn + 1500);
        const skip = body.indexOf('"type": "heartbeat"');
        const count = body.indexOf('dataFramesSeen++');
        expect(skip).toBeGreaterThan(-1);
        expect(count).toBeGreaterThan(skip);
    });

    it('shows an error when the predicate holds, inside the safety net', () => {
        const net = SRC.indexOf('SAFETY_NET: No content and no error shown');
        expect(net).toBeGreaterThan(-1);
        const after = SRC.slice(net, net + 3500);
        const ask = after.indexOf('isSilentEmptyStream({');
        expect(ask).toBeGreaterThan(-1);
        const call = after.slice(ask, ask + 700);
        expect(call).toContain('dataFrames: dataFramesSeen');
        expect(call).toContain('reattach');
        expect(call).toMatch(/showError\(SILENT_EMPTY_STREAM_MESSAGE, conversationId, addMessageToConversation, 'error'/);
    });
});
