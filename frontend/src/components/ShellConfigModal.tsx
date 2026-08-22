import React, { useState, useEffect } from 'react';
import { Modal, Switch, Input, Button, List, Tag, Space, message, Divider, Checkbox, Collapse, Alert, Slider } from 'antd';
import { PlusOutlined, DeleteOutlined, WarningOutlined, CopyOutlined } from '@ant-design/icons';

interface ShellConfigModalProps {
    visible: boolean;
    onClose: () => void;
}

// Escalation-signature status reported by GET /api/mcp/shell-config (ASR F-004).
// hasEscalation: any privilege-bearing field is beyond the built-in floor.
// authorized: that escalation carries a valid root signature (or there is none).
// pendingDelta: the specific beyond-floor entries, by field.
interface SignatureStatus {
    hasEscalation: boolean;
    authorized: boolean;
    pendingDelta: Record<string, string[]>;
}

interface ShellConfig {
    enabled: boolean;
    allowedCommands: string[];
    gitOperationsEnabled: boolean;
    safeGitOperations: string[];
    timeout: number;
    persist?: boolean;
    safeWritePaths: string[];
    allowedWritePatterns: string[];
    allowedInterpreters: string[];
    alwaysBlocked: string[];
    signatureStatus?: SignatureStatus;
    sessionPending?: boolean;
    // Applied temporary grant (verified server-side against the current
    // session nonce). Powers the "Temporary grant active" indicator.
    sessionGrant?: {
        active: boolean;
        provider?: string;
        grantedBy?: string;
        grantedAt?: number;
        delta: Record<string, string[]>;
    } | null;
}

const { Panel } = Collapse;

// A terminal command the user must run, rendered as a distinct copyable
// block. Previously these (`sudo ziya-approve` ...) were inline <code>
// fragments buried in prose — easy to skim past, painful to retranscribe.
// rgba grays render legibly on both light and dark themes.
const CmdBlock: React.FC<{ cmd: string }> = ({ cmd }) => (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, margin: '8px 0' }}>
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
);

