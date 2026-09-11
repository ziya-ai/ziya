/**
 * The shell-marker decision in utils/conversationShell.ts.
 *
 * WHY THIS SUITE EXISTS
 *
 * A conversation with ZERO messages was being stamped `_isShell: true` and
 * `_fullMessageCount: 0` by getConversationShells.  Nothing had been stripped
 * from it -- the "shell" was byte-identical to the full record -- so the
 * markers described a truncation that never happened.  The cost was not
 * cosmetic, because those markers are a write lock:
 *
 *   1. SHELL_GUARD arms on the very first message, since a _fullMessageCount
 *      of 0 cannot satisfy isKnownCompleteShell's positive-count proof.
 *   2. Recovery finds no IDB body -- marker-bearing records are dropped by
 *      every nonShells IDB write filter, so the row may not exist at all.
 *   3. The server answers 404, because empty conversations are never pushed.
 *   4. recoverShellMessages returns hold/unreachable and the message is
 *      DISCARDED with a toast.
 *
 * Net effect: after a reload, the restored last-active empty conversation
 * could never accept its first message.  Every new-conversation send failed.
 *
 * WHY THE REAL FUNCTION AND NOT A COPY
 *
 * The sibling saveGuardMetadata suite re-implements its subject inline
 * ("extracted logic from db.ts").  That pins a copy: the real function could
 * be reverted and the copy would still pass.  stripToShell was therefore
 * lifted out of getConversationShells into its own module -- the same move
 * already made for shellRecovery.ts -- so this suite can exercise the actual
 * production code.  The last describe block closes the remaining hole by
 * asserting db.ts still delegates to it rather than re-declaring a local one.
 */
import * as fs from 'fs';
import * as path from 'path';
import { stripToShell } from '../conversationShell';
import { isKnownCompleteShell } from '../shellRecovery';

/** The exact condition ChatContext's SHELL_GUARD arms on. */
const guardWouldArm = (conv: any): boolean =>
    !!(conv && conv._isShell && !isKnownCompleteShell(conv));

const msg = (role: string, content: string, i: number) => ({
    id: `m${i}`, role, content, _timestamp: 1000 + i,
});

const convWith = (n: number, extra: Record<string, any> = {}) => ({
    id: 'c1',
    title: 'T',
    _version: 42,
    projectId: 'p1',
    messages: Array.from({ length: n }, (_, i) =>
        msg(i % 2 === 0 ? 'human' : 'assistant', `body-${i}`, i)),
    ...extra,
});

describe('stripToShell — an empty record is not a shell', () => {
    it('does not stamp _isShell on a zero-message conversation', () => {
        const out: any = stripToShell(convWith(0));
        expect(out).not.toBeNull();
        expect(out._isShell).toBeUndefined();
    });

    it('does not stamp _fullMessageCount either', () => {
        // A count of 0 is what defeats isKnownCompleteShell's positive-count
        // proof, so leaving it absent is as important as dropping _isShell.
        const out: any = stripToShell(convWith(0));
        expect(out._fullMessageCount).toBeUndefined();
    });

    it('cannot arm SHELL_GUARD — the actual regression', () => {
        // THE assertion this file exists for.  Before the fix this was true,
        // and a true here means the user's first message is discarded.
        expect(guardWouldArm(stripToShell(convWith(0)))).toBe(false);
    });

    it('leaves the empty message array empty', () => {
        expect(stripToShell(convWith(0))!.messages).toEqual([]);
    });

    it('returns a clone, so cache mutation cannot poison the source', () => {
        const src = convWith(0);
        const out: any = stripToShell(src);
        expect(out).not.toBe(src);
        out.title = 'mutated';
        expect(src.title).toBe('T');
    });

    it('preserves the fields sync and the sidebar read', () => {
        const out: any = stripToShell(convWith(0, { folderId: 'f1', isGlobal: true }));
        expect(out).toMatchObject({
            id: 'c1', title: 'T', _version: 42, projectId: 'p1',
            folderId: 'f1', isGlobal: true,
        });
    });

    it('keeps a real _version rather than pinning it', () => {
        // syncMerge compares this against the server summary.  An absent or
        // zeroed _version changes which side is treated as fresher.
        expect((stripToShell(convWith(0)) as any)._version).toBe(42);
    });
});

