'use client';

import type { DataSourceMeta } from '@/lib/store';

type Props = {
  dataSource: DataSourceMeta | null;
  graphNodeCount: number;
  evidenceCount: number;
};

/**
 * Empty state for the alert workbench. Distinguishes "no data source attached"
 * (invite the user to import / watch a session) from "a real run with a graph
 * but zero triggered alerts" (common for monitoring sessions — report the
 * consumable counts instead of claiming nothing was loaded).
 */
export default function AlertEmptyState({ dataSource, graphNodeCount, evidenceCount }: Props) {
  // Backend runs (kind 'run') carry no `label` — use the run id.
  const label = dataSource?.kind === 'run' ? dataSource.runId : (dataSource?.label ?? '');
  const message = dataSource
    ? graphNodeCount > 0
      ? `当前数据源「${label}」未检测到告警（已解析 ${graphNodeCount} 节点 · ${evidenceCount} 条证据）`
      : `当前数据源「${label}」中没有可解析的责任数据`
    : '等待导入数据或实时观测会话接入';

  return (
    <div
      className="ln-panel"
      style={{ maxWidth: 480, margin: '10vh auto 0', padding: 28, textAlign: 'center' }}
    >
      <span className="ln-eyebrow">告警</span>
      <h1 className="ln-h1" style={{ marginTop: 12 }}>
        暂无告警数据
      </h1>
      <p className="ln-desc" style={{ marginTop: 12 }}>
        {message}
      </p>
    </div>
  );
}
