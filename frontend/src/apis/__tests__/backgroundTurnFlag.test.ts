/**
 * runBackgroundTurn must mark its request `background: true`, and an ordinary
 * send must not.  On that flag the server runs the turn beside the
 * conversation's own instead of replacing it (app/utils/background_turn.py;
 * the server half is tests/test_background_turn.py).  Without it the handoff
 * drawer's draft request cancels a turn held for its diffs to be applied.
 *
 * getApiResponse is not exported and chatApi.ts is not importable under jest
 * in isolation, so, as chatApiConnectionError.test.ts does, this is a source
 * contract scoped to the two functions by name.
 */
import * as fs from 'fs';
import * as path from 'path';

const SRC = fs.readFileSync(path.join(__dirname, '..', 'chatApi.ts'), 'utf-8');

function body(startMarker: string, endMarker: string): string {
    const start = SRC.indexOf(startMarker);
    expect(start).toBeGreaterThan(-1);
    const end = SRC.indexOf(endMarker, start + startMarker.length);
    expect(end).toBeGreaterThan(start);
    return SRC.slice(start, end);
}

const runBackgroundTurn = () => body('export async function runBackgroundTurn(', '\nasync function getApiResponse(');
const getApiResponse = () => body('\nasync function getApiResponse(', "return fetch('/api/chat'");

describe('background turn flag', () => {
    it('getApiResponse takes the flag and puts it in the payload only when set', () => {
        const fn = getApiResponse();
        expect(fn.slice(0, fn.indexOf(') {'))).toMatch(/\bbackground\?: boolean$/);
        const payload = fn.slice(fn.indexOf('const payload = {'));
        expect(payload).toContain('...(background ? { background: true } : {})');
    });

    it('runBackgroundTurn passes it', () => {
        const call = runBackgroundTurn();
        const args = call.slice(call.indexOf('getApiResponse('), call.indexOf(');'));
        expect(args).toMatch(/opts\.resolvedModelPin,\s*true,?\s*$/);
    });

    it('no other caller of getApiResponse passes it', () => {
        const calls = SRC.match(/getApiResponse\([^;]*?\);/g) ?? [];
        expect(calls.length).toBeGreaterThanOrEqual(3);
        const flagged = calls.filter(c => /resolvedModelPin,\s*true/.test(c));
        expect(flagged.length).toBe(1);
    });
});