describe('stripToShell — a populated record is still stripped', () => {
    it('marks a genuine shell and records the true full count', () => {
        const out: any = stripToShell(convWith(50));
        expect(out._isShell).toBe(true);
        expect(out._fullMessageCount).toBe(50);
    });

    it('keeps only first and last', () => {
        const out: any = stripToShell(convWith(50));
        expect(out.messages).toHaveLength(2);
        expect(out.messages[0].id).toBe('m0');
        expect(out.messages[1].id).toBe('m49');
    });

    it('DOES arm SHELL_GUARD (positive control — fix is not always-off)', () => {
        // If this ever goes false the truncation defense is gone entirely,
        // which is the failure mode the markers were introduced to prevent.
        expect(guardWouldArm(stripToShell(convWith(50)))).toBe(true);
    });

    it('blanks bodies but keeps the fields the sidebar renders', () => {
        const out: any = stripToShell(convWith(3));
        expect(out.messages[0]).toEqual({
            id: 'm0', role: 'human', content: '', _timestamp: 1000,
        });
    });

    it('keeps a single message as one entry, not a duplicated pair', () => {
        const out: any = stripToShell(convWith(1));
        expect(out.messages).toHaveLength(1);
        expect(out._fullMessageCount).toBe(1);
    });

    it('keeps both when there are exactly two', () => {
        const out: any = stripToShell(convWith(2));
        expect(out.messages.map((m: any) => m.id)).toEqual(['m0', 'm1']);
        expect(out._fullMessageCount).toBe(2);
    });

    it('does not mutate the input conversation', () => {
        const src = convWith(3);
        stripToShell(src);
        expect(src.messages).toHaveLength(3);
        expect(src.messages[0].content).toBe('body-0');
        expect((src as any)._isShell).toBeUndefined();
    });

    it('a shell it produces is never a recovery source for itself', () => {
        // recoverShellMessages rejects an IDB record that is itself _isShell,
        // however many entries it holds.  Pinned here because the two-entry
        // array otherwise looks like a plausible body.
        const out: any = stripToShell(convWith(50));
        expect(out._isShell).toBe(true);
        expect(out.messages.length).toBeLessThan(out._fullMessageCount);
    });
});

describe('stripToShell — records too malformed to display', () => {
    it.each([
        ['null', null],
        ['undefined', undefined],
        ['no id', { messages: [] }],
        ['non-string id', { id: 7, messages: [] }],
        ['empty-string id', { id: '', messages: [] }],
        ['messages absent', { id: 'c1' }],
        ['messages not an array', { id: 'c1', messages: 'nope' }],
        ['messages null', { id: 'c1', messages: null }],
    ])('drops %s', (_label, input) => {
        expect(stripToShell(input as any)).toBeNull();
    });
});

describe('stripToShell — null entries inside the array', () => {
    it('yields an empty array when the first entry is null', () => {
        // stripMessage passes a falsy entry straight through, so a naive
        // [firstMsg] would store [null] -- worse downstream than [].
        const out: any = stripToShell({ id: 'c1', messages: [null] });
        expect(out.messages).toEqual([]);
    });

    it('drops a null last entry rather than storing a hole', () => {
        const out: any = stripToShell({
            id: 'c1', messages: [msg('human', 'a', 0), null],
        });
        expect(out.messages).toHaveLength(1);
        expect(out.messages[0].id).toBe('m0');
    });

    it('still reports the true full count when an entry is null', () => {
        const out: any = stripToShell({
            id: 'c1', messages: [msg('human', 'a', 0), null, null],
        });
        expect(out._fullMessageCount).toBe(3);
    });
});

describe('db.ts delegates rather than re-inlining', () => {
    const DB = fs.readFileSync(
        path.resolve(__dirname, '..', 'db.ts'), 'utf8');

    it('imports stripToShell from this module', () => {
        expect(DB).toMatch(
            /import\s*\{[^}]*\bstripToShell\b[^}]*\}\s*from\s*['"]\.\/conversationShell['"]/);
    });

    it('declares no local stripToShell', () => {
        // Closes the hole a copy-based test leaves open: without this, someone
        // could re-add the closure and every assertion above would still pass
        // while production reverted to the broken behaviour.
        expect(DB).not.toMatch(/(?:const|let|var|function)\s+stripToShell\b/);
    });

    it('still calls it (helper imported and then bypassed is the classic regression)', () => {
        expect(DB).toMatch(/stripToShell\s*\(/);
    });
});
