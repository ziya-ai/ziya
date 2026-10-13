/**
 * Bulk hydration must not replace an in-memory message list with a shorter
 * server copy unless the server's tombstones explain the shrink.
 *
 * Reported loop (conv eb3fe253): the assistant reply streamed to the browser,
 * then handoff_write re-stamped the server record's _version while it still
 * held only the human turn.  Each 30 s sync, bulk hydration adopted the
 * newer 43-message server copy (the only check was "local _version newer?"),
 * the reply vanished from screen, POST_SYNC restored it from IndexedDB, and
 * the push was rejected as older -- forever.  The server half is fixed in
 * bulk_sync_chats (tests/test_bulk_sync_stale_version_append.py); this is
 * the browser half, so a future server-side shrink cannot recreate the loop.
 *
 * DIRECTION: against the unpatched code applyHydratedChat and
 * hydrationWouldDropMessages do not exist and ChatContext adopts
 * full.messages unconditionally.
 */
import * as fs from 'fs';
import * as path from 'path';
import { applyHydratedChat, hydrationWouldDropMessages, TOMBSTONE_FIELD } from '../syncMerge';

const H1 = { id: 'h1', role: 'human', content: 'first question' };
const A1 = { id: 'a1', role: 'assistant', content: 'first answer' };
const H2 = { id: 'h2', role: 'human', content: 'second question' };
const A2 = { id: 'a2', role: 'assistant', content: 'second answer, only in the browser' };

const ids = (r: any) => (r.messages || []).map((m: any) => m.id);

function local(extra: any = {}) {
    return { id: 'c1', title: 'Real title', messages: [H1, A1, H2, A2], _version: 1000, folderId: null, ...extra };
}
function server(extra: any = {}) {
    return { id: 'c1', title: 'Real title', messages: [H1, A1, H2], _version: 2000, groupId: 'g9', ...extra };
}

describe('applyHydratedChat — the reported case', () => {
    it('keeps the local reply when a newer server copy lacks it and has no tombstones', () => {
        const out = applyHydratedChat(local(), server(), 'p1');
        expect(ids(out)).toEqual(['h1', 'a1', 'h2', 'a2']);
    });

    it("adopts the server's _version and metadata while keeping the messages", () => {
        // Equal versions make the push filter's msg-count rule fire (local > server)
        // instead of serverShrankPastLocal suppressing the push.
        const out = applyHydratedChat(local(), server({ title: 'Renamed' }), 'p1');
        expect(out._version).toBe(2000);
        expect(out.title).toBe('Renamed');
        expect(out.folderId).toBe('g9');
        expect(out._isShell).toBe(false);
    });
});

describe('applyHydratedChat — server copy still wins when it should', () => {
    it('adopts the shorter server list when its tombstones cover every missing message', () => {
        const srv = server({ [TOMBSTONE_FIELD]: { ids: ['a2'] } });
        expect(ids(applyHydratedChat(local(), srv, 'p1'))).toEqual(['h1', 'a1', 'h2']);
    });

    it('keeps local when tombstones explain only part of the shrink', () => {
        const srv = server({ messages: [H1, A1], [TOMBSTONE_FIELD]: { ids: ['a2'] } });
        expect(ids(applyHydratedChat(local(), srv, 'p1'))).toEqual(['h1', 'a1', 'h2', 'a2']);
    });

    it('adopts the server list when it is a superset', () => {
        const extra = { id: 'h3', role: 'human', content: 'from another tab' };
        const srv = server({ messages: [H1, A1, H2, A2, extra] });
        expect(ids(applyHydratedChat(local(), srv, 'p1'))).toEqual(['h1', 'a1', 'h2', 'a2', 'h3']);
    });

    it('adopts the server list when the local record is only a shell', () => {
        const shell = local({ _isShell: true, messages: [] });
        expect(ids(applyHydratedChat(shell, server(), 'p1'))).toEqual(['h1', 'a1', 'h2']);
    });

    it('returns the local record untouched when it is newer', () => {
        const l = local({ _version: 3000 });
        expect(applyHydratedChat(l, server(), 'p1')).toBe(l);
    });
});

describe('hydrationWouldDropMessages', () => {
    it('is true only for an unexplained loss', () => {
        expect(hydrationWouldDropMessages(local(), server())).toBe(true);
        expect(hydrationWouldDropMessages(local(), server({ [TOMBSTONE_FIELD]: { ids: ['a2'] } }))).toBe(false);
        expect(hydrationWouldDropMessages(local({ messages: [H1, A1, H2] }), server())).toBe(false);
        expect(hydrationWouldDropMessages(local({ _isShell: true }), server())).toBe(false);
    });
});

describe('seam: ChatContext bulk hydration goes through applyHydratedChat', () => {
    const src = fs.readFileSync(path.join(__dirname, '../../context/ChatContext.tsx'), 'utf8');
    const start = src.indexOf('// Apply each returned chat.');
    const end = src.indexOf('// Mark each returned chat as fetched.', start);
    const block = src.slice(start, end);

    it('finds the hydration block', () => {
        expect(start).toBeGreaterThan(-1);
        expect(end).toBeGreaterThan(start);
    });

    it('commits the result of syncMerge.applyHydratedChat', () => {
        expect(block).toContain('syncMerge.applyHydratedChat(');
    });

    it('no longer spreads the server record inline', () => {
        expect(block).not.toMatch(/\.\.\.full\s*,/);
    });
});
