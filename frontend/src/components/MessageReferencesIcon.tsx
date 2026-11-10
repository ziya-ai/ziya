/**
 * MessageReferencesIcon — the 📎 on an assistant message's action row
 * (design/conversation-references.md §Surface 4).  Nothing is fetched until
 * the tooltip opens; the row count is the message count, so a subscription
 * per row was never an option.  ``locate`` is called at hover time so the
 * owning project and the message's turn are read from live refs, not
 * captured at render (MessageActions keeps no project-context subscription).
 */
import React, { useState } from 'react';
import { Button, Tooltip } from 'antd';
import { loadReferences } from '../hooks/useConversationReferences';
import { referencesForTurn, TurnReferenceUses } from '../utils/references';
import { referenceLabel } from './ReferencesLine';

export interface TurnLocator { projectId: string | undefined; conversationId: string; turn: number; }

export function describeTurnReferences(t: TurnReferenceUses): string {
    const parts: string[] = [];
    if (t.read.length) parts.push(`read ${t.read.map(referenceLabel).join(', ')}`);
    if (t.cited.length) parts.push(`cited ${t.cited.map(referenceLabel).join(', ')}`);
    if (t.added.length) parts.push(`new: ${t.added.map(referenceLabel).join(', ')}`);
    return parts.length ? parts.join(' · ') : 'No references touched in this turn';
}

interface Props { locate: () => TurnLocator | null; }

export const MessageReferencesIcon: React.FC<Props> = ({ locate }) => {
    const [text, setText] = useState('…');

    const onOpenChange = (open: boolean) => {
        if (!open) return;
        const loc = locate();
        if (!loc || !loc.projectId) { setText('No references'); return; }
        setText('…');
        loadReferences(loc.projectId, loc.conversationId)
            .then(v => setText(describeTurnReferences(referencesForTurn(v.references, loc.turn))))
            .catch(() => setText('Could not load references'));
    };

    return (
        <Tooltip title={text} onOpenChange={onOpenChange} mouseEnterDelay={0.2}>
            <Button type="default" size="small" data-testid="message-references"
                aria-label="References this turn touched"
                style={{ padding: '0 8px', minWidth: '32px', height: '32px' }}>📎</Button>
        </Tooltip>
    );
};

export default MessageReferencesIcon;
