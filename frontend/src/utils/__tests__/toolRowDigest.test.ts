/**
 * Compact tool rows (ToolBlock) and the batch strip (ToolBatch) read every
 * display fact from frontend/src/utils/toolRowDigest.ts.  The digest rule is
 * "a wrong digest is worse than none", so each extractor is tested with a
 * case that MUST produce a digest and a case that MUST NOT.
 */
import {
    toolFamily,
    headerArgument,
    splitShellCommand,
    shellResultDigest,
    structuredResultDigest,
    buildToolRowModel,
    classifyTokenForToolRun,
    groupToolRuns,
    summarizeFamilies,
    toolNameOfRunToken,
} from '../toolRowDigest';

describe('toolFamily', () => {
    it.each([
        ['mcp_run_shell_command', 'shell'],
        ['mcp_memory_search', 'memory'],
        ['chat_search', 'chat'],
        ['mcp_task_card_stage', 'task'],
        ['mcp_WorkspaceSearch', 'search'],
        ['mcp_file_read|file read: x|text', 'file'],
    ])('%s -> %s', (name, family) => {
        expect(toolFamily(name)).toBe(family);
    });
});

describe('headerArgument', () => {
    it('drops the lock prefix and the tool label', () => {
        expect(headerArgument('🔐 Memory Search: availability bridge')).toBe('availability bridge');
    });
    it('returns empty when the header has no argument', () => {
        expect(headerArgument('🔐 Memory Context')).toBe('');
    });
});

describe('splitShellCommand', () => {
    it('splits a one-line command from its output', () => {
        expect(splitShellCommand('$ ls -la\nfile1\nfile2')).toEqual({ command: 'ls -la', output: 'file1\nfile2' });
    });

    it('keeps a heredoc body with the command, through its delimiter', () => {
        const content = "$ python3 - <<'EOF'\nimport json\nprint(1)\nEOF\n1";
        expect(splitShellCommand(content)).toEqual({
            command: "python3 - <<'EOF'\nimport json\nprint(1)\nEOF",
            output: '1',
        });
    });

    it('treats only the first line as the command when a heredoc never closes', () => {
        const content = "$ cat <<EOF\nline a\nline b";
        expect(splitShellCommand(content)).toEqual({ command: 'cat <<EOF', output: 'line a\nline b' });
    });

    it('follows backslash continuations', () => {
        expect(splitShellCommand('$ echo a \\\n  b\na b')).toEqual({ command: 'echo a \\\n  b', output: 'a b' });
    });

    it('returns no command when the body does not start with "$ "', () => {
        expect(splitShellCommand('plain output')).toEqual({ command: '', output: 'plain output' });
    });
});

describe('shellResultDigest', () => {
    it('surfaces the exception line of a Python traceback, not its frames', () => {
        const out = [
            'Traceback (most recent call last):',
            '  File "<stdin>", line 23, in <module>',
            "KeyError: 'measure_name'",
            'trailing noise',
        ].join('\n');
        expect(shellResultDigest(out)).toEqual({ text: "KeyError: 'measure_name'", tone: 'error' });
    });

    it('flags "command not found"', () => {
        expect(shellResultDigest('ok\nbash: frob: command not found')).toEqual({
            text: 'bash: frob: command not found', tone: 'error',
        });
    });

    it('does not flag output that merely mentions an error class', () => {
        const out = 'src/a.py: raise ValueError raised here\nsrc/b.py: ok';
        expect(shellResultDigest(out)).toBeUndefined();
    });

    it('shows a single-line result as plain', () => {
        expect(shellResultDigest('arn:aws:sts::1:assumed-role/x\n')).toEqual({
            text: 'arn:aws:sts::1:assumed-role/x', tone: 'plain',
        });
    });

    it('returns nothing for ordinary multi-line output', () => {
        expect(shellResultDigest('a\nb\nc')).toBeUndefined();
    });
});

describe('structuredResultDigest', () => {
    it('ignores the generic "Output (N chars, M lines)" summary', () => {
        expect(structuredResultDigest('a\nb', 'Output (3 chars, 2 lines)')).toBeUndefined();
    });

    it('uses an informative formatter summary, stripped of markdown', () => {
        expect(structuredResultDigest('...', '**12** files read')).toEqual({ text: '12 files read', tone: 'plain' });
    });

    it('counts a JSON array', () => {
        expect(structuredResultDigest('[1,2,3]')).toEqual({ text: '3 items', tone: 'plain' });
    });

    it('counts a known list key in a JSON object, singular for one', () => {
        expect(structuredResultDigest('{"results":[{"a":1}],"total":1}')).toEqual({ text: '1 result', tone: 'plain' });
        expect(structuredResultDigest('{"conversations":[1,2,3,4,5,6,7,8,9]}')).toEqual({ text: '9 conversations', tone: 'plain' });
    });

    it('returns nothing for a multi-line non-JSON body', () => {
        expect(structuredResultDigest('one\ntwo')).toBeUndefined();
    });
});

