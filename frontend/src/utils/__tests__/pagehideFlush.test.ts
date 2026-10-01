/**
 * @jest-environment jsdom
 *
 * pagehide flush: the dual-write debounce's pending set, sent with keepalive
 * when the page goes away.  The property that matters is that it can never
 * push anything the 2s timer would not have pushed -- same set, same filter,
 * same endpoint -- so the tests are mostly about what is NOT sent.
 */
import * as fs from 'fs';
import * as path from 'path';
import {
    KEEPALIVE_BODY_LIMIT,
    flushPendingDirtyOnPagehide,
    selectDirtyConversations,
} from '../pagehideFlush';
import { _resetInstanceIdentityForTests, setServerInstanceId } from '../instanceIdentity';

const conv = (over: Partial<any> = {}) => ({
    id: 'c1', title: 'Real title', isActive: true, _isShell: false,
    messages: [{ role: 'human', content: 'hi' }], projectId: 'p1', ...over,
});

const toServerChat = (c: any, pid: string) => ({ id: c.id, projectId: pid, messages: c.messages });

beforeEach(() => {
    _resetInstanceIdentityForTests();
    (window as any).__ZIYA_CURRENT_PROJECT_PATH__ = '/proj/root';
});

describe('selectDirtyConversations (the shared filter)', () => {
    it('keeps only batched, active, non-shell, non-empty conversations', () => {
        const all = [
            conv({ id: 'keep' }),
            conv({ id: 'not-batched' }),
            conv({ id: 'inactive', isActive: false }),
            conv({ id: 'shell', _isShell: true }),
            conv({ id: 'empty-new', title: 'New Conversation', messages: [] }),
            conv({ id: 'empty-titled', title: 'Has a title', messages: [] }), // kept: title is real
        ];
        const batch = new Set(['keep', 'inactive', 'shell', 'empty-new', 'empty-titled']);
        expect(selectDirtyConversations(all, batch).map(c => c.id))
            .toEqual(['keep', 'empty-titled']);
    });
});

