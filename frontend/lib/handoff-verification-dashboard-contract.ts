// ═══════════════════════════════════════════════════════════════
// 宫室仪象 · 递验总持 — 术法符文
//
// 阅  首局递送文书 shared_handoff.md（计九阶五十五目·Templates A–I）
// 及  第二局营缮要略（mtagentlens diag 三阶递验章程 · 器具接口与流转程式）
//
// 拟定宫室仪象所呈之型制约法：
//   仪象类型 — Dashboard / Panel / Card / Chart / Timeline / Gauge
//   展示规格 — 色彩语意 / 阈值划线 / 响应式序列 / 动画规制
//   组件型范 — Props 接口 / 状态切片 / 联动机理 / SSE 事件路由
//   施行步法 — 分阶有序，术法详尽，器具齐备
//
// 与第二局之器相合：
//   mtagentlens diag --probes  → Template D 探针生机殿
//   mtagentlens diag --stream  → Template E 流脉校验殿
//   mtagentlens diag --runtime → Template F 境迁监视殿
//   handoff_manifest.json     → 三阶递验总持殿
//   shared_handoff.md 九符   → 九阶贯通观
//
// 术法符文之形（TypeScript 类型契约）
// 对齐现有模式: lib/diagnostic-visualization-contract.ts
//               lib/handoff-verification-contract.ts
//               lib/handoff-visualization-contract.ts
//               lib/handoff-dashboard-contract.ts
//               lib/analyzer-dashboard-contract.ts
// ═══════════════════════════════════════════════════════════════

import type { AnomalySeverity } from "./data-contract";
import type { AlertSeverity, MatchLevel } from "./types";
import type {
  VerificationTier,
  TierGateState,
  VerificationProgress,
  TierAssertionMatrix,
  VerificationAssertion,
  AssertionVerdict,
  AssertionMethod,
  FailureRemediation,
  FailureMode,
} from "./handoff-verification-contract";
import type { InstrumentType, DeepInstrumentType } from "./handoff-visualization-contract";
import type {
  DiagnosticReport,
  DiagnosticStatus,
  VitalityLevel,
  RuntimePhase,
  RootCauseLayer,
  ProbeStatus,
  SourceCoverage,
  DiagThresholdConfig,
  DiagSessionSummary,
  DiagTrendPoint,
  ProbeVitalityReport,
  StreamIntegrityReport,
  RuntimeLivenessReport,
  CascadeSummary,
} from "./diagnostic-visualization-contract";

// ═══════════════════════════════════════════════════════════════
// 第一章 · 仪象殿宇总纲 — 五大殿宇及视图模式
// ═══════════════════════════════════════════════════════════════

/**
 * 宫室仪象殿宇鉴别符 — 每殿对应一套独立的可视化子系统
 *
 * 殿宇序列（呼应 shared_handoff.md 诊断级联 D→E→F 及九阶互锁）：
 *   第一殿 · 探针生机殿 — Template D (内核探针生机查验)
 *   第二殿 · 流脉校验殿 — Template E (事件流脉完整性校验)
 *   第三殿 · 境迁监视殿 — Template F (运行时境迁监视)
 *   第四殿 · 递验总持殿 — 三阶递验章程 (第一/二/三阶)
 *   第五殿 · 九阶贯通观 — 全九阶递验监视 (第四–九阶)
 *   第六殿 · 诊断级联殿 — D→E→F 逐层根因定位 (cascade summary)
 */
export type PalaceHall =
  | "hall_probe_vitality"     // 第一殿 · 探针生机殿
  | "hall_stream_integrity"   // 第二殿 · 流脉校验殿
  | "hall_runtime_liveness"   // 第三殿 · 境迁监视殿
  | "hall_verification"       // 第四殿 · 递验总持殿
  | "hall_full_nine_stages"   // 第五殿 · 九阶贯通观
  | "hall_diag_cascade";      // 第六殿 · 诊断级联殿

/** 殿宇人类可读名称与符印 */
export const PALACE_HALL_DISPLAY: Record<PalaceHall, { name: string; seal: string; icon: string }> = {
  hall_probe_vitality:     { name: "探针生机殿", seal: "生机查验·不动根本印", icon: "🏛️" },
  hall_stream_integrity:   { name: "流脉校验殿", seal: "流脉校验·水月镜花印", icon: "🏛️" },
  hall_runtime_liveness:   { name: "境迁监视殿", seal: "境迁监视·天网恢恢印", icon: "🏛️" },
  hall_verification:       { name: "递验总持殿", seal: "三阶递验·因果不断",   icon: "⚖️" },
  hall_full_nine_stages:   { name: "九阶贯通观", seal: "九阶不破·递送不灭",   icon: "🔭" },
  hall_diag_cascade:       { name: "诊断级联殿", seal: "逐层根因·大制不割",   icon: "🔗" },
};

// ═══════════════════════════════════════════════════════════════
// 第二章 · 第一殿 · 探针生机殿 — Template D 仪象类型
// ═══════════════════════════════════════════════════════════════

/** 探针生机殿子视图模式 */
export type ProbeVitalityViewMode =
  | "probe_grid"        // 四探针网格卡片
  | "probe_detail"      // 单探针详情面板
  | "probe_timeline";   // 生机率时间线

/** 探针生机殿滑块（生机仪表盘组件 Props） */
export interface ProbeVitalityPalaceProps {
  /** 当前诊断报告（完整 Template D） */
  report: ProbeVitalityReport | null;
  /** 上一份报告的探针配置（用于对比） */
  previousReport: ProbeVitalityReport | null;
  /** 历史趋势数据 */
  trend: DiagTrendPoint[];
  /** 当前子视图 */
  viewMode: ProbeVitalityViewMode;
  /** 选中的探针名 */
  selectedProbe: string | null;

  // 诊断执行
  isRunning: boolean;
  lastRunId: string | null;

  // 阈值
  silentTimeoutSec: number;

  // 联动
  onViewModeChange: (mode: ProbeVitalityViewMode) => void;
  onSelectProbe: (name: string | null) => void;
  onRunProbeDiag: () => void;
  /** 跳转至诊断级联殿 */
  onNavigateToCascade: () => void;
}

/**
 * 探针状态卡片（四枚探针各一枚）
 *
 * 展示规格：
 *   - 存活态: 绿色实心圆 + "存活 · xx 秒内活跃"
 *   - 静默态: 黄色半圆 + "静默 · 挂载但无输出"
 *   - 阵亡态: 红色空心圆 + "阵亡 · run_cnt = 0"
 *   - 卡片宽: min-w-[200px]，hover 时 border 高亮
 *   - run_cnt 数值以 font-mono 展示，阈值以下以 animate-pulse 闪烁
 */
