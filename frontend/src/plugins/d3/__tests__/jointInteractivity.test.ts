import * as fs from 'fs';
import * as path from 'path';

// Regression guard for: "LinkView: invalid target cell." escaping to the root
// error boundary on a plain pointerdown inside a rendered joint diagram.
//
// Root cause was a two-part interaction that no runtime test covered: every
// element factory marks attrs.body.magnet = true (making the body a valid
// link-drag source), and dia.Paper was constructed with a blanket
// `interactive: true`, which enables the addLinkFromMagnet feature. A
// pointerdown then synchronously added a link cell to the graph, and the view
// flush that followed could throw from LinkView.checkEndModel.
//
// These are read-only diagrams in a chat message, so the fix disables the
// link-authoring feature rather than trying to make it correct. This is a
// source contract test: reproducing the crash needs real pointer events
// against a live JointJS paper, which jsdom cannot deliver, so we assert on
// the paper configuration that gates the whole code path.

/**
 * Strip comments before matching.
 *
 * A source-contract guard that reads comments as code is actively harmful: the
 * fix for this bug documents the old `interactive: true` option in a comment
 * explaining why it was removed, which made the negative assertions below fail
 * against a correctly-fixed file. Worse than the false positive is the inverse —
 * a guard that can be satisfied or broken by prose is not measuring the code
 * state at all.
 *
 * Block comments are removed first, then whole-line `//` and continuation `*`
 * lines. Deliberately not a real parser: string literals containing `//` (URLs)
 * would confuse a naive inline-comment strip, so inline trailing comments are
 * left alone. No assertion here depends on removing those.
 */
function stripComments(src: string): string {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .split('\n')
    .filter(line => !/^\s*(\/\/|\*)/.test(line))
    .join('\n');
}

describe('jointPlugin paper interactivity', () => {
  const pluginPath = path.resolve(__dirname, '..', 'jointPlugin.ts');
  let source: string;
  let code: string;

  beforeAll(() => {
    source = fs.readFileSync(pluginPath, 'utf8');
    code = stripComments(source);
  });

  it('exists at the expected path', () => {
    expect(fs.existsSync(pluginPath)).toBe(true);
  });

  it('stripComments removes commented-out config without touching real config', () => {
    const sample = [
      '// interactive: true,',
      '/* interactive: spec.interactive !== false */',
      'interactive: { addLinkFromMagnet: false },',
    ].join('\n');
    const stripped = stripComments(sample);
    expect(stripped).not.toMatch(/interactive:\s*true\b/);
    expect(stripped).not.toMatch(/spec\.interactive\s*!==\s*false/);
    expect(stripped).toMatch(/addLinkFromMagnet:\s*false/);
  });

  it('constructs exactly one dia.Paper', () => {
    const matches = code.match(/new dia\.Paper\(/g) ?? [];
    expect(matches).toHaveLength(1);
  });

  it('never passes a blanket truthy `interactive` to dia.Paper', () => {
    // The original defect, verbatim. If this reappears the crash returns.
    expect(code).not.toMatch(/interactive:\s*spec\.interactive\s*!==\s*false/);
    expect(code).not.toMatch(/interactive:\s*true\b/);
  });

  it('disables addLinkFromMagnet in the paper options', () => {
    expect(code).toMatch(/addLinkFromMagnet:\s*false/);
  });

  it('preserves JointJS\'s labelMove: false default when using the object form', () => {
    // The object form of `interactive` fully replaces the library default
    // ({ labelMove: false }), so it has to be restated or label dragging
    // silently becomes enabled.
    expect(code).toMatch(/labelMove:\s*false/);
  });

  it('still honours an explicit spec.interactive === false as fully non-interactive', () => {
    expect(code).toMatch(/spec\.interactive\s*===\s*false/);
  });

  it('element bodies are still magnets (links render from declared connections)', () => {
    // Guards against "fixing" this by stripping magnets instead, which would
    // also be a behaviour change for connection anchoring.
    expect(code).toMatch(/magnet:\s*true/);
  });

  // Self-test: the assertions above must be capable of failing, so a future
  // refactor that renames the option cannot leave a green but vacuous suite.
  it('the guard detects the original defective configuration', () => {
    const defective = `
      const paper = new dia.Paper({
        interactive: spec.interactive !== false,
        snapLinks: { radius: 30 },
      });
    `;
    expect(defective).toMatch(/interactive:\s*spec\.interactive\s*!==\s*false/);
    expect(defective).not.toMatch(/addLinkFromMagnet:\s*false/);
  });
});
