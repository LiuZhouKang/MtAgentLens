"use client";

import React, { useMemo, useState, useCallback } from "react";
import type { DelegationNode, HandoffObsMetrics } from "@/lib/handoff-observability-contract";
import { ChevronRight, Circle, Layers, Users, GitForkIcon, AlertTriangle } from "lucide-react";

// ═══════════════════════════════════════════════════════════════════════════════
// 常量：深度色彩映射
// ═══════════════════════════════════════════════════════════════════════════════

const DEPTH_COLORS: Record<number, { dot: string; bg: string; border: string; text: string }> = {
  0: { dot: "bg-indigo-500", bg: "bg-indigo-50", border: "border-indigo-300", text: "text-indigo-700" },
  1: { dot: "bg-blue-500", bg: "bg-blue-50", border: "border-blue-300", text: "text-blue-700" },
  2: { dot: "bg-cyan-500", bg: "bg-cyan-50", border: "border-cyan-300", text: "text-cyan-700" },
  3: { dot: "bg-teal-500", bg: "bg-teal-50", border: "border-teal-300", text: "text-teal-700" },
  4: { dot: "bg-emerald-500", bg: "bg-emerald-50", border: "border-emerald-300", text: "text-emerald-700" },
  5: { dot: "bg-amber-500", bg: "bg-amber-50", border: "border-amber-300", text: "text-amber-700" },
};

function depthColor(depth: number) {
  return DEPTH_COLORS[depth] ?? { dot: "bg-slate-400", bg: "bg-slate-50", border: "border-slate-300", text: "text-slate-600" };
}

function depthLabel(depth: number): string {
  if (depth === 0) return "根代理";
  if (depth === 1) return "第一层";
  if (depth === 2) return "第二层";
  return `第 ${depth} 层`;
}

// ═══════════════════════════════════════════════════════════════════════════════
// 局部类型扩展: 为 DelegationNode 增加运行时 children 字段
// 后端应返回嵌套结构，此处支持从 flat child_ids 构建 + 直接接收嵌套 children
// ═══════════════════════════════════════════════════════════════════════════════

interface TreeNode extends DelegationNode {
  children: TreeNode[];
}

interface FlatRow {
  node: TreeNode;
  depth: number;
  isLastChild: boolean;
  hasChildren: boolean;
}

/**
 * 将可能带 child_ids 的根节点展开为嵌套 TreeNode（含 children）。
 * 如果节点已带 children 属性（后端嵌套返回），则直接使用；
 * 否则只展示单层（仅 root），子节点通过 empty children 跳过。
 */
function buildTree(root: DelegationNode | null, allNodes?: Map<string, DelegationNode>): TreeNode | null {
  if (!root) return null;
  const nodeMap = allNodes ?? new Map();

  function toTreeNode(node: DelegationNode): TreeNode {
    // 如果节点已带嵌套 children（后端嵌套返回格式），直接递归使用
    const existingChildren = (node as unknown as { children?: unknown[] }).children;
    if (existingChildren && existingChildren.length > 0) {
      const children: TreeNode[] = existingChildren.map((child) =>
        toTreeNode(child as DelegationNode)
      );
      return { ...node, children };
    }
    // 否则从 child_ids 查找 nodeMap 构建（前端扁平模式）
    const children: TreeNode[] = [];
    for (const childId of node.child_ids) {
      const child = nodeMap.get(childId);
      if (child) {
        children.push(toTreeNode(child));
      }
    }
    return { ...node, children };
  }

  return toTreeNode(root);
}

/**
 * 将嵌套 TreeNode 展平为行列表（用于列表渲染）
 */
function flattenTree(node: TreeNode | null): FlatRow[] {
  if (!node) return [];
  const result: FlatRow[] = [];

  function walk(n: TreeNode, depth: number, isLastChild: boolean) {
    const hasChildren = n.children.length > 0;
    result.push({ node: n, depth, isLastChild, hasChildren });

    if (depth > 20) return; // 安全限制
    for (let i = 0; i < n.children.length; i++) {
      walk(n.children[i], depth + 1, i === n.children.length - 1);
    }
  }

  walk(node, 0, true);
  return result;
}

