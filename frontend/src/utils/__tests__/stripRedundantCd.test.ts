/**
 * @jest-environment jsdom
 *
 * Tests for stripRedundantCdForDisplay — the DISPLAY-ONLY helper that hides a
 * redundant leading `cd <cwd> &&` from shell-tool command previews.
 *
 * Why the cwd match must be exact (not "strip any leading cd"):
 *   The shell tool (run_shell_command) always executes in a fresh subprocess
 *   rooted at ZIYA_USER_CODEBASE_DIR — set at manager.py to abspath(workspace_path)
 *   and read by shell_server.py as the cwd for EVERY command (no per-call cwd
 *   override). The frontend mirrors that same workspace path on
 *   window.__ZIYA_CURRENT_PROJECT_PATH__. So a leading `cd <that path>` is a
 *   genuine no-op and safe to hide, but `cd some/other/dir` is a REAL directory
 *   change and must be preserved — otherwise the preview would misrepresent
 *   what ran. These tests pin exactly that distinction.
 *
 * The helper never alters the executed command; it only rewrites the string
 * shown in the header / `$ command` line / streaming preview.
 */
import {
    stripRedundantCdForDisplay,
    setKnownShellCwd,
} from '../mcpFormatter';

const CWD = '/Users/dcohn/workspace/proj';
const WINDOW_KEY = '__ZIYA_CURRENT_PROJECT_PATH__';

function setWindowCwd(value: string | undefined): void {
    if (value === undefined) {
        delete (window as any)[WINDOW_KEY];
    } else {
        (window as any)[WINDOW_KEY] = value;
    }
}

afterEach(() => {
    // Reset both cwd sources so cases don't leak into one another.
    setKnownShellCwd(null);
    setWindowCwd(undefined);
});

describe('stripRedundantCdForDisplay — cwd match via setKnownShellCwd', () => {
    beforeEach(() => setKnownShellCwd(CWD));

    it('strips `cd <cwd> && cmd` down to the command', () => {
        expect(stripRedundantCdForDisplay(`cd ${CWD} && grep foo`)).toBe('grep foo');
    });

    it('strips when the cd path has a trailing slash the cwd lacks', () => {
        expect(stripRedundantCdForDisplay(`cd ${CWD}/ && ls`)).toBe('ls');
    });

    it('strips a double-quoted cwd path', () => {
        expect(stripRedundantCdForDisplay(`cd "${CWD}" && ls`)).toBe('ls');
    });

    it('strips a single-quoted cwd path', () => {
        expect(stripRedundantCdForDisplay(`cd '${CWD}' && ls`)).toBe('ls');
    });

    it('strips with a `;` separator as well as `&&`', () => {
        expect(stripRedundantCdForDisplay(`cd ${CWD} ; ls`)).toBe('ls');
    });

    it('preserves the tail verbatim, including pipes', () => {
        expect(stripRedundantCdForDisplay(`cd ${CWD} && ls | wc -l`)).toBe('ls | wc -l');
    });

    it('trims surplus whitespace around the surviving command', () => {
        expect(stripRedundantCdForDisplay(`cd ${CWD} &&   grep foo  `)).toBe('grep foo');
    });

    it('does NOT strip a cd to a DIFFERENT directory (real dir change)', () => {
        const cmd = `cd ${CWD}/frontend && npm test`;
        expect(stripRedundantCdForDisplay(cmd)).toBe(cmd);
    });

    it('does NOT strip a cd to an unrelated absolute path', () => {
        const cmd = 'cd /tmp && ls';
        expect(stripRedundantCdForDisplay(cmd)).toBe(cmd);
    });

    it('does NOT touch a bare `cd <cwd>` with no following command', () => {
        const cmd = `cd ${CWD}`;
        expect(stripRedundantCdForDisplay(cmd)).toBe(cmd);
    });
});