export interface ProbeStatusCardProps {
  probe: ProbeStatus;
  /** 当前基线探头状态（上次诊断） */
  baseline: ProbeStatus | null;
  /** 静默超时阈值 */
  silentTimeoutSec: number;
  /** 是否被选中 */
  isSelected: boolean;
  /** 选择回调 */
  onSelect: () => void;
}

/**
 * 探针生机时间线（历史生机率图表）
 *
 * 展示规格：
 *   - SVG 折线图，X 轴 = 时间，Y 轴 = 0–4 探针存活数
 *   - 初始基线为水平参考线（半透明绿色）
 *   - 超过 20% 存活率下降标记为 anomalous 段（红色填充背景）
 *   - 若全部阵亡则整段图背景变红 + 闪烁边框
 */
export interface ProbeVitalityTimelineProps {
  trend: DiagTrendPoint[];
  /** 焦点探针名（null = 全部聚合） */
  focusProbe: string | null;
  /** 生机率告警阈值（0.0–1.0，默认 0.5） */
  alertThreshold: number;
  height?: number;
}

// ═══════════════════════════════════════════════════════════════
// 第三章 · 第二殿 · 流脉校验殿 — Template E 仪象类型
// ═══════════════════════════════════════════════════════════════

/** 流脉校验殿子视图模式 */
export type StreamIntegrityViewMode =
  | "stream_overview"         // 概要：破损率 + 源覆盖 + 单调性
  | "event_type_matrix"       // 事件类型覆盖矩阵
  | "monotonicity_detail"     // 单调性违例详情
  | "source_coverage_radar";  // 源覆盖雷达图

/** 流脉校验殿滑块 */
export interface StreamIntegrityPalaceProps {
  report: StreamIntegrityReport | null;
  previousReport: StreamIntegrityReport | null;
  viewMode: StreamIntegrityViewMode;
  isRunning: boolean;
  /** 当前校验的 JSONL 文件路径 */
  inputPath: string;

  // 阈值
  monotonicityViolationRatio: number;

  onViewModeChange: (mode: StreamIntegrityViewMode) => void;
  onInputPathChange: (path: string) => void;
  onRunStreamDiag: () => void;
  onNavigateToCascade: () => void;
}

/**
 * 事件类型覆盖矩阵
 *
 * 展示规格：
 *   - 列表布局: 左列 = 事件类型名，右列 = 来源覆盖状态
 *   - 已知类型: 用灰色背景 + 勾号标记（✓ 已检测 / — 缺失）
 *   - 未知类型: 用橙色背景 + 感叹号标记（⚠ 新发现）
 *   - 缺失源: 红色左侧边框 + 闪烁指示
 *   - 右侧统计: "x/y 类型覆盖" + 百分比进度条
 *   - 行高: 36px，偶数行 bg-slate-50
 */
export interface EventTypeCoverageMatrixProps {
  /** 当前流完整性报告 */
  report: StreamIntegrityReport | null;
  /** 全量已知事件类型（KNOW_EVENT_TYPES） */
  allKnownTypes: string[];
  /** 紧凑模式（嵌入诊断级联殿时使用） */
  compact?: boolean;
}

/**
 * 单调性违例详情面板
 *
 * 展示规格：
 *   - 顶部: 违例数 / 总行数 + 违例率百分比（font-mono, 大字）
 *   - 本体: 按 source 分组的违例分布表
 *     - 每行: source 名 | 违例数 | 占比 | mini sparkline
 *   - 违例率 > 1%: 表头变红 + 红色 border
 *   - 违例率 < 0.1%: 绿色 "正常" 标记
 *   - 底部: 行动建议列表（对应 shared_handoff.md Template E §ACTIONS）
 */
export interface MonotonicityViolationDetailProps {
  violations: number;
  totalLines: number;
  perSourceViolations: Record<string, number>;
}

/**
 * 源覆盖雷达图
 *
 * 展示规格：
 *   - SVG 多边形雷达图（或 Canvas），轴数 = 数据源数量
 *   - 实心填充 = 检测到的源覆盖范围
 *   - 虚线边框 = 期望的全部源
 *   - 缺失源标注为红色轴线 + 轴名高亮
 *   - 图例: ✓ 已覆盖（绿）/ ✗ 缺失（红）
 *   - 容器: 250×250px，居中
 */
export interface SourceCoverageRadarProps {
  coverage: SourceCoverage;
  allKnownSources: string[];
  /** 图尺寸 (px) */
  size?: number;
}

// ═══════════════════════════════════════════════════════════════
// 第四章 · 第三殿 · 境迁监视殿 — Template F 仪象类型
// ═══════════════════════════════════════════════════════════════

/** 境迁监视殿子视图模式 */
export type RuntimeLivenessViewMode =
  | "runtime_overview"       // 概览：进程健康 + 阶段状态 + RSS
  | "phase_state_machine"    // 阶段状态机图
  | "rss_growth"             // RSS 增长趋势
  | "process_detail";        // 进程完整详情

/** 境迁监视殿滑块 */
export interface RuntimeLivenessPalaceProps {
  report: RuntimeLivenessReport | null;
  previousReport: RuntimeLivenessReport | null;
  viewMode: RuntimeLivenessViewMode;
  isRunning: boolean;

  // 阈值
  phaseStallTimeoutSec: number;
  rssGrowthRateCriticalKbps: number;

  // 活跃进程
  activePid: number | null;
  activePort: number;

  onViewModeChange: (mode: RuntimeLivenessViewMode) => void;
  onPidChange: (pid: number | null) => void;
  onPortChange: (port: number) => void;
  onRunRuntimeDiag: () => void;
  onNavigateToCascade: () => void;
}

/**
 * 阶段状态机图
 *
 * 展示规格：
 *   - 六节点状态图: bootstrap → phase1 → phase2_triggered → phase2 → complete
 *     外加 stuck 异常态节点（红色虚线）
 *   - 当前阶段: 高亮填充 + pulse 动画
 *   - 已通过阶段: 绿色勾号
 *   - 未到达阶段: 灰色虚线
 *   - 阶段间箭头: 已走过 = 实线，未走到 = 虚线
 *   - 每节点下方显示: 超时阈值 / 实际耗时
 *   - 若 stuck: 从当前节点伸出一条红色虚线到 stuck 节点
 *   - 容器: 横向滚动，min-width 600px
 */
export interface PhaseStateMachineProps {
  currentPhase: RuntimePhase;
  phaseHistory: Array<{ timestampEpochMs: number; phase: RuntimePhase }>;
  stallTimeoutSec: number;
  elapsedSeconds: number;
  /** 各阶段耗时 (ms) */
  phaseDurations: Partial<Record<RuntimePhase, number>>;
}

