import React from 'react';
import { Button, message } from 'antd';
import { CopyOutlined } from '@ant-design/icons';

/**
 * A terminal command the user must run, rendered as a distinct copyable
 * block rather than an inline <code> fragment buried in prose.
 *
 * Extracted so the shell-config modal and the task-card proposal panel
 * share one implementation: both surfaces hand the user a `sudo
 * ziya-approve …` line, and both had the same two defects when the
 * command was inline prose — easy to skim past, and painful to
 * retranscribe by hand (the task-card form carries two uuids).
 *
 * The rgba grays are deliberate: they render legibly against both light
 * and dark app themes without either surface having to know which is
 * active.
 */
export const CommandBlock: React.FC<{
    cmd: string;
    /** Optional caption rendered above the command, e.g. which block it signs. */
    label?: string;
}> = ({ cmd, label }) => (
    <div style={{ margin: '8px 0' }}>
        {label && (
            <div style={{ fontSize: 11, opacity: 0.7, marginBottom: 3 }}>{label}</div>
        )}
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <code style={{
                flex: 1, display: 'block', padding: '6px 10px', fontSize: 13,
                fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
                borderRadius: 6, background: 'rgba(128, 128, 128, 0.15)',
                border: '1px solid rgba(128, 128, 128, 0.35)',
                userSelect: 'all', whiteSpace: 'nowrap', overflowX: 'auto',
            }}>
                {cmd}
            </code>
            <Button
                size="small"
                icon={<CopyOutlined />}
                title="Copy command"
                onClick={() => navigator.clipboard?.writeText(cmd).then(
                    () => message.success('Copied'),
                    () => message.error('Copy failed')
                )}
            />
        </div>
    </div>
);

export default CommandBlock;
