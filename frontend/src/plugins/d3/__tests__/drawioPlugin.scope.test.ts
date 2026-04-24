import * as ts from 'typescript';
import * as path from 'path';
import * as fs from 'fs';

// Regression guard: catch undefined-variable-in-closure slips before runtime.
//
// Context: drawioPlugin.ts is ~3000 lines of deeply nested callbacks with
// multiple inner scopes that each declare their own `isHorizontal` /
// `isHorizontalDominant` local. A reference to `isHorizontal` in one scope
// that relies on a declaration in a sibling scope will compile under some
// looser tsconfigs but throws `ReferenceError: isHorizontal is not defined`
// at runtime. Bugs in this family have shipped to users because no static
// check exercises the plugin file with strict name resolution.
//
// This test compiles drawioPlugin.ts with the project's tsconfig options
// and asserts that there are zero "Cannot find name" diagnostics. It is
// intentionally narrow: it doesn't try to be a general lint, it just closes
// the specific loophole that let `isHorizontal is not defined` escape.

describe('drawioPlugin.ts name resolution', () => {
  const pluginPath = path.resolve(
    __dirname,
    '..',
    'drawioPlugin.ts'
  );

  it('exists at the expected path', () => {
    expect(fs.existsSync(pluginPath)).toBe(true);
  });

  it('has no "Cannot find name" diagnostics under strict compilation', () => {
    const program = ts.createProgram([pluginPath], {
      target: ts.ScriptTarget.ES2015,
      module: ts.ModuleKind.ESNext,
      moduleResolution: ts.ModuleResolutionKind.Bundler,
      strict: true,
      noImplicitAny: false,
      allowJs: true,
      esModuleInterop: true,
      skipLibCheck: true,
      isolatedModules: true,
      noEmit: true,
      jsx: ts.JsxEmit.Preserve,
      lib: ['lib.dom.d.ts', 'lib.dom.iterable.d.ts', 'lib.esnext.d.ts'],
    });

    const diagnostics = ts
      .getPreEmitDiagnostics(program)
      .filter(d => d.file && d.file.fileName.endsWith('drawioPlugin.ts'));

    // Narrow to the class of error we care about: undeclared identifiers.
    // TS2304 = "Cannot find name 'X'."
    // TS2448 = "Block-scoped variable 'X' used before its declaration."
    // TS2454 = "Variable 'X' is used before being assigned."
    const undefinedIdentifierDiags = diagnostics.filter(d =>
      [2304, 2448, 2454].includes(d.code)
    );

    if (undefinedIdentifierDiags.length > 0) {
      const rendered = undefinedIdentifierDiags.map(d => {
        const { line, character } =
          d.file!.getLineAndCharacterOfPosition(d.start!);
        const msg = ts.flattenDiagnosticMessageText(d.messageText, '\n');
        return `${d.file!.fileName}:${line + 1}:${character + 1}  TS${d.code}: ${msg}`;
      });
      throw new Error(
        'drawioPlugin.ts has unresolved identifier references ' +
          '(likely a variable used outside its declaring scope):\n' +
          rendered.join('\n')
      );
    }

    expect(undefinedIdentifierDiags).toHaveLength(0);
  });
});
