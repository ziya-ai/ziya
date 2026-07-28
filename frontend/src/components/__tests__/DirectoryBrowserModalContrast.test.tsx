/**
 * Regression guards for DirectoryBrowserModal light-mode contrast.
 *
 * The modal previously hardcoded dark-mode colors (#1a1a1a dialog paper,
 * #141414 breadcrumb bar, #fff current breadcrumb, dark scrollbars) while
 * directory names used the theme token text.primary — which resolves to
 * black in light mode, producing unreadable black-on-dark text. The modal
 * must derive all surface and text colors from the active MUI theme.
 */
import React from 'react';
import fs from 'fs';
import path from 'path';
import { render, screen } from '@testing-library/react';
import { ThemeProvider, createTheme } from '@mui/material/styles';
import { DirectoryBrowserModal } from '../DirectoryBrowserModal';

describe('DirectoryBrowserModal theme awareness', () => {
  it('contains no hardcoded dark-surface colors (must use theme tokens)', () => {
    const src = fs.readFileSync(
      path.join(__dirname, '..', 'DirectoryBrowserModal.tsx'),
      'utf8',
    );
    // Each of these hardcoded values forced dark-mode styling even when
    // the app theme was light, making text.primary content unreadable.
    const forbidden = ["'#1a1a1a'", "'#141414'", "'#0a0a0a'", "'#333'", "'#555'", "'#fff'"];
    for (const color of forbidden) {
      expect(src).not.toContain(color);
    }
  });

  it('renders directory entries under a light theme without a forced-dark paper', async () => {
    (global as any).fetch = jest.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        current_path: '/Users/test',
        entries: [
          { name: 'projects', path: '/Users/test/projects', is_dir: true },
        ],
      }),
    });

    render(
      <ThemeProvider theme={createTheme({ palette: { mode: 'light' } })}>
        <DirectoryBrowserModal open onClose={jest.fn()} onSelect={jest.fn()} />
      </ThemeProvider>,
    );

    // Directory entry renders (theme-token refactor didn't break the list)
    expect(await screen.findByText('projects')).toBeInTheDocument();

    const paper = document.querySelector('.MuiDialog-paper') as HTMLElement;
    expect(paper).toBeTruthy();
    // Must not resolve to the old hardcoded dark background. (In jsdom
    // environments without full CSS cascade support this is a no-op
    // guard; the source-scan test above is the authoritative check.)
    expect(getComputedStyle(paper).backgroundColor).not.toBe('rgb(26, 26, 26)');
  });
});
