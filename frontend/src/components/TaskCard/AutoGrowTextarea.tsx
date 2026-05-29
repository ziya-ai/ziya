/**
 * Textarea that auto-resizes to fit its content.
 *
 * Used for the Task instructions and the Until condition fields so the
 * editor card grows naturally as the user types longer prompts, rather
 * than forcing them into a tiny scroll box.
 */

import React, { useLayoutEffect, useRef } from 'react';

type Props = Omit<React.TextareaHTMLAttributes<HTMLTextAreaElement>, 'ref'> & {
  /** Minimum visible rows (sets a min-height floor). */
  minRows?: number;
};

export const AutoGrowTextarea: React.FC<Props> = ({
  minRows = 2,
  value,
  style,
  ...rest
}) => {
  const ref = useRef<HTMLTextAreaElement | null>(null);

  const resize = () => {
    const el = ref.current;
    if (!el) return;
    // Reset first so shrinking works when text is deleted.
    el.style.height = 'auto';
    el.style.height = `${el.scrollHeight}px`;
  };

  useLayoutEffect(() => {
    resize();
  }, [value]);

  // Also resize on mount so an initial long value isn't clipped.
  useLayoutEffect(() => {
    resize();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <textarea
      {...rest}
      ref={ref}
      value={value}
      rows={minRows}
      onInput={(e) => {
        resize();
        rest.onInput?.(e);
      }}
      style={{
        // Disable manual resize; the textarea controls its own height.
        resize: 'none',
        overflow: 'hidden',
        ...style,
      }}
    />
  );
};
