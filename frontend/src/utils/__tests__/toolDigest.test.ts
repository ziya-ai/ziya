/**
 * Shape digest for tool results (utils/toolDigest.ts).
 *
 * The collapsed tool block used to preview the first 200 characters of the
 * payload, which for JSON is a lone "{" or a run of keys and for a shell
 * transcript is the command echoed back.  The digest derives a headline,
 * chips and facts from the payload's *shape* with no per-tool knowledge.
 * Each case below is one of the shapes seen in real transcripts.
 */
import { digestToolResult, formatBytes } from '../toolDigest';

const issues = (n: number) => Array.from({ length: n }, (_, i) => ({
    key: `PROJ-${600 + i}`, id: String(1724369 + i),
    summary: `Issue ${i}`,
    status: i % 3 === 0 ? 'In Progress' : i % 3 === 1 ? 'Resolved' : 'Open',
    assignee: `user${i % 12}`, priority: 'P2', created: '2026-01-01',
    url: `https://jira.example/browse/PROJ-${600 + i}`,
}));

describe('collection digest (array of homogeneous objects)', () => {
    const raw = JSON.stringify({ total: 225, start_at: 0, max_results: 100, issues: issues(100) });
    const d = digestToolResult('mcp_search_jira_issues', raw);

    it('reads the count from the wrapper and the identifier range from the rows', () => {
        expect(d.kind).toBe('collection');
        expect(d.headline).toBe('225 issues matched, showing 100');
        expect(d.headline).not.toContain('{');
        expect(d.range).toEqual({ first: 'PROJ-600', last: 'PROJ-699' });
    });

    it('turns a recurring enum field into histogram chips', () => {
        const labels = d.chips.map(c => c.label);
        expect(labels).toContain('In Progress 34');
        expect(labels).toContain('Resolved 33');
        expect(labels).toContain('Open 33');
        expect(labels).toContain('12 assignees');
    });

    it('picks table columns with the identifier first and parks the rest as hidden', () => {
        expect(d.table).toBeDefined();
        expect(d.table!.columns[0]).toBe('key');
        expect(d.table!.columns).toContain('summary');
        expect(d.table!.columns).toContain('status');
        expect(d.table!.columns.length).toBeLessThanOrEqual(5);
        expect(d.table!.hidden).toContain('url');
        expect(d.table!.rows.length).toBe(100);
    });

    it('bylines size and type, not line counts of pretty-printing', () => {
        expect(d.byline).toMatch(/^\d+(\.\d+)? KB · JSON$/);
    });

    it('a bare array works the same as a wrapped one', () => {
        const bare = digestToolResult('x', JSON.stringify(issues(7)));
        expect(bare.kind).toBe('collection');
        expect(bare.headline).toBe('7 items');
        expect(bare.table!.rows.length).toBe(7);
    });
});

describe('record digest (one nested object)', () => {
    const group = {
        groupName: 'Payments Oncall', description: 'payments resolver group',
        memberCount: 14, primaryOwner: 'alice', secondaryOwner: 'carol',
        cti: { category: 'Payments', type: 'Ledger', item: 'Oncall' },
        members: Array.from({ length: 14 }, (_, i) => `m${i}`),
        settings: { a: 1, b: 2 }, enabled: true, longText: 'x'.repeat(300),
    };
    const d = digestToolResult('mcp_TicketingReadActions', JSON.stringify(group));

    it('headlines the name-like field and lists scalar facts, never a lone brace', () => {
        expect(d.kind).toBe('record');
        expect(d.headline).toBe('Payments Oncall');
        const keys = d.facts.map(f => f.key);
        expect(keys).toContain('secondaryOwner');
        expect(keys).toContain('memberCount');
        expect(keys).not.toContain('groupName');     // already the headline
        expect(keys).not.toContain('longText');      // too long for a fact row
        expect(keys).not.toContain('members');       // not scalar
        expect(d.facts.length).toBeLessThanOrEqual(6);
    });

    it('counts the fields it did not show and exposes the parsed value for the tree', () => {
        expect(d.moreFields).toBe(Object.keys(group).length - 1 - d.facts.length);
        expect(d.tree).toEqual(group);
        expect(d.byline).toContain('10 fields');
    });
});

