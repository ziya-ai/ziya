/**
 * Which of the file explorer's four mutually-exclusive top-level views wins,
 * for the states that occur around a project switch.
 *
 * The regression this pins: for the whole window between "user switched
 * project" and "first folder data for the new project arrived", the explorer
 * rendered the **"No files found" empty state**, complete with a Refresh
 * button, as though the incoming project were genuinely empty.
 *
 * That window is not short. The switch handler clears \`folders\`/\`treeData\` and
 * sets \`isScanning=false\`, but \`hasLoadedData\` is still \`true\` — it latched
 * on the *previous* project and nothing resets it — and \`isInitialLoad\` is
 * long since false. So the empty-state guard
 * (\`!isScanning && !isInitialLoad && hasLoadedData && treeLen === 0\`) is
 * satisfied exactly, and the loading guard is not (it needs
 * \`isScanning || isInitialLoad\`). The scanning spinner only takes over once
 * the first fetch comes back with \`_scanning\`, which is a round trip away and
 * behind a gitignore-pattern build on a large project.
 */
import {
    fileExplorerViewState,
} from '../folderUtil';

describe('fileExplorerViewState — project-switch window', () => {
    // The exact state the explorer is in immediately after handleProjectSwitch:
    // old data cleared, scan not yet reported, hasLoadedData latched from the
    // project we just left.
    const justSwitched = {
        isScanning: false,
        isInitialLoad: false,
        hasLoadedData: true,
        treeNodeCount: 0,
    };

    it('shows the switching view, NOT the empty state, right after a switch', () => {
        expect(
            fileExplorerViewState({ ...justSwitched, isSwitchingProject: true }),
        ).toBe('switching');
    });

    it('would show "No files found" without the switching flag (the bug)', () => {
        // Documents why the flag is required rather than derivable: with the
        // same state and no flag, the empty-state guard is what matches.
        expect(
            fileExplorerViewState({ ...justSwitched, isSwitchingProject: false }),
        ).toBe('empty');
    });

    it('keeps showing switching once the scan is reported but no tree exists yet', () => {
        // First fetch returned {_scanning:true, children:{}} — still blank.
        expect(fileExplorerViewState({
            isSwitchingProject: true,
            isScanning: true,
            isInitialLoad: false,
            hasLoadedData: true,
            treeNodeCount: 0,
        })).toBe('switching');
    });

    it('switching outranks a stale tree still held from the previous project', () => {
        // If clearing raced and stale nodes are still present, we must not
        // render the old project's files under the new project's name.
        expect(fileExplorerViewState({
            isSwitchingProject: true,
            isScanning: false,
            isInitialLoad: false,
            hasLoadedData: true,
            treeNodeCount: 42,
        })).toBe('switching');
    });

    it('hands over to the tree once real data for the new project lands', () => {
        expect(fileExplorerViewState({
            isSwitchingProject: false,
            isScanning: true,
            isInitialLoad: false,
            hasLoadedData: true,
            treeNodeCount: 4,
        })).toBe('tree');
    });
});

describe('fileExplorerViewState — states unrelated to switching', () => {
    it('shows loading on a cold first load', () => {
        expect(fileExplorerViewState({
            isSwitchingProject: false,
            isScanning: true,
            isInitialLoad: true,
            hasLoadedData: false,
            treeNodeCount: 0,
        })).toBe('loading');
    });

    it('still shows the empty state for a genuinely empty project', () => {
        // A finished scan that legitimately produced nothing must keep its
        // "No files found" + Refresh affordance; the switching flag must not
        // have swallowed this case.
        expect(fileExplorerViewState({
            isSwitchingProject: false,
            isScanning: false,
            isInitialLoad: false,
            hasLoadedData: true,
            treeNodeCount: 0,
        })).toBe('empty');
    });

    it('shows the tree during a mid-scan refresh of the same project', () => {
        // Re-scanning the current project must not blank the tree — that is
        // the case the switching flag deliberately does not cover.
        expect(fileExplorerViewState({
            isSwitchingProject: false,
            isScanning: true,
            isInitialLoad: false,
            hasLoadedData: true,
            treeNodeCount: 900,
        })).toBe('tree');
    });

    it('does not report empty while a scan is running', () => {
        expect(fileExplorerViewState({
            isSwitchingProject: false,
            isScanning: true,
            isInitialLoad: false,
            hasLoadedData: true,
            treeNodeCount: 0,
        })).toBe('loading');
    });
});
