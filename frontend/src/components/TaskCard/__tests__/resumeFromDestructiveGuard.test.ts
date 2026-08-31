/**
 * Per-block resume must not read as a continuation.
 *
 * INCIDENT (2026-08-28).  A GFX Stage-2 run sat at status 'held' after an
 * expired-credential fault.  The held badge's own tooltip said "Expand to
 * resume from where it stopped", so the user expanded the run map and
 * clicked the affordance on the row marked HELD HERE — the correct row.
 * That row's button read "↻ from here" and its tooltip read "Resume from
 * this block.", but the endpoint it calls defaults to mode='retry', which
 * RE-EXECUTES the block.  The block was "Consolidate the defect backlog",
 * whose whole job is to write .ziya/gfx-sweep/backlog.json from the triage
 * inputs.  Re-running it regenerated that file from scratch, discarding
 * 192 fix-applied dispositions, 88 fix-groups and every recorded contrast
 * measurement accumulated over five hours by a SEPARATE lineage of the
 * same card.  Unrecoverable: no backup, not tracked in git, and the run
 * records carry no copy.
 *
 * Two things were wrong, and neither was the user's reading:
 *
 * 1. LABEL.  "from here" describes a starting point, not an action.  The
 *    server's own docstring for the endpoint opens "Continue a finished
 *    run, preserving prior state" — true of the RUN RECORD (earlier
 *    blocks replay recorded artifacts) but silent on the fact that the
 *    target block executes again, side effects included.  Nothing on the
 *    button distinguished it from "▶ past here", which genuinely does
 *    preserve the block's outcome.
 *
 * 2. NO GATE.  The click fired immediately. The signing flow in this same
 *    component uses Modal.confirm for a strictly less destructive action,
 *    so the absence here was an inconsistency rather than a house style.
 *
 * These are source-text assertions, matching headerControlsLayout.test.ts
 * and the other *Wiring suites: the defect was never in a pure function,
 * so a unit test over one would have passed throughout. What has to hold
 * is that the rendered affordance says what it does and asks first.
 */

import fs from 'fs';
import path from 'path';

const MAP = fs.readFileSync(
  path.join(__dirname, '..', 'TaskRunMap.tsx'), 'utf8',
);
const TILE = fs.readFileSync(
  path.join(__dirname, '..', 'TaskCardInlineTile.tsx'), 'utf8',
);

/**
 * The retry button's JSX, isolated so an assertion about "the retry
 * affordance" cannot accidentally be satisfied by the continue button
 * sitting immediately after it.  Anchored on the className rather than
 * on line numbers or surrounding prose.
 */
function retryButtonBlock(): string {
  const start = MAP.indexOf('className="tc-map__resume"');
  expect(start).toBeGreaterThan(-1);          // the affordance still exists
  const end = MAP.indexOf('</button>', start);
  expect(end).toBeGreaterThan(start);
  return MAP.slice(start, end);
}

function continueButtonBlock(): string {
  const start = MAP.indexOf('className="tc-map__continue"');
  expect(start).toBeGreaterThan(-1);
  const end = MAP.indexOf('</button>', start);
  expect(end).toBeGreaterThan(start);
  return MAP.slice(start, end);
}

describe('retry affordance names the action, not just the position', () => {
  it('says it re-runs rather than only "from here"', () => {
    const btn = retryButtonBlock();
    // The visible label must carry a verb meaning "execute again".
    // 'from here' alone is what read as a continuation.
    expect(btn).toMatch(/re-?run/i);
  });

  it('warns in the tooltip that the block executes again', () => {
    const btn = retryButtonBlock();
    expect(btn).toMatch(/re-?run|re-?execut/i);
    // The bare pre-incident tooltip must not survive: it is the exact
    // string that promised a continuation.
    expect(btn).not.toContain("'Resume from this block.'");
  });

  it('still distinguishes itself from the continue affordance', () => {
    // Positive control for the two assertions above: they would also pass
    // if the retry button had been deleted outright, or if both buttons
    // had collapsed into one. Both must remain, and remain distinct.
    const retry = retryButtonBlock();
    const cont = continueButtonBlock();
    expect(retry).not.toEqual(cont);
    expect(cont).toMatch(/past here/);
    // Continuing is the non-destructive path and must NOT acquire a
    // re-run warning, or the distinction the user needs is lost again.
    expect(cont).not.toMatch(/re-?run/i);
  });
});

describe('a destructive retry is confirmed before it launches', () => {
  /**
   * The handler body, from its declaration to the dependency array that
   * closes the useCallback.  Scoped so a Modal.confirm belonging to the
   * signing flow elsewhere in this large component cannot satisfy the
   * assertion.
   */
  function handlerBody(): string {
    const start = TILE.indexOf('const handleResumeFrom = useCallback(');
    expect(start).toBeGreaterThan(-1);
    const end = TILE.indexOf('selectAttempt]);', start);
    expect(end).toBeGreaterThan(start);
    return TILE.slice(start, end);
  }

  it('gates on Modal.confirm inside the resume-from handler', () => {
    expect(handlerBody()).toContain('Modal.confirm');
  });

  it('gates only the retry mode, and only when the block already ran', () => {
    const body = handlerBody();
    // Keyed on the mode, so 'continue' is never gated — it accepts the
    // recorded outcome and destroys nothing.
    expect(body).toMatch(/mode === 'retry'/);
    // Keyed on a recorded artifact, so re-running a block that never
    // produced anything stays a single click.  Without this the gate
    // would fire on every resume and get clicked through reflexively.
    expect(body).toMatch(/block_states\??\.?\[?/);
    expect(body).toMatch(/artifact/);
  });

  it('abandons the launch when the user declines', () => {
    const body = handlerBody();
    // The confirm must be able to STOP the request. A Modal.confirm whose
    // onCancel does nothing would render a dialog and launch anyway —
    // strictly worse than no dialog, since it implies a safety that is
    // absent.
    expect(body).toMatch(/onCancel/);
    expect(body).toMatch(/return|resolve\(false\)/);
    // And the request must still be reachable on approval: assert the
    // call survives inside the handler.
    expect(body).toContain('resumeRunFromBlock(');
  });
});

describe('held runs are still the case this all exists for', () => {
  it('keeps held classified terminal so the per-block path is offered', () => {
    // Not a change — a pin. 'held' is deliberately terminal (the executor
    // coroutine has unwound, so there is nothing to pause or step), which
    // is precisely why canResumeFromBlock is the only continuation on
    // offer. If a later change made 'held' non-terminal to give it a
    // run-level Resume, canResumeFromBlock would go false and the held
    // run would be stranded with no forward action at all.
    const CONTROLS = fs.readFileSync(
      path.join(__dirname, '..', 'runControls.ts'), 'utf8',
    );
    const terminal = CONTROLS.match(/const TERMINAL = \[([^\]]+)\]/);
    expect(terminal).not.toBeNull();
    expect(terminal![1]).toContain("'held'");
  });
});
