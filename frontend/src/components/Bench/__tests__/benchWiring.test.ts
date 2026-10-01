/**
 * Seams the render tests cannot see: App mounts the modal, the modal is the
 * only Bench file that calls useBench(), and no renderer reaches the API.
 */
import * as fs from 'fs';
import * as path from 'path';

const benchDir = path.join(__dirname, '..');
const read = (p: string) => fs.readFileSync(p, 'utf8');
// Header comments describe files as "renderers of the useBench() view-model";
// only code that CALLS the hook counts.
const stripComments = (src: string) => src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');

/**
 * For each lazy component declared in ``src``, the Suspense depth at every
 * JSX usage.  A lazy component rendered at depth 0 suspends with no boundary
 * above it; on a synchronous update (a click) React throws #426 and the root
 * crashes -- which is how the first Bench mount failed on 2026-09-28.
 */
function lazyUsagesOutsideSuspense(src: string): string[] {
    const lazyNames = Array.from(src.matchAll(/const\s+(\w+)\s*=\s*(?:lazyWithRetry|React\.lazy|lazy)\(/g))
        .map(m => m[1]);
    const findings: string[] = [];
    let depth = 0;
    // One pass over every tag; only Suspense open/close and lazy opens matter.
    const tag = /<\/?Suspense\b|<(\w+)\b/g;
    let m: RegExpExecArray | null;
    while ((m = tag.exec(src)) !== null) {
        if (m[0] === '<Suspense') depth++;
        else if (m[0] === '</Suspense') depth--;
        else if (m[1] && lazyNames.includes(m[1]) && depth <= 0) {
            const line = src.slice(0, m.index).split('\n').length;
            findings.push(`${m[1]} at line ${line}`);
        }
    }
    return findings;
}

it('App lazily loads BenchModal and a toolbar button opens it', () => {
    const app = read(path.join(benchDir, '..', 'App.tsx'));
    expect(app).toContain('lazyWithRetry(() => import("./Bench/BenchModal"))');
    expect(app).toContain('onClick={() => setShowBench(true)}');
    expect(app).toMatch(/\{showBench && <BenchModal[\s\S]*?projectId=\{currentProject\?\.id\}/);
    // Exactly one mount: a second copy outside the Suspense boundary is what
    // crashed the root on first open.
    expect(app.match(/<BenchModal\b/g)).toHaveLength(1);
});

it('no lazy component in App.tsx is rendered outside a Suspense boundary', () => {
    const app = read(path.join(benchDir, '..', 'App.tsx'));
    expect(lazyUsagesOutsideSuspense(app)).toEqual([]);
    // Positive control: the scanner does see an unguarded lazy mount.
    expect(lazyUsagesOutsideSuspense(
        'const Foo = lazyWithRetry(() => import("./Foo"));\n<Suspense>\n<Bar/>\n</Suspense>\n{x && <Foo />}\n',
    )).toEqual(['Foo at line 5']);
});

it('only the modal calls useBench(); no Bench renderer imports the API client', () => {
    const files = fs.readdirSync(benchDir).filter(f => /\.tsx?$/.test(f) && f !== 'benchTestUtils.ts');
    expect(files).toEqual(expect.arrayContaining(['BenchModal.tsx', 'BenchPanel.tsx', 'BenchBoard.tsx']));
    const callers = files.filter(f => /\buseBench\(/.test(stripComments(read(path.join(benchDir, f)))));
    expect(callers).toEqual(['BenchModal.tsx']);
    for (const f of files) {
        expect({ f, imports: /apis\/benchApi/.test(read(path.join(benchDir, f))) }).toEqual({ f, imports: false });
    }
});

it('the modal provides BenchActions and App hands it the MCP panel openers', () => {
    // Consumers (card back, panel) read useBenchActions(); without the
    // provider in the shell every link silently renders nothing.
    const modal = read(path.join(benchDir, 'BenchModal.tsx'));
    expect(modal).toMatch(/<BenchActionsProvider actions=\{actions\}>/);
    const app = read(path.join(benchDir, '..', 'App.tsx'));
    expect(app).toMatch(/<BenchModal[\s\S]*?actions=\{mcpEnabled \? benchActions : \{\}\}/);
});

// ---------------------------------------------------------------------------
// Partially-applied batches.  Three times a multi-file change landed with one
// new file missing while the edits importing it applied, and the first sign
// was a broken build.  These two guards move that failure into the test run.
// ---------------------------------------------------------------------------

const EXTS = ['.ts', '.tsx', '/index.ts', '/index.tsx'];

/** Relative imports in ``src`` (resolved from ``fromDir``) with no file behind them. */
function unresolvedRelativeImports(src: string, fromDir: string): string[] {
    const out: string[] = [];
    for (const m of src.matchAll(/from\s+['"](\.{1,2}\/[^'"]+)['"]/g)) {
        const base = path.resolve(fromDir, m[1]);
        if (!EXTS.some(ext => fs.existsSync(base + ext)) && !fs.existsSync(base)) out.push(m[1]);
    }
    return out;
}

it('every relative import under Bench/ (and its tests) resolves to a file', () => {
    const findings: string[] = [];
    for (const dir of [benchDir, path.join(benchDir, '__tests__')]) {
        // This file holds import-shaped string literals as probe data; it is not a consumer.
        for (const f of fs.readdirSync(dir).filter(f => /\.tsx?$/.test(f) && path.join(dir, f) !== __filename)) {
            for (const imp of unresolvedRelativeImports(read(path.join(dir, f)), dir)) {
                findings.push(`${path.relative(benchDir, path.join(dir, f))} -> ${imp}`);
            }
        }
    }
    expect(findings).toEqual([]);
    // Positive control: the scanner does report a missing module.
    expect(unresolvedRelativeImports("import { x } from './DoesNotExist';\nimport y from './laneDnd';", benchDir))
        .toEqual(['./DoesNotExist']);
});

it('Bench/ type-checks under the project tsconfig (a dropped hunk shows up here, not in the build)', () => {
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const ts = require('typescript') as typeof import('typescript');
    const frontendRoot = path.resolve(benchDir, '..', '..', '..');
    const cfg = ts.readConfigFile(path.join(frontendRoot, 'tsconfig.json'), ts.sys.readFile);
    expect(cfg.error).toBeUndefined();
    const parsed = ts.parseJsonConfigFileContent(cfg.config, ts.sys, frontendRoot);
    const roots = fs.readdirSync(benchDir).filter(f => /\.tsx?$/.test(f)).map(f => path.join(benchDir, f));
    const program = ts.createProgram(roots, { ...parsed.options, noEmit: true });
    // Only diagnostics IN Bench files: the rest of the tree is not this suite's job.
    const diags = ts.getPreEmitDiagnostics(program)
        .filter(d => d.file && d.file.fileName.includes('/components/Bench/'))
        .map(d => {
            const { line } = d.file!.getLineAndCharacterOfPosition(d.start ?? 0);
            return `${path.basename(d.file!.fileName)}:${line + 1} TS${d.code} ${ts.flattenDiagnosticMessageText(d.messageText, ' ')}`;
        });
    expect(diags).toEqual([]);

    // Positive control: a Bench file that misuses its own props IS reported.
    // Same compiler options, so a config that suppressed errors would fail here.
    const bad = path.join(benchDir, '__typecheck_probe__.tsx');
    const probe = "import React from 'react';\nimport { CapabilityCard } from './CapabilityCard';\n" +
                  'export const P = () => <CapabilityCard item={1 as any} onIntent={() => {}} notAProp={1} />;\n';
    const host = ts.createCompilerHost({ ...parsed.options, noEmit: true });
    const realRead = host.readFile;
    host.readFile = (f: string) => (path.resolve(f) === bad ? probe : realRead(f));
    const realExists = host.fileExists;
    host.fileExists = (f: string) => path.resolve(f) === bad || realExists(f);
    const probeProgram = ts.createProgram([bad], { ...parsed.options, noEmit: true }, host);
    const probeDiags = ts.getPreEmitDiagnostics(probeProgram).filter(d => d.file && path.resolve(d.file.fileName) === bad);
    expect(probeDiags.map(d => d.code)).toContain(2322);
}, 60_000);
