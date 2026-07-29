/**
 * Regression guard for ProjectSwitcher light-mode hover contrast.
 *
 * The Recent-project gear icon's hover handlers previously assigned
 * light-only colors unconditionally (#d4d4d4 text and a white-tint
 * background) — invisible against the white menu surface in light mode.
 * Hover colors on theme-following surfaces must be conditioned on
 * isDarkMode. (The current-project gear's white tint is exempt: that
 * item has a constant blue background in both modes.)
 */
import fs from 'fs';
import path from 'path';

describe('ProjectSwitcher hover theme awareness', () => {
  it('has no unconditional light-only hover color assignments', () => {
    const src = fs.readFileSync(
      path.join(__dirname, '..', 'ProjectSwitcher.tsx'),
      'utf8',
    );
    // Unconditional assignments of dark-mode-only hover values. After the
    // fix these are ternaries on isDarkMode, so the literal strings below
    // must not reappear.
    expect(src).not.toContain("style.color = '#d4d4d4'");
    expect(src).not.toContain("style.background = 'rgba(255,255,255,0.1)'");
  });
});
