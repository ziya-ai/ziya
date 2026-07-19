import * as fs from 'fs';
import {
  splitJsonSpecTrailingContent,
  stripBareProseFences,
  escapeNestedBacktickFences,
} from '../fenceScanner';

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

// A line that belongs to a unified-diff body.
function isDiffBodyLine(line: string): boolean {
  if (line === '') return true;
  if (/^[+\- ]/.test(line)) return true;
  if (/^@@/.test(line)) return true;
  if (/^(diff --git|index |--- |\+\+\+ |new file|deleted file|rename |similarity |old mode|new mode|\\)/.test(line)) return true;
  return false;
}

const NESTABLE = new Set(['diff', 'markdown', 'md']);

// RULE 2: for a column-0 nestable fence, the outer close is the first
// column-0 BARE run (len >= outer) whose NEXT non-blank line is NOT a
// diff-body line (or EOF). All interior fences (indented, lang-tagged,
// +/- prefixed) are treated as content; we still track the longest run
// seen for the upgrade width.
function rule2Upgrade(markdown: string): string {
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
      for (let j = i + 1; j < lines.length; j++) {
        const line = lines[j];
        if (!nestable) {
          const nx = matchOpen(line);
          if (nx && nx.info !== '') break;
          const cl = line.match(/^(`{3,})\s*$/);
          if (cl && cl[1].length >= outerLen) { closeIdx = j; break; }
          const inner = line.match(/^ {1,3}(`{3,})\s*$/);
          if (inner) maxInner = Math.max(maxInner, inner[1].length);
        } else {
          const bareCol0 = line.match(/^(`{3,})\s*$/);
          if (bareCol0 && bareCol0[1].length >= outerLen) {
            // peek next non-blank line
            let k = j + 1;
            while (k < lines.length && lines[k].trim() === '') k++;
            const next = k < lines.length ? lines[k] : null;
            if (next === null || !isDiffBodyLine(next)) {
              closeIdx = j;
              break;
            }
            // else: this bare ``` is interior diff content; keep scanning
            maxInner = Math.max(maxInner, bareCol0[1].length);
            continue;
          }
          const anyFence = line.match(/^\s*[+\-]?\s*(`{3,})/);
          if (anyFence) maxInner = Math.max(maxInner, anyFence[1].length);
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
    console.log(`  #${idx} lang=${JSON.stringify(c.lang)} lines=${ls.length} last=${JSON.stringify(ls[ls.length - 1])}`);
  });
}

describe('rule2 on /tmp/broken.md', () => {
  if (!fs.existsSync(SRC)) {
    it('skip', () => expect(true).toBe(true));
    return;
  }
  const raw = fs.readFileSync(SRC, 'utf8');
  it('RULE 2 + chain through marked', () => {
    const out = rule2Upgrade(raw);
    const a = splitJsonSpecTrailingContent(out);
    const b = stripBareProseFences(a);
    const c = escapeNestedBacktickFences(b);
    report(c, 'RULE2 full chain');
    expect(true).toBe(true);
  });
});
