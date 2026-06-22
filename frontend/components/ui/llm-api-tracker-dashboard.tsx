"use client";

import React, { useMemo } from "react";
import type {
  LlmApiTrackerDashboardProps,
  LlmApiTrackerDashboardState,
  LlmTrackerDashboardViewMode,
  LlmProvider,
  LlmCallStatus,
  LlmCallRecord,
  TrackerSummaryCardProps,
  LlmCallCardProps,
  LlmCallDetailProps,
  ProviderDistributionProps,
  LlmTrackerFilterBarProps,
} from "@/lib/llm-api-tracker-contract";
import {
  LLM_PROVIDER_LABEL,
  LLM_PROVIDER_COLOR,
  LLM_PROVIDER_DOT,
  LLM_CALL_STATUS_LABEL,
  LLM_CALL_STATUS_ICON,
  LLM_CALL_STATUS_COLOR,
  formatLatency,
  formatTokens,
} from "@/lib/llm-api-tracker-contract";

// ═══════════════════════════════════════════════════════════════════════════════
// 常量
// ═══════════════════════════════════════════════════════════════════════════════

const VIEW_LABELS: { mode: LlmTrackerDashboardViewMode; label: string; icon: string }[] = [
  { mode: "overview", label: "概览", icon: "📊" },
  { mode: "calls", label: "调用列表", icon: "📋" },
  { mode: "by_provider", label: "Provider", icon: "🔌" },
];

const ALL_STATUSES: LlmCallStatus[] = ["complete", "orphaned", "error"];

// ═══════════════════════════════════════════════════════════════════════════════
// 子组件: SummaryCard — 统计卡片
// ═══════════════════════════════════════════════════════════════════════════════

const SummaryCard: React.FC<TrackerSummaryCardProps> = ({
  label,
  value,
  unit = "",
  highlight = false,
  icon,
  onClick,
}) => (
  <div
    onClick={onClick}
    className={`p-3 rounded-lg border transition-all ${
      highlight
        ? "border-red-300 bg-red-50"
        : "border-slate-200 bg-white"
    } ${onClick ? "cursor-pointer hover:shadow-sm" : ""}`}
  >
    <div className="flex items-center gap-1.5 mb-1">
      {icon && <span className="text-sm">{icon}</span>}
      <span className="text-xs text-gray-500">{label}</span>
    </div>
    <div className="font-mono text-lg font-semibold">
      {typeof value === "number" ? value.toLocaleString() : value}
      {unit && <span className="text-xs text-gray-400 font-normal ml-1">{unit}</span>}
    </div>
  </div>
);

// ═══════════════════════════════════════════════════════════════════════════════
// 子组件: ProviderBadge — Provider 标识徽章
// ═══════════════════════════════════════════════════════════════════════════════

