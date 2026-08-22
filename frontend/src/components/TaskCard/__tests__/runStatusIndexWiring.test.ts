/**
 * Wiring for project-wide run status in the conversation list.
 *
 * The defect: the gear cluster read only the OPEN chat's bindings, so a run
 * that held or failed in any other conversation rendered nothing until that
 * conversation was visited -- which defeats the purpose of a background
 * indicator entirely.
 *
 * Static source assertions, deliberately.  Every defect in this area has
 * been WIRING rather than logic: a value declared but not exported, a hook
 * whose result nothing reads, a sort memo that ignores the input that
 * changed.  In each case the unit tests on both sides passed while the
 * feature could not work, so these assert the joins exist.
 */

import * as fs from 'fs';
import * as path from 'path';

const read = (rel: string) =>
  fs.readFileSync(path.join(__dirname, '..', '..', '..', rel), 'utf8');

const API = () => read('services/taskRunApi.ts');
const HOOK = () => read('hooks/useRunStatusIndex.ts');
const SIDEBAR = () => read('components/MUIChatHistory.tsx');
const GEARS = () => read('components/TaskCard/RunStatusGears.tsx');
const VOCAB = () => read('components/TaskCard/runStatusVocabulary.ts');

describe('api client', () => {
  it('calls the projection route, not the full run list', () => {
    // Polling listTaskRuns would decrypt every run record in the project
    // (measured: 134 records / 14.5 MB) to learn eight status strings.
    expect(API()).toMatch(/status-index/);
  });

  it('returns an empty index on 404 so an older server still renders', () => {
    const fn = API().match(
      /export async function getRunStatusIndex[\s\S]*?\n\}/,
    );
    expect(fn).not.toBeNull();
    expect(fn![0]).toMatch(/404/);
  });

  it('exposes live and built_at, not just the counts', () => {
    // `live` is what lets the client stop polling; without it an idle
    // project polls forever.
    expect(API()).toMatch(/live: boolean/);
    expect(API()).toMatch(/built_at: number/);
  });
});

describe('poll hook', () => {
  it('polls at the long interval, not the deck rate', () => {
    // Task runs are minutes-to-hours long, so a tighter interval buys no
    // useful latency while multiplying cost across every open window.  The
    // tile (WS) and the deck (4 s) cover a run being actively watched; this
    // one is for the runs nobody is watching.
    expect(HOOK()).toMatch(/const POLL_MS = 40000/);
  });

  it('polls only the current project, never every project', () => {
    // The load property that matters for a user with many projects: the
    // hook takes ONE project id, so switching projects moves the poll
    // rather than accumulating another one.
    expect(HOOK()).toMatch(/useRunStatusIndex\(projectId: string \| null \| undefined\)/);
    // No iteration over a project collection anywhere in the hook.
    expect(HOOK()).not.toMatch(/projects\.(map|forEach|filter)/);
  });

  it('gates the timer on live', () => {
    // The cost guard: a project full of finished runs must poll ONCE.
    expect(HOOK()).toMatch(/if \(!projectId \|\| !index\.live\) return;/);
  });

  it('pauses while the tab is hidden and re-fetches on becoming visible', () => {
    expect(HOOK()).toMatch(/document\.hidden/);
    expect(HOOK()).toMatch(/visibilitychange/);
  });

  it('re-arms on a launch, since the timer stops when nothing is live', () => {
    // Without this, launching a card after the project went idle would
    // never restart polling and the new run would never appear.
    expect(HOOK()).toMatch(/task-binding-created/);
  });

  it('preserves identity when nothing changed', () => {
    // A new object every poll invalidates the sidebar's sort memo and
    // re-renders the whole conversation list on a timer for no visual
    // change.
    expect(HOOK()).toMatch(/sigRef/);
    expect(HOOK()).toMatch(/if \(sig === sigRef\.current\) return;/);
  });

  it('keeps the last good index when a poll fails', () => {
    // An indicator that flickers to empty is worse than a stale one.
    const fn = HOOK().match(/const refresh = useCallback[\s\S]*?\n  \}, \[projectId\]\);/);
    expect(fn).not.toBeNull();
    expect(fn![0]).toMatch(/catch \{/);
    expect(fn![0]).not.toMatch(/catch \{\s*\n\s*setIndex\(EMPTY\)/);
  });

  it('resets when the project changes', () => {
    // One project's runs must not linger on another project's rows.
    expect(HOOK()).toMatch(/setIndex\(EMPTY\)/);
  });
});

describe('vocabulary shares one presentation path', () => {
  it('exposes a counts-based entry point', () => {
    expect(VOCAB()).toMatch(/export function clustersFromCounts/);
  });

  it('takes colour from the FOREGROUND map, not the fill', () => {
    // The sidebar gear is drawn on a surface; RUN_STATUS_FILL.running is
    // tuned as a chip background and drops to ~2.5:1 as a glyph.
    const fn = VOCAB().match(/export function clustersFromCounts[\s\S]*?\n\}/);
    expect(fn).not.toBeNull();
    expect(fn![0]).toMatch(/RUN_STATUS_FG\[status\]/);
    expect(fn![0]).not.toMatch(/RUN_STATUS_FILL\[status\]/);
  });

  it('orders through RUN_STATUS_ORDER rather than object key order', () => {
    // Needs-attention first, so a problem cannot be clipped off the end of
    // a narrow row by successes.  Object key order would be arbitrary.
    const fn = VOCAB().match(/export function clustersFromCounts[\s\S]*?\n\}/);
    expect(fn![0]).toMatch(/of RUN_STATUS_ORDER/);
  });
});

describe('gear component accepts both sources', () => {
  it('takes counts as well as bindings', () => {
    expect(GEARS()).toMatch(/counts\?: Record<string, number> \| null;/);
  });

  it('prefers bindings when present', () => {
    // The open chat's bindings are fresher than a polled projection, so
    // preferring them keeps the row and the tile from disagreeing mid-run.
    expect(GEARS()).toMatch(
      /bindings && bindings\.length > 0\s*\n?\s*\?\s*statusClusters\(bindings\)/,
    );
  });
});

describe('sidebar reads the index', () => {
  it('imports and calls the hook', () => {
    expect(SIDEBAR()).toMatch(/useRunStatusIndex/);
    expect(SIDEBAR()).toMatch(/useRunStatusIndex\(currentProject\?\.id\)/);
  });

  it('resolves per-row counts from the index', () => {
    expect(SIDEBAR()).toMatch(/runStatusIndex\.conversations\[convId\]/);
  });

  it('passes them to the row', () => {
    expect(SIDEBAR()).toMatch(/taskStatusCounts=\{rowTaskCounts\}/);
  });

  it('declares the prop and destructures it', () => {
    expect(SIDEBAR()).toMatch(/taskStatusCounts\?: Record<string, number>;/);
    expect(SIDEBAR()).toMatch(/\n\s*taskStatusCounts,/);
  });

  it('renders gears from counts when there are no bindings', () => {
    // The branch that is the entire point of this change.
    expect(SIDEBAR()).toMatch(/counts=\{taskStatusCounts\}/);
  });

  it('treats the index as an ordering input', () => {
    // A run finishing in a conversation the user is not looking at changes
    // that row without touching any timestamp; omitting it from the sort
    // hash leaves the cached order -- and the rendered list -- stale.
    expect(SIDEBAR()).toMatch(/runStatusIndex\.conversations\)\.forEach/);
    expect(SIDEBAR()).toMatch(/'idx:'/);
  });
});
