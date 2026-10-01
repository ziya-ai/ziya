/**
 * Usage & Cost — the global view over the usage ledger.
 *
 * Opened from the Manage Projects footer (the closest thing Ziya has to a
 * global settings surface).  Everything shown is computed by the backend at
 * read time from the ledger + pricing catalog + rate plan; this component
 * only chooses a grouping and a window and renders the emphasis rule:
 * observed tokens bold, any-estimated italic, null cost "unpriced".
 */
import React, { useCallback, useEffect, useState } from 'react';
import { Alert, Modal, Segmented, Select, Space, Spin, Table, Tag, Tooltip, Typography } from 'antd';
import {
  costEmphasis, costQualifier, fetchUsageRollup, fetchUsageSummary, formatCost, formatTokens,
  RollupGroup, RollupKey, RollupResponse, UsageSummary,
} from '../api/usageApi';

const { Text } = Typography;

type Window = '7d' | '30d' | 'month' | 'all';

function windowSince(w: Window): string | undefined {
  const now = new Date();
  if (w === 'all') return undefined;
  if (w === 'month') return new Date(now.getFullYear(), now.getMonth(), 1).toISOString();
  const days = w === '7d' ? 7 : 30;
  return new Date(now.getTime() - days * 86400_000).toISOString();
}

const GROUPINGS: { value: RollupKey; label: string }[] = [
  { value: 'month', label: 'Month' },
  { value: 'day', label: 'Day' },
  { value: 'project', label: 'Project' },
  { value: 'model', label: 'Model' },
  { value: 'source', label: 'Source' },
  { value: 'tier', label: 'Tier' },
  { value: 'conversation', label: 'Conversation' },
];

export const CostCell: React.FC<{ g: RollupGroup }> = ({ g }) => {
  const emphasis = costEmphasis(g);
  const text = formatCost(g.effective_cost);
  const qualifier = costQualifier(g);
  const body = g.effective_cost === null
    ? <Text type="secondary">{text}</Text>
    : emphasis === 'observed'
      ? <Text strong>{text}</Text>
      : <Text italic>{text}</Text>;
  return (
    <Tooltip
      title={
        <div style={{ fontSize: 12 }}>
          <div>{emphasis === 'observed' ? 'All token counts provider-reported' : `${g.estimate_records} of ${g.records} lines estimated (pre-flight count)`}</div>
          {g.list_cost !== null && g.list_cost !== g.effective_cost && <div>List price: {formatCost(g.list_cost)}</div>}
          <div>Provenance: {Object.entries(g.provenance).map(([k, v]) => `${k} ${v}`).join(', ') || '—'}</div>
          {qualifier && <div>{qualifier}</div>}
        </div>
      }
    >
      <span>
        {body}
        {g.is_partial && g.effective_cost !== null && (
          <Text type="warning" style={{ marginLeft: 4 }}>*</Text>
        )}
      </span>
    </Tooltip>
  );
};

interface Props {
  visible: boolean;
  onClose: () => void;
  /** Pre-filter to one project (the modal still offers all groupings). */
  projectId?: string;
}

