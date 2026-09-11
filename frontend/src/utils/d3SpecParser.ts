/**
 * Parse a D3 spec string into a JavaScript object.
 *
 * The spec can be provided in several forms a model realistically emits:
 *   1. Strict JSON:            { "type": "force-directed", ... }
 *   2. JS expression:          ({ type: "force-directed", ... })
 *   3. Markdown-fenced:        ```json\n{ ... }\n``` (optionally with a prose
 *                              line before the fence)
 *   4. JS assignment wrapper:  const chart = { ... };
 *   5. Smart/curly quotes:     { “type”: “bar”, ... }
 *   6. Python repr leakage:    { 'x': True, 'y': None, 'z': False }
 *   7. Semicolon separators:   { "a": 1; "b": 2 }
 *   8. Trailing commas / line & block comments / single-quoted strings.
 *
 * This is the SHARED lenient parse/recovery stage for the JSON-ish D3 engine
 * family (basic-chart, force/d3 charts, music). Before this stage existed a
 * malformed-but-recoverable spec never parsed, no plugin claimed it, and the
 * host retried to a 30s timeout with zero output (D-250). The strict
 * `JSON.parse` fast path is preserved so a well-formed spec is never rewritten.
 */
import JSON5 from 'json5';

/**
 * Strip JS-style comments (line and block) that are NOT inside strings.
 * Operates on the raw text before any JSON/JS parsing. Retained for the
 * strict fast path; the lenient path relies on JSON5 (comment-aware) and on
 * {@link normalizeOutsideStrings} (comment-stripping) instead.
 */