// ═══════════════════════════════════════════════════════════════════════════════
// 子组件: TreeNodeRow — 单行树节点渲染
// ═══════════════════════════════════════════════════════════════════════════════

const TreeNodeRow: React.FC<{
  node: TreeNode;
  depth: number;
  isSelected: boolean;
  onSelect: (nodeId: string) => void;
}> = ({ node, depth, isSelected, onSelect }) => {
  const color = depthColor(depth);
  const isCompleted = node.completed_at_ms != null;
  const contextOk = node.context_overhead_ratio < 0.6;
  const hasChildren = node.child_ids.length > 0;

  return (
    <button
      onClick={() => onSelect(node.node_id)}
      className={`
        w-full text-left flex items-center gap-2 px-3 py-2 rounded-md text-xs
        transition-all duration-150 border
        ${isSelected
          ? "bg-indigo-50 border-indigo-300 shadow-sm"
          : "border-transparent hover:bg-slate-50 hover:border-slate-200"
        }
      `}
    >
      {/* 深度缩进指示器 */}
      <span className="flex items-center gap-0.5 shrink-0">
        {Array.from({ length: depth }).map((_, i) => (
          <span key={i} className="w-3 h-px bg-slate-200 last:hidden" />
        ))}
      </span>

      {/* 展开/折叠图标 */}
      <span className="w-4 h-4 shrink-0 flex items-center justify-center text-slate-400">
        {hasChildren ? (
          <ChevronRight size={12} className={isSelected ? "rotate-90 transition-transform" : ""} />
        ) : (
          <span className="w-2 h-px bg-slate-300" />
        )}
      </span>

      {/* 状态圆点 */}
      <span className={`shrink-0 w-2 h-2 rounded-full ${isCompleted ? "bg-emerald-500" : "bg-amber-400 animate-pulse"}`} />

      {/* 代理标识 */}
      <span className={`font-mono truncate max-w-[120px] ${color.text}`}>
        {node.agent_id || node.node_id.slice(0, 8)}
      </span>

      {/* 深度徽章 */}
      <span className={`shrink-0 px-1 py-0.5 rounded text-[10px] font-medium ${color.bg} ${color.text}`}>
        L{depth}
      </span>

      {/* 上下文饱和度指示 */}
      {!contextOk && (
        <span className="shrink-0 flex items-center gap-0.5 text-amber-600" title={`上下文饱和度: ${(node.context_overhead_ratio * 100).toFixed(0)}%`}>
          <AlertTriangle size={10} />
        </span>
      )}

      {/* 完成状态 */}
      <span className="ml-auto shrink-0 text-[10px] text-slate-400 font-mono">
        {isCompleted ? `✓ ${Math.round((node.completed_at_ms! - node.spawned_at_ms) / 1000)}s` : "▶ 运行中"}
      </span>
    </button>
  );
};

// ═══════════════════════════════════════════════════════════════════════════════
// 子组件: MetricsBar — 指标汇总条
// ═══════════════════════════════════════════════════════════════════════════════

