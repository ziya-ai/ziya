/**
 * Static wiring guard for mid-scan tree refresh and the project-switch view.
 *
 * WHY STATIC RATHER THAN A RENDER TEST
 *
 * Both defects this pins were wiring, not logic.  The pure decision
 * functions -- `scanEventTargetsProject`, `nextScanRefetchInterval`,
 * `fileExplorerViewState` -- are unit-tested directly and all passed
 * while the bug was live, because nothing CALLED them.  Reproducing the
 * failures in jsdom would require driving a WebSocket, a 1-second poll
 * chain, a MessageChannel hop (which `fetchFolders` uses, and whose
 * promise resolves before the work it schedules), and a project switch
 * -- for assertions whose real content is "this call site exists".
 * Pinning the call sites is both cheaper and more direct.
 *
 * These tests are deliberately about STRUCTURE.  They will not catch a
 * wrong threshold or a wrong comparison; the unit suites do that.  They
 * catch the two ways this regressed before: a helper existing but never
 * being invoked, and a state branch nobody reaches.
 */

import * as fs from 'fs';
import * as path from 'path';

const CTX = fs.readFileSync(
  path.resolve(__dirname, '..', 'FolderContext.tsx'), 'utf8');
const EXPLORER = fs.readFileSync(
  path.resolve(__dirname, '..', '..', 'components', 'MUIFileExplorer.tsx'), 'utf8');

/** Body of the progress-poll effect: from its marker to the isScanning dep. */
const progressPollEffect = (): string => {
  const start = CTX.indexOf('One-time setup for folder progress checking');
  expect(start).toBeGreaterThan(-1);
  const end = CTX.indexOf('}, [isScanning]);', start);
  expect(end).toBeGreaterThan(start);
  return CTX.slice(start, end);
};

/**
 * Body of the scan_complete WebSocket handler branch.
 *
 * Anchored on the comment that FOLLOWS the branch, not on the first
 * \`return;\` inside it: the project guard's own early return is the first
 * one, so slicing there cuts the body off before \`setIsScanning\` and made
 * the ordering assertion below unable to see it (a false failure against
 * correct source).
 */
const scanCompleteBranch = (): string => {
  const start = CTX.indexOf("if (type === 'scan_complete')");
  expect(start).toBeGreaterThan(-1);
  const end = CTX.indexOf('// Remaining handlers below', start);
  expect(end).toBeGreaterThan(start);
  return CTX.slice(start, end);
};

describe('the tree is refetched DURING a scan, not only at completion', () => {
  it('refetches from the active branch of the progress poll', () => {
    // The whole defect: fetchFolders was called only in the else branch
    // (server reports scan finished), so a 79s scan showed the tree as it
    // existed in its first instant for all 79 seconds -- even though the
    // backend publishes a live partial that deepens throughout.
    const effect = progressPollEffect();
    const activeAt = effect.indexOf('if (data.active)');
    expect(activeAt).toBeGreaterThan(-1);
    const elseAt = effect.indexOf('} else {', activeAt);
    expect(elseAt).toBeGreaterThan(activeAt);
    const activeBranch = effect.slice(activeAt, elseAt);
    expect(activeBranch).toContain('fetchFoldersRef.current()');
  });

  it('uses the backoff helper rather than refetching on every poll tick', () => {
    // A refetch per 1s poll re-runs convertToTreeData over an
    // ever-larger tree; the point of the helper is that it backs off.
    const effect = progressPollEffect();
    expect(effect).toContain('nextScanRefetchInterval(');
    expect(effect).toContain('SCAN_REFETCH_INITIAL_TICK');
  });

  it('imports the backoff helpers it uses', () => {
    expect(CTX).toMatch(
      /import\s*\{[^}]*nextScanRefetchInterval[^}]*\}\s*from\s*"\.\.\/utils\/folderUtil"/);
    expect(CTX).toMatch(
      /import\s*\{[^}]*SCAN_REFETCH_INITIAL_TICK[^}]*\}\s*from\s*"\.\.\/utils\/folderUtil"/);
  });

  it('gates the refetch on the file counter having moved', () => {
    // A scan stalled inside one slow directory would otherwise pay for a
    // re-convert that renders identically.
    const effect = progressPollEffect();
    expect(effect).toContain('lastRefetchedFileCount');
  });

  it('resets its backoff per scan rather than across scans', () => {
    // The counters must be declared INSIDE the effect (which re-runs when
    // isScanning flips); module or ref scope would carry a previous
    // scan's exhausted backoff into the next one, so a second scan would
    // start at the 30-tick cap and appear not to refresh at all.
    const effect = progressPollEffect();
    expect(effect).toMatch(/let\s+pollTick\s*=\s*0/);
    expect(effect).toMatch(/let\s+refetchInterval\s*=/);
    // Declared before the poll function that consumes them.
    expect(effect.indexOf('let pollTick')).toBeLessThan(
      effect.indexOf('const checkFolderProgress'));
  });
});

