/**
 * Group chrome for parallel tool batches (utils/toolGroupDisplay.ts) and the
 * seam into chatApi.ts.  The runner tags members of a >= 2 group and sends one
 * tool_group_end; the chat writes a START comment before the first member to
 * arrive and appends an END comment carrying the timing.  renderTokens
 * collects what lies between into one <ToolGroup> container
 * (utils/toolGroupTokens.ts, toolGroupTokens.test.ts).  A lone call renders
 * exactly as before.
 */
import * as fs from 'fs';
import * as path from 'path';
import {
    MEMBER_PREFIX, appendGroupEnd, groupEndMarker, groupStartDisplay, groupStartMarker,
    groupSummary, groupTiming, memberHeader, opensGroup,
} from '../toolGroupDisplay';
import { parseGroupEnd, parseGroupStart } from '../toolGroupTokens';

const END = { type: 'tool_group_end' as const, group_id: 'g1', size: 4, wall_ms: 2400, serial_ms: 6300 };

describe('opensGroup', () => {
    it('the first member to arrive opens the group, whatever its index', () => {
        const opened = new Set<string>();
        expect(opensGroup({ id: 'g', size: 3, index: 2 }, opened)).toBe(true);
        expect(opensGroup({ id: 'g', size: 3, index: 0 }, opened)).toBe(false);
        expect(opensGroup({ id: 'h', size: 2, index: 0 }, opened)).toBe(true);
        expect([...opened].sort()).toEqual(['g', 'h']);
    });
    it('a lone call or an untagged one never opens a group', () => {
        const opened = new Set<string>();
        expect(opensGroup({ id: 'g', size: 1, index: 0 }, opened)).toBe(false);
        expect(opensGroup(undefined, opened)).toBe(false);
        expect(opened.size).toBe(0);
    });
});

describe('memberHeader', () => {
    it('members get the ⫽ mark, lone calls keep their header verbatim', () => {
        expect(memberHeader('Shell: grep x', { id: 'g', size: 2, index: 1 })).toBe(`${MEMBER_PREFIX}Shell: grep x`);
        expect(memberHeader('Shell: grep x', undefined)).toBe('Shell: grep x');
        expect(memberHeader('Shell: grep x', { id: 'g', size: 1, index: 0 })).toBe('Shell: grep x');
    });
});

describe('markers', () => {
    it('START is a lone comment on its own block and round-trips through the parser', () => {
        const s = groupStartDisplay({ id: 'g1', size: 4, index: 0 });
        expect(s).toBe(`\n\n${groupStartMarker({ id: 'g1', size: 4, index: 0 })}\n\n`);
        expect(s).not.toMatch(/tool:mcp_parallel|running concurrently/);
        expect(parseGroupStart({ type: 'html', text: groupStartMarker({ id: 'g1', size: 4, index: 0 }) + '\n' }))
            .toEqual({ id: 'g1', size: 4 });
    });
    it('END carries the timing and round-trips', () => {
        expect(parseGroupEnd({ type: 'html', text: groupEndMarker(END) + '\n' }))
            .toEqual({ group_id: 'g1', size: 4, wall_ms: 2400, serial_ms: 6300 });
    });
    it('ids are sanitized so a hostile id cannot close the comment early', () => {
        // '-' is a legal id character and survives; '>' is stripped, so no
        // '-->' can form before the marker's own terminator.
        const m = groupStartMarker({ id: 'a-->b<x>', size: 2, index: 0 });
        expect(m.indexOf('-->')).toBe(m.length - 3);
        expect(m).not.toMatch(/<x|x>/);
        expect(parseGroupStart({ type: 'html', text: m })).toEqual({ id: 'a--bx', size: 2 });
    });
    it('appendGroupEnd appends the END marker after everything already written', () => {
        const out = appendGroupEnd('intro\n\nmember blocks', END);
        expect(out.startsWith('intro\n\nmember blocks')).toBe(true);
        expect(out.trimEnd().endsWith(groupEndMarker(END))).toBe(true);
    });
});

describe('summary', () => {
    it('reports wall, serial and the saving with a ratio', () => {
        expect(groupSummary(END)).toBe('✓ 4 calls in parallel · 2.4 s wall · saved 3.9 s vs serial (6.3 s, 2.6×)');
        expect(groupTiming(END)).toBe('2.4 s wall · saved 3.9 s vs serial (6.3 s, 2.6×)');
    });
    it('is honest when nothing was saved', () => {
        expect(groupTiming({ wall_ms: 1000, serial_ms: 1020 })).toBe('1.0 s wall · no saving vs serial (1.0 s)');
    });
});

describe('renderToolPart keeps the member mark on the result block', () => {
    it('a shell member keeps ⫽ when the header is rebuilt from the command', () => {
        // eslint-disable-next-line @typescript-eslint/no-var-requires
        const { renderToolPart } = require('../renderToolPart');
        const r = renderToolPart({
            type: 'tool', toolId: 't1', toolName: 'run_shell_command', result: 'x',
            startArgs: { command: 'grep x' }, startHeader: `${MEMBER_PREFIX}Shell: grep x`,
        }, '');
        expect(r.displayHeader).toBe(`${MEMBER_PREFIX}Shell: grep x`);
        const lone = renderToolPart({
            type: 'tool', toolId: 't2', toolName: 'run_shell_command', result: 'x',
            startArgs: { command: 'grep x' }, startHeader: 'Shell: grep x',
        }, '');
        expect(lone.displayHeader).toBe('Shell: grep x');
    });
});

describe('chatApi.ts seam (source contract)', () => {
    const src = fs.readFileSync(path.join(__dirname, '..', '..', 'apis', 'chatApi.ts'), 'utf-8');
    it('imports the helpers and closes the group on tool_group_end', () => {
        expect(src).toMatch(/from '\.\.\/utils\/toolGroupDisplay'/);
        expect(src).toMatch(/unwrappedData\.type === 'tool_group_end'/);
        expect(src).toMatch(/currentContent = appendGroupEnd\(currentContent, unwrappedData\)/);
        expect(src).not.toMatch(/spliceGroupEnd|isGroupLeader|groupLead/);
    });
    it('the group opens before the specialized handlers, so their blocks land inside it', () => {
        expect(src).toMatch(/const openedToolGroups = new Set<string>\(\)/);
        const branch = src.indexOf("unwrappedData.type === 'tool_start'");
        const open = src.indexOf('opensGroup(batchTag, openedToolGroups)', branch);
        const handler = src.indexOf('handleToolStart(unwrappedData.tool_name', branch);
        expect(branch).toBeGreaterThan(-1);
        expect(open).toBeGreaterThan(branch);
        expect(handler).toBeGreaterThan(open);
        expect(src.slice(open, handler)).toMatch(/currentContent \+= groupStartDisplay\(batchTag\)/);
    });
    it('members are marked and their header stored for tool_display', () => {
        const mark = src.indexOf('displayHeader = memberHeader(displayHeader, batchTag)');
        const store = src.indexOf('toolInputsMap.set(`${unwrappedData.tool_id}_header`, displayHeader)', mark);
        expect(mark).toBeGreaterThan(-1);
        expect(store).toBeGreaterThan(mark);
        expect(src).toMatch(/currentContent \+= toolStartDisplay;/);
    });
});
