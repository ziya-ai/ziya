/**
 * The periodic sync's push filter sends any local conversation with MORE
 * messages than the server reports, regardless of version (the
 * "msg-count divergence — pushing" rule).  That rule exists for a local copy
 * that genuinely ran ahead of the server with a coincident _version.
 *
 * It also fired for the opposite situation.  A server record only shrinks
 * through an explicit rewind/delete, and every such op bumps the server
 * _version; bulk-sync never removes a message.  So when the server is NEWER
 * and SHORTER than local, local's extra messages were removed on the server
 * and local is stale, not ahead.  Pushing then re-sends tombstoned messages,
 * bulk_sync_chats drops them and (because the merge differs from what the
 * client sent) bumps _version again, which re-arms the same rule on the next
 * cycle:
 *     📡 SERVER_SYNC: msg-count divergence (local=18 server=13) — pushing
 *     📡 SERVER_SYNC: Push complete — created=0 updated=1 skipped=1
 * every 30 s for conv e04a626a.  The pull side (shouldFetchFull's
 * serverShrank) already owns that reconciliation.
 *
 * DIRECTION: against the unpatched code `serverShrankPastLocal` does not
 * exist and the filter pushes on any count divergence.
 */
import * as fs from 'fs';
import * as path from 'path';
import { serverShrankPastLocal } from '../syncMerge';

describe('serverShrankPastLocal', () => {
    it('true when the server is newer AND shorter: local extras were removed there', () => {
        expect(serverShrankPastLocal({ localMsgCount: 18, serverMsgCount: 13, localVer: 100, serverVer: 101 })).toBe(true);
    });

    it('false on the Rahul-Patil shape: same version, local far ahead (must still push)', () => {
        expect(serverShrankPastLocal({ localMsgCount: 46, serverMsgCount: 2, localVer: 100, serverVer: 100 })).toBe(false);
    });

    it('false when local is newer: a local append the server has not seen', () => {
        expect(serverShrankPastLocal({ localMsgCount: 14, serverMsgCount: 13, localVer: 102, serverVer: 101 })).toBe(false);
    });

    it('false when the server is not shorter, whatever the versions', () => {
        expect(serverShrankPastLocal({ localMsgCount: 13, serverMsgCount: 13, localVer: 100, serverVer: 101 })).toBe(false);
        expect(serverShrankPastLocal({ localMsgCount: 12, serverMsgCount: 13, localVer: 100, serverVer: 101 })).toBe(false);
    });
});

describe('seam — the push filter consults serverShrankPastLocal before the count rule', () => {
    const src = fs.readFileSync(path.resolve(__dirname, '..', '..', 'context', 'ChatContext.tsx'), 'utf8');
    const start = src.indexOf('const pushCandidateIds = mergedProjectConvs');
    const end = src.indexOf('.map(c => c.id);', start);

    it('inside the pushCandidateIds filter', () => {
        expect(start).toBeGreaterThan(-1);
        expect(end).toBeGreaterThan(start);
        const body = src.slice(start, end);
        const guard = body.indexOf('syncMerge.serverShrankPastLocal(');
        const pushLog = body.indexOf('msg-count divergence for');
        expect(guard).toBeGreaterThan(-1);
        expect(pushLog).toBeGreaterThan(-1);
        // The skip must be decided before the push log is reached.
        expect(guard).toBeLessThan(pushLog);
    });
});
