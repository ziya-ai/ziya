import * as fs from 'fs';

// eslint-disable-next-line @typescript-eslint/no-var-requires
const markedMod = require('marked/lib/marked.umd.js');
const marked = markedMod.marked || markedMod;

const SRC = '/tmp/broken.md';

const OPEN_RE = /^( {0,3})(`{3,}|~{3,})(.*)$/;
function matchOpen(line: string) {
  const m = OPEN_RE.exec(line);
  if (!m) return null;
  const run = m[2];
  const char = run[0];
  const info = m[3];
  if (char === '`' && info.includes('`')) return null;
  return { char, len: run.length, info: info.trim(), indent: m[1].length };
}

const NESTABLE = new Set(['diff', 'markdown', 'md']);

// CANDIDATE FIX: depth-pair, but treat ANY fence-bearing line as a
// depth event regardless of +/-/space prefix. A line whose first
// non-space, non-(+/-) char run is >=3 backticks with an info string is
// an opener (depth++); the same shape bare is a close (depth--). This
// balances diff-prefixed inner fences (+```sql ... +```), which the
// current code drops.
function stripPrefix(line: string): string {
  // remove a single leading diff marker (+/-) but keep a context space
  return line.replace(/^[+\-]/, '');
}
function candidateUpgrade(markdown: string): string {
  const lines = markdown.split('\n');
  let i = 0;
  while (i < lines.length) {
    const open = matchOpen(lines[i]);
    if (open && open.char === '`' && open.indent === 0) {
      const outerLen = open.len;
      const info = open.info;
      const nestable = NESTABLE.has(info.toLowerCase());
      let closeIdx = -1;
      let maxInner = 0;
      let depth = 0;
      for (let j = i + 1; j < lines.length; j++) {
        const raw = lines[j];
        // True outer close: bare backtick run at column 0, no prefix.
        const bareCol0 = raw.match(/^(`{3,})\s*$/);
        if (bareCol0 && bareCol0[1].length >= outerLen && depth === 0) {
          closeIdx = j; break;
        }
        // Look at the line with any diff +/- marker stripped, to see the
        // inner fence shape regardless of diff prefix.
        const s = stripPrefix(raw);
        const innerOpen = s.match(/^\s*(`{3,})(\S.*)?$/);
        if (innerOpen) {
          const runLen = innerOpen[1].length;
          const hasInfo = !!(innerOpen[2] && innerOpen[2].trim() !== '');
          maxInner = Math.max(maxInner, runLen);
          if (hasInfo) depth++;
          else if (depth > 0) depth--;
        }
      }
      if (closeIdx !== -1 && maxInner >= outerLen) {
        const nf = '`'.repeat(maxInner + 1);
        lines[i] = nf + info;
        lines[closeIdx] = nf;
      }
      if (nestable && closeIdx !== -1) { i = closeIdx + 1; continue; }
    }
    i++;
  }
  return lines.join('\n');
}

function report(md: string, label: string) {
  const lexer = new marked.Lexer({ gfm: true });
  const toks = lexer.lex(md);
  const code = toks.filter((t: any) => t.type === 'code');
  // eslint-disable-next-line no-console
  console.log(`\n=== ${label}: ${code.length} code blocks ===`);
  code.forEach((c: any, idx: number) => {
    const ls = c.text.split('\n');
    // eslint-disable-next-line no-console
    console.log(`  #${idx} lang=${JSON.stringify(c.lang)} lines=${ls.length} first=${JSON.stringify(ls[0])} last=${JSON.stringify(ls[ls.length - 1])}`);
  });
  const loose = toks.find((t: any) => t.type === 'heading' && /Installation/.test(t.text));
  // eslint-disable-next-line no-console
  console.log(`  Installation loose heading? ${!!loose}`);
}

describe('isolated diff #2 (single live message)', () => {
  if (!fs.existsSync(SRC)) { it('skip', () => expect(true).toBe(true)); return; }
  const all = fs.readFileSync(SRC, 'utf8').split('\n');
  // diff #2 block: from the ```diff at L157 to its close at L305 (1-based)
  const diff2 = all.slice(156, 305).join('\n');

  it('RAW diff2 through marked (reproduces the break)', () => {
    report(diff2, 'RAW diff2');
    expect(true).toBe(true);
  });

  it('CANDIDATE fix on diff2 through marked', () => {
    report(candidateUpgrade(diff2), 'CANDIDATE diff2');
    expect(true).toBe(true);
  });
});
