/**
 * AddReferenceModal — the paperclip menu's "Add reference" (design/
 * conversation-references.md §Surface 3).  A locator and an optional title;
 * the server infers the kind and refuses what it cannot track.  Distinct
 * from "Attach": nothing is inlined into the message.
 */
import React, { useState } from 'react';
import { Input, Modal, message } from 'antd';
import { addReference } from '../hooks/useConversationReferences';

interface Props {
    open: boolean;
    onClose: () => void;
    projectId: string | null | undefined;
    conversationId: string | null | undefined;
}

export const AddReferenceModal: React.FC<Props> = ({ open, onClose, projectId, conversationId }) => {
    const [locator, setLocator] = useState('');
    const [title, setTitle] = useState('');
    const [busy, setBusy] = useState(false);

    const submit = async () => {
        const loc = locator.trim();
        if (!loc || !projectId || !conversationId || busy) return;
        setBusy(true);
        try {
            await addReference(projectId, conversationId, loc, title.trim() || undefined);
            message.success('Reference added');
            setLocator('');
            setTitle('');
            onClose();
        } catch (e: any) {
            message.error(/\b400\b/.test(String(e?.message))
                ? 'Not a trackable reference (URL, file path, PDF or ziya://chat link)'
                : 'Could not add reference');
        } finally {
            setBusy(false);
        }
    };

    return (
        <Modal open={open} title="Add reference" onCancel={onClose} onOk={submit} okText="Add"
            okButtonProps={{ disabled: !locator.trim(), loading: busy }} destroyOnClose>
            <p style={{ marginTop: 0, fontSize: 12, opacity: 0.75 }}>
                Stays with this conversation, link-only: the model is told it exists and reads it when it
                needs to. To put a file's contents into this message, use Attach instead.
            </p>
            <Input data-testid="add-reference-locator" autoFocus
                placeholder="URL, file path, PDF, or ziya://chat/… link"
                value={locator} onChange={e => setLocator(e.target.value)} onPressEnter={submit} />
            <Input data-testid="add-reference-title" style={{ marginTop: 8 }}
                placeholder="Title (optional)"
                value={title} onChange={e => setTitle(e.target.value)} onPressEnter={submit} />
        </Modal>
    );
};

export default AddReferenceModal;