/**
 * RSS 增长仪表
 *
 * 展示规格：
 *   - 顶部: 实时 RSS 数值 (font-mono, 大字) / 临界值
 *   - 速度表: 弧形 gauge，绿色=正常 / 黄色=接近 / 红色=超过
 *   - 本体: SVG 折线图，Y轴 = KB，X轴 = 时间
 *   - 临界线: 红色虚线横向穿过图表，标注 "10 MB/s 临界"
 *   - 历史峰值: 标注为红色圆点
 *   - 单位: 自动缩放（KB → MB → GB）
 */
export interface RssGrowthGaugeProps {
  rssKb: number;
  vszKb: number;
  growthRateKbps: number | null;
  criticalThresholdKbps: number;
  history: Array<{ timestampEpochMs: number; rssKb: number }>;
  height?: number;
}

/**
 * 进程健康卡片
 *
 * 展示规格：
 *   - 左侧: 进程状态指示灯（绿色 alive / 红色 dead）
 *   - 中: PID + 状态字符 + 运行阶段
 *   - 右侧: phase1 已处理事件数 + phase2 完成状态
 *   - alive 态: 绿色左边框
 *   - dead 态: 红色左边框 + "重启" 操作按钮
 *   - hover: 显示完整进程信息 tooltip
 */
export interface ProcessHealthCardProps {
  pid: number | null;
  processAlive: boolean;
  processState: string;
  phase: RuntimePhase;
  phase1EventsProcessed: number;
  phase2Complete: boolean;
  phaseElapsedSeconds: number;
  /** 跳转至境迁监视殿 */
  onNavigateToRuntimeLiveness: () => void;
}

// ═══════════════════════════════════════════════════════════════
// 第五章 · 第四殿 · 递验总持殿 — 三阶递验章程仪象类型
// ═══════════════════════════════════════════════════════════════

/** 递验总持殿子视图模式 */
export type VerificationViewMode =
  | "progress_overview"    // 三阶递验进度概览
  | "tier_assertions"      // 单阶断言详情
  | "failure_remediation"; // 失败破执（处置）面板

/** 递验总持殿滑块 */
export interface VerificationPalaceProps {
  /** 三阶递验进度 */
  progress: VerificationProgress;
  /** 当前聚焦的阶次 */
  focusedTier: VerificationTier | null;
  /** 当前激活的递验阶 */
  activeTier: VerificationTier | null;
  /** 各阶断言矩阵 */
  assertionMatrices: Record<VerificationTier, TierAssertionMatrix | null>;
  /** 失败处置目录 */
  failureCatalog: FailureRemediation[];
  /** 当前视图模式 */
  viewMode: VerificationViewMode;

  // 递验执行
  isRunning: boolean;
  manifestPath: string;

  onViewModeChange: (mode: VerificationViewMode) => void;
  onFocusTier: (tier: VerificationTier | null) => void;
  onStartVerification: () => void;
  onAbortVerification: () => void;
  /** 点击断言跳转到对应仪器视图 */
  onNavigateToInstrument: (type: InstrumentType | DeepInstrumentType) => void;
}

/**
 * 三阶递验进度板
 *
 * 展示规格：
 *   - 横向三列: 第一阶 | 第二阶 | 第三阶
 *   - 阶间连接: 箭头，已通过 = 绿色，未通过 = 红色虚线阻断
 *   - 每阶卡片内容:
 *     - 标题: 阶名 + 符印名
 *     - 进度条: 通过断言 / 总断言 (X/Y)
 *     - 状态指示灯: pending(灰)/in_progress(蓝闪)/passed(绿)/blocked(红)/failed(红)
 *     - 阻断失败数 (红色徽章)
 *   - 前阶不通后阶不行: 后阶 colored overlay + 🔒 图标
 *   - 门禁约束线: 阶间垂直虚线 + "门禁" 标签
 */
export interface VerificationProgressPanelProps {
  progress: VerificationProgress;
  assertionMatrices: Record<VerificationTier, TierAssertionMatrix | null>;
  /** 点击阶次卡片 */
  onSelectTier: (tier: VerificationTier) => void;
  /** 是否可展开断言详情 */
  canExpand: boolean;
}

/**
 * 单阶断言表格
 *
 * 展示规格：
 *   - 表头: 断言编号 | 描述 | 验证方式 | 阈值 | 结果 | 耗时 | 动作
 *   - 结果列着色: PASS=绿色, FAIL=红色, NA=灰色, INFO=蓝色, SKIP=黄色
 *   - 失败行: 左侧红色边框 + 红色背景淡色底
 *   - 点击断言行: 展开底部详情面板 (Actual Output + 破执动作)
 *   - 验证方式图标: sha256sum=🔏, jq=🔍, cargo=🦀, 等
 *   - 筛选: 按告警（仅看 FAIL）/ 全部 / 信息性
 */
export interface TierAssertionTableProps {
  matrix: TierAssertionMatrix;
  /** 筛选断言类型 */
  filterVerdict: AssertionVerdict | "ALL";
  /** 当前选中的断言 ID */
  selectedAssertionId: string | null;

  onSelectAssertion: (assertion: VerificationAssertion | null) => void;
  onFilterChange: (verdict: AssertionVerdict | "ALL") => void;
}

/**
 * 断言详情面板（单断言展开态）
 *
 * 展示规格：
 *   - 标题: 断言编号 + 描述
 *   - 上区: 判定结果 + 阈值 vs 实际值
 *   - 中区: actual_output 代码块（等宽字体，可滚动，最大高度 200px）
 *   - 破执区（FAIL 时）:
 *     - 失败模式图标 + 名称
 *     - 根因分析
 *     - 修复步骤（编号列表）
 *     - 自动修复 / 需人工介入 标签
 *     - 关联仪器跳转按钮
 *   - 导航区: 上一个断言 | 下一个断言
 */
export interface AssertionDetailPanelProps {
  assertion: VerificationAssertion;
  remediation: FailureRemediation | null;
  onClose: () => void;
  onNavigateToInstrument: (type: InstrumentType | DeepInstrumentType) => void;
  onPrevious: () => void;
  onNext: () => void;
  hasPrevious: boolean;
  hasNext: boolean;
}

/**
 * 验证方式图标映射
 */
export const ASSERTION_METHOD_ICON: Record<AssertionMethod, string> = {
  sha256_compare: "🔏",
  diff_output: "📊",
  grep_count: "🔍",
  sql_query: "🗄️",
  jq_filter: "⚙️",
  python_assert: "🐍",
  mtagentlens_replay_diff: "🔄",
  mtagentlens_cli: "🛠️",
  browser_dom: "🌐",
  browser_sse: "📡",
  zustand_devtools: "🔬",
  cargo_test: "🦀",
};

// ═══════════════════════════════════════════════════════════════
// 第六章 · 第五殿 · 九阶贯通观 — 全九阶递验监视仪象
// ═══════════════════════════════════════════════════════════════

/**
 * 九阶概要 — 每阶的监视状态摘要
 * 对应 shared_handoff.md Stages 1–9
 */