describe('flushPendingDirtyOnPagehide', () => {
    it('sends exactly the pending, filtered set with keepalive and the project header', () => {
        // No `Response` in this jest env; the flush only needs a thenable back.
        const fetchFn = jest.fn(() => Promise.resolve({} as any));
        const pending = new Set(['c1', 'shell']);
        const out = flushPendingDirtyOnPagehide({
            conversations: [conv(), conv({ id: 'shell', _isShell: true }), conv({ id: 'untouched' })],
            pendingIds: pending, fallbackProjectId: 'p1', toServerChat, fetchFn: fetchFn as any,
        });
        expect(out).toEqual({ kind: 'sent', projects: 1, conversations: 1 });
        expect(fetchFn).toHaveBeenCalledTimes(1);
        const [url, init] = fetchFn.mock.calls[0] as unknown as [string, RequestInit];
        expect(url).toBe('/api/v1/projects/p1/chats/bulk-sync');
        expect(init.method).toBe('POST');
        expect(init.keepalive).toBe(true);
        expect((init.headers as any)['X-Project-Root']).toBe('/proj/root');
        const body = JSON.parse(init.body as string);
        expect(body.chats.map((c: any) => c.id)).toEqual(['c1']);   // shell filtered, untouched not batched
        expect(pending.size).toBe(0);                                 // consumed
    });

    it('a stale record the debounce would have dropped produces ZERO requests', () => {
        // The anchor: an empty "New Conversation" and an inactive record are
        // in the pending set (they were written to IDB), but the debounce
        // filter drops them -- so must we.
        const fetchFn = jest.fn();
        const pending = new Set(['empty', 'gone']);
        const out = flushPendingDirtyOnPagehide({
            conversations: [
                conv({ id: 'empty', title: 'New Conversation', messages: [] }),
                conv({ id: 'gone', isActive: false }),
            ],
            pendingIds: pending, fallbackProjectId: 'p1', toServerChat, fetchFn,
        });
        expect(out).toEqual({ kind: 'nothing' });
        expect(fetchFn).not.toHaveBeenCalled();
        expect(pending.size).toBe(0);
    });

    it('nothing pending / no project => no request', () => {
        const fetchFn = jest.fn();
        expect(flushPendingDirtyOnPagehide({
            conversations: [conv()], pendingIds: new Set(), fallbackProjectId: 'p1', toServerChat, fetchFn,
        })).toEqual({ kind: 'nothing' });
        expect(flushPendingDirtyOnPagehide({
            conversations: [conv()], pendingIds: new Set(['c1']), fallbackProjectId: null, toServerChat, fetchFn,
        })).toEqual({ kind: 'nothing' });
        expect(fetchFn).not.toHaveBeenCalled();
    });

    it('groups by per-conversation projectId, falling back to the tab project', () => {
        const fetchFn = jest.fn(() => Promise.resolve({} as any));
        flushPendingDirtyOnPagehide({
            conversations: [conv({ id: 'a', projectId: 'p1' }), conv({ id: 'b', projectId: undefined })],
            pendingIds: new Set(['a', 'b']), fallbackProjectId: 'ptab', toServerChat, fetchFn: fetchFn as any,
        });
        const urls = fetchFn.mock.calls.map(c => (c as unknown as [string])[0]).sort();
        expect(urls).toEqual(['/api/v1/projects/p1/chats/bulk-sync', '/api/v1/projects/ptab/chats/bulk-sync']);
    });

    it('refuses to send when the instance latch has halted the tab', () => {
        setServerInstanceId('A');
        setServerInstanceId('B'); // mismatch -> halted
        const fetchFn = jest.fn();
        const pending = new Set(['c1']);
        expect(flushPendingDirtyOnPagehide({
            conversations: [conv()], pendingIds: pending, fallbackProjectId: 'p1', toServerChat, fetchFn,
        })).toEqual({ kind: 'halted' });
        expect(fetchFn).not.toHaveBeenCalled();
        expect(pending.size).toBe(1); // left pending, not consumed
    });

    it('over the keepalive limit: not sent, reported honestly, ids left pending', () => {
        const fetchFn = jest.fn();
        const big = conv({ messages: [{ role: 'human', content: 'x'.repeat(KEEPALIVE_BODY_LIMIT) }] });
        const pending = new Set(['c1']);
        const out = flushPendingDirtyOnPagehide({
            conversations: [big], pendingIds: pending, fallbackProjectId: 'p1', toServerChat, fetchFn,
        });
        expect(out.kind).toBe('too-large');
        expect(fetchFn).not.toHaveBeenCalled();
        expect(pending.size).toBe(1);
    });

    it('never throws even if fetch does', () => {
        const fetchFn = jest.fn(() => { throw new Error('boom'); });
        expect(() => flushPendingDirtyOnPagehide({
            conversations: [conv()], pendingIds: new Set(['c1']), fallbackProjectId: 'p1', toServerChat, fetchFn,
        })).not.toThrow();
    });
});

describe('seam: ChatContext uses the shared filter and registers pagehide', () => {
    const src = fs.readFileSync(path.resolve(__dirname, '..', '..', 'context', 'ChatContext.tsx'), 'utf8');

    it('both debounce timers call selectDirtyConversations (no inline copy of the filter)', () => {
        expect((src.match(/selectDirtyConversations\(/g) || []).length).toBeGreaterThanOrEqual(2);
        // The inline shape must be gone, or the two could drift again.
        expect(src).not.toMatch(/batchIds\.has\(c\.id\) && c\.isActive !== false && !c\._isShell/);
    });

    it('registers a pagehide listener that calls the flush', () => {
        expect(src).toMatch(/addEventListener\('pagehide'/);
        expect(src).toMatch(/flushPendingDirtyOnPagehide\(\{/);
    });
});
