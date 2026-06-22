"use client";

import React, { useMemo, useState, useCallback } from "react";
import type { DelegationNode, HandoffObsMetrics, HandoffObservabilityEvent } from "@/lib/handoff-observability-contract";
import { HandoffDelegationTree } from "./handoff-delegation-tree";
import {
  AlertTriangle,
  Activity,
  Shield,
  FileText,
  Layers,
  GitBranch,
  Zap,
  X,
} from "lucide-react";

// ═══════════════════════════════════════════════════════════════════════════════
// 常量: 视图 Tab 定义
// ═══════════════════════════════════════════════════════════════════════════════

type HandoffDashboardView =
  | "delegation_tree"
  | "events"
  | "fanout_monitor"
  | "satellite";

const VIEW_TABS: { key: HandoffDashboardView; label: string; icon: React.ReactNode }[] = [
  { key: "delegation_tree", label: "委托树", icon: <GitBranch size={13} /> },
  { key: "events", label: "事件流", icon: <Zap size={13} /> },
  { key: "fanout_monitor", label: "扇出监控", icon: <Activity size={13} /> },
  { key: "satellite", label: "边界卫星", icon: <Shield size={13} /> },
];

// ═══════════════════════════════════════════════════════════════════════════════
// 工具函数
// ═══════════════════════════════════════════════════════════════════════════════

const SEVERITY_COLORS: Record<string, { text: string; bg: string; dot: string }> = {
  INFO:    { text: "text-blue-700", bg: "bg-blue-50", dot: "bg-blue-500" },
  WARNING: { text: "text-amber-700", bg: "bg-amber-50", dot: "bg-amber-500" },
  ERROR:   { text: "text-red-700", bg: "bg-red-50", dot: "bg-red-500" },
  CRITICAL: { text: "text-red-800", bg: "bg-red-100", dot: "bg-red-600" },
};

function severityColor(sev: string) {
  return SEVERITY_COLORS[sev.toUpperCase()] ?? SEVERITY_COLORS.INFO;
}

function prettySubType(subType: string): string {
  const map: Record<string, string> = {
    HANDOFF_SPAWN: "子代理生成",
    HANDOFF_COMPLETE: "子代理完成",
    BOUNDARY_TAG: "边界标记",
    FILE_CORRELATION: "文件关联",
    CONTEXT_SATURATION: "上下文饱和",
    TOOL_DENIED: "工具拒绝",
    FANOUT_STORM: "扇出风暴",
    DATA_LEAK: "数据泄漏",
    STRATEGY_DIVERGENCE: "策略偏离",
    CONTEXT_CONFLICT: "上下文冲突",
    CORRECTIVE_LOOP: "纠正循环",
    ORPHANED_WORK: "孤立工作",
    DRIFT_ALERT: "漂移告警",
  };
  return map[subType] ?? subType;
}