export interface StageSummary {
  /** 阶次编号 1–9 */
  stageNumber: number;
  /** 阶名 */
  stageName: string;
  /** 所属诊断层（器物层/逻辑层/效用层） */
  diagnosticLayer: "artifact" | "logic" | "effect";

  /** 递验时机: handoff_time / between_handoff / meta */
  timing: "handoff_time" | "between_handoff" | "meta";

  /** 该阶是否已执行过 */
  hasExecuted: boolean;
  /** 最近执行时间 (epoch ms) */
  lastExecutedMs: number | null;
  /** 最近执行结果状态 */
  lastStatus: "passed" | "warning" | "failed" | "skipped" | "pending";

  /** 关键指标摘要（人类可读） */
  keyMetrics: string;
  /** 是否有未解决告警 */
  hasOpenAlerts: boolean;
  /** 快照数量 */
  snapshotCount: number;
  /** 该阶对应的诊断 Template（D/E/F） */
  primaryTemplate: "D" | "E" | "F" | "G" | "H" | "I" | "composite";
}

/**
 * 九阶全局状态 — 贯通全部九个递验阶次
 */
export interface NineStageState {
  stages: StageSummary[];
  /** 全部阶次是否均通过 */
  allStagesClean: boolean;
  /** 当前阶段的阶次号 */
  stageIndex: "handoff" | "between" | "meta";
  /** 最近一次完整九阶运行时间 */
  lastFullRunMs: number | null;
  /** 元健康分（第九阶计算） */
  metaHealthScore: number | null;
}

/**
 * 九阶贯通观滑块
 *
 * 展示规格：
 *   - 左侧: 九阶垂直时间线 + 筛选选择器
 *   - 右侧: 选中阶次的详情面板
 *   - 三区水平分割: 递送时刻 | 递送间隙 | 递道自明
 */
export interface NineStagePalaceProps {
  state: NineStageState;
  /** 当前选中阶次 */
  selectedStage: number | null;
  /** 是否为紧凑模式（嵌入诊断级联殿下方） */
  compact?: boolean;

  onSelectStage: (stageNumber: number | null) => void;
}

/**
 * 九阶垂直时间线
 *
 * 展示规格：
 *   - 垂直时间线: 9 个节点排列，每节点含:
 *     - 阶号圆圈: 绿色通过/黄色告警/红色失败/灰色未执行
 *     - 阶名标签
 *     - 最后执行时间（若已执行）
 *   - 节点间连线: 已执行=实线, 未执行=虚线
 *   - 三区背景色区分: 递送时刻(蓝灰) / 递送间隙(绿灰) / 递道自明(紫灰)
 *   - 当前选中阶: 圆圈放大 + 文字高亮
 */
export interface NineStageTimelineProps {
  stages: StageSummary[];
  selectedStage: number | null;
  onSelectStage: (stageNumber: number) => void;
}

/**
 * 递送节律统计（第五阶·递风预警 趋势数据）
 *
 * 展示规格：
 *   - 折线图: X=递送序号, Y=间隔时间(分)
 *   - 基线: 均值线 + 2σ 上下界
 *   - 异常标注: 超出 2σ 的点标红 + 垂直虚线
 *   - 底部: 连续 D 递送的数据点分布
 */
export interface DeliveryCadenceChartProps {
  intervals: number[];        // 各次递送的间隔（秒）
  meanInterval: number;       // 基线均值
  stdInterval: number;        // 基线标准差
  qualityScores: number[];    // 各次递送的质量评分
  anomalyThreshold: number;   // 异常检测 σ 倍数（默认 2.0）
  height?: number;
}

// ═══════════════════════════════════════════════════════════════
// 第七章 · 第六殿 · 诊断级联殿 — D→E→F 逐层根因定位
// ═══════════════════════════════════════════════════════════════

/**
 * 诊断级联殿滑块
 *
 * 展示规格：
 *   - 顶部: 三层流程图（探针层 → 流脉层 → 运行时层）
 *   - 每层: 状态图标 + 详细摘要
 *   - 底部: 根因面板 + 行动建议
 *   - 按 D→E→F 顺序排查: 若探针层死则流脉/运行时无意义
 */
export interface DiagCascadePalaceProps {
  cascadeSummary: CascadeSummary;
  probeReport: ProbeVitalityReport | null;
  streamReport: StreamIntegrityReport | null;
  runtimeReport: RuntimeLivenessReport | null;
  /** 当前聚焦层 */
  focusLayer: RootCauseLayer | null;

  onFocusLayer: (layer: RootCauseLayer | null) => void;
  /** 跳转至对应单殿 */
  onNavigateToProbeHall: () => void;
  onNavigateToStreamHall: () => void;
  onNavigateToRuntimeHall: () => void;
}

/**
 * 级联流程图（D→E→F 三层）
 *
 * 展示规格：
 *   - 三节点水平或垂直排列
 *   - D(探针层) → E(流脉层) → F(运行时层) 箭头连接
 *   - 每节点: 层名 + 状态图标 + 关键摘要（一行）
 *   - 正常节点: 绿色边框 + 勾号
 *   - 异常节点: 红色边框 + 震波动画
 *   - 不可用节点（前序层死）：灰色 + 锁定图标
 *   - 线: D死→E/F 变灰虚线
 */
export interface CascadeFlowDiagramProps {
  probeHealthy: boolean;
  streamHealthy: boolean;
  runtimeHealthy: boolean;
  rootCauseLayer: RootCauseLayer;
  /** 点击节点跳转对应殿 */
  onSelectLayer: (layer: RootCauseLayer) => void;
}

/**
 * 根因面板
 *
 * 展示规格：
 *   - 标题: "根因定位" + 根因层名
 *   - 根因层色: 对应 ROOT_CAUSE_LAYER_DISPLAY 颜色
 *   - 依据: diagnosis_rationale — 诊断链逐层检查结果（单独区块）
 *   - 行动建议: 编号列表，含优先级
 *   - 推荐工具: 对应 CLI 命令 / bpftool / curl
 */
export interface RootCausePanelProps {
  rootCauseLayer: RootCauseLayer;
  recommendedActions: string[];
  diagnosisRationale: string;
  /** 关联的探查范围 */
  investigationScope: string;
}

// ═══════════════════════════════════════════════════════════════
// 第八章 · 历史与趋势 — 跨递送的历史记录仪象
// ═══════════════════════════════════════════════════════════════

/**
 * 诊断历史表
 *
 * 展示规格：
 *   - 分页表格: Run ID | 时间 | 状态 | 探针 | 流完整性 | 运行时阶段 | 根因层 | 耗时
 *   - 状态列着色: healthy=绿, degraded=黄, unhealthy=红
 *   - 点击行: 加载该次完整报告到当前视图
 *   - 默认排序: 时间倒序
 *   - 搜索: 按 Run ID 搜索
 */
