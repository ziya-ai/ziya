import React from 'react';

/**
 * Sidebar marker for a plain fork (lineageKind='fork'): the fork-row
 * counterpart of the ⛓ a handoff continuation carries.
 *
 * Inline SVG stroked with currentColor, so it follows the theme and the
 * selected-row text colour without per-mode styling.  Sized in em so it
 * tracks the label's font size.
 */
export interface ForkGlyphProps {
  title?: string;
  style?: React.CSSProperties;
}

const ForkGlyph: React.FC<ForkGlyphProps> = ({ title = 'Forked from another conversation', style }) => (
  <span
    title={title}
    data-testid="fork-glyph"
    style={{ marginRight: 4, opacity: 0.8, display: 'inline-flex', verticalAlign: '-0.125em', ...style }}
  >
    <svg
      width="1em" height="1em" viewBox="0 0 16 16"
      fill="none" stroke="currentColor" strokeWidth={1.6}
      strokeLinecap="round" strokeLinejoin="round"
      aria-hidden="true" focusable="false"
    >
      <circle cx="4" cy="3" r="1.6" />
      <circle cx="12" cy="3" r="1.6" />
      <circle cx="8" cy="13" r="1.6" />
      <path d="M4 4.6v1.4c0 1.7 1.3 3 3 3h2c1.7 0 3-1.3 3-3V4.6M8 9v2.4" />
    </svg>
  </span>
);

export default ForkGlyph;
