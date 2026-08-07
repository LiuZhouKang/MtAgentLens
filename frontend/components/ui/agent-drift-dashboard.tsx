'use client';

import React, { useMemo } from 'react';
import type {
  AgentDriftDashboardProps,
  AgentDriftDashboardState,
  DriftDashboardViewMode,
  DriftDimension,
  DriftDirection,
  DriftSnapshot,
  DriftSeverity,
  AgentDriftSummary,
  DriftDimensionBarProps,
  DriftTrendChartProps,
  AgentDriftCardProps,
  DriftSnapshotCardProps,
  DriftSnapshotDetailProps,
} from '@/lib/agent-drift-contract';
import {
  DRIFT_DIMENSION_LABEL,
  DRIFT_DIMENSION_ICON,
  DRIFT_DIMENSION_WEIGHT,
  DRIFT_DIRECTION_LABEL,
  DRIFT_DIRECTION_ICON,
  DRIFT_DIRECTION_COLOR,
  DRIFT_SEVERITY_COLOR,
  DRIFT_SEVERITY_DOT,
  driftScoreToSeverity,
} from '@/lib/agent-drift-contract';

// ═══════════════════════════════════════════════════════════════════════════
// 常量
// ═══════════════════════════════════════════════════════════════════════════

const VIEW_LABELS: { mode: DriftDashboardViewMode; label: string; icon: string }[] = [
  { mode: 'overview', label: '概览', icon: '📊' },
  { mode: 'dimensions', label: '维度雷达', icon: '🔄' },
  { mode: 'agents', label: 'Agent', icon: '🤖' },
  { mode: 'trend', label: '趋势', icon: '📈' },
];

const DIMENSION_COLORS: Record<DriftDimension, string> = {
  ToolUsage: 'bg-indigo-400',
  Latency: 'bg-amber-400',
  ErrorRate: 'bg-rose-400',
  OutputSize: 'bg-emerald-400',
  EventFrequency: 'bg-violet-400',
  HttpMethodDistribution: 'bg-teal-400',
};

const DIMENSION_TEXT: Record<DriftDimension, string> = {
  ToolUsage: 'text-indigo-600',
  Latency: 'text-amber-600',
  ErrorRate: 'text-rose-600',
  OutputSize: 'text-emerald-600',
  EventFrequency: 'text-violet-600',
  HttpMethodDistribution: 'text-teal-600',
};

const ALL_DIMENSIONS: DriftDimension[] = [
  'ToolUsage',
  'Latency',
  'ErrorRate',
  'OutputSize',
  'EventFrequency',
  'HttpMethodDistribution',
];

// ═══════════════════════════════════════════════════════════════════════════
// 子组件: SummaryCard — 统计卡片
// ═══════════════════════════════════════════════════════════════════════════

const SummaryCard: React.FC<{
  label: string;
  value: number | string;
  unit?: string;
  highlight?: boolean;
  raw?: boolean;
}> = ({ label, value, unit = '', highlight = false, raw = false }) => (
  <div
    className={`rounded-lg border p-3 ${
      highlight ? 'border-red-300 bg-red-50' : 'border-slate-200 bg-white'
    }`}
  >
    <div className="mb-1 text-xs text-gray-500">{label}</div>
    <div className="font-mono text-lg font-semibold">
      {raw ? value : typeof value === 'number' ? value.toLocaleString() : value}
      {unit && <span className="ml-1 text-xs font-normal text-gray-400">{unit}</span>}
    </div>
  </div>
);

// ═══════════════════════════════════════════════════════════════════════════
// 子组件: SeverityDot — 严重性指示点
// ═══════════════════════════════════════════════════════════════════════════

const SeverityDot: React.FC<{ severity: DriftSeverity }> = ({ severity }) => (
  <span className={`inline-block h-2 w-2 rounded-full ${DRIFT_SEVERITY_DOT[severity]}`} />
);

