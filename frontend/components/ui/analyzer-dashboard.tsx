'use client';

import React, { useMemo } from 'react';
import type {
  IntegratedAnalyzerDashboardProps,
  IntegratedViewMode,
  PipelineStageState,
  StageAnalyzerInfo,
  PipelineStatus,
  CrossAnalysisData,
} from '@/lib/analyzer-dashboard-contract';
import { PIPELINE_STAGE_LABEL, analyzerTypeToViewMode } from '@/lib/analyzer-dashboard-contract';

// ── 子仪表板导入 ──
import { AgentLoopDashboard } from './agent-loop-dashboard';
import { ToolExecutionDashboard } from './tool-execution-dashboard';
import { DedupDiagnosticsPanel } from './dedup-diagnostics-panel';
import { SessionHealthDashboard } from './session-health-dashboard';
import { SseContentScannerDashboard } from './sse-content-scanner-dashboard';

// ═══════════════════════════════════════════════════════════════════════════════
// 常量
// ═══════════════════════════════════════════════════════════════════════════════

const TAB_DEFS: { mode: IntegratedViewMode; label: string; icon: string }[] = [
  { mode: 'pipeline_overview', label: '流水线', icon: '🔗' },
  { mode: 'agent_loop', label: '循环检测', icon: '🔄' },
  { mode: 'tool_execution', label: '工具调用', icon: '🔧' },
  { mode: 'dedup_diagnostics', label: '去重诊断', icon: '📊' },
  { mode: 'sse_content_scanner', label: 'SSE 扫描', icon: '🛡️' },
  { mode: 'session_health', label: '健康监控', icon: '💚' },
  { mode: 'cross_analysis', label: '交叉分析', icon: '🔀' },
];

const STAGE_COLORS: Record<string, string> = {
  Preprocessing: 'border-l-slate-400 bg-slate-50',
  Parsing: 'border-l-blue-400 bg-blue-50',
  CausalLinking: 'border-l-violet-400 bg-violet-50',
  InjectionDetection: 'border-l-amber-400 bg-amber-50',
  AnomalyDetection: 'border-l-red-400 bg-red-50',
  AlertEnrichment: 'border-l-emerald-400 bg-emerald-50',
};

const STAGE_DOT: Record<string, string> = {
  Preprocessing: 'bg-slate-400',
  Parsing: 'bg-blue-400',
  CausalLinking: 'bg-violet-400',
  InjectionDetection: 'bg-amber-400',
  AnomalyDetection: 'bg-red-400',
  AlertEnrichment: 'bg-emerald-400',
};

// ═══════════════════════════════════════════════════════════════════════════════
// 子组件: PipelineStageRow — 单个流水线阶段
// ═══════════════════════════════════════════════════════════════════════════════