describe('buildToolRowModel', () => {
    it('builds a signed shell row from the body, not the truncated header', () => {
        const longCmd = 'ada credentials update --account=123456789012 --provider=sso --role=ReadOnly --profile dev --once';
        const model = buildToolRowModel({
            toolName: 'mcp_run_shell_command',
            header: '🔐 Shell: ada credentials update --account=123456789012 -...',
            content: `$ ${longCmd}\nline1\nline2`,
        });
        expect(model.family).toBe('shell');
        expect(model.primary).toBe(longCmd);
        expect(model.status).toBe('signed');
        expect(model.lineCount).toBe(2);
        expect(model.digest).toBeUndefined();
    });

    it('notes heredoc length and reports the traceback', () => {
        const model = buildToolRowModel({
            toolName: 'mcp_run_shell_command',
            header: 'Shell: python3 - <<...',
            content: "$ python3 - <<'EOF'\nimport json\nx = {}\nx['m']\nEOF\nTraceback (most recent call last):\n  File \"<stdin>\", line 3\nKeyError: 'm'",
        });
        expect(model.primary).toBe("python3 - <<'EOF'");
        expect(model.primaryNote).toBe('(5 lines)');
        expect(model.digest).toEqual({ text: "KeyError: 'm'", tone: 'error' });
        expect(model.status).toBe('unsigned');
    });

    it('marks the streaming placeholder as running with no digest', () => {
        const model = buildToolRowModel({
            toolName: 'mcp_run_shell_command',
            header: 'Shell: ls',
            content: '$ ls\n⏳ Running...',
        });
        expect(model.status).toBe('running');
        expect(model.digest).toBeUndefined();
    });

    it('combines verb and header argument for non-shell tools', () => {
        const model = buildToolRowModel({
            toolName: 'mcp_memory_search',
            header: '🔐 Memory Search: availability bridge',
            content: '{"results": []}',
        });
        expect(model.family).toBe('memory');
        expect(model.primary).toBe('search availability bridge');
        expect(model.digest).toEqual({ text: '0 results', tone: 'plain' });
        expect(model.status).toBe('signed');
    });

    it('falls back to the verb alone when there is no argument', () => {
        const model = buildToolRowModel({ toolName: 'mcp_task_card_stage', header: 'Task Card Stage', content: 'a\nb' });
        expect(model.family).toBe('task');
        expect(model.primary).toBe('card stage');
    });
});

describe('tool-run grouping', () => {
    const tool = (name: string) => ({ type: 'code', lang: `tool:${name}|H|text`, text: 'x' });
    const space = { type: 'space', raw: '\n\n' };
    const para = { type: 'paragraph', text: 'prose' };

    it('classifies fence, html-marker, spacer, thinking and prose tokens', () => {
        expect(classifyTokenForToolRun(tool('mcp_run_shell_command'))).toBe('tool');
        expect(classifyTokenForToolRun({
            type: 'html', text: '<!-- TOOL_BLOCK_START:mcp_WorkspaceSearch|H|toolu_1 -->\nx\n<!-- TOOL_BLOCK_END:mcp_WorkspaceSearch|toolu_1 -->',
        })).toBe('tool');
        expect(classifyTokenForToolRun(space)).toBe('spacer');
        expect(classifyTokenForToolRun(tool('mcp_sequentialthinking'))).toBe('other');
        expect(classifyTokenForToolRun({ type: 'code', lang: 'thinking:1', toolName: 'thinking_1', text: 'x' })).toBe('other');
        expect(classifyTokenForToolRun(para)).toBe('other');
    });

    it('reads the bare tool name from a fence or an html marker', () => {
        expect(toolNameOfRunToken(tool('mcp_memory_search'))).toBe('mcp_memory_search');
        expect(toolNameOfRunToken({ type: 'html', text: '<!-- TOOL_BLOCK_START:mcp_chat_search|H|c_1 -->' })).toBe('mcp_chat_search');
        expect(toolNameOfRunToken(para)).toBe('');
    });

    it('batches three tool calls separated only by whitespace', () => {
        const kinds = [tool('a'), space, tool('b'), space, tool('c'), para].map(classifyTokenForToolRun);
        expect(groupToolRuns(kinds)).toEqual([
            { kind: 'batch', indices: [0, 2, 4] },
            { kind: 'single', index: 5 },
        ]);
    });

    it('leaves two consecutive calls unbatched', () => {
        const kinds = [tool('a'), space, tool('b')].map(classifyTokenForToolRun);
        expect(groupToolRuns(kinds)).toEqual([
            { kind: 'single', index: 0 }, { kind: 'single', index: 1 }, { kind: 'single', index: 2 },
        ]);
    });

    it('splits a run at prose', () => {
        const kinds = [tool('a'), para, tool('b'), tool('c')].map(classifyTokenForToolRun);
        expect(groupToolRuns(kinds).some(r => r.kind === 'batch')).toBe(false);
    });

    it('keeps trailing whitespace outside the batch', () => {
        const kinds = [tool('a'), tool('b'), tool('c'), space].map(classifyTokenForToolRun);
        expect(groupToolRuns(kinds)).toEqual([
            { kind: 'batch', indices: [0, 1, 2] },
            { kind: 'single', index: 3 },
        ]);
    });
});

describe('summarizeFamilies', () => {
    it('counts families, most frequent first, ties in first-seen order', () => {
        expect(summarizeFamilies([
            'mcp_run_shell_command', 'mcp_memory_search', 'mcp_run_shell_command',
            'mcp_chat_search', 'mcp_memory_context', 'mcp_run_shell_command',
        ])).toBe('3 shell · 2 memory · 1 chat');
    });
});