// ═══════════════════════════════════════════════════════════════════════════
// 子组件: ScoreBar — 评分进度条
// ═══════════════════════════════════════════════════════════════════════════

const ScoreBar: React.FC<{ score: number; color: string; threshold?: number }> = ({
  score,
  color,
  threshold = 0.25,
}) => {
  const pct = Math.min(score * 100, 100);
  const severity = driftScoreToSeverity(score);

  return (
    <div className="flex w-full items-center gap-2">
      <div className="relative h-2 flex-1 overflow-hidden rounded-full bg-slate-100">
        <div
          className={`h-full rounded-full ${color} transition-all`}
          style={{ width: `${pct}%` }}
        />
        {/* 阈值线 */}
        <div
          className="absolute top-0 bottom-0 w-0.5 bg-red-300"
          style={{ left: `${threshold * 100}%` }}
        />
      </div>
      <span
        className={`w-10 text-right font-mono text-xs ${DRIFT_SEVERITY_COLOR[severity].split(' ')[0]}`}
      >
        {(score * 100).toFixed(0)}%
      </span>
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════════
// 子组件: DriftDimensionBar — 单维度评分条（含权重标注）
// ═══════════════════════════════════════════════════════════════════════════

const DriftDimensionBar: React.FC<DriftDimensionBarProps> = ({
  dimension,
  score,
  weight,
  compact = false,
}) => (
  <div className={`flex items-center gap-2 ${compact ? 'text-[10px]' : 'text-xs'}`}>
    {/* 维度标签 */}
    <span className="flex w-20 flex-shrink-0 items-center gap-1">
      <span>{DRIFT_DIMENSION_ICON[dimension]}</span>
      <span className={`${DIMENSION_TEXT[dimension]} ${compact ? 'text-[10px]' : 'text-xs'}`}>
        {DRIFT_DIMENSION_LABEL[dimension]}
      </span>
    </span>
    {/* 评分条 */}
    <div className="flex-1">
      <ScoreBar score={score} color={DIMENSION_COLORS[dimension]} />
    </div>
    {/* 权重标注 */}
    {weight !== undefined && !compact && (
      <span className="w-8 text-right font-mono text-[10px] text-gray-400">
        ×{weight.toFixed(2)}
      </span>
    )}
  </div>
);

// ═══════════════════════════════════════════════════════════════════════════
// 子组件: DriftDirectionBadge — 漂移方向徽章
// ═══════════════════════════════════════════════════════════════════════════

const DriftDirectionBadge: React.FC<{ direction: DriftDirection }> = ({ direction }) => (
  <span
    className={`inline-flex items-center rounded border px-1.5 py-0.5 text-[10px] font-medium ${DRIFT_DIRECTION_COLOR[direction]}`}
  >
    {DRIFT_DIRECTION_ICON[direction]} {DRIFT_DIRECTION_LABEL[direction]}
  </span>
);

// ═══════════════════════════════════════════════════════════════════════════
// 子组件: AgentDriftCard — Agent 漂移摘要卡片
// ═══════════════════════════════════════════════════════════════════════════

const AgentDriftCard: React.FC<AgentDriftCardProps> = ({
  summary,
  isSelected,
  onSelect,
  onNavigateToProcessTree,
  compact = false,
}) => {
  const severity = driftScoreToSeverity(summary.latest_overall_score);

  // 找出得分最高的两个维度
  const topDimensions = useMemo(() => {
    return ALL_DIMENSIONS.filter((d) => summary.latest_dimensions[d] !== undefined)
      .sort((a, b) => (summary.latest_dimensions[b] ?? 0) - (summary.latest_dimensions[a] ?? 0))
      .slice(0, 2);
  }, [summary.latest_dimensions]);

  return (
    <div
      onClick={onSelect}
      className={`cursor-pointer rounded-lg border border-l-4 p-3 transition-all ${
        summary.latest_direction === 'Worsening'
          ? 'border-l-red-500'
          : summary.latest_direction === 'Recovering'
            ? 'border-l-emerald-500'
            : 'border-l-slate-300'
      } ${
        isSelected ? 'bg-blue-50/50 shadow-sm ring-2 ring-blue-400' : 'bg-white hover:shadow-sm'
      }`}
    >
      {/* ── 头部: Agent 名 + PID + 方向 ── */}
      <div className="mb-2 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <SeverityDot severity={severity} />
          <span className="text-sm font-semibold text-gray-800">{summary.agent_comm}</span>
          <span className="font-mono text-[10px] text-gray-400">pid:{summary.agent_root_pid}</span>
        </div>
        <DriftDirectionBadge direction={summary.latest_direction} />
      </div>

      {/* ── 综合评分 ── */}
      <div className="mb-2">
        <ScoreBar score={summary.latest_overall_score} color="bg-slate-500" />
      </div>

      {/* ── 最强漂移维度 ── */}
      <div className={`space-y-1 ${compact ? 'hidden' : ''}`}>
        {topDimensions.map((dim) => (
          <DriftDimensionBar
            key={dim}
            dimension={dim}
            score={summary.latest_dimensions[dim] ?? 0}
            compact
          />
        ))}
      </div>

      {/* ── 底部信息行 ── */}
      <div className="mt-2 flex items-center justify-between border-t border-slate-100 pt-2 text-[10px] text-gray-400">
        <span>
          {summary.snapshot_count} 快照 · {summary.threshold_breach_count} 告警
        </span>
        {summary.baseline_established ? (
          <span>基线: {summary.baseline_event_count} 事件</span>
        ) : (
          <span className="text-amber-500">基线学习中…</span>
        )}
      </div>

      {/* ── 操作按钮（选中状态） ── */}
      {isSelected && (
        <div className="mt-2 flex items-center gap-2 border-t border-slate-200/50 pt-2">
          <button
            onClick={(e) => {
              e.stopPropagation();
              onNavigateToProcessTree(summary.agent_root_pid);
            }}
            className="rounded border border-slate-200 bg-white px-2 py-1 text-[10px] text-gray-600 transition-colors hover:bg-slate-50"
          >
            🌳 进程树
          </button>
        </div>
      )}
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════════
// 子组件: DriftSnapshotCard — 漂移快照卡片
// ═══════════════════════════════════════════════════════════════════════════

const DriftSnapshotCard: React.FC<DriftSnapshotCardProps> = ({
  snapshot,
  isSelected,
  onSelect,
  onNavigateToTimerange,
  compact = false,
}) => {
  const severity = driftScoreToSeverity(snapshot.overall_score);
  const time = new Date(snapshot.window_start_ms).toLocaleTimeString();

  return (
    <div
      onClick={onSelect}
      className={`cursor-pointer rounded-lg border border-l-4 p-3 transition-all ${
        snapshot.direction === 'Worsening'
          ? 'border-l-red-500'
          : snapshot.direction === 'Recovering'
            ? 'border-l-emerald-500'
            : 'border-l-slate-300'
      } border-slate-200 bg-white hover:shadow-sm`}
    >
      {/* ── 头部 ── */}
      <div className="mb-1.5 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <SeverityDot severity={severity} />
          <span className="text-xs font-semibold text-gray-700">{snapshot.agent_comm}</span>
          <span className="font-mono text-[10px] text-gray-400">{time}</span>
        </div>
        <DriftDirectionBadge direction={snapshot.direction} />
      </div>

      {/* ── 综合评分 ── */}
      <div className="mb-2">
        <ScoreBar score={snapshot.overall_score} color="bg-slate-500" />
      </div>

      {/* ── 维度 mini bars ── */}
      {!compact && Object.keys(snapshot.dimensions).length > 0 && (
        <div className="space-y-0.5">
          {ALL_DIMENSIONS.filter((d) => snapshot.dimensions[d] !== undefined)
            .slice(0, 3)
            .map((dim) => (
              <DriftDimensionBar
                key={dim}
                dimension={dim}
                score={snapshot.dimensions[dim]!}
                compact
              />
            ))}
        </div>
      )}

      {/* ── 操作按钮 ── */}
      {isSelected && (
        <div className="mt-2 border-t border-slate-100 pt-2">
          <button
            onClick={(e) => {
              e.stopPropagation();
              onNavigateToTimerange(snapshot.window_start_ms, snapshot.window_end_ms);
            }}
            className="rounded border border-blue-200 bg-blue-50 px-2 py-1 text-[10px] text-blue-700 transition-colors hover:bg-blue-100"
          >
            ⏱ 定位时间窗口
          </button>
        </div>
      )}
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════════
// 子组件: DriftSnapshotDetail — 快照详情面板
// ═══════════════════════════════════════════════════════════════════════════

const DriftSnapshotDetail: React.FC<DriftSnapshotDetailProps> = ({
  snapshot,
  onClose,
  onNavigateToTimerange,
  onNavigateToProcessTree,
}) => {
  const windowDurationMs = snapshot.window_end_ms - snapshot.window_start_ms;

  return (
    <div className="space-y-3 rounded-lg border border-slate-300 bg-white p-4 shadow-sm">
      {/* 头部 */}
      <div className="flex items-center justify-between">
        <h4 className="text-sm font-semibold text-gray-800">快照详情</h4>
        <button onClick={onClose} className="text-xs text-gray-400 hover:text-gray-600">
          ✕ 关闭
        </button>
      </div>

      {/* 快照 ID */}
      <div className="truncate font-mono text-[10px] text-gray-400">ID: {snapshot.snapshot_id}</div>

      {/* 属性网格 */}
      <div className="grid grid-cols-2 gap-2 text-xs">
        <div>
          <div className="text-[10px] text-gray-400">Agent</div>
          <div className="text-gray-700">
            {snapshot.agent_comm} (pid:{snapshot.agent_root_pid})
          </div>
        </div>
        <div>
          <div className="text-[10px] text-gray-400">综合评分</div>
          <div className="font-mono text-gray-700">
            {(snapshot.overall_score * 100).toFixed(1)}%
            <DriftDirectionBadge direction={snapshot.direction} />
          </div>
        </div>
        <div>
          <div className="text-[10px] text-gray-400">趋势值</div>
          <div
            className={`font-mono text-gray-700 ${snapshot.trend > 0 ? 'text-red-600' : 'text-emerald-600'}`}
          >
            {snapshot.trend > 0 ? '+' : ''}
            {snapshot.trend.toFixed(3)}
          </div>
        </div>
        <div>
          <div className="text-[10px] text-gray-400">连续周期</div>
          <div className="font-mono text-gray-700">{snapshot.consecutive_periods}</div>
        </div>
        <div className="col-span-2">
          <div className="text-[10px] text-gray-400">时间窗口</div>
          <div className="font-mono text-gray-700">
            {new Date(snapshot.window_start_ms).toLocaleString()} →{' '}
            {new Date(snapshot.window_end_ms).toLocaleString()}
            <span className="ml-1 text-gray-400">
              (
              {windowDurationMs >= 1000
                ? `${(windowDurationMs / 1000).toFixed(1)}s`
                : `${windowDurationMs}ms`}
              )
            </span>
          </div>
        </div>
      </div>

      {/* 维度评分 */}
      <div>
        <div className="mb-2 text-[10px] font-medium text-gray-400">维度评分</div>
        <div className="space-y-1.5">
          {ALL_DIMENSIONS.filter((d) => snapshot.dimensions[d] !== undefined).map((dim) => (
            <DriftDimensionBar
              key={dim}
              dimension={dim}
              score={snapshot.dimensions[dim]!}
              weight={DRIFT_DIMENSION_WEIGHT[dim]}
            />
          ))}
        </div>
      </div>

      {/* 操作按钮 */}
      <div className="flex items-center gap-2 border-t border-slate-100 pt-2">
        <button
          onClick={() => onNavigateToTimerange(snapshot.window_start_ms, snapshot.window_end_ms)}
          className="rounded border border-blue-200 bg-blue-50 px-3 py-1.5 text-xs text-blue-700 transition-colors hover:bg-blue-100"
        >
          ⏱ 定位时间窗口
        </button>
        <button
          onClick={() => onNavigateToProcessTree(snapshot.agent_root_pid)}
          className="rounded border border-slate-200 bg-slate-50 px-3 py-1.5 text-xs text-slate-700 transition-colors hover:bg-slate-100"
        >
          🌳 进程树
        </button>
      </div>
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════════
// 子组件: FilterBar — 漂移筛选栏
// ═══════════════════════════════════════════════════════════════════════════

const FilterBar: React.FC<{
  min_score_filter: number;
  direction_filter: DriftDirection | null;
  availableAgents: Array<{ pid: number; comm: string }>;
  onMinScoreChange: (score: number) => void;
  onDirectionFilterChange: (direction: DriftDirection | null) => void;
}> = ({ min_score_filter, direction_filter, onMinScoreChange, onDirectionFilterChange }) => {
  const hasActiveFilters = min_score_filter > 0 || direction_filter !== null;

  return (
    <div className="space-y-2 rounded-lg border border-slate-200 bg-white p-3">
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium text-gray-500">筛选</span>
        {hasActiveFilters && (
          <button
            onClick={() => {
              onMinScoreChange(0);
              onDirectionFilterChange(null);
            }}
            className="text-[10px] text-blue-500 hover:text-blue-700"
          >
            清除全部
          </button>
        )}
      </div>

      {/* ── 最低评分 ── */}
      <div className="flex items-center gap-2">
        <span className="text-[10px] whitespace-nowrap text-gray-400">
          最低评分: {(min_score_filter * 100).toFixed(0)}%
        </span>
        <input
          type="range"
          min={0}
          max={100}
          step={5}
          value={Math.round(min_score_filter * 100)}
          onChange={(e) => onMinScoreChange(Number(e.target.value) / 100)}
          className="h-1 flex-1 accent-blue-500"
        />
      </div>

      {/* ── 方向筛选 ── */}
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="text-[10px] text-gray-400">方向:</span>
        {(['Worsening', 'Plateaued', 'Recovering', 'Uncertain'] as DriftDirection[]).map((dir) => (
          <button
            key={dir}
            onClick={() => onDirectionFilterChange(dir === direction_filter ? null : dir)}
            className={`rounded border px-2 py-0.5 text-[10px] transition-colors ${
              direction_filter === dir
                ? 'border-blue-600 bg-blue-600 text-white'
                : 'border-slate-200 text-gray-500 hover:border-slate-300'
            }`}
          >
            {DRIFT_DIRECTION_ICON[dir]} {DRIFT_DIRECTION_LABEL[dir]}
          </button>
        ))}
      </div>
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════════
// 子组件: OverviewTab — 概览视图
// ═══════════════════════════════════════════════════════════════════════════

const OverviewTab: React.FC<{
  state: AgentDriftDashboardState;
  onAgentSelect: (pid: number | null) => void;
  onSnapshotSelect: (id: string | null) => void;
  onNavigateToTimerange: (start_ms: number, end_ms: number) => void;
  onDimensionFocus: (dim: DriftDimension | null) => void;
  compact?: boolean;
}> = ({
  state,
  onAgentSelect,
  onSnapshotSelect,
  onNavigateToTimerange,
  onDimensionFocus,
  compact = false,
}) => {
  const { stats, agent_summaries, recent_snapshots, selected_snapshot_id } = state;
  const hasAgents = stats.monitored_agents > 0;
  const hasSnapshots = stats.total_snapshots > 0;

  const selectedSnapshot = useMemo(
    () => recent_snapshots.find((s) => s.snapshot_id === selected_snapshot_id) ?? null,
    [recent_snapshots, selected_snapshot_id],
  );

  return (
    <div className="space-y-4">
      {/* ── 统计卡片 ── */}
      <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
        <SummaryCard label="监控中" value={stats.monitored_agents} unit="个" />
        <SummaryCard label="已基线" value={stats.baselined_agents} unit="个" />
        <SummaryCard
          label="恶化态"
          value={stats.worsening_agents}
          unit="个"
          highlight={stats.worsening_agents > 0}
        />
        <SummaryCard
          label="告警率"
          value={(stats.alert_rate * 100).toFixed(1)}
          unit="%"
          highlight={stats.alert_rate > 0.1}
        />
      </div>

      {/* ── 空状态: 无 agent ── */}
      {!hasAgents && (
        <div className="p-6 text-center text-gray-400">
          <div className="mb-1 text-lg">📉</div>
          <div className="text-xs">AgentDriftAnalyzer 等待事件流…</div>
          <div className="mt-1 text-xs text-gray-300">
            正在收集事件以建立行为基线，需要至少 100 个事件后激活漂移检测
          </div>
        </div>
      )}

      {/* ── 空状态: 有 agent 但无漂移 ── */}
      {hasAgents && !hasSnapshots && (
        <div className="p-6 text-center text-gray-400">
          <div className="mb-1 text-lg">✅</div>
          <div className="text-xs">
            {stats.monitored_agents} 个 agent 已就绪，{stats.baselined_agents} 个已建立基线
          </div>
          <div className="mt-1 text-xs text-gray-300">所有 agent 行为稳定，未检测到显著漂移</div>
        </div>
      )}

      {hasSnapshots && (
        <>
          {/* ── 漂移最严重 Agent 排行 ── */}
          {stats.top_drifting_agents.length > 0 && (
            <div className="rounded-lg border border-slate-200 bg-white p-4">
              <h4 className="mb-3 text-xs font-semibold tracking-wide text-gray-500 uppercase">
                漂移最严重 Agent
              </h4>
              <div className="space-y-2">
                {stats.top_drifting_agents.slice(0, 5).map((agent, i) => (
                  <div key={agent.agent_root_pid} className="flex items-center gap-2 text-xs">
                    <span className="w-4 font-mono text-gray-400">{i + 1}.</span>
                    <span className="flex-1 truncate font-mono text-blue-600">
                      {agent.agent_comm}
                    </span>
                    <ScoreBar score={agent.overall_score} color="bg-slate-500" />
                    <DriftDirectionBadge direction={agent.direction} />
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* ── 六维最近均值 ── */}
          {Object.keys(stats.dimension_avg).length > 0 && (
            <div className="rounded-lg border border-slate-200 bg-white p-4">
              <h4 className="mb-3 text-xs font-semibold tracking-wide text-gray-500 uppercase">
                六维全局均值
              </h4>
              <div className="space-y-2">
                {ALL_DIMENSIONS.filter((d) => stats.dimension_avg[d] !== undefined).map((dim) => (
                  <button
                    key={dim}
                    onClick={() => onDimensionFocus(dim)}
                    className="w-full text-left"
                  >
                    <DriftDimensionBar
                      dimension={dim}
                      score={stats.dimension_avg[dim]!}
                      weight={DRIFT_DIMENSION_WEIGHT[dim]}
                      compact={compact}
                    />
                  </button>
                ))}
              </div>
            </div>
          )}

          {/* ── 最近快照预览 ── */}
          {recent_snapshots.length > 0 && (
            <div className="rounded-lg border border-slate-200 bg-white p-4">
              <h4 className="mb-3 text-xs font-semibold tracking-wide text-gray-500 uppercase">
                最近快照
              </h4>

              {/* 选中快照的详情面板 */}
              {selectedSnapshot && (
                <div className="mb-3">
                  <DriftSnapshotDetail
                    snapshot={selectedSnapshot}
                    onClose={() => onSnapshotSelect(null)}
                    onNavigateToTimerange={onNavigateToTimerange}
                    onNavigateToProcessTree={() => {
                      // 联动跳转 — 通过 agent_select 间接触发
                    }}
                  />
                </div>
              )}

              <div className="space-y-2">
                {recent_snapshots.slice(0, 5).map((snapshot) => (
                  <DriftSnapshotCard
                    key={snapshot.snapshot_id}
                    snapshot={snapshot}
                    isSelected={selected_snapshot_id === snapshot.snapshot_id}
                    onSelect={() =>
                      onSnapshotSelect(
                        selected_snapshot_id === snapshot.snapshot_id ? null : snapshot.snapshot_id,
                      )
                    }
                    onNavigateToTimerange={onNavigateToTimerange}
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

// ═══════════════════════════════════════════════════════════════════════════
// 主组件: AgentDriftDashboard
// ═══════════════════════════════════════════════════════════════════════════

/** @deprecated 阶段二实时视角（SSE 接入后迁移至运行监控区块），现无生产者，仅存档。 */
export const AgentDriftDashboard: React.FC<AgentDriftDashboardProps> = ({
  state,
  onViewModeChange,
  onAgentSelect,
  onSnapshotSelect,
  onDimensionFocus,
  onMinScoreChange,
  onDirectionFilterChange,
  onNavigateToTimerange,
  onNavigateToProcessTree,
  onNavigateToToolExecution,
  compact = false,
}) => {
  const {
    view_mode,
    stats,
    agent_summaries,
    recent_snapshots,
    selected_agent_pid,
    min_score_filter,
    direction_filter,
  } = state;

  // ── 筛选后的 agent 列表 ──
  const filteredAgents = useMemo(() => {
    return agent_summaries.filter((a) => {
      if (a.latest_overall_score < min_score_filter) return false;
      if (direction_filter && a.latest_direction !== direction_filter) return false;
      return true;
    });
  }, [agent_summaries, min_score_filter, direction_filter]);

  // ── 筛选后的快照列表 ──
  const filteredSnapshots = useMemo(() => {
    return recent_snapshots.filter((s) => {
      if (s.overall_score < min_score_filter) return false;
      if (direction_filter && s.direction !== direction_filter) return false;
      return true;
    });
  }, [recent_snapshots, min_score_filter, direction_filter]);

  return (
    <div className="space-y-4">
      {/* ═══ 顶部 Tab 导航 ═══ */}
      <div className="flex flex-wrap items-center gap-1 border-b border-slate-200 pb-2">
        {VIEW_LABELS.map(({ mode, label, icon }) => {
          const hasData =
            mode === 'overview'
              ? stats.monitored_agents > 0
              : mode === 'dimensions'
                ? Object.keys(stats.dimension_avg).length > 0
                : mode === 'agents'
                  ? agent_summaries.length > 0
                  : recent_snapshots.length > 0;

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
              {mode === 'agents' && stats.worsening_agents > 0 && (
                <span
                  className={`inline-flex h-5 w-5 items-center justify-center rounded-full text-[10px] font-bold ${
                    view_mode === mode ? 'bg-white/20 text-white' : 'bg-red-100 text-red-700'
                  }`}
                >
                  {stats.worsening_agents}
                </span>
              )}
              {mode === 'trend' && stats.threshold_breach_total > 0 && (
                <span
                  className={`inline-flex h-5 w-5 items-center justify-center rounded-full text-[10px] font-bold ${
                    view_mode === mode ? 'bg-white/20 text-white' : 'bg-orange-100 text-orange-700'
                  }`}
                >
                  {stats.threshold_breach_total}
                </span>
              )}
            </button>
          );
        })}
      </div>

      {/* ═══ 筛选栏（agents + trend 视图） ═══ */}
      {(view_mode === 'agents' || view_mode === 'trend') && (
        <FilterBar
          min_score_filter={min_score_filter}
          direction_filter={direction_filter}
          availableAgents={agent_summaries.map((a) => ({
            pid: a.agent_root_pid,
            comm: a.agent_comm,
          }))}
          onMinScoreChange={onMinScoreChange}
          onDirectionFilterChange={onDirectionFilterChange}
        />
      )}

      {/* ═══ 视图内容 ═══ */}
      <div className={compact ? 'max-h-[60vh] overflow-y-auto' : ''}>
        {/* ── 概览视图 ── */}
        {view_mode === 'overview' && (
          <OverviewTab
            state={state}
            onAgentSelect={onAgentSelect}
            onSnapshotSelect={onSnapshotSelect}
            onNavigateToTimerange={onNavigateToTimerange}
            onDimensionFocus={onDimensionFocus}
            compact={compact}
          />
        )}

        {/* ── Agent 列表视图 ── */}
        {view_mode === 'agents' && (
          <div className="space-y-3">
            {filteredAgents.length === 0 ? (
              <div className="p-6 text-center text-gray-400">
                <div className="mb-1 text-lg">🔍</div>
                <div className="text-xs">无匹配 Agent</div>
                <div className="mt-1 text-xs text-gray-300">尝试调整筛选条件</div>
              </div>
            ) : (
              filteredAgents.map((summary) => (
                <AgentDriftCard
                  key={summary.agent_root_pid}
                  summary={summary}
                  isSelected={selected_agent_pid === summary.agent_root_pid}
                  onSelect={() =>
                    onAgentSelect(
                      selected_agent_pid === summary.agent_root_pid ? null : summary.agent_root_pid,
                    )
                  }
                  onNavigateToProcessTree={onNavigateToProcessTree}
                  compact={compact}
                />
              ))
            )}
          </div>
        )}

        {/* ── 趋势视图 (占位 — P2 实现) ── */}
        {view_mode === 'trend' && (
          <div className="p-6 text-center text-gray-400">
            <div className="mb-1 text-lg">📈</div>
            <div className="text-xs">时间线漂移趋势图将在 P2 阶段实现</div>
            <div className="mt-1 text-xs text-gray-300">
              SVG/Canvas 折线图 + 阈值线 + agent 叠加对比
            </div>
          </div>
        )}

        {/* ── 维度雷达视图 (占位 — P2 实现) ── */}
        {view_mode === 'dimensions' && (
          <div className="p-6 text-center text-gray-400">
            <div className="mb-1 text-lg">🔄</div>
            <div className="text-xs">六维雷达图将在 P2 阶段实现</div>
            <div className="mt-1 text-xs text-gray-300">SVG 雷达图 + agent 覆盖层 + 基线对比</div>
          </div>
        )}
      </div>

      {/* ═══ 底部状态栏 ═══ */}
      <div className="flex items-center justify-between border-t border-slate-100 pt-2 text-xs text-gray-400">
        <span>
          {view_mode === 'overview' &&
            `${stats.monitored_agents} agents · ${stats.total_snapshots} snapshots · ${stats.threshold_breach_total} breaches`}
          {view_mode === 'agents' &&
            `${filteredAgents.length} agents${agent_summaries.length !== filteredAgents.length ? ` (共 ${agent_summaries.length})` : ''}`}
          {view_mode === 'trend' &&
            `${filteredSnapshots.length} snapshots${recent_snapshots.length !== filteredSnapshots.length ? ` (共 ${recent_snapshots.length})` : ''}`}
          {view_mode === 'dimensions' && `${Object.keys(stats.dimension_avg).length} 维度有数据`}
        </span>
        <span>{new Date(state.last_update_ms).toLocaleTimeString()}</span>
      </div>
    </div>
  );
};