function stripComments(text: string): string {
  // Remove block comments
  let result = text.replace(/\/\*[\s\S]*?\*\//g, '');
  // Remove line comments (only when not inside a string)
  result = result.replace(/(?<!["\w])\/\/.*$/gm, '');
  return result;
}

/**
 * Convert JS object-literal syntax to valid JSON. Legacy fallback used only
 * after strict JSON and JSON5 both fail.
 *
 * Handles:
 *   - Unquoted keys:        key: value  →  "key": value
 *   - Single-quoted strings: 'value'    →  "value"
 *   - Trailing commas:       [1, 2, ]   →  [1, 2]
 */
function jsObjectToJson(text: string): string {
  let result = text;

  // Quote unquoted keys while skipping content inside double-quoted strings.
  result = result.replace(
    /"(?:[^"\\]|\\.)*"|([A-Za-z_$][\w$]*)\s*:/g,
    (match, key) => {
      if (key === undefined) {
        // Matched a double-quoted string — return it unchanged
        return match;
      }
      // Matched an unquoted key — wrap it in double quotes
      return `"${key}":`;
    }
  );

  // Convert single-quoted strings to double-quoted
  result = result.replace(
    /'([^'\\]*(?:\\.[^'\\]*)*)'/g,
    '"$1"'
  );

  // Remove trailing commas before } or ]
  result = result.replace(/,\s*([}\]])/g, '$1');

  return result;
}

/**
 * Strip a leading/trailing markdown code fence (```json / ```chart / ```d3 /
 * bare ```), tolerating a matched pair or a stray leading/trailing fence.
 * Any prose surrounding the fence is left in place for the brace-slice in
 * {@link lenientParse} to discard.
 */
export function stripDefinitionFence(raw: string): string {
  let t = String(raw).trim();
  const matched = /^```[a-zA-Z0-9_-]*\s*\n?([\s\S]*?)\n?```$/.exec(t);
  if (matched) return matched[1].trim();
  t = t.replace(/^```[a-zA-Z0-9_-]*\s*/, '').replace(/```\s*$/, '');
  return t.trim();
}

/**
 * Normalise smart/curly quotes to ASCII so a copy-pasted payload parses.
 * Neither JSON.parse nor JSON5 accept U+201C/U+201D/U+2018/U+2019.
 */
export function normalizeSmartQuotes(raw: string): string {
  return String(raw)
    .replace(/[\u201C\u201D\u201E\u201F]/g, '"')
    .replace(/[\u2018\u2019\u201A\u201B]/g, "'");
}

/**
 * Single-pass rewrite that touches only content OUTSIDE string literals:
 *   - Python repr literals  True/False/None  → true/false/null
 *   - Semicolon separators  ;                → ,
 *   - Line (//) and block (/* *​/) comments   → removed
 *
 * String literals (single- OR double-quoted, with escapes) are copied
 * verbatim, so a value like "don't; True" or 'None' is never rewritten. This
 * runs only on the lenient path after strict JSON and plain JSON5 have failed,
 * and its output is still handed to JSON5 (so single quotes / unquoted keys /
 * trailing commas it leaves untouched are handled there). Pure/testable.
 */
export function normalizeOutsideStrings(text: string): string {
  let out = '';
  let i = 0;
  const n = text.length;
  let quote: string | null = null;
  while (i < n) {
    const ch = text[i];
    if (quote) {
      out += ch;
      if (ch === '\\' && i + 1 < n) {
        out += text[i + 1];
        i += 2;
        continue;
      }
      if (ch === quote) quote = null;
      i++;
      continue;
    }
    // Not inside a string.
    if (ch === '"' || ch === "'") {
      quote = ch;
      out += ch;
      i++;
      continue;
    }
    // Strip comments (defensive: JSON5 also handles them, but a comment may
    // contain a lone quote that would otherwise desync this scanner).
    if (ch === '/' && text[i + 1] === '/') {
      i += 2;
      while (i < n && text[i] !== '\n') i++;
      continue;
    }
    if (ch === '/' && text[i + 1] === '*') {
      i += 2;
      while (i < n && !(text[i] === '*' && text[i + 1] === '/')) i++;
      i += 2;
      continue;
    }
    if (ch === ';') {
      // Statement-separator slip → JSON member separator.
      out += ',';
      i++;
      continue;
    }
    // Python repr literals as bare tokens.
    if (/[A-Za-z_$]/.test(ch)) {
      const rest = text.slice(i);
      const lit = /^(True|False|None)\b/.exec(rest);
      if (lit) {
        out += lit[1] === 'True' ? 'true' : lit[1] === 'False' ? 'false' : 'null';
        i += lit[1].length;
        continue;
      }
      // Consume the whole identifier so we don't re-scan its interior chars.
      const id = /^[A-Za-z_$][\w$]*/.exec(rest)![0];
      out += id;
      i += id.length;
      continue;
    }
    out += ch;
    i++;
  }
  return out;
}

/**
 * Lenient parse of a JSON-ish spec string. Order of attempts, most-faithful
 * first, so a well-formed spec is parsed byte-identically and never mutated:
 *   0. fence strip + smart-quote fold + slice to the outermost {...}
 *   1. strict JSON.parse
 *   2. JSON5.parse            (unquoted keys, single quotes, trailing commas, comments)
 *   3. JSON5.parse(normalizeOutsideStrings(...))  (Python literals, semicolons)
 *   4. legacy jsObjectToJson  (last-resort regex rewrite)
 * Returns the parsed value, or null when unrecoverable. Pure/testable.
 */
export function lenientParse(raw: string): any | null {
  let text = normalizeSmartQuotes(stripDefinitionFence(String(raw).trim())).trim();
  if (!text) return null;

  // Slice to the outermost {...} so a prose prefix ("Here is the chart:"), a
  // JS assignment wrapper ("const chart = "), surrounding parentheses and a
  // trailing ";" are all discarded before parsing.
  const first = text.indexOf('{');
  const last = text.lastIndexOf('}');
  if (first !== -1 && last !== -1 && last > first) {
    text = text.slice(first, last + 1);
  }

  try {
    return JSON.parse(text);
  } catch (_) {
    /* fall through */
  }
  try {
    return JSON5.parse(text);
  } catch (_) {
    /* fall through */
  }
  try {
    return JSON5.parse(normalizeOutsideStrings(text));
  } catch (_) {
    /* fall through */
  }
  try {
    return JSON.parse(jsObjectToJson(text));
  } catch (_) {
    return null;
  }
}

/**
 * Parse a raw D3 spec string into an object.
 * Returns the parsed object on success, or null if parsing fails.
 */
export function parseD3Spec(raw: string): any | null {
  if (!raw || typeof raw !== 'string') return null;

  let text = raw.trim();

  // Strip outer parentheses: ({ ... }) → { ... }
  if (text.startsWith('(') && text.endsWith(')')) {
    text = text.slice(1, -1).trim();
  }

  // Strict fast path (comments stripped) — leaves a well-formed spec untouched.
  try {
    return JSON.parse(stripComments(text));
  } catch (_) {
    // Fall through to the shared lenient recovery stage.
  }

  return lenientParse(text);
}
