/**
 * A derived title must never be downgraded back to a placeholder by a
 * merge.  Observed: new conversations stayed "New Conversation" even though
 * the first send derived a title, because a server record re-stamped by a
 * backend writer (tool auto-vivify, bead/handoff/context writes) carried
 * the placeholder with a NEWER _version, and the server-newer merge took
 * `sc.title || local.title`.  Once a human message exists derivation never
 * fires again, so the placeholder was permanent.
 */
import * as fs from 'fs';
import * as path from 'path';
import { mergeTitle } from '../conversationTitle';
import { mergeServerChat, ServerChatSummary, LocalShell, MergeDecisionCtx } from '../syncMerge';

const NOW = 1_750_000_000_000;

describe('mergeTitle', () => {
    it('keeps a real local title over an incoming placeholder', () => {
        expect(mergeTitle('New Conversation', 'why are titles stuck')).toBe('why are titles stuck');
        expect(mergeTitle('', 'real')).toBe('real');
        expect(mergeTitle(undefined, 'real')).toBe('real');
    });

    it('adopts a real incoming title (rename elsewhere)', () => {
        expect(mergeTitle('Renamed', 'old')).toBe('Renamed');
        expect(mergeTitle('Renamed', 'New Conversation')).toBe('Renamed');
    });

    it('both placeholders: incoming wins', () => {
        expect(mergeTitle('New Conversation', 'Loading...')).toBe('New Conversation');
    });
});

describe('mergeServerChat server-newer branch', () => {
    const ctx: MergeDecisionCtx = {
        projectId: 'proj-1', isActiveConv: true, now: NOW, staleShellAgeMs: 600_000,
    };
    const local: LocalShell = {
        id: 'c', title: 'why are titles stuck', messages: [{}, {}],
        lastAccessedAt: NOW - 5_000, _version: NOW - 5_000,
    };

    it('does not replace the derived title with a newer server placeholder', () => {
        const server: ServerChatSummary = {
            id: 'c', title: 'New Conversation', projectId: 'proj-1',
            messageCount: 0, lastActiveAt: NOW, _version: NOW,
        };
        const d = mergeServerChat(server, local, ctx);
        // Positive: the server-newer branch actually ran.
        expect(d.action).toBe('set');
        if (d.action !== 'set') return;
        expect(d.record._version).toBe(NOW);
        expect(d.record.title).toBe('why are titles stuck');
    });

    it('still adopts a real server title', () => {
        const server: ServerChatSummary = {
            id: 'c', title: 'Renamed elsewhere', projectId: 'proj-1',
            messageCount: 2, lastActiveAt: NOW, _version: NOW,
        };
        const d = mergeServerChat(server, local, ctx);
        expect(d.action).toBe('set');
        if (d.action !== 'set') return;
        expect(d.record.title).toBe('Renamed elsewhere');
    });
});

describe('ChatContext wiring', () => {
    const src = fs.readFileSync(
        path.join(__dirname, '..', '..', 'context', 'ChatContext.tsx'), 'utf8');

    it('bulk hydration guards the title with mergeTitle', () => {
        expect(src).toMatch(/\.\.\.full,\s*\n\s*title: mergeTitle\(\(full as any\)\.title, c\.title\)/);
    });

    it('shell recovery derives the title for a first human message', () => {
        const start = src.indexOf('const fresh = toApply.filter');
        expect(start).toBeGreaterThan(0);
        const block = src.slice(start, src.indexOf('}));', start));
        expect(block).toMatch(/shouldDeriveTitleFromMessage\(/);
        expect(block).toMatch(/deriveTitleFromContent\(firstHuman\.content, dynamicTitleLength\)/);
    });
});
