/**
 * The warning marker on a shell-command tag must explain itself, must flag
 * the command WORD rather than any substring, and must agree with the
 * backend list of path-gated commands.  A new user read the unexplained red
 * triangle on `cp` as "cp is blocked"; it actually means "cp may only write
 * to safe write paths".
 */
import * as fs from 'fs';
import * as path from 'path';

const MODAL = fs.readFileSync(path.join(__dirname, '..', 'ShellConfigModal.tsx'), 'utf8');
const WRITE_POLICY = fs.readFileSync(
  path.join(__dirname, '..', '..', '..', '..', 'app', 'config', 'write_policy.py'),
  'utf8',
);

const quoted = (body: string) =>
  body.split(',').map(s => s.trim().replace(/^['"]|['"]$/g, '')).filter(Boolean).sort();

describe('ShellConfigModal command caution marker', () => {
  it('explains the warning icon with a tooltip', () => {
    expect(MODAL).toMatch(/<Tooltip title=\{commandCaution\(command\)\}>\s*<WarningOutlined/);
  });

  it('matches the command word, not a substring', () => {
    expect(MODAL).not.toMatch(/command\.toLowerCase\(\)\.includes\(/);
    expect(MODAL).toMatch(/const word = command\.trim\(\)\.split\(\/\\s\+\/\)\[0\]/);
  });

  it('path-gated list mirrors DEFAULT_WRITE_POLICY.destructive_commands', () => {
    const ui = MODAL.match(/const pathGatedCommands = \[([^\]]*)\]/);
    const py = WRITE_POLICY.match(/"destructive_commands":\s*\[([^\]]*)\]/);
    expect(ui).not.toBeNull();
    expect(py).not.toBeNull();
    expect(quoted(ui![1])).toEqual(quoted(py![1]));
  });
});
