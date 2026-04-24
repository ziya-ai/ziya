/**
 * Minimal RTF-to-HTML converter for preview rendering.
 * Handles: bold, italic, underline, font size, color table, paragraphs,
 * hex escapes, tabs, line breaks.  Unsupported control words are stripped.
 * No external dependencies.
 */

export function rtfToHtml(rtf: string): string {
  // Parse color table: {\colortbl ;\red0\green0\blue0;...}
  const colors: string[] = ['#000000']; // index 0 = auto/black
  const ctMatch = rtf.match(/\{\\colortbl\s*;?([^}]*)\}/);
  if (ctMatch) {
    const entries = ctMatch[1].split(';').filter(Boolean);
    for (const e of entries) {
      const r = e.match(/\\red(\d+)/)?.[1] ?? '0';
      const g = e.match(/\\green(\d+)/)?.[1] ?? '0';
      const b = e.match(/\\blue(\d+)/)?.[1] ?? '0';
      colors.push(`rgb(${r},${g},${b})`);
    }
  }

  // Strip header groups we don't need (fonttbl, stylesheet, info, etc.)
  let body = rtf;
  // Remove nested groups like {\fonttbl...}, {\stylesheet...}, {\info...}
  // by iteratively removing innermost brace groups that start with a known header
  const headerRe = /\{\\(?:fonttbl|stylesheet|info|generator|colortbl|listtable|listoverridetable|revtbl)\b[^{}]*\}/g;
  for (let i = 0; i < 5; i++) {
    const prev = body;
    body = body.replace(headerRe, '');
    if (body === prev) break;
  }
  // Remove the outermost {\rtf1 ... } wrapper
  body = body.replace(/^\{\\rtf1[^}]*?\s/, '').replace(/\}$/, '');

  // State machine
  let bold = false, italic = false, underline = false;
  let fontSize = 24; // RTF default fs24 = 12pt (half-points)
  let colorIdx = 0;
  const parts: string[] = [];
  let i = 0;

  const flush = (text: string) => {
    if (!text) return;
    const esc = text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    const styles: string[] = [];
    if (bold) styles.push('font-weight:bold');
    if (italic) styles.push('font-style:italic');
    if (underline) styles.push('text-decoration:underline');
    const pts = Math.round(fontSize / 2);
    if (pts !== 12) styles.push(`font-size:${pts}pt`);
    if (colorIdx > 0 && colorIdx < colors.length) styles.push(`color:${colors[colorIdx]}`);
    const attr = styles.length ? ` style="${styles.join(';')}"` : '';
    parts.push(attr ? `<span${attr}>${esc}</span>` : esc);
  };

  while (i < body.length) {
    const ch = body[i];

    // Skip nested groups we don't understand
    if (ch === '{') { i++; continue; }
    if (ch === '}') { i++; continue; }

    if (ch === '\\') {
      // Hex escape: \'xx
      if (body[i + 1] === "'") {
        const hex = body.substring(i + 2, i + 4);
        flush(String.fromCharCode(parseInt(hex, 16)));
        i += 4; continue;
      }
      // Control word
      const cwMatch = body.substring(i).match(/^\\([a-z]+)(-?\d+)?\s?/);
      if (cwMatch) {
        const [full, word, numStr] = cwMatch;
        const num = numStr !== undefined ? parseInt(numStr, 10) : undefined;
        switch (word) {
          case 'par': case 'line': parts.push('<br>'); break;
          case 'tab': flush('\t'); break;
          case 'b': bold = num !== 0; break;
          case 'i': italic = num !== 0; break;
          case 'ul': case 'ulnone': underline = word === 'ul' && num !== 0; break;
          case 'fs': if (num) fontSize = num; break;
          case 'cf': if (num !== undefined) colorIdx = num; break;
          case 'plain': bold = italic = underline = false; fontSize = 24; colorIdx = 0; break;
          // skip all other control words silently
        }
        i += full.length; continue;
      }
      // Escaped literal: \\ \{ \}
      if ('\\{}'.includes(body[i + 1])) { flush(body[i + 1]); i += 2; continue; }
      i++; continue;
    }

    // Collect plain text run
    const textMatch = body.substring(i).match(/^[^\\{}]+/);
    if (textMatch) { flush(textMatch[0]); i += textMatch[0].length; }
    else i++;
  }

  return parts.join('');
}