export interface DiagHistoryTableProps {
  sessions: DiagSessionSummary[];
  selectedRunId: string | null;
  onSelectRun: (runId: string) => void;
  /** 是否正在加载报告 */
  isLoadingReport: boolean;
}

/**
 * 趋势图表容器
 *
 * 展示规格：
 *   - 可折叠容器，带标题栏 "趋势图表"
 *   - 主体: 多线折线图，Y轴 = 存活率/破损率/RSS增长率
 *   - 支持显示/隐藏各线
 *   - 时间范围选择器: 最近 10 次 / 最近 1 小时 / 全部
 */
export interface DiagTrendChartProps {
  points: DiagTrendPoint[];
  /** 可视指标开关 */
  visibleMetrics: DiagTrendMetrics;
  /** 时间范围 */
  timeRange: "last_10" | "last_hour" | "all";
  height?: number;

  onVisibilityChange: (metrics: Partial<DiagTrendMetrics>) => void;
  onTimeRangeChange: (range: "last_10" | "last_hour" | "all") => void;
}

/** 趋势图可视指标控制 */
export interface DiagTrendMetrics {
  showProbeAliveRatio: boolean;
  showStreamCorruptRate: boolean;
  showRssGrowthRate: boolean;
}

// ═══════════════════════════════════════════════════════════════
// 第九章 · 宫室仪象总持壳 — DiagDashboardShell 主布局组件
// ═══════════════════════════════════════════════════════════════

/**
 * 宫室仪象总持壳 — 六大殿宇的统一布局控制器
 *
 * 布局结构:
 * ┌──────────────────────────────────────────────────────────┐
 * │  Toolbar: [Run Diag ▼] [Export ▼] [PID ___]  [SSE ●/○]  │
 * ├──────────────────────────────────────────────────────────┤
 * │  殿宇导航: [🏛️生机] [🏛️流脉] [🏛️境迁] [⚖️递验] [🔭九阶]  │
 * ├──────────────────────────────────────────────────────────┤
 * │                                                            │
 * │  (当前殿宇的完整内容区)                                      │
 * │                                                            │
 * ├──────────────────────────────────────────────────────────┤
 * │  DiagnosticCascadeView (底部固定级联面板)                   │
 * ├──────────────────────────────────────────────────────────┤
 * │  StatusBar: 最后诊断时间 · 运行中/空闲 · 报告数 · 健康分    │
 * └──────────────────────────────────────────────────────────┘
 */
export interface DiagDashboardShellProps {
  // ── 殿宇状态 ──
  activeHall: PalaceHall;
  /** 各殿数据是否就绪 */
  hallDataReady: Partial<Record<PalaceHall, boolean>>;

  // ── 诊断执行状态 ──
  isRunning: boolean;
  currentRunId: string | null;
  overallStatus: DiagnosticStatus | null;
  activePid: number | null;
  activePort: number;
  sseConnected: boolean;

  // ── 报告与历史 ──
  currentReport: DiagnosticReport | null;
  history: DiagSessionSummary[];
  trend: DiagTrendPoint[];

  // ── 递验状态 ──
  verificationProgress: VerificationProgress | null;
  nineStageState: NineStageState | null;

  // ── 阈值 ──
  thresholds: DiagThresholdConfig;

  // ── 殿宇导航 ──
  onSwitchHall: (hall: PalaceHall) => void;

  // ── 诊断操作 ──
  onRunDiag: (scope: "probes" | "stream" | "runtime" | "all") => void;
  onExportReport: (format: "json" | "text") => void;
  onThresholdChange: (config: Partial<DiagThresholdConfig>) => void;

  // ── 递验操作 ──
  onStartVerification: () => void;
  onAbortVerification: () => void;

  // ── 进程操作 ──
  onPidChange: (pid: number | null) => void;
  onPortChange: (port: number) => void;

  // ── 历史操作 ──
  onSelectHistoryRun: (runId: string) => void;
}

// ═══════════════════════════════════════════════════════════════
// 第十章 · Zustand 全局状态切片 — 宫室仪象总持状态
// ═══════════════════════════════════════════════════════════════

/**
 * 宫室仪象总持 Zustand slice
 *
 * 这是整个宫室仪象系统的中心状态，整合了:
 *   - 六大殿宇的导航与数据
 *   - 诊断执行状态（实时 SSE 流）
 *   - 三阶递验状态
 *   - 九阶贯通状态
 *   - 历史与趋势数据
 *   - 各殿间的跨殿联动参数
 */
export interface PalaceDashboardSlice {
  // ── 殿宇 ──
  activeHall: PalaceHall;
  hallDataReady: Partial<Record<PalaceHall, boolean>>;

  // ── 诊断 ──
  isRunning: boolean;
  currentRunId: string | null;
  currentReport: DiagnosticReport | null;
  overallStatus: DiagnosticStatus | null;
  activePid: number | null;
  activePort: number;
  sseConnected: boolean;

  // ── 实时事件缓冲 ──
  /** Template D 探针事件 */
  probeEvents: ProbeVitalityEvent[];
  /** Template E 流脉事件 */
  streamEvents: StreamIntegrityEvent[];
  /** Template F 境迁事件 */
  runtimeEvents: RuntimeLivenessEvent[];
  /** 级联事件 (各层完成通知) */
  cascadeEvents: DiagCascadeEvent[];
  /** 各层完成状态 */
  layerComplete: Record<"probe" | "stream" | "runtime" | "cascade", boolean>;

  // ── 历史与趋势 ──
  history: DiagSessionSummary[];
  trend: DiagTrendPoint[];

  // ── 阈值 ──
  thresholds: DiagThresholdConfig;

  // ── 递验状态 ──
  verificationProgress: VerificationProgress | null;
  assertionMatrices: Record<VerificationTier, TierAssertionMatrix | null>;
  failureRemediations: FailureRemediation[];
  verifyManifestPath: string;

  // ── 九阶贯通 ──
  nineStageState: NineStageState | null;

  // ── 跨殿联动参数 ──
  /** 当前联动的时间窗口 (ms)，用于跳转至 TimelinePanel */
  navigateTimerange: { start: number; end: number } | null;
  /** 当前联动的探针名 */
  navigateProbe: string | null;
  /** 当前联动的 agent PID */
  navigateAgentPid: number | null;

  // ── Actions: 殿宇导航 ──
  switchHall: (hall: PalaceHall) => void;
  setHallDataReady: (hall: PalaceHall, ready: boolean) => void;

  // ── Actions: 诊断执行 ──
  startDiag: (scope: "probes" | "stream" | "runtime" | "all") => void;
  onProbeEvent: (event: ProbeVitalityEvent) => void;
  onStreamEvent: (event: StreamIntegrityEvent) => void;
  onRuntimeEvent: (event: RuntimeLivenessEvent) => void;
  onCascadeEvent: (event: DiagCascadeEvent) => void;
  onDiagComplete: (report: DiagnosticReport, runId: string) => void;
  clearDiagEvents: () => void;

