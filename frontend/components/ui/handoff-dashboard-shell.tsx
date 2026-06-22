"use client";

import React, { useMemo, useState } from "react";
import type { DelegationNode, HandoffObsMetrics, HandoffObservabilityEvent } from "@/lib/handoff-observability-contract";
import { HandoffDelegationTree } from "./handoff-delegation-tree";
import {
  AlertTriangle,
  Activity,
  Shield,
  GitBranch,
  Zap,
} from "lucide-react";
import PrismStatCard from "@/components/ui/prism-stat-card";

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

const SEVERITY_COLORS: Record<string, { text: string; bg: string; dot: string; border: string; tone: "info" | "warning" | "danger" }> = {
  INFO: { text: "var(--tone-info-text)", bg: "var(--tone-info-bg)", dot: "#60a5fa", border: "var(--tone-info-border)", tone: "info" },
  WARNING: { text: "var(--tone-warning-text)", bg: "var(--tone-warning-bg)", dot: "#fbbf24", border: "var(--tone-warning-border)", tone: "warning" },
  ERROR: { text: "var(--tone-danger-text)", bg: "var(--tone-danger-bg)", dot: "#fb7185", border: "var(--tone-danger-border)", tone: "danger" },
  CRITICAL: { text: "var(--tone-danger-text)", bg: "var(--tone-danger-bg)", dot: "#fb7185", border: "var(--tone-danger-border)", tone: "danger" },
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
      className="prism-list-row"
    >
      <span
        className="shrink-0 rounded-full"
        style={{
          width: 8,
          height: 8,
          marginTop: 4,
          background: color.dot,
          boxShadow: `0 0 0 4px ${color.bg}`,
        }}
      />
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-1.5 mb-0.5">
          <span style={{ fontSize: 12, fontWeight: 600, color: color.text }}>{prettySubType(subtype)}</span>
          {(event as any).agent_id && (
            <code style={{ fontSize: 10, color: "var(--text-tertiary)", fontFamily: "var(--font-geist-mono), monospace" }}>
              {(event as any).agent_id}
            </code>
          )}
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", fontSize: 10, color: "var(--text-tertiary)" }}>
          <span>{formatTimestamp(ts)}</span>
          {(event as any).chain_depth != null && (
            <span
              style={{
                padding: "2px 6px",
                borderRadius: 999,
                border: "1px solid var(--border-soft)",
                background: "var(--surface-soft)",
              }}
            >
              L{(event as any).chain_depth}
            </span>
          )}
          {(event as any).overhead_ratio != null && (
            <span style={{ color: severityColor(sev).text }}>
              {(event as any).overhead_ratio * 100}%
            </span>
          )}
          {(event as any).ratio != null && (
            <span style={{ color: "var(--tone-danger-text)" }}>{(event as any).ratio}:1</span>
          )}
        </div>
      </div>
      <span
        className="shrink-0 font-mono"
        style={{
          padding: "4px 7px",
          borderRadius: 999,
          fontSize: 10,
          background: color.bg,
          border: `1px solid ${color.border}`,
          color: color.text,
        }}
      >
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
  const fanoutEvents = useMemo(() => {
    return events.filter((e) => (e as any).sub_type === "FANOUT_STORM");
  }, [events]);

  return (
    <div className="space-y-3">
      <div className="prism-detail-grid">
        <PrismStatCard
          label="扇出比率"
          value={`${metrics.fan_out_ratio.toFixed(2)}:1`}
          hint="识别委托是否开始并行失控。"
          toneColor={metrics.fan_out_critical ? "#fb7185" : "#60a5fa"}
        />
        <PrismStatCard
          label="生成 / 完成"
          value={`${metrics.total_spawned} / ${metrics.total_completed}`}
          hint="检查链路生成与收敛是否匹配。"
        />
        <PrismStatCard
          label="活跃子代理"
          value={metrics.active_count}
          hint="实时仍在委托链中的执行体数量。"
          toneColor="#fbbf24"
        />
        <PrismStatCard
          label="平均上下文开销"
          value={`${(metrics.avg_context_overhead * 100).toFixed(1)}%`}
          hint="用于评估 handoff 数据膨胀与饱和风险。"
        />
      </div>

      {fanoutEvents.length > 0 && (
        <div
          style={{
            padding: 14,
            borderRadius: 18,
            border: "1px solid var(--tone-danger-border)",
            background: "var(--tone-danger-bg)",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, fontWeight: 600, color: "var(--tone-danger-text)", marginBottom: 8 }}>
            <AlertTriangle size={12} />
            扇出风暴告警 ({fanoutEvents.length})
          </div>
          <div style={{ display: "grid", gap: 6 }}>
            {fanoutEvents.slice(0, 10).map((e, i) => {
              const evt = e as any;
              return (
                <div
                  key={i}
                  className="font-mono"
                  style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 11, color: "var(--tone-danger-text)" }}
                >
                  <span>{formatTimestamp(evt.timestamp_ms)}</span>
                  <span className="font-bold">{evt.ratio?.toFixed(2)}:1</span>
                  <span style={{ opacity: 0.78 }}>窗口 {evt.window_seconds}s</span>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {fanoutEvents.length === 0 && (
        <div className="prism-list-surface" style={{ padding: 18, textAlign: "center", color: "var(--text-tertiary)", fontSize: 12 }}>
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
      <div className="prism-detail-grid">
        <PrismStatCard
          label="边界存活率"
          value={`${(survivalRate * 100).toFixed(1)}%`}
          hint="观察标签和边界上下文的保真度。"
          toneColor={survivalRate < 1 ? "#fbbf24" : "#34d399"}
        />
        <PrismStatCard label="外部访问" value={outsideCount} hint="被标记为 outside 的访问数量。" toneColor="#fb7185" />
        <PrismStatCard label="数据泄漏" value={dataLeaks.length} hint="跨主体暴露敏感物料的次数。" toneColor="#fb7185" />
        <PrismStatCard label="边界漂移" value={boundaryDrifts.length} hint="边界规则失配或逐步衰减的迹象。" toneColor="#fbbf24" />
      </div>

      {boundaryEvents.length > 0 && (
        <div className="prism-list-surface">
          <div
            style={{
              padding: "12px 14px",
              fontSize: 11,
              fontWeight: 600,
              letterSpacing: "0.12em",
              textTransform: "uppercase",
              color: "var(--text-quaternary)",
              borderBottom: "1px solid var(--border-soft)",
            }}
          >
            边界标记事件
          </div>
          {boundaryEvents.slice(0, 20).map((e, i) => {
            const evt = e as any;
            const tagTone =
              evt.workspace_tag === "outside"
                ? { dot: "#fb7185", text: "var(--tone-danger-text)" }
                : evt.workspace_tag === "boundary-ambiguous"
                  ? { dot: "#fbbf24", text: "var(--tone-warning-text)" }
                  : { dot: "#34d399", text: "var(--tone-success-text)" };
            return (
              <div
                key={i}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                  padding: "10px 14px",
                  fontSize: 12,
                  borderTop: i === 0 ? "none" : "1px solid var(--border-soft)",
                }}
              >
                <span style={{ width: 7, height: 7, borderRadius: "50%", background: tagTone.dot }} />
                <code
                  className="font-mono truncate flex-1"
                  style={{ color: "var(--text-secondary)", fontFamily: "var(--font-geist-mono), monospace" }}
                >
                  {evt.target_path}
                </code>
                <span className="font-mono" style={{ fontSize: 10, color: tagTone.text }}>
                  {evt.workspace_tag}
                </span>
                <span style={{ color: "var(--text-tertiary)", fontSize: 10 }}>{formatTimestamp(evt.timestamp_ms ?? Date.now())}</span>
              </div>
            );
          })}
        </div>
      )}

      {dataLeaks.length > 0 && (
        <div
          style={{
            padding: 14,
            borderRadius: 18,
            border: "1px solid var(--tone-danger-border)",
            background: "var(--tone-danger-bg)",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, fontWeight: 600, color: "var(--tone-danger-text)", marginBottom: 8 }}>
            <Shield size={12} />
            数据泄漏 ({dataLeaks.length})
          </div>
          {dataLeaks.slice(0, 5).map((e, i) => {
            const evt = e as any;
            return (
              <div key={i} className="font-mono" style={{ fontSize: 11, color: "var(--tone-danger-text)" }}>
                {evt.leaking_agent_id?.slice(0, 8)} → {evt.accessing_agent_id?.slice(0, 8)}: {evt.exposed_path}
              </div>
            );
          })}
        </div>
      )}

      {boundaryEvents.length === 0 && dataLeaks.length === 0 && (
        <div className="prism-list-surface" style={{ padding: 18, textAlign: "center", color: "var(--text-tertiary)", fontSize: 12 }}>
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
  onSelectNode: (nodeId: string | null) => void;
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
      <div className="prism-tab-rail">
        {VIEW_TABS.map((tab) => {
          const isActive = activeView === tab.key;
          const hasAlerts = tab.key === "events" ? severityCounts["CRITICAL"] > 0 || severityCounts["ERROR"] > 0 : false;
          return (
            <button
              key={tab.key}
              onClick={() => setActiveView(tab.key)}
              className="prism-tab"
              data-active={isActive}
            >
              <span className="prism-tab__icon">{tab.icon}</span>
              <span>{tab.label}</span>
              {hasAlerts && (
                <span className="prism-tab__count" data-tone="danger">
                  {(severityCounts["CRITICAL"] || 0) + (severityCounts["ERROR"] || 0)}
                </span>
              )}
            </button>
          );
        })}
      </div>

      <div className="transition-opacity duration-200">
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

        {activeView === "events" && (
          <div className="space-y-3">
            <div className="prism-pill-row">
              {Object.entries(severityCounts).map(([sev, count]) => {
                const color = severityColor(sev);
                return (
                  <span
                    key={sev}
                    className="prism-pill"
                    data-tone={color.tone}
                    style={{ letterSpacing: 0 }}
                  >
                    <span style={{ width: 7, height: 7, borderRadius: "50%", background: color.dot }} />
                    {sev}: {count}
                  </span>
                );
              })}
              {Object.keys(severityCounts).length === 0 && (
                <span style={{ fontSize: 12, color: "var(--text-tertiary)" }}>暂无事件</span>
              )}
            </div>

            <div className="prism-list-surface prism-list-surface--scroll prism-premium-scroll" style={{ padding: 10 }}>
              {recentEvents.length === 0 ? (
                <div style={{ padding: 28, textAlign: "center", color: "var(--text-tertiary)", fontSize: 12 }}>
                  <div style={{ fontSize: 22, marginBottom: 6 }}>📡</div>
                  <div>等待 handoff_observability SSE 事件...</div>
                  <div style={{ color: "var(--text-quaternary)", marginTop: 4 }}>
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

        {activeView === "fanout_monitor" && (
          <FanoutMonitorPanel metrics={metrics} events={recentEvents} />
        )}

        {activeView === "satellite" && (
          <BoundarySatellitePanel events={recentEvents} metrics={metrics} />
        )}
      </div>

      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 12,
          paddingTop: 10,
          borderTop: "1px solid var(--border-soft)",
          fontSize: 10,
          color: "var(--text-tertiary)",
          flexWrap: "wrap",
        }}
      >
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
