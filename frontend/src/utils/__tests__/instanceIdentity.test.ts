/**
 * @jest-environment jsdom
 *
 * Instance identity: the client half of the "same origin, different server"
 * fix.  Two incidents drove it -- a port reused via an ssh -L tunnel that
 * had half-bound (::1 to the remote, 127.0.0.1 still the local Ziya), and a
 * year-old browser cache resurrecting a project the server no longer had.
 *
 * These tests pin the SEAMS, not the halves: that every origin-scoped name
 * actually carries the id, that a foreign id refuses writes, and that the
 * legacy/no-id server is byte-identical to pre-feature behaviour.
 */
import {
    INSTANCE_HEADER,
    InstanceMismatchError,
    _resetInstanceIdentityForTests,
    assertInstanceNotHalted,
    installFetchLatch,
    instanceIdSync,
    isInstanceHalted,
    observeResponseHeaders,
    onInstanceHalt,
    scopedName,
    scopedNameSync,
    setServerInstanceId,
    whenInstanceKnown,
} from '../instanceIdentity';

// The jest jsdom environment in this tree predates fetch primitives.  Only
// `.get()` (Headers) and `.headers`/`.status` (Response) are used here.
class MiniHeaders {
    private m = new Map<string, string>();
    constructor(init: Record<string, string> = {}) {
        for (const [k, v] of Object.entries(init)) this.m.set(k.toLowerCase(), v);
    }
    get(k: string) { return this.m.get(k.toLowerCase()) ?? null; }
}
class MiniResponse {
    headers: MiniHeaders;
    status: number;
    constructor(_body: any, init: { status?: number; headers?: Record<string, string> } = {}) {
        this.status = init.status ?? 200;
        this.headers = new MiniHeaders(init.headers);
    }
}
if (typeof (globalThis as any).Headers === 'undefined') (globalThis as any).Headers = MiniHeaders;
if (typeof (globalThis as any).Response === 'undefined') (globalThis as any).Response = MiniResponse;

const H = (id?: string) => new Headers(id ? { [INSTANCE_HEADER]: id } : {}) as any;

beforeEach(() => _resetInstanceIdentityForTests());

describe('scoping', () => {
    it('suffixes every name with the instance id', () => {
        expect(scopedName('ZiyaDB', 'abc')).toBe('ZiyaDB__abc');
        expect(scopedName('ziya-project-p1', 'abc')).toBe('ziya-project-p1__abc');
        expect(scopedName('ziya-db-write', 'abc')).toBe('ziya-db-write__abc');
    });

    it('a legacy server (no id) yields the unscoped name -- pre-feature behaviour', () => {
        expect(scopedName('ZiyaDB', null)).toBe('ZiyaDB');
    });

    it('two instances never share a name for the same base + project', () => {
        // This is the BroadcastChannel leak: same projectId, different servers.
        expect(scopedName('ziya-project-p1', 'A')).not.toBe(scopedName('ziya-project-p1', 'B'));
    });

    it('a server WITH an id never resolves to the unowned legacy DB name', () => {
        // No adoption path: a stale ZiyaDB on this origin is invisible to any
        // server that reports an id.
        expect(scopedName('ZiyaDB', 'A')).not.toBe('ZiyaDB');
    });

    it('scopedNameSync refuses to guess before the id is known', () => {
        expect(() => scopedNameSync('ZiyaDB')).toThrow(/not known yet/);
        setServerInstanceId('A');
        expect(scopedNameSync('ZiyaDB')).toBe('ZiyaDB__A');
    });
});

describe('latch', () => {
    it('resolves waiters once, with the first id seen', async () => {
        const p = whenInstanceKnown();
        expect(instanceIdSync()).toBeUndefined();
        observeResponseHeaders(H('A'));
        await expect(p).resolves.toBe('A');
        await expect(whenInstanceKnown()).resolves.toBe('A');
    });

    it('same id again is a no-op', () => {
        observeResponseHeaders(H('A'));
        observeResponseHeaders(H('A'));
        expect(isInstanceHalted()).toBe(false);
    });

    it('a DIFFERENT id halts the tab and notifies listeners', () => {
        const seen: any[] = [];
        onInstanceHalt(h => seen.push(h));
        observeResponseHeaders(H('A'));
        observeResponseHeaders(H('B'));
        expect(isInstanceHalted()).toBe(true);
        expect(seen).toHaveLength(1);
        expect(seen[0]).toMatchObject({ expected: 'A', got: 'B' });
        expect(() => assertInstanceNotHalted('bulk-sync')).toThrow(InstanceMismatchError);
    });

    it('null (legacy startup) never overrides a real id, but a real id after null is a mismatch', () => {
        observeResponseHeaders(H('A'));
        setServerInstanceId(null);           // ProjectContext's explicit call, arriving late
        expect(instanceIdSync()).toBe('A');
        expect(isInstanceHalted()).toBe(false);

        _resetInstanceIdentityForTests();
        setServerInstanceId(null);           // old backend: no header, no field
        expect(instanceIdSync()).toBeNull();
        observeResponseHeaders(H('B'));      // ...and then a real server answers
        expect(isInstanceHalted()).toBe(true);
    });

    it('a response with no header does nothing', () => {
        observeResponseHeaders(H());
        expect(instanceIdSync()).toBeUndefined();
    });
});

describe('fetch latch (the module-wide guarantee)', () => {
    const realFetch = (globalThis as any).fetch;
    let served: string[];
    const mockFetch = (id: string) => jest.fn(async (_i: any, _init?: any) =>
        new Response('{}', { status: 200, headers: { [INSTANCE_HEADER]: id } }));

    afterEach(() => { (globalThis as any).fetch = realFetch; (window as any).fetch = realFetch; });

    it('observes the header on every response and refuses /api writes after a flip', async () => {
        served = [];
        let id = 'A';
        (window as any).fetch = jest.fn(async () => {
            served.push(id);
            return new Response('{}', { status: 200, headers: { [INSTANCE_HEADER]: id } });
        });
        installFetchLatch();

        await window.fetch('/api/v1/projects/startup');
        await window.fetch('/api/v1/projects/p/chats/bulk-sync', { method: 'POST', body: '[]' });
        expect(isInstanceHalted()).toBe(false);
        expect(served).toHaveLength(2);     // writes flow before any mismatch

        id = 'B';                            // the other backend wins a connection
        await window.fetch('/api/v1/projects/p/chats');
        expect(isInstanceHalted()).toBe(true);

        // Positive control that the refusal is the latch and not the mock:
        await expect(
            window.fetch('/api/v1/projects/p/chats/bulk-sync', { method: 'POST', body: '[]' }),
        ).rejects.toBeInstanceOf(InstanceMismatchError);
        await expect(
            window.fetch('/api/v1/projects/p/chats/x', { method: 'DELETE' }),
        ).rejects.toBeInstanceOf(InstanceMismatchError);
        expect(served).toHaveLength(3);     // neither write reached the network

        // Reads still flow so the banner and the UI can render state.
        await window.fetch('/api/v1/projects/p/chats');
        expect(served).toHaveLength(4);
    });

    it('installs once', async () => {
        (window as any).fetch = mockFetch('A');
        installFetchLatch();
        const wrapped = window.fetch;
        installFetchLatch();
        expect(window.fetch).toBe(wrapped);
    });
});