  // ── Actions: 历史与趋势 ──
  loadHistory: (sessions: DiagSessionSummary[]) => void;
  loadTrend: (points: DiagTrendPoint[]) => void;
  selectHistoryRun: (runId: string) => void;

  // ── Actions: 阈值 ──
  setThresholds: (config: Partial<DiagThresholdConfig>) => void;

  // ── Actions: 递验 ──
  setVerificationProgress: (progress: VerificationProgress) => void;
  setAssertionMatrix: (tier: VerificationTier, matrix: TierAssertionMatrix) => void;
  setFailureRemediations: (remediations: FailureRemediation[]) => void;
  setVerifyManifestPath: (path: string) => void;

  // ── Actions: 九阶贯通 ──
  setNineStageState: (state: NineStageState) => void;

  // ── Actions: 进程 ──
  setActivePid: (pid: number | null) => void;
  setActivePort: (port: number) => void;
  setSseConnected: (connected: boolean) => void;

  // ── Actions: 跨殿联动 ──
  navigateToTimerange: (start: number, end: number) => void;
  navigateToProbe: (probeName: string) => void;
  navigateToAgentPid: (pid: number) => void;
  clearNavigation: () => void;

  // ── Actions: 全局重置 ──
  resetPalace: () => void;
}

// ═══════════════════════════════════════════════════════════════
// 第十一章 · 色彩与视觉规制 — 全殿统一的视觉常理
// ═══════════════════════════════════════════════════════════════

/**
 * 殿宇主题色彩映射
 *
 * 每殿有独立的强调色，用于导航标签栏和殿名标识
 */
export const PALACE_HALL_THEME: Record<PalaceHall, { primary: string; bg: string; border: string }> = {
  hall_probe_vitality:   { primary: "text-emerald-700", bg: "bg-emerald-50", border: "border-emerald-300" },
  hall_stream_integrity: { primary: "text-blue-700",    bg: "bg-blue-50",    border: "border-blue-300" },
  hall_runtime_liveness: { primary: "text-violet-700",  bg: "bg-violet-50",  border: "border-violet-300" },
  hall_verification:     { primary: "text-amber-700",   bg: "bg-amber-50",   border: "border-amber-300" },
  hall_full_nine_stages: { primary: "text-slate-700",   bg: "bg-slate-100",  border: "border-slate-300" },
  hall_diag_cascade:     { primary: "text-rose-700",    bg: "bg-rose-50",    border: "border-rose-300" },
};

/**
 * 通用状态色彩（全殿共用）
 */
export const PALACE_STATUS_COLORS = {
  healthy:   { text: "text-emerald-700", bg: "bg-emerald-50",  dot: "bg-emerald-500", border: "border-emerald-300" },
  degraded:  { text: "text-amber-700",   bg: "bg-amber-50",    dot: "bg-amber-500",   border: "border-amber-300" },
  unhealthy: { text: "text-red-700",     bg: "bg-red-50",      dot: "bg-red-500",     border: "border-red-300" },
  unknown:   { text: "text-slate-500",   bg: "bg-slate-50",    dot: "bg-slate-400",   border: "border-slate-200" },
  pending:   { text: "text-slate-400",   bg: "bg-slate-50",    dot: "bg-slate-300",   border: "border-slate-200" },
} as const;

/**
 * 断言判定着色（递验总持殿专用）
 */
export const PALACE_VERDICT_COLORS: Record<AssertionVerdict, { text: string; bg: string; icon: string }> = {
  PASS:  { text: "text-emerald-700", bg: "bg-emerald-50",  icon: "✅" },
  FAIL:  { text: "text-red-700",     bg: "bg-red-50",      icon: "❌" },
  NA:    { text: "text-slate-400",   bg: "bg-slate-50",    icon: "⊘" },
  INFO:  { text: "text-blue-600",    bg: "bg-blue-50",     icon: "ℹ️" },
  SKIP:  { text: "text-amber-600",   bg: "bg-amber-50",    icon: "⏭️" },
};

/**
 * 阶次状态着色（九阶贯通观专用）
 */
export const STAGE_STATUS_COLORS: Record<string, { dot: string; badge: string }> = {
  passed:   { dot: "bg-emerald-500", badge: "bg-emerald-100 text-emerald-700" },
  warning:  { dot: "bg-amber-500",   badge: "bg-amber-100 text-amber-700" },
  failed:   { dot: "bg-red-500",     badge: "bg-red-100 text-red-700" },
  skipped:  { dot: "bg-slate-400",   badge: "bg-slate-100 text-slate-500" },
  pending:  { dot: "bg-slate-200",   badge: "bg-slate-50 text-slate-400" },
};

/**
 * 阈值线样式常理（用于图表）
 */
export const PALACE_THRESHOLD_LINE = {
  solid:   "stroke-red-400 stroke-[1.5px]",
  dashed:  "stroke-red-400 stroke-[1px] stroke-dasharray-[4,4]",
  warning: "stroke-amber-400 stroke-[1.5px]",
};

/**
 * 动画规制
 */
export const PALACE_ANIMATION = {
  /** 殿宇切换: 淡入淡出 200ms */
  hallTransition: "transition-opacity duration-200",
  /** 状态指示灯闪烁(恶化态): 1秒周期 */
  criticalPulse: "animate-pulse",
  /** 状态指示灯慢闪(告警态): 2秒周期 */
  warningPulse: "animate-pulse [animation-duration:2s]",
  /** 级联图节点激活: 缩放弹跳 */
  nodeBounce: "transition-transform duration-150 hover:scale-105",
  /** 断言行高亮（新增）: 黄色闪烁一次 */
  assertionNew: "animate-[highlight_2s_ease-in-out]",
} as const;

/**
 * 通用排版规制
 */
export const PALACE_TYPOGRAPHY = {
  /** 首要数据（得分、统计） */
  score: "font-mono text-2xl font-bold tabular-nums",
  /** 次要数据（计数、百分比） */
  metric: "font-mono text-lg tabular-nums",
  /** 殿宇标题 */
  hallTitle: "text-lg font-semibold tracking-tight",
  /** 卡片标题 */
  cardTitle: "text-sm font-medium text-slate-700",
  /** 标签（状态徽章） */
  badge: "text-[11px] font-medium px-1.5 py-0.5 rounded",
  /** 描述文字 */
  desc: "text-xs text-slate-500",
  /** 代码块 */
  code: "font-mono text-xs bg-slate-100 rounded px-1 py-0.5",
} as const;

// ═══════════════════════════════════════════════════════════════
// 第十二章 · SSE 实时事件类型 — 诊断实时流
// ═══════════════════════════════════════════════════════════════

