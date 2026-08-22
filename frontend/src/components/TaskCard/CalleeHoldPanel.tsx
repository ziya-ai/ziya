/**
 * CalleeHoldPanel — a card's own view of a hold it is stuck inside.
 *
 * A Call executes its target inline in the CALLER's run, so a six-card
 * study produces one run record owned by the outermost card.  Opening an
 * inner card in the deck therefore showed nothing at all — no runs, no
 * status — even while that card was the one holding the study.  This is
 * that card's answer to "where am I stuck?", resolved from the caller's
 * run against this card's own block ids.
 *
 * Layout is option 1A: a banner naming the caller and the breadth, then
 * this card's own blocks marked with their position relative to the
 * fault.  The banner carries what the rows cannot (which run, how wide),
 * and the rows carry what a banner cannot (which stage, and what is
 * stranded behind it).
 *
 * Renders nothing at all when there is no context, so a card that has
 * never been called adds no chrome to the deck.
 */

import React, { useEffect, useState } from 'react';
import type { Block } from '../../types/task_card';
import type { CalleeContext } from '../../types/task_run';
import { getCalleeContext } from '../../services/taskRunApi';
import {
  deriveCalleeHoldChain, positionOf, holdLabel,
  describeBreadth, describeGate,
} from './holdChain';
import { flattenBlocks } from './runMapModel';

interface Props {
  projectId: string;
  cardId: string | null;
  /** This card's own root, for marking up its blocks. */
  root: Block | null | undefined;
}

const POSITION_LABELS: Record<string, string> = {
  local: 'HELD HERE',
  descendant: 'holding',
  ancestor: 'blocked',
};

/** Glyph per position.  Independent of BlockStatus: this card has no run
 *  of its own, so there is no lifecycle status to read — only position. */
const POSITION_GLYPHS: Record<string, string> = {
  local: '⏸', descendant: '⏸', ancestor: '○', none: '✓',
};

export const CalleeHoldPanel: React.FC<Props> = ({
  projectId, cardId, root,
}) => {
  const [ctx, setCtx] = useState<CalleeContext | null>(null);

  // Fetch, then keep fetching while the caller is still live.
  //
  // A single fetch on card-select was wrong in the case this panel exists
  // for: a study running for hours holds partway through, and the deck —
  // already open, already showing this card — would keep reporting
  // "running in CL0" until the user happened to reselect the card.  The
  // surface whose whole job is to answer "am I stuck?" cannot only answer
  // as of whenever you last clicked.
  //
  // Polling rather than the WS stream used for a single active run: this
  // card owns no run (a Call executes inline in the caller's), so there is
  // no run id to subscribe to from here — the caller's id is discovered BY
  // this request.  4000ms and the same "poll only while live" gating as
  // the deck's own run-list poll above it, deliberately: two surfaces in
  // one modal polling at different rates would make the deck's state read
  // as inconsistent with itself.
  useEffect(() => {
    if (!projectId || !cardId) { setCtx(null); return; }
    let cancelled = false;

    const load = async () => {
      try {
        const list = await getCalleeContext(projectId, cardId);
        if (cancelled) return;
        // Prefer a hold that is actually THIS card's; fall back to the
        // most recent invocation so a merely-running study still reports
        // its position (option 3A).  Without the preference a card called
        // twice could show the healthy invocation and hide the held one.
        const held = list.find(c => c.held_in_callee);
        setCtx(held ?? list[0] ?? null);
      } catch {
        // Only blank on the FIRST failure.  A transient error mid-poll
        // must not clear a hold the user is currently reading — the same
        // "keep the last good value" rule the deck's run poll follows.
        if (!cancelled) setCtx(prev => prev ?? null);
      }
    };

    load();
    return () => { cancelled = true; };
  }, [projectId, cardId]);

  // Separate effect for the timer so it can be gated on what the FIRST
  // fetch found.  A held run is terminal: once this card reports held,
  // nothing further will change until the user acts, so continuing to
  // poll would be pure noise.  An idle card polls not at all.
  const shouldPoll = !!ctx && (ctx.run_status === 'running' || ctx.run_status === 'paused');
  useEffect(() => {
    if (!projectId || !cardId || !shouldPoll) return;
    let cancelled = false;
    const timer = setInterval(async () => {
      try {
        const list = await getCalleeContext(projectId, cardId);
        if (cancelled) return;
        const held = list.find(c => c.held_in_callee);
        setCtx(held ?? list[0] ?? null);
      } catch {
        // Transient: keep the last good context.
      }
    }, 4000);
    return () => { cancelled = true; clearInterval(timer); };
  }, [projectId, cardId, shouldPoll]);

  if (!ctx) return null;

  const chain = deriveCalleeHoldChain(ctx);
  const breadth = describeBreadth(chain.faults);
  const gate = describeGate(chain);
  const rows = flattenBlocks(root, 0);

  // 3A: report position even when nothing is wrong.  Answering only on a
  // fault would train the surface to read as an error indicator rather
  // than a location indicator — and "shows nothing while executing
  // inside another card" is the exact confusion this panel exists to fix.
  if (!chain.isHeld) {
    return (
      <div className="tc-callee tc-callee--ok">
        <span className="tc-callee__live">{ctx.run_status}</span>
        <span className="tc-callee__in">
          in{' '}
          <code className="tc-callee__caller">{ctx.caller_card_id}</code>
        </span>
      </div>
    );
  }

  return (
    <div className="tc-callee" role="region" aria-label="Hold position">
      <div className="tc-callee__head">
        <span className="tc-callee__pause">⏸</span>
        {' '}Held inside{' '}
        <code className="tc-callee__caller">{ctx.caller_card_id}</code>
      </div>

      <div className="tc-callee__breadth">
        {chain.faults?.fleet_wide && (
          <span className="tc-callee__fleet">FLEET-WIDE</span>
        )}
        {chain.kind && <code className="tc-callee__kind">{chain.kind}</code>}
        {breadth && <span className="tc-callee__n">{breadth}</span>}
      </div>

      {gate && <div className="tc-callee__remedy">{gate}</div>}

      {/* This card's OWN blocks, marked with their position.  Suppressed
          for a single-node card for the same reason TaskRunMap is: one
          row restates the banner. */}
      {rows.length > 1 && (
        <div className="tc-callee__map">
          {rows.map(({ block, depth }) => {
            const pos = positionOf(chain, block.id);
            return (
              <div
                key={block.id}
                className={`tc-callee__row tc-callee__row--${pos}`}
                style={{ paddingLeft: 6 + depth * 14 }}
                title={holdLabel(chain, pos) ?? undefined}
              >
                <span className={`tc-callee__icon tc-callee__icon--${pos}`}>
                  {POSITION_GLYPHS[pos]}
                </span>
                <span className="tc-callee__label">
                  {block.name || block.id}
                </span>
                {pos !== 'none' && (
                  <span className={`tc-callee__badge tc-callee__badge--${pos}`}>
                    {POSITION_LABELS[pos]}
                  </span>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
};

export default CalleeHoldPanel;
