/**
 * Tests for DiffToken context enhancement overlay logic.
 *
 * The DiffToken component shows different overlays depending on:
 * 1. Whether "auto-add diff files to context" is enabled
 * 2. Whether files are missing from context
 * 3. Whether token counts are available for those files
 *
 * This test file verifies the pure decision logic extracted from
 * the contextEnhancementOverlay in MarkdownRenderer.tsx.
 */

interface OverlayDecision {
    /** Whether to show the overlay at all */
    show: boolean;
    /** Which variant to display */
    variant: 'checking' | 'auto-added' | 'manual-add' | 'none';
    /** Token count string to display (only for auto-added variant) */
    tokenDisplay: string | null;
}

/**
 * Pure decision function matching the overlay logic in DiffToken.
 */
function computeOverlayDecision(
    isCheckingFiles: boolean,
    needsContextEnhancement: boolean,
    autoAddDiffFiles: boolean,
    missingFilesList: string[],
    accurateTokenCounts: Record<string, { count: number; timestamp: number }>
): OverlayDecision {
    if (!isCheckingFiles && !needsContextEnhancement) {
        return { show: false, variant: 'none', tokenDisplay: null };
    }

    if (isCheckingFiles) {
        return { show: true, variant: 'checking', tokenDisplay: null };
    }

    if (needsContextEnhancement && autoAddDiffFiles) {
        const totalTokens = missingFilesList.reduce((sum, file) => {
            const entry = accurateTokenCounts[file];
            return sum + (entry ? entry.count : 0);
        }, 0);
        const hasEstimates = missingFilesList.some(f => accurateTokenCounts[f]);
        return {
            show: true,
            variant: 'auto-added',
            tokenDisplay: hasEstimates ? `~${totalTokens.toLocaleString()} tokens` : null,
        };
    }

    if (needsContextEnhancement) {
        return { show: true, variant: 'manual-add', tokenDisplay: null };
    }

    return { show: false, variant: 'none', tokenDisplay: null };
}

describe('DiffToken context enhancement overlay', () => {
    it('returns none when no files are missing and not checking', () => {
        const result = computeOverlayDecision(false, false, true, [], {});
        expect(result.show).toBe(false);
        expect(result.variant).toBe('none');
    });

    it('returns checking variant while files are being checked', () => {
        const result = computeOverlayDecision(true, false, true, [], {});
        expect(result.show).toBe(true);
        expect(result.variant).toBe('checking');
    });

    it('returns auto-added variant when autoAddDiffFiles is enabled', () => {
        const result = computeOverlayDecision(
            false, true, true,
            ['src/foo.ts', 'src/bar.ts'],
            {}
        );
        expect(result.show).toBe(true);
        expect(result.variant).toBe('auto-added');
        // No token counts available yet
        expect(result.tokenDisplay).toBeNull();
    });

    it('returns manual-add variant when autoAddDiffFiles is disabled', () => {
        const result = computeOverlayDecision(
            false, true, false,
            ['src/foo.ts'],
            {}
        );
        expect(result.show).toBe(true);
        expect(result.variant).toBe('manual-add');
        expect(result.tokenDisplay).toBeNull();
    });

    it('includes token count when estimates are available (auto-add)', () => {
        const counts = {
            'src/foo.ts': { count: 1200, timestamp: Date.now() / 1000 },
            'src/bar.ts': { count: 800, timestamp: Date.now() / 1000 },
        };
        const result = computeOverlayDecision(
            false, true, true,
            ['src/foo.ts', 'src/bar.ts'],
            counts
        );
        expect(result.show).toBe(true);
        expect(result.variant).toBe('auto-added');
        expect(result.tokenDisplay).toBe('~2,000 tokens');
    });

    it('includes partial token count when only some files have estimates', () => {
        const counts = {
            'src/foo.ts': { count: 500, timestamp: Date.now() / 1000 },
            // src/bar.ts has no estimate yet
        };
        const result = computeOverlayDecision(
            false, true, true,
            ['src/foo.ts', 'src/bar.ts'],
            counts
        );
        expect(result.show).toBe(true);
        expect(result.variant).toBe('auto-added');
        // Still shows the partial total since at least one file has an estimate
        expect(result.tokenDisplay).toBe('~500 tokens');
    });

    it('does not show token count for manual-add variant even when available', () => {
        const counts = {
            'src/foo.ts': { count: 1000, timestamp: Date.now() / 1000 },
        };
        const result = computeOverlayDecision(
            false, true, false,
            ['src/foo.ts'],
            counts
        );
        expect(result.variant).toBe('manual-add');
        expect(result.tokenDisplay).toBeNull();
    });

    it('checking variant takes priority over needsContextEnhancement', () => {
        // Both flags true — checking should win
        const result = computeOverlayDecision(true, true, true, ['src/foo.ts'], {});
        expect(result.variant).toBe('checking');
    });

    it('handles empty missing files list with auto-add', () => {
        // Shouldn't normally happen, but defensive check
        const result = computeOverlayDecision(false, true, true, [], {});
        expect(result.show).toBe(true);
        expect(result.variant).toBe('auto-added');
        expect(result.tokenDisplay).toBeNull();
    });
});