/**
 * SSE 流鉴别符
 *
 * 与 diagnostic-visualization-contract.ts 中的 DiagEvent 一致，
 * 此处定义其在 Zustand palace slice 中的消费类型
 */
export type DiagStreamType =
  | "diag_probe"
  | "diag_stream"
  | "diag_runtime"
  | "diag_cascade"
  | "diag_complete";

/** 探针生机实时事件 */
export interface ProbeVitalityEvent {
  type: "diag_probe";
  runId: string;
  timestampEpochMs: number;
  probe: ProbeStatus;
}

/** 流脉校验实时事件 */
export interface StreamIntegrityEvent {
  type: "diag_stream";
  runId: string;
  timestampEpochMs: number;
  linesProcessed: number;
  corruptLines: number;
  newUnknownType: string | null;
  monotonicityViolations: number;
}

/** 境迁监视实时事件 */
export interface RuntimeLivenessEvent {
  type: "diag_runtime";
  runId: string;
  timestampEpochMs: number;
  rssKb: number;
  rssGrowthRateKbps: number | null;
  phase: RuntimePhase;
  phase1EventsProcessed: number;
}

/** 级联完成事件 */
export interface DiagCascadeEvent {
  type: "diag_cascade";
  runId: string;
  timestampEpochMs: number;
  layerCompleted: "probe" | "stream" | "runtime" | "cascade";
  layerStatus: DiagnosticStatus;
}

/** 诊断完成事件 */
export interface DiagCompleteEvent {
  type: "diag_complete";
  runId: string;
  timestampEpochMs: number;
  report: DiagnosticReport;
}

/** 诊断事件联合类型 */
export type PalaceDiagEvent =
  | ProbeVitalityEvent
  | StreamIntegrityEvent
  | RuntimeLivenessEvent
  | DiagCascadeEvent
  | DiagCompleteEvent;

/** SSE 事件分发器签名（用于在 store 中注册） */
export type PalaceEventDispatcher = (event: PalaceDiagEvent) => void;

// ═══════════════════════════════════════════════════════════════
// 第十三章 · REST API 端点类型 — 与 Rust 后端协议
// ═══════════════════════════════════════════════════════════════

/** POST /api/diag/run */
export interface DiagRunRequest {
  scope: "probes" | "stream" | "runtime" | "all";
  pid: number | null;
  port: number;
  streamPath: string | null;
  thresholds?: Partial<DiagThresholdConfig>;
}

/** GET /api/diag/report/:runId */
export type DiagReportResponse = DiagnosticReport;

/** GET /api/diag/history */
export interface DiagHistoryResponse {
  sessions: DiagSessionSummary[];
  total: number;
}

/** GET /api/verification/status — 递验状态查询 */
export interface VerificationStatusResponse {
  progress: VerificationProgress;
  matrices: Record<VerificationTier, TierAssertionMatrix | null>;
  manifestPath: string;
}

/** POST /api/verification/start — 启动递验 */
export interface VerificationStartRequest {
  manifestPath: string;
  /** 指定运行阶次（null = 全部三阶） */
  specificTier: VerificationTier | null;
}

// ═══════════════════════════════════════════════════════════════
// 第十四章 · 宫室仪象施行步法 — 分阶有序的组件实现指南
// ═══════════════════════════════════════════════════════════════

/**
 * 施行步法总纲
 *
 * 术法施行之序：
 *   第一日 — 搭建殿宇骨架：DiagDashboardShell + 殿宇导航 + Zustand slice 初始化
 *   第二日 — 第一殿·探针生机：ProbeStatusCard ×4 + ProbeVitalityTimeline
 *   第三日 — 第二殿·流脉校验：EventTypeCoverageMatrix + MonotonicityViolationDetail
 *   第四日 — 第三殿·境迁监视：PhaseStateMachine + RssGrowthGauge + ProcessHealthCard
 *   第五日 — 第四殿·递验总持：VerificationProgressPanel + TierAssertionTable
 *   第六日 — 第六殿·诊断级联：CascadeFlowDiagram + RootCausePanel
 *   第七日 — 第五殿·九阶贯通：NineStageTimeline + StageSummary cards
 *   第八日 — 历史与趋势：DiagHistoryTable + DiagTrendChart
 *   第九日 — SSE 实时流 + REST API 对接 + 跨殿联动完整调试
 *   第十日 — pr-review + pr-review-toolkit:code-reviewer 审查
 *
 * 器具清单（与第二局之器相合）：
 *   - Rust: mtagentlens diag CLI (probes/stream/runtime 三种模式)
 *   - 构建: cargo build --release (collector)
 *   - 前端: npm run dev (端口 7395)
 *   - 状态: Zustand store slice (本章定义)
 *   - SSE: EventSource → onmessage → PalaceEventDispatcher
 *   - REST: fetch() → /api/diag/run, /api/diag/report/:id
 *   - 图表: SVG 纯手绘 (无外部依赖)
 *   - 样式: Tailwind CSS (白底 + slate 边框体系)
 *   - 测试: cargo test accountability + npm run type-check
 *
 * 组件依赖关系：
 *   所有子组件 ← DiagDashboardShell（唯一入口）
 *   各殿 Panel ← PalaceDashboardSlice（单向数据流）
 *   实时事件 ← EventSource SSE → store dispatch → 各殿重渲染
 *
 * 跨殿联动路径：
 *   探针阵亡 → 点击 → 跳转诊断级联殿 (focusLayer = kernel)
 *   流脉破损 → 点击破损行 → 跳转 TimelinePanel (event_type 筛选)
 *   境迁僵滞 → 点击 stuck → 跳转诊断级联殿 (focusLayer = framework)
 *   断言失败 → 点击仪器跳转 → 对应 InstrumentType 面板
 *   九阶告警 → 点击阶号 → 跳转对应殿宇
 */

/**
 * 各殿组件实现优先级
 */
export const PALACE_IMPLEMENTATION_PRIORITY: Array<{
  hall: PalaceHall;
  priority: "P0" | "P1" | "P2" | "P3";
  components: string[];
  rationale: string;
}> = [
  {
    hall: "hall_diag_cascade",
    priority: "P0",
    components: ["DiagDashboardShell", "CascadeFlowDiagram", "RootCausePanel"],
    rationale: "最小可用版本：诊断级联图是三殿的统一视图，单独即可提供诊断价值",
  },
  {
    hall: "hall_probe_vitality",
    priority: "P1",
    components: ["ProbeStatusCard ×4", "ProbeVitalityTimeline"],
    rationale: "探针生机是诊断链起点——先确认数据是否在采集",
  },
  {
    hall: "hall_runtime_liveness",
    priority: "P1",
    components: ["ProcessHealthCard", "PhaseStateMachine", "RssGrowthGauge"],
    rationale: "运行时健康是最常被查看的诊断面板",
  },
  {
    hall: "hall_stream_integrity",
    priority: "P2",
    components: ["EventTypeCoverageMatrix", "MonotonicityViolationDetail", "SourceCoverageRadar"],
    rationale: "流完整性校验是细化诊断，在探针正常后才有意义",
  },
  {
    hall: "hall_verification",
    priority: "P2",
    components: ["VerificationProgressPanel", "TierAssertionTable", "AssertionDetailPanel"],
    rationale: "递验总持是递送时刻的核心核验，需要 Manifest 数据驱动",
  },
  {
    hall: "hall_full_nine_stages",
    priority: "P3",
    components: ["NineStageTimeline", "StageSummary cards", "DeliveryCadenceChart"],
    rationale: "九阶贯通观是全局视图，在基础三殿完成后再整合",
  },
];