const MetricsBar: React.FC<{ metrics: HandoffObsMetrics }> = ({ metrics }) => {
  const items = [
    { label: "生成", value: metrics.total_spawned, icon: GitForkIcon, color: "text-indigo-600" },
    { label: "完成", value: metrics.total_completed, icon: Users, color: "text-emerald-600" },
    { label: "活跃", value: metrics.active_count, icon: Circle, color: "text-amber-600" },
    { label: "最大深度", value: metrics.max_chain_depth, icon: Layers, color: "text-blue-600" },
  ];

  return (
    <div className="flex items-center gap-3 flex-wrap">
      {items.map((item) => {
        const Icon = item.icon;
        return (
          <div key={item.label} className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-md bg-white border border-slate-200">
            <Icon size={12} className={item.color} />
            <span className="text-xs text-slate-500">{item.label}</span>
            <span className={`font-mono text-sm font-semibold ${item.color}`}>
              {item.value}
            </span>
          </div>
        );
      })}
      {metrics.fan_out_critical && (
        <span className="flex items-center gap-1 px-2 py-1 rounded-md bg-red-50 border border-red-200 text-red-700 text-[11px] font-medium">
          <AlertTriangle size={12} />
          扇出风暴: {metrics.fan_out_ratio.toFixed(1)}:1
        </span>
      )}
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════════════
// 子组件: NodeDetailPanel — 选中节点的详情面板
// ═══════════════════════════════════════════════════════════════════════════════

const NodeDetailPanel: React.FC<{
  node: TreeNode;
  onClose: () => void;
}> = ({ node, onClose }) => {
  const color = depthColor(node.chain_depth);
  const isCompleted = node.completed_at_ms != null;
  const latencyMs = isCompleted ? node.completed_at_ms! - node.spawned_at_ms : Date.now() - node.spawned_at_ms;
  const idle = node.idle_breakdown;

  const contextPct = (node.context_overhead_ratio * 100).toFixed(1);
  const contextColor = node.context_overhead_ratio > 0.85 ? "text-red-600" : node.context_overhead_ratio > 0.6 ? "text-amber-600" : "text-emerald-600";

  return (
    <div className="p-4 rounded-lg border border-slate-200 bg-white space-y-3">
      {/* 标题 */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className={`inline-block w-2.5 h-2.5 rounded-full ${isCompleted ? "bg-emerald-500" : "bg-amber-400 animate-pulse"}`} />
          <span className={`font-mono font-semibold text-sm ${color.text}`}>
            {node.agent_id || node.node_id.slice(0, 12)}
          </span>
          <span className={`text-[10px] font-medium px-1.5 py-0.5 rounded ${color.bg} ${color.text}`}>
            L{node.chain_depth}
          </span>
        </div>
        <button onClick={onClose} className="text-slate-400 hover:text-slate-600 transition-colors text-xs">
          ✕
        </button>
      </div>

      {/* 指标网格 */}
      <div className="grid grid-cols-2 gap-2">
        <div className="p-2 rounded bg-slate-50">
          <div className="text-[10px] text-slate-500 mb-0.5">延迟</div>
          <div className="font-mono text-sm font-semibold text-slate-800">
            {latencyMs > 60_000 ? `${(latencyMs / 60_000).toFixed(1)}m` : `${(latencyMs / 1000).toFixed(1)}s`}
          </div>
        </div>
        <div className="p-2 rounded bg-slate-50">
          <div className="text-[10px] text-slate-500 mb-0.5">上下文饱和度</div>
          <div className={`font-mono text-sm font-semibold ${contextColor}`}>
            {contextPct}%
          </div>
        </div>
        <div className="p-2 rounded bg-slate-50">
          <div className="text-[10px] text-slate-500 mb-0.5">输入/输出</div>
          <div className="font-mono text-sm font-semibold text-slate-800">
            {(node.bytes_in / 1024).toFixed(0)}k / {(node.bytes_out / 1024).toFixed(0)}k
          </div>
        </div>
        <div className="p-2 rounded bg-slate-50">
          <div className="text-[10px] text-slate-500 mb-0.5">Token 数</div>
          <div className="font-mono text-sm font-semibold text-slate-800">
            {node.token_count.toLocaleString()}
          </div>
        </div>
      </div>

      {/* 空闲归因 */}
      {idle && (
        <div>
          <div className="text-[10px] font-medium text-slate-500 mb-1.5">空闲归因 (ms)</div>
          <div className="space-y-1">
            <IdleBar label="排队等待派发" value={idle.queued_awaiting_dispatch_ms} max={4096} color="bg-slate-400" />
            <IdleBar label="上下文注入 I/O" value={idle.context_injection_io_ms} max={4096} color="bg-blue-400" />
            <IdleBar label="模型推理排队" value={idle.model_inference_queueing_ms} max={4096} color="bg-violet-400" />
            <IdleBar label="工具调用往返" value={idle.tool_call_round_trip_ms} max={4096} color="bg-emerald-400" />
          </div>
        </div>
      )}

      {/* 模型保真度 */}
      {node.model_assigned && (
        <div className="flex items-center gap-2 text-xs">
          <span className="text-slate-500">模型:</span>
          <code className="px-1.5 py-0.5 rounded bg-slate-100 text-slate-700">{node.model_assigned}</code>
          {node.model_optimal && node.model_optimal !== node.model_assigned && (
            <>
              <span className="text-slate-400">→ 推荐:</span>
              <code className="px-1.5 py-0.5 rounded bg-amber-50 text-amber-700">{node.model_optimal}</code>
              <span className="text-amber-500" title="非最优模型分配">⚠</span>
            </>
          )}
        </div>
      )}

      {/* 子节点统计 */}
      {node.child_ids.length > 0 && (
        <div className="text-xs text-slate-500">
          子代理: <span className="font-mono font-semibold text-slate-700">{node.child_ids.length}</span> 个
        </div>
      )}
    </div>
  );
};

/** 空闲归因进度条 */
const IdleBar: React.FC<{ label: string; value: number; max: number; color: string }> = ({ label, value, max, color }) => {
  const pct = Math.min((value / max) * 100, 100);
  return (
    <div className="flex items-center gap-2">
      <span className="text-[10px] text-slate-500 w-24 shrink-0">{label}</span>
      <div className="flex-1 h-2 bg-slate-100 rounded-full overflow-hidden">
        <div className={`h-full rounded-full ${color}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="font-mono text-[10px] text-slate-500 w-12 text-right">{value.toFixed(0)}</span>
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════════════
// 子组件: TreeConnector — 树连接线
// ═══════════════════════════════════════════════════════════════════════════════

const TreeConnector: React.FC<{ depth: number; isLast: boolean }> = ({ depth, isLast }) => {
  if (depth === 0) return null;
  return (
    <span className="shrink-0 flex items-center" style={{ width: depth * 16 }}>
      {Array.from({ length: depth - 1 }).map((_, i) => (
        <span key={i} className="w-4 h-full flex items-center">
          <span className="w-4 h-px bg-slate-200" />
        </span>
      ))}
      <span className={`w-4 flex items-center ${isLast ? "h-px" : "h-4"}`}>
        <span className={`w-2 h-px bg-slate-300 ${isLast ? "" : "-translate-y-1/2"}`} />
      </span>
    </span>
  );
};

// ═══════════════════════════════════════════════════════════════════════════════
// 主组件: HandoffDelegationTree
// ═══════════════════════════════════════════════════════════════════════════════

export interface HandoffDelegationTreeProps {
  /** 委托树根节点 */
  tree: DelegationNode | null;
  /** 聚合指标 */
  metrics: HandoffObsMetrics;
  /** 当前选中的节点 ID */
  selectedNodeId: string | null;
  /** 节点选择回调 */
  onSelectNode: (nodeId: string) => void;
  /** 时间线回放时间戳 (ms, null = 实时) */
  replayTimestampMs: number | null;
  /** 依赖边 */
  dependencyEdges?: Array<{
    from_spawn_id: string;
    to_spawn_id: string;
    artifact_path: string;
    is_orphaned: boolean;
  }>;
  /** 按深度着色 */
  colorByDepth?: boolean;
}

export const HandoffDelegationTree: React.FC<HandoffDelegationTreeProps> = ({
  tree: rootNode,
  metrics,
  selectedNodeId,
  onSelectNode,
  replayTimestampMs,
  dependencyEdges = [],
  colorByDepth = true,
}) => {
  const [showDetail, setShowDetail] = useState(false);

  // 构建内部树结构 + 展平行列表
  const { tree, flatRows } = useMemo(() => {
    if (!rootNode) return { tree: null as TreeNode | null, flatRows: [] as FlatRow[] };
    const built = buildTree(rootNode);
    return { tree: built, flatRows: flattenTree(built) };
  }, [rootNode]);

  const selectedNode = useMemo(() => {
    if (!selectedNodeId || !tree) return null;
    function find(n: TreeNode): TreeNode | null {
      if (n.node_id === selectedNodeId) return n;
      for (const c of n.children) {
        const found = find(c);
        if (found) return found;
      }
      return null;
    }
    return find(tree);
  }, [tree, selectedNodeId]);

  const orphanCount = dependencyEdges.filter((e) => e.is_orphaned).length;

  // 空状态
  if (!tree) {
    return (
      <div className="p-8 text-center text-slate-400">
        <div className="text-2xl mb-2">🕸️</div>
        <div className="text-sm">无委托数据</div>
        <div className="text-xs mt-1 text-slate-300">
          等待 AgentSight 后端推送 `delegation_tree` 事件
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* 标题栏 */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <h3 className="text-sm font-semibold text-slate-700">委托树</h3>
          <span className="text-[10px] font-mono text-slate-400 px-1.5 py-0.5 rounded bg-slate-100">
            {metrics.total_spawned} 节点
          </span>
          {orphanCount > 0 && (
            <span className="text-[10px] text-red-600 px-1.5 py-0.5 rounded bg-red-50 border border-red-200">
              {orphanCount} 孤立
            </span>
          )}
          {replayTimestampMs && (
            <span className="text-[10px] text-indigo-600 px-1.5 py-0.5 rounded bg-indigo-50 border border-indigo-200">
              回放 T+{(replayTimestampMs / 1000).toFixed(0)}s
            </span>
          )}
        </div>
        <span className="text-[10px] text-slate-400">
          {metrics.fan_out_critical ? (
            <span className="text-red-600 font-medium flex items-center gap-1">
              <AlertTriangle size={10} /> 扇出 {metrics.fan_out_ratio.toFixed(1)}:1
            </span>
          ) : (
            `扇出 ${metrics.fan_out_ratio.toFixed(1)}:1`
          )}
        </span>
      </div>

      {/* 指标条 */}
      <MetricsBar metrics={metrics} />

      {/* 树列表区域 */}
      <div className="border border-slate-200 rounded-lg bg-white divide-y divide-slate-100 max-h-[50vh] overflow-y-auto">
        {flatRows.map((row, i) => (
          <div key={row.node.node_id || i} className="flex">
            {/* 连接线 */}
            <TreeConnector depth={row.depth} isLast={row.isLastChild} />
            {/* 节点行 */}
            <div className="flex-1 min-w-0">
              <TreeNodeRow
                node={row.node}
                depth={row.depth}
                isSelected={row.node.node_id === selectedNodeId}
                onSelect={(id) => {
                  onSelectNode(id);
                  setShowDetail(true);
                }}
              />
            </div>
          </div>
        ))}
        {flatRows.length === 0 && (
          <div className="p-6 text-center text-slate-400 text-xs">
            委托树为空
          </div>
        )}
      </div>

      {/* 孤立工作提示 */}
      {dependencyEdges.filter((e) => e.is_orphaned).length > 0 && (
        <div className="p-3 rounded-lg border border-amber-200 bg-amber-50">
          <div className="flex items-center gap-1.5 text-xs font-medium text-amber-700 mb-1.5">
            <AlertTriangle size={12} />
            孤立工作
          </div>
          <div className="space-y-1">
            {dependencyEdges.filter((e) => e.is_orphaned).slice(0, 5).map((edge, i) => (
              <div key={i} className="text-[11px] text-amber-600 font-mono truncate">
                {edge.from_spawn_id.slice(0, 8)} → {edge.to_spawn_id.slice(0, 8)}: {edge.artifact_path}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 选中节点详情面板 */}
      {showDetail && selectedNode && (
        <NodeDetailPanel
          node={selectedNode}
          onClose={() => { setShowDetail(false); onSelectNode(""); }}
        />
      )}
    </div>
  );
};

export default HandoffDelegationTree;
