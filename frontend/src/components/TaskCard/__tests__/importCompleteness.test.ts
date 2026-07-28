/**
 * Static guard: every capitalized JSX element a component renders must
 * be imported or declared in that same file.
 *
 * Why this exists: `ArtifactViewer` was wired into TaskCardInlineTile's
 * render tree while its `import` statement was missing.  Nothing caught
 * it —
 *   - Jest passed, because the component's own test imports it directly
 *     and the tile's test only asserts the *module* imports (a missing
 *     import is a ReferenceError at JSX *evaluation*, not at load).
 *   - No type-check runs in the test suite.
 * The result compiles and imports fine, then throws the moment a user
 * expands a run tile that has output artifacts.
 *
 * This is a source-level scan rather than a render test on purpose: it
 * covers every component in the directory at once, including render
 * paths that need heavy mocking (or specific run state) to reach, which
 * is exactly where an unimported identifier hides.
 */

import * as fs from 'fs';
import * as path from 'path';

const DIR = path.resolve(__dirname, '..');

/** HTML/SVG intrinsics and React built-ins that need no import. */
const INTRINSICS = new Set([
  'React', 'Fragment',
]);

/**
 * Identifiers used as JSX elements: `<Foo`, `<Foo.Bar`, `</Foo>`.
 * Only capitalized names matter — lowercase is an HTML intrinsic.
 *
 * The hard part is telling a JSX element from a GENERIC TYPE ARGUMENT:
 * `React.FC<Props>`, `useRef<HTMLDivElement>` and `useState<TabKey>` all
 * contain `<Capitalized`, but bind nothing at runtime and must not be
 * required to be a component.  The distinguishing feature is the
 * character immediately before `<`: a generic argument always follows an
 * identifier/`>`/`)` character, whereas JSX only ever appears at the
 * start of an expression — after `(`, `{`, `=`, `,`, `:`, `?`, `&&`,
 * `||`, `return`, `>` (a sibling's close), or the start of a line.
 */
function jsxComponentNames(src: string): Set<string> {
  const names = new Set<string>();
  // Group 1 = the allowed preceding context, group 2 = the element name.
  const re = /(^|[(){}[\]=,:?]|&&|\|\||=>|\breturn\b|>|\n\s*)\s*<\/?([A-Z][A-Za-z0-9_]*)(?:\.[A-Za-z0-9_]+)*[\s/>]/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(src)) !== null) names.add(m[2]);
  return names;
}

/**
 * Names bound in the module: imports (default, named, namespace) plus
 * local const/let/function/class declarations.
 */
function boundNames(src: string): Set<string> {
  const bound = new Set<string>();

  // import X from '...' / import X, { A, B } from '...' / import * as X
  const importRe = /import\s+(?:type\s+)?([^;]*?)\s+from\s+['"][^'"]+['"]/g;
  let m: RegExpExecArray | null;
  while ((m = importRe.exec(src)) !== null) {
    const clause = m[1];
    // Namespace: * as Foo
    const ns = clause.match(/\*\s+as\s+([A-Za-z0-9_$]+)/);
    if (ns) bound.add(ns[1]);
    // Named: { A, B as C }
    const named = clause.match(/\{([^}]*)\}/);
    if (named) {
      for (const raw of named[1].split(',')) {
        const piece = raw.trim();
        if (!piece) continue;
        const alias = piece.split(/\s+as\s+/);
        bound.add((alias[1] ?? alias[0]).trim());
      }
    }
    // Default: leading bare identifier before any brace
    const dflt = clause.replace(/\{[^}]*\}/g, '').split(',')[0].trim();
    if (dflt && /^[A-Za-z0-9_$]+$/.test(dflt)) bound.add(dflt);
  }

  // Local declarations
  const declRe = /(?:^|\n)\s*(?:export\s+)?(?:const|let|var|function|class)\s+([A-Za-z0-9_$]+)/g;
  while ((m = declRe.exec(src)) !== null) bound.add(m[1]);

  return bound;
}

function componentFiles(): string[] {
  return fs.readdirSync(DIR)
    .filter(f => f.endsWith('.tsx'))
    .map(f => path.join(DIR, f));
}

describe('TaskCard components — JSX identifiers are all bound', () => {
  const files = componentFiles();

  it('finds component files to check', () => {
    expect(files.length).toBeGreaterThan(0);
  });

  it.each(files.map(f => [path.basename(f), f]))(
    '%s references no unbound JSX component',
    (_name, file) => {
      const src = fs.readFileSync(file as string, 'utf8');
      const used = jsxComponentNames(src);
      const bound = boundNames(src);
      const missing = [...used].filter(
        n => !bound.has(n) && !INTRINSICS.has(n),
      );
      expect(missing).toEqual([]);
    },
  );
});

