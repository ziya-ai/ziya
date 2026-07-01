/**
 * Tests for the shared CommonMark-aware fence scanner.
 *
 * Pins the two hazards that caused the "diff renders as raw text
 * mid-block, every subsequent fence inverted" failure class:
 *   Hazard 1 — a line beginning with ``` that is actually the tail of a
 *              wrapped inline-code span must NOT open a phantom fence.
 *   Hazard 2 — a run of backticks inside a ~~~ fence is content, not a
 *              close (and vice-versa).
 */
import {
    classifyFenceLines,
    matchFenceOpen,
    matchFenceClose,
    isInsideFence,
    escapeNestedBacktickFences,
    stripBareProseFences,
    applyOutsideFences,
    splitJsonSpecTrailingContent,
} from '../fenceScanner';

const BT = '`'.repeat(3);
const BT4 = '`'.repeat(4);

const kinds = (md: string) => classifyFenceLines(md).map(c => c.kind);

describe('tool fence header with backtick (prose-absorption regression)', () => {
    // The producer (chatApi.ts) emits a tool block as a fenced code block
    // whose info string encodes the header:
    //   ````tool:<name>|<displayHeader>|<syntax>
    // A shell command like `sed ... | tr '`' '~'` puts a literal backtick
    // into <displayHeader>. CommonMark forbids a backtick in a backtick-fence
    // info string, so the opener fails to parse, the block's own closing
    // fence is misread as an opener, and the trailing prose after the block
    // is absorbed as that stray fence's content. The fix strips backticks
    // from the header at both emission sites (-> apostrophe).
    const headerWithBacktick = "🔐 Shell: sed -n '1,5p' x.py | tr '`'...";
    const headerSafe = headerWithBacktick.replace(/`/g, "'");

    // 5 lines, indices 0-4: opener / command / output / closer / prose
    const block = (header: string) =>
        [
            BT4 + 'tool:mcp_run_shell_command|' + header + '|bash',
            "$ sed -n '1,5p' x.py | tr '`' '~'",
            'some output line',
            BT4,
            "I've now verified the prose after the block.",
        ].join('\n');

    it('HAZARD: a backtick in the info string absorbs trailing prose into the block', () => {
        const cls = classifyFenceLines(block(headerWithBacktick));
        // The opener line is rejected (backtick in info string), so no block
        // opens there; the bare ```` at index 3 becomes the opener, and the
        // prose at index 4 is absorbed as that fence's content.
        expect(cls[0].kind).toBe('text');
        expect(cls[3].kind).toBe('open');
        expect(cls[4].kind).toBe('content');
    });

    it('FIX: backtick->apostrophe in the header opens the block and frees the prose', () => {
        const cls = classifyFenceLines(block(headerSafe));
        expect(cls[0].kind).toBe('open');     // real opener parses
        expect(cls[1].kind).toBe('content');  // command line
        expect(cls[2].kind).toBe('content');  // output line
        expect(cls[3].kind).toBe('close');    // real close
        expect(cls[4].kind).toBe('text');     // prose is free
    });

    it('matchFenceOpen rejects a tool info string containing a backtick', () => {
        expect(
            matchFenceOpen(BT4 + 'tool:mcp_run_shell_command|' + headerWithBacktick + '|bash'),
        ).toBeNull();
        expect(
            matchFenceOpen(BT4 + 'tool:mcp_run_shell_command|' + headerSafe + '|bash'),
        ).not.toBeNull();
    });

    it('non-shell tool header with a backtick poisons the fence identically', () => {
        // The fence info string is tool-agnostic: a non-shell tool whose header
        // echoes a backtick (e.g. a search query) breaks the opener the same
        // way a shell command does. This is why the result-path backtick strip
        // must be unconditional, not gated on isShellCommandTool.
        const badHeader = '🔐 Search: `foo` results';
        const safeHeader = badHeader.replace(/`/g, "'");
        expect(
            matchFenceOpen(BT4 + 'tool:mcp_WorkspaceSearch|' + badHeader + '|text'),
        ).toBeNull();
        expect(
            matchFenceOpen(BT4 + 'tool:mcp_WorkspaceSearch|' + safeHeader + '|text'),
        ).not.toBeNull();
    });
});

describe('matchFenceOpen', () => {
    it('accepts a plain backtick fence with a language tag', () => {
        expect(matchFenceOpen(BT + 'diff')).toEqual({
            char: '`', len: 3, info: 'diff', indent: 0,
        });
    });

    it('accepts a bare backtick fence (empty info)', () => {
        expect(matchFenceOpen(BT)).toEqual({
            char: '`', len: 3, info: '', indent: 0,
        });
    });

    it('accepts a tilde fence with a language tag', () => {
        expect(matchFenceOpen('~~~diff')).toEqual({
            char: '~', len: 3, info: 'diff', indent: 0,
        });
    });

    it('records longer opening runs', () => {
        expect(matchFenceOpen(BT4 + 'shell')?.len).toBe(4);
    });

    it('allows up to 3 spaces of indent', () => {
        expect(matchFenceOpen('   ' + BT + 'js')?.indent).toBe(3);
    });

    it('rejects 4+ spaces of indent (indented code, not a fence)', () => {
        expect(matchFenceOpen('    ' + BT + 'js')).toBeNull();
    });

    // HAZARD 1
    it('rejects a backtick opener whose info string contains a backtick', () => {
        expect(matchFenceOpen(BT + 'task-card ` fences collided')).toBeNull();
    });

    it('does NOT apply the backtick-in-info rule to tilde fences', () => {
        const r = matchFenceOpen('~~~ has a ` backtick');
        expect(r).not.toBeNull();
        expect(r?.char).toBe('~');
    });

    it('returns null for non-fence lines', () => {
        expect(matchFenceOpen('Right - the inner `')).toBeNull();
        expect(matchFenceOpen('plain prose')).toBeNull();
        expect(matchFenceOpen('`single`')).toBeNull();
    });
});

