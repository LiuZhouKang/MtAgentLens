'use client';

import { useMemo, useRef, useState } from 'react';
import { useGSAP } from '@gsap/react';
import gsap from 'gsap';
import { SectionPanel } from '@/components/ui/linear/section-panel';
import AgentAvatar from '@/components/ui/agent-avatar';
import {
  buildAgentProcessTimeline,
  buildAgentProcessTrees,
  buildProcessActions,
  type AgentProcessTree as AgentProcessTreeModel,
  type ProcessAction,
  type ProcessTreeNode,
  type TimelineProcess,
} from '@/lib/identity-lineage';
import { processIconSpec } from '@/lib/identity-process-icons';
import { actionTypeLabel } from '@/lib/monitoring/perspective-labels';
import type {
  Action,
  AgentIdentity,
  IdentityProcessMapEntry,
  ProcessEvent,
  ProvisionalIdentity,
} from '@/lib/types';

const T = { xs: 9.5, sm: 10.5, md: 12 };

function statusLabel(status: AgentProcessTreeModel['status']): string {
  return status === 'authoritative' ? 'AUTH' : 'PROV';
}

/** 后代节点计数（含自身）—— 兼容 ProcessTreeNode 与 TimelineProcess。 */
function countSubtree(node: ProcessTreeNode | TimelineProcess): number {
  return 1 + node.children.reduce((s, c) => s + countSubtree(c), 0);
}

/** 相对时间格式化：<1000ms 显示 ms，否则 s。 */
function fmtMs(ms: number): string {
  return ms < 1000 ? `${Math.round(ms)}ms` : `${(ms / 1000).toFixed(1)}s`;
}

/** 动作类型 → 徽章 tone */
function actionTone(actionType: string): string {
  if (actionType === 'FileRead') return 'read';
  if (actionType === 'FileWrite') return 'write';
  if (actionType === 'FileOverwrite') return 'overwrite';
  if (actionType === 'FileRename') return 'overwrite';
  if (actionType === 'ProcessExec') return 'exec';
  return 'other';
}

/** 关联动作列表（进程 → 具体行为）：错峰浮现动效 */
function ProcessActions({ actions }: { actions: ProcessAction[] }) {
  const rootRef = useRef<HTMLDivElement>(null);
  // 动作条目错峰浮现（左滑入 + 淡入）
  useGSAP(
    () => {
      const root = rootRef.current;
      if (!root) return;
      if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
      const items = root.querySelectorAll<HTMLElement>('.ln-tree-action');
      if (items.length === 0) return;
      gsap.from(items, {
        opacity: 0,
        x: -8,
        duration: 0.32,
        stagger: 0.025,
        ease: 'power2.out',
        delay: 0.05,
        clearProps: 'transform',
      });
    },
    { scope: rootRef, dependencies: [actions] },
  );

  if (actions.length === 0) return null;
  return (
    <div ref={rootRef} className="ln-tree-actions" role="list" aria-label="进程行为">
      {actions.map((a) => {
        const tone = actionTone(a.actionType);
        const label = actionTypeLabel(a.actionType).label;
        return (
          <div key={a.actionId} className="ln-tree-action" role="listitem">
            <span className="ln-tree-action__type" data-tone={tone}>
              {label}
            </span>
            <span className="ln-tree-action__target" title={a.target ?? undefined}>
              {a.target ?? '（无路径记录）'}
            </span>
          </div>
        );
      })}
    </div>
  );
}

