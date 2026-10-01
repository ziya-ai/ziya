/** "−1,872t" / "+400t": the same transient marker wherever a token count moved,
 *  so the eye connects the drawer switch to the card, the gauge and the total. */
import React from 'react';

export const DeltaChip: React.FC<{ delta?: number; testId?: string }> = ({ delta, testId }) => {
    if (!delta) return null;
    const down = delta < 0;
    return (
        <span data-testid={testId} title="fades in a moment"
              style={{ fontSize: 10, lineHeight: 1, padding: '2px 6px', borderRadius: 8, fontWeight: 600,
                       color: down ? '#0d9488' : '#d46b08',
                       background: down ? 'rgba(13,148,136,0.18)' : 'rgba(212,107,8,0.18)' }}>
            {down ? '−' : '+'}{Math.abs(delta).toLocaleString()}t
        </span>
    );
};