describe('matchFenceClose', () => {
    it('closes a backtick fence with an equal-length bare run', () => {
        expect(matchFenceClose(BT, { char: '`', len: 3 })).toEqual({ len: 3 });
    });

    it('closes with a longer run', () => {
        expect(matchFenceClose(BT4, { char: '`', len: 3 })).toEqual({ len: 4 });
    });

    it('does NOT close with a shorter run', () => {
        expect(matchFenceClose(BT, { char: '`', len: 4 })).toBeNull();
    });

    // HAZARD 2
    it('does NOT close a tilde fence with a backtick run', () => {
        expect(matchFenceClose(BT, { char: '~', len: 3 })).toBeNull();
    });

    it('does NOT close a backtick fence with a tilde run', () => {
        expect(matchFenceClose('~~~', { char: '`', len: 3 })).toBeNull();
    });

    it('does NOT treat a run with trailing text as a close', () => {
        expect(matchFenceClose(BT + 'diff', { char: '`', len: 3 })).toBeNull();
    });

    it('allows trailing whitespace on a close', () => {
        expect(matchFenceClose(BT + '   ', { char: '`', len: 3 })).toEqual({ len: 3 });
    });

    // A column-0 ```diff fence is only closed by a column-0 backtick run.
    // Any indented bare fence is diff content (a fenced block inside the
    // file being patched, carried in as a +/-/space-prefixed line), not
    // the wrapping close — accepting it would truncate the diff mid-body.
    it('does NOT close a column-0 ```diff fence with a space-indented run', () => {
        expect(
            matchFenceClose(' ' + BT, { char: '`', len: 3, info: 'diff', indent: 0 }),
        ).toBeNull();
    });

    it('DOES close a column-0 ```diff fence with a column-0 run', () => {
        expect(
            matchFenceClose(BT, { char: '`', len: 3, info: 'diff', indent: 0 }),
        ).toEqual({ len: 3 });
    });

    it('keeps CommonMark indent tolerance for an INDENTED ```diff open', () => {
        // When the diff fence itself opened indented, its close may match
        // that indent — the column-0 rule only applies to column-0 opens.
        expect(
            matchFenceClose('  ' + BT, { char: '`', len: 3, info: 'diff', indent: 2 }),
        ).toEqual({ len: 3 });
    });

    it('keeps indent tolerance for a non-diff fence', () => {
        expect(
            matchFenceClose(' ' + BT, { char: '`', len: 3, info: 'json', indent: 0 }),
        ).toEqual({ len: 3 });
    });
});