describe('message digest (object whose payload is one string)', () => {
    const msg = '\nWorkspace could not be found. Creating a review without a workspace.\n\n'
        + 'Uploading ExampleService...\n\n * 50b31b0 - Add data sources\n\n'
        + 'Created code review of ExampleService...\n\n'
        + 'https://review.example.com/reviews/CR-1234567/revisions/1\n\n';
    const d = digestToolResult('mcp_CRRevisionCreator', JSON.stringify({ message: msg }));

    it('unwraps the string, surfaces the URL as a link and the failure line as a warning', () => {
        expect(d.kind).toBe('message');
        expect(d.link).toEqual({
            href: 'https://review.example.com/reviews/CR-1234567/revisions/1',
            label: 'CR-1234567',
        });
        expect(d.warning).toMatch(/could not be found/);
        expect(d.headline).toBe('Created code review of ExampleService...');
        expect(d.text).toContain('50b31b0');
        expect(d.text).not.toContain('\\n');
    });

    it('a success flag beside the message becomes a status chip', () => {
        const ok = digestToolResult('t', JSON.stringify({ success: true, message: 'Wrote 3 files to disk; nothing else was changed.' }));
        expect(ok.kind).toBe('message');
        expect(ok.chips).toEqual(expect.arrayContaining([{ label: 'success', tone: 'ok' }]));
        const bad = digestToolResult('t', JSON.stringify({ success: false, message: 'Nothing could be written here; the directory is read-only.' }));
        expect(bad.chips).toEqual(expect.arrayContaining([{ label: 'failed', tone: 'err' }]));
    });
});

describe('shell digest', () => {
    const out = ['$ grep -o foo bar.txt | sort -u', 'beam_planner_run_count',
        'beam_planner_run_duration_ms', 'partition_id', ...Array.from({ length: 122 }, (_, i) => `m${i}`)].join('\n');

    it('previews output lines and never echoes the command', () => {
        const d = digestToolResult('mcp_run_shell_command', out);
        expect(d.kind).toBe('shell');
        expect(d.headline).toBe('125 lines of output');
        expect(d.preview[0]).toBe('beam_planner_run_count');
        expect(d.preview.join('\n')).not.toContain('$ grep');
        expect(d.preview.length).toBeLessThanOrEqual(4);
        expect(d.moreLines).toBe(121);
        expect(d.chips.find(c => c.label.startsWith('exit'))).toBeUndefined();
    });

    it('reports a non-zero exit code the server appended, and omits that line from the preview', () => {
        const d = digestToolResult('mcp_run_shell_command', '$ false\nnope\n[Exit code: 1]');
        expect(d.chips).toEqual(expect.arrayContaining([{ label: 'exit 1', tone: 'err' }]));
        expect(d.preview).toEqual(['nope']);
    });

    it('an empty transcript says so instead of showing nothing', () => {
        const d = digestToolResult('mcp_run_shell_command', '$ true');
        expect(d.headline).toBe('no output');
    });
});

describe('JSON that does not parse (truncated payload)', () => {
    it('scans keys and a total from the prefix instead of previewing bytes', () => {
        const prefix = JSON.stringify({ total: 225, start_at: 0, issues: issues(3) }).slice(0, 180) + '...[truncated]';
        const d = digestToolResult('mcp_search_jira_issues', prefix);
        expect(d.kind).toBe('json-unparsed');
        expect(d.headline).toMatch(/^JSON \(truncated\)/);
        expect(d.chips.map(c => c.label)).toContain('total 225');
        expect(d.keys).toEqual(expect.arrayContaining(['total', 'issues', 'key']));
        expect(d.byline).toContain('truncated');
    });
});

describe('plain text', () => {
    it('headlines the first sentence and previews the next lines', () => {
        const d = digestToolResult('mcp_x', 'Query: "foo" - 4 results of 10 total\nline 2\nline 3\nline 4\nline 5');
        expect(d.kind).toBe('text');
        expect(d.headline).toBe('Query: "foo" - 4 results of 10 total');
        expect(d.preview).toEqual(['line 2', 'line 3', 'line 4']);
        expect(d.byline).toBe('5 lines · 64 B');
    });
});

