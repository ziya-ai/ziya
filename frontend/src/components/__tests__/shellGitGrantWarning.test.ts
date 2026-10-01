/**
 * The shell-config modal must warn about explicit `git <sub>` grants
 * (design/git-tool.md step 6: warn and redirect).
 *
 * The hazard catalogue lives in ONE place, app/utils/git_grant_hazards.py,
 * and reaches the modal through POST /api/mcp/git-grant-hazards.  These
 * checks read the modal source (mounting it needs antd + a live config
 * fetch): the failure they guard is structural — a catalogue duplicated in
 * TS drifting from the backend, or a warning computed but never rendered.
 */

import fs from 'fs';
import path from 'path';

const MODAL = fs.readFileSync(path.join(__dirname, '..', 'ShellConfigModal.tsx'), 'utf8');

describe('ShellConfigModal git grant warning', () => {
  it('asks the backend, re-asking whenever the allowlist changes', () => {
    expect(MODAL).toContain("'/api/mcp/git-grant-hazards'");
    expect(MODAL).toMatch(/config\?\.allowedCommands[^\n]*join/);
  });

  it('renders each hazard and the tool redirect', () => {
    expect(MODAL).toMatch(/gitHazards\.map\(/);
    expect(MODAL).toMatch(/h\.hazards\.map\(/);
    expect(MODAL).toMatch(/h\.alternative/);
  });

  it('does not keep its own copy of the catalogue', () => {
    // A TS list of destructive git forms would drift from the backend.
    expect(MODAL).not.toMatch(/--discard-changes|reset --hard.*discards/);
  });
});
