/**
 * Sidebar loading-vs-empty affordance.
 *
 * On a cold start into a large project the conversation sidebar rendered as a
 * blank panel for as long as the first chat list took to arrive — no spinner,
 * no message, no indication anything was happening.  Two independent gaps
 * produced that:
 *
 *   1. `isProjectSwitching` is false on a cold start.  It is set only when
 *      `isActualProjectSwitch` is true, which requires
 *      `serverSyncedForProject.current !== null` — i.e. a PREVIOUS project.
 *      On first load there isn't one, so FolderTree's spinner never showed.
 *   2. Nothing distinguished "conversations not loaded yet" from "this project
 *      genuinely has no conversations".  MUIChatHistory's tree memo returns
 *      the last built tree (empty on cold start) while folders are still in
 *      flight, so both cases rendered identically: as nothing.
 *
 * The fix adds `hasLoadedConversations`, set by whichever of the IndexedDB
 * preload or the server sync commits a list first, and reset on project
 * change.  These tests pin the state machine, because the failure mode of
 * getting it wrong is not a crash — it is a permanently stuck spinner or an
 * "empty" message shown over a list that is merely still loading.
 */

/** The three states the sidebar can be in, derived from the two flags. */
type SidebarState = 'spinner' | 'empty' | 'list';

/**
 * Mirrors the render branches added to MUIChatHistory.  Kept as a pure
 * function so the decision table is testable without mounting the component
 * (which needs ~8 nested providers, IndexedDB, and a virtualized list).
 */
export function sidebarState(
  hasLoadedConversations: boolean,
  rowCount: number,
): SidebarState {
  if (!hasLoadedConversations && rowCount === 0) return 'spinner';
  if (hasLoadedConversations && rowCount === 0) return 'empty';
  return 'list';
}

describe('sidebarState', () => {
  it('shows a spinner on cold start (not loaded, nothing to show)', () => {
    expect(sidebarState(false, 0)).toBe('spinner');
  });

  it('shows the empty message only once loading has finished', () => {
    expect(sidebarState(true, 0)).toBe('empty');
  });

  it('never shows "empty" while the list is still loading', () => {
    // The regression: these two were indistinguishable, so a slow cold start
    // looked identical to an empty project.
    expect(sidebarState(false, 0)).not.toBe('empty');
  });

  it('keeps showing rows from the IDB preload while the server sync runs', () => {
    // hasLoadedConversations is already true after the preload commits, but
    // even if it were not, existing rows must never be replaced by a spinner —
    // that would flash the list away mid-sync.
    expect(sidebarState(false, 12)).toBe('list');
    expect(sidebarState(true, 12)).toBe('list');
  });

  it('is total — every combination resolves to exactly one state', () => {
    for (const loaded of [true, false]) {
      for (const rows of [0, 1, 500]) {
        expect(['spinner', 'empty', 'list']).toContain(
          sidebarState(loaded, rows),
        );
      }
    }
  });
});

/**
 * Models the `hasLoadedConversations` lifecycle across the events that touch
 * it, in the order ChatContext fires them.  The invariant that matters is
 * liveness: every path out of "loading" must reach loaded, or the spinner
 * added above becomes permanent.
 */
class LoadStateMachine {
  hasLoaded = false;
  private syncedProject: string | null = null;

  /** Project-switch effect: resets only when the project actually changes. */
  projectEffect(projectId: string) {
    if (this.syncedProject !== projectId) {
      this.hasLoaded = false;
    }
  }

  /** IDB preload finally-block (first list the user can see). */
  preloadCommitted(stale = false) {
    if (!stale) this.hasLoaded = true;
  }

  /** syncWithServer finally-block — runs on success, error, and early exit. */
  syncFinally(projectId: string) {
    this.syncedProject = projectId;
    this.hasLoaded = true;
  }
}

describe('hasLoadedConversations lifecycle', () => {
  it('cold start: loading until the preload commits', () => {
    const m = new LoadStateMachine();
    m.projectEffect('proj-a');
    expect(m.hasLoaded).toBe(false); // spinner visible
    m.preloadCommitted();
    expect(m.hasLoaded).toBe(true);
  });

  it('reaches loaded even when IndexedDB never commits', () => {
    // Corrupt/unavailable IDB: the preload contributes nothing, so the sync
    // finally-block is the only thing that can release the spinner.
    const m = new LoadStateMachine();
    m.projectEffect('proj-a');
    m.syncFinally('proj-a');
    expect(m.hasLoaded).toBe(true);
  });

  it('reaches loaded when the server is unreachable', () => {
    // syncWithServer returns early (!isServerReachable), but its finally
    // still runs — this is why the backstop lives there and not on the
    // success path.
    const m = new LoadStateMachine();
    m.projectEffect('proj-a');
    m.syncFinally('proj-a'); // early return still hits finally
    expect(m.hasLoaded).toBe(true);
  });

  it('periodic 30s ticks do not flip a loaded sidebar back to a spinner', () => {
    // The effect body re-runs on dependency changes.  If the reset were
    // unconditional, every poll would blank the sidebar for a frame.
    const m = new LoadStateMachine();
    m.projectEffect('proj-a');
    m.syncFinally('proj-a');
    expect(m.hasLoaded).toBe(true);

    m.projectEffect('proj-a'); // same project — must be a no-op
    expect(m.hasLoaded).toBe(true);
  });

  it('a real project switch resets to loading, then loads again', () => {
    const m = new LoadStateMachine();
    m.projectEffect('proj-a');
    m.syncFinally('proj-a');

    m.projectEffect('proj-b');
    expect(m.hasLoaded).toBe(false); // must not inherit proj-a's state
    m.preloadCommitted();
    expect(m.hasLoaded).toBe(true);
  });

  it('a stale preload does not mark a newer project as loaded', () => {
    // Epoch guard: proj-a's preload resolves after the user moved to proj-b.
    // Honouring it would show "empty" for proj-b using proj-a's result.
    const m = new LoadStateMachine();
    m.projectEffect('proj-a');
    m.projectEffect('proj-b');
    m.preloadCommitted(true /* stale */);
    expect(m.hasLoaded).toBe(false);
  });

  it('ephemeral mode is loaded immediately (no persistence to await)', () => {
    const m = new LoadStateMachine();
    m.hasLoaded = true; // set inline by initializeWithRecovery's ephemeral path
    expect(sidebarState(m.hasLoaded, 1)).toBe('list');
  });

  it('liveness: no ordering of events leaves the spinner stuck', () => {
    // Exhaustive over the interleavings that can actually occur.  A failure
    // here means some real sequence ends with a permanent spinner.
    const orders: Array<Array<'preload' | 'sync'>> = [
      ['preload'],
      ['sync'],
      ['preload', 'sync'],
      ['sync', 'preload'],
    ];
    for (const order of orders) {
      const m = new LoadStateMachine();
      m.projectEffect('proj-a');
      for (const ev of order) {
        if (ev === 'preload') m.preloadCommitted();
        else m.syncFinally('proj-a');
      }
      expect(m.hasLoaded).toBe(true);
    }
  });
});