describe('classifyFenceLines', () => {
    it('classifies a normal backtick code block', () => {
        const md = [BT + 'js', 'const x = 1;', BT].join('\n');
        expect(kinds(md)).toEqual(['open', 'content', 'close']);
    });

    it('classifies a normal tilde code block', () => {
        const md = ['~~~diff', '-old', '+new', '~~~'].join('\n');
        expect(kinds(md)).toEqual(['open', 'content', 'content', 'close']);
    });

    // HAZARD 1 end-to-end
    it('does not let an inline-span tail open a phantom fence', () => {
        const md = [
            'Right - the inner `',
            '',
            BT + 'task-card ` fences collided with the fence.',
            '',
            'next paragraph',
        ].join('\n');
        expect(kinds(md)).toEqual(['text', 'text', 'text', 'text', 'text']);
    });

    // HAZARD 2 end-to-end
    it('keeps backtick runs inside a tilde fence as content', () => {
        const md = [
            '~~~diff',
            '--- a/x.py',
            '+    ' + BT + 'task-card' + BT + ' fenced block.',
            '~~~',
        ].join('\n');
        const cls = classifyFenceLines(md);
        expect(cls.map(c => c.kind)).toEqual(['open', 'content', 'content', 'close']);
        expect((cls[0] as any).char).toBe('~');
    });

    // The exact multi-block shape from the production failure
    it('classifies the production failure response correctly', () => {
        const md = [
            'Right - the inner `',
            '',
            BT + 'task-card ` fences.',
            '',
            '~~~diff',
            '--- a/app/api/commands.py',
            '+    ' + BT + 'task-card' + BT + ' fenced block.',
            '~~~',
            '',
            'prose between blocks',
            '',
            BT + 'diff',
            '+  const f = "x";',
            BT,
        ].join('\n');
        expect(kinds(md)).toEqual([
            'text', 'text', 'text', 'text',
            'open', 'content', 'content', 'close',
            'text', 'text', 'text',
            'open', 'content', 'close',
        ]);
    });

    // Regression: a ```diff that patches a file containing its OWN fenced
    // code blocks. Those inner fences arrive as diff body lines — a context
    // line is " ```" (leading context space), an added opener is "+```sql".
    // The space-indented " ```" must NOT close the outer diff; only the
    // final column-0 ``` does. Previously the first " ```" truncated the
    // diff and everything after spilled out as loose markdown.
    it('keeps space-indented body fences inside a column-0 ```diff', () => {
        const md = [
            BT + 'diff',
            '--- a/SKILL.md',
            '+++ b/SKILL.md',
            '@@ -1,5 +1,7 @@',
            ' Install the model:',
            ' ' + BT + 'bash',
            ' aws configure',
            ' ' + BT,          // context-space close of SKILL.md's own block
            '+More text',
            '+' + BT + 'json',  // added opener inside the diff body
            '+{"a": 1}',
            '+' + BT,
            BT,                 // the REAL outer close, at column 0
        ].join('\n');
        const k = kinds(md);
        // Exactly one open and one close; everything between is content.
        expect(k[0]).toBe('open');
        expect(k[k.length - 1]).toBe('close');
        expect(k.filter(x => x === 'open')).toHaveLength(1);
        expect(k.filter(x => x === 'close')).toHaveLength(1);
        expect(k.slice(1, -1).every(x => x === 'content')).toBe(true);
    });

    it('does not close a 4-backtick fence with a 3-backtick run', () => {
        const md = [BT4 + 'tool', BT, 'still inside', BT4].join('\n');
        expect(kinds(md)).toEqual(['open', 'content', 'content', 'close']);
    });
});

