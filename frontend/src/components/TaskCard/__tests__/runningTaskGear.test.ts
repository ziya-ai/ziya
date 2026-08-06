/**
 * The sidebar "Task running…" gear must clear when the run stops.
 *
 * The bug: Conversation.tsx's reconciler carried its own
 * ``['done','failed','cancelled']`` — precisely the duplication
 * runControls.ts warns about — so 'partial' and 'held' were read as
 * still-running. The gear kept spinning on a conversation whose task had
 * stopped, with no task in flight, and the checkmark never appeared.
 *
 * Both missing statuses are recent AND describe the case a user is most
 * likely to be looking at (a run that ended without finishing), so the
 * omission hit the common path rather than an edge.
 *
 * These test the predicate and the reduction over bindings; the wiring
 * assertions live in runningTaskGearWiring.test.ts.
 */

import { isRunOver } from '../runControls';

/** The reduction the reconciler effect performs, in isolation. */
function hasRunningTask(
  bindingsByAnchor: Map<string, Array<{ run_status?: string }>>,
): boolean {
  for (const arr of bindingsByAnchor.values()) {
    for (const b of arr) {
      if (b.run_status && !isRunOver(b.run_status)) return true;
    }
  }
  return false;
}

const anchored = (...statuses: Array<string | undefined>) =>
  new Map([['msg-1', statuses.map(s => ({ run_status: s }))]]);

describe('isRunOver covers every terminal status', () => {
  it.each(['done', 'partial', 'failed', 'cancelled', 'held'])(
    'treats %s as over', (s) => {
      expect(isRunOver(s)).toBe(true);
    },
  );

  it.each(['queued', 'running', 'paused'])(
    'treats %s as still live', (s) => {
      expect(isRunOver(s)).toBe(false);
    },
  );

  it('includes the two statuses the old local list omitted', () => {
    // States the bug directly: this is what the deleted
    // ['done','failed','cancelled'] Set could not answer.
    const OLD = new Set(['done', 'failed', 'cancelled']);
    expect(OLD.has('partial')).toBe(false);
    expect(OLD.has('held')).toBe(false);
    expect(isRunOver('partial')).toBe(true);
    expect(isRunOver('held')).toBe(true);
  });
});

describe('gear clears for a stopped run', () => {
  it('clears on partial — the reported case', () => {
    expect(hasRunningTask(anchored('partial'))).toBe(false);
  });

  it('clears on held', () => {
    expect(hasRunningTask(anchored('held'))).toBe(false);
  });

  it('clears on done, failed and cancelled', () => {
    expect(hasRunningTask(anchored('done'))).toBe(false);
    expect(hasRunningTask(anchored('failed'))).toBe(false);
    expect(hasRunningTask(anchored('cancelled'))).toBe(false);
  });
});

describe('gear stays lit while a run is genuinely live', () => {
  it.each(['queued', 'running', 'paused'])('stays lit on %s', (s) => {
    expect(hasRunningTask(anchored(s))).toBe(true);
  });

  it('a paused run counts as running', () => {
    // Paused is under user control but NOT finished — the executor is
    // still alive and will continue, so hiding the gear would suggest
    // the work had ended.
    expect(hasRunningTask(anchored('paused'))).toBe(true);
  });
});

describe('reduction across multiple bindings', () => {
  it('one live run among many terminal ones keeps the gear', () => {
    expect(hasRunningTask(anchored('done', 'partial', 'running')))
      .toBe(true);
  });

  it('all-terminal clears the gear', () => {
    expect(hasRunningTask(anchored('done', 'partial', 'held')))
      .toBe(false);
  });

  it('scans every anchor, not just the first', () => {
    const m = new Map([
      ['msg-1', [{ run_status: 'partial' }]],
      ['msg-2', [{ run_status: 'running' }]],
    ]);
    expect(hasRunningTask(m)).toBe(true);
  });

  it('an empty binding map clears the gear', () => {
    expect(hasRunningTask(new Map())).toBe(false);
  });

  it('an anchor with no bindings clears the gear', () => {
    expect(hasRunningTask(new Map([['msg-1', []]]))).toBe(false);
  });
});

describe('a staged binding does not light the gear', () => {
  it('ignores a binding with no run_status', () => {
    // A staged goal card has a binding but no run yet.  Treating absent
    // as running would spin the gear for a card the user never launched.
    expect(hasRunningTask(anchored(undefined))).toBe(false);
  });

  it('ignores an empty-string status', () => {
    expect(hasRunningTask(anchored(''))).toBe(false);
  });

  it('still lights for a live sibling of a staged binding', () => {
    expect(hasRunningTask(anchored(undefined, 'running'))).toBe(true);
  });
});

describe('an unknown status errs toward showing the gear', () => {
  it('treats an unrecognized status as live', () => {
    // Deliberate: a status this build has never heard of is more likely
    // a newly-added non-terminal one than a terminal one, and a gear
    // that lingers is a smaller error than a finished-looking run that
    // is still writing to the workspace.
    expect(hasRunningTask(anchored('some_future_status'))).toBe(true);
  });
});
