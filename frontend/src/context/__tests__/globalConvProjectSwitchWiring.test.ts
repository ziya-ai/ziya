/**
 * Static wiring guard: a globally-shared conversation must survive a
 * project switch.
 *
 * WHY STATIC RATHER THAN A RENDER TEST
 *
 * The decision function is `conversationIsEffectivelyGlobal`, which is
 * unit-tested directly (utils/__tests__/folderUtil.test.ts) and passed
 * throughout the live defect.  The bug was entirely in WHAT it was
 * handed: the project-switch effect passed the `folders` React state,
 * which the PROJECT_CLEAR layout effect empties before that effect runs.
 * A conversation that is global only by folder inheritance therefore read
 * as non-global, was dropped from the switch preload, and lost selection
 * to an unrelated project-local conversation.  Reproducing that in jsdom
 * needs a real IndexedDB, a two-project switch, and the exact commit
 * ordering of a layout effect versus a passive effect -- for an assertion
 * whose real content is "this call site reads folders from IDB".
 *
 * These tests are about STRUCTURE.  They will not catch a wrong folder
 * comparison; folderUtil's suite does that.  They catch the two ways this
 * regressed: feeding the effective-global check a folder set that is
 * empty at switch time, and testing the raw `isGlobal` flag where the
 * inherited case has to count.
 */

import * as fs from 'fs';
import * as path from 'path';

const CTX = fs.readFileSync(
    path.resolve(__dirname, '..', 'ChatContext.tsx'), 'utf8');

/** Body of the project-switch / periodic-sync effect. */
const switchEffect = (): string => {
    const start = CTX.indexOf('project-switch effect fired');
    expect(start).toBeGreaterThan(-1);
    const end = CTX.indexOf("}, [isInitialized, currentProject?.id, isEphemeralMode]);", start);
    expect(end).toBeGreaterThan(start);
    return CTX.slice(start, end);
};

describe('global conversation survives a project switch', () => {
    it('never resolves effective-global against the folders React state inside the switch effect', () => {
        const body = switchEffect();
        const callsWithReactState = body.match(
            /conversationIsEffectivelyGlobal\([^)]*,\s*folders\s*\)/g
        );
        // PROJECT_CLEAR sets folders to [] before this effect runs, so any
        // call site using it silently treats every inherited-global chat as
        // project-local.
        expect(callsWithReactState).toBeNull();
    });

    it('reads the full folder set from IndexedDB before filtering shells', () => {
        const body = switchEffect();
        expect(body).toContain('db.getFolders()');
        // Both the preload and the sync filter must consume it.
        const idbCalls = body.match(
            /conversationIsEffectivelyGlobal\([^)]*,\s*idbFolders\s*\)/g
        ) || [];
        expect(idbCalls.length).toBeGreaterThanOrEqual(3);
    });

    it('treats the active conversation as belonging to the new project when it is effectively global', () => {
        const body = switchEffect();
        const marker = body.indexOf('const belongsToNewProject');
        expect(marker).toBeGreaterThan(-1);
        const decl = body.slice(marker, marker + 400);
        // A raw `currentConv.isGlobal` test relocates away from a chat that
        // is shared via its parent folder rather than its own flag.
        expect(decl).toContain('conversationIsEffectivelyGlobal(currentConv');
        expect(decl).not.toMatch(/currentConv\.isGlobal\s*\|\|/);
    });

    it('still keeps the project-local ownership test alongside the global one', () => {
        const body = switchEffect();
        const marker = body.indexOf('const belongsToNewProject');
        const decl = body.slice(marker, marker + 400);
        // Negative control: the fix must not turn every conversation into a
        // member of the current project.
        expect(decl).toContain('currentConv.projectId === projectId');
    });
});