describe('isInsideFence', () => {
    it('reports content and close as inside, open and text as outside', () => {
        const md = [BT + 'js', 'x', BT, 'after'].join('\n');
        const cls = classifyFenceLines(md);
        expect(isInsideFence(cls, 0)).toBe(false); // open
        expect(isInsideFence(cls, 1)).toBe(true);  // content
        expect(isInsideFence(cls, 2)).toBe(true);  // close
        expect(isInsideFence(cls, 3)).toBe(false); // text
    });
});

describe('escapeNestedBacktickFences', () => {
    // Original purpose: a nested backtick fence at column 0 inside a
    // wider backtick block would prematurely close it in marked — escape it.
    it('escapes a nested col-0 backtick run inside a 4-backtick block', () => {
        const md = [BT4 + 'diff', '--- a/x', BT, 'inner', BT, BT4].join('\n');
        const out = escapeNestedBacktickFences(md).split('\n');
        expect(out[0]).toBe(BT4 + 'diff');           // outer opener intact
        expect(out[2]).toBe('&#96;&#96;&#96;');       // inner run escaped
        expect(out[4]).toBe('&#96;&#96;&#96;');       // second inner run escaped
        expect(out[5]).toBe(BT4);                     // outer close intact
    });

    // HAZARD 1: the inline-span tail must not be escaped (it's text, not content)
    it('leaves an inline-span tail untouched', () => {
        const md = ['Right - the inner `', '', BT + 'task-card ` fences.'].join('\n');
        const out = escapeNestedBacktickFences(md).split('\n');
        expect(out[2]).toBe(BT + 'task-card ` fences.');
    });

    // HAZARD 2: backtick run inside a tilde fence is content but NOT escaped
    it('does not escape backtick runs inside a tilde fence', () => {
        const md = ['~~~diff', '+    ' + BT + 'task-card' + BT + ' block.', '~~~'].join('\n');
        const out = escapeNestedBacktickFences(md).split('\n');
        expect(out[1]).toBe('+    ' + BT + 'task-card' + BT + ' block.');
    });

    // End-to-end: the exact production failure shape round-trips with
    // both real diff openers intact and the phantom never escaped.
    it('preserves both real diff fences in the production failure shape', () => {
        const md = [
            'Right - the inner `',
            '',
            BT + 'task-card ` fences.',
            '',
            '~~~diff',
            '+    ' + BT + 'task-card' + BT + ' block.',
            '~~~',
            '',
            'prose',
            '',
            BT + 'diff',
            '+ const f = "x";',
            BT,
        ].join('\n');
        const out = escapeNestedBacktickFences(md).split('\n');
        expect(out[2]).toBe(BT + 'task-card ` fences.');  // phantom not escaped
        expect(out[5]).toBe('+    ' + BT + 'task-card' + BT + ' block.'); // tilde body intact
        expect(out[10]).toBe(BT + 'diff');                // real opener intact
        expect(out[12]).toBe(BT);                         // real close intact
    });

    it('does not escape open or close fence lines themselves', () => {
        const md = [BT + 'js', 'safe();', BT].join('\n');
        const out = escapeNestedBacktickFences(md).split('\n');
        expect(out[0]).toBe(BT + 'js');
        expect(out[2]).toBe(BT);
    });
});

