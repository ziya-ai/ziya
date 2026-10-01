/**
 * The Manage Projects "Git tool" control must round-trip settings.toolModes
 * (design/git-tool.md step 5; app/utils/project_tool_modes.py).
 *
 * Source-reading, like templateEntryPointAffordance.test.ts: mounting this
 * modal needs ProjectContext, ThemeContext, ActiveChatContext and antd.  The
 * failure this guards is structural — a control that renders and updates
 * local state but is never loaded from, or never included in, the PUT — so
 * reading the structure is the honest test.  Anchored on identifiers, not
 * line positions.
 */

import fs from 'fs';
import path from 'path';

const MODAL = fs.readFileSync(
  path.join(__dirname, '..', '..', 'components', 'ProjectManagerModal.tsx'), 'utf8');

/** The JSON.stringify({...}) body of the PUT that saves contextManagement. */
function settingsPutBody(): string {
  const anchor = MODAL.indexOf('contextManagement: contextManagement');
  expect(anchor).toBeGreaterThan(-1);
  const start = MODAL.lastIndexOf('JSON.stringify({', anchor);
  const end = MODAL.indexOf('})', anchor);
  expect(start).toBeGreaterThan(-1);
  return MODAL.slice(start, end);
}

describe('ProjectManagerModal toolModes', () => {
  it('sends toolModes inside settings of the same PUT as contextManagement', () => {
    const body = settingsPutBody();
    expect(body).toMatch(/settings:\s*\{/);
    expect(body).toMatch(/toolModes:\s*toolModes/);
  });

  it('loads toolModes from the fetched project settings', () => {
    expect(MODAL).toMatch(/setToolModes\(\s*projectData\.settings\?\.toolModes\s*\|\|\s*\{\}\s*\)/);
  });

  it('offers exactly inherit / disabled / gated / autonomous for git', () => {
    for (const v of ["'disabled'", "'gated'", "'autonomous'"]) {
      expect(MODAL).toContain(`value: ${v}`);
    }
    expect(MODAL).toMatch(/value:\s*undefined,\s*label:\s*'Inherit'/);
    // Inherit must clear the key, not store a value the backend rejects.
    expect(MODAL).toMatch(/delete next\.git/);
  });

  it('values match the backend Literal', () => {
    const model = fs.readFileSync(
      path.join(__dirname, '..', '..', '..', '..', 'app', 'models', 'project.py'), 'utf8');
    expect(model).toMatch(/toolModes: Dict\[str, Literal\["disabled", "gated", "autonomous"\]\]/);
  });
});