describe('scanner self-tests — guards against a vacuous pass', () => {
  it('detects a JSX component that is not imported', () => {
    const src = `
      import React from 'react';
      export const A = () => <Missing prop={1} />;
    `;
    const used = jsxComponentNames(src);
    const bound = boundNames(src);
    expect(used.has('Missing')).toBe(true);
    expect(bound.has('Missing')).toBe(false);
  });

  it('accepts a named import', () => {
    const src = `
      import { Present } from './x';
      export const A = () => <Present />;
    `;
    expect(boundNames(src).has('Present')).toBe(true);
  });

  it('accepts an aliased import', () => {
    const src = `
      import { Orig as Alias } from './x';
      export const A = () => <Alias />;
    `;
    const bound = boundNames(src);
    expect(bound.has('Alias')).toBe(true);
  });

  it('accepts a default import', () => {
    const src = `
      import Deflt from './x';
      export const A = () => <Deflt />;
    `;
    expect(boundNames(src).has('Deflt')).toBe(true);
  });

  it('accepts a locally declared component', () => {
    const src = `
      const Local = () => null;
      export const A = () => <Local />;
    `;
    expect(boundNames(src).has('Local')).toBe(true);
  });

  it('ignores lowercase HTML intrinsics', () => {
    const src = `export const A = () => <div><span /></div>;`;
    expect(jsxComponentNames(src).size).toBe(0);
  });

  it('picks up dotted member elements by their root object', () => {
    const src = `
      import { Input } from 'antd';
      export const A = () => <Input.TextArea />;
    `;
    const used = jsxComponentNames(src);
    expect(used.has('Input')).toBe(true);
    expect(boundNames(src).has('Input')).toBe(true);
  });

  it('recognizes React.Fragment shorthand usage', () => {
    const src = `
      import React from 'react';
      export const A = () => <React.Fragment><i /></React.Fragment>;
    `;
    const used = jsxComponentNames(src);
    const bound = boundNames(src);
    expect([...used].filter(n => !bound.has(n))).toEqual([]);
  });

  // ── Generic type arguments must NOT be mistaken for JSX ──────────
  // These are the false positives that made the first version of this
  // scanner unusable; each is a type position, binding nothing.

  it('ignores a React.FC generic type argument', () => {
    const src = `const A: React.FC<Props> = () => null;`;
    expect(jsxComponentNames(src).has('Props')).toBe(false);
  });

  it('ignores useRef / useState generic arguments', () => {
    const src = `
      const r = useRef<HTMLTextAreaElement>(null);
      const [t, setT] = useState<TabKey>('a');
    `;
    const used = jsxComponentNames(src);
    expect(used.has('HTMLTextAreaElement')).toBe(false);
    expect(used.has('TabKey')).toBe(false);
  });

  it('ignores a generic in a function signature', () => {
    const src = `function pick(x: Map<Thing, Other>): Set<Thing> { return x; }`;
    const used = jsxComponentNames(src);
    expect(used.has('Thing')).toBe(false);
    expect(used.has('Other')).toBe(false);
  });

  // ── …while still catching JSX in every position it can appear ────

  it('catches JSX returned directly from an arrow', () => {
    expect(jsxComponentNames(`const A = () => <Gone />;`).has('Gone')).toBe(true);
  });

  it('catches JSX after a return keyword', () => {
    expect(jsxComponentNames(`function A() { return <Gone />; }`).has('Gone')).toBe(true);
  });

  it('catches JSX inside a brace expression', () => {
    const src = `const A = () => <div>{cond && <Gone />}</div>;`;
    expect(jsxComponentNames(src).has('Gone')).toBe(true);
  });

  it('catches JSX in a ternary branch', () => {
    const src = `const A = () => (cond ? <Gone /> : <Other />);`;
    const used = jsxComponentNames(src);
    expect(used.has('Gone')).toBe(true);
    expect(used.has('Other')).toBe(true);
  });

  it('catches a nested child element after a sibling close', () => {
    const src = `const A = () => <div><First /><Gone /></div>;`;
    expect(jsxComponentNames(src).has('Gone')).toBe(true);
  });

  it('catches JSX passed as a prop value', () => {
    const src = `const A = () => <Host icon={<Gone />} />;`;
    expect(jsxComponentNames(src).has('Gone')).toBe(true);
  });

  it('reproduces the original ArtifactViewer bug shape end-to-end', () => {
    // The exact failure this file exists to prevent: rendered in the
    // tree, absent from the imports.
    const src = `
      import React from 'react';
      import { TaskRunMap } from './TaskRunMap';
      export const Tile = () => (
        <div>
          <TaskRunMap />
          <ArtifactViewer parts={x} projectId={p} runId={r} />
        </div>
      );
    `;
    const used = jsxComponentNames(src);
    const bound = boundNames(src);
    const missing = [...used].filter(n => !bound.has(n) && !INTRINSICS.has(n));
    expect(missing).toEqual(['ArtifactViewer']);
  });
});