describe('stripBareProseFences', () => {
    it('unwraps markdown prose trapped between bare fence pairs', () => {
        const input = [
            '**Update 1**: First section.',
            '',
            BT,
            '',
            '**Update 2**: Second section.',
            '',
            BT,
            '',
            '**Update 3**: Third section.',
        ].join('\n');
        const result = stripBareProseFences(input);
        expect(result).toContain('**Update 1**');
        expect(result).toContain('**Update 2**');
        expect(result).toContain('**Update 3**');
    });

    it('strips empty fence pairs entirely', () => {
        const input = ['Before', '', BT4, '', BT4, '', 'After'].join('\n');
        const result = stripBareProseFences(input);
        expect(result).toContain('Before');
        expect(result).toContain('After');
        expect(result).not.toMatch(/^`{3,}\s*$/m);
    });

    it('preserves code blocks with language tags', () => {
        const input = [BT + 'python', 'def hello():', '    return "world"', BT].join('\n');
        const result = stripBareProseFences(input);
        expect(result).toBe(input);
    });

    it('preserves bare fences that actually wrap code', () => {
        const input = [BT, 'const x = 42;', 'function test() {', '    return x;', '}', BT].join('\n');
        const result = stripBareProseFences(input);
        expect(result).toBe(input);
    });

    it('unwraps web-search results with HTML tags and markdown links', () => {
        const input = [
            'Here are the results:',
            '',
            BT4,
            'Title: Olympic View Elementary',
            'Description: Above average. <strong>56% math</strong>.',
            'URL: [niche.com](https://www.niche.com/k12/olympic-view/)',
            BT4,
        ].join('\n');
        const result = stripBareProseFences(input);
        expect(result).toContain('Title: Olympic View Elementary');
        expect(result).toContain('<strong>56% math</strong>');
        expect(result).not.toMatch(/^`{3,}\s*$/m);
    });

    it('preserves JSON-like code blocks without prose markers', () => {
        const input = [BT, '{"key": "value", "n": 42}', BT].join('\n');
        const result = stripBareProseFences(input);
        expect(result).toBe(input);
    });

    it('does not treat an inline-span-tail line as a language fence opener', () => {
        const input = [
            'Right - the inner `',
            '',
            BT + 'task-card ` fences collided with the markdown code fence.',
            '',
            'next paragraph',
        ].join('\n');
        const result = stripBareProseFences(input);
        expect(result).toContain(BT + 'task-card ` fences collided');
        expect(result).toContain('next paragraph');
    });

    it('unwraps a 4-backtick outer fence around a tagged inner block', () => {
        const input = [BT4, BT + 'diff', '+ added', '- removed', BT, BT4].join('\n');
        const result = stripBareProseFences(input);
        expect(result).toContain(BT + 'diff');
        expect(result).toContain('+ added');
        const fences = result.split('\n').filter(l => /^`{4,}\s*$/.test(l));
        expect(fences.length).toBe(0);
    });
});

// ---------------------------------------------------------------------------
// applyOutsideFences
// ---------------------------------------------------------------------------
describe('applyOutsideFences', () => {
    // Transform used in most tests: insert a blank line before any ``` run
    // that is not already preceded by a blank line — mirrors the real
    // MarkdownRenderer preprocessing passes.
    const ensureBlankBeforeFence = (s: string): string =>
        s.replace(/([^\n])\n(`{3,})/g, '$1\n\n$2');

    it('applies transform to plain prose with no fences', () => {
        const input = 'some text\n```js\ncode\n```';
        const result = applyOutsideFences(input, ensureBlankBeforeFence);
        expect(result).toBe('some text\n\n```js\ncode\n```');
    });

    it('leaves verbatim content inside a fenced block untouched', () => {
        // The inner diff body contains "text\n```vega-lite" which the transform
        // would corrupt if it ran over it — but it must not.
        const inner = 'diff --git a/f b/f\n--- a/f\n+++ b/f\n@@ -1 +1 @@\n-old\n+new';
        const input = '```diff\n' + inner + '\n```';
        const result = applyOutsideFences(input, ensureBlankBeforeFence);
        expect(result).toBe(input);
    });

    it('transforms prose before and after a fenced block but not inside', () => {
        const before = 'heading\n```js';
        const fence  = '```js\nconst x = 1;\n```';
        const after  = 'more text\n```python';
        const input  = before + '\n' + fence + '\n' + after;
        const result = applyOutsideFences(input, ensureBlankBeforeFence);
        // The prose opener "heading\n```js" gets a blank line injected
        expect(result).toContain('heading\n\n```js');
        // The prose trailer "more text\n```python" gets a blank line injected
        expect(result).toContain('more text\n\n```python');
        // The fence body is preserved verbatim
        expect(result).toContain('const x = 1;');
    });

    it('handles an unterminated fence (streaming) without corrupting tail', () => {
        // Unterminated fences: everything after the opener is content.
        const input = 'prose\n```ts\nconst a = 1;\nmore code';
        const result = applyOutsideFences(input, ensureBlankBeforeFence);
        // Only "prose\n```ts" is mutable — the opener sits in a text segment
        // together with "prose", so a blank line gets inserted before it.
        expect(result).toContain('prose\n\n```ts');
        // The code body must be unchanged
        expect(result).toContain('const a = 1;');
        expect(result).toContain('more code');
    });

    it('reproduces the production failure: diff body with fence-like line inside outer fence', () => {
        // This is the shape that caused the original defect: a diff shown inside
        // a ```diff fence whose context lines contain a fence-like line. The
        // preprocessing pass must not inject a blank line inside the diff body.
        const diffBody = [
            '```diff',
            'diff --git a/f b/f',
            '--- a/f',
            '+++ b/f',
            '@@ -1 +1 @@',
            '-    /(^#{1,6}\\s+[^\\n`]+?)\\s+(`{3,}[a-zA-Z0-9_-]*)/gm,',
            '+    /(^#{1,6}\\s+[^\\n`]+?)\\s+(`{3,}[a-zA-Z0-9_-]*)(?=\\s|$)/gm,',
            '```',
        ].join('\n');
        const input = 'See the fix below:\n' + diffBody;
        const result = applyOutsideFences(input, ensureBlankBeforeFence);
        // Blank line injected before the outer fence opener (prose region)
        expect(result).toContain('See the fix below:\n\n```diff');
        // The diff body lines must be byte-identical — no blank lines injected
        expect(result).toContain('-    /(^#{1,6}\\s+[^\\n`]+?)\\s+(`{3,}[a-zA-Z0-9_-]*)/gm,');
        expect(result).toContain('+    /(^#{1,6}\\s+[^\\n`]+?)\\s+(`{3,}[a-zA-Z0-9_-]*)(?=\\s|$)/gm,');
        // No blank line was inserted before the inner fence-like lines
        const lines = result.split('\n');
        const innerFenceLine = lines.findIndex(l => l.includes('`{3,}[a-zA-Z0-9_-]*)'));
        expect(innerFenceLine).toBeGreaterThan(0);
        expect(lines[innerFenceLine - 1]).not.toBe('');
    });

    it('identity transform leaves input unchanged', () => {
        const input = 'text\n```js\ncode\n```\nmore text';
        expect(applyOutsideFences(input, (s) => s)).toBe(input);
    });

    it('handles empty string', () => {
        expect(applyOutsideFences('', ensureBlankBeforeFence)).toBe('');
    });

    it('handles multiple consecutive fenced blocks', () => {
        const input = 'a\n```js\nx\n```\nb\n```py\ny\n```\nc';
        const result = applyOutsideFences(input, (s) => s.toUpperCase());
        // Prose segments "a\n", "\nb\n", "\nc" are uppercased
        expect(result.startsWith('A\n')).toBe(true);
        expect(result).toContain('\nB\n');
        expect(result.endsWith('\nC')).toBe(true);
        // Fence content is left in original case
        expect(result).toContain('x');
        expect(result).toContain('y');
    });
});

describe('splitJsonSpecTrailingContent', () => {
    /** Extract [lang, content] pairs for every closed fenced block in md. */
    const blocks = (md: string): Array<[string, string]> => {
        const lines = md.split('\n');
        const classes = classifyFenceLines(md);
        const out: Array<[string, string]> = [];
        let open = -1;
        let lang = '';
        lines.forEach((_, i) => {
            if (classes[i].kind === 'open') { open = i; lang = (classes[i] as any).info; }
            else if (classes[i].kind === 'close' && open >= 0) {
                out.push([lang, lines.slice(open + 1, i).join('\n')]);
                open = -1;
            }
        });
        return out;
    };

    it('leaves a well-formed plotly block untouched', () => {
        const md = [BT + 'plotly', '{"data": [1, 2]}', BT, 'after'].join('\n');
        expect(splitJsonSpecTrailingContent(md)).toBe(md);
    });

    it('leaves an unterminated (streaming) plotly block untouched', () => {
        const md = [BT + 'plotly', '{"data": [1,'].join('\n');
        expect(splitJsonSpecTrailingContent(md)).toBe(md);
    });

    it('leaves non-JSON languages untouched even with trailing text', () => {
        const md = [BT + 'mermaid', 'graph TD', 'A-->B', BT].join('\n');
        expect(splitJsonSpecTrailingContent(md)).toBe(md);
    });

    it('splits prose glued after the JSON value out of the block', () => {
        const md = [BT + 'plotly', '{"data": [1]}Some prose here.', BT].join('\n');
        const result = splitJsonSpecTrailingContent(md);
        const b = blocks(result);
        expect(b).toHaveLength(1);
        expect(JSON.parse(b[0][1])).toEqual({ data: [1] });
        expect(result).toContain('Some prose here.');
        // Prose must be outside the fence.
        expect(b[0][1]).not.toContain('Some prose');
    });

    it('recovers a second plotly block swallowed by an unclosed first one', () => {
        const md = [
            BT + 'plotly',
            '{"data": [1]}prose between the charts',
            '',
            BT + 'plotly',
            '{"data": [2]}',
            BT,
        ].join('\n');
        const result = splitJsonSpecTrailingContent(md);
        const b = blocks(result);
        expect(b).toHaveLength(2);
        expect(JSON.parse(b[0][1])).toEqual({ data: [1] });
        expect(JSON.parse(b[1][1])).toEqual({ data: [2] });
        expect(result).toContain('prose between the charts');
    });

    it('handles braces and escapes inside JSON strings', () => {
        const md = [BT + 'plotly', '{"t": "a } b \\" c"}trailing', BT].join('\n');
        const result = splitJsonSpecTrailingContent(md);
        const b = blocks(result);
        expect(b).toHaveLength(1);
        expect(JSON.parse(b[0][1])).toEqual({ t: 'a } b " c' });
        expect(result).toContain('trailing');
    });

    it('recovers nested specs across multiple passes', () => {
        const md = [
            BT + 'plotly',
            '{"a": 1}x',
            BT + 'plotly',
            '{"b": 2}y',
            BT + 'plotly',
            '{"c": 3}',
            BT,
        ].join('\n');
        const result = splitJsonSpecTrailingContent(md);
        const b = blocks(result);
        expect(b).toHaveLength(3);
        expect(b.map(([, c]) => JSON.parse(c))).toEqual([{ a: 1 }, { b: 2 }, { c: 3 }]);
    });

    it('does not split when only whitespace trails the JSON', () => {
        const md = [BT + 'plotly', '{"data": [1]}   ', '', BT].join('\n');
        expect(splitJsonSpecTrailingContent(md)).toBe(md);
    });
});
