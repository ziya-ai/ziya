/**
 * Parse a D3 spec string into a JavaScript object.
 *
 * The spec can be provided in two forms:
 *   1. Strict JSON:  { "type": "force-directed", ... }
 *   2. JS expression: ({ type: "force-directed", ... })
 *
 * JS-expression form uses unquoted keys and may include comments or
 * trailing commas — none of which JSON.parse tolerates.  This utility
 * normalizes both formats into a plain object.
 */

/**
 * Strip JS-style comments (line and block) that are NOT inside strings.
 * Operates on the raw text before any JSON/JS parsing.
 */
function stripComments(text: string): string {
  // Remove block comments
  let result = text.replace(/\/\*[\s\S]*?\*\//g, '');
  // Remove line comments (only when not inside a string)
  result = result.replace(/(?<!["\w])\/\/.*$/gm, '');
  return result;
}

/**
 * Convert JS object-literal syntax to valid JSON.
 *
 * Handles:
 *   - Unquoted keys:        key: value  →  "key": value
 *   - Single-quoted strings: 'value'    →  "value"
 *   - Trailing commas:       [1, 2, ]   →  [1, 2]
 */
function jsObjectToJson(text: string): string {
  let result = text;

  // Quote unquoted keys while skipping content inside double-quoted strings.
  // A simple lookbehind can't reliably distinguish keys from words inside
  // string values (e.g. "Multiverse: Age" has "Multiverse:" that looks like
  // a key).  Instead, we consume strings as opaque tokens and only apply
  // key-quoting to non-string segments.
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

  // Strip comments
  text = stripComments(text);

  // Try strict JSON first (fast path)
  try {
    return JSON.parse(text);
  } catch (_) {
    // Fall through to JS-expression handling
  }

  // Convert JS object literal syntax to JSON and retry
  try {
    const json = jsObjectToJson(text);
    return JSON.parse(json);
  } catch (_) {
    return null;
  }
}
