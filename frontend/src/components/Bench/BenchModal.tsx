/**
 * Modal shell for the Bench (slice 6a).  The ONLY component that calls
 * useBench(): it hands the model to BenchPanel, which renders it.  Mounted
 * only while open, so LensProvider's initial lens is chosen per opening:
 * "this chat" when a conversation is open, else "project".
 */
import React, { useEffect, useState } from 'react';
import { Modal } from 'antd';
import { LensProvider } from '../../context/LensContext';
import { useBench } from '../../hooks/useBench';
import type { Attention } from '../../utils/headerAttention';
import { BenchActions, BenchActionsProvider } from './BenchActions';
import { BenchPalette } from './BenchPalette';
import { BenchPanel } from './BenchPanel';

interface Props {
    visible: boolean;
    onClose: () => void;
    projectId: string | null | undefined;
    conversationId?: string | null;
    /** From useHeaderAttention in App; drawn on the environment row's shell badge. */
    shellAttention?: Attention;
    onOpenShellConfig?: () => void;
    /** MCP Servers / Registry panels, reached from inside the Bench. */
    actions?: BenchActions;
}

type BodyProps = Omit<Props, 'visible' | 'onClose' | 'actions'>;

const Body: React.FC<BodyProps> = ({ projectId, conversationId, shellAttention, onOpenShellConfig }) => {
    const bench = useBench({ projectId, conversationId });
    const { ready, lenses, lens, setLens } = bench;
    const [paletteOpen, setPaletteOpen] = useState(false);
    // The server offers no conversation lens when the chat has no record.
    useEffect(() => { if (ready && !lenses.includes(lens)) setLens('project'); }, [ready, lenses, lens, setLens]);
    // ⌘K / Ctrl-K while the Bench is open toggles the palette (slice 7).
    useEffect(() => {
        const onKey = (e: KeyboardEvent) => {
            if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); setPaletteOpen(o => !o); }
        };
        window.addEventListener('keydown', onKey);
        return () => window.removeEventListener('keydown', onKey);
    }, []);
    if (!projectId) return <div style={{ opacity: 0.6 }}>Open a project to arrange its bench.</div>;
    return (
        <div style={{ position: 'relative', display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0 }}>
            <BenchPanel bench={bench} shellAttention={shellAttention} onOpenShellConfig={onOpenShellConfig}
                        onOpenPalette={() => setPaletteOpen(true)} />
            {ready && <BenchPalette bench={bench} open={paletteOpen} onClose={() => setPaletteOpen(false)} />}
        </div>
    );
};

const Title: React.FC = () => (
    <span>
        <span style={{ fontFamily: 'Georgia, "Times New Roman", serif', fontWeight: 800,
                       background: 'linear-gradient(90deg,#b32ca8,#0d9488)', WebkitBackgroundClip: 'text',
                       backgroundClip: 'text', color: 'transparent', marginRight: 6 }}>ℤ</span>
        <span style={{ letterSpacing: '.1em' }}>BENCH</span>
        <span style={{ fontWeight: 400, opacity: 0.6, fontSize: 12, marginLeft: 10 }}>
            skills and MCP servers this conversation carries
        </span>
    </span>
);

const BenchModal: React.FC<Props> = ({ visible, onClose, projectId, conversationId, shellAttention, onOpenShellConfig, actions = {} }) => (
    <Modal open={visible} onCancel={onClose} footer={null} width="min(1400px, 94vw)" title={<Title />} destroyOnClose
           style={{ top: 24 }}
           styles={{ body: { height: 'calc(100vh - 140px)', display: 'flex', flexDirection: 'column', minHeight: 0 } }}>
        <LensProvider initial={conversationId ? 'conversation' : 'project'}>
            <BenchActionsProvider actions={actions}>
                <Body projectId={projectId} conversationId={conversationId ?? null}
                      shellAttention={shellAttention} onOpenShellConfig={onOpenShellConfig} />
            </BenchActionsProvider>
        </LensProvider>
    </Modal>
);

export default BenchModal;