/** 单进程树节点：chevron + 图标 + pid + comm + 相对时间，递归渲染后代 + 关联动作。 */
function ProcessTree({
  node,
  agentId,
  depth = 0,
  actionsByPid,
}: {
  node: TimelineProcess;
  agentId: string;
  depth?: number;
  actionsByPid: Map<number, ProcessAction[]>;
}) {
  const [expanded, setExpanded] = useState(depth < 2); // 前两层默认展开，深层收起（信息不过载）
  const collapseRef = useRef<HTMLDivElement>(null);
  const isOwn = node.agentId === agentId;
  const hasChildren = node.children.length > 0;
  const hasActions = (actionsByPid.get(node.pid)?.length ?? 0) > 0;
  const iconSpec = processIconSpec(node.comm);
  const Icon = iconSpec.Icon;
  const expandable = hasChildren || hasActions;

  // 展开/收起高度过渡 + 子节点错峰揭示（GSAP，reduced-motion 直接切换）
  useGSAP(
    () => {
      const el = collapseRef.current;
      if (!el) return;
      const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
      if (reduce) {
        gsap.set(el, { height: expanded ? 'auto' : 0 });
        return;
      }
      gsap.to(el, {
        height: expanded ? 'auto' : 0,
        opacity: expanded ? 1 : 0,
        duration: 0.24,
        ease: 'power2.inOut',
        overwrite: 'auto',
        onStart: () => {
          if (!expanded || !el) return;
          // 展开时：子进程树节点错峰浮现（上浮 + 淡入）
          const childTrees = el.querySelectorAll<HTMLElement>(':scope > .ln-tree-children > div');
          if (childTrees.length > 0) {
            gsap.from(childTrees, {
              opacity: 0,
              y: 6,
              duration: 0.3,
              stagger: 0.04,
              ease: 'power2.out',
              clearProps: 'transform',
            });
          }
        },
      });
    },
    { scope: collapseRef, dependencies: [expanded] },
  );

  return (
    <div>
      <button
        type="button"
        onClick={() => expandable && setExpanded(!expanded)}
        className="ln-tree-node"
        style={{ paddingLeft: depth > 0 ? 10 : 2 }}
      >
        <span style={{ color: 'var(--ln-text-muted)', width: 12, flexShrink: 0 }}>
          {expandable ? (expanded ? '▾' : '▸') : '·'}
        </span>
        <span className="ln-tree-node__icon">
          <Icon size={10} strokeWidth={2} />
        </span>
        <span className="ln-tree-node__pid">{node.pid}</span>
        <span className="ln-tree-node__comm">{node.comm}</span>
        {!isOwn && <span className="ln-tree-node__cross">跨 agent</span>}
        <span className="ln-tree-node__time">
          {hasActions ? `${actionsByPid.get(node.pid)!.length} 动作 · ` : ''}
          +{fmtMs(node.startMs)}
        </span>
      </button>
      {expandable && (
        <div ref={collapseRef} className="ln-tree-collapse" style={{ height: expanded ? 'auto' : 0 }}>
          <ProcessActions actions={actionsByPid.get(node.pid) ?? []} />
          {hasChildren && (
            <div className="ln-tree-children">
              {node.children.map((c) => (
                <ProcessTree
                  key={`${c.pid}:${c.start_time_ns}`}
                  node={c}
                  agentId={agentId}
                  depth={depth + 1}
                  actionsByPid={actionsByPid}
                />
              ))}
            </div>
          )}
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
  /** 关联动作（进程 → 具体行为，如读某文件/写某文件/执行命令） */
  actions?: Action[];
  processEventCount?: number;
  processEventTruncated?: boolean;
};

/**
 * 进程谱系——顶级身份关系工作台。
 *
 * 信息架构：
 *  - 外层 agent 按最早进程事件时间升序（真实时间流），无进程的沉底。
 *  - 展开后：权威进程树（父子层级 + 品牌连接线 + lucide 进程图标 + 相对时间标注）。
 * 全部数据经纯函数派生（buildAgentProcessTrees / buildAgentProcessTimeline），无造数。
 */
export default function AgentProcessTree({
  provisionalIdentities,
  agentIdentities,
  identityProcessMap,
  processEvents,
  actions = [],
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
  // 进程 → 关联动作（具体行为）
  const actionsByPid = useMemo(() => buildProcessActions(actions), [actions]);
  const [expandedAgent, setExpandedAgent] = useState<string | null>(null);
  const listRef = useRef<HTMLDivElement>(null);

  // 每 agent 最早进程事件时间（真实时间流排序依据）；无进程 → null
  const earliestByAgent = useMemo(() => {
    const map = new Map<string, number | null>();
    for (const agent of trees.values()) {
      if (agent.roots.length === 0) {
        map.set(agent.agentId, null);
        continue;
      }
      const timeline = buildAgentProcessTimeline({
        processEvents,
        identityProcessMap,
        agentId: agent.agentId,
      });
      const earliest = timeline ? Math.min(...timeline.roots.map((r) => r.ts_ns)) : Infinity;
      map.set(agent.agentId, Number.isFinite(earliest) ? earliest : null);
    }
    return map;
  }, [trees, processEvents, identityProcessMap]);

  // 排序：按最早进程事件时间升序（真实时间流），无进程的沉底
  const agents = useMemo(
    () =>
      Array.from(trees.values()).sort((a, b) => {
        const aT = earliestByAgent.get(a.agentId);
        const bT = earliestByAgent.get(b.agentId);
        if (aT == null && bT == null) {
          return (b.confidence ?? -1) - (a.confidence ?? -1) || a.agentId.localeCompare(b.agentId);
        }
        if (aT == null) return 1; // a 无进程 → 沉底
        if (bT == null) return -1;
        return aT - bT; // 时间升序
      }),
    [trees, earliestByAgent],
  );

  // 外层 agent 行错峰浮现（真实时间流的逐行揭示）
  useGSAP(
    () => {
      const list = listRef.current;
      if (!list) return;
      if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
      const rows = list.querySelectorAll<HTMLElement>('.ln-lineage-row');
      if (rows.length === 0) return;
      gsap.from(rows, {
        opacity: 0,
        x: -8,
        duration: 0.4,
        stagger: 0.03,
        ease: 'power2.out',
        delay: 0.08,
        clearProps: 'transform',
      });
    },
    { scope: listRef, dependencies: [agents] },
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
        <div ref={listRef} style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          {agents.map((agent) => {
            const open = expandedAgent === agent.agentId;
            const hasTree = agent.roots.length > 0;
            const procCount = agent.roots.reduce((s, r) => s + countSubtree(r), 0);
            const timeline = hasTree
              ? buildAgentProcessTimeline({
                  processEvents,
                  identityProcessMap,
                  agentId: agent.agentId,
                })
              : null;
            return (
              <div
                key={agent.agentId}
                style={{
                  borderRadius: 8,
                  border: open ? '1px solid var(--ln-border)' : '1px solid transparent',
                  transition: 'border-color 160ms ease, background 160ms ease',
                }}
              >
                <button
                  type="button"
                  onClick={() => setExpandedAgent(open ? null : agent.agentId)}
                  className="ln-lineage-row"
                  data-open={open ? 'true' : 'false'}
                  aria-expanded={open}
                >
                  <span style={{ color: 'var(--ln-text-muted)', width: 12, flexShrink: 0 }}>
                    {open ? '▾' : '▸'}
                  </span>
                  <AgentAvatar agentId={agent.agentId} size={22} />
                  <span
                    style={{
                      fontFamily: 'var(--ln-mono)',
                      fontWeight: 600,
                      fontSize: T.sm,
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                    }}
                  >
                    {agent.agentId}
                  </span>
                  <span
                    className="ln-lineage-badge"
                    data-tone={agent.status === 'authoritative' ? 'auth' : 'prov'}
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
                  <span className="ln-lineage-count">
                    {hasTree
                      ? `${procCount} 进程`
                      : agent.orphanPids.length > 0
                        ? `${agent.orphanPids.length} 孤儿`
                        : '0 进程'}
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
                  </span>
                </button>
                {open && (
                  <div
                    style={{
                      padding: '4px 10px 10px',
                      display: 'flex',
                      flexDirection: 'column',
                      gap: 2,
                    }}
                  >
                    {!hasTree ? (
                      <div className="ln-lineage-note">
                        <i aria-hidden />
                        该身份由 TLS 会话信号关联，无独立进程事件记录
                      </div>
                    ) : timeline ? (
                      <div>
                        {timeline.roots.map((root) => (
                          <ProcessTree
                            key={`${root.pid}:${root.start_time_ns}`}
                            node={root}
                            agentId={agent.agentId}
                            actionsByPid={actionsByPid}
                          />
                        ))}
                      </div>
                    ) : (
                      <div className="ln-lineage-note">
                        <i aria-hidden />
                        进程事件缺失时间戳，无法构建进程树
                      </div>
                    )}
                    {agent.orphanPids.length > 0 && (
                      <div className="ln-lineage-note">
                        <i aria-hidden />
                        另有 {agent.orphanPids.length} 个进程已在身份映射但无事件记录
                      </div>
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
