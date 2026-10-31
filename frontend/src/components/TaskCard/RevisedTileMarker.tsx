/**
 * One line left where a task card's tile used to sit before the model
 * revised the card and the tile moved to the revising turn.  Placed by
 * utils/taskBindingRevisions.ts; jumps to the tile with the rail's scroll.
 */
import React from 'react';
import { Button } from 'antd';
import { scrollToTile } from './taskSurfacing';

export const RevisedTileMarker: React.FC<{ bindingId: string }> = ({ bindingId }) => (
    <div
        className="tc-revised-marker"
        data-testid="tc-revised-marker"
        style={{
            fontSize: 12, opacity: 0.8, margin: '6px 0', padding: '2px 8px',
            borderLeft: '2px dashed var(--mockup-border, rgba(127,127,127,0.5))',
        }}
    >
        🎯 The task card staged here was revised later in this conversation.
        <Button type="link" size="small" onClick={() => scrollToTile(bindingId)}
                style={{ padding: '0 4px', height: 'auto' }}>
            Jump to the current version ↓
        </Button>
    </div>
);

export default RevisedTileMarker;
