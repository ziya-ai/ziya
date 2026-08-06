/**
 * Guard against double-applied diff hunks in the large context providers.
 *
 * A `useState` block in FolderContext.tsx was once applied twice, producing
 * six `TS2451: Cannot redeclare block-scoped variable` errors and a frontend
 * that would not build.  Every jest suite still passed, because jest compiles
 * each test's imports through babel (which strips types and does not check
 * scope collisions across a file it never loads) — only `tsc` or
 * `craco build` surfaced it.  That gap is why this test is source-level.
 *
 * These files are thousands of lines long and are edited by patch, so a
 * duplicated hunk is easy to miss in review and produces a confusing failure
 * far from its cause.
 */

import * as fs from 'fs';
import * as path from 'path';

const SRC = path.resolve(__dirname, '../..');

/** Files big enough that a duplicated hunk would plausibly go unnoticed. */
const WATCHED = [
  'context/FolderContext.tsx',
  'context/ChatContext.tsx',
  'context/ConversationListContext.tsx',
  'components/MUIChatHistory.tsx',
  'components/FolderTree.tsx',
];

/**
 * Collect `const [name, setName] = useState...` bindings with line numbers.
 * Deliberately regex-based rather than AST: this must run even when the file
 * does not typecheck, which is precisely the situation it exists to catch.
 */
function stateDecls(source: string): Array<{ name: string; line: number }> {
  const out: Array<{ name: string; line: number }> = [];
  source.split('\n').forEach((text, i) => {
    const m = text.match(/^\s*const\s*\[\s*([A-Za-z_$][\w$]*)\s*,/);
    if (m && /useState|useReducer/.test(text)) {
      out.push({ name: m[1], line: i + 1 });
    }
  });
  return out;
}

describe('no duplicated state declarations', () => {
  it.each(WATCHED)('%s declares each useState binding once', (rel) => {
    const file = path.join(SRC, rel);
    if (!fs.existsSync(file)) return; // renamed/removed — not this test's job

    const decls = stateDecls(fs.readFileSync(file, 'utf8'));
    const seen = new Map<string, number[]>();
    for (const d of decls) {
      seen.set(d.name, [...(seen.get(d.name) ?? []), d.line]);
    }

    const dupes = [...seen.entries()].filter(([, lines]) => lines.length > 1);
    expect(dupes.map(([n, l]) => `${n} at lines ${l.join(', ')}`)).toEqual([]);
  });

  it('detector actually flags a duplicated block', () => {
    // Non-vacuous: if the regex stops matching, the assertions above would
    // pass by finding nothing — the failure mode this whole file guards.
    const src = [
      'const [foo, setFoo] = useState(0);',
      'const [bar, setBar] = useState(1);',
      'const [foo, setFoo] = useState(0);',
    ].join('\n');
    const decls = stateDecls(src);
    expect(decls.map((d) => d.name)).toEqual(['foo', 'bar', 'foo']);
  });

  it('detector finds real declarations in the watched files', () => {
    // Guard the guard: prove the scan is not silently matching zero lines.
    const file = path.join(SRC, 'context/FolderContext.tsx');
    if (!fs.existsSync(file)) return;
    const decls = stateDecls(fs.readFileSync(file, 'utf8'));
    expect(decls.length).toBeGreaterThan(5);
  });

  it('does not flag distinct names with a shared prefix', () => {
    const src = [
      'const [folders, setFolders] = useState();',
      'const [folderFileSelections, setFolderFileSelections] = useState();',
    ].join('\n');
    const names = stateDecls(src).map((d) => d.name);
    expect(new Set(names).size).toBe(2);
  });

  it('ignores non-useState destructuring', () => {
    // Context destructures like `const { a, b } = useFoo()` and array
    // destructuring from other hooks must not be treated as state decls.
    const src = [
      'const [first, second] = someArray;',
      'const { conversations, folders } = useConversationList();',
    ].join('\n');
    expect(stateDecls(src)).toEqual([]);
  });
});
