"use client";

import React, { useMemo } from "react";
import type {
  IntentVerifierDashboardProps,
  IntentVerifierDashboardState,
  MismatchCardProps,
  MismatchDetailPanelProps,
  MismatchSeverityBadgeProps,
  MismatchTypeIconProps,
  AgentMismatchChartProps,
  MismatchSummary,
  MismatchType,
  MismatchSeverity,
  IntentVerifierDashboardViewMode,
} from "@/lib/intent-verifier-contract";
import {
  MISMATCH_TYPE_LABEL,
  MISMATCH_TYPE_ICON,
  MISMATCH_SEVERITY_LABEL,
  describeMismatchType,
  describeDeclaredTarget,
  describeObservedBehavior,
  formatTimestampNs,
  countByMismatchType,
  countByMismatchSeverity,
  aggregateMismatchByPid,
  aggregateMismatchByTool,
  maxMismatchSeverity,
  countCritical,
} from "@/lib/intent-verifier-contract";

// ═══════════════════════════════════════════════════════════════════════════
// 常量
// ═══════════════════════════════════════════════════════════════════════════

const VIEW_LABELS: { mode: IntentVerifierDashboardViewMode; label: string }[] = [
  { mode: "overview", label: "概览" },
  { mode: "timeline", label: "时间线" },
  { mode: "agents", label: "Agent 视图" },
];

const SEVERITY_RING: Record<MismatchSeverity, string> = {
  Info: "ring-blue-300",
  Warning: "ring-amber-400",
  Critical: "ring-red-500",
};

const SEVERITY_BG: Record<MismatchSeverity, string> = {
  Info: "bg-blue-50 border-blue-200",
  Warning: "bg-amber-50 border-amber-200",
  Critical: "bg-red-50 border-red-200",
};

const SEVERITY_DOT: Record<MismatchSeverity, string> = {
  Info: "bg-blue-400",
  Warning: "bg-amber-400",
  Critical: "bg-red-500 animate-pulse",
};

const SEVERITY_BORDER: Record<MismatchSeverity, string> = {
  Info: "border-l-blue-300",
  Warning: "border-l-amber-400",
  Critical: "border-l-red-500",
};

const MISMATCH_TYPE_COLOR: Record<MismatchType, string> = {
  ToolMismatch: "text-slate-600 bg-slate-50",
  FilePathEscalation: "text-red-600 bg-red-50",
  NetworkDeviation: "text-purple-600 bg-purple-50",
  CommandDeviation: "text-orange-600 bg-orange-50",
  UndeclaredBehavior: "text-pink-600 bg-pink-50",
  DeclaredButNotExecuted: "text-gray-500 bg-gray-50",
};

// ═══════════════════════════════════════════════════════════════════════════
// 子组件: MismatchSeverityBadge — 严重度徽章
// ═══════════════════════════════════════════════════════════════════════════