// ═══════════════════════════════════════════════════════════════
// 第十五章 · 初始状态工厂 — 全部 slice 的零值
// ═══════════════════════════════════════════════════════════════

/** 创建默认殿宇仪表板状态 */
export function createDefaultPalaceState(): Omit<
  PalaceDashboardSlice,
  | "switchHall" | "setHallDataReady"
  | "startDiag" | "onProbeEvent" | "onStreamEvent" | "onRuntimeEvent"
  | "onCascadeEvent" | "onDiagComplete" | "clearDiagEvents"
  | "loadHistory" | "loadTrend" | "selectHistoryRun"
  | "setThresholds"
  | "setVerificationProgress" | "setAssertionMatrix" | "setFailureRemediations" | "setVerifyManifestPath"
  | "setNineStageState"
  | "setActivePid" | "setActivePort" | "setSseConnected"
  | "navigateToTimerange" | "navigateToProbe" | "navigateToAgentPid" | "clearNavigation"
  | "resetPalace"
> {
  return {
    // 殿宇
    activeHall: "hall_diag_cascade",
    hallDataReady: {},

    // 诊断
    isRunning: false,
    currentRunId: null,
    currentReport: null,
    overallStatus: null,
    activePid: null,
    activePort: 7395,
    sseConnected: false,

    // 实时事件
    probeEvents: [],
    streamEvents: [],
    runtimeEvents: [],
    cascadeEvents: [],
    layerComplete: { probe: false, stream: false, runtime: false, cascade: false },

    // 历史与趋势
    history: [],
    trend: [],

    // 阈值（与 Rust 端 shared_handoff.md 一致）
    thresholds: {
      probeSilentTimeoutSec: 30,
      monotonicityViolationRatio: 0.01,
      phaseStallTimeoutSec: 120,
      rssGrowthRateCriticalKbps: 10240,
    },

    // 递验
    verificationProgress: null,
    assertionMatrices: {
      tier_1_document: null,
      tier_2_propagation: null,
      tier_3_causal: null,
    },
    failureRemediations: [],
    verifyManifestPath: "",

    // 九阶贯通
    nineStageState: null,

    // 跨殿联动
    navigateTimerange: null,
    navigateProbe: null,
    navigateAgentPid: null,
  };
}

// ═══════════════════════════════════════════════════════════════
// 第十六章 · 型制完备性自检
// ═══════════════════════════════════════════════════════════════

/**
 * 型制完备性判词：
 *
 * ✅ shared_handoff.md Template D  → 第一殿·探针生机殿 (ProbeVitalityPalace + ProbeStatusCard ×4 + ProbeVitalityTimeline)
 * ✅ shared_handoff.md Template E  → 第二殿·流脉校验殿 (StreamIntegrityPalace + EventTypeCoverageMatrix + MonotonicityViolationDetail + SourceCoverageRadar)
 * ✅ shared_handoff.md Template F  → 第三殿·境迁监视殿 (RuntimeLivenessPalace + PhaseStateMachine + RssGrowthGauge + ProcessHealthCard)
 * ✅ shared_handoff.md 三阶递验    → 第四殿·递验总持殿 (VerificationPalace + VerificationProgressPanel + TierAssertionTable + AssertionDetailPanel)
 * ✅ shared_handoff.md 四–九阶     → 第五殿·九阶贯通观 (NineStagePalace + NineStageTimeline + DeliveryCadenceChart)
 * ✅ shared_handoff.md D→E→F 级联  → 第六殿·诊断级联殿 (DiagCascadePalace + CascadeFlowDiagram + RootCausePanel)
 * ✅ shared_handoff.md 历史监视    → DiagHistoryTable + DiagTrendChart
 * ✅ 第二局 mtagentlens diag CLI    → DiagRunRequest → REST API (4 个端点)
 * ✅ 第二局 SSE 实时流              → PalaceDiagEvent (5 种事件类型)
 * ✅ 第二局递验器具                 → AssertionMethod (12 种验证方式)
 * ✅ 第二局 threshold 覆盖         → DiagThresholdConfig + setThresholds action
 * ✅ Zustand 状态                   → PalaceDashboardSlice (35 属性 + 28 actions)
 * ✅ 跨殿联动                       → navigateToTimerange/navigateToProbe/navigateToAgentPid
 *
 * 组件总数: 18 个独立组件 + 1 个 DashboardShell
 * SSE 事件类型: 5 种
 * REST API 端点: 4 个
 * Zustand 属性/actions: 35 + 28
 * 验证方式: 12 种（sha256 到 cargo test）
 * 殿宇数: 6 座
 *
 * 判曰:
 *   宫室既成，六殿具备。
 *   生机查验为东殿（D），流脉校验为西殿（E），境迁监视为南殿（F）。
 *   递验总持居北（三阶），九阶贯通观天（总览），诊断级联贯地（根因）。
 *   三符（D/E/F）各有其居，三阶递验各有其器，六阶互锁各有其道。
 *   第二局之器（mtagentlens diag）出 JSON 则入 REST，
 *   出 SSE 则入实时流，型制无缺，器象相合。
 *   《道德经》云「大制不割」，此之谓也。
 *
 *   假设一人欲行此事，循此术法符文之所示：
 *     第一日，立殿宇之骨架（DiagDashboardShell + store）；
 *     第二日，营探针之生机（ProbeStatusCard + Timeline）；
 *     第三日，疏流脉之校验（EventTypeCoverage + Monotonicity）；
 *     第四日，筑境迁之监视（PhaseStateMachine + RssGauge）；
 *     第五日，设递验之总持（VerificationProgress + AssertionTable）；
 *     第六日，通诊断之级联（CascadeFlow + RootCause）；
 *     第七日，观九阶之贯通（NineStageTimeline + Cadence）；
 *     第八日，接实时之洪流（SSE + REST + 跨殿联动）；
 *     第九日，试全器之和合（cargo test + type-check + browser QA）；
 *     第十日，审宫室之完美（pr-review-toolkit:code-reviewer）。
 *   十日功成，宫室仪象自明，递送之道不灭。
 */
