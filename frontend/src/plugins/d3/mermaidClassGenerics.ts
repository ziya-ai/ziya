/**
 * mermaidClassGenerics
 *
 * Flattens NESTED / unbalanced generic tilde delimiters in mermaid
 * `classDiagram` class-name and type tokens so they cannot hang the parser.
 *
 * WHY THIS EXISTS
 * ---------------
 * Mermaid encodes a generic type parameter with a SINGLE, non-nested pair of
 * tildes: `ClassName~Type~` renders as `ClassName<Type>`. The grammar has no
 * concept of a nested generic, so a class token that contains nested tildes
 * such as `Repo~Comparable~K~~` (the author's intent being
 * `Repo<Comparable<K>>`) drives mermaid's class-diagram parser/lexer into an
 * unbounded parse: the render never produces an SVG and the harness spins to
 * the 30000ms timeout with ZERO output (verified: `class Repo~Comparable~K~~`
 * reliably times out; the same class rewritten to a single-level generic
 * `Repo~Comparable K~` renders instantly as `Repo<Comparable K>`).
 *
 * This is the same UNBOUNDED-WORK-on-malformed-input class as the graphviz
 * `minlen=1e6` and plotly `nbinsy=1e9` defects, but triggered by malformed
 * STRUCTURE (nested delimiters) rather than magnitude. An LLM emits nested
 * generics constantly (they are valid in Java/C#/TS), so this is a whole
 * family of malformed-but-well-intentioned input, not a one-off.
 *
 * THE FIX
 * -------
 * On `classDiagram` specs only, find every class/type token of the shape
 * `Identifier~ ...one-or-more tilde segments... ~` and, when the generic body
 * between the outer tildes still contains interior tildes, collapse it to a
 * single balanced generic: keep the outer `~...~`, replace every interior `~`
 * with a space, and collapse whitespace. A WELL-FORMED single-level generic
 * (`Foo~T~`, `Map~K, V~`) has no interior tildes and is returned BYTE-IDENTICAL
 * — this is a gap-fill for the nested case, not a catch-all rewrite.
 *
 * Generic content is matched excluding the relationship-arrow / structural
 * characters `< > | : { } "` so a line carrying TWO class refs plus an arrow
 * (`IComparable~T~ <|.. Repository~K extends Comparable~K~, V~`) is split at
 * the arrow instead of swallowing it — each generic is flattened independently
 * and the relationship survives.
 *
 * This is a PURE function (no DOM) so it can be unit-tested directly.
 */

// Identifier: a letter / underscore / any non-ASCII (unicode class names are
// legal in mermaid) followed by word chars or non-ASCII. Digits alone are not
// a class name, but a name may CONTAIN digits after the first char.
// Generic body: any run of characters that are NOT a tilde and NOT one of the
// relationship-arrow / structural delimiters. Spaces and commas ARE allowed so
// multi-word / multi-param generics (`K extends Comparable, V`) are captured.
const CLASS_GENERIC_RE =
  /([A-Za-z_\u00C0-\uFFFF][\w\u00C0-\uFFFF]*)((?:~[^~<>|:{}"\n]*)+~)/g;

/**
 * Return true when `definition` is a mermaid classDiagram (ignoring a leading
 * `%%{init:...}%%` directive and surrounding whitespace).
 */
export function isClassDiagram(definition: string): boolean {
  if (typeof definition !== 'string') return false;
  const withoutInit = definition.replace(/^\s*(?:%%\{[\s\S]*?\}%%\s*)*/, '');
  return /^\s*classDiagram(-v2)?\b/.test(withoutInit);
}

/**
 * Collapse one matched generic body to a single balanced level.
 *
 * @param body the matched generic INCLUDING its outer tildes, e.g.
 *             "~K extends Comparable~K~, V~"
 * @returns the flattened generic (still tilde-delimited), or the original body
 *          unchanged when it is already a well-formed single-level generic.
 */
function flattenGenericBody(body: string): string {
  // Strip the guaranteed leading and trailing tilde.
  const inner = body.slice(1, -1);
  if (inner.indexOf('~') === -1) {
    // Already a single-level generic (`~T~`, `~K, V~`) — leave byte-identical.
    return body;
  }
  const flattened = inner.replace(/~/g, ' ').replace(/\s+/g, ' ').trim();
  return `~${flattened}~`;
}

/**
 * Flatten nested / unbalanced class-name and type generics in a mermaid
 * classDiagram definition.
 *
 * @param definition raw mermaid definition
 * @returns definition with nested class generics collapsed to a single balanced
 *          level; well-formed single-level generics and non-classDiagram specs
 *          are returned unchanged.
 */
export function flattenNestedClassGenerics(definition: string): string {
  if (typeof definition !== 'string' || definition.indexOf('~') === -1) {
    return definition;
  }
  if (!isClassDiagram(definition)) {
    return definition;
  }

  return definition.replace(
    CLASS_GENERIC_RE,
    (_match: string, ident: string, body: string) =>
      `${ident}${flattenGenericBody(body)}`,
  );
}