const ShellConfigModal: React.FC<ShellConfigModalProps> = ({ visible, onClose }) => {
    const [config, setConfig] = useState<ShellConfig | null>(null);
    const [originalConfig, setOriginalConfig] = useState<ShellConfig | null>(null);
    const [newCommand, setNewCommand] = useState('');
    const [loading, setLoading] = useState(false);
    const [restarting, setRestarting] = useState(false);
    // True once an ephemeral escalation has been staged to the transient
    // pending file (via "Apply (this session)"), so the post-sign "Apply now"
    // activator is surfaced. Independent of the durable config's pendingDelta.
    const [sessionStaged, setSessionStaged] = useState(false);
    const [newWritePath, setNewWritePath] = useState('');
    const [newWritePattern, setNewWritePattern] = useState('');
    const [newInterpreter, setNewInterpreter] = useState('');
    const [newBlockedCmd, setNewBlockedCmd] = useState('');

    useEffect(() => {
        if (visible) {
            fetchShellConfig();
        }
    }, [visible]);

    // Returns the fresh config (or null) so callers — e.g. the post-Save
    // check — can branch on the just-fetched signatureStatus.
    const fetchShellConfig = async (): Promise<ShellConfig | null> => {
        try {
            const response = await fetch('/api/mcp/shell-config');
            if (response.ok) {
                const data = await response.json();
                const normalized: ShellConfig = {
                    ...data,
                    safeWritePaths: data.safeWritePaths ?? ['.ziya/', '/tmp/', '/var/tmp/', '/dev/null'],
                    allowedWritePatterns: data.allowedWritePatterns ?? [],
                    allowedInterpreters: data.allowedInterpreters ?? ['python3', 'python', 'node', 'ruby'],
                    alwaysBlocked: data.alwaysBlocked ?? ['sudo', 'su', 'vim', 'nano', 'emacs', 'systemctl', 'service'],
                };
                setConfig(normalized);
                setOriginalConfig(normalized);
                // Rehydrate the ephemeral "Apply now" affordance: the staging
                // lives on disk (transient pending file), not in component
                // state, so a modal close/reopen must restore it from the
                // server's sessionPending flag rather than losing it.
                setSessionStaged(!!data.sessionPending);
                return normalized;
            }
        } catch (error) {
            console.error('Failed to fetch shell config:', error);
            setConfig({
                enabled: true,
                allowedCommands: [],
                gitOperationsEnabled: true,
                safeGitOperations: [],
                timeout: 30,
                safeWritePaths: ['.ziya/', '/tmp/', '/var/tmp/', '/dev/null'],
                allowedWritePatterns: [],
                allowedInterpreters: ['python3', 'python', 'node', 'ruby'],
                alwaysBlocked: ['sudo', 'su', 'vim', 'nano', 'emacs', 'systemctl', 'service'],
            });
            setOriginalConfig(null);
        }
        return null;
    };

    // Restart the shell server so it re-reads the persisted config from disk,
    // picking up a ZIYA_SCOPE_SIG just written by `sudo ziya-approve`. This is
    // the "I've signed — make it active" action: the running server's in-memory
    // config never sees the out-of-process signature until a restart re-reads
    // the file and respawns the (workspace-scoped) subprocess.
    const restartShellServer = async () => {
        setRestarting(true);
        try {
            const response = await fetch('/api/mcp/shell-config/restart', { method: 'POST' });
            const result = await response.json();
            if (response.ok && result.success) {
                message.success(result.message || 'Shell server restarted');
                // Re-fetch so the signature banner reflects the new state.
                await fetchShellConfig();
                window.dispatchEvent(new CustomEvent('mcpStatusChanged', {
                    detail: { serverName: 'shell', enabled: true }
                }));
            } else {
                message.error(result.message || 'Failed to restart shell server');
            }
        } catch (error) {
            message.error('Failed to restart shell server');
            console.error('Shell restart error:', error);
        } finally {
            setRestarting(false);
        }
    };

    // Apply an EPHEMERAL session grant minted by `sudo ziya-approve --session`.
    // This is the runtime-consent tier: the escalation is authorized for the
    // current server session only (bound to a per-server-start nonce) and is
    // automatically void on the next full server restart — it is never written
    // to the durable config. Durable, cross-restart privilege still uses the
    // `sudo ziya-approve` (no --session) + Save path. The manager only
    // transports the signed grant; the shell subprocess re-verifies it against
    // the trust anchor at init and fails closed if it does not check out.
    const applySessionGrant = async () => {
        setRestarting(true);
        try {
            const response = await fetch('/api/mcp/shell-config/apply-session-grant', { method: 'POST' });
            const result = await response.json();
            if (response.ok && result.success) {
                message.success(result.message || 'Session grant applied for this session');
                await fetchShellConfig();
                window.dispatchEvent(new CustomEvent('mcpStatusChanged', {
                    detail: { serverName: 'shell', enabled: true }
                }));
            } else {
                message.error(result.message || 'No session grant found — run `sudo ziya-approve --session`');
            }
        } catch (error) {
            message.error('Failed to apply session grant');
            console.error('Session grant error:', error);
        } finally {
            setRestarting(false);
        }
    };

    // Interstitial for the session-only path. The footer button's "(this
    // session)" suffix proved too easy to read past — users ran the sudo
    // ceremony without registering that the grant evaporates on restart.
    // Spell out the temporary/persistent fork once, in a modal that cannot
    // be skimmed away, BEFORE anything is staged.
    const confirmSessionStage = () => {
        Modal.confirm({
            title: 'Temporary grant — this session only',
            icon: <WarningOutlined style={{ color: '#faad14' }} />,
            content: (
                <div>
                    <p style={{ marginBottom: 8 }}>
                        This stages your changes as a <b>temporary</b> grant: after
                        signing, it lasts only until the Ziya server restarts, and{' '}
                        <b>nothing is written to the persistent config</b>.
                    </p>
                    <p style={{ marginBottom: 0 }}>
                        Want it to survive restarts? Use <b>Save (persistent)</b>{' '}
                        instead, then sign with <code>sudo ziya-approve</code>.
                    </p>
                </div>
            ),
            okText: 'Stage temporary grant',
            cancelText: 'Cancel',
            onOk: () => requestSessionGrant(),
        });
    };

    // Stage an EPHEMERAL escalation request: write the current fields to the
    // transient pending file (~/.ziya/pending_session_shell.json) WITHOUT
    // touching the durable config. This is the ephemeral sibling of Save — the
    // escalation never lands in the config file. Next steps are out-of-process
    // signing (`sudo ziya-approve --session`) then "Apply now". The grant
    // itself carries the escalation values; the subprocess re-verifies it.
    const requestSessionGrant = async () => {
        if (!config) return;
        setLoading(true);
        try {
            const response = await fetch('/api/mcp/shell-config/request-session-grant', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ ...config }),
            });
            const result = await response.json();
            if (response.ok && result.success) {
                setSessionStaged(true);
                message.success(
                    result.message ||
                    'Staged for this session. Run `sudo ziya-approve --session`, then Apply now.'
                );
            } else {
                message.error(result.message || 'Failed to stage session escalation');
            }
        } catch (error) {
            message.error('Failed to stage session escalation');
            console.error('Session stage error:', error);
        } finally {
            setLoading(false);
        }
    };

    // Abandon a staged-but-not-activated ephemeral escalation: clears the
    // transient pending/grant files so the "Apply now" affordance stops being
    // offered for a request you decided against. Does not revoke an already-
    // applied grant (that is voided on the next server restart).
    const discardSessionGrant = async () => {
        setLoading(true);
        try {
            const response = await fetch('/api/mcp/shell-config/discard-session-grant', { method: 'POST' });
            const result = await response.json();
            if (response.ok && result.success) {
                setSessionStaged(false);
                message.success(result.message || 'Staged session escalation discarded');
            } else {
                message.error(result.message || 'Failed to discard staged escalation');
            }
        } catch (error) {
            message.error('Failed to discard staged escalation');
            console.error('Session discard error:', error);
        } finally {
            setLoading(false);
        }
    };

    const syncMCPServerToggle = async (enabled: boolean) => {
        try {
            await fetch('/api/mcp/toggle-server', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    server_name: 'shell',
                    enabled: enabled
                }),
            });
        } catch (error) {
            console.warn('Failed to sync MCP server toggle:', error);
        }
    };

    // There is a single write path: persist to ~/.ziya/mcp_config.json and
    // restart the shell server. Escalations beyond the default floor are
    // written but clamped to the floor in every session until signed with
    // `sudo ziya-approve`. There is no unsigned/ephemeral apply.
    const saveConfig = async (persist: boolean = true) => {
        if (!config) return;

        setLoading(true);
        try {
            const response = await fetch('/api/mcp/shell-config', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({ ...config, persist }),
            });

            if (response.ok) {
                const result = await response.json();
                if (result.success) {
                    await syncMCPServerToggle(config.enabled);

                    window.dispatchEvent(new CustomEvent('mcpStatusChanged', {
                        detail: {
                            serverName: 'shell',
                            enabled: config.enabled
                        }
                    }));

                    // Re-fetch so signatureStatus reflects the file just
                    // written. If this save introduced (or kept) an unsigned
                    // escalation, keep the modal OPEN: the signing walkthrough
                    // lives in this modal's warning banner, and closing here
                    // was exactly how "I saved but nothing invited me to sign"
                    // happened — the invitation only reappeared on reopen.
                    const fresh = await fetchShellConfig();
                    const needsSignature = !!(
                        fresh?.signatureStatus?.hasEscalation &&
                        !fresh.signatureStatus.authorized
                    );
                    if (needsSignature) {
                        message.warning(
                            'Saved — but the new privileges are NOT active ' +
                            'until signed. Follow the steps in the warning below.',
                            6
                        );
                    } else {
                        message.success(result.message || 'Saved and restarted.');
                        onClose();
                    }
                } else {
                    message.error(result.message || 'Failed to update shell configuration');
                }
            } else {
                message.error('Failed to save shell configuration');
            }
        } catch (error) {
            message.error('Failed to save shell configuration');
            console.error('Save error:', error);
        } finally {
            setLoading(false);
        }
    };

    const addCommand = () => {
        if (!config) return;

        if (newCommand.trim() && !config.allowedCommands.includes(newCommand.trim())) {
            setConfig(prev => ({
                ...prev!,
                allowedCommands: [...prev!.allowedCommands, newCommand.trim()]
            }));
            setNewCommand('');
        }
    };

    const removeCommand = (command: string) => {
        if (!config) return;

        setConfig(prev => ({
            ...prev!,
            allowedCommands: prev!.allowedCommands.filter(cmd => cmd !== command)
        }));
    };

    const toggleGitOperation = (operation: string) => {
        if (!config) return;

        setConfig(prev => ({
            ...prev!,
            safeGitOperations: prev!.safeGitOperations.includes(operation)
                ? prev!.safeGitOperations.filter(op => op !== operation)
                : [...prev!.safeGitOperations, operation]
        }));
    };

    const hasChanges = (): boolean => {
        if (!config || !originalConfig) return false;
        return JSON.stringify(config) !== JSON.stringify(originalConfig);
    };

    const handleClose = () => {
        if (hasChanges()) {
            Modal.confirm({
                title: 'Unsaved Changes',
                icon: <WarningOutlined style={{ color: '#faad14' }} />,
                content: 'Save (persistent) writes your edits to ~/.ziya/mcp_config.json and restarts the shell server — new privileges still need `sudo ziya-approve` afterwards (the modal will walk you through it). Discard closes without changing anything.',
                okText: 'Save Changes',
                cancelText: 'Discard Changes',
                okButtonProps: {
                    loading: loading
                },
                onOk: async () => {
                    await saveConfig(true);
                },
                onCancel: () => {
                    onClose();
                },
                maskClosable: true,
                closable: true
            });
        } else {
            onClose();
        }
    };

    const dangerousCommands = ['rm', 'rmdir', 'mv', 'cp', 'chmod', 'chown', 'sudo', 'su'];
    const allGitOperations = ['status', 'log', 'show', 'diff', 'branch', 'remote', 'config --get', 'ls-files', 'ls-tree', 'blame', 'tag', 'stash list', 'reflog', 'rev-parse', 'describe', 'shortlog', 'whatchanged'];

    const isDangerous = (command: string) =>
        dangerousCommands.some(dangerous =>
            command.toLowerCase().includes(dangerous.toLowerCase())
        );

    if (!config) {
        return (
            <Modal title="Shell Command Configuration" open={visible} onCancel={onClose} footer={null}>
                <div style={{ textAlign: 'center', padding: '40px' }}>Loading configuration...</div>
            </Modal>
        );
    }

    return (
        <Modal
            title="Shell Command Configuration"
            open={visible}
            onCancel={handleClose}
            width={600}
            footer={[
                <Button key="cancel" onClick={handleClose}>
                    Cancel
                </Button>,
                <Button
                    key="restart"
                    loading={restarting}
                    onClick={restartShellServer}
                    title="Restart the shell server so it re-reads the signed config from disk"
                >
                    Restart shell server
                </Button>,
                <Button
                    key="apply-session"
                    loading={loading}
                    onClick={confirmSessionStage}
                    title="Temporary: stage this escalation for the current server session only. Signed with `sudo ziya-approve --session`; voided on the next server restart; never written to the persistent config."
                >
                    This session only…
                </Button>,
                <Button
                    key="save"
                    type="primary"
                    loading={loading}
                    onClick={() => saveConfig(true)}
                    title="Persistent: write to ~/.ziya/mcp_config.json and restart. New privileges beyond the default floor still require `sudo ziya-approve` before taking effect. Discards any staged session-only request."
                >
                    Save (persistent)
                </Button>
            ]}
        >
            <Space direction="vertical" style={{ width: '100%' }} size="large">
                <div>
                    <Space align="center">
                        <Switch
                            checked={config.enabled}
                            onChange={async (checked) => {
                                setConfig(prev => ({ ...prev!, enabled: checked }));
                                await syncMCPServerToggle(checked);
                            }}
                        />
                        <span>Enable shell command execution</span>
                    </Space>
                    <div style={{ marginTop: 8, color: '#666', fontSize: '12px' }}>
                        Enables execution of whitelisted commands for file operations, system inspection, and diagnostics
                    </div>
                </div>

                <Divider />

                <Alert
                    message="Security Notice"
                    description="Only enable commands that are safe for AI execution. Git operations are limited to read-only and safe operations by default."
                    type="info"
                    showIcon
                    style={{ marginBottom: 16 }}
                />

                {config.signatureStatus?.hasEscalation && !config.signatureStatus?.authorized && (
                    <Alert
                        type="warning"
                        showIcon
                        message="Unsigned privilege escalation — not active"
                        style={{ marginBottom: 16 }}
                        description={
                            <div>
                                <div style={{ marginBottom: 6 }}>
                                    This configuration requests commands/paths beyond the
                                    default safe set, but they are <b>not signed</b>, so the
                                    shell server is ignoring them and running at the default
                                    floor. Editing a privileged field voids any prior
                                    approval until you re-sign.
                                </div>
                                {Object.entries(config.signatureStatus.pendingDelta).map(([field, vals]) => (
                                    <div key={field} style={{ fontFamily: 'monospace', fontSize: 12 }}>
                                        {field}: {vals.join(', ')}
                                    </div>
                                ))}
                                <div style={{ marginTop: 10 }}>
                                    <b>To activate</b> (persists across restarts): run this
                                    in a terminal…
                                </div>
                                <CmdBlock cmd="sudo ziya-approve" />
                                <Button
                                    size="small"
                                    type="primary"
                                    loading={restarting}
                                    style={{ marginTop: 6 }}
                                    onClick={restartShellServer}
                                >
                                    I've signed — Restart shell server &amp; re-check
                                </Button>
                            </div>
                        }
                    />
                )}

                {sessionStaged && (
                    <Alert
                        type="warning"
                        showIcon
                        style={{ marginBottom: 16 }}
                        message="Temporary grant staged — this session only"
                        description={
                            <div>
                                Your requested escalation is staged but <b>not yet
                                active</b>. It is <b>temporary</b>: nothing is written to
                                the persistent config, and once active it is voided the
                                next time the Ziya server restarts. To activate it, run
                                this in a terminal…
                                <CmdBlock cmd="sudo ziya-approve --session" />
                                <Button
                                    size="small"
                                    type="primary"
                                    loading={restarting}
                                    style={{ marginTop: 10 }}
                                    onClick={applySessionGrant}
                                >
                                    I've signed for this session — Apply now
                                </Button>
                                <Button
                                    size="small"
                                    loading={loading}
                                    style={{ marginTop: 10, marginLeft: 8 }}
                                    onClick={discardSessionGrant}
                                >
                                    Discard
                                </Button>
                                <div style={{ marginTop: 8, fontSize: 12, opacity: 0.75 }}>
                                    For permanent access use <b>Save (persistent)</b>{' '}
                                    instead — note that Save discards this staged request
                                    and starts the persistent path, which requires its own{' '}
                                    <code>sudo ziya-approve</code> signature.
                                </div>
                            </div>
                        }
                    />
                )}

                {config.sessionGrant?.active && (
                    <Alert
                        type="success"
                        showIcon
                        style={{ marginBottom: 16 }}
                        message="Temporary grant active — this session only"
                        description={
                            <div>
                                <div style={{ marginBottom: 6 }}>
                                    These privileges are live via a temporary grant
                                    {config.sessionGrant.provider
                                        ? ` (signed via ${config.sessionGrant.provider})`
                                        : ''}
                                    . They are <b>not</b> in the persistent config and
                                    are <b>voided automatically when the Ziya server
                                    restarts</b>.
                                </div>
                                {Object.entries(config.sessionGrant.delta).map(([field, vals]) => (
                                    <div key={field} style={{ fontFamily: 'monospace', fontSize: 12 }}>
                                        {field}: {vals.join(', ')}
                                    </div>
                                ))}
                                <div style={{ marginTop: 8, fontSize: 12, opacity: 0.75 }}>
                                    To keep these across restarts, use{' '}
                                    <b>Save (persistent)</b> +{' '}
                                    <code>sudo ziya-approve</code>.
                                </div>
                            </div>
                        }
                    />
                )}

                <Collapse defaultActiveKey={['1']} ghost>
                    <Panel header="Basic Configuration" key="1">
                        <div>
                            <h4>Allowed Commands</h4>
                            <div style={{ marginBottom: 12, color: '#666', fontSize: '12px' }}>
                                Commands that the AI agent can execute. These are loaded from the server configuration.
                            </div>

                            <div style={{ marginBottom: 12 }}>
                                <Input.Group compact>
                                    <Input
                                        placeholder="Add additional command"
                                        value={newCommand}
                                        onChange={(e) => setNewCommand(e.target.value)}
                                        onPressEnter={addCommand}
                                        style={{ width: 'calc(100% - 80px)' }}
                                    />
                                    <Button
                                        type="primary"
                                        icon={<PlusOutlined />}
                                        onClick={addCommand}
                                        style={{ width: 80 }}
                                    >
                                        Add
                                    </Button>
                                </Input.Group>
                            </div>

                            {config.allowedCommands && config.allowedCommands.length > 0 && (
                                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px', marginBottom: 16 }}>
                                    {[...config.allowedCommands].sort().map((command) => (
                                        <Tag
                                            key={command}
                                            closable
                                            color={isDangerous(command) ? 'red' : 'blue'}
                                            onClose={() => removeCommand(command)}
                                            style={{ marginBottom: '4px' }}
                                        >
                                            {command}
                                            {isDangerous(command) && (
                                                <WarningOutlined style={{ marginLeft: 4, color: '#ff4d4f' }} />
                                            )}
                                        </Tag>
                                    ))}
                                </div>
                            )}
                        </div>

                        <Divider />

                        <div>
                            <h4>Command Timeout: {config.timeout}s</h4>
                            <Slider
                                min={10}
                                max={600}
                                value={config.timeout}
                                onChange={(value) => setConfig(prev => ({ ...prev!, timeout: value }))}
                                marks={{
                                    10: '10s',
                                    60: '1m',
                                    120: '2m',
                                    300: '5m',
                                    600: '10m'
                                }}
                                style={{ width: '100%', marginBottom: 20 }}
                            />
                        </div>

                        <Divider />

                        <div>
                            <Space align="center" style={{ marginBottom: 12 }}>
                                <Switch
                                    checked={config.gitOperationsEnabled}
                                    onChange={(checked) => setConfig(prev => ({ ...prev!, gitOperationsEnabled: checked }))}
                                />
                                <span>Enable safe Git operations</span>
                            </Space>
                            <div style={{ marginBottom: 12, color: '#666', fontSize: '12px' }}>
                                When enabled, allows read-only and safe Git commands
                            </div>

                            {config.gitOperationsEnabled && (
                                <div style={{ marginLeft: 24 }}>
                                    <h5>Allowed Git Operations:</h5>
                                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
                                        {allGitOperations.map(operation => (
                                            <Checkbox
                                                key={operation}
                                                checked={config.safeGitOperations.includes(operation)}
                                                onChange={() => toggleGitOperation(operation)}
                                            >
                                                git {operation}
                                            </Checkbox>
                                        ))}
                                    </div>
                                </div>
                            )}
                        </div>
                    </Panel>

                    <Panel header="Advanced Configuration" key="2">
                        <Space direction="vertical" style={{ width: '100%' }} size="middle">
                            <Alert message="Global Write Policy" description="Controls where shell commands can write. Per-project write patterns are configured in Manage Projects (from the project switcher dropdown)." type="info" showIcon style={{ marginBottom: 8 }} />

                            <div>
                                <h5>Safe Write Paths</h5>
                                <div style={{ marginBottom: 8, color: '#666', fontSize: '12px' }}>Path prefixes always writable (e.g., .ziya/, /tmp/)</div>
                                <Input.Group compact style={{ marginBottom: 8 }}>
                                    <Input placeholder=".ziya/ or /tmp/" value={newWritePath} onChange={(e) => setNewWritePath(e.target.value)} onPressEnter={() => { if (newWritePath.trim() && config && !config.safeWritePaths.includes(newWritePath.trim())) { setConfig(prev => ({ ...prev!, safeWritePaths: [...prev!.safeWritePaths, newWritePath.trim()] })); setNewWritePath(''); } }} style={{ width: 'calc(100% - 80px)' }} />
                                    <Button type="primary" icon={<PlusOutlined />} onClick={() => { if (newWritePath.trim() && config && !config.safeWritePaths.includes(newWritePath.trim())) { setConfig(prev => ({ ...prev!, safeWritePaths: [...prev!.safeWritePaths, newWritePath.trim()] })); setNewWritePath(''); } }} style={{ width: 80 }}>Add</Button>
                                </Input.Group>
                                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px' }}>
                                    {config.safeWritePaths.map(p => (<Tag key={p} closable color="green" onClose={() => setConfig(prev => ({ ...prev!, safeWritePaths: prev!.safeWritePaths.filter(x => x !== p) }))}>{p}</Tag>))}
                                </div>
                            </div>

                            <Divider style={{ margin: '8px 0' }} />

                            <div>
                                <h5>Allowed Interpreters</h5>
                                <div style={{ marginBottom: 8, color: '#666', fontSize: '12px' }}>Script interpreters allowed for computation. File writes are heuristic-blocked.</div>
                                <Input.Group compact style={{ marginBottom: 8 }}>
                                    <Input placeholder="python3" value={newInterpreter} onChange={(e) => setNewInterpreter(e.target.value)} onPressEnter={() => { if (newInterpreter.trim() && config && !config.allowedInterpreters.includes(newInterpreter.trim())) { setConfig(prev => ({ ...prev!, allowedInterpreters: [...prev!.allowedInterpreters, newInterpreter.trim()] })); setNewInterpreter(''); } }} style={{ width: 'calc(100% - 80px)' }} />
                                    <Button type="primary" icon={<PlusOutlined />} onClick={() => { if (newInterpreter.trim() && config && !config.allowedInterpreters.includes(newInterpreter.trim())) { setConfig(prev => ({ ...prev!, allowedInterpreters: [...prev!.allowedInterpreters, newInterpreter.trim()] })); setNewInterpreter(''); } }} style={{ width: 80 }}>Add</Button>
                                </Input.Group>
                                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px' }}>
                                    {config.allowedInterpreters.map(p => (<Tag key={p} closable color="cyan" onClose={() => setConfig(prev => ({ ...prev!, allowedInterpreters: prev!.allowedInterpreters.filter(x => x !== p) }))}>{p}</Tag>))}
                                </div>
                            </div>

                            <Divider style={{ margin: '8px 0' }} />

                            <div>
                                <h5>Always Blocked</h5>
                                <div style={{ marginBottom: 8, color: '#666', fontSize: '12px' }}>Commands never allowed regardless of arguments.</div>
                                <Input.Group compact style={{ marginBottom: 8 }}>
                                    <Input placeholder="sudo" value={newBlockedCmd} onChange={(e) => setNewBlockedCmd(e.target.value)} onPressEnter={() => { if (newBlockedCmd.trim() && config && !config.alwaysBlocked.includes(newBlockedCmd.trim())) { setConfig(prev => ({ ...prev!, alwaysBlocked: [...prev!.alwaysBlocked, newBlockedCmd.trim()] })); setNewBlockedCmd(''); } }} style={{ width: 'calc(100% - 80px)' }} />
                                    <Button type="primary" icon={<PlusOutlined />} onClick={() => { if (newBlockedCmd.trim() && config && !config.alwaysBlocked.includes(newBlockedCmd.trim())) { setConfig(prev => ({ ...prev!, alwaysBlocked: [...prev!.alwaysBlocked, newBlockedCmd.trim()] })); setNewBlockedCmd(''); } }} style={{ width: 80 }}>Add</Button>
                                </Input.Group>
                                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px' }}>
                                    {config.alwaysBlocked.map(p => (<Tag key={p} closable color="red" onClose={() => setConfig(prev => ({ ...prev!, alwaysBlocked: prev!.alwaysBlocked.filter(x => x !== p) }))}>{p}</Tag>))}
                                </div>
                            </div>
                        </Space>
                    </Panel>
                </Collapse>
            </Space>
        </Modal>
    );
};

export default ShellConfigModal;