const PipelineStageRow: React.FC<{
  stage: PipelineStageState;
  onAnalyzerClick: (analyzer: StageAnalyzerInfo) => void;
}> = ({ stage, onAnalyzerClick }) => {
  return (
    <div
      className={`rounded-lg border border-l-4 border-slate-200 p-3 ${STAGE_COLORS[stage.stage]}`}
    >
      {/* 阶段标题行 */}
      <div className="mb-2 flex items-center gap-2">
        <span className={`inline-block h-2.5 w-2.5 rounded-full ${STAGE_DOT[stage.stage]}`} />
        <h4 className="text-xs font-semibold text-gray-700">{PIPELINE_STAGE_LABEL[stage.stage]}</h4>
        <span className="ml-auto font-mono text-xs text-gray-400">
          {stage.throughput > 0 ? `${(stage.throughput / 1000).toFixed(1)}k evt/s` : '空闲'}
        </span>
        {stage.has_alerts && (
          <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-red-500" />
        )}
      </div>

      {/* 分析器列表 */}
      <div className="space-y-1">
        {stage.analyzers.map((analyzer) => (
          <button
            key={analyzer.id}
            onClick={() => {
              if (analyzer.dashboard_type && analyzer.dashboard_type !== 'generic') {
                onAnalyzerClick(analyzer);
              }
            }}
            disabled={!analyzer.dashboard_type || analyzer.dashboard_type === 'generic'}
            className={`flex w-full items-center gap-2 rounded p-1.5 text-left text-xs transition-colors ${
              analyzer.dashboard_type && analyzer.dashboard_type !== 'generic'
                ? 'cursor-pointer hover:bg-white/60'
                : 'cursor-default'
            }`}
          >
            {/* 状态指示灯 */}
            <span
              className={`inline-block h-1.5 w-1.5 flex-shrink-0 rounded-full ${
                analyzer.active ? 'bg-emerald-500' : 'bg-gray-300'
              }`}
            />
            {/* 名称 */}
            <span className="flex-1 truncate font-mono">{analyzer.name}</span>
            {/* 仪表板链接指示 */}
            {analyzer.dashboard_type && analyzer.dashboard_type !== 'generic' && (
              <span className="flex-shrink-0 text-blue-400" title="查看详情">
                →
              </span>
            )}
          </button>
        ))}
      </div>
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════════════
// 子组件: PipelineOverview — 流水线概览视图
// ═══════════════════════════════════════════════════════════════════════════════

const PipelineOverview: React.FC<{
  pipeline: PipelineStatus;
  onAnalyzerClick: (analyzer: StageAnalyzerInfo) => void;
  compact?: boolean;
}> = ({ pipeline, onAnalyzerClick, compact = false }) => {
  const stageOrder = Object.keys(pipeline.stages) as (keyof typeof pipeline.stages)[];

  // ── 汇总指标 ──
  const totalAnalyzers = stageOrder.reduce(
    (sum, s) => sum + pipeline.stages[s].analyzers.length,
    0,
  );
  const activeAnalyzers = stageOrder.reduce(
    (sum, s) => sum + pipeline.stages[s].analyzers.filter((a) => a.active).length,
    0,
  );
  const stagesWithAlerts = stageOrder.filter((s) => pipeline.stages[s].has_alerts).length;

  return (
    <div className="space-y-4">
      {/* ── 顶部摘要卡片 ── */}
      <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
        <SummaryCard label="总吞吐量" value={pipeline.total_throughput} unit="evt/s" />
        <SummaryCard label="活跃分析器" value={activeAnalyzers} unit={`/ ${totalAnalyzers}`} />
        <SummaryCard
          label="告警阶段"
          value={stagesWithAlerts}
          unit={`/ ${stageOrder.length}`}
          highlight={stagesWithAlerts > 0}
        />
        <SummaryCard
          label="更新时间"
          value={new Date(pipeline.last_update_ms).toLocaleTimeString()}
          unit=""
          raw
        />
      </div>

      {/* ── 流水线阶段图 ── */}
      <div
        className={`grid gap-3 ${
          compact ? 'grid-cols-1' : 'grid-cols-1 md:grid-cols-2 lg:grid-cols-3'
        }`}
      >
        {stageOrder.map((stageKey) => (
          <PipelineStageRow
            key={stageKey}
            stage={pipeline.stages[stageKey]}
            onAnalyzerClick={onAnalyzerClick}
          />
        ))}
      </div>
    </div>
  );
};

/** 摘要卡片 */
const SummaryCard: React.FC<{
  label: string;
  value: number | string;
  unit: string;
  highlight?: boolean;
  raw?: boolean;
}> = ({ label, value, unit, highlight = false, raw = false }) => (
  <div
    className={`rounded-lg border p-3 ${
      highlight ? 'border-red-300 bg-red-50' : 'border-slate-200 bg-white'
    }`}
  >
    <div className="mb-1 text-xs text-gray-500">{label}</div>
    <div className="font-mono text-lg font-semibold">
      {raw
        ? value
        : typeof value === 'number'
          ? value >= 1000
            ? `${(value / 1000).toFixed(1)}k`
            : value
          : value}{' '}
      <span className="text-xs font-normal text-gray-400">{unit}</span>
    </div>
  </div>
);

// ═══════════════════════════════════════════════════════════════════════════════
// 子组件: CrossAnalysis — 交叉分析视图
// ═══════════════════════════════════════════════════════════════════════════════

const CrossAnalysis: React.FC<{
  data: CrossAnalysisData;
  onToolNameClick: (tool_name: string) => void;
  compact?: boolean;
}> = ({ data, onToolNameClick, compact = false }) => {
  // 按循环率降序排列
  const sortedCorrelations = useMemo(
    () => [...data.loop_tool_correlations].sort((a, b) => b.loop_rate - a.loop_rate),
    [data.loop_tool_correlations],
  );

  return (
    <div className="space-y-6">
      {/* ── 全局概览指标 ── */}
      <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
        <SummaryCard
          label="事件缩减率"
          value={(data.global_event_reduction * 100).toFixed(1)}
          unit="%"
          highlight={data.global_event_reduction > 0.3}
        />
        <SummaryCard
          label="循环影响率"
          value={(data.loop_affected_call_ratio * 100).toFixed(1)}
          unit="%"
          highlight={data.loop_affected_call_ratio > 0.1}
        />
        <SummaryCard label="受影响工具" value={sortedCorrelations.length} unit="个" />
        <SummaryCard
          label="去重节省"
          value={data.dedup_by_source.reduce((s, d) => s + d.suppressed, 0)}
          unit="事件"
        />
      </div>

      {/* ── 循环-工具关联表 ── */}
      {sortedCorrelations.length > 0 && (
        <div className="rounded-lg border border-slate-200 bg-white p-4">
          <h4 className="mb-3 text-xs font-semibold tracking-wide text-gray-500 uppercase">
            工具循环关联分析
          </h4>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-slate-100 text-gray-400">
                  <th className="py-2 text-left font-medium">工具</th>
                  <th className="py-2 text-right font-medium">调用总数</th>
                  <th className="py-2 text-right font-medium">循环次数</th>
                  <th className="py-2 text-right font-medium">循环率</th>
                  <th className="py-2 text-right font-medium">成功率</th>
                  <th className="py-2 text-right font-medium">严重度</th>
                </tr>
              </thead>
              <tbody>
                {sortedCorrelations.map((corr) => (
                  <tr
                    key={corr.tool_name}
                    className="cursor-pointer border-b border-slate-50 hover:bg-slate-50"
                    onClick={() => onToolNameClick(corr.tool_name)}
                  >
                    <td className="py-2 font-mono text-blue-600">{corr.tool_name}</td>
                    <td className="py-2 text-right font-mono">{corr.total_calls}</td>
                    <td className="py-2 text-right font-mono">
                      <span className={corr.loop_rate > 0.1 ? 'font-semibold text-red-600' : ''}>
                        {corr.loop_count}
                      </span>
                    </td>
                    <td className="py-2 text-right font-mono">
                      <LoopRateBar rate={corr.loop_rate} />
                    </td>
                    <td className="py-2 text-right font-mono">
                      {(corr.tool_success_rate * 100).toFixed(0)}%
                    </td>
                    <td className="py-2 text-right">
                      {corr.loop_severities.Emergency > 0 && (
                        <span className="ml-1 text-red-500">🔴</span>
                      )}
                      {corr.loop_severities.Critical > 0 && (
                        <span className="ml-1 text-orange-500">🟠</span>
                      )}
                      {corr.loop_severities.Warning > 0 && (
                        <span className="ml-1 text-amber-500">🟡</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* ── 空状态 ── */}
      {sortedCorrelations.length === 0 && (
        <div className="p-6 text-center text-gray-400">
          <div className="mb-1 text-lg">🔀</div>
          <div className="text-xs">暂无交叉分析数据</div>
          <div className="mt-1 text-xs text-gray-300">
            需要 AgentLoopDetector 和 ToolExecutionTracker 同时输出数据
          </div>
        </div>
      )}
    </div>
  );
};

/** 循环率微型进度条 */
const LoopRateBar: React.FC<{ rate: number }> = ({ rate }) => {
  const pct = (rate * 100).toFixed(1);
  const color = rate > 0.2 ? 'bg-red-500' : rate > 0.1 ? 'bg-amber-500' : 'bg-slate-300';

  return (
    <div className="flex items-center justify-end gap-1.5">
      <div className="h-1.5 w-12 overflow-hidden rounded-full bg-slate-100">
        <div
          className={`h-full rounded-full ${color}`}
          style={{ width: `${Math.min(rate * 100, 100)}%` }}
        />
      </div>
      <span className="w-8 text-left text-gray-500">{pct}%</span>
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════════════
// 主组件: IntegratedAnalyzerDashboard
// ═══════════════════════════════════════════════════════════════════════════════

/** @deprecated 阶段二实时视角（SSE 接入后迁移至运行监控区块），现无生产者，仅存档。 */
export const IntegratedAnalyzerDashboard: React.FC<IntegratedAnalyzerDashboardProps> = ({
  state,
  onViewModeChange,
  // AgentLoop callbacks
  onLoopViewModeChange,
  onLoopSelect,
  onLoopTypeFilterChange,
  onLoopSeverityFilterChange,
  onLoopToolFilterChange,
  onLoopPidFilterChange,
  onShowActiveOnlyChange,
  // ToolExecution callbacks
  onToolViewModeChange,
  onAgentSelect,
  onCallSelect,
  onToolStatusFilterChange,
  onToolFilterChange,
  // SessionHealth callbacks
  onSessionHealthViewModeChange,
  onSessionHealthAlertSelect,
  onSessionHealthSessionSelect,
  onSessionHealthAgentSelect,
  onSessionHealthAlertTypeFilterChange,
  onSessionHealthSeverityFilterChange,
  onSessionHealthAgentFilterChange,
  onSessionHealthMaxScoreFilterChange,
  onSessionHealthDimensionWeightsChange,
  // SseContentScanner callbacks
  onScannerViewModeChange,
  onScannerSignalSelect,
  onScannerLayerFilterChange,
  onScannerEventTypeFilterChange,
  onScannerToolFilterChange,
  onScannerMinConfidenceChange,
  // Cross-navigation
  onNavigateFromLoopToTool,
  onNavigateFromSessionHealthToTool,
  onNavigateFromSessionHealthToLoop,
  onNavigateFromSessionHealthToTokenCost,
  onNavigateFromScannerToTimeline,
  onNavigateFromScannerToToolExecution,
  onNavigateToView,
  compact = false,
}) => {
  const {
    view_mode,
    pipeline,
    agent_loop_state,
    tool_exec_state,
    dedup_state,
    session_health_state,
    sse_scanner_state,
    cross_analysis,
  } = state;

  // ── 导航：从流水线概览点击分析器 ──
  const handleAnalyzerClick = (analyzer: StageAnalyzerInfo) => {
    if (analyzer.dashboard_type && analyzer.dashboard_type !== 'generic') {
      const mode = analyzerTypeToViewMode(analyzer.dashboard_type);
      onNavigateToView(mode);
    }
  };

  // ── 从 AgentLoop 跳转到 ToolExecution ──
  const handleNavigateFromLoopToTool = (tool_name: string) => {
    onLoopToolFilterChange(tool_name);
    onNavigateFromLoopToTool(tool_name);
  };

  return (
    <div className="space-y-4">
      {/* ═══ 顶部 Tab 导航 ═══ */}
      <div className="flex flex-wrap items-center gap-1 border-b border-slate-200 pb-2">
        {TAB_DEFS.map(({ mode, label, icon }) => {
          // 没有数据时淡化显示
          const hasData =
            mode === 'pipeline_overview'
              ? pipeline != null
              : mode === 'agent_loop'
                ? agent_loop_state.active_loops.length > 0 ||
                  agent_loop_state.resolved_loops.length > 0
                : mode === 'tool_execution'
                  ? tool_exec_state.agent_pids.length > 0 || tool_exec_state.recent_calls.length > 0
                  : mode === 'dedup_diagnostics'
                    ? dedup_state.current != null
                    : mode === 'sse_content_scanner'
                      ? sse_scanner_state.stats.events_scanned > 0
                      : mode === 'session_health'
                        ? session_health_state.recent_snapshots.length > 0 ||
                          session_health_state.agent_summaries.size > 0
                        : cross_analysis != null;

          return (
            <button
              key={mode}
              onClick={() => onViewModeChange(mode)}
              className={`flex items-center gap-1.5 rounded-md px-3 py-2 text-xs font-medium transition-all ${
                view_mode === mode
                  ? 'bg-blue-600 text-white shadow-sm'
                  : hasData
                    ? 'text-slate-600 hover:bg-slate-100 hover:text-slate-900'
                    : 'text-slate-400'
              }`}
            >
              <span>{icon}</span>
              <span className={compact ? 'hidden md:inline' : ''}>{label}</span>
              {/* 数据徽章 */}
              {mode === 'agent_loop' && agent_loop_state.active_loops.length > 0 && (
                <span
                  className={`inline-flex h-5 w-5 items-center justify-center rounded-full text-[10px] font-bold ${
                    view_mode === mode ? 'bg-white/20 text-white' : 'bg-red-100 text-red-700'
                  }`}
                >
                  {agent_loop_state.active_loops.length}
                </span>
              )}
              {mode === 'tool_execution' && tool_exec_state.agent_pids.length > 0 && (
                <span
                  className={`inline-flex h-5 w-5 items-center justify-center rounded-full text-[10px] font-bold ${
                    view_mode === mode ? 'bg-white/20 text-white' : 'bg-blue-100 text-blue-700'
                  }`}
                >
                  {tool_exec_state.agent_pids.length}
                </span>
              )}
              {mode === 'session_health' && session_health_state.agent_summaries.size > 0 && (
                <span
                  className={`inline-flex h-5 w-5 items-center justify-center rounded-full text-[10px] font-bold ${
                    view_mode === mode
                      ? 'bg-white/20 text-white'
                      : 'bg-emerald-100 text-emerald-700'
                  }`}
                >
                  {session_health_state.agent_summaries.size}
                </span>
              )}
              {mode === 'sse_content_scanner' && sse_scanner_state.stats.signals_produced > 0 && (
                <span
                  className={`inline-flex h-5 w-5 items-center justify-center rounded-full text-[10px] font-bold ${
                    view_mode === mode ? 'bg-white/20 text-white' : 'bg-amber-100 text-amber-700'
                  }`}
                >
                  {sse_scanner_state.stats.signals_produced}
                </span>
              )}
            </button>
          );
        })}
      </div>

      {/* ═══ 视图内容 ═══ */}
      <div className={compact ? 'max-h-[70vh] overflow-y-auto' : ''}>
        {/* ── 流水线概览 ── */}
        {view_mode === 'pipeline_overview' && pipeline && (
          <PipelineOverview
            pipeline={pipeline}
            onAnalyzerClick={handleAnalyzerClick}
            compact={compact}
          />
        )}
        {view_mode === 'pipeline_overview' && !pipeline && (
          <div className="p-6 text-center text-gray-400">
            <div className="mb-1 text-lg">🔗</div>
            <div className="text-xs">等待流水线状态数据...</div>
          </div>
        )}

        {/* ── AgentLoop 详情 ── */}
        {view_mode === 'agent_loop' && (
          <AgentLoopDashboard
            state={agent_loop_state}
            onViewModeChange={onLoopViewModeChange}
            onLoopSelect={onLoopSelect}
            onTypeFilterChange={onLoopTypeFilterChange}
            onSeverityFilterChange={onLoopSeverityFilterChange}
            onToolFilterChange={onLoopToolFilterChange}
            onPidFilterChange={onLoopPidFilterChange}
            onShowActiveOnlyChange={onShowActiveOnlyChange}
            compact={compact}
          />
        )}

        {/* ── ToolExecution 详情 ── */}
        {view_mode === 'tool_execution' && (
          <ToolExecutionDashboard
            state={tool_exec_state}
            onViewModeChange={onToolViewModeChange}
            onAgentSelect={onAgentSelect}
            onCallSelect={onCallSelect}
            onStatusFilterChange={onToolStatusFilterChange}
            onToolFilterChange={onToolFilterChange}
            compact={compact}
          />
        )}

        {/* ── Dedup 诊断 ── */}
        {view_mode === 'dedup_diagnostics' && (
          <DedupDiagnosticsPanel
            state={dedup_state}
            compact={compact}
            showDroppedEvents={dedup_state.recent_dropped.length > 0}
          />
        )}

        {/* ── SseContentScanner 详情 ── */}
        {view_mode === 'sse_content_scanner' && (
          <SseContentScannerDashboard
            state={sse_scanner_state}
            onViewModeChange={onScannerViewModeChange}
            onSignalSelect={onScannerSignalSelect}
            onLayerFilterChange={onScannerLayerFilterChange}
            onEventTypeFilterChange={onScannerEventTypeFilterChange}
            onToolFilterChange={onScannerToolFilterChange}
            onMinConfidenceChange={onScannerMinConfidenceChange}
            onNavigateToTimeline={onNavigateFromScannerToTimeline}
            onNavigateToToolExecution={onNavigateFromScannerToToolExecution}
            compact={compact}
          />
        )}

        {/* ── SessionHealth 详情 ── */}
        {view_mode === 'session_health' && (
          <SessionHealthDashboard
            state={session_health_state}
            onViewModeChange={onSessionHealthViewModeChange}
            onAlertSelect={onSessionHealthAlertSelect}
            onSessionSelect={onSessionHealthSessionSelect}
            onAgentSelect={onSessionHealthAgentSelect}
            onAlertTypeFilterChange={onSessionHealthAlertTypeFilterChange}
            onSeverityFilterChange={onSessionHealthSeverityFilterChange}
            onAgentFilterChange={onSessionHealthAgentFilterChange}
            onMaxHealthScoreFilterChange={onSessionHealthMaxScoreFilterChange}
            onNavigateToToolExecution={onNavigateFromSessionHealthToTool}
            onNavigateToAgentLoop={onNavigateFromSessionHealthToLoop}
            onNavigateToTokenCost={onNavigateFromSessionHealthToTokenCost}
            compact={compact}
          />
        )}

        {/* ── 交叉分析 ── */}
        {view_mode === 'cross_analysis' && cross_analysis && (
          <CrossAnalysis
            data={cross_analysis}
            onToolNameClick={handleNavigateFromLoopToTool}
            compact={compact}
          />
        )}
        {view_mode === 'cross_analysis' && !cross_analysis && (
          <div className="p-6 text-center text-gray-400">
            <div className="mb-1 text-lg">🔀</div>
            <div className="text-xs">等待交叉分析数据...</div>
            <div className="mt-1 text-xs text-gray-300">
              需要至少两个分析器输出数据才能计算交叉指标
            </div>
          </div>
        )}
      </div>

      {/* ═══ 底部状态栏 ═══ */}
      <div className="flex items-center justify-between border-t border-slate-100 pt-2 text-xs text-gray-400">
        <span>
          {view_mode === 'agent_loop' &&
            `活跃 ${agent_loop_state.active_loops.length} · 已解决 ${agent_loop_state.resolved_loops.length}`}
          {view_mode === 'tool_execution' &&
            `${tool_exec_state.agent_pids.length} Agent · ${tool_exec_state.recent_calls.length} 次调用`}
          {view_mode === 'dedup_diagnostics' &&
            dedup_state.current &&
            `事件 ${dedup_state.current.events_processed.toLocaleString()} · 丢弃 ${dedup_state.current.duplicates_suppressed.toLocaleString()}`}
          {view_mode === 'session_health' &&
            `${session_health_state.agent_summaries.size} Agent · ${session_health_state.alerts.length} 告警`}
          {view_mode === 'sse_content_scanner' &&
            `已扫描 ${sse_scanner_state.stats.events_scanned.toLocaleString()} · 信号 ${sse_scanner_state.stats.signals_produced.toLocaleString()}`}
          {view_mode === 'pipeline_overview' &&
            `6 阶段 · ${pipeline ? Object.values(pipeline.stages).reduce((s, st) => s + st.analyzers.length, 0) : '?'} 分析器`}
          {view_mode === 'cross_analysis' &&
            `${cross_analysis?.loop_tool_correlations.length ?? 0} 工具关联`}
        </span>
        <span>{new Date(state.last_update_ms).toLocaleTimeString()}</span>
      </div>
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════════════
// 向后兼容：AnalyzerDashboard 别名
// ═══════════════════════════════════════════════════════════════════════════════

/**
 * @deprecated 使用 IntegratedAnalyzerDashboard 替代。
 * 保留此别名以便调用方渐进迁移。
 */
export const AnalyzerDashboard = IntegratedAnalyzerDashboard;
