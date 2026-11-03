/**
 * Every lazy component App.tsx mounts must sit inside a LazyChunkBoundary.
 *
 * A lazy chunk that cannot be fetched (stale build, or the server restarting
 * underneath an open tab) rejects with ChunkLoadError, or with
 * lazyWithRetry's "recovery reload did not complete" once the hard reload
 * fails to land.  A Suspense boundary does not catch rejections; without a
 * LazyChunkBoundary the error climbs to RootErrorBoundary and one
 * auto-mounted indicator takes the whole page down.  That happened on
 * 2026-10-08 with AstStatusIndicator / GraphPanel, which were the only two
 * App lazies still outside a boundary.
 */
import * as fs from 'fs';
import * as path from 'path';

function lazyUsagesOutsideChunkBoundary(src: string): string[] {
    const lazyNames = Array.from(src.matchAll(/const\s+(\w+)\s*=\s*(?:lazyWithRetry|React\.lazy|lazy)\(/g))
        .map(m => m[1]);
    const findings: string[] = [];
    let depth = 0;
    const tag = /<\/?LazyChunkBoundary\b|<(\w+)\b/g;
    let m: RegExpExecArray | null;
    while ((m = tag.exec(src)) !== null) {
        if (m[0] === '<LazyChunkBoundary') depth++;
        else if (m[0] === '</LazyChunkBoundary') depth--;
        else if (m[1] && lazyNames.includes(m[1]) && depth <= 0) {
            const line = src.slice(0, m.index).split('\n').length;
            findings.push(`${m[1]} at line ${line}`);
        }
    }
    return findings;
}

const app = fs.readFileSync(path.join(__dirname, '..', 'App.tsx'), 'utf8');

it('no lazy component in App.tsx is rendered outside a LazyChunkBoundary', () => {
    // The scan must actually see App's lazies, or an empty result proves nothing.
    expect(app).toMatch(/const AstStatusIndicator = lazyWithRetry\(/);
    expect(app).toMatch(/<AstStatusIndicator\b/);
    expect(app).toMatch(/<GraphPanel\b/);
    expect(lazyUsagesOutsideChunkBoundary(app)).toEqual([]);
});

it('the scanner flags an unguarded lazy mount (positive control)', () => {
    expect(lazyUsagesOutsideChunkBoundary(
        'const Foo = lazyWithRetry(() => import("./Foo"));\n'
        + '<LazyChunkBoundary onDismiss={d}><Suspense><Foo/></Suspense></LazyChunkBoundary>\n'
        + '<Suspense>\n<Foo />\n</Suspense>\n',
    )).toEqual(['Foo at line 4']);
});

it('the boundary dismisses by unmounting the lazy, so the cached rejection is not re-thrown in a loop', () => {
    // LazyChunkBoundary resets to failed:false after onDismiss; if onDismiss
    // does not remove the lazy child, React.lazy re-throws its cached
    // rejection and the boundary catches it forever.
    expect(app).toMatch(/<LazyChunkBoundary onDismiss=\{\(\) => setAstEnabled\(false\)\}>/);
    expect(app).toMatch(/<LazyChunkBoundary onDismiss=\{\(\) => setGraphPanelOpen\(false\)\}>/);
});
