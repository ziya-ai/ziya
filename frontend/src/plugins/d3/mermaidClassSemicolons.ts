/**
 * mermaidClassSemicolons
 *
 * Escapes bare semicolons that appear INSIDE double-quoted label / note /
 * multiplicity strings of a mermaid `classDiagram` so they cannot be mistaken
 * for mermaid's statement separator.
 *
 * WHY THIS EXISTS
 * ---------------
 * In mermaid a `;` is a legitimate statement separator, so
 * `classA --|> classB; classC --|> classD` is two statements on one line and
 * parses fine. But a `;` that is merely part of a relationship-label or note
 * body (e.g. `A --> B : "self-ref ; semi"`) is NOT followed by a valid
 * statement — the tokenizer treats the trailing text as the start of a new
 * statement, fails, and mermaid's classDiagram parser spins. In the render
 * harness that surfaces as a hard 30000ms timeout with ZERO output (verified:
 * `A --> B : "self-ref ; semi"` reliably times out; escaping the `;` renders
 * instantly). This is the classDiagram sibling of the sequenceDiagram
 * semicolon hang already handled by mermaidSequenceSemicolons.ts.
 *
 * THE FIX
 * -------
 * On `classDiagram` specs only, escape any `;` that sits INSIDE a double-quoted
 * string to mermaid's native numeric entity `#59;` (verified: `#59;` renders
 * back to a literal `;` in a classDiagram label — the HTML-style `&#59;` does
 * NOT, it shows up as `&;`). A `;` inside quotes is unambiguously literal text,
 * never a statement separator, so this can NEVER corrupt a correctly-authored
 * multi-statement line: genuine separators live OUTSIDE quotes and are left
 * untouched. Backslash-escaped inner quotes (`\"`) are handled so the quote
 * state stays correct across labels like `"a \"b\" ; c"`.
 *
 * This is a PURE function (no DOM) so it can be unit-tested directly.
 */

/**
 * Return true when `definition` is a mermaid classDiagram (ignoring a leading
 * `%%{init:...}%%` directive and surrounding whitespace).
 */
export function isClassDiagramForSemicolons(definition: string): boolean {
  if (typeof definition !== 'string') return false;
  const withoutInit = definition.replace(/^\s*(?:%%\{[\s\S]*?\}%%\s*)*/, '');
  return /^\s*classDiagram(-v2)?\b/.test(withoutInit);
}

/**
 * Escape bare semicolons inside classDiagram quoted label / note strings.
 *
 * @param definition raw mermaid definition
 * @returns definition with in-quote semicolons rewritten to `#59;`; genuine
 *          statement separators (outside quotes) and non-classDiagram specs are
 *          returned unchanged.
 */
export function escapeClassDiagramLabelSemicolons(definition: string): string {
  if (typeof definition !== 'string' || definition.indexOf(';') === -1) {
    return definition;
  }
  if (!isClassDiagramForSemicolons(definition)) {
    return definition;
  }

  let out = '';
  let inQuote = false;
  for (let i = 0; i < definition.length; i++) {
    const ch = definition[i];

    // Preserve a backslash-escaped quote as literal content; it must not flip
    // the in-quote state (labels may contain `\"`).
    if (ch === '\\' && definition[i + 1] === '"') {
      out += '\\"';
      i++;
      continue;
    }

    if (ch === '"') {
      inQuote = !inQuote;
      out += ch;
      continue;
    }

    // A newline always terminates a quoted string in mermaid's line-oriented
    // grammar; reset defensively so an unbalanced quote can't leak across lines.
    if (ch === '\n') {
      inQuote = false;
      out += ch;
      continue;
    }

    if (ch === ';' && inQuote) {
      // Idempotency: if this `;` already completes a `#59` entity we emitted
      // (or the author typed the escaped form directly), leave it as-is so a
      // second pass over already-escaped text cannot produce `#59#59;`.
      if (out.endsWith('#59')) {
        out += ch;
        continue;
      }
      out += '#59;';
      continue;
    }

    out += ch;
  }
  return out;
}
