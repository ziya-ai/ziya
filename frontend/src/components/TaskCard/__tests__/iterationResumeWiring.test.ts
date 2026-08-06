/**
 * Wiring for mid-loop resume in the focused-iteration panel.
 *
 * The backend endpoint and its refusals are covered by
 * tests/test_resume_mid_loop*.py. These assert the UI actually reaches
 * it — the gap that left the feature HTTP-only after the server work
 * landed.
 */

import * as fs from 'fs';
import * as path from 'path';

const read = (p: string) =>
  fs.readFileSync(path.resolve(__dirname, p), 'utf-8');

const API = read('../../../services/taskRunApi.ts');
const PANEL = read('../BlockDetailPanel.tsx');
const TILE = read('../TaskCardInlineTile.tsx');
const CSS = read('../task-card-inline-tile.css');

describe('API client', () => {
  it('exposes resumeRunFromIteration', () => {
    expect(API).toMatch(/export async function resumeRunFromIteration/);
  });

  it('targets the resume-iteration route with the index in the path', () => {
    // The index is a path segment, not a query param — which is why this
    // is a separate endpoint rather than a mode on resume-from.
    expect(API).toMatch(/\/resume-iteration\/\$\{encodeURIComponent\(blockId\)\}\/\$\{index\}/);
  });

  it('defines both iteration modes', () => {
    expect(API).toMatch(
      /IterationResumeMode = 'retry_iteration' \| 'continue_iteration'/,
    );
  });

  it('surfaces the server detail on failure', () => {
    // Each 422 names a different actionable refusal (parallel loop,
    // dropped predecessor artifact), so a bare status code is useless.
    const fn = API.match(
      /export async function resumeRunFromIteration[\s\S]*?\n\}/,
    );
    expect(fn).not.toBeNull();
    expect(fn![0]).toMatch(/detail/);
  });
});

describe('detail panel', () => {
  it('accepts both iteration resume handlers', () => {
    expect(PANEL).toMatch(/onRetryIteration\?:/);
    expect(PANEL).toMatch(/onContinueIteration\?:/);
  });

  it('renders the controls only when an iteration is focused', () => {
    // Block-level focus already has its own buttons in the run map; the
    // iteration controls are meaningless without an index.
    expect(PANEL).toMatch(
      /isIter && \(onRetryIteration \|\| onContinueIteration\)/,
    );
  });

  it('explains that earlier iterations are replayed', () => {
    // The reason the buttons live here rather than on an 8px dot: a user
    // who does not know this will assume the loop restarts from zero.
    expect(PANEL).toMatch(/replayed from\s*\n?\s*record/);
  });

  it('passes the focused index to both handlers', () => {
    expect(PANEL).toMatch(/onRetryIteration\(block\.id, iterationIndex!\)/);
    expect(PANEL).toMatch(/onContinueIteration\(block\.id, iterationIndex!\)/);
  });

  it('labels continue with the NEXT index', () => {
    // continue_iteration starts at index+1; labelling it with the
    // focused index would misstate what the button does.
    expect(PANEL).toMatch(/continue from #\{iterationIndex! \+ 1\}/);
  });

  it('disables both while a request is in flight', () => {
    expect(PANEL).toMatch(/disabled=\{resumingIteration != null\}/);
  });
});

describe('tile wiring', () => {
  it('imports the iteration API', () => {
    expect(TILE).toMatch(/resumeRunFromIteration/);
  });

  it('defines a mid-loop resume handler', () => {
    expect(TILE).toMatch(/const handleResumeIteration = useCallback/);
  });

  it('switches the tile to the new attempt', () => {
    // Without this the click looks like it did nothing: the new run is
    // the newest in the lineage but the tile stays on the old one.
    const fn = TILE.match(
      /const handleResumeIteration = useCallback[\s\S]*?\}, \[[^\]]*\]\);/,
    );
    expect(fn).not.toBeNull();
    expect(fn![0]).toMatch(/selectAttempt\(res\.run\.id\)/);
  });

  it('dispatches the binding event so the tile survives a reload', () => {
    const fn = TILE.match(
      /const handleResumeIteration = useCallback[\s\S]*?\}, \[[^\]]*\]\);/,
    );
    expect(fn![0]).toMatch(/TASK_BINDING_EVENT/);
  });

  it('reports an unbound run rather than failing silently', () => {
    const fn = TILE.match(
      /const handleResumeIteration = useCallback[\s\S]*?\}, \[[^\]]*\]\);/,
    );
    expect(fn![0]).toMatch(/will not appear as a tile/);
  });

  it('gates the handlers on the terminal-run flags', () => {
    // The endpoint 409s on a live run and 422s without a card_snapshot,
    // so an ungated control would only ever produce errors.
    expect(TILE).toMatch(
      /onRetryIteration=\{\s*\n?\s*controls\.canResumeFromBlock/,
    );
    expect(TILE).toMatch(
      /onContinueIteration=\{\s*\n?\s*controls\.canContinueFromBlock/,
    );
  });
});

describe('styling', () => {
  it('is not hover-gated', () => {
    // Same discoverability argument that un-hid the per-block buttons:
    // a control that cannot be found was never shipped.
    const block = CSS.match(/\.tc-iter-resume \{[\s\S]*?\}/);
    expect(block).not.toBeNull();
    expect(block![0]).not.toMatch(/opacity:\s*0;/);
  });
});