export const MismatchSeverityBadge: React.FC<MismatchSeverityBadgeProps> = ({
  severity,
  showLabel = true,
}) => {
  const colors: Record<MismatchSeverity, string> = {
    Info: "bg-blue-100 text-blue-800",
    Warning: "bg-amber-100 text-amber-800",
    Critical: "bg-red-100 text-red-800",
  };

  return (
    <span
      className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-xs font-medium ${colors[severity]}`}
    >
      <span className={`inline-block w-1.5 h-1.5 rounded-full ${SEVERITY_DOT[severity]}`} />
      {showLabel && MISMATCH_SEVERITY_LABEL[severity]}
    </span>
  );
};

// ═══════════════════════════════════════════════════════════════════════════
// 子组件: MismatchTypeIcon — 不匹配类型图标
// ═══════════════════════════════════════════════════════════════════════════

export const MismatchTypeIcon: React.FC<MismatchTypeIconProps> = ({
  mismatch_type,
  size = "md",
}) => {
  const sizeClass = size === "sm" ? "text-sm" : size === "lg" ? "text-xl" : "text-base";
  return (
    <span className={sizeClass} title={MISMATCH_TYPE_LABEL[mismatch_type]}>
      {MISMATCH_TYPE_ICON[mismatch_type]}
    </span>
  );
};

// ═══════════════════════════════════════════════════════════════════════════
// 子组件: MismatchCard — 单条不匹配卡片
// ═══════════════════════════════════════════════════════════════════════════

export const MismatchCard: React.FC<MismatchCardProps> = ({
  mismatch,
  selected,
  onSelect,
  onAcknowledge,
}) => {
  const isCritical = mismatch.severity === "Critical";

  return (
    <button
      onClick={onSelect}
      className={`w-full text-left p-3 rounded border transition-all cursor-pointer
        border-l-4 ${SEVERITY_BORDER[mismatch.severity]}
        ${selected ? "ring-2 ring-blue-400 bg-blue-50" : "bg-white hover:bg-slate-50"}
      `}
    >
      {/* 顶部行：类型图标 + 工具名 + 严重度 */}
      <div className="flex items-center gap-2 mb-1.5">
        <MismatchTypeIcon mismatch_type={mismatch.mismatch_type} size="sm" />
        <span className="font-mono font-semibold text-sm truncate">
          {mismatch.tool_name}
        </span>
        <MismatchSeverityBadge severity={mismatch.severity} />
        {mismatch.acknowledged ? (
          <span className="text-xs text-green-600 font-medium ml-auto flex-shrink-0">
            ✓ 已确认
          </span>
        ) : (
          <span className="text-xs text-red-500 font-medium ml-auto flex-shrink-0">
            ● 新
          </span>
        )}
      </div>

      {/* 描述 */}
      <div className="text-xs text-gray-600 mb-1.5 line-clamp-2">
        {mismatch.description}
      </div>

      {/* 声明 vs 观测 对比行 */}
      <div className="text-xs space-y-0.5 mb-2">
        <div className="flex gap-1.5">
          <span className="text-gray-400 flex-shrink-0">声称:</span>
          <span className="text-gray-700 truncate font-mono">
            {describeDeclaredTarget(mismatch.declared)}
          </span>
        </div>
        <div className="flex gap-1.5">
          <span className="text-gray-400 flex-shrink-0">观测:</span>
          <span className={`truncate font-mono ${isCritical ? "text-red-600" : "text-gray-700"}`}>
            {describeObservedBehavior(mismatch.observed)}
          </span>
        </div>
      </div>

      {/* 底部行：时间 + PID */}
      <div className="flex items-center gap-3 text-xs text-gray-400">
        <span>⏱ {formatTimestampNs(mismatch.detected_at_ns)}</span>
        <span className="font-mono">PID {mismatch.agent_pid}</span>
        {mismatch.sensitive_path_match && (
          <span className="text-red-400 truncate ml-auto" title={mismatch.sensitive_path_match}>
            ⚠ {mismatch.sensitive_path_match.split("/").pop()}
          </span>
        )}
        {!mismatch.acknowledged && onAcknowledge && (
          <button
            onClick={(e) => {
              e.stopPropagation();
              onAcknowledge();
            }}
            className="ml-auto text-blue-500 hover:text-blue-700 cursor-pointer flex-shrink-0"
          >
            确认
          </button>
        )}
      </div>
    </button>
  );
};

// ═══════════════════════════════════════════════════════════════════════════
// 子组件: MismatchDetailPanel — 不匹配详情面板
// ═══════════════════════════════════════════════════════════════════════════

export const MismatchDetailPanel: React.FC<MismatchDetailPanelProps> = ({
  mismatch,
  onToolNameClick,
  onAgentPidClick,
  onAcknowledge,
}) => {
  const isCritical = mismatch.severity === "Critical";

  return (
    <div className={`p-4 rounded-lg border ${SEVERITY_BG[mismatch.severity]} space-y-4`}>
      {/* 标题行 */}
      <div className="flex items-center gap-2">
        <MismatchTypeIcon mismatch_type={mismatch.mismatch_type} size="lg" />
        <div>
          <h3 className="font-semibold text-sm">
            {MISMATCH_TYPE_LABEL[mismatch.mismatch_type]}
          </h3>
          <div className="flex items-center gap-2">
            <button
              onClick={() => onToolNameClick?.(mismatch.tool_name)}
              className="font-mono text-sm text-blue-600 hover:underline cursor-pointer"
            >
              {mismatch.tool_name}
            </button>
            <span className="text-gray-400 text-xs">·</span>
            <button
              onClick={() => onAgentPidClick?.(mismatch.agent_pid)}
              className="font-mono text-xs text-blue-600 hover:underline cursor-pointer"
            >
              PID {mismatch.agent_pid}
            </button>
          </div>
        </div>
        <div className="ml-auto flex items-center gap-2">
          <MismatchSeverityBadge severity={mismatch.severity} />
          {mismatch.acknowledged ? (
            <span className="text-xs text-green-700 bg-green-100 px-1.5 py-0.5 rounded font-medium">
              已确认
            </span>
          ) : (
            onAcknowledge && (
              <button
                onClick={onAcknowledge}
                className="text-xs text-blue-600 bg-blue-100 px-1.5 py-0.5 rounded font-medium hover:bg-blue-200 cursor-pointer"
              >
                标记确认
              </button>
            )
          )}
        </div>
      </div>

      {/* 描述 */}
      <div className="text-sm text-gray-700 p-3 bg-white/60 rounded border border-slate-100">
        {mismatch.description}
      </div>

      {/* 声明 vs 观测 对比 */}
      <div className="grid grid-cols-2 gap-3">
        {/* 声明 */}
        <div className="p-3 bg-white/80 rounded border border-slate-100">
          <h4 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">
            📋 Agent 声称
          </h4>
          <div className="space-y-1.5 text-xs font-mono">
            <div>
              <span className="text-gray-400">工具: </span>
              <span className="text-gray-700">{mismatch.declared.tool_name}</span>
            </div>
            {mismatch.declared.target.type === "FilePath" && (
              <>
                <div>
                  <span className="text-gray-400">路径: </span>
                  <span className="text-gray-700">{mismatch.declared.target.path}</span>
                </div>
                <div>
                  <span className="text-gray-400">操作: </span>
                  <span className="text-gray-700">{mismatch.declared.target.operation}</span>
                </div>
              </>
            )}
            {mismatch.declared.target.type === "NetworkTarget" && (
              <>
                <div>
                  <span className="text-gray-400">目标: </span>
                  <span className="text-gray-700">
                    {mismatch.declared.target.host}
                    {mismatch.declared.target.port ? `:${mismatch.declared.target.port}` : ""}
                  </span>
                </div>
                <div>
                  <span className="text-gray-400">协议: </span>
                  <span className="text-gray-700">{mismatch.declared.target.scheme}</span>
                </div>
              </>
            )}
            {mismatch.declared.target.type === "Command" && (
              <div>
                <span className="text-gray-400">命令: </span>
                <span className="text-gray-700">
                  {[mismatch.declared.target.binary, ...mismatch.declared.target.args_prefix].join(" ")}
                </span>
              </div>
            )}
            {mismatch.declared.raw_args && (
              <div className="mt-2">
                <span className="text-gray-400">原始参数: </span>
                <pre className="text-gray-500 mt-0.5 text-xs whitespace-pre-wrap break-all">
                  {JSON.stringify(mismatch.declared.raw_args, null, 2)}
                </pre>
              </div>
            )}
          </div>
        </div>

        {/* 观测 */}
        <div className={`p-3 rounded border ${
          isCritical ? "bg-red-50/80 border-red-200" : "bg-white/80 border-slate-100"
        }`}>
          <h4 className={`text-xs font-semibold uppercase tracking-wide mb-2 ${
            isCritical ? "text-red-600" : "text-gray-500"
          }`}>
            🔍 eBPF 实际观测
          </h4>
          <div className="space-y-1.5 text-xs font-mono">
            <div>
              <span className="text-gray-400">来源: </span>
              <span className="text-gray-700">{mismatch.observed.source}</span>
            </div>
            {mismatch.observed.path && (
              <div>
                <span className="text-gray-400">路径: </span>
                <span className={isCritical ? "text-red-600 font-semibold" : "text-gray-700"}>
                  {mismatch.observed.path}
                </span>
              </div>
            )}
            {mismatch.observed.host && (
              <div>
                <span className="text-gray-400">主机: </span>
                <span className={isCritical ? "text-red-600 font-semibold" : "text-gray-700"}>
                  {mismatch.observed.host}
                  {mismatch.observed.port ? `:${mismatch.observed.port}` : ""}
                </span>
              </div>
            )}
            {(mismatch.observed.command || mismatch.observed.binary) && (
              <div>
                <span className="text-gray-400">命令: </span>
                <span className={isCritical ? "text-red-600 font-semibold" : "text-gray-700"}>
                  {mismatch.observed.command ?? mismatch.observed.binary}
                </span>
              </div>
            )}
            {mismatch.observed.operation && (
              <div>
                <span className="text-gray-400">操作: </span>
                <span className="text-gray-700">{mismatch.observed.operation}</span>
              </div>
            )}
            {mismatch.sensitive_path_match && (
              <div className="mt-2 p-1.5 bg-red-100 rounded text-red-700">
                <span className="text-red-500">⚠ 敏感路径: </span>
                {mismatch.sensitive_path_match}
              </div>
            )}
            {mismatch.observed.extra && (
              <div className="mt-2">
                <span className="text-gray-400">额外上下文: </span>
                <pre className="text-gray-500 mt-0.5 text-xs whitespace-pre-wrap break-all">
                  {JSON.stringify(mismatch.observed.extra, null, 2)}
                </pre>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* 时间信息 */}
      <div className="text-xs text-gray-400">
        检测时间: {new Date(mismatch.detected_at_ns / 1_000_000).toLocaleString()}
      </div>
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════════
// 子组件: AgentMismatchChart — Agent 不匹配频率图（纯 CSS 条形图）
// ═══════════════════════════════════════════════════════════════════════════

export const AgentMismatchChart: React.FC<AgentMismatchChartProps> = ({
  frequency,
  selected_pid,
  onPidSelect,
}) => {
  const entries = Object.entries(frequency).sort((a, b) => b[1] - a[1]);
  const maxCount = entries[0]?.[1] ?? 1;

  if (entries.length === 0) {
    return <div className="text-sm text-gray-400 p-4">暂无 Agent 数据</div>;
  }

  return (
    <div className="space-y-2">
      <h4 className="text-xs font-semibold text-gray-500 uppercase tracking-wide">
        Agent 不匹配排行
      </h4>
      {entries.map(([pidStr, count]) => {
        const pid = Number(pidStr);
        const isSelected = selected_pid === pid;
        return (
          <button
            key={pidStr}
            onClick={() => onPidSelect(isSelected ? null : pid)}
            className="w-full text-left group"
          >
            <div className="flex items-center justify-between text-xs mb-1">
              <span
                className={`font-mono truncate ${
                  isSelected ? "text-blue-600 font-semibold" : "text-gray-700"
                }`}
              >
                PID {pidStr}
              </span>
              <span className="text-gray-400 ml-2 flex-shrink-0">{count} 次</span>
            </div>
            <div className="h-1.5 bg-slate-100 rounded-full overflow-hidden">
              <div
                className={`h-full rounded-full transition-all ${
                  isSelected ? "bg-blue-500" : "bg-slate-400"
                }`}
                style={{ width: `${(count / maxCount) * 100}%` }}
              />
            </div>
          </button>
        );
      })}
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════════
// 子组件: MismatchTypeDistribution — 不匹配类型分布图
// ═══════════════════════════════════════════════════════════════════════════

const MismatchTypeDistribution: React.FC<{
  counts: Record<MismatchType, number>;
  total: number;
}> = ({ counts, total }) => {
  const entries = (Object.entries(counts) as [MismatchType, number][])
    .filter(([, c]) => c > 0)
    .sort((a, b) => b[1] - a[1]);

  if (entries.length === 0) {
    return <div className="text-sm text-gray-400">暂无数据</div>;
  }

  return (
    <div className="space-y-2">
      {entries.map(([type, count]) => (
        <div key={type} className="flex items-center gap-2 text-xs">
          <MismatchTypeIcon mismatch_type={type} size="sm" />
          <span className="text-gray-600 flex-1">{MISMATCH_TYPE_LABEL[type]}</span>
          <span className="font-mono font-semibold">{count}</span>
          <div className="w-20 h-1.5 bg-slate-100 rounded-full overflow-hidden">
            <div
              className="h-full bg-slate-400 rounded-full"
              style={{ width: `${total > 0 ? (count / total) * 100 : 0}%` }}
            />
          </div>
        </div>
      ))}
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════════
// 子组件: SummaryCard — 统计摘要卡片
// ═══════════════════════════════════════════════════════════════════════════

const SummaryCard: React.FC<{
  summary: NonNullable<IntentVerifierDashboardState["latest_summary"]>;
}> = ({ summary }) => {
  const matchRate =
    summary.total_intents_declared > 0
      ? Math.round((summary.matched / summary.total_intents_declared) * 100)
      : 0;

  const hasIssues = summary.mismatched > 0;

  return (
    <div className={`grid grid-cols-2 md:grid-cols-4 gap-2 text-xs`}>
      <div className="p-2 bg-white/60 rounded">
        <div className="text-gray-500">声明意图</div>
        <div className="font-mono font-semibold text-lg">{summary.total_intents_declared}</div>
      </div>
      <div className="p-2 bg-white/60 rounded">
        <div className="text-gray-500">匹配成功</div>
        <div className="font-mono font-semibold text-lg text-green-600">{summary.matched}</div>
        <div className="text-gray-400">{matchRate}%</div>
      </div>
      <div className={`p-2 rounded ${hasIssues ? "bg-red-50" : "bg-white/60"}`}>
        <div className="text-gray-500">不匹配</div>
        <div className={`font-mono font-semibold text-lg ${hasIssues ? "text-red-600" : "text-gray-700"}`}>
          {summary.mismatched}
        </div>
      </div>
      <div className="p-2 bg-white/60 rounded">
        <div className="text-gray-500">过期未决</div>
        <div className="font-mono font-semibold text-lg text-amber-600">
          {summary.expired_unresolved}
        </div>
      </div>
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════════
// 主组件: IntentVerifierDashboard
// ═══════════════════════════════════════════════════════════════════════════

export const IntentVerifierDashboard: React.FC<IntentVerifierDashboardProps> = ({
  state,
  onViewModeChange,
  onMismatchSelect,
  onTypeFilterChange,
  onSeverityFilterChange,
  onToolFilterChange,
  onPidFilterChange,
  onShowUnacknowledgedOnlyChange,
  onAcknowledgeMismatch,
  compact = false,
}) => {
  const {
    mismatches,
    latest_summary,
    view_mode,
    selected_mismatch_id,
    type_filter,
    severity_filter,
    tool_filter,
    pid_filter,
    show_unacknowledged_only,
  } = state;

  // ── 过滤后的不匹配 ──
  const filteredMismatches = useMemo(() => {
    let list = [...mismatches];
    if (type_filter) list = list.filter((m) => m.mismatch_type === type_filter);
    if (severity_filter) list = list.filter((m) => m.severity === severity_filter);
    if (tool_filter) list = list.filter((m) => m.tool_name === tool_filter);
    if (pid_filter != null) list = list.filter((m) => m.agent_pid === pid_filter);
    if (show_unacknowledged_only) list = list.filter((m) => !m.acknowledged);
    // Critical 优先排序
    return list.sort((a, b) => {
      const sev = { Critical: 0, Warning: 1, Info: 2 };
      return sev[a.severity] - sev[b.severity] || b.detected_at_ns - a.detected_at_ns;
    });
  }, [mismatches, type_filter, severity_filter, tool_filter, pid_filter, show_unacknowledged_only]);

  // ── 选中的不匹配 ──
  const selectedMismatch = useMemo(
    () =>
      selected_mismatch_id
        ? filteredMismatches.find((m) => m.intent_id === selected_mismatch_id) ?? null
        : null,
    [filteredMismatches, selected_mismatch_id],
  );

  // ── 统计数据 ──
  const typeCounts = useMemo(() => countByMismatchType(filteredMismatches), [filteredMismatches]);
  const severityCounts = useMemo(() => countByMismatchSeverity(filteredMismatches), [filteredMismatches]);
  const overallSeverity = useMemo(() => maxMismatchSeverity(mismatches), [mismatches]);
  const criticalCount = useMemo(() => countCritical(mismatches), [mismatches]);
  const pidFrequency = useMemo(() => aggregateMismatchByPid(filteredMismatches), [filteredMismatches]);
  const toolFrequency = useMemo(() => aggregateMismatchByTool(filteredMismatches), [filteredMismatches]);

  // ── 工具名列表（用于过滤器）──
  const toolNames = useMemo(() => {
    const names = new Set<string>();
    for (const m of mismatches) names.add(m.tool_name);
    return Array.from(names).sort();
  }, [mismatches]);

  // ── PID 列表（用于过滤器）──
  const allPids = useMemo(() => {
    const pids = new Set<number>();
    for (const m of mismatches) pids.add(m.agent_pid);
    return Array.from(pids).sort((a, b) => a - b);
  }, [mismatches]);

  // ── 空状态 ──
  if (mismatches.length === 0 && !latest_summary) {
    return (
      <div className="p-6 text-center text-gray-400">
        <div className="text-2xl mb-2">🛡️</div>
        <div className="text-sm">未检测到意图-行为不一致</div>
        <div className="text-xs mt-1">
          AgentIntentVerifier 正在交叉验证声明意图与实际行为...
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* ═══ 顶部状态栏 ═══ */}
      {criticalCount > 0 && (
        <div className="flex items-center gap-3 p-3 rounded-lg border bg-red-50 border-red-200 text-sm">
          <span className="text-lg">🚨</span>
          <div className="flex-1">
            <span className="font-semibold text-red-700">
              {criticalCount} 个严重不匹配
            </span>
            <span className="text-red-500 ml-2">
              · 共 {mismatches.length} 个不匹配 · 最高: {overallSeverity && MISMATCH_SEVERITY_LABEL[overallSeverity]}
            </span>
          </div>
          {latest_summary && (
            <span className="text-xs text-gray-400">
              匹配率{" "}
              {latest_summary.total_intents_declared > 0
                ? Math.round(
                    (latest_summary.matched / latest_summary.total_intents_declared) * 100
                  )
                : 0}
              %
            </span>
          )}
        </div>
      )}

      {/* 无严重项时的温和告警 */}
      {criticalCount === 0 && mismatches.length > 0 && overallSeverity && (
        <div
          className={`flex items-center gap-3 p-3 rounded-lg border text-sm ${
            SEVERITY_BG[overallSeverity]
          }`}
        >
          <span className="text-lg">⚠️</span>
          <div className="flex-1">
            <span className="font-semibold">
              {mismatches.length} 个不匹配
            </span>
            <span className="text-gray-500 ml-2">
              · 最高严重度: {MISMATCH_SEVERITY_LABEL[overallSeverity]}
            </span>
          </div>
        </div>
      )}

      {/* ═══ 顶部工具栏 ═══ */}
      <div className="flex items-center gap-3 flex-wrap">
        {/* 视图切换 */}
        <div className="flex rounded-lg bg-slate-100 p-0.5">
          {VIEW_LABELS.map(({ mode, label }) => (
            <button
              key={mode}
              onClick={() => onViewModeChange(mode)}
              className={`px-3 py-1.5 text-xs font-medium rounded-md transition-colors ${
                view_mode === mode
                  ? "bg-white text-slate-900 shadow-sm"
                  : "text-slate-500 hover:text-slate-700"
              }`}
            >
              {label}
            </button>
          ))}
        </div>

        {/* 未确认过滤 */}
        <label className="flex items-center gap-1.5 text-xs text-gray-600 cursor-pointer">
          <input
            type="checkbox"
            checked={show_unacknowledged_only}
            onChange={(e) => onShowUnacknowledgedOnlyChange(e.target.checked)}
            className="rounded border-slate-300"
          />
          仅未确认
        </label>

        {/* 不匹配类型过滤 */}
        <select
          className="text-xs border border-slate-300 rounded px-2 py-1.5 bg-white"
          value={type_filter ?? ""}
          onChange={(e) => {
            const v = e.target.value;
            onTypeFilterChange(v ? (v as MismatchType) : null);
          }}
        >
          <option value="">全部类型</option>
          {Object.entries(MISMATCH_TYPE_LABEL).map(([k, v]) => (
            <option key={k} value={k}>
              {MISMATCH_TYPE_ICON[k as MismatchType]} {v}
            </option>
          ))}
        </select>

        {/* 严重度过滤 */}
        <select
          className="text-xs border border-slate-300 rounded px-2 py-1.5 bg-white"
          value={severity_filter ?? ""}
          onChange={(e) => {
            const v = e.target.value;
            onSeverityFilterChange(v ? (v as MismatchSeverity) : null);
          }}
        >
          <option value="">全部严重度</option>
          {Object.entries(MISMATCH_SEVERITY_LABEL).map(([k, v]) => (
            <option key={k} value={k}>
              {v}
            </option>
          ))}
        </select>

        {/* 工具名过滤 */}
        {toolNames.length > 0 && (
          <select
            className="text-xs border border-slate-300 rounded px-2 py-1.5 bg-white max-w-[160px]"
            value={tool_filter ?? ""}
            onChange={(e) => {
              const v = e.target.value;
              onToolFilterChange(v || null);
            }}
          >
            <option value="">全部工具</option>
            {toolNames.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
        )}

        {/* PID 过滤 */}
        {allPids.length > 0 && (
          <select
            className="text-xs border border-slate-300 rounded px-2 py-1.5 bg-white"
            value={pid_filter ?? ""}
            onChange={(e) => {
              const v = e.target.value;
              onPidFilterChange(v ? Number(v) : null);
            }}
          >
            <option value="">全部 Agent</option>
            {allPids.map((pid) => (
              <option key={pid} value={pid}>
                PID {pid}
              </option>
            ))}
          </select>
        )}
      </div>

      {/* ═══ 概览视图 ═══ */}
      {view_mode === "overview" && (
        <div
          className={`grid gap-4 ${
            compact ? "grid-cols-1" : "grid-cols-1 md:grid-cols-2"
          }`}
        >
          {/* 统计摘要 */}
          {latest_summary && (
            <div className={`p-4 rounded-lg border border-slate-200 bg-white ${compact ? "" : "md:col-span-2"}`}>
              <h4 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-3">
                📊 会话统计
              </h4>
              <SummaryCard summary={latest_summary} />
            </div>
          )}

          {/* 严重度分布 */}
          <div className="p-4 rounded-lg border border-slate-200 bg-white">
            <h4 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-3">
              严重度分布
            </h4>
            <div className="space-y-2">
              {(["Critical", "Warning", "Info"] as MismatchSeverity[]).map((sev) => {
                const count = severityCounts[sev];
                const total = filteredMismatches.length;
                return (
                  <div key={sev} className="flex items-center gap-2 text-xs">
                    <MismatchSeverityBadge severity={sev} showLabel={true} />
                    <span className="font-mono font-semibold">{count}</span>
                    <div className="flex-1 h-1.5 bg-slate-100 rounded-full overflow-hidden">
                      <div
                        className={`h-full rounded-full ${sev === "Critical" ? "bg-red-400" : sev === "Warning" ? "bg-amber-400" : "bg-blue-400"}`}
                        style={{ width: `${total > 0 ? (count / total) * 100 : 0}%` }}
                      />
                    </div>
                  </div>
                );
              })}
            </div>
          </div>

          {/* 类型分布 */}
          <div className="p-4 rounded-lg border border-slate-200 bg-white">
            <h4 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-3">
              不匹配类型分布
            </h4>
            <MismatchTypeDistribution counts={typeCounts} total={filteredMismatches.length} />
          </div>

          {/* 选中的不匹配详情 */}
          {selectedMismatch && (
            <div className={compact ? "" : "md:col-span-2"}>
              <MismatchDetailPanel
                mismatch={selectedMismatch}
                onToolNameClick={(name) => onToolFilterChange(name)}
                onAgentPidClick={(pid) => onPidFilterChange(pid)}
                onAcknowledge={
                  !selectedMismatch.acknowledged && onAcknowledgeMismatch
                    ? () => onAcknowledgeMismatch(selectedMismatch.intent_id)
                    : undefined
                }
              />
            </div>
          )}
        </div>
      )}

      {/* ═══ 时间线视图 ═══ */}
      {view_mode === "timeline" && (
        <div
          className={`grid gap-4 ${
            compact || !selectedMismatch ? "grid-cols-1" : "grid-cols-1 md:grid-cols-2"
          }`}
        >
          <div className="space-y-2 max-h-[60vh] overflow-y-auto">
            {filteredMismatches.length === 0 ? (
              <div className="text-sm text-gray-400 p-4">暂无匹配的记录</div>
            ) : (
              filteredMismatches.map((mismatch) => (
                <MismatchCard
                  key={mismatch.intent_id}
                  mismatch={mismatch}
                  selected={mismatch.intent_id === selected_mismatch_id}
                  onSelect={() => onMismatchSelect(mismatch)}
                  onAcknowledge={
                    !mismatch.acknowledged && onAcknowledgeMismatch
                      ? () => onAcknowledgeMismatch(mismatch.intent_id)
                      : undefined
                  }
                />
              ))
            )}
          </div>
          {selectedMismatch && !compact && (
            <MismatchDetailPanel
              mismatch={selectedMismatch}
              onToolNameClick={(name) => onToolFilterChange(name)}
              onAgentPidClick={(pid) => onPidFilterChange(pid)}
              onAcknowledge={
                !selectedMismatch.acknowledged && onAcknowledgeMismatch
                  ? () => onAcknowledgeMismatch(selectedMismatch.intent_id)
                  : undefined
              }
            />
          )}
        </div>
      )}

      {/* ═══ Agent 视图 ═══ */}
      {view_mode === "agents" && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div className="p-4 rounded-lg border border-slate-200 bg-white">
            <AgentMismatchChart
              frequency={pidFrequency}
              selected_pid={pid_filter}
              onPidSelect={onPidFilterChange}
            />
          </div>
          <div className="p-4 rounded-lg border border-slate-200 bg-white">
            <h4 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-3">
              工具不匹配频率
            </h4>
            {Object.entries(toolFrequency).length === 0 ? (
              <div className="text-sm text-gray-400">暂无数据</div>
            ) : (
              <div className="space-y-2">
                {Object.entries(toolFrequency)
                  .sort((a, b) => b[1] - a[1])
                  .slice(0, 8)
                  .map(([name, count]) => (
                    <div key={name} className="flex items-center gap-2 text-xs">
                      <span
                        className={`font-mono flex-1 truncate ${
                          tool_filter === name ? "text-blue-600 font-semibold" : "text-gray-700"
                        }`}
                      >
                        {name}
                      </span>
                      <span className="font-mono font-semibold">{count}</span>
                    </div>
                  ))}
              </div>
            )}
          </div>
          {selectedMismatch && (
            <div className={compact ? "" : "md:col-span-2"}>
              <MismatchDetailPanel
                mismatch={selectedMismatch}
                onToolNameClick={(name) => onToolFilterChange(name)}
                onAgentPidClick={(pid) => onPidFilterChange(pid)}
                onAcknowledge={
                  !selectedMismatch.acknowledged && onAcknowledgeMismatch
                    ? () => onAcknowledgeMismatch(selectedMismatch.intent_id)
                    : undefined
                }
              />
            </div>
          )}
        </div>
      )}

      {/* ═══ 底部状态栏 ═══ */}
      <div className="text-xs text-gray-400 flex items-center justify-between pt-2 border-t border-slate-100">
        <span>
          共 {mismatches.length} 个不匹配 · Critical {criticalCount} 个
          {latest_summary &&
            ` · 意图匹配率 ${
              latest_summary.total_intents_declared > 0
                ? Math.round((latest_summary.matched / latest_summary.total_intents_declared) * 100)
                : 0
            }%`}
        </span>
        <span>
          显示 {filteredMismatches.length} 条
          {(type_filter || severity_filter || tool_filter || pid_filter != null || show_unacknowledged_only) &&
            " (已过滤)"}
        </span>
      </div>
    </div>
  );
};
