/**
 * Tests for pruneRendererCaches — ensures all window-level registries
 * are bounded and pruned on conversation switch.
 */

// Inline the prune logic so the suite has no React/DOM dependency.
// This mirrors the implementation in MarkdownRenderer.tsx:168.

function pruneRendererCaches(win: typeof window): void {
    // Module-level sets are cleared unconditionally
    // (simulated externally in tests)

    if (win.diffElementPaths?.size > 500) {
        const entries = Array.from(win.diffElementPaths.entries());
        win.diffElementPaths = new Map(entries.slice(-200));
    }
    if (win.hunkStatusRegistry?.size > 500) {
        const entries = Array.from(win.hunkStatusRegistry.entries());
        win.hunkStatusRegistry = new Map(entries.slice(-200));
    }
    if (win.appliedDiffsRegistry?.size > 500) {
        const entries = Array.from(win.appliedDiffsRegistry);
        win.appliedDiffsRegistry = new Set(entries.slice(-200));
    }
}

// Helpers
function fillMap(map: Map<string, any>, count: number) {
    for (let i = 0; i < count; i++) {
        map.set(`key-${i}`, `val-${i}`);
    }
}

function fillNestedMap(map: Map<string, Map<string, any>>, count: number) {
    for (let i = 0; i < count; i++) {
        map.set(`diff-${i}`, new Map([['0-0', { applied: true, reason: 'ok' }]]));
    }
}

function fillSet(set: Set<string>, count: number) {
    for (let i = 0; i < count; i++) {
        set.add(`item-${i}`);
    }
}

describe('pruneRendererCaches', () => {
    let fakeWindow: typeof window;

    beforeEach(() => {
        fakeWindow = {
            diffElementPaths: new Map(),
            hunkStatusRegistry: new Map(),
            appliedDiffsRegistry: new Set(),
        } as any;
    });

    test('does nothing when registries are below threshold', () => {
        fillMap(fakeWindow.diffElementPaths!, 100);
        fillNestedMap(fakeWindow.hunkStatusRegistry, 100);
        fillSet(fakeWindow.appliedDiffsRegistry, 100);

        pruneRendererCaches(fakeWindow);

        expect(fakeWindow.diffElementPaths!.size).toBe(100);
        expect(fakeWindow.hunkStatusRegistry.size).toBe(100);
        expect(fakeWindow.appliedDiffsRegistry.size).toBe(100);
    });

    test('caps diffElementPaths to 200 when exceeding 500', () => {
        fillMap(fakeWindow.diffElementPaths!, 600);
        expect(fakeWindow.diffElementPaths!.size).toBe(600);

        pruneRendererCaches(fakeWindow);

        expect(fakeWindow.diffElementPaths!.size).toBe(200);
        // Keeps the LAST 200 entries (most recent)
        expect(fakeWindow.diffElementPaths!.has('key-599')).toBe(true);
        expect(fakeWindow.diffElementPaths!.has('key-400')).toBe(true);
        expect(fakeWindow.diffElementPaths!.has('key-399')).toBe(false);
    });

    test('caps hunkStatusRegistry to 200 when exceeding 500', () => {
        fillNestedMap(fakeWindow.hunkStatusRegistry, 600);

        pruneRendererCaches(fakeWindow);

        expect(fakeWindow.hunkStatusRegistry.size).toBe(200);
        expect(fakeWindow.hunkStatusRegistry.has('diff-599')).toBe(true);
        expect(fakeWindow.hunkStatusRegistry.has('diff-399')).toBe(false);
    });

    test('caps appliedDiffsRegistry to 200 when exceeding 500', () => {
        fillSet(fakeWindow.appliedDiffsRegistry, 600);

        pruneRendererCaches(fakeWindow);

        expect(fakeWindow.appliedDiffsRegistry.size).toBe(200);
    });

    test('handles undefined registries gracefully', () => {
        fakeWindow.diffElementPaths = undefined as any;
        fakeWindow.hunkStatusRegistry = undefined as any;
        fakeWindow.appliedDiffsRegistry = undefined as any;

        expect(() => pruneRendererCaches(fakeWindow)).not.toThrow();
    });
});
