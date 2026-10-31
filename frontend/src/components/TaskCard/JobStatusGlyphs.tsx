/**
 * JobStatusGlyphs — per-state indicators for chat-started managed jobs on a
 * conversation row, beside RunStatusGears.
 *
 * One terminal icon per kind, coloured by state; only a running job pulses,
 * since motion is how a reader decides to keep waiting rather than
 * intervene.  The outcome badge (✓ ✗ !) sits in the corner and is dropped
 * in compact rows, where colour and pulse carry it — four cells beside a
 * gear was too much for a narrow row.  Policy lives in jobStatusGlyph.ts.
 */

import React from 'react';
import Box from '@mui/material/Box';
import Tooltip from '@mui/material/Tooltip';
import Typography from '@mui/material/Typography';
import TerminalIcon from '@mui/icons-material/Terminal';
import { styled } from '@mui/material/styles';
import type { JobSummary } from '../../services/taskRunApi';
import { jobClusters } from './jobStatusGlyph';

// A soft pulse rather than the gears' rotation: a different motion for a
// different kind of work, so the two are distinguishable at a glance.
const Pulse = styled('span')({
    position: 'absolute', inset: -2, borderRadius: '50%',
    animation: 'job-pulse 2.4s ease-in-out infinite',
    '@keyframes job-pulse': {
        '0%, 100%': { boxShadow: '0 0 0 0 currentColor', opacity: 0.35 },
        '50%': { boxShadow: '0 0 0 3px currentColor', opacity: 0 },
    },
});

interface Props {
    jobs?: ReadonlyArray<JobSummary> | null;
    /** Epoch seconds the user last opened this conversation. */
    seenAt?: number;
    /** True for the open conversation: finished jobs are the rail's job there. */
    hideFinished?: boolean;
    compact?: boolean;
}

export const JobStatusGlyphs: React.FC<Props> = ({ jobs, seenAt, hideFinished = false, compact = false }) => {
    const clusters = jobClusters(jobs, { seenAt, hideFinished });
    if (clusters.length === 0) return null;
    return (
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75, flexShrink: 0, ml: 0.5 }}>
            {clusters.map(c => (
                <Tooltip key={c.kind} title={c.hint} placement="top">
                    <Box
                        data-testid="job-glyph"
                        data-kind={c.kind}
                        role={c.kind === 'running' ? undefined : 'status'}
                        aria-label={c.label}
                        sx={{ display: 'inline-flex', alignItems: 'center', color: c.color, position: 'relative' }}
                    >
                        <Box sx={{ position: 'relative', display: 'inline-flex' }}>
                            {c.animate && <Pulse data-testid="job-glyph-pulse" />}
                            <TerminalIcon sx={{ fontSize: '13px' }} />
                            {c.badge && !compact && (
                                <Typography
                                    component="span"
                                    aria-hidden="true"
                                    sx={{
                                        position: 'absolute', right: -5, top: -6,
                                        fontSize: '8px', fontWeight: 700, lineHeight: 1,
                                    }}
                                >
                                    {c.badge}
                                </Typography>
                            )}
                        </Box>
                        {c.count > 1 && (
                            <Typography
                                variant="caption"
                                sx={{ fontSize: '10px', ml: 0.25, fontWeight: 600 }}
                            >
                                {c.count}
                            </Typography>
                        )}
                    </Box>
                </Tooltip>
            ))}
        </Box>
    );
};

export default JobStatusGlyphs;