function formatTimestamp(ms: number): string {
  const d = new Date(ms);
  return d.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

// ═══════════════════════════════════════════════════════════════════════════════
// 子组件: HandoffEventCard — 单条 handoff 事件卡片
// ═══════════════════════════════════════════════════════════════════════════════

const HandoffEventCard: React.FC<{
  event: HandoffObservabilityEvent;
  onSelect?: (event: HandoffObservabilityEvent) => void;
}> = ({ event, onSelect }) => {
  const subtype = (event as any).sub_type ?? "UNKNOWN";
  const sev = (event as any).severity ?? "INFO";
  const color = severityColor(sev);
  const ts = (event as any).timestamp_ms ?? (event as any).dispatched_at_ms ?? Date.now();

  return (
    <button
      onClick={() => onSelect?.(event)}
      className="w-full text-left flex items-start gap-2.5 p-2.5 rounded-lg border border-slate-200 bg-white hover:bg-slate-50 hover:border-slate-300 transition-all text-xs"
    >
      <span className={`shrink-0 w-2 h-2 rounded-full mt-0.5 ${color.dot}`} />
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-1.5 mb-0.5">
          <span className={`font-medium ${color.text}`}>{prettySubType(subtype)}</span>
          {(event as any).agent_id && (
            <code className="text-[10px] text-slate-400 font-mono">{(event as any).agent_id}</code>
          )}
        </div>
        <div className="flex items-center gap-2 text-[10px] text-slate-400">
          <span>{formatTimestamp(ts)}</span>
          {(event as any).chain_depth != null && (
            <span className="px-1 py-px rounded bg-slate-100">L{(event as any).chain_depth}</span>
          )}
          {(event as any).overhead_ratio != null && (
            <span className={severityColor(sev).text}>
              {(event as any).overhead_ratio * 100}%
            </span>
          )}
          {(event as any).ratio != null && (
            <span className="text-red-600">{event as any}ratio:1</span>
          )}
        </div>
      </div>
      <span className={`shrink-0 text-[10px] font-mono px-1.5 py-0.5 rounded ${color.bg} ${color.text}`}>
        {sev}
      </span>
    </button>
  );
};

// ═══════════════════════════════════════════════════════════════════════════════
// 子组件: FanoutMonitorPanel — 扇出风暴监控
// ═══════════════════════════════════════════════════════════════════════════════

const FanoutMonitorPanel: React.FC<{
  metrics: HandoffObsMetrics;
  events: HandoffObservabilityEvent[];
}> = ({ metrics, events }) => {
  // 从事件中提取 spawn/completion 时间序列
  const spawnHistory = useMemo(() => {
    return events
      .filter((e) => (e as any).sub_type === "HANDOFF_SPAWN")
      .map((e) => ({ ts: (e as any).dispatched_at_ms ?? Date.now(), type: "spawn" as const }));
  }, [events]);

  const completionHistory = useMemo(() => {
    return events
      .filter((e) => (e as any).sub_type === "HANDOFF_COMPLETE")
      .map((e) => ({ ts: (e as any).timestamp_ms ?? Date.now(), type: "complete" as const }));
  }, [events]);

  const fanoutEvents = useMemo(() => {
    return events.filter((e) => (e as any).sub_type === "FANOUT_STORM");
  }, [events]);

  return (
    <div className="space-y-3">
      {/* 指标卡 */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
        <div className="p-3 rounded-lg border border-slate-200 bg-white">
          <div className="flex items-center gap-1.5 text-xs text-slate-500 mb-1">
            <Activity size={12} /> 扇出比率
          </div>
          <div className={`font-mono text-lg font-bold ${metrics.fan_out_critical ? "text-red-600" : "text-slate-800"}`}>
            {metrics.fan_out_ratio.toFixed(2)}:1
          </div>
        </div>
        <div className="p-3 rounded-lg border border-slate-200 bg-white">
          <div className="text-xs text-slate-500 mb-1">生成 / 完成</div>
          <div className="font-mono text-lg font-bold text-slate-800">
            {metrics.total_spawned} / {metrics.total_completed}
          </div>
        </div>
        <div className="p-3 rounded-lg border border-slate-200 bg-white">
          <div className="text-xs text-slate-500 mb-1">活跃子代理</div>
          <div className="font-mono text-lg font-bold text-amber-600">{metrics.active_count}</div>
        </div>
        <div className="p-3 rounded-lg border border-slate-200 bg-white">
          <div className="text-xs text-slate-500 mb-1">平均上下文开销</div>
          <div className="font-mono text-lg font-bold text-slate-800">
            {(metrics.avg_context_overhead * 100).toFixed(1)}%
          </div>
        </div>
      </div>

      {/* 告警列表 */}
      {fanoutEvents.length > 0 && (
        <div className="p-3 rounded-lg border border-red-200 bg-red-50">
          <div className="flex items-center gap-1.5 text-xs font-medium text-red-700 mb-2">
            <AlertTriangle size={12} />
            扇出风暴告警 ({fanoutEvents.length})
          </div>
          <div className="space-y-1">
            {fanoutEvents.slice(0, 10).map((e, i) => {
              const evt = e as any;
              return (
                <div key={i} className="text-[11px] text-red-600 font-mono flex items-center gap-2">
                  <span>{formatTimestamp(evt.timestamp_ms)}</span>
                  <span className="font-bold">{evt.ratio?.toFixed(2)}:1</span>
                  <span className="text-red-400">窗口 {evt.window_seconds}s</span>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* 无告警 */}
      {fanoutEvents.length === 0 && (
        <div className="p-4 text-center text-slate-400 text-xs rounded-lg border border-slate-200 bg-white">
          未检测到扇出异常。生成/完成比率在正常范围内。
        </div>
      )}
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════════════
// 子组件: BoundarySatellitePanel — 工作区边界和 PIPELINE 完整性
// ═══════════════════════════════════════════════════════════════════════════════

const BoundarySatellitePanel: React.FC<{
  events: HandoffObservabilityEvent[];
  metrics: HandoffObsMetrics;
}> = ({ events, metrics }) => {
  const boundaryEvents = useMemo(() => events.filter((e) => (e as any).sub_type === "BOUNDARY_TAG"), [events]);
  const dataLeaks = useMemo(() => events.filter((e) => (e as any).sub_type === "DATA_LEAK"), [events]);
  const boundaryDrifts = useMemo(() => events.filter((e) => (e as any).sub_type === "BOUNDARY_DRIFT" || (e as any).sub_type === "BOUNDARY_DRIFT" ), [events]);

  const outsideCount = boundaryEvents.filter((e) => (e as any).workspace_tag === "outside").length;
  const preservedCount = boundaryEvents.filter((e) => (e as any).tag_preserved === true).length;
  const survivalRate = boundaryEvents.length > 0 ? preservedCount / boundaryEvents.length : metrics.workspace_tag_survival_rate;

  return (
    <div className="space-y-3">
      {/* 摘要卡片 */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
        <div className="p-3 rounded-lg border border-slate-200 bg-white">
          <div className="text-xs text-slate-500 mb-1">边界存活率</div>
          <div className={`font-mono text-lg font-bold ${survivalRate < 1 ? "text-amber-600" : "text-emerald-600"}`}>
            {(survivalRate * 100).toFixed(1)}%
          </div>
        </div>
        <div className="p-3 rounded-lg border border-slate-200 bg-white">
          <div className="text-xs text-slate-500 mb-1">外部访问</div>
          <div className="font-mono text-lg font-bold text-red-600">{outsideCount}</div>
        </div>
        <div className="p-3 rounded-lg border border-slate-200 bg-white">
          <div className="text-xs text-slate-500 mb-1">数据泄漏</div>
          <div className="font-mono text-lg font-bold text-red-600">{dataLeaks.length}</div>
        </div>
        <div className="p-3 rounded-lg border border-slate-200 bg-white">
          <div className="text-xs text-slate-500 mb-1">边界漂移</div>
          <div className="font-mono text-lg font-bold text-amber-600">{boundaryDrifts.length}</div>
        </div>
      </div>

      {/* 边界事件列表 */}
      {boundaryEvents.length > 0 && (
        <div className="rounded-lg border border-slate-200 bg-white divide-y divide-slate-100">
          <div className="px-3 py-2 text-[11px] font-medium text-slate-500">边界标记事件</div>
          {boundaryEvents.slice(0, 20).map((e, i) => {
            const evt = e as any;
            const tagColor = evt.workspace_tag === "outside" ? "text-red-600" : evt.workspace_tag === "boundary-ambiguous" ? "text-amber-600" : "text-emerald-600";
            return (
              <div key={i} className="flex items-center gap-2 px-3 py-1.5 text-xs">
                <span className={`w-1.5 h-1.5 rounded-full ${evt.workspace_tag === "outside" ? "bg-red-500" : evt.workspace_tag === "boundary-ambiguous" ? "bg-amber-500" : "bg-emerald-500"}`} />
                <code className="font-mono text-slate-600 truncate flex-1">{evt.target_path}</code>
                <span className={`font-mono text-[10px] ${tagColor}`}>{evt.workspace_tag}</span>
                <span className="text-slate-400">{formatTimestamp(evt.timestamp_ms ?? Date.now())}</span>
              </div>
            );
          })}
        </div>
      )}

      {/* 数据泄漏告警 */}
      {dataLeaks.length > 0 && (
        <div className="p-3 rounded-lg border border-red-200 bg-red-50">
          <div className="flex items-center gap-1.5 text-xs font-medium text-red-700 mb-2">
            <Shield size={12} />
            数据泄漏 ({dataLeaks.length})
          </div>
          {dataLeaks.slice(0, 5).map((e, i) => {
            const evt = e as any;
            return (
              <div key={i} className="text-[11px] text-red-600 font-mono">
                {evt.leaking_agent_id?.slice(0, 8)} → {evt.accessing_agent_id?.slice(0, 8)}: {evt.exposed_path}
              </div>
            );
          })}
        </div>
      )}

      {/* 空状态 */}
      {boundaryEvents.length === 0 && dataLeaks.length === 0 && (
        <div className="p-4 text-center text-slate-400 text-xs rounded-lg border border-slate-200 bg-white">
          暂未检测到边界相关事件。工作区完整性良好。
        </div>
      )}
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════════════
// 主组件: HandoffDashboardShell
// ═══════════════════════════════════════════════════════════════════════════════

export interface HandoffDashboardShellProps {
  /** 委托树根节点 */
  delegationTree: DelegationNode | null;
  /** 聚合指标 */
  metrics: HandoffObsMetrics;
  /** 最近 handoff 可观测性事件列表 */
  recentEvents: HandoffObservabilityEvent[];
  /** 当前选中的节点 ID */
  selectedNodeId: string | null;
  /** 依赖边 (用于孤立工作检测) */
  dependencyEdges?: Array<{
    from_spawn_id: string;
    to_spawn_id: string;
    artifact_path: string;
    is_orphaned: boolean;
  }>;

  // 回调
  onSelectNode: (nodeId: string) => void;
  onSelectEvent?: (event: HandoffObservabilityEvent) => void;
  /** 时间线回放时间戳 */
  replayTimestampMs?: number | null;
}

export const HandoffDashboardShell: React.FC<HandoffDashboardShellProps> = ({
  delegationTree,
  metrics,
  recentEvents,
  selectedNodeId,
  dependencyEdges = [],
  onSelectNode,
  onSelectEvent,
  replayTimestampMs = null,
}) => {
  const [activeView, setActiveView] = useState<HandoffDashboardView>("delegation_tree");

  // 按严重度统计
  const severityCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const e of recentEvents) {
      const sev = ((e as any).severity ?? "INFO").toUpperCase();
      counts[sev] = (counts[sev] || 0) + 1;
    }
    return counts;
  }, [recentEvents]);

  return (
    <div className="space-y-3">
      {/* 顶栏: 视图 Tab */}
      <div className="flex items-center gap-1 border-b border-slate-200 pb-2">
        {VIEW_TABS.map((tab) => {
          const isActive = activeView === tab.key;
          const hasAlerts = tab.key === "events" ? severityCounts["CRITICAL"] > 0 || severityCounts["ERROR"] > 0 : false;
          return (
            <button
              key={tab.key}
              onClick={() => setActiveView(tab.key)}
              className={`
                flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md transition-all
                ${isActive
                  ? "bg-indigo-600 text-white shadow-sm"
                  : "text-slate-500 hover:text-slate-700 hover:bg-slate-100"
                }
              `}
            >
              <span className={isActive ? "text-white/80" : "text-slate-400"}>{tab.icon}</span>
              <span>{tab.label}</span>
              {hasAlerts && (
                <span className={`inline-flex items-center justify-center w-4 h-4 rounded-full text-[9px] font-bold ${
                  isActive ? "bg-white/20 text-white" : "bg-red-100 text-red-700"
                }`}>
                  {(severityCounts["CRITICAL"] || 0) + (severityCounts["ERROR"] || 0)}
                </span>
              )}
            </button>
          );
        })}
      </div>

      {/* 视图内容 */}
      <div className="transition-opacity duration-200">
        {/* ── 委托树视图 ── */}
        {activeView === "delegation_tree" && (
          <HandoffDelegationTree
            tree={delegationTree}
            metrics={metrics}
            selectedNodeId={selectedNodeId}
            onSelectNode={onSelectNode}
            replayTimestampMs={replayTimestampMs}
            dependencyEdges={dependencyEdges}
          />
        )}

        {/* ── 事件流视图 ── */}
        {activeView === "events" && (
          <div className="space-y-3">
            {/* 严重度统计 */}
            <div className="flex items-center gap-2 flex-wrap">
              {Object.entries(severityCounts).map(([sev, count]) => {
                const color = severityColor(sev);
                return (
                  <span key={sev} className={`inline-flex items-center gap-1 px-2 py-1 rounded text-xs font-medium ${color.bg} ${color.text}`}>
                    <span className={`w-1.5 h-1.5 rounded-full ${color.dot}`} />
                    {sev}: {count}
                  </span>
                );
              })}
              {Object.keys(severityCounts).length === 0 && (
                <span className="text-xs text-slate-400">暂无事件</span>
              )}
            </div>

            {/* 事件列表 */}
            <div className="space-y-1 max-h-[55vh] overflow-y-auto">
              {recentEvents.length === 0 ? (
                <div className="p-6 text-center text-slate-400 text-xs rounded-lg border border-slate-200 bg-white">
                  <div className="text-lg mb-1">📡</div>
                  <div>等待 handoff_observability SSE 事件...</div>
                  <div className="text-slate-300 mt-1">
                    后端推送 `source=handoff_observability` 事件后即可显示
                  </div>
                </div>
              ) : (
                recentEvents.slice(0, 100).map((event, i) => {
                  const id = (event as any).spawn_id ?? (event as any).alert_id ?? (event as any).access_id ?? i;
                  return (
                    <HandoffEventCard
                      key={id}
                      event={event}
                      onSelect={onSelectEvent}
                    />
                  );
                })
              )}
            </div>
          </div>
        )}

        {/* ── 扇出监控视图 ── */}
        {activeView === "fanout_monitor" && (
          <FanoutMonitorPanel metrics={metrics} events={recentEvents} />
        )}

        {/* ── 边界卫星视图 ── */}
        {activeView === "satellite" && (
          <BoundarySatellitePanel events={recentEvents} metrics={metrics} />
        )}
      </div>

      {/* 底部状态栏 */}
      <div className="flex items-center justify-between pt-2 border-t border-slate-100 text-[10px] text-slate-400">
        <span>
          {activeView === "delegation_tree" && `${metrics.total_spawned} spawned · ${metrics.total_completed} completed · ${metrics.active_count} active`}
          {activeView === "events" && `${recentEvents.length} events · ${severityCounts["CRITICAL"] || 0} critical · ${severityCounts["WARNING"] || 0} warning`}
          {activeView === "fanout_monitor" && `ratio ${metrics.fan_out_ratio.toFixed(2)}:1 · avg overhead ${(metrics.avg_context_overhead * 100).toFixed(1)}%`}
          {activeView === "satellite" && `tag survival ${(metrics.workspace_tag_survival_rate * 100).toFixed(1)}% · ${metrics.orphaned_artifacts} orphaned`}
        </span>
        <span>{replayTimestampMs ? `回放 T+${(replayTimestampMs / 1000).toFixed(0)}s` : `${recentEvents.length} events`}</span>
      </div>
    </div>
  );
};

export default HandoffDashboardShell;
