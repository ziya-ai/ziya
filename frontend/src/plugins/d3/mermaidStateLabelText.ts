/**
 * stateDiagram label text: escape colons and prose semicolons.
 *
 * Mermaid's state lexer reads the text after a label colon as
 * `":"[^:\n;]+`, so the first `:` or `;` inside the text ends it.  A
 * transition label, state description or single-line note carrying a time
 * ("parabolic 1, 05:54:19.101") is cut at "05" and the rest lexes as a
 * second DESCR: "Expecting ... got 'DESCR'" (reported 2026-10-16).  A `;`
 * does not fail the parse but ends the label and turns the words after it
 * into states.
 *
 * Text after the separator colon is rewritten with `#58;` / `#59;`, which
 * mermaid's entity encoding renders back as ":" / ";".  (`&#58;` renders as
 * "&:" in state diagrams.)  The state lexer has no working statement
 * separator: its ID rule `[^:\n\s\-\{]+` takes `;`, so `B --> C ; C --> D`
 * adds a state named ";" and `A --> B;` a state named "B;".  A `;` followed
 * by a real statement (`B --> C`, `note left of X`, ...) therefore becomes a
 * line break at the same indent, and a trailing `;` is dropped.  Multi-line
 * note bodies already accept colons and are left alone, as are directive
 * lines, comments and `state "..." as X`.  Existing `#...;` entities are
 * preserved, so the rewrite is idempotent.
 *
 * Pure functions (no DOM) so they can be unit-tested directly.
 */

const ESCAPED_COLON = '#58;';
const ESCAPED_SEMICOLON = '#59;';

// A state reference in a transition: `[*]`, or a name with an optional
// `:::class` shorthand.
const STATE_REF = String.raw`(?:\[\*\]|[^\s:;]+?)(?::::[\w-]+)?`;

const TRANSITION = new RegExp(String.raw`^(\s*${STATE_REF}\s*-->\s*${STATE_REF}\s*):(.*)$`);
const NOTE_LINE = /^(\s*note\s+(?:left|right)\s+of\s+[^\s:;]+\s*):(.*)$/i;
const DESCRIPTION = /^(\s*[\w.-]+\s*):(?!::)(.*)$/;

// `note left of X` with no colon opens a block that `end note` closes.
const NOTE_BLOCK_START = /^note\s+(?:left|right)\s+of\s+[^\s:;]+$/i;
const NOTE_END = /^end\s+note$/i;

// Lines whose colons are not label separators.
const NOT_A_LABEL = /^\s*(?:%%|stateDiagram|state\s|classDef\s|class\s|style\s|click\s|accTitle\b|accDescr\b|direction\s|scale\s|hide\s|note\s*")/i;

// What may follow a `;` that the author meant as a statement separator.
const STATEMENT_AFTER_SEMICOLON = new RegExp(
  String.raw`^\s*(?:${STATE_REF}\s*-->|note\s+(?:left|right)\s+of\s|state\s|classDef\s|class\s|style\s|direction\s+(?:TB|BT|LR|RL)\b)`,
  'i',
);

const LABEL_DELIMITER = /#\w+;|[:;]/g;

function escapeLabel(text: string, indent: string): string {
  let out = '';
  let last = 0;
  const re = new RegExp(LABEL_DELIMITER.source, 'g');
  let m: RegExpExecArray | null;
  while ((m = re.exec(text)) !== null) {
    out += text.slice(last, m.index);
    last = m.index + m[0].length;
    if (m[0] === ':') {
      out += ESCAPED_COLON;
    } else if (m[0] === ';') {
      const rest = text.slice(last);
      if (!rest.trim()) {
        return out.trimEnd(); // a statement terminator
      }
      if (STATEMENT_AFTER_SEMICOLON.test(rest)) {
        return out.trimEnd() + '\n' + indent + escapeStatement(rest.trimStart(), indent);
      }
      out += ESCAPED_SEMICOLON;
    } else {
      out += m[0]; // an existing entity
    }
  }
  return out + text.slice(last);
}

function escapeStatement(stmt: string, indent: string): string {
  if (NOT_A_LABEL.test(stmt)) return stmt;
  const m = stmt.match(TRANSITION) || stmt.match(NOTE_LINE) || stmt.match(DESCRIPTION);
  if (m) return `${m[1]}:${escapeLabel(m[2], indent)}`;
  // No label: a `;` here ends the statement.  Put what follows on its own
  // line (it may have a label of its own) and drop a trailing terminator.
  const semi = stmt.indexOf(';');
  if (semi === -1) return stmt;
  const head = stmt.slice(0, semi).trimEnd();
  const rest = stmt.slice(semi + 1);
  if (!rest.trim()) return head;
  return head + '\n' + indent + escapeStatement(rest.trimStart(), indent);
}

export function escapeStateLabelText(definition: string): string {
  let inNote = false;
  return definition
    .split('\n')
    .map(line => {
      const t = line.trim();
      if (inNote) {
        if (NOTE_END.test(t)) inNote = false;
        return line;
      }
      if (NOTE_BLOCK_START.test(t)) {
        inNote = true;
        return line;
      }
      return escapeStatement(line, line.match(/^\s*/)![0]);
    })
    .join('\n');
}
