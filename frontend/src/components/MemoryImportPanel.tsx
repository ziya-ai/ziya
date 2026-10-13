/**
 * MemoryImportPanel — the Import side of the Memory Browser's transfer tab.
 *
 * Takes a ziya-memory-export bundle, previews how every memory in it
 * would land in this store (new / identical / duplicate / collision),
 * lets the user pick the collision policy, and applies exactly the
 * previewed plan: the server refuses (409) if the bundle or the store
 * moved in between.  Embeddings are not computed on import; the success
 * state offers the backfill so semantic retrieval covers what arrived.
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Alert, Button, Input, Segmented, Switch, Tag, Tooltip, Upload, message } from 'antd';
import { InboxOutlined, ImportOutlined, ThunderboltOutlined } from '@ant-design/icons';
import * as api from '../api/memoryApi';
import { LAYER_COLORS, LAYER_ICONS, LAYER_LABELS } from '../api/memoryApi';
import type { ImportDisposition, ImportOptions, ImportPreview, ImportResult } from '../api/memoryApi';

interface Props {
  isDarkMode: boolean;
  onImported?: () => void;
}

const DISPOSITION_LABELS: Record<ImportDisposition, { label: string; color: string; hint: string }> = {
  new: { label: 'new', color: 'green', hint: 'Not in your memory; will be added.' },
  identical: { label: 'already have', color: 'default', hint: 'Same id and content as one of yours; skipped.' },
  duplicate_content: { label: 'duplicate', color: 'default', hint: 'Same content under another id; skipped, references point at yours.' },
  collision_renamed: { label: 'renamed', color: 'orange', hint: 'Your memory has this id with different content; the incoming one gets a fresh id.' },
  collision_skipped: { label: 'skipped', color: 'red', hint: 'Your memory has this id with different content; incoming one dropped.' },
  collision_overwritten: { label: 'overwrites yours', color: 'red', hint: 'Your memory with this id will be replaced.' },
};

const MemoryImportPanel: React.FC<Props> = ({ isDarkMode, onImported }) => {
  const [bundle, setBundle] = useState<unknown>(null);
  const [fileName, setFileName] = useState<string | null>(null);
  const [options, setOptions] = useState<ImportOptions>(api.DEFAULT_IMPORT_OPTIONS);
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const [previewStale, setPreviewStale] = useState(false);
  const [previewing, setPreviewing] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [importing, setImporting] = useState(false);
  const [result, setResult] = useState<ImportResult | null>(null);
  const [backfilling, setBackfilling] = useState(false);
  const debounceRef = useRef<ReturnType<typeof setTimeout>>();

  const card = isDarkMode ? '#1e293b' : '#fff';
  const border = isDarkMode ? '#334155' : '#e2e8f0';
  const muted = isDarkMode ? '#94a3b8' : '#64748b';
  const text = isDarkMode ? '#e2e8f0' : '#1e293b';

  // Re-preview whenever the bundle or the options change.
  useEffect(() => {
    if (!bundle) { setPreview(null); return; }
    setPreviewStale(true);
    setResult(null);
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(async () => {
      setPreviewing(true);
      setPreviewError(null);
      try {
        setPreview(await api.previewImport(bundle, options));
        setPreviewStale(false);
      } catch (e: any) {
        setPreview(null);
        setPreviewError(e?.message || 'Preview failed');
      } finally {
        setPreviewing(false);
      }
    }, 250);
    return () => { if (debounceRef.current) clearTimeout(debounceRef.current); };
  }, [bundle, options]);

  const update = useCallback((patch: Partial<ImportOptions>) => {
    setOptions(prev => ({ ...prev, ...patch }));
  }, []);

  const readiness = useMemo(
    () => api.importReadiness(preview, previewStale || previewing),
    [preview, previewStale, previewing],
  );

  const handleFile = useCallback(async (file: File) => {
    try {
      const obj = await api.readBundleFile(file);
      setBundle(obj);
      setFileName(file.name);
      // Default the label to the exporter named in the bundle.
      const by = (obj as any)?.exported_by;
      if (typeof by === 'string' && by) setOptions(prev => ({ ...prev, source_label: prev.source_label ?? by }));
    } catch (e: any) {
      message.error(e?.message || 'Could not read file');
    }
    return false;  // handled here; antd must not upload it anywhere
  }, []);

  const handleImport = useCallback(async () => {
    if (!bundle || !preview || !readiness.ready) return;
    setImporting(true);
    try {
      const r = await api.runImport(bundle, options, preview, fileName);
      setResult(r);
      message.success(`Imported ${r.imported} memor${r.imported === 1 ? 'y' : 'ies'}`);
      onImported?.();
    } catch (e: any) {
      message.error(e?.message || 'Import failed');
      setOptions(prev => ({ ...prev }));  // 409: re-preview against what's there now
    } finally {
      setImporting(false);
    }
  }, [bundle, preview, readiness.ready, options, fileName, onImported]);

  const handleBackfill = useCallback(async () => {
    setBackfilling(true);
    try {
      const r = await api.backfillEmbeddings();
      message.success(r.status === 'disabled' ? 'Embeddings are not configured; keyword search still covers the imported memories.'
        : `Embedded ${r.embedded ?? 0} memories`);
    } catch (e: any) {
      message.error(e?.message || 'Backfill failed');
    } finally {
      setBackfilling(false);
    }
  }, []);

  const section = (title: string, hint: string | null, body: React.ReactNode) => (
    <div style={{ background: card, border: `1px solid ${border}`, borderRadius: 8, padding: '12px 14px', marginBottom: 12 }}>
      <div style={{ fontWeight: 600, fontSize: 13, color: text, marginBottom: hint ? 2 : 8 }}>{title}</div>
      {hint && <div style={{ fontSize: 11, color: muted, marginBottom: 8 }}>{hint}</div>}
      {body}
    </div>
  );

  const count = (k: string) => preview?.counts[k] || 0;

  return (
    <div style={{ padding: '12px 0', display: 'flex', gap: 16, alignItems: 'flex-start', flexWrap: 'wrap' }}>
      <div style={{ flex: '1 1 380px', minWidth: 320 }}>
        {section('Bundle', 'A ziya-memory-export JSON file from another user\'s Memory Browser.',
          <Upload.Dragger accept=".json,application/json" multiple={false} showUploadList={false}
            beforeUpload={handleFile} style={{ background: 'transparent' }}>
            <p className="ant-upload-drag-icon"><InboxOutlined /></p>
            <p style={{ color: text, fontSize: 13 }}>{fileName ? fileName : 'Drop a bundle here, or click to choose'}</p>
            {preview && (
              <p style={{ color: muted, fontSize: 11 }}>
                {preview.total} memories{preview.exported_by ? ` · exported by ${preview.exported_by}` : ''}
                {preview.exported_at ? ` · ${preview.exported_at.slice(0, 10)}` : ''}
              </p>
            )}
          </Upload.Dragger>)}

        {section('When an id already exists with different content', null,
          <Segmented
            size="small" value={options.on_collision}
            onChange={v => update({ on_collision: v as ImportOptions['on_collision'] })}
            options={[
              { label: 'Keep both (rename incoming)', value: 'rename' },
              { label: 'Keep mine', value: 'skip' },
              { label: 'Take theirs', value: 'overwrite' },
            ]}
          />)}

        {section('Options', null,
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8, fontSize: 12, color: text }}>
            <label style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <span>Skip memories whose content I already have <span style={{ color: muted }}>(under any id)</span></span>
              <Switch size="small" checked={options.skip_duplicate_content} onChange={v => update({ skip_duplicate_content: v })} />
            </label>
            <label style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <span>Merge their mind-map nodes into mine</span>
              <Switch size="small" checked={options.include_mindmap} onChange={v => update({ include_mindmap: v })} />
            </label>
            <label style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8 }}>
              <span>Tag imported memories <span style={{ color: muted }}>from:</span></span>
              <Input size="small" style={{ width: 160 }} placeholder="label" maxLength={64}
                value={options.source_label ?? ''} onChange={e => update({ source_label: e.target.value || null })} />
            </label>
          </div>)}
      </div>

      <div style={{ flex: '1 1 380px', minWidth: 320 }}>
        <div style={{ background: card, border: `1px solid ${border}`, borderRadius: 8, padding: '14px 16px', marginBottom: 12,
          opacity: previewStale || previewing ? 0.6 : 1, transition: 'opacity 0.2s' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 10 }}>
            <span style={{ fontWeight: 600, fontSize: 13, color: text }}>What would happen</span>
            <span style={{ fontSize: 11, color: muted }}>{previewing ? 'refreshing…' : ''}</span>
          </div>
          {previewError && <Alert type="error" showIcon message={previewError} style={{ marginBottom: 8 }} />}
          {!preview && !previewError && <div style={{ color: muted, fontSize: 12 }}>Choose a bundle to see the plan.</div>}
          {preview && (
            <>
              <div style={{ display: 'flex', gap: 16, alignItems: 'baseline', marginBottom: 10 }}>
                <span style={{ fontSize: 28, fontWeight: 700, color: text }}>{count('to_write')}</span>
                <span style={{ fontSize: 12, color: muted }}>of {preview.total} will be written</span>
              </div>
              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 10 }}>
                {(Object.keys(DISPOSITION_LABELS) as ImportDisposition[]).filter(k => count(k) > 0).map(k => (
                  <Tooltip key={k} title={DISPOSITION_LABELS[k].hint}>
                    <Tag color={DISPOSITION_LABELS[k].color}>{count(k)} {DISPOSITION_LABELS[k].label}</Tag>
                  </Tooltip>
                ))}
              </div>
              <div style={{ fontSize: 12, color: text }}>
                Mind-map: {preview.nodes_new.length} new node{preview.nodes_new.length === 1 ? '' : 's'}, {preview.nodes_merged.length} merged into existing.
              </div>
              {preview.warnings.length > 0 && (
                <Alert type="warning" showIcon style={{ marginTop: 10 }}
                  message={`${preview.warnings.length} note${preview.warnings.length === 1 ? '' : 's'}`}
                  description={<div style={{ fontSize: 11 }}>{preview.warnings.slice(0, 8).map((w, i) => <div key={i}>{w}</div>)}</div>} />
              )}
            </>
          )}
        </div>

        {result ? (
          <Alert type="success" showIcon style={{ marginBottom: 12 }}
            message={`Imported ${result.imported} memor${result.imported === 1 ? 'y' : 'ies'}`}
            description={
              <div style={{ fontSize: 12 }}>
                {result.embeddings_pending > 0 && (
                  <div style={{ marginTop: 4 }}>
                    Keyword search covers them now. Semantic retrieval needs embeddings for the {result.embeddings_pending} new memor{result.embeddings_pending === 1 ? 'y' : 'ies'}.
                    <Button size="small" icon={<ThunderboltOutlined />} loading={backfilling} onClick={handleBackfill} style={{ marginLeft: 8 }}>
                      Backfill embeddings
                    </Button>
                  </div>
                )}
              </div>
            } />
        ) : (
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 12 }}>
            <Tooltip title={readiness.reason || `Write ${count('to_write')} memories into your store`}>
              <Button type="primary" icon={<ImportOutlined />} loading={importing}
                disabled={!readiness.ready} onClick={handleImport}>
                Import {preview && readiness.ready ? `${count('to_write')} memories` : ''}
              </Button>
            </Tooltip>
            {readiness.reason && <span style={{ fontSize: 11, color: muted }}>{readiness.reason}</span>}
          </div>
        )}

        {preview && preview.sample.length > 0 && (
          <details>
            <summary style={{ cursor: 'pointer', fontSize: 12, color: muted }}>
              Per-memory plan ({Math.min(preview.sample.length, preview.total)} of {preview.total})
            </summary>
            <div style={{ maxHeight: 280, overflowY: 'auto', marginTop: 8 }}>
              {preview.sample.map(m => {
                const d = DISPOSITION_LABELS[m.disposition];
                return (
                  <div key={m.id} style={{ fontSize: 12, color: text, padding: '6px 8px', marginBottom: 4,
                    background: card, border: `1px solid ${border}`, borderLeft: `3px solid ${LAYER_COLORS[m.layer || ''] || '#64748b'}`, borderRadius: 4 }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, marginBottom: 2 }}>
                      <span style={{ color: muted, fontSize: 11 }}>{LAYER_ICONS[m.layer || '']} {LAYER_LABELS[m.layer || ''] || m.layer}</span>
                      <Tooltip title={d.hint}><Tag color={d.color} style={{ margin: 0, fontSize: 10 }}>{d.label}</Tag></Tooltip>
                    </div>
                    <div>{m.content}</div>
                  </div>
                );
              })}
            </div>
          </details>
        )}
      </div>
    </div>
  );
};

export default MemoryImportPanel;
