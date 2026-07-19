import * as fs from 'fs';
import {
  upgradeNestedFences,
  splitJsonSpecTrailingContent,
  stripBareProseFences,
  escapeNestedBacktickFences,
} from '../fenceScanner';

// Real marked via its UMD/CJS build.
// eslint-disable-next-line @typescript-eslint/no-var-requires
const markedMod = require('marked/lib/marked.umd.js');
const marked = markedMod.marked || markedMod;

const SRC = '/tmp/broken.md';

function blockReport(md: string, label: string) {
  const lexer = new marked.Lexer({ gfm: true });
  const toks = lexer.lex(md);
  const codeBlocks = toks.filter((t: any) => t.type === 'code');
  // eslint-disable-next-line no-console
  console.log(`\n=== ${label}: ${codeBlocks.length} code blocks ===`);
  codeBlocks.forEach((c: any, idx: number) => {
    const lines = c.text.split('\n');
    // eslint-disable-next-line no-console
    console.log(
      `  block#${idx} lang=${JSON.stringify(c.lang)} lines=${lines.length} ` +
        `first=${JSON.stringify(lines[0])} last=${JSON.stringify(lines[lines.length - 1])}`,
    );
  });
  // also report whether '## Installation' is inside a code block or loose
  const looseHeading = toks.find(
    (t: any) => t.type === 'heading' && /Installation/.test(t.text),
  );
  // eslint-disable-next-line no-console
  console.log(`  '## Installation' as loose heading token? ${!!looseHeading}`);
}

describe('definitive trace on /tmp/broken.md', () => {
  if (!fs.existsSync(SRC)) {
    it('skipped: no /tmp/broken.md', () => expect(true).toBe(true));
    return;
  }
  const raw = fs.readFileSync(SRC, 'utf8');
  const rawLines = raw.split('\n');

  it('prints every fence line with visible leading whitespace', () => {
    rawLines.forEach((l, i) => {
      if (/`{3,}/.test(l)) {
        const lead = (l.match(/^(\s*)/) || ['', ''])[1].length;
        // eslint-disable-next-line no-console
        console.log(`  L${i + 1} lead=${lead} ${JSON.stringify(l)}`);
      }
    });
    expect(true).toBe(true);
  });

  it('runs the real chain + marked, reports block boundaries', () => {
    blockReport(raw, 'RAW');
    const afterUpgrade = upgradeNestedFences(raw);
    blockReport(afterUpgrade, 'after upgradeNestedFences');
    const afterSplit = splitJsonSpecTrailingContent(afterUpgrade);
    const afterStrip = stripBareProseFences(afterSplit);
    const afterEscape = escapeNestedBacktickFences(afterStrip);
    blockReport(afterEscape, 'after FULL chain');
    expect(true).toBe(true);
  });

  it('tests PROPOSED rule (first column-0 bare run = close) through marked', () => {
    // Local reimplementation of the proposed nestable-branch rule.
    const NESTABLE = new Set(['diff', 'markdown', 'md']);
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
    function proposedUpgrade(markdown: string): string {
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
              if (bareCol0 && bareCol0[1].length >= outerLen) { closeIdx = j; break; }
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
    const out = proposedUpgrade(raw);
    const afterSplit = splitJsonSpecTrailingContent(out);
    const afterStrip = stripBareProseFences(afterSplit);
    const afterEscape = escapeNestedBacktickFences(afterStrip);
    blockReport(afterEscape, 'PROPOSED rule + chain');
    expect(true).toBe(true);
  });
});