describe('truncated collection (payload cut off mid-object by the MCP server)', () => {
    const items = Array.from({ length: 10 }, (_, i) => ({
        key: `PROJ-${600 + i}`, summary: `Issue ${i}`, status: 'Open',
        labels: ['a', 'b'], comments: [{ body: `c${i}` }],
    }));
    const full = JSON.stringify({ total: 225, issues: items });
    // Cut inside PROJ-607, after its nested comments array could not close.
    const cut = full.slice(0, full.indexOf('"key":"PROJ-607"') + 30) + '\n\n[content truncated]';
    const d = digestToolResult('mcp_search_jira_issues', cut);

    it('keeps every complete item instead of falling back to a key list', () => {
        expect(d.kind).toBe('collection');
        expect(d.headline).toBe('225 issues matched, showing 7');
        expect(d.range).toEqual({ first: 'PROJ-600', last: 'PROJ-606' });
        expect(d.chips[0]).toEqual({ label: 'output truncated', tone: 'warn' });
        expect(d.byline).toContain('JSON (truncated)');
        expect(d.truncated).toBe(true);
    });

    it('cuts at the shallowest complete element, so the last row is whole', () => {
        const last = d.table!.rows[d.table!.rows.length - 1];
        expect(last.key).toBe('PROJ-606');
        expect(last.comments).toEqual([{ body: 'c6' }]);
    });
});

describe('message that is a list of entries (memory search)', () => {
    const content = '[m_de6186b8] (negative_constraint) Lesson: the reconciler cannot be trusted here.\n\n'
        + '[m_c1064f78] (decision) The fast path cannot be used because retries failed.\n\n'
        + '[m_aa000001] (domain_context) Third entry about the topic.';
    const d = digestToolResult('mcp_memory_search', JSON.stringify({ content, count: 3 }));

    it('counts the entries, previews their first lines, and raises no warning', () => {
        expect(d.kind).toBe('message');
        expect(d.headline).toBe('3 entries');
        expect(d.preview[0]).toMatch(/^\[m_de6186b8\]/);
        expect(d.preview.length).toBe(3);
        expect(d.warning).toBeUndefined();
    });

    it('a failure word deep in an ordinary message is content, not a warning', () => {
        const m = digestToolResult('t', JSON.stringify({ message:
            'Uploaded the package successfully.\nBuilt the review.\nNotes follow.\nThe old approach failed in 2024.' }));
        expect(m.kind).toBe('message');
        expect(m.warning).toBeUndefined();
    });
});

describe('diff digest', () => {
    const diff = 'diff --git a/CHANGELOG.md b/CHANGELOG.md\n--- a/CHANGELOG.md\n+++ b/CHANGELOG.md\n'
        + '@@ -1,2 +1,3 @@\n-### Added\n+### Added\n+- **New entry**\n context';

    it('headlines the file and counts changed lines, never the diff headers', () => {
        const d = digestToolResult('', diff);
        expect(d.kind).toBe('diff');
        expect(d.headline).toBe('CHANGELOG.md');
        expect(d.chips).toEqual([{ label: '+2', tone: 'ok' }, { label: '−1', tone: 'err' }]);
        expect(d.preview).toEqual(['-### Added', '+### Added', '+- **New entry**']);
        expect(d.preview.join('\n')).not.toMatch(/^(---|\+\+\+|@@)/m);
    });

    it('a multi-file diff says how many files', () => {
        const two = diff + '\ndiff --git a/x.ts b/x.ts\n--- a/x.ts\n+++ b/x.ts\n@@ -1 +1 @@\n-a\n+b';
        expect(digestToolResult('', two).headline).toBe('2 files changed');
    });
});

describe('text headline skips wordless and marker lines', () => {
    it('a source file headlines its first real line, not "/**"', () => {
        const d = digestToolResult('', '/**\n * @jest-environment jsdom\n *\n * The MCP Servers panel reloads.');
        expect(d.headline).toBe('@jest-environment jsdom');
        expect(d.preview).toEqual(['The MCP Servers panel reloads.']);
    });

    it('a markdown heading loses its "#"', () => {
        expect(digestToolResult('', '# PROJ survey: A → B\nbody text').headline).toBe('PROJ survey: A → B');
    });
});

describe('formatBytes', () => {
    it('uses B / KB / MB with one decimal under 10', () => {
        expect(formatBytes(412)).toBe('412 B');
        expect(formatBytes(3617)).toBe('3.5 KB');
        expect(formatBytes(104559)).toBe('102 KB');
        expect(formatBytes(2 * 1024 * 1024 + 1)).toBe('2.0 MB');
    });
});
