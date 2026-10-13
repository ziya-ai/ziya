/**
 * Collapse doubled class-body braces in mermaid classDiagrams.
 *
 * Models that have been writing Python f-strings / Jinja / C# interpolated
 * strings often emit `class Foo {{ ... }}` (brace-escaped). Mermaid's
 * classDiagram grammar accepts exactly one `{` / `}` and fails with
 * "Expecting 'STRUCT_STOP', 'MEMBER', got 'OPEN_IN_STRUCT'".
 *
 * Only structural braces are rewritten:
 *  - a run of 2+ `{` terminating a `class X ...` or `namespace X` header line
 *  - a line consisting solely of 2+ `}` while such a block is open
 * Member lines, quoted labels, and `%%` comments are never modified.
 */

const CLASS_DIAGRAM_HEADER = /^\s*(?:%%\{[\s\S]*?\}%%\s*)?classDiagram(?:-v2)?\b/;

// `class Name`, optional `~Generic~`, optional `["label"]`, then the brace run.
const BLOCK_HEADER_DOUBLE =
  /^(\s*(?:class\s+[^\s{"]+(?:~[^~\n]*~)?(?:\s*\["[^"\n]*"\])?|namespace\s+[^\s{"]+)\s*)\{{2,}\s*$/;
const BLOCK_HEADER_SINGLE =
  /^\s*(?:class\s+[^\s{"]+(?:~[^~\n]*~)?(?:\s*\["[^"\n]*"\])?|namespace\s+[^\s{"]+)\s*\{\s*$/;
const CLOSE_RUN = /^(\s*)\}+\s*$/;

export function isClassDiagramForBraces(definition: string): boolean {
  return typeof definition === 'string' && CLASS_DIAGRAM_HEADER.test(definition.trimStart());
}

export function collapseClassDiagramDoubleBraces(definition: string): string {
  if (!isClassDiagramForBraces(definition)) {
    return definition;
  }
  // Fast path: nothing to do.
  if (!definition.includes('{{') && !definition.includes('}}')) {
    return definition;
  }

  const lines = definition.split('\n');
  let depth = 0;
  let changed = false;

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (/^\s*%%/.test(line)) {
      continue;
    }

    const dbl = line.match(BLOCK_HEADER_DOUBLE);
    if (dbl) {
      lines[i] = `${dbl[1]}{`;
      depth++;
      changed = true;
      continue;
    }
    if (BLOCK_HEADER_SINGLE.test(line)) {
      depth++;
      continue;
    }

    if (depth > 0) {
      const close = line.match(CLOSE_RUN);
      if (close) {
        if (line.trim().length > 1) {
          lines[i] = `${close[1]}}`;
          changed = true;
        }
        depth--;
      }
    }
  }

  return changed ? lines.join('\n') : definition;
}

/*
 * erDiagram variant. Models emit \`ENTITY {{ ... }}\` and \`||--o{{\`, which the
 * ER grammar rejects ("Expecting 'BLOCK_STOP', 'ATTRIBUTE_WORD', got '{'").
 * Valid ER syntax never has two adjacent braces outside a quoted string or
 * comment (cardinality tokens carry at most one brace per side), so every
 * run of 2+ braces outside quotes and \`%%\` lines is collapsed to one.
 */
const ER_DIAGRAM_HEADER = /^\s*(?:%%\{[\s\S]*?\}%%\s*)?erDiagram\b/;
const QUOTED_SEGMENT = /("(?:[^"\\\n]|\\.)*")/;

export function isErDiagramForBraces(definition: string): boolean {
  return typeof definition === 'string' && ER_DIAGRAM_HEADER.test(definition.trimStart());
}

export function collapseErDiagramDoubleBraces(definition: string): string {
  if (!isErDiagramForBraces(definition)) {
    return definition;
  }
  if (!definition.includes('{{') && !definition.includes('}}')) {
    return definition;
  }

  let changed = false;
  const lines = definition.split('\n').map((line) => {
    if (/^\s*%%/.test(line)) {
      return line;
    }
    // Odd indices of the split are quoted segments; leave them untouched.
    const out = line
      .split(QUOTED_SEGMENT)
      .map((seg, idx) => (idx % 2 === 1 ? seg : seg.replace(/\{{2,}/g, '{').replace(/\}{2,}/g, '}')))
      .join('');
    if (out !== line) {
      changed = true;
    }
    return out;
  });

  return changed ? lines.join('\n') : definition;
}