describe('scan_complete is filtered to this window\'s own project', () => {
  it('guards the branch with scanEventTargetsProject', () => {
    const branch = scanCompleteBranch();
    expect(branch).toContain('scanEventTargetsProject(');
  });

  it('checks the project BEFORE clearing scanning state', () => {
    // Order is the whole fix.  Clearing isScanning first would tear down
    // the progress poller -- which is also the fallback that fetches the
    // finished tree -- before discovering the event belonged to another
    // project, which is precisely the observed freeze.
    const branch = scanCompleteBranch();
    const guardAt = branch.indexOf('scanEventTargetsProject(');
    const clearAt = branch.indexOf('setIsScanning(false)');
    expect(guardAt).toBeGreaterThan(-1);
    expect(clearAt).toBeGreaterThan(-1);
    expect(guardAt).toBeLessThan(clearAt);
  });

  it('compares against the ref, not the render-scoped project value', () => {
    // The WS handler effect has an empty dep array, so a captured
    // currentProject would be pinned to its mount-time value forever and
    // every post-switch event would be judged against a stale project.
    const branch = scanCompleteBranch();
    expect(branch).toContain('currentProjectRef.current');
  });
});

describe('the project-switch window is distinguishable from an empty project', () => {
  it('both switch entry points raise the flag', () => {
    // Two paths reach a switch: the projectSwitched event, and the
    // currentProject?.path effect (ProjectContext finishing its load, or
    // a restore from storage). The latter previously only re-fetched,
    // leaving the OUTGOING project's tree on screen under the new
    // project's name.
    const eventAt = CTX.indexOf('const handleProjectSwitch');
    const effectAt = CTX.indexOf('const prevProjectPath');
    expect(eventAt).toBeGreaterThan(-1);
    expect(effectAt).toBeGreaterThan(-1);

    const eventBody = CTX.slice(eventAt, CTX.indexOf('};', CTX.indexOf('seedDefaultIncludedFolders(projectPath)', eventAt)));
    expect(eventBody).toContain('setIsSwitchingProject(true)');

    const effectBody = CTX.slice(effectAt, CTX.indexOf('}, [currentProject?.path', effectAt));
    expect(effectBody).toContain('setIsSwitchingProject(true)');
  });

  it('the path effect clears the outgoing tree, not just refetches', () => {
    const effectAt = CTX.indexOf('const prevProjectPath');
    const effectBody = CTX.slice(effectAt, CTX.indexOf('}, [currentProject?.path', effectAt));
    expect(effectBody).toContain('setFolders(undefined)');
    expect(effectBody).toContain('setTreeData([])');
  });

  it('the path effect does not treat first load as a switch', () => {
    // prevProjectPath starts null; without this guard the ordinary
    // first-load spinner would be replaced by "Switching projects…".
    const effectAt = CTX.indexOf('const prevProjectPath');
    const effectBody = CTX.slice(effectAt, CTX.indexOf('}, [currentProject?.path', effectAt));
    expect(effectBody).toContain('isRealSwitch');
  });

  it('clears the flag on every terminal outcome of a fetch', () => {
    // Stranding it true would pin the explorer to the switching spinner
    // permanently. Terminal outcomes: server-reported error, a landed
    // partial, a completed scan, and a thrown fetch.
    // Four clears, one raise per switch path.
    const clears = CTX.match(/setIsSwitchingProject\(false\)/g) ?? [];
    expect(clears.length).toBeGreaterThanOrEqual(4);
  });

  it('does NOT clear the flag on the pure-_scanning branch', () => {
    // That branch is the blank-with-spinner window the flag exists to
    // mark: the server has nothing to show yet. Clearing there would
    // reintroduce the "No files found" flash.
    const at = CTX.indexOf('if (data._scanning || data._stale_and_scanning)');
    expect(at).toBeGreaterThan(-1);
    const staleAt = CTX.indexOf('if (data._stale_and_scanning)', at);
    expect(staleAt).toBeGreaterThan(at);
    // Between entering the scanning branch and the stale sub-branch there
    // must be no clear -- only the sub-branch (real data) may clear it.
    expect(CTX.slice(at, staleAt)).not.toContain('setIsSwitchingProject(false)');
  });

  it('exposes the flag through the context value and the fallback', () => {
    expect(CTX).toContain('isSwitchingProject: boolean;');
    // Present in the useMemo dep array, or a switch would not re-render.
    const memoAt = CTX.indexOf('const contextValue = useMemo');
    const memoBody = CTX.slice(memoAt, CTX.indexOf('  return (', memoAt));
    expect(memoBody).toContain('isSwitchingProject');
    expect(memoBody).toMatch(/\}\),\s*\[[^\]]*isSwitchingProject/);
    // The outside-provider fallback must supply it, or consumers crash.
    expect(CTX).toContain('isSwitchingProject: false,');
  });
});

