/**
 * A reloaded tab must reopen the PROJECT it had open, not the project some
 * other tab switched to last.
 *
 * The conversation id has been per-tab (sessionStorage via tabState) for a
 * while, but the project lived only in the browser-wide, instance-scoped
 * localStorage key ZIYA_LAST_PROJECT_ID.  Switching projects in tab B then
 * reloading tab A put A on B's project — and then, because the saved
 * conversation belonged to A's project, on the wrong conversation too.
 *
 * Source-anchored: booting ProjectProvider drags in the project API, the
 * instance latch and the lens store.
 */
import * as fs from 'fs';
import * as path from 'path';

const PROJECT = fs.readFileSync(path.join(__dirname, '..', 'ProjectContext.tsx'), 'utf8');
const TABSTATE = fs.readFileSync(path.join(__dirname, '..', '..', 'utils', 'tabState.ts'), 'utf8');

function slice(src: string, start: string, end: string): string {
    const s = src.indexOf(start);
    expect(s).toBeGreaterThan(-1);
    const e = src.indexOf(end, s + start.length);
    expect(e).toBeGreaterThan(s);
    return src.slice(s, e);
}

describe('per-tab project key', () => {
    it('is a sessionStorage key scoped to the server instance', () => {
        expect(PROJECT).toMatch(/const tabProjectKey = \(\) => scopedNameSync\('ZIYA_TAB_PROJECT_ID'\);/);
        expect(PROJECT).toMatch(/import \{ getTabState, setTabState \} from '\.\.\/utils\/tabState';/);
        const reader = slice(PROJECT, 'function readTabProjectId', '\n}\n');
        expect(reader).toMatch(/getTabState\(tabProjectKey\(\)\)/);
        expect(reader).not.toMatch(/localStorage/);
    });

    it('is written everywhere the browser-wide key is written', () => {
        const remember = slice(PROJECT, 'function rememberProject', '\n}\n');
        expect(remember).toMatch(/setTabState\(tabProjectKey\(\), projectId\)/);
        expect(remember).toMatch(/localStorage\.setItem\(lastProjectKey\(\), projectId\)/);
        // The only direct write to the browser-wide key is inside rememberProject;
        // init and switchProject both go through it.
        expect(PROJECT.match(/localStorage\.setItem\(lastProjectKey\(\)/g)!.length).toBe(1);
        expect(PROJECT.match(/rememberProject\(project\.id\)/g)!.length).toBe(2);
    });
});

describe('init', () => {
    const init = slice(PROJECT, 'let project: Project | null = null;', 'setCurrentProject(project);');

    it('tries the tab project before the explicit startup root and the browser-wide key', () => {
        const tab = init.indexOf('readTabProjectId()');
        const explicit = init.indexOf('startup?.explicit');
        const browserWide = init.indexOf('localStorage.getItem(lastProjectKey())');
        expect(tab).toBeGreaterThan(-1);
        expect(explicit).toBeGreaterThan(tab);
        expect(browserWide).toBeGreaterThan(explicit);
        // The explicit branch is skipped when the tab restored a project.
        expect(init).toMatch(/if \(project\) \{[\s\S]*?\} else if \(startup\?\.explicit\) \{/);
    });

    it('drops a tab project the server no longer knows instead of looping on it', () => {
        const restore = slice(init, 'const tabProjectId = readTabProjectId();', 'if (project) {');
        expect(restore).toMatch(/projectApi\.getProject\(tabProjectId\)/);
        expect(restore).toMatch(/forgetTabProjectId\(\)/);
        const forget = slice(PROJECT, 'function forgetTabProjectId', '\n}\n');
        expect(forget).toMatch(/sessionStorage\.removeItem\(tabProjectKey\(\)\)/);
    });
});

describe('readLastProjectId (used by ChatContext before currentProject resolves)', () => {
    it('prefers the tab project so the startup conversation filter matches the tab', () => {
        const reader = slice(PROJECT, 'export function readLastProjectId', '\n}\n');
        const tab = reader.indexOf('readTabProjectId()');
        const browserWide = reader.indexOf('localStorage.getItem(lastProjectKey())');
        expect(tab).toBeGreaterThan(-1);
        expect(browserWide).toBeGreaterThan(tab);
    });
});

describe('tabState inventory', () => {
    it('lists the project key as per-tab state', () => {
        expect(TABSTATE).toMatch(/ZIYA_TAB_PROJECT_ID/);
    });
});
