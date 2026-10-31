/**
 * groupToolTokens (utils/toolGroupTokens.ts): the members of a parallel
 * tool batch, bracketed by TOOL_GROUP_START/END comments, become one
 * synthetic 'tool_group' token that renderTokens draws as a <ToolGroup>.
 * Tokens are hand-built: marked is ESM-only and stubbed under jest.
 */
import { groupMemberProgress, groupToolTokens } from '../toolGroupTokens';

const html = (text: string) => ({ type: 'html', text: text + '\n', raw: text + '\n' });
const start = (id: string, size: number) => html(`<!-- TOOL_GROUP_START:${id}|${size} -->`);
const end = (id: string, size: number, wall = 2400, serial = 6300) =>
    html(`<!-- TOOL_GROUP_END:${id}|${size}|${wall}|${serial} -->`);
const tool = (name: string, body: string) => ({ type: 'code', lang: `tool:mcp_${name}|⫽ ${name}|text`, text: body, raw: body });
const para = (t: string) => ({ type: 'paragraph', text: t, raw: t, tokens: [] });
const running = (n: string) => tool(n, '⏳ Running...');
const done = (n: string) => tool(n, 'ok');

describe('groupToolTokens', () => {
    it('returns the same array when there are no markers', () => {
        const toks = [para('a'), done('x')];
        expect(groupToolTokens(toks)).toBe(toks);
    });

    it('collects the members of a closed group and keeps what surrounds it', () => {
        const a = done('a'), b = done('b');
        const out: any[] = groupToolTokens([para('before'), start('g1', 2), a, b, end('g1', 2), para('after')]);
        expect(out.map(t => t.type)).toEqual(['paragraph', 'tool_group', 'paragraph']);
        expect(out[1]).toMatchObject({ groupId: 'g1', size: 2, text: 'tool_group:g1' });
        expect(out[1].end).toEqual({ group_id: 'g1', size: 2, wall_ms: 2400, serial_ms: 6300 });
        expect(out[1].tokens).toEqual([a, b]);           // markers excluded
    });

    it('an unclosed group (still running) takes the rest of the message', () => {
        const out: any[] = groupToolTokens([para('x'), start('g1', 3), running('a'), done('b')]);
        expect(out.map(t => t.type)).toEqual(['paragraph', 'tool_group']);
        expect(out[1].end).toBeNull();
        expect(out[1].tokens).toHaveLength(2);
    });

    it('a new START before the END closes the previous group as unfinished', () => {
        const out: any[] = groupToolTokens([start('g1', 2), done('a'), start('g2', 2), done('b'), done('c'), end('g2', 2)]);
        expect(out.map(t => [t.groupId, t.end ? 'closed' : 'open', t.tokens.length])).toEqual([
            ['g1', 'open', 1], ['g2', 'closed', 2],
        ]);
    });

    it('drops an orphan END outside any group', () => {
        const out: any[] = groupToolTokens([start('g1', 2), done('a'), end('g1', 2), end('zz', 2), para('p')]);
        expect(out.map(t => t.type)).toEqual(['tool_group', 'paragraph']);
    });

    // A group whose END never arrived (the relay dropped tool_group_end, or a
    // message recorded while it did) must not swallow the rest of the
    // message.  Once `size` members are in, the group is complete.
    it('an unclosed group with all its members closes there, not at message end', () => {
        const a = done('a'), b = done('b');
        const out: any[] = groupToolTokens([start('g1', 2), a, b, para('after'), done('c')]);
        expect(out.map(t => t.type)).toEqual(['tool_group', 'paragraph', 'code']);
        expect(out[0].tokens).toEqual([a, b]);
        expect(out[0].end).toBeNull();
    });

    it('an END separated from the last member by space tokens is still consumed', () => {
        const sp = { type: 'space', raw: '\n\n' };
        const out: any[] = groupToolTokens([start('g1', 2), done('a'), done('b'), sp, end('g1', 2), para('after')]);
        expect(out.map(t => t.type)).toEqual(['tool_group', 'paragraph']);
        expect(out[0].end).toMatchObject({ group_id: 'g1', wall_ms: 2400 });
    });

    it('the group key is stable while its members grow', () => {
        const one: any[] = groupToolTokens([start('g1', 2), running('a')]);
        const two: any[] = groupToolTokens([start('g1', 2), done('a'), running('b')]);
        expect(one[0].text).toBe(two[0].text);
        expect(one[0].raw).toBe(two[0].raw);
    });
});

describe('groupMemberProgress', () => {
    it('counts finished members across fences and shell html blocks', () => {
        const shell = html('<!-- TOOL_BLOCK_START:mcp_run_shell_command|Shell: ls|toolu_1 -->\nout\n<!-- TOOL_BLOCK_END:mcp_run_shell_command|toolu_1 -->');
        expect(groupMemberProgress([running('a'), done('b'), shell, { type: 'space', raw: '\n' }], 4))
            .toEqual({ seen: 3, done: 2 });
    });
    it('never reports more done than the group size', () => {
        expect(groupMemberProgress([done('a'), done('b'), done('c')], 2).done).toBe(2);
    });
});