describe('the explorer routes its views through the resolver', () => {
  it('resolves the view state once instead of per-branch conditions', () => {
    expect(EXPLORER).toContain('fileExplorerViewState({');
    expect(EXPLORER).toMatch(
      /import\s*\{[^}]*fileExplorerViewState[^}]*\}\s*from\s*'\.\.\/utils\/folderUtil'/);
  });

  it('renders a switching view that names the switch', () => {
    expect(EXPLORER).toContain("viewState === 'switching'");
    expect(EXPLORER).toMatch(/Switching projects/);
  });

  it('the switching branch precedes loading and empty', () => {
    // Precedence is the fix: the switch window matches the empty-state
    // condition exactly (isScanning=false, isInitialLoad=false,
    // hasLoadedData latched true, tree empty).
    const sw = EXPLORER.indexOf("viewState === 'switching'");
    const loading = EXPLORER.indexOf("viewState === 'loading'");
    const empty = EXPLORER.indexOf("viewState === 'empty'");
    expect(sw).toBeGreaterThan(-1);
    expect(loading).toBeGreaterThan(sw);
    expect(empty).toBeGreaterThan(loading);
  });

  it('no longer duplicates the raw branch conditions', () => {
    // If the old inline conditions survive alongside the resolver they
    // can disagree with it, which is the class of bug being removed.
    expect(EXPLORER).not.toContain('(isScanning || isInitialLoad) && (!hasLoadedData');
    expect(EXPLORER).not.toContain('!isScanning && !isInitialLoad && hasLoadedData &&');
  });

  it('shows no file counters while switching', () => {
    // Counters there would be the outgoing project's, or zero -- both
    // read as "the new project is empty", the thing being fixed.
    const sw = EXPLORER.indexOf("viewState === 'switching'");
    const next = EXPLORER.indexOf("viewState === 'loading'", sw);
    expect(EXPLORER.slice(sw, next)).not.toContain('scanProgress');
  });
});