describe('stripRedundantCdForDisplay — `.`/`./` no-ops (cwd-independent)', () => {
    it('strips `cd . && cmd` even when cwd is unknown', () => {
        setKnownShellCwd(null);
        setWindowCwd(undefined);
        expect(stripRedundantCdForDisplay('cd . && ls')).toBe('ls');
    });

    it('strips `cd ./ && cmd` even when cwd is unknown', () => {
        setKnownShellCwd(null);
        setWindowCwd(undefined);
        expect(stripRedundantCdForDisplay('cd ./ && ls')).toBe('ls');
    });

    it('strips `cd . && cmd` even when cwd is a different real dir', () => {
        setKnownShellCwd('/some/other/dir');
        expect(stripRedundantCdForDisplay('cd . && pwd')).toBe('pwd');
    });
});

describe('stripRedundantCdForDisplay — unknown cwd preserves real cd', () => {
    beforeEach(() => {
        setKnownShellCwd(null);
        setWindowCwd(undefined);
    });

    it('leaves `cd /anything && cmd` untouched when no cwd is known', () => {
        const cmd = `cd ${CWD} && grep foo`;
        // cwd unknown → cannot prove the cd is redundant → must preserve.
        expect(stripRedundantCdForDisplay(cmd)).toBe(cmd);
    });
});

describe('stripRedundantCdForDisplay — window.__ZIYA_CURRENT_PROJECT_PATH__ fallback', () => {
    it('uses the window global when knownShellCwd is unset', () => {
        setKnownShellCwd(null);
        setWindowCwd(CWD);
        expect(stripRedundantCdForDisplay(`cd ${CWD} && ls`)).toBe('ls');
    });

    it('normalizes a trailing slash on the window-global cwd', () => {
        setKnownShellCwd(null);
        setWindowCwd(`${CWD}/`);
        expect(stripRedundantCdForDisplay(`cd ${CWD} && ls`)).toBe('ls');
    });

    it('setKnownShellCwd takes precedence over the window global', () => {
        setKnownShellCwd('/a');
        setWindowCwd('/b');
        expect(stripRedundantCdForDisplay('cd /a && ls')).toBe('ls');
        expect(stripRedundantCdForDisplay('cd /b && ls')).toBe('cd /b && ls');
    });

    it('ignores a non-string window global', () => {
        setKnownShellCwd(null);
        (window as any)[WINDOW_KEY] = 12345;
        const cmd = `cd ${CWD} && ls`;
        expect(stripRedundantCdForDisplay(cmd)).toBe(cmd);
    });
});

describe('stripRedundantCdForDisplay — non-cd and malformed inputs', () => {
    beforeEach(() => setKnownShellCwd(CWD));

    it('returns a command with no leading cd unchanged', () => {
        expect(stripRedundantCdForDisplay('grep foo && ls')).toBe('grep foo && ls');
    });

    it('does not strip a single `&` separator (only `&&` / `;`)', () => {
        const cmd = `cd ${CWD} & ls`;
        expect(stripRedundantCdForDisplay(cmd)).toBe(cmd);
    });

    it('does not treat `cdfoo` (no space) as a cd', () => {
        expect(stripRedundantCdForDisplay('cdfoo && ls')).toBe('cdfoo && ls');
    });

    it('returns an empty string unchanged', () => {
        expect(stripRedundantCdForDisplay('')).toBe('');
    });

    it('returns a non-string input unchanged', () => {
        expect(stripRedundantCdForDisplay(null as any)).toBe(null);
        expect(stripRedundantCdForDisplay(undefined as any)).toBe(undefined);
    });

    it('is idempotent: stripping an already-stripped command is a no-op', () => {
        const once = stripRedundantCdForDisplay(`cd ${CWD} && grep foo`);
        expect(stripRedundantCdForDisplay(once)).toBe('grep foo');
    });
});

describe('setKnownShellCwd — normalization and reset', () => {
    afterEach(() => setKnownShellCwd(null));

    it('normalizes trailing slashes on the stored cwd', () => {
        setKnownShellCwd(`${CWD}///`);
        expect(stripRedundantCdForDisplay(`cd ${CWD} && ls`)).toBe('ls');
    });

    it('treats an empty-string cwd as unknown (falls through to no-op only)', () => {
        setKnownShellCwd('');
        setWindowCwd(undefined);
        // No known cwd → real path preserved, but `.` no-op still stripped.
        expect(stripRedundantCdForDisplay(`cd ${CWD} && ls`)).toBe(`cd ${CWD} && ls`);
        expect(stripRedundantCdForDisplay('cd . && ls')).toBe('ls');
    });
});