const ProviderBadge: React.FC<{ provider: LlmProvider }> = ({ provider }) => (
  <span
    className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium border ${LLM_PROVIDER_COLOR[provider]}`}
  >
    <span className={`inline-block w-1.5 h-1.5 rounded-full ${LLM_PROVIDER_DOT[provider]}`} />
    {LLM_PROVIDER_LABEL[provider]}
  </span>
);

// ═══════════════════════════════════════════════════════════════════════════════
// 子组件: StatusBadge — 调用状态徽章
// ═══════════════════════════════════════════════════════════════════════════════

const StatusBadge: React.FC<{ status: LlmCallStatus }> = ({ status }) => (
  <span
    className={`inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium border ${LLM_CALL_STATUS_COLOR[status]}`}
  >
    {LLM_CALL_STATUS_ICON[status]} {LLM_CALL_STATUS_LABEL[status]}
  </span>
);

// ═══════════════════════════════════════════════════════════════════════════════
// 子组件: LlmCallCard — 单条 LLM 调用记录卡片
// ═══════════════════════════════════════════════════════════════════════════════

const LlmCallCard: React.FC<LlmCallCardProps> = ({
  record,
  isSelected,
  onSelect,
  onNavigateToTimeline,
  compact = false,
}) => {
  const requestTime = new Date(record.request_timestamp_ns / 1_000_000).toLocaleTimeString();

  return (
    <div
      onClick={onSelect}
      className={`p-3 rounded-lg border border-l-4 cursor-pointer transition-all ${
        record.status === "error"
          ? "border-l-red-500"
          : record.status === "orphaned"
            ? "border-l-amber-500"
            : "border-l-emerald-500"
      } ${
        isSelected
          ? "ring-2 ring-blue-400 shadow-sm bg-blue-50/50"
          : "bg-white hover:shadow-sm"
      }`}
    >
      {/* ── 头部: Provider + Model + 状态 ── */}
      <div className="flex items-center justify-between mb-1.5">
        <div className="flex items-center gap-2 min-w-0">
          <ProviderBadge provider={record.provider} />
          {record.model && (
            <span className="text-xs font-mono text-gray-600 truncate max-w-[120px]">
              {record.model}
            </span>
          )}
        </div>
        <div className="flex items-center gap-1.5 flex-shrink-0">
          {record.is_streaming && (
            <span className="text-[10px] bg-cyan-50 text-cyan-600 border border-cyan-200 rounded px-1">
              SSE
            </span>
          )}
          <StatusBadge status={record.status} />
        </div>
      </div>

      {/* ── 关键指标行 ── */}
      <div className="grid grid-cols-3 gap-2 mb-1.5">
        <div>
          <div className="text-[10px] text-gray-400">延迟</div>
          <div className={`text-xs font-mono font-semibold ${record.status === "error" ? "text-red-600" : "text-gray-700"}`}>
            {record.latency_formatted}
          </div>
        </div>
        <div>
          <div className="text-[10px] text-gray-400">Tokens</div>
          <div className="text-xs font-mono text-gray-700">
            {record.total_tokens !== null ? formatTokens(record.total_tokens) : "—"}
          </div>
        </div>
        <div>
          <div className="text-[10px] text-gray-400">HTTP</div>
          <div className={`text-xs font-mono ${
            record.status_code && record.status_code >= 400 ? "text-red-600" : "text-gray-700"
          }`}>
            {record.status_code ?? "—"}
          </div>
        </div>
      </div>

      {/* ── 底部信息行 ── */}
      <div className="flex items-center justify-between pt-1.5 border-t border-slate-100 text-[10px] text-gray-400">
        <span className="font-mono">
          pid:{record.pid} tid:{record.tid}
        </span>
        <span>{requestTime}</span>
      </div>

      {/* ── 操作按钮（选中状态） ── */}
      {isSelected && (
        <div className="mt-2 pt-1.5 border-t border-slate-200/50">
          <button
            onClick={(e) => {
              e.stopPropagation();
              onNavigateToTimeline(record.request_timestamp_ns);
            }}
            className="text-[10px] px-2 py-1 rounded bg-blue-50 text-blue-700 border border-blue-200 hover:bg-blue-100 transition-colors"
          >
            ⏱ 定位时间轴
          </button>
        </div>
      )}
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════════════
// 子组件: LlmCallDetail — 调用记录详情面板
// ═══════════════════════════════════════════════════════════════════════════════

const LlmCallDetail: React.FC<LlmCallDetailProps> = ({
  record,
  onClose,
  onNavigateToTimeline,
  onNavigateToProcessTree,
  onNavigateToTokenCost,
}) => {
  const requestTime = new Date(record.request_timestamp_ns / 1_000_000).toLocaleString();
  const responseTime = record.response_timestamp_ns
    ? new Date(record.response_timestamp_ns / 1_000_000).toLocaleString()
    : "—";

  return (
    <div className="p-4 rounded-lg border border-slate-300 bg-white shadow-sm space-y-3">
      {/* 头部 */}
      <div className="flex items-center justify-between">
        <h4 className="text-sm font-semibold text-gray-800">调用详情</h4>
        <button onClick={onClose} className="text-gray-400 hover:text-gray-600 text-xs">
          ✕ 关闭
        </button>
      </div>

      {/* Record ID */}
      <div className="text-[10px] font-mono text-gray-400 truncate">
        ID: {record.record_id}
      </div>

      {/* Provider + Model */}
      <div className="flex items-center gap-2">
        <ProviderBadge provider={record.provider} />
        {record.model && (
          <span className="text-xs font-mono text-gray-700">{record.model}</span>
        )}
        {record.is_streaming && (
          <span className="text-[10px] bg-cyan-50 text-cyan-600 border border-cyan-200 rounded px-1">
            流式
          </span>
        )}
      </div>

      {/* 属性网格 */}
      <div className="grid grid-cols-2 gap-2 text-xs">
        <div>
          <div className="text-[10px] text-gray-400">请求路径</div>
          <div className="text-gray-700 font-mono truncate" title={record.request_path}>
            {record.request_path}
          </div>
        </div>
        <div>
          <div className="text-[10px] text-gray-400">HTTP 方法</div>
          <div className="text-gray-700 font-mono">{record.http_method}</div>
        </div>
        <div>
          <div className="text-[10px] text-gray-400">状态码</div>
          <div className={`font-mono ${
            record.status_code && record.status_code >= 400 ? "text-red-600" : "text-gray-700"
          }`}>
            {record.status_code ?? "—"}
          </div>
        </div>
        <div>
          <div className="text-[10px] text-gray-400">延迟</div>
          <div className="text-gray-700 font-mono">{record.latency_formatted}</div>
        </div>
        <div>
          <div className="text-[10px] text-gray-400">Prompt Tokens</div>
          <div className="text-gray-700 font-mono">
            {record.prompt_tokens !== null ? record.prompt_tokens.toLocaleString() : "—"}
          </div>
        </div>
        <div>
          <div className="text-[10px] text-gray-400">Completion Tokens</div>
          <div className="text-gray-700 font-mono">
            {record.completion_tokens !== null ? record.completion_tokens.toLocaleString() : "—"}
          </div>
        </div>
        <div>
          <div className="text-[10px] text-gray-400">Total Tokens</div>
          <div className="text-gray-700 font-mono">
            {record.total_tokens !== null ? formatTokens(record.total_tokens) : "—"}
          </div>
        </div>
        <div>
          <div className="text-[10px] text-gray-400">调用状态</div>
          <StatusBadge status={record.status} />
        </div>
        <div>
          <div className="text-[10px] text-gray-400">进程/线程</div>
          <div className="text-gray-700 font-mono">
            pid:{record.pid} · tid:{record.tid}
          </div>
        </div>
        <div className="col-span-2">
          <div className="text-[10px] text-gray-400">请求时间</div>
          <div className="text-gray-700 font-mono">{requestTime}</div>
        </div>
        {record.response_timestamp_ns && (
          <div className="col-span-2">
            <div className="text-[10px] text-gray-400">响应时间</div>
            <div className="text-gray-700 font-mono">{responseTime}</div>
          </div>
        )}
        {record.error_message && (
          <div className="col-span-2">
            <div className="text-[10px] text-gray-400">错误信息</div>
            <div className="text-red-600 text-xs font-mono break-all">{record.error_message}</div>
          </div>
        )}
      </div>

      {/* 操作按钮 */}
      <div className="flex items-center gap-2 pt-2 border-t border-slate-100">
        <button
          onClick={() => onNavigateToTimeline(record.request_timestamp_ns)}
          className="text-xs px-3 py-1.5 rounded bg-blue-50 text-blue-700 border border-blue-200 hover:bg-blue-100 transition-colors"
        >
          ⏱ 定位时间轴
        </button>
        <button
          onClick={() => onNavigateToProcessTree(record.pid)}
          className="text-xs px-3 py-1.5 rounded bg-slate-50 text-slate-700 border border-slate-200 hover:bg-slate-100 transition-colors"
        >
          🌳 进程树
        </button>
        {record.model && (
          <button
            onClick={() => onNavigateToTokenCost(record.model!)}
            className="text-xs px-3 py-1.5 rounded bg-amber-50 text-amber-700 border border-amber-200 hover:bg-amber-100 transition-colors"
          >
            💰 Token 成本
          </button>
        )}
      </div>
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════════════
// 子组件: ProviderDistBar — Provider 分布条形图
// ═══════════════════════════════════════════════════════════════════════════════

const ProviderDistBar: React.FC<{
  provider: LlmProvider;
  count: number;
  total: number;
  isSelected: boolean;
  onClick: () => void;
}> = ({ provider, count, total, isSelected, onClick }) => {
  const pct = total > 0 ? (count / total) * 100 : 0;

  return (
    <button
      onClick={onClick}
      className={`w-full flex items-center gap-2 p-2 rounded text-xs transition-colors ${
        isSelected ? "bg-blue-50 ring-1 ring-blue-200" : "hover:bg-slate-50"
      }`}
    >
      <span className={`w-2 h-2 rounded-full flex-shrink-0 ${LLM_PROVIDER_DOT[provider]}`} />
      <span className="w-20 text-left text-gray-600 flex-shrink-0">
        {LLM_PROVIDER_LABEL[provider]}
      </span>
      <span className="flex-1 font-mono text-right text-gray-500">{count}</span>
      <div className="w-16 h-1.5 bg-slate-100 rounded-full overflow-hidden flex-shrink-0">
        <div
          className={`h-full rounded-full ${LLM_PROVIDER_DOT[provider]}`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="w-10 text-right font-mono text-gray-400 text-[10px]">
        {pct.toFixed(0)}%
      </span>
    </button>
  );
};

// ═══════════════════════════════════════════════════════════════════════════════
// 子组件: FilterBar — LLM Tracker 筛选栏
// ═══════════════════════════════════════════════════════════════════════════════

const FilterBar: React.FC<LlmTrackerFilterBarProps> = ({
  provider_filter,
  model_filter,
  status_filter,
  streaming_filter,
  available_providers,
  available_models,
  onProviderFilterChange,
  onModelFilterChange,
  onStatusFilterChange,
  onStreamingFilterChange,
}) => {
  const hasActiveFilters =
    provider_filter !== null ||
    model_filter !== null ||
    status_filter !== null ||
    streaming_filter !== null;

  return (
    <div className="p-3 rounded-lg border border-slate-200 bg-white space-y-2">
      <div className="flex items-center justify-between">
        <span className="text-xs text-gray-500 font-medium">筛选</span>
        {hasActiveFilters && (
          <button
            onClick={() => {
              onProviderFilterChange(null);
              onModelFilterChange(null);
              onStatusFilterChange(null);
              onStreamingFilterChange(null);
            }}
            className="text-[10px] text-blue-500 hover:text-blue-700"
          >
            清除全部
          </button>
        )}
      </div>

      {/* Provider 筛选 */}
      <div className="flex items-center gap-1 flex-wrap">
        <span className="text-[10px] text-gray-400 flex-shrink-0">Provider:</span>
        {available_providers.map((p) => (
          <button
            key={p}
            onClick={() => onProviderFilterChange(provider_filter === p ? null : p)}
            className={`px-2 py-0.5 rounded text-[10px] border transition-colors ${
              provider_filter === p
                ? "bg-blue-600 text-white border-blue-600"
                : "border-slate-200 text-gray-500 hover:border-slate-300"
            }`}
          >
            {LLM_PROVIDER_LABEL[p]}
          </button>
        ))}
      </div>

      {/* 状态筛选 */}
      <div className="flex items-center gap-1 flex-wrap">
        <span className="text-[10px] text-gray-400 flex-shrink-0">状态:</span>
        {ALL_STATUSES.map((s) => (
          <button
            key={s}
            onClick={() => onStatusFilterChange(status_filter === s ? null : s)}
            className={`px-2 py-0.5 rounded text-[10px] border transition-colors ${
              status_filter === s
                ? "bg-blue-600 text-white border-blue-600"
                : "border-slate-200 text-gray-500 hover:border-slate-300"
            }`}
          >
            {LLM_CALL_STATUS_ICON[s]} {LLM_CALL_STATUS_LABEL[s]}
          </button>
        ))}
      </div>

      {/* 流式筛选 + 模型选择 */}
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-[10px] text-gray-400">流式:</span>
        <button
          onClick={() => onStreamingFilterChange(streaming_filter === true ? null : true)}
          className={`px-2 py-0.5 rounded text-[10px] border transition-colors ${
            streaming_filter === true
              ? "bg-blue-600 text-white border-blue-600"
              : "border-slate-200 text-gray-500 hover:border-slate-300"
          }`}
        >
          SSE 流式
        </button>
        <button
          onClick={() => onStreamingFilterChange(streaming_filter === false ? null : false)}
          className={`px-2 py-0.5 rounded text-[10px] border transition-colors ${
            streaming_filter === false
              ? "bg-blue-600 text-white border-blue-600"
              : "border-slate-200 text-gray-500 hover:border-slate-300"
          }`}
        >
          非流式
        </button>

        {available_models.length > 0 && (
          <>
            <span className="text-[10px] text-gray-400 ml-2">模型:</span>
            <select
              value={model_filter ?? ""}
              onChange={(e) => onModelFilterChange(e.target.value || null)}
              className="text-[10px] border border-slate-200 rounded px-1 py-0.5 text-gray-600"
            >
              <option value="">全部</option>
              {available_models.map((m) => (
                <option key={m} value={m}>{m}</option>
              ))}
            </select>
          </>
        )}
      </div>
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════════════
// 子组件: OverviewTab — 概览视图
// ═══════════════════════════════════════════════════════════════════════════════

const OverviewTab: React.FC<{
  state: LlmApiTrackerDashboardState;
  onRecordSelect: (record: LlmCallRecord | null) => void;
  onProviderFilterChange: (provider: LlmProvider | null) => void;
  onNavigateToTimeline: (timestamp_ns: number) => void;
  onNavigateToTokenCost: (model: string) => void;
  onNavigateToLlmApiAnomaly: (provider: string) => void;
  compact?: boolean;
}> = ({
  state,
  onRecordSelect,
  onProviderFilterChange,
  onNavigateToTimeline,
  onNavigateToTokenCost,
  onNavigateToLlmApiAnomaly,
  compact = false,
}) => {
  const { snapshot, recent_calls, selected_record_id, total_record_count } = state;
  const hasData = snapshot !== null && snapshot.total_records > 0;

  const selectedRecord = useMemo(
    () => recent_calls.find((r) => r.record_id === selected_record_id) ?? null,
    [recent_calls, selected_record_id],
  );

  return (
    <div className="space-y-4">
      {/* ── 统计卡片 ── */}
      {snapshot && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
          <SummaryCard
            label="总调用"
            value={total_record_count}
            unit="次"
          />
          <SummaryCard
            label="完成率"
            value={(snapshot.global_completion_rate * 100).toFixed(0)}
            unit="%"
          />
          <SummaryCard
            label="平均延迟"
            value={snapshot.global_avg_latency_ms.toFixed(0)}
            unit="ms"
            highlight={snapshot.global_avg_latency_ms > 5000}
          />
          <SummaryCard
            label="错误率"
            value={(snapshot.global_error_rate * 100).toFixed(1)}
            unit="%"
            highlight={snapshot.global_error_rate > 0.05}
            icon="❌"
          />
          <SummaryCard
            label="总 Token"
            value={formatTokens(snapshot.global_total_tokens)}
          />
          <SummaryCard
            label="流式占比"
            value={(snapshot.global_streaming_ratio * 100).toFixed(0)}
            unit="%"
          />
          <SummaryCard
            label="孤立率"
            value={(snapshot.global_orphan_rate * 100).toFixed(1)}
            unit="%"
            highlight={snapshot.global_orphan_rate > 0.1}
          />
          <SummaryCard
            label="Provider"
            value={Object.keys(snapshot.by_provider).length}
            unit="个"
          />
        </div>
      )}

      {/* ── 空状态 ── */}
      {!hasData && (
        <div className="p-6 text-center text-gray-400">
          <div className="text-lg mb-1">🔌</div>
          <div className="text-xs">LLMApiTracker 等待 LLM API 调用…</div>
          <div className="text-xs mt-1 text-gray-300">
            正在从 HTTP 事件中提取并配对 LLM API 请求-响应
          </div>
        </div>
      )}

      {hasData && snapshot && (
        <>
          {/* ── Provider 分布 ── */}
          {Object.keys(snapshot.by_provider).length > 0 && (
            <div className="p-4 rounded-lg border border-slate-200 bg-white">
              <h4 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-3">
                Provider 调用分布
              </h4>
              <div className="space-y-0.5">
                {Object.entries(snapshot.by_provider)
                  .sort(([, a], [, b]) => b.total_calls - a.total_calls)
                  .map(([provider, stats]) => (
                    <ProviderDistBar
                      key={provider}
                      provider={stats.provider}
                      count={stats.total_calls}
                      total={snapshot.total_records}
                      isSelected={false}
                      onClick={() => onProviderFilterChange(stats.provider)}
                    />
                  ))}
              </div>
              {/* Provider 跳转提示 */}
              <div className="mt-3 pt-2 border-t border-slate-100 flex flex-wrap gap-1.5">
                <span className="text-[10px] text-gray-400">点击跳转:</span>
                {Object.entries(snapshot.by_provider)
                  .slice(0, 4)
                  .map(([provider, stats]) => (
                    <button
                      key={provider}
                      onClick={() => onNavigateToLlmApiAnomaly(provider)}
                      className="text-[10px] px-1.5 py-0.5 rounded bg-purple-50 text-purple-600 border border-purple-200 hover:bg-purple-100"
                    >
                      {LLM_PROVIDER_LABEL[stats.provider]} 异常 →
                    </button>
                  ))}
              </div>
            </div>
          )}

          {/* ── 最近调用（含详情面板） ── */}
          {recent_calls.length > 0 && (
            <div className="p-4 rounded-lg border border-slate-200 bg-white">
              <h4 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-3">
                最近 LLM API 调用
              </h4>

              {selectedRecord && (
                <div className="mb-3">
                  <LlmCallDetail
                    record={selectedRecord}
                    onClose={() => onRecordSelect(null)}
                    onNavigateToTimeline={onNavigateToTimeline}
                    onNavigateToProcessTree={() => {}}
                    onNavigateToTokenCost={onNavigateToTokenCost}
                  />
                </div>
              )}

              <div className="space-y-2">
                {recent_calls.slice(0, 8).map((record) => (
                  <LlmCallCard
                    key={record.record_id}
                    record={record}
                    isSelected={selected_record_id === record.record_id}
                    onSelect={() =>
                      onRecordSelect(
                        selected_record_id === record.record_id ? null : record,
                      )
                    }
                    onNavigateToTimeline={onNavigateToTimeline}
                    compact={compact}
                  />
                ))}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════════════
// 子组件: ProviderTab — Provider 分析视图
// ═══════════════════════════════════════════════════════════════════════════════

const ProviderTab: React.FC<{
  state: LlmApiTrackerDashboardState;
  onProviderFilterChange: (provider: LlmProvider | null) => void;
  onNavigateToLlmApiAnomaly: (provider: string) => void;
}> = ({ state, onProviderFilterChange, onNavigateToLlmApiAnomaly }) => {
  const { snapshot } = state;
  if (!snapshot) return null;

  const providers = Object.entries(snapshot.by_provider).sort(
    ([, a], [, b]) => b.total_calls - a.total_calls,
  );

  if (providers.length === 0) {
    return (
      <div className="p-6 text-center text-gray-400">
        <div className="text-lg mb-1">🔌</div>
        <div className="text-xs">尚未检测到任何 LLM API 调用</div>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {providers.map(([provider, stats]) => (
        <div
          key={provider}
          className="p-4 rounded-lg border border-slate-200 bg-white space-y-3"
        >
          {/* Provider 头部 */}
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <span className={`w-2.5 h-2.5 rounded-full ${LLM_PROVIDER_DOT[stats.provider]}`} />
              <h4 className="text-sm font-semibold text-gray-800">
                {LLM_PROVIDER_LABEL[stats.provider]}
              </h4>
              <span className="text-[10px] font-mono text-gray-400">
                {stats.total_calls} 次调用
              </span>
            </div>
            <div className="flex items-center gap-1.5">
              <button
                onClick={() => onProviderFilterChange(stats.provider)}
                className="text-[10px] px-2 py-0.5 rounded bg-blue-50 text-blue-600 border border-blue-200 hover:bg-blue-100"
              >
                在调用列表中筛选
              </button>
              <button
                onClick={() => onNavigateToLlmApiAnomaly(provider)}
                className="text-[10px] px-2 py-0.5 rounded bg-purple-50 text-purple-600 border border-purple-200 hover:bg-purple-100"
              >
                查看异常 →
              </button>
            </div>
          </div>

          {/* 统计网格 */}
          <div className="grid grid-cols-3 gap-2 text-xs">
            <div>
              <div className="text-[10px] text-gray-400">完成/孤立/错误</div>
              <div className="font-mono text-gray-700">
                <span className="text-emerald-600">{stats.complete_calls}</span>
                {" / "}
                <span className="text-amber-600">{stats.orphaned_calls}</span>
                {" / "}
                <span className="text-red-600">{stats.error_calls}</span>
              </div>
            </div>
            <div>
              <div className="text-[10px] text-gray-400">延迟 P50/P95/P99</div>
              <div className="font-mono text-gray-700">
                {formatLatency(stats.latency_p50_ms)}{" / "}
                {formatLatency(stats.latency_p95_ms)}{" / "}
                {formatLatency(stats.latency_p99_ms)}
              </div>
            </div>
            <div>
              <div className="text-[10px] text-gray-400">流式占比</div>
              <div className="font-mono text-gray-700">
                {(stats.streaming_ratio * 100).toFixed(0)}%
              </div>
            </div>
            <div>
              <div className="text-[10px] text-gray-400">平均延迟</div>
              <div className="font-mono text-gray-700">
                {formatLatency(stats.avg_latency_ms)}
              </div>
            </div>
            <div>
              <div className="text-[10px] text-gray-400">输入 Tokens</div>
              <div className="font-mono text-gray-700">
                {formatTokens(stats.total_input_tokens)}
              </div>
            </div>
            <div>
              <div className="text-[10px] text-gray-400">输出 Tokens</div>
              <div className="font-mono text-gray-700">
                {formatTokens(stats.total_output_tokens)}
              </div>
            </div>
          </div>

          {/* 模型列表 */}
          {stats.models.length > 0 && (
            <div className="flex items-center gap-1.5 flex-wrap pt-1 border-t border-slate-100">
              <span className="text-[10px] text-gray-400">模型:</span>
              {stats.models.map((m) => (
                <span
                  key={m}
                  className="text-[10px] px-1.5 py-0.5 rounded bg-slate-50 text-slate-600 font-mono border border-slate-200"
                >
                  {m}
                </span>
              ))}
            </div>
          )}
        </div>
      ))}
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════════════
// 子组件: CallsTab — 调用列表视图
// ═══════════════════════════════════════════════════════════════════════════════

const CallsTab: React.FC<{
  state: LlmApiTrackerDashboardState;
  onRecordSelect: (record: LlmCallRecord | null) => void;
  onNavigateToTimeline: (timestamp_ns: number) => void;
  onNavigateToTokenCost: (model: string) => void;
  compact?: boolean;
}> = ({
  state,
  onRecordSelect,
  onNavigateToTimeline,
  onNavigateToTokenCost,
  compact = false,
}) => {
  const {
    recent_calls,
    selected_record_id,
    provider_filter,
    model_filter,
    status_filter,
    streaming_filter,
    total_record_count,
  } = state;

  // 筛选
  const filtered = useMemo(() => {
    return recent_calls.filter((r) => {
      if (provider_filter && r.provider !== provider_filter) return false;
      if (model_filter && r.model !== model_filter) return false;
      if (status_filter && r.status !== status_filter) return false;
      if (streaming_filter !== null && r.is_streaming !== streaming_filter) return false;
      return true;
    });
  }, [recent_calls, provider_filter, model_filter, status_filter, streaming_filter]);

  const selectedRecord = useMemo(
    () => recent_calls.find((r) => r.record_id === selected_record_id) ?? null,
    [recent_calls, selected_record_id],
  );

  return (
    <div className="space-y-3">
      {/* 已筛选数量提示 */}
      <div className="text-xs text-gray-400">
        显示 {filtered.length} / {total_record_count} 条记录
        {filtered.length !== recent_calls.length &&
          `（缓冲区 ${recent_calls.length} 条）`}
      </div>

      {filtered.length === 0 ? (
        <div className="p-6 text-center text-gray-400">
          <div className="text-lg mb-1">🔍</div>
          <div className="text-xs">无匹配记录</div>
          <div className="text-xs mt-1 text-gray-300">尝试调整筛选条件</div>
        </div>
      ) : (
        <>
          {/* 选中的详情面板 */}
          {selectedRecord && (
            <LlmCallDetail
              record={selectedRecord}
              onClose={() => onRecordSelect(null)}
              onNavigateToTimeline={onNavigateToTimeline}
              onNavigateToProcessTree={() => {}}
              onNavigateToTokenCost={onNavigateToTokenCost}
            />
          )}

          {/* 调用列表 */}
          <div className="space-y-2">
            {filtered.map((record) => (
              <LlmCallCard
                key={record.record_id}
                record={record}
                isSelected={selected_record_id === record.record_id}
                onSelect={() =>
                  onRecordSelect(
                    selected_record_id === record.record_id ? null : record,
                  )
                }
                onNavigateToTimeline={onNavigateToTimeline}
                compact={compact}
              />
            ))}
          </div>
        </>
      )}
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════════════
// 主组件: LlmApiTrackerDashboard
// ═══════════════════════════════════════════════════════════════════════════════

export const LlmApiTrackerDashboard: React.FC<LlmApiTrackerDashboardProps> = ({
  state,
  onViewModeChange,
  onRecordSelect,
  onProviderFilterChange,
  onModelFilterChange,
  onAgentFilterChange,
  onStatusFilterChange,
  onStreamingFilterChange,
  onNavigateToTimeline,
  onNavigateToProcessTree,
  onNavigateToTokenCost,
  onNavigateToLlmApiAnomaly,
  compact = false,
}) => {
  const {
    view_mode,
    snapshot,
    recent_calls,
    provider_filter,
    model_filter,
    status_filter,
    streaming_filter,
    total_record_count,
  } = state;

  // 提取可用的 provider 和模型列表（用于筛选栏）
  const availableProviders = useMemo(
    () => snapshot
      ? Object.keys(snapshot.by_provider).map((p) => p as LlmProvider)
      : [],
    [snapshot],
  );

  const availableModels = useMemo(
    () => snapshot
      ? Object.values(snapshot.by_model).map((m) => m.model).filter(Boolean)
      : [],
    [snapshot],
  );

  const hasData = snapshot !== null && snapshot.total_records > 0;

  return (
    <div className="space-y-4">
      {/* ═══ 顶部 Tab 导航 ═══ */}
      <div className="flex items-center gap-1 flex-wrap border-b border-slate-200 pb-2">
        {VIEW_LABELS.map(({ mode, label, icon }) => {
          const isActive = view_mode === mode;
          return (
            <button
              key={mode}
              onClick={() => onViewModeChange(mode)}
              className={`px-3 py-2 text-xs font-medium rounded-md transition-all flex items-center gap-1.5 ${
                isActive
                  ? "bg-blue-600 text-white shadow-sm"
                  : hasData
                    ? "text-slate-600 hover:text-slate-900 hover:bg-slate-100"
                    : "text-slate-400"
              }`}
            >
              <span>{icon}</span>
              <span className={compact ? "hidden md:inline" : ""}>{label}</span>
              {/* 数据徽章 */}
              {mode === "calls" && total_record_count > 0 && (
                <span
                  className={`inline-flex items-center justify-center min-w-[1.25rem] h-5 rounded-full text-[10px] font-bold px-1 ${
                    isActive
                      ? "bg-white/20 text-white"
                      : "bg-blue-100 text-blue-700"
                  }`}
                >
                  {total_record_count}
                </span>
              )}
              {mode === "by_provider" && snapshot && Object.keys(snapshot.by_provider).length > 0 && (
                <span
                  className={`inline-flex items-center justify-center min-w-[1.25rem] h-5 rounded-full text-[10px] font-bold px-1 ${
                    isActive
                      ? "bg-white/20 text-white"
                      : "bg-purple-100 text-purple-700"
                  }`}
                >
                  {Object.keys(snapshot.by_provider).length}
                </span>
              )}
            </button>
          );
        })}
      </div>

      {/* ═══ 筛选栏（calls 视图） ═══ */}
      {view_mode === "calls" && (
        <FilterBar
          provider_filter={provider_filter}
          model_filter={model_filter}
          status_filter={status_filter}
          streaming_filter={streaming_filter}
          available_providers={availableProviders}
          available_models={availableModels}
          onProviderFilterChange={onProviderFilterChange}
          onModelFilterChange={onModelFilterChange}
          onStatusFilterChange={onStatusFilterChange}
          onStreamingFilterChange={onStreamingFilterChange}
        />
      )}

      {/* ═══ 视图内容 ═══ */}
      <div className={compact ? "max-h-[60vh] overflow-y-auto" : ""}>
        {/* ── 概览视图 ── */}
        {view_mode === "overview" && (
          <OverviewTab
            state={state}
            onRecordSelect={onRecordSelect}
            onProviderFilterChange={onProviderFilterChange}
            onNavigateToTimeline={onNavigateToTimeline}
            onNavigateToTokenCost={onNavigateToTokenCost}
            onNavigateToLlmApiAnomaly={onNavigateToLlmApiAnomaly}
            compact={compact}
          />
        )}

        {/* ── 调用列表视图 ── */}
        {view_mode === "calls" && (
          <CallsTab
            state={state}
            onRecordSelect={onRecordSelect}
            onNavigateToTimeline={onNavigateToTimeline}
            onNavigateToTokenCost={onNavigateToTokenCost}
            compact={compact}
          />
        )}

        {/* ── Provider 分析视图 ── */}
        {view_mode === "by_provider" && (
          <ProviderTab
            state={state}
            onProviderFilterChange={onProviderFilterChange}
            onNavigateToLlmApiAnomaly={onNavigateToLlmApiAnomaly}
          />
        )}
      </div>

      {/* ═══ 底部状态栏 ═══ */}
      <div className="text-xs text-gray-400 flex items-center justify-between pt-2 border-t border-slate-100">
        <span>
          {view_mode === "overview" && snapshot &&
            `${snapshot.total_records} 调用 · ${Object.keys(snapshot.by_provider).length} providers · ${formatTokens(snapshot.global_total_tokens)} tokens`}
          {view_mode === "calls" &&
            `${total_record_count} 记录${recent_calls.length !== total_record_count ? `（缓冲区 ${recent_calls.length}）` : ""}`}
          {view_mode === "by_provider" && snapshot &&
            `${Object.keys(snapshot.by_provider).length} providers`}
          {!hasData && "等待 LLM API 调用数据…"}
        </span>
        <span>{new Date(state.last_update_ms).toLocaleTimeString()}</span>
      </div>
    </div>
  );
};
