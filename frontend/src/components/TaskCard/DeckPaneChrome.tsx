/**
 * DeckPaneChrome — the collapse tab and the collapsed strip of a Task
 * Cards dialog pane.
 *
 * The two are drawn as one control in two states: the tab in an open
 * pane's header corner carries the same label as the strip the pane
 * collapses into, on the edge where that strip will appear.  Collapsing
 * and reopening therefore happen in the same place.
 */

import React from 'react';
import './task-deck.css';

type Side = 'left' | 'right';
type Tone = 'plain' | 'held';

interface TabProps {
  label: string;
  /** Edge the pane folds toward. */
  side: Side;
  onCollapse: () => void;
  title: string;
  tone?: Tone;
}

/** Header-corner control of an OPEN pane. */
export const DeckPaneTab: React.FC<TabProps> = ({
  label, side, onCollapse, title, tone = 'plain',
}) => (
  <button
    type="button"
    className={`tc-deck__tab tc-deck__tab--${side} tc-deck__tab--${tone}`}
    onClick={onCollapse}
    title={title}
    data-testid={`deck-collapse-${label.toLowerCase()}`}
  >
    {side === 'left' ? `« ${label}` : `${label} »`}
  </button>
);

interface StripProps {
  label: string;
  /** Edge the strip sits on. */
  side: Side;
  onOpen: () => void;
  title: string;
  tone?: Tone;
  /** State the pane keeps showing while collapsed (a count, a dot). */
  badge?: React.ReactNode;
}

/** A COLLAPSED pane: a full-height strip that reopens it. */
export const DeckPaneStrip: React.FC<StripProps> = ({
  label, side, onOpen, title, tone = 'plain', badge,
}) => (
  <button
    type="button"
    className={`tc-deck__strip tc-deck__strip--${side} tc-deck__strip--${tone}`}
    onClick={onOpen}
    title={title}
    data-testid={`deck-open-${label.split(' ')[0].toLowerCase()}`}
  >
    <span className="tc-deck__strip-chevron">{side === 'left' ? '»' : '«'}</span>
    <span className="tc-deck__strip-label">{label}</span>
    {badge != null && <span className="tc-deck__strip-badge">{badge}</span>}
  </button>
);
