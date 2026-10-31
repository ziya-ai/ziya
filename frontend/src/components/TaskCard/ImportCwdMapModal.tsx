/**
 * Shown between choosing an export file and importing it, when the file
 * holds absolute `scope.cwd` paths.  One row per distinct path: the path
 * as written on the exporting machine, and a target directory on this
 * one (typed, or picked with the same directory browser the editor's cwd
 * picker uses).  "Set all to…" covers the common 1:1 case.  Rows left
 * blank import unchanged.
 */
import React, { useEffect, useState } from 'react';
import { Modal, Input, Button, Tooltip } from 'antd';
import { FolderOpenOutlined } from '@ant-design/icons';
import { DirectoryBrowserModal } from '../DirectoryBrowserModal';
import {
  applyToAll, buildCwdMap, initialTargets, mappedCount, type CwdTargets,
} from './importCwdMap';

interface ImportCwdMapModalProps {
  open: boolean;
  /** Distinct absolute cwd paths found in the file (from /import/inspect). */
  cwds: string[];
  cardCount: number;
  /** This project's root, for the "project root" shortcut and the hint. */
  projectPath: string | null;
  onCancel: () => void;
  /** Import with the given map (possibly empty = as-is). */
  onImport: (cwdMap: Record<string, string>) => Promise<void>;
}

export const ImportCwdMapModal: React.FC<ImportCwdMapModalProps> = ({
  open, cwds, cardCount, projectPath, onCancel, onImport,
}) => {
  const [targets, setTargets] = useState<CwdTargets>(() => initialTargets(cwds));
  // Which row the directory browser is picking for; 'ALL' = set-all.
  const [browsing, setBrowsing] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (open) setTargets(initialTargets(cwds));
  }, [open, cwds]);

  const setOne = (src: string, dst: string) =>
    setTargets((t) => ({ ...t, [src]: dst }));
  const setAll = (dst: string) => setTargets(applyToAll(cwds, dst));

  const n = mappedCount(targets);
  const importLabel = n === 0
    ? 'Import as-is'
    : `Import with ${n} of ${cwds.length} path${cwds.length === 1 ? '' : 's'} remapped`;

  const submit = async () => {
    setBusy(true);
    try {
      await onImport(buildCwdMap(targets));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <Modal
        open={open}
        title="Working directories in this export"
        width={720}
        onCancel={onCancel}
        footer={[
          <Button key="cancel" onClick={onCancel} disabled={busy}>Cancel</Button>,
          <Button key="ok" type="primary" onClick={submit} loading={busy}
            data-testid="import-cwd-submit">
            {importLabel}
          </Button>,
        ]}
      >
        <p style={{ fontSize: 12, marginTop: 0 }}>
          {cardCount === 1 ? 'This card' : `These ${cardCount} cards`} set a working
          directory using {cwds.length === 1 ? 'an absolute path' : 'absolute paths'} from
          the machine that exported {cardCount === 1 ? 'it' : 'them'}. A task's working
          directory must be inside this project
          {projectPath ? <> (<code>{projectPath}</code>)</> : null}; a path outside it
          is ignored at run time and the task runs at the project root instead.
          Choose a local directory for each, or leave a row blank to import it unchanged.
        </p>

        <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 12 }}>
          <span style={{ fontSize: 12, whiteSpace: 'nowrap' }}>Set all to:</span>
          <Tooltip title="Pick one local directory for every path">
            <Button size="small" icon={<FolderOpenOutlined />}
              onClick={() => setBrowsing('ALL')} data-testid="import-cwd-browse-all">
              Browse…
            </Button>
          </Tooltip>
          {projectPath && (
            <Button size="small" onClick={() => setAll(projectPath)}
              data-testid="import-cwd-all-root">
              Project root
            </Button>
          )}
          <Button size="small" onClick={() => setAll('')} disabled={n === 0}>
            Clear
          </Button>
        </div>

        <div style={{ maxHeight: 320, overflow: 'auto' }}>
          {cwds.map((src) => (
            <div key={src} data-testid="import-cwd-row"
              style={{ display: 'grid', gridTemplateColumns: '1fr 1fr auto',
                       gap: 8, alignItems: 'center', marginBottom: 8 }}>
              <Tooltip title={src}>
                <code style={{ fontSize: 11, overflow: 'hidden',
                               textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {src}
                </code>
              </Tooltip>
              <Input
                size="small"
                value={targets[src] ?? ''}
                placeholder="(leave unchanged)"
                onChange={(e) => setOne(src, e.target.value)}
                data-testid="import-cwd-target"
              />
              <Tooltip title="Browse for a local directory">
                <Button size="small" icon={<FolderOpenOutlined />}
                  onClick={() => setBrowsing(src)} />
              </Tooltip>
            </div>
          ))}
        </div>
      </Modal>

      <DirectoryBrowserModal
        open={browsing !== null}
        onClose={() => setBrowsing(null)}
        title="Select local working directory"
        subtitle={browsing === 'ALL'
          ? 'Every remapped task will run in this folder'
          : `Tasks that used ${browsing ?? ''} will run in this folder`}
        confirmLabel="Use this folder"
        busyLabel="Setting…"
        onSelect={async (path) => {
          if (browsing === 'ALL') setAll(path);
          else if (browsing) setOne(browsing, path);
          setBrowsing(null);
        }}
      />
    </>
  );
};

export default ImportCwdMapModal;
