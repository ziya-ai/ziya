/**
 * mermaidSequenceSemicolons
 *
 * Escapes bare semicolons that appear inside sequenceDiagram MESSAGE / NOTE
 * text so they cannot be mistaken for mermaid's statement separator.
 *
 * WHY THIS EXISTS
 * ---------------
 * In mermaid's sequence grammar a `;` is a legitimate statement separator, so
 * `A->>B: m1 ; A->>B: m2` is two statements on one line and parses fine. But a
 * `;` that is merely part of a message body (`A->>B: a ; b`) is NOT followed by
 * a valid statement, so the tokenizer treats `b` as the start of a new
 * statement, fails to find an arrow, and mermaid throws
 *   "Parse error ... Expecting 'SOLID_ARROW' ... got 'NEWLINE'".
 * In the render harness that bounded parse error surfaces as a full 30s render
 * timeout with no SVG ever mounted (verified: bare `A->>B: a ; b` reliably
 * times out; the same line with `;` removed renders instantly).
 *
 * An LLM emits prose semicolons in message text constantly, so this is a whole
 * class of malformed-but-well-intentioned input, not a one-off.
 *
 * THE FIX
 * -------
 * On sequenceDiagram specs only, walk each message/note line's text (the part
 * after the FIRST colon) and replace a `;` with the HTML entity `&#59;` UNLESS
 * the remainder after that `;` looks like a genuine mermaid statement (an arrow
 * message such as `A->>B: ...`, or a block keyword such as `activate`, `loop`,
 * `alt`, `note`, ...). `&#59;` renders back to a literal `;` in the SVG, so the
 * visible output is unchanged. A genuine `X ; Y->>Z: msg` separator is left
 * intact, so this cannot corrupt correctly-authored multi-statement lines.
 *
 * This is a PURE function (no DOM) so it can be unit-tested directly.
 */

// Tokens that legitimately START a mermaid sequence statement. If the text
// immediately after a `;` matches one of these, the `;` is a real statement
// separator and must be preserved. Anything else is prose and gets escaped.
const STATEMENT_AFTER_SEMICOLON = new RegExp(
  '^\\s*(?:' +
    // an arrow message: "<participant> ->> <participant> :" (any arrow variant)
    // or an arrow with no trailing colon on this segment.
    '[^:;\\n]*?(?:->>|--?>>|--?x|-x|--?\\)|-\\)|->|-->|<<->>|<<-->>)' +
    '|' +
    // a block / control keyword that opens or closes a construct.
    '(?:activate|deactivate|note|loop|alt|else|opt|par|and|critical|option|' +
    'break|rect|end|participant|actor|autonumber|destroy|box|link|links|title)\\b' +
  ')',
  'i',
);

/**
 * Return true when `definition` is a mermaid sequenceDiagram (ignoring a
 * leading `%%{init:...}%%` directive and surrounding whitespace).
 */
export function isSequenceDiagram(definition: string): boolean {
  if (typeof definition !== 'string') return false;
  // Strip any leading %%{ ... }%% init directives, then leading blank lines.
  const withoutInit = definition.replace(/^\s*(?:%%\{[\s\S]*?\}%%\s*)*/, '');
  return /^\s*sequenceDiagram\b/.test(withoutInit);
}

/**
 * Escape bare semicolons inside sequenceDiagram message/note text.
 *
 * @param definition raw mermaid definition
 * @returns definition with prose semicolons in sequence message text escaped to
 *          `&#59;`; genuine statement separators and non-sequence diagrams are
 *          returned unchanged.
 */
export function escapeSequenceMessageSemicolons(definition: string): string {
  if (typeof definition !== 'string' || definition.indexOf(';') === -1) {
    return definition;
  }
  if (!isSequenceDiagram(definition)) {
    return definition;
  }

  return definition
    .split('\n')
    .map((line) => {
      if (line.indexOf(';') === -1) return line;

      // Only message/note lines carry free text, and that text lives after the
      // FIRST colon (e.g. "A->>B: <text>", "Note over A,B: <text>"). Lines with
      // no colon (participant decls, block openers, activate/deactivate) never
      // contain prose semicolons we need to touch.
      const colon = line.indexOf(':');
      if (colon === -1) return line;

      const head = line.slice(0, colon + 1);
      const body = line.slice(colon + 1);

      let out = '';
      for (let i = 0; i < body.length; i++) {
        const ch = body[i];
        if (ch === ';') {
          const rest = body.slice(i + 1);
          // Keep the `;` only if what follows starts a real statement.
          out += STATEMENT_AFTER_SEMICOLON.test(rest) ? ';' : '&#59;';
        } else {
          out += ch;
        }
      }
      return head + out;
    })
    .join('\n');
}
