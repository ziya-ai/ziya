/**
 * queueSave and the cross-tab broadcast handler read only the conversations
 * they act on, not the whole IndexedDB store.
 *
 * Before: queueSave's slow path called db.getConversations() (getAll over
 * every project, every message body) whenever React state held a shell --
 * effectively always after the first sync -- and the cross-tab handler did
 * the same in every other tab on every save, ignoring the ids each
 * broadcast carries.
 */
import * as fs from 'fs';
import * as path from 'path';
import { readConversationsByIds } from '../idbTargetedRead';

const conv = (id: string): any => ({ id, title: id, messages: [{ role: 'human', content: 'x' }] });

describe('readConversationsByIds', () => {
    it('reads each requested id once and nothing else', async () => {
        const getConversation = jest.fn(async (id: string) => conv(id));
        const out = await readConversationsByIds({ getConversation }, ['a', 'b', 'a', '', 'c']);
        expect(getConversation.mock.calls.map(c => c[0]).sort()).toEqual(['a', 'b', 'c']);
        expect(out.map(c => c.id).sort()).toEqual(['a', 'b', 'c']);
    });

    it('drops missing records and read failures', async () => {
        const getConversation = jest.fn(async (id: string) => {
            if (id === 'gone') return null;
            if (id === 'bad') throw new Error('tx inactive');
            return conv(id);
        });
        const out = await readConversationsByIds({ getConversation }, new Set(['ok', 'gone', 'bad']));
        expect(out.map(c => c.id)).toEqual(['ok']);
    });

    it('makes no read for an empty id list', async () => {
        const getConversation = jest.fn();
        expect(await readConversationsByIds({ getConversation }, [])).toEqual([]);
        expect(getConversation).not.toHaveBeenCalled();
    });
});

const CHAT = fs.readFileSync(path.join(__dirname, '..', '..', 'context', 'ChatContext.tsx'), 'utf8');

function slice(start: string, end: string): string {
    const s = CHAT.indexOf(start);
    expect(s).toBeGreaterThan(-1);
    const e = CHAT.indexOf(end, s + start.length);
    expect(e).toBeGreaterThan(s);
    return CHAT.slice(s, e);
}

describe('queueSave base read', () => {
    const base = slice('saveQueue.current = saveQueue.current.then(async () => {', '// Build merged list');

    it('reads only the changed ids when changedIds is given', () => {
        const targeted = base.indexOf('if (changedIdSet.size > 0) {\n                allDbConversations = await readConversationsByIds(db, changedIdSet);');
        expect(targeted).toBeGreaterThan(-1);
        // The full read survives only as the no-changedIds fallback, after it.
        const full = base.match(/await db\.getConversations\(\)/g) || [];
        expect(full.length).toBe(1);
        expect(base.indexOf('await db.getConversations()')).toBeGreaterThan(targeted);
    });

    it('no longer keeps the other-project cache, whose branch is unreachable', () => {
        expect(CHAT).not.toMatch(/otherProjectConvsCache/);
    });
});

describe('cross-tab conversations-changed handler', () => {
    const handler = slice('const handleConversationsChanged = async (msg: any) => {', 'const handleFoldersChanged');

    it('reads the ids the broadcast names', () => {
        expect(handler).toMatch(/Array\.isArray\(msg\?\.ids\) \? msg\.ids : \[\]/);
        expect(handler).toMatch(/ids\.length > 0\s*\?\s*await readConversationsByIds\(db, ids\)\s*:\s*await db\.getConversations\(\)/);
    });

    it('every sender still includes ids', () => {
        const src = path.join(__dirname, '..', '..');
        const files = ['context/ChatContext.tsx', 'utils/conversationMutations.ts', 'components/DelegateLaunchButton.tsx'];
        let posts = 0;
        for (const f of files) {
            const text = fs.readFileSync(path.join(src, f), 'utf8');
            for (const m of text.matchAll(/projectSync\.post\('conversations-changed'([^;]*)\)/g)) {
                posts++;
                expect(m[1]).toMatch(/ids:/);
            }
        }
        expect(posts).toBeGreaterThanOrEqual(5);
    });
});
