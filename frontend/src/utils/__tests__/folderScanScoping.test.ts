/**
 * Scan-lifecycle scoping and mid-scan refetch scheduling.
 *
 * Both helpers exist because of one observed failure: switching projects left
 * the file tree showing almost nothing (only the pre-populated `[external]`
 * entries) for minutes while the backend scan ran, and it never filled in.
 *
 * Two independent causes, both covered here:
 *
 *  1. `scan_complete` was broadcast over a socket shared by every window with
 *     an empty path.  Any project finishing a scan therefore cleared
 *     `isScanning` in EVERY window, including one still scanning a different
 *     project.  That stopped the progress poller — and the poller is also the
 *     fallback that refetches the finished tree — so the affected window sat
 *     on whatever partial tree it had last received.
 *
 *  2. Even with the poller alive, nothing refetched the tree DURING a scan.
 *     The backend publishes a progressively deepening partial tree, but the
 *     frontend fetched once at scan start and then not again until completion.
 */

import {
    scanEventTargetsProject,
    nextScanRefetchInterval,
    SCAN_REFETCH_INITIAL_TICK,
    SCAN_REFETCH_MAX_INTERVAL_TICKS,
} from '../folderUtil';

describe('scanEventTargetsProject', () => {
    const A = '/Users/dcohn/workspace/KuiperEAR-switch-mgr';
    const B = '/Users/dcohn/workspace/ziya-0.4.0.1';

    it('accepts an event for this window\'s own project', () => {
        expect(scanEventTargetsProject(A, A)).toBe(true);
    });

    it('rejects an event for a different project', () => {
        // The regression: project B finishing must not clear scan state in a
        // window watching project A.
        expect(scanEventTargetsProject(B, A)).toBe(false);
    });

    it('normalizes trailing separators on either side', () => {
        expect(scanEventTargetsProject(`${A}/`, A)).toBe(true);
        expect(scanEventTargetsProject(A, `${A}/`)).toBe(true);
        expect(scanEventTargetsProject(`${A}/`, `${A}/`)).toBe(true);
    });

    it('does not treat a sibling with a shared prefix as a match', () => {
        // Guards against a naive startsWith() comparison.
        expect(scanEventTargetsProject(`${A}-old`, A)).toBe(false);
    });

    it('accepts a pathless event, for backends that do not scope it', () => {
        // Older servers send scan_complete with an empty path.  Dropping it
        // would strand isScanning=true forever, which is worse than the
        // over-broad refetch it causes.
        expect(scanEventTargetsProject('', A)).toBe(true);
        expect(scanEventTargetsProject(undefined, A)).toBe(true);
    });

    it('accepts any event when the window has no project path yet', () => {
        // During early load we cannot scope, so fail open rather than risk
        // never clearing the scanning indicator.
        expect(scanEventTargetsProject(A, undefined)).toBe(true);
        expect(scanEventTargetsProject(A, '')).toBe(true);
    });
});

describe('nextScanRefetchInterval', () => {
    it('doubles the interval on each refetch', () => {
        expect(nextScanRefetchInterval(3)).toBe(6);
        expect(nextScanRefetchInterval(6)).toBe(12);
        expect(nextScanRefetchInterval(12)).toBe(24);
    });

    it('caps the interval so a long scan still refreshes periodically', () => {
        expect(nextScanRefetchInterval(24)).toBe(SCAN_REFETCH_MAX_INTERVAL_TICKS);
        expect(nextScanRefetchInterval(SCAN_REFETCH_MAX_INTERVAL_TICKS))
            .toBe(SCAN_REFETCH_MAX_INTERVAL_TICKS);
    });

    it('never returns a non-advancing interval', () => {
        // A zero/negative interval would busy-refetch on every 1s poll.
        for (const start of [0, -5, 1]) {
            expect(nextScanRefetchInterval(start)).toBeGreaterThanOrEqual(1);
        }
    });
});

describe('mid-scan refetch schedule', () => {
    /** Replays the scheduling arithmetic used by the 1s progress poller. */
    const refetchTicksOverScan = (totalTicks: number): number[] => {
        const fired: number[] = [];
        let interval = SCAN_REFETCH_INITIAL_TICK;
        let nextTick = SCAN_REFETCH_INITIAL_TICK;
        for (let tick = 1; tick <= totalTicks; tick++) {
            if (tick >= nextTick) {
                fired.push(tick);
                interval = nextScanRefetchInterval(interval);
                nextTick = tick + interval;
            }
        }
        return fired;
    };

    it('refetches early and often, then backs off', () => {
        // Polls are 1s apart, so these are ~seconds into the scan.
        expect(refetchTicksOverScan(120)).toEqual([3, 9, 21, 45, 75, 105]);
    });

    it('paints the tree within a few seconds of the scan starting', () => {
        // The user-visible symptom was an empty tree for minutes; the first
        // refetch must land early regardless of how long the scan runs.
        const first = refetchTicksOverScan(200)[0];
        expect(first).toBe(SCAN_REFETCH_INITIAL_TICK);
        expect(first).toBeLessThanOrEqual(5);
    });

    it('keeps refreshing across a multi-minute scan', () => {
        // The 79s+ scan in the observed report must get several updates.
        const fired = refetchTicksOverScan(80);
        expect(fired.length).toBeGreaterThanOrEqual(4);
    });

    it('never fires twice on the same poll tick', () => {
        const fired = refetchTicksOverScan(300);
        expect(new Set(fired).size).toBe(fired.length);
    });

    it('bounds total refetches so convertToTreeData is not run constantly', () => {
        // 300 ticks = 5 minutes of scanning.  Each refetch re-converts the
        // whole (growing) tree, so the count must stay small.
        expect(refetchTicksOverScan(300).length).toBeLessThan(15);
    });
});
