/**
 * MemoryExportPanel — the Export tab of the Memory Browser.
 *
 * Lets the user carve a bounded slice of memory for another user: pick
 * domains (mind-map subtrees), include/exclude projects, toggle what is
 * stripped, and watch a live preview of exactly what will leave the
 * machine (counts, what was held back and why, leak warnings).  The
 * Export button is gated by exportReadiness() and the server re-checks
 * the previewed fingerprint, so the file can never differ from the
 * preview the user looked at.
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Alert, Button, Checkbox, Empty, Input, Modal, Segmented, Select, Switch, Tag, Tooltip, Tree, message } from 'antd';
import { DownloadOutlined, ReloadOutlined, SaveOutlined, WarningOutlined } from '@ant-design/icons';
import * as api from '../api/memoryApi';
import { LAYER_COLORS, LAYER_ICONS, LAYER_LABELS } from '../api/memoryApi';
import type { ExportDomainOption, ExportOptions, ExportPreview, ExportSelection } from '../api/memoryApi';

interface Props {
  isDarkMode: boolean;
}

type ProjectMode = 'default' | 'include' | 'exclude';

const HELD_BACK_LABELS: Record<string, string> = {
  status: 'not in selected statuses',
  layer: 'not in selected layers',
  outside_domains: 'outside selected domains',
  excluded_project: 'in an excluded project',
  project_not_included: 'in a project not included',
  unscoped: 'unscoped (excluded by option)',
};

const WARNING_LABELS: Record<string, string> = {
  local_path: 'Local path from this machine',
  email: 'Email address',
  provenance_included: 'Provenance will be included',
  project_paths_included: 'Absolute project paths will be included',
};

function warningLabel(kind: string): string {
  if (kind.startsWith('credential:')) return `Credential (${kind.slice('credential:'.length)})`;
  return WARNING_LABELS[kind] || kind;
}

function toTreeData(domains: ExportDomainOption[]): any[] {
  return domains.map(d => ({
    key: d.id,
    title: (
      <span>
        {d.handle || d.id}
        <span style={{ opacity: 0.55, marginLeft: 6, fontSize: 11 }}>{d.count}</span>
      </span>
    ),
    children: d.children?.length ? toTreeData(d.children) : undefined,
  }));
}

function triggerDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

const MemoryExportPanel: React.FC<Props> = ({ isDarkMode }) => {
  const [options, setOptions] = useState<ExportOptions | null>(null);
  const [optionsError, setOptionsError] = useState<string | null>(null);
  const [selection, setSelection] = useState<ExportSelection>(api.DEFAULT_EXPORT_SELECTION);
  const [preview, setPreview] = useState<ExportPreview | null>(null);
  const [previewStale, setPreviewStale] = useState(false);
  const [previewing, setPreviewing] = useState(false);
  const [acknowledged, setAcknowledged] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [saveOpen, setSaveOpen] = useState(false);
  const [savePath, setSavePath] = useState('');
  const debounceRef = useRef<ReturnType<typeof setTimeout>>();

  const card = isDarkMode ? '#1e293b' : '#fff';
  const border = isDarkMode ? '#334155' : '#e2e8f0';
  const muted = isDarkMode ? '#94a3b8' : '#64748b';
  const text = isDarkMode ? '#e2e8f0' : '#1e293b';

  useEffect(() => {
    api.getExportOptions().then(setOptions).catch(e => setOptionsError(String(e?.message || e)));
  }, []);

  // Live preview: any selection change invalidates the previous preview
  // and schedules a new one.  Acknowledgement resets too — the warnings
  // the user agreed to may no longer be the ones on screen.
  useEffect(() => {
    setPreviewStale(true);
    setAcknowledged(false);
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(async () => {
      setPreviewing(true);
      try {
        const p = await api.previewExport(selection);
        setPreview(p);
        setPreviewStale(false);
      } catch (e: any) {
        message.error(e?.message || 'Preview failed');
      } finally {
        setPreviewing(false);
      }
    }, 300);
    return () => { if (debounceRef.current) clearTimeout(debounceRef.current); };
  }, [selection]);

  const update = useCallback((patch: Partial<ExportSelection>) => {
    setSelection(prev => ({ ...prev, ...patch }));
  }, []);

  const projectMode = useCallback((path: string): ProjectMode => {
    if (selection.exclude_projects.includes(path)) return 'exclude';
    if (selection.include_projects.includes(path)) return 'include';
    return 'default';
  }, [selection]);

  const setProjectMode = useCallback((path: string, mode: ProjectMode) => {
    setSelection(prev => ({
      ...prev,
      include_projects: mode === 'include'
        ? Array.from(new Set([...prev.include_projects, path]))
        : prev.include_projects.filter(p => p !== path),
      exclude_projects: mode === 'exclude'
        ? Array.from(new Set([...prev.exclude_projects, path]))
        : prev.exclude_projects.filter(p => p !== path),
    }));
  }, []);

  const readiness = useMemo(
    () => api.exportReadiness(preview, acknowledged, previewStale || previewing),
    [preview, acknowledged, previewStale, previewing],
  );

  const openSaveDialog = useCallback(() => {
    if (!preview || !readiness.ready) return;
    setSavePath(api.defaultSaveTarget(options?.default_save_dir, preview.suggested_filename));
    setSaveOpen(true);
  }, [preview, readiness.ready, options]);

  // A 409 other than file_exists means the store moved; refresh the
  // preview so the next attempt is against what's actually there.
  const refreshAfterStale = useCallback(() => {
    setSaveOpen(false);
    setSelection(prev => ({ ...prev }));
  }, []);

  const handleSave = useCallback(async (overwrite: boolean = false) => {
    if (!preview || !readiness.ready || !savePath.trim()) return;
    setExporting(true);
    try {
      const r = await api.saveExport(selection, preview.fingerprint, acknowledged, savePath, overwrite);
      setSaveOpen(false);
      message.open({
        type: 'warning', key: 'memory-export-saved', duration: 20,
        content: (
          <span>
            Exported {r.total} memories to{' '}
            <code style={{ userSelect: 'all', wordBreak: 'break-all' }}>{r.path}</code>
          </span>
        ),
      });
    } catch (e: any) {
      if (e instanceof api.ExportSaveError && e.code === 'file_exists') {
        Modal.confirm({
          title: 'File already exists',
          content: <code style={{ wordBreak: 'break-all' }}>{e.path || savePath}</code>,
          okText: 'Overwrite',
          okButtonProps: { danger: true },
          onOk: () => handleSave(true),
        });
      } else {
        message.error(e?.message || 'Export failed');
        if (e instanceof api.ExportSaveError && e.status === 409) refreshAfterStale();
      }
    } finally {
      setExporting(false);
    }
  }, [preview, readiness.ready, selection, acknowledged, savePath, refreshAfterStale]);

  const handleDownload = useCallback(async () => {
    if (!preview || !readiness.ready) return;
    setExporting(true);
    try {
      const { blob, filename } = await api.runExport(selection, preview.fingerprint, acknowledged);
      triggerDownload(blob, filename);
      setSaveOpen(false);
      // The browser does not tell the page where it put the file; name it
      // and say where browsers put downloads by default.
      message.open({
        type: 'warning', key: 'memory-export-saved', duration: 20,
        content: (
          <span>
            Sent to your browser's download folder (usually ~/Downloads) as{' '}
            <code style={{ userSelect: 'all', wordBreak: 'break-all' }}>{filename}</code>
          </span>
        ),
      });
    } catch (e: any) {
      message.error(e?.message || 'Export failed');
      refreshAfterStale();
    } finally {
      setExporting(false);
    }
  }, [preview, readiness.ready, selection, acknowledged, refreshAfterStale]);

  if (optionsError) {
    return <Alert type="error" showIcon message="Could not load export options" description={optionsError} />;
  }
  if (!options) {
    return <div style={{ textAlign: 'center', padding: 32, opacity: 0.5 }}>Loading export options...</div>;
  }

  const warningsByMemory = new Map<string, api.ExportWarning[]>();
  const globalWarnings: api.ExportWarning[] = [];
  for (const w of preview?.warnings || []) {
    if (w.memory_id) {
      const list = warningsByMemory.get(w.memory_id) || [];
      list.push(w);
      warningsByMemory.set(w.memory_id, list);
    } else {
      globalWarnings.push(w);
    }
  }
  const heldBackTotal = Object.values(preview?.held_back || {}).reduce((a, b) => a + b, 0);

  const section = (title: string, hint: string | null, body: React.ReactNode) => (
    <div style={{ background: card, border: `1px solid ${border}`, borderRadius: 8, padding: '12px 14px', marginBottom: 12 }}>
      <div style={{ fontWeight: 600, fontSize: 13, color: text, marginBottom: hint ? 2 : 8 }}>{title}</div>
      {hint && <div style={{ fontSize: 11, color: muted, marginBottom: 8 }}>{hint}</div>}
      {body}
    </div>
  );

  return (
    <div style={{ padding: '12px 0', display: 'flex', gap: 16, alignItems: 'flex-start', flexWrap: 'wrap' }}>
      {/* ── Left: selection ─────────────────────────────────────────── */}
      <div style={{ flex: '1 1 380px', minWidth: 320 }}>
        {section('Domains', `Tick a domain to export its whole subtree. Nothing ticked = every memory, including the ${options.unplaced} not yet placed in a domain.`,
          options.domains.length === 0
            ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="No domains yet — run Organize first, or export everything." />
            : (
              <Tree
                checkable
                selectable={false}
                checkStrictly
                treeData={toTreeData(options.domains)}
                checkedKeys={{ checked: selection.domains, halfChecked: [] }}
                onCheck={(keys: any) => update({ domains: (Array.isArray(keys) ? keys : keys.checked) as string[] })}
                style={{ background: 'transparent', color: text, maxHeight: 260, overflowY: 'auto' }}
              />
            ))}

        {section('Projects', 'Exclude wins over include. With nothing included, every project not excluded is in scope.',
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6, maxHeight: 220, overflowY: 'auto' }}>
            {options.projects.map(p => (
              <div key={p.path} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <Tooltip title={p.path}>
                  <span style={{ flex: 1, fontSize: 12, color: text, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {p.label} <span style={{ color: muted }}>{p.count}</span>
                  </span>
                </Tooltip>
                <Segmented
                  size="small"
                  value={projectMode(p.path)}
                  onChange={v => setProjectMode(p.path, v as ProjectMode)}
                  options={[
                    { label: '—', value: 'default' },
                    { label: 'Include', value: 'include' },
                    { label: 'Exclude', value: 'exclude' },
                  ]}
                />
              </div>
            ))}
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 4, paddingTop: 6, borderTop: `1px dashed ${border}` }}>
              <span style={{ flex: 1, fontSize: 12, color: text }}>(unscoped) <span style={{ color: muted }}>{options.unscoped}</span></span>
              <Switch size="small" checked={selection.include_unscoped} onChange={v => update({ include_unscoped: v })} />
            </div>
          </div>)}

        {section('Filters', null,
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <Select
              mode="multiple" allowClear size="small" placeholder="All layers"
              value={selection.layers} onChange={v => update({ layers: v })}
              options={options.layers.map(l => ({ value: l.layer, label: `${LAYER_ICONS[l.layer] || ''} ${LAYER_LABELS[l.layer] || l.layer} (${l.count})` }))}
            />
            <Select
              mode="multiple" size="small" placeholder="Statuses"
              value={selection.statuses} onChange={v => update({ statuses: v.length ? v : ['active'] })}
              options={options.statuses.map(s => ({ value: s.status, label: `${s.status} (${s.count})` }))}
            />
          </div>)}

        {section('What leaves the machine', 'Defaults are the safe choice. Turn these off only if the recipient needs them.',
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8, fontSize: 12, color: text }}>
            <label style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <span>Strip provenance <span style={{ color: muted }}>(conversation ids, usage telemetry)</span></span>
              <Switch size="small" checked={selection.strip_provenance} onChange={v => update({ strip_provenance: v })} />
            </label>
            <label style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <span>Anonymize project paths <span style={{ color: muted }}>(keep only the folder name)</span></span>
              <Switch size="small" checked={selection.anonymize_paths} onChange={v => update({ anonymize_paths: v })} />
            </label>
            <label style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <span>Include mind-map nodes <span style={{ color: muted }}>(domain structure for the memories)</span></span>
              <Switch size="small" checked={selection.include_mindmap} onChange={v => update({ include_mindmap: v })} />
            </label>
          </div>)}
      </div>

      {/* ── Right: preview + export ─────────────────────────────────── */}
      <div style={{ flex: '1 1 380px', minWidth: 320 }}>
        <div style={{ background: card, border: `1px solid ${border}`, borderRadius: 8, padding: '14px 16px', marginBottom: 12,
          opacity: previewStale || previewing ? 0.6 : 1, transition: 'opacity 0.2s' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 10 }}>
            <span style={{ fontWeight: 600, fontSize: 13, color: text }}>Preview</span>
            <span style={{ fontSize: 11, color: muted }}>
              {previewing ? 'refreshing…' : preview ? <>fingerprint <code>{preview.fingerprint}</code></> : ''}
            </span>
          </div>
          {!preview ? (
            <div style={{ color: muted, fontSize: 12 }}>Computing…</div>
          ) : (
            <>
              <div style={{ display: 'flex', gap: 16, alignItems: 'baseline', marginBottom: 10 }}>
                <span style={{ fontSize: 28, fontWeight: 700, color: text }}>{preview.total}</span>
                <span style={{ fontSize: 12, color: muted }}>
                  of {options.total} memories · {preview.counts.nodes} mind-map node{preview.counts.nodes === 1 ? '' : 's'}
                </span>
              </div>

              {/* Layer bar */}
              {preview.total > 0 && (
                <div style={{ display: 'flex', height: 8, borderRadius: 4, overflow: 'hidden', marginBottom: 6 }}>
                  {Object.entries(preview.counts.by_layer).map(([layer, n]) => (
                    <Tooltip key={layer} title={`${LAYER_LABELS[layer] || layer}: ${n}`}>
                      <div style={{ flex: n, background: LAYER_COLORS[layer] || '#64748b' }} />
                    </Tooltip>
                  ))}
                </div>
              )}

              <div style={{ fontSize: 12, color: text, marginTop: 10 }}>
                <div style={{ color: muted, fontSize: 11, marginBottom: 4 }}>By domain</div>
                {preview.counts.by_domain.slice(0, 8).map(d => (
                  <div key={d.id || '(unplaced)'} style={{ display: 'flex', justifyContent: 'space-between' }}>
                    <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{d.handle || d.id}</span>
                    <span style={{ color: muted, marginLeft: 8 }}>{d.count}</span>
                  </div>
                ))}
                {preview.counts.by_domain.length > 8 && (
                  <div style={{ color: muted }}>… {preview.counts.by_domain.length - 8} more</div>
                )}
                <div style={{ color: muted, fontSize: 11, margin: '8px 0 4px' }}>By project</div>
                {preview.counts.by_project.map(p => (
                  <div key={p.path || '(unscoped)'} style={{ display: 'flex', justifyContent: 'space-between' }}>
                    <span>{p.label}</span><span style={{ color: muted }}>{p.count}</span>
                  </div>
                ))}
              </div>

              {heldBackTotal > 0 && (
                <div style={{ marginTop: 12, fontSize: 12 }}>
                  <div style={{ color: muted, fontSize: 11, marginBottom: 4 }}>Held back ({heldBackTotal})</div>
                  {Object.entries(preview.held_back).map(([k, n]) => (
                    <div key={k} style={{ display: 'flex', justifyContent: 'space-between', color: text }}>
                      <span>{HELD_BACK_LABELS[k] || k}</span><span style={{ color: muted }}>{n}</span>
                    </div>
                  ))}
                </div>
              )}
            </>
          )}
        </div>

        {/* Safety check */}
        {preview && preview.warnings.length > 0 && (
          <Alert
            type="warning" showIcon icon={<WarningOutlined />} style={{ marginBottom: 12 }}
            message={`${preview.warnings.length} thing${preview.warnings.length === 1 ? '' : 's'} to check before this leaves your machine`}
            description={
              <div style={{ fontSize: 12 }}>
                {globalWarnings.map((w, i) => (
                  <div key={`g${i}`} style={{ marginBottom: 4 }}>• {warningLabel(w.kind)}</div>
                ))}
                {Array.from(warningsByMemory.entries()).slice(0, 12).map(([mid, ws]) => (
                  <div key={mid} style={{ marginBottom: 6 }}>
                    <div>
                      {ws.map(w => <Tag key={w.kind} color="orange" style={{ marginRight: 4 }}>{warningLabel(w.kind)}</Tag>)}
                      <code style={{ fontSize: 10, opacity: 0.7 }}>{mid}</code>
                    </div>
                    <div style={{ opacity: 0.8, fontStyle: 'italic' }}>“{ws[0].snippet}{ws[0].snippet.length >= 120 ? '…' : ''}”</div>
                  </div>
                ))}
                {warningsByMemory.size > 12 && <div>… {warningsByMemory.size - 12} more memories flagged</div>}
                <Checkbox checked={acknowledged} onChange={e => setAcknowledged(e.target.checked)} style={{ marginTop: 8 }}>
                  I have reviewed these and want to export them as-is
                </Checkbox>
              </div>
            }
          />
        )}
        {preview && preview.warnings.length === 0 && preview.total > 0 && !previewStale && (
          <Alert type="success" showIcon style={{ marginBottom: 12 }}
            message="No credentials, local paths or addresses detected in the selected memories." />
        )}

        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <Tooltip title={readiness.reason || `Save ${preview?.total ?? 0} memories as JSON — you choose where`}>
            <Button
              type="primary" icon={<SaveOutlined />} loading={exporting}
              disabled={!readiness.ready} onClick={openSaveDialog}
            >
              Export {preview && readiness.ready ? `${preview.total} memories` : ''}
            </Button>
          </Tooltip>
          <Button icon={<ReloadOutlined />} size="small" onClick={() => setSelection(prev => ({ ...prev }))}
            disabled={previewing}>
            Refresh preview
          </Button>
          {readiness.reason && <span style={{ fontSize: 11, color: muted }}>{readiness.reason}</span>}
        </div>

        <Modal
          title="Save memory export"
          open={saveOpen}
          onCancel={() => setSaveOpen(false)}
          footer={[
            <Button key="dl" icon={<DownloadOutlined />} onClick={handleDownload} disabled={exporting}>
              Browser download instead
            </Button>,
            <Button key="cancel" onClick={() => setSaveOpen(false)}>Cancel</Button>,
            <Button key="save" type="primary" icon={<SaveOutlined />} loading={exporting}
              disabled={!savePath.trim()} onClick={() => handleSave(false)}>
              Save
            </Button>,
          ]}
        >
          <div style={{ fontSize: 12, marginBottom: 6 }}>
            Save to (a folder or a file path; <code>~</code> is your home folder):
          </div>
          <Input value={savePath} onChange={e => setSavePath(e.target.value)}
            onPressEnter={() => handleSave(false)} autoFocus spellCheck={false} />
          <div style={{ fontSize: 11, marginTop: 6, opacity: 0.65 }}>
            {preview?.total ?? 0} memories · fingerprint {preview?.fingerprint}
          </div>
        </Modal>

        {/* Sample */}
        {preview && preview.sample.length > 0 && (
          <details style={{ marginTop: 12 }}>
            <summary style={{ cursor: 'pointer', fontSize: 12, color: muted }}>
              Sample of what the file contains ({Math.min(preview.sample.length, preview.total)} of {preview.total}, sanitized)
            </summary>
            <div style={{ maxHeight: 260, overflowY: 'auto', marginTop: 8 }}>
              {preview.sample.map(m => (
                <div key={m.id} style={{ fontSize: 12, color: text, padding: '6px 8px', borderLeft: `3px solid ${LAYER_COLORS[m.layer] || '#64748b'}`,
                  background: card, border: `1px solid ${border}`, borderLeftWidth: 3, borderRadius: 4, marginBottom: 4 }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                    <span style={{ color: muted, fontSize: 11 }}>{LAYER_ICONS[m.layer]} {LAYER_LABELS[m.layer] || m.layer}</span>
                    <span style={{ color: muted, fontSize: 11 }}>{(m.scope?.project_paths || []).join(', ')}</span>
                  </div>
                  <div>{m.content}</div>
                </div>
              ))}
            </div>
          </details>
        )}
      </div>
    </div>
  );
};

export default MemoryExportPanel;
