'use client';

import { useMemo, useState } from 'react';
import { SectionPanel } from '@/components/ui/linear/section-panel';
import {
  buildAgentProcessTrees,
  type AgentProcessTree,
  type ProcessTreeNode,
} from '@/lib/identity-lineage';
import type {
  AgentIdentity,
  IdentityProcessMapEntry,
  ProcessEvent,
  ProvisionalIdentity,
} from '@/lib/types';

const T = { xs: 9.5, sm: 10.5, md: 12 };

function statusColor(status: AgentProcessTree['status']): string {
  return status === 'authoritative' ? '#4ade80' : '#fbbf24';
}
function statusLabel(status: AgentProcessTree['status']): string {
  return status === 'authoritative' ? 'AUTH' : 'PROV';
}

/** 单进程节点：递归渲染后代，子树可折叠（默认展开，进程链深度有限）。 */
function ProcessTree({ node, agentId }: { node: ProcessTreeNode; agentId: string }) {
  const [expanded, setExpanded] = useState(true);
  const isOwn = node.agentId === agentId;
  return (
    <div>
      <button
        type="button"
        onClick={() => setExpanded(!expanded)}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 6,
          background: 'none',
          border: 'none',
          cursor: 'pointer',
          padding: '3px 0',
          color: 'var(--ln-text)',
          fontSize: T.sm,
          textAlign: 'left',
        }}
      >
        <span style={{ color: 'var(--ln-text-muted)', width: 12 }}>{expanded ? '▾' : '▸'}</span>
        <span style={{ fontFamily: 'var(--ln-mono)', color: 'var(--ln-text)', minWidth: 42 }}>
          {node.pid}
        </span>
        <span style={{ color: 'var(--ln-text-secondary)' }}>{node.comm}</span>
        {!isOwn && <span className="ln-panel__meta">跨 agent</span>}
      </button>
      {expanded && node.children.length > 0 && (
        <div style={{ marginLeft: 16, borderLeft: '1px solid var(--ln-border)', paddingLeft: 8 }}>
          {node.children.map((c) => (
            <ProcessTree key={`${c.pid}:${c.start_time_ns}`} node={c} agentId={agentId} />
          ))}
        </div>
      )}
    </div>
  );
}

export type AgentProcessTreeProps = {
  provisionalIdentities: ProvisionalIdentity[];
  agentIdentities: AgentIdentity[];
  identityProcessMap: IdentityProcessMapEntry[];
  processEvents: ProcessEvent[];
  /** cap 截断时由调用方传入，用于「已展示 X / total」标注。 */
  processEventCount?: number;
  processEventTruncated?: boolean;
};

/**
 * 进程谱系面板——按 agent 展示其进程树（provisional/authoritative 徽章 + 主信号 +
 * 置信度 + 孤儿 pid）。全部数据经 buildAgentProcessTrees 纯函数派生，无造数。
 */
export default function AgentProcessTree({
  provisionalIdentities,
  agentIdentities,
  identityProcessMap,
  processEvents,
  processEventCount,
  processEventTruncated,
}: AgentProcessTreeProps) {
  const trees = useMemo(
    () =>
      buildAgentProcessTrees({
        provisionalIdentities,
        agentIdentities,
        identityProcessMap,
        processEvents,
      }),
    [provisionalIdentities, agentIdentities, identityProcessMap, processEvents],
  );
  const [expandedAgent, setExpandedAgent] = useState<string | null>(null);

  const agents = useMemo(
    () =>
      Array.from(trees.values()).sort(
        (a, b) => (b.confidence ?? -1) - (a.confidence ?? -1) || a.agentId.localeCompare(b.agentId),
      ),
    [trees],
  );

  const empty = provisionalIdentities.length === 0 && agentIdentities.length === 0;

  return (
    <SectionPanel
      eyebrow="身份血缘"
      title="进程谱系"
      meta={
        processEventTruncated && processEventCount != null
          ? `已展示 ${processEventCount} / ${processEventCount}+`
          : undefined
      }
      bodyClassName="ln-panel-body"
    >
      {empty ? (
        <div style={{ fontSize: T.sm, color: 'var(--ln-text-muted)' }}>
          导入含进程表的 .db 后此处有数据
        </div>
      ) : agents.length === 0 ? (
        <div style={{ fontSize: T.sm, color: 'var(--ln-text-muted)' }}>无进程身份数据</div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          {agents.map((agent) => {
            const open = expandedAgent === agent.agentId;
            return (
              <div
                key={agent.agentId}
                style={{
                  borderRadius: 8,
                  background: open ? 'var(--ln-surface-0)' : 'transparent',
                  border: open ? '1px solid var(--ln-border)' : '1px solid transparent',
                }}
              >
                <button
                  type="button"
                  onClick={() => setExpandedAgent(open ? null : agent.agentId)}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 8,
                    width: '100%',
                    textAlign: 'left',
                    background: 'none',
                    border: 'none',
                    cursor: 'pointer',
                    padding: '7px 10px',
                    color: 'var(--ln-text)',
                    fontSize: T.sm,
                  }}
                >
                  <span style={{ color: 'var(--ln-text-muted)' }}>{open ? '▾' : '▸'}</span>
                  <span
                    style={{
                      fontFamily: 'var(--ln-mono)',
                      color: 'var(--ln-text)',
                      fontWeight: 600,
                    }}
                  >
                    {agent.agentId}
                  </span>
                  <span
                    style={{
                      fontSize: T.xs,
                      padding: '2px 6px',
                      borderRadius: 999,
                      fontWeight: 600,
                      fontFamily: 'var(--ln-mono)',
                      background: `${statusColor(agent.status)}16`,
                      color: statusColor(agent.status),
                    }}
                  >
                    {statusLabel(agent.status)}
                  </span>
                  <span
                    style={{
                      fontSize: T.xs,
                      color: 'var(--ln-text-muted)',
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                    }}
                  >
                    {agent.primarySignal ?? '—'}
                  </span>
                  <span
                    style={{
                      marginLeft: 'auto',
                      fontSize: T.xs,
                      fontFamily: 'var(--ln-mono)',
                      color: 'var(--ln-text-muted)',
                      flexShrink: 0,
                    }}
                  >
                    {agent.confidence != null ? `${Math.round(agent.confidence * 100)}%` : '—'}
                    {agent.orphanPids.length > 0 && ` · ${agent.orphanPids.length} 孤儿`}
                  </span>
                </button>
                {open && (
                  <div
                    style={{
                      padding: '2px 10px 10px',
                      display: 'flex',
                      flexDirection: 'column',
                      gap: 4,
                    }}
                  >
                    {agent.roots.length === 0 ? (
                      <div style={{ fontSize: T.xs, color: 'var(--ln-text-muted)' }}>
                        无进程事件记录
                      </div>
                    ) : (
                      agent.roots.map((root) => (
                        <ProcessTree
                          key={`${root.pid}:${root.start_time_ns}`}
                          node={root}
                          agentId={agent.agentId}
                        />
                      ))
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </SectionPanel>
  );
}