const UsageCostModal: React.FC<Props> = ({ visible, onClose, projectId }) => {
  const [by, setBy] = useState<RollupKey>('month');
  const [win, setWin] = useState<Window>('30d');
  const [scope, setScope] = useState<'all' | 'project'>(projectId ? 'project' : 'all');
  const [data, setData] = useState<RollupResponse | null>(null);
  const [summary, setSummary] = useState<UsageSummary | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [r, s] = await Promise.all([
        fetchUsageRollup(by, {
          since: windowSince(win),
          project_id: scope === 'project' ? projectId : undefined,
        }),
        summary ? Promise.resolve(summary) : fetchUsageSummary(),
      ]);
      setData(r);
      setSummary(s);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [by, win, scope, projectId, summary]);

  useEffect(() => { if (visible) void load(); }, [visible, load]);

  const totals = data?.totals;
  const unpricedShare = totals && totals.records
    ? Math.round(100 * totals.unknown_records / totals.records) : 0;

  return (
    <Modal title="Usage & cost" open={visible} onCancel={onClose} footer={null} width={860}>
      <Space wrap style={{ marginBottom: 12 }}>
        <Segmented
          size="small"
          value={by}
          onChange={v => setBy(v as RollupKey)}
          options={GROUPINGS}
        />
        <Select
          size="small" value={win} style={{ width: 120 }}
          onChange={v => setWin(v as Window)}
          options={[
            { value: '7d', label: 'Last 7 days' }, { value: '30d', label: 'Last 30 days' },
            { value: 'month', label: 'This month' }, { value: 'all', label: 'All time' },
          ]}
        />
        {projectId && (
          <Segmented
            size="small" value={scope}
            onChange={v => setScope(v as 'all' | 'project')}
            options={[{ value: 'project', label: 'This project' }, { value: 'all', label: 'All projects' }]}
          />
        )}
      </Space>

      {error && <Alert type="error" showIcon message={error} style={{ marginBottom: 12 }} />}

      {totals && unpricedShare > 0 && (
        <Alert
          type={unpricedShare >= 50 ? 'warning' : 'info'} showIcon style={{ marginBottom: 12 }}
          message={`${unpricedShare}% of records have no rate and are excluded from the totals`}
          description={
            <span style={{ fontSize: 12 }}>
              Cost is never guessed. Add rates for these models to{' '}
              <Text code>{summary?.catalog.user_file || '~/.ziya/pricing.json'}</Text>
              {' '}(see Docs/CostAccounting.md). Token counts above are complete regardless.
            </span>
          }
        />
      )}

      <Spin spinning={loading}>
        <Table<RollupGroup>
          size="small"
          rowKey="key"
          pagination={data && data.groups.length > 25 ? { pageSize: 25, size: 'small' } : false}
          dataSource={data?.groups || []}
          summary={() => totals ? (
            <Table.Summary.Row>
              <Table.Summary.Cell index={0}><Text strong>Total</Text></Table.Summary.Cell>
              <Table.Summary.Cell index={1}>{totals.records.toLocaleString()}</Table.Summary.Cell>
              <Table.Summary.Cell index={2}>{formatTokens(totals.dims.input)}</Table.Summary.Cell>
              <Table.Summary.Cell index={3}>{formatTokens(totals.dims.cache_read)}</Table.Summary.Cell>
              <Table.Summary.Cell index={4}>{formatTokens(totals.dims.output)}</Table.Summary.Cell>
              <Table.Summary.Cell index={5}><CostCell g={totals} /></Table.Summary.Cell>
            </Table.Summary.Row>
          ) : null}
          columns={[
            {
              title: GROUPINGS.find(g => g.value === by)?.label || by, dataIndex: 'key',
              render: (k: string, g) => (
                <span>
                  {by === 'model' && g.provider ? <Tag style={{ marginRight: 4 }}>{g.provider}</Tag> : null}
                  <Text style={{ fontSize: 12 }} ellipsis={{ tooltip: k }}>
                    {by === 'model' && g.provider && k.startsWith(g.provider + '/') ? k.slice(g.provider.length + 1) : k}
                  </Text>
                </span>
              ),
            },
            { title: 'Calls', dataIndex: 'records', width: 80, align: 'right', render: (n: number) => n.toLocaleString() },
            { title: 'Input', width: 90, align: 'right', render: (_, g) => formatTokens(g.dims.input) },
            { title: 'Cached', width: 90, align: 'right', render: (_, g) => formatTokens(g.dims.cache_read) },
            { title: 'Output', width: 90, align: 'right', render: (_, g) => formatTokens(g.dims.output) },
            {
              title: 'Cost', width: 130, align: 'right',
              sorter: (a, b) => (a.effective_cost ?? -1) - (b.effective_cost ?? -1),
              render: (_, g) => <CostCell g={g} />,
            },
          ]}
        />
      </Spin>

      <div style={{ marginTop: 12, fontSize: 11, display: 'flex', gap: 16, flexWrap: 'wrap' }}>
        <Text type="secondary"><Text strong style={{ fontSize: 11 }}>bold</Text> = all counts provider-reported</Text>
        <Text type="secondary"><Text italic style={{ fontSize: 11 }}>italic</Text> = includes estimated counts</Text>
        <Text type="secondary">* = some dimensions unpriced</Text>
        {data && (
          <Text type="secondary">
            catalog {data.catalog_version} · plan {data.plan.id}
            {summary ? ` · ${summary.ledger.records.toLocaleString()} records in ledger` : ''}
          </Text>
        )}
      </div>
    </Modal>
  );
};

export default UsageCostModal;
