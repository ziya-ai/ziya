/**
 * FirstRunProjectDialog
 *
 * Shown only when Ziya boots with no usable project: there are no projects
 * at all AND the startup directory has none. Offers two choices:
 *   1. Create a project rooted at the startup directory (one click), or
 *   2. Browse to and select a different directory to root a project in.
 *
 * Both paths call `createProject`, which creates-or-returns the project and
 * auto-switches to it; we then dismiss via `onResolved`.
 */
import React, { useState } from 'react';
import { Modal, Button, Input, List, Space, Typography, message } from 'antd';
import { FolderOpenOutlined, PlusOutlined } from '@ant-design/icons';
import { Project } from '../types/project';

const { Text } = Typography;

interface BrowseEntry {
  name: string;
  path: string;
  is_dir: boolean;
}

interface FirstRunProjectDialogProps {
  /** Absolute startup directory the server was launched in. */
  root: string;
  createProject: (path: string, name?: string) => Promise<Project>;
  onResolved: () => void;
}

const FirstRunProjectDialog: React.FC<FirstRunProjectDialogProps> = ({
  root,
  createProject,
  onResolved,
}) => {
  const [busy, setBusy] = useState(false);
  const [browsing, setBrowsing] = useState(false);
  const [browsePath, setBrowsePath] = useState(root);
  const [entries, setEntries] = useState<BrowseEntry[]>([]);

  const create = async (path: string) => {
    if (busy) return;
    setBusy(true);
    try {
      await createProject(path);
      onResolved();
    } catch (e) {
      console.error('FirstRunProjectDialog: failed to create project', e);
      message.error('Failed to create project for that directory.');
    } finally {
      setBusy(false);
    }
  };

  const browse = async (path: string) => {
    try {
      const res = await fetch(`/api/browse-directory?path=${encodeURIComponent(path)}`);
      if (res.ok) {
        const data = await res.json();
        setBrowsePath(data.current_path);
        setEntries((data.entries || []).filter((e: BrowseEntry) => e.is_dir));
      }
    } catch (e) {
      console.error('FirstRunProjectDialog: browse failed', e);
    }
  };

  const openBrowser = () => {
    setBrowsing(true);
    browse(root);
  };

  return (
    <>
      <Modal
        open={!browsing}
        title="Welcome to Ziya — choose a project directory"
        closable={false}
        maskClosable={false}
        keyboard={false}
        footer={null}
        width={560}
      >
        <Space direction="vertical" size="middle" style={{ width: '100%' }}>
          <Text>
            No projects exist yet. A project roots file context, AST indexing,
            and shell commands at a directory.
          </Text>
          <div>
            <Text strong>Ziya was started in:</Text>
            <div style={{ marginTop: 4 }}>
              <Text code>{root}</Text>
            </div>
          </div>
          <Button
            type="primary"
            icon={<PlusOutlined />}
            loading={busy}
            block
            onClick={() => create(root)}
          >
            Create a project here
          </Button>
          <Button
            icon={<FolderOpenOutlined />}
            disabled={busy}
            block
            onClick={openBrowser}
          >
            Choose a different directory…
          </Button>
        </Space>
      </Modal>

      <Modal
        open={browsing}
        title="Select a project directory"
        onCancel={() => setBrowsing(false)}
        okText="Use this directory"
        okButtonProps={{ loading: busy }}
        onOk={() => create(browsePath)}
        width={560}
      >
        <Space direction="vertical" size="small" style={{ width: '100%' }}>
          <Input.Group compact>
            <Input
              style={{ width: 'calc(100% - 90px)' }}
              value={browsePath}
              onChange={(e) => setBrowsePath(e.target.value)}
              onPressEnter={() => browse(browsePath)}
            />
            <Button style={{ width: 90 }} onClick={() => browse(browsePath)}>
              Go
            </Button>
          </Input.Group>
          <List
            size="small"
            bordered
            style={{ maxHeight: 300, overflowY: 'auto' }}
            dataSource={[{ name: '..', path: `${browsePath}/..`, is_dir: true }, ...entries]}
            renderItem={(item) => (
              <List.Item
                style={{ cursor: 'pointer' }}
                onClick={() => browse(item.path)}
              >
                <FolderOpenOutlined style={{ marginRight: 8 }} />
                {item.name}
              </List.Item>
            )}
          />
        </Space>
      </Modal>
    </>
  );
};

export default FirstRunProjectDialog;
