/**
 * "Find more" in the Bench opens the MCP Registry, which must land IN FRONT of
 * the Bench.  Both are antd Modals at z-index 1000, so the DOM order of their
 * portal containers in <body> decides which paints on top.
 *
 * Measured in Chrome (scripts/ui_probe/bench_registry_front.mjs): with the
 * Bench in the same Suspense boundary as the lazy MCP modals, the registry's
 * chunk load suspended that boundary, React hid the Bench (running its
 * portal's layout cleanup, which removes the container), and on reveal the
 * Bench container was re-appended AFTER the registry's.  The same applied to
 * the MCP Servers and shell-config panels the Bench opens.
 *
 * jsdom does not reproduce the hide/re-append (the container stays put), so
 * this pins the seam in App.tsx; the probe script is the end-to-end check.
 */
import * as fs from 'fs';
import * as path from 'path';

const src = fs.readFileSync(path.join(__dirname, '..', 'App.tsx'), 'utf8');

describe('BenchModal Suspense boundary', () => {
    const bench = src.indexOf('{showBench && <BenchModal');
    const registry = src.indexOf('{showMCPRegistry && <MCPRegistryModal');
    const status = src.indexOf('{showMCPStatus && <MCPStatusModal');
    const shell = src.indexOf('{showShellConfig && <ShellConfigModal');

    it('every modal the Bench opens is outside the Bench boundary', () => {
        for (const i of [bench, registry, status, shell]) expect(i).toBeGreaterThan(-1);
        // Nearest <Suspense opening before the Bench comes after all three.
        const benchOpen = src.lastIndexOf('<Suspense', bench);
        expect(benchOpen).toBeGreaterThan(Math.max(registry, status, shell));
        // ...and the MCP modals' boundary is closed before it opens.
        expect(src.slice(Math.max(registry, status, shell), benchOpen)).toContain('</Suspense>');
    });

    it('the Bench boundary closes right after the Bench', () => {
        const close = src.indexOf('</Suspense>', bench);
        const nextModal = src.indexOf('<ExportConversationModal', bench);
        expect(close).toBeGreaterThan(bench);
        expect(close).toBeLessThan(nextModal);
    });
});
