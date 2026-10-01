/**
 * Seam test: two tabs on the SAME origin and SAME project id but talking to
 * DIFFERENT Ziya installations must not hear each other's broadcasts.
 *
 * This is the mechanism most consistent with the 7123 incident: a tab on a
 * half-bound tunnel port resolved a project id from the local server, joined
 * `ziya-project-<id>`, and its 'conversation-deleted' / 'conversations-changed'
 * posts landed directly in the other tab's handlers -- no server, no IDB.
 */
// jsdom has no BroadcastChannel; a name-keyed fake is exactly the semantics
// under test (same name => shared bus, different name => isolated).
class FakeBC {
    static buses = new Map<string, Set<FakeBC>>();
    onmessage: ((ev: { data: any }) => void) | null = null;
    constructor(public readonly name: string) {
        if (!FakeBC.buses.has(name)) FakeBC.buses.set(name, new Set());
        FakeBC.buses.get(name)!.add(this);
    }
    postMessage(data: any) {
        for (const peer of FakeBC.buses.get(this.name)!) {
            if (peer !== this) peer.onmessage?.({ data });
        }
    }
    close() { FakeBC.buses.get(this.name)?.delete(this); }
}

// Fresh module graph per "tab": its own TAB_ID, its own projectSync singleton
// AND its own instanceIdentity state -- exactly as a real browser tab has.
// `instanceId` undefined leaves the id unknown (join stays pending).
function loadTab(instanceId?: string | null) {
    let sync: typeof import('../projectSync')['projectSync'];
    let ident: typeof import('../instanceIdentity');
    jest.isolateModules(() => {
        ident = require('../instanceIdentity');
        sync = require('../projectSync').projectSync;
    });
    if (instanceId !== undefined) ident!.setServerInstanceId(instanceId);
    return { sync: sync!, setId: (id: string | null) => ident!.setServerInstanceId(id) };
}

beforeEach(() => {
    (globalThis as any).BroadcastChannel = FakeBC;
    FakeBC.buses.clear();
});

const flush = () => new Promise(r => setTimeout(r, 0));

describe('projectSync channel is scoped by server instance', () => {
    it('same project, same instance: tabs hear each other (control)', async () => {
        const t1 = loadTab('A'); const t2 = loadTab('A');
        t1.sync.join('proj-1'); t2.sync.join('proj-1');
        await flush();
        const got: any[] = [];
        t2.sync.on('conversation-deleted', m => got.push(m));
        t1.sync.post('conversation-deleted', { id: 'c1' });
        expect(got).toHaveLength(1);
        expect(Array.from(FakeBC.buses.keys())).toEqual(['ziya-project-proj-1__A']);
    });

    it('same project, DIFFERENT instances: a delete never crosses', async () => {
        // Tab on the local server vs. tab on the tunnelled port that resolved
        // the same project id.  The channel NAME is what must differ.
        const local = loadTab('A');
        local.sync.join('proj-1');
        const tunnelled = loadTab('B');
        tunnelled.sync.join('proj-1');
        await flush();

        const got: any[] = [];
        local.sync.on('conversation-deleted', m => got.push(m));
        local.sync.on('conversations-changed', m => got.push(m));
        tunnelled.sync.post('conversation-deleted', { id: 'c1' });
        tunnelled.sync.post('conversations-changed', { ids: ['c1'] });
        expect(got).toEqual([]);
        expect(Array.from(FakeBC.buses.keys()).sort())
            .toEqual(['ziya-project-proj-1__A', 'ziya-project-proj-1__B']);
    });

    it('legacy server (no id) keeps the pre-feature channel name', async () => {
        const t = loadTab(null);
        t.sync.join('proj-1');
        await flush();
        expect(Array.from(FakeBC.buses.keys())).toEqual(['ziya-project-proj-1']);
    });

    it('a join that is superseded before the id is known opens nothing for the old project', async () => {
        const t = loadTab();
        t.sync.join('proj-old');          // id not yet known: pending
        t.sync.join('proj-new');          // user switched project meanwhile
        t.setId('A');
        await flush();
        expect(Array.from(FakeBC.buses.keys())).toEqual(['ziya-project-proj-new__A']);
        expect(t.sync.currentProjectId).toBe('proj-new');
    });

    it('leave() before the id is known cancels the pending join', async () => {
        const t = loadTab();
        t.sync.join('proj-1');
        t.sync.leave();
        t.setId('A');
        await flush();
        expect(Array.from(FakeBC.buses.keys())).toEqual([]);
    });
});
