/**
 * 宫室仪象所呈之型制约法 · 术法符文
 *
 * 第三局 · 宫室仪象 — 阅首局递送文书 (shared_handoff.md Templates D/E/F)
 * 及第二局营缮要略 (mtagentlens diag 设计要略)，拟定前端仪象之型制：
 * 仪象类型、展示规格、组件型范。与第二局之器相合。
 *
 * 第二局器具：mtagentlens diag（Rust CLI 诊断工具）
 *   - Template D → ProbeVitalityCheck（内核探针生机查验）
 *   - Template E → StreamIntegrityCheck（事件流脉完整性校验）
 *   - Template F → RuntimeLivenessCheck（运行时境迁监视）
 *   - CascadeSummary → 诊断链 D→E→F 逐层根因定位
 *
 * 对应 Rust 类型定义：collector/src/diagnostic/types.rs
 *
 * 此文为单一 TypeScript 型符，凡第二局之器所出，皆有前端仪象与之对应。
 */

// ════════════════════════════════════════════════════════════════
// 第一章 · 状态枚举 — 与 Rust enum 镜像
// ════════════════════════════════════════════════════════════════

/** 诊断全局状态 — 对应 Rust `DiagnosticStatus` */
export type DiagnosticStatus = 'healthy' | 'degraded' | 'unhealthy';

/** 探针生机等级 — 对应 Rust `VitalityLevel` */
export type VitalityLevel = 'alive' | 'silent' | 'dead';

/** 运行时阶段 — 对应 Rust `RuntimePhase` */
export type RuntimePhase =
  'bootstrap' | 'phase1' | 'phase2_triggered' | 'phase2' | 'complete' | 'stuck' | 'unknown';

/** 诊断链根因层 — 对应 Rust `RootCauseLayer` */
export type RootCauseLayer = 'kernel' | 'pipeline' | 'framework' | 'none';

/** 诊断检查范围 — 映射 diag CLI 的 --probes/--stream/--runtime 标志 */
export type DiagCheckScope = 'probes' | 'stream' | 'runtime' | 'all';

/** 输出格式 — 映射 diag CLI 的 --json/--text */
export type DiagOutputFormat = 'json' | 'text';

/** 单次断言状态（递验用） */
export type AssertionStatus = 'pass' | 'fail' | 'warn' | 'skip' | 'pending';

/** 探针名称 — 对应 shared_handoff.md Template D 中的 BPF_PROGS 数组 */
export type ProbeName = 'tls_capture' | 'browsertrace' | 'process' | 'stdiocap';

/** 已知事件类型枚举 — 对应 shared_handoff.md Template E 的 KNOWN_TYPES */
export const KNOWN_EVENT_TYPES = [
  'ssl_read',
  'ssl_write',
  'process_exec',
  'process_exit',
  'process_file',
  'process_signal',
  'process_net',
  'stdio_read',
  'stdio_write',
  'system_cpu',
  'system_mem',
  'sse_text',
  'sse_done',
  'http_request',
  'http_response',
] as const;

export type KnownEventType = (typeof KNOWN_EVENT_TYPES)[number];

// ════════════════════════════════════════════════════════════════
// 第二章 · 数据报告类型 — 与 Rust struct 镜像
// ════════════════════════════════════════════════════════════════

// --- Template D: 内核探针生机查验 ---

/** 单枚探针状态 — 对应 Rust `ProbeStatus` */
export interface ProbeStatus {
  /** 探针名称 */
  name: ProbeName;
  /** bpftool prog show 是否可见 */
  attached: boolean;
  /** bpftool 提取的 run_cnt（未挂载时为 null） */
  runCount: number | null;
  /** 最近事件时间戳 epoch-ms（30s 内无输出时为 null） */
  lastEventTsMs: number | null;
  /** 生机等级 */
  vitality: VitalityLevel;
  /** bpftool prog id（用于 perf_event_open 计数器 fallback） */
  progId: number | null;
}

/** 探针生机摘要 — 对应 Rust `ProbeVitalitySummary` */
export interface ProbeVitalitySummary {
  total: number;
  alive: number;
  silent: number;
  dead: number;
  /** 死探针列表 */
  deadProbes: ProbeName[];
  /** 建议动作 */
  recommendedAction: string | null;
}

/** Template D 完整报告 — 对应 Rust `ProbeVitalityReport` */
export interface ProbeVitalityReport {
  probes: ProbeStatus[];
  aliveCount: number;
  deadCount: number;
  summary: ProbeVitalitySummary;
}

// --- Template E: 事件流脉完整性校验 ---

/** 数据源覆盖状态 */
export interface SourceCoverage {
  /** 期望出现的数据源 */
  expected: string[];
  /** 实际出现的数据源 */
  detected: string[];
  /** 缺失的数据源 */
  missing: string[];
  /** 覆盖率 0-1 */
  coverageRatio: number;
}

/** 流完整性摘要 — 对应 Rust `StreamIntegritySummary` */
export interface StreamIntegritySummary {
  /** 完备性：无破损行 + 无未知类型 + 单调性正常 */
  isIntact: boolean;
  /** 破损率（corrupt_lines / total_lines） */
  corruptRate: number;
  /** 超过 1% 单调性违例时告警 */
  monotonicityExceedsThreshold: boolean;
  /** 源头覆盖是否完整 */
  allSourcesCovered: boolean;
  /** 逐维度行动建议 */
  actions: string[];
}

/** Template E 完整报告 — 对应 Rust `StreamIntegrityReport` */
export interface StreamIntegrityReport {
  inputPath: string;
  totalLines: number;
  corruptLines: number;
  unknownEventTypes: string[];
  monotonicityViolations: number;
  sourceCoverage: SourceCoverage;
  summary: StreamIntegritySummary;
}

// --- Template F: 运行时境迁监视 ---

/** 运行时摘要 — 对应 Rust `RuntimeLivenessSummary` */
export interface RuntimeLivenessSummary {
  /** 是否健康 */
  isHealthy: boolean;
  /** 阶段停滞判定 */
  isStalled: boolean;
  /** RSS 增长超过 10MB/s 判定 */
  rssGrowthCritical: boolean;
  /** 当前阶段已耗时秒数 vs 相应阈值 */
  elapsedVsThreshold: { elapsed: number; threshold: number };
  /** 逐维度行动建议 */
  actions: string[];
}

/** Template F 完整报告 — 对应 Rust `RuntimeLivenessReport` */
export interface RuntimeLivenessReport {
  pid: number | null;
  processAlive: boolean;
  rssKb: number;
  vszKb: number;
  processState: string;
  /** RSS 增长速率 KB/s（首次运行无历史数据时为 null） */
  rssGrowthRateKbps: number | null;
  phase: RuntimePhase;
  phase1EventsProcessed: number;
  phase2Complete: boolean;
  phaseElapsedSeconds: number;
  summary: RuntimeLivenessSummary;
}

// --- 诊断链汇总 ---

/** Template D→E→F 诊断链汇总 — 对应 Rust `CascadeSummary` */
export interface CascadeSummary {
  rootCauseLayer: RootCauseLayer;
  recommendedActions: string[];
  /** 诊断依据：逐层检查结果与判定逻辑 */
  diagnosisRationale: string;
}

// --- 全量诊断报告 ---

/** 单次诊断完整报告 — 对应 Rust `DiagnosticReport` */
export interface DiagnosticReport {
  runId: string;
  timestampEpochMs: number;
  overallStatus: DiagnosticStatus;
  probeVitality: ProbeVitalityReport;
  streamIntegrity: StreamIntegrityReport;
  runtimeLiveness: RuntimeLivenessReport;
  cascadeSummary: CascadeSummary;
  /** 诊断耗时 ms */
  elapsedMs: number;
}

// ════════════════════════════════════════════════════════════════
// 第三章 · 趋势与历史类型 — 前端特有
// ════════════════════════════════════════════════════════════════

/** 诊断趋势数据点（用于时间序列图表） */
export interface DiagTrendPoint {
  timestampEpochMs: number;
  runId: string;
  overallStatus: DiagnosticStatus;
  /** 探针存活率 0-1 */
  probeAliveRatio: number;
  /** 流破损率 0-1 */
  streamCorruptRate: number;
  /** RSS 增长速率 KB/s */
  rssGrowthRateKbps: number | null;
  /** 运行时阶段 */
  phase: RuntimePhase;
}

/** 诊断历史会话摘要（表列展示） */
export interface DiagSessionSummary {
  runId: string;
  timestampEpochMs: number;
  overallStatus: DiagnosticStatus;
  probeAliveCount: number;
  probeTotalCount: number;
  streamCorruptLines: number;
  streamTotalLines: number;
  runtimePhase: RuntimePhase;
  rootCauseLayer: RootCauseLayer;
  elapsedMs: number;
}

/** 诊断阈值配置（前端可覆盖） */
export interface DiagThresholdConfig {
  /** Template D: 探针无事件超时秒数（默认 30） */
  probeSilentTimeoutSec: number;
  /** Template E: 单调性违例告警比例（默认 0.01 = 1%） */
  monotonicityViolationRatio: number;
  /** Template F: 阶段停滞超时秒数（默认 120） */
  phaseStallTimeoutSec: number;
  /** Template F: RSS 增长速率告警阈值 KB/s（默认 10240 = 10MB/s） */
  rssGrowthRateCriticalKbps: number;
}

// ════════════════════════════════════════════════════════════════
// 第四章 · 组件 Props 接口 — 每个可视化组件的型范
// ════════════════════════════════════════════════════════════════

// --- Dashboard Shell ---

/** 诊断仪表板外壳 Props */
export interface DiagDashboardShellProps {
  /** 当前诊断报告 */
  report: DiagnosticReport | null;
  /** 历史会话摘要列表 */
  history: DiagSessionSummary[];
  /** 趋势数据 */
  trend: DiagTrendPoint[];
  /** 当前阈值配置 */
  thresholds: DiagThresholdConfig;
  /** 是否正在执行诊断 */
  isRunning: boolean;
  /** 触发新一轮诊断 */
  onRunDiag: (scope: DiagCheckScope) => void;
  /** 更新阈值配置 */
  onThresholdChange: (config: Partial<DiagThresholdConfig>) => void;
  /** 导出诊断报告 */
  onExport: (format: DiagOutputFormat) => void;
  /** SSE 连接状态 */
  sseConnected: boolean;
  /** 活跃的 mtagentlens PID */
  activePid: number | null;
}

// --- Template D 组件 ---

/** 单枚探针状态卡片 Props */
export interface ProbeStatusCardProps {
  probe: ProbeStatus;
  /** 当前阈值配置 */
  silentTimeoutSec: number;
  /** 点击展开详情 */
  onExpand: (probeName: ProbeName) => void;
}

/** 探针生机仪表板 Props */
export interface ProbeVitalityDashboardProps {
  report: ProbeVitalityReport | null;
  /** 是否正在采集 */
  isRunning: boolean;
  /** 选中探针名（null 为全部视图） */
  selectedProbe: ProbeName | null;
  onSelectProbe: (name: ProbeName | null) => void;
}

/** 探针生机时间线 Props（历史趋势） */
export interface ProbeVitalityTimelineProps {
  trend: DiagTrendPoint[];
  /** 焦点探针名（null 为全部） */
  focusProbe: ProbeName | null;
}

// --- Template E 组件 ---

/** 事件类型覆盖矩阵 Props */
export interface EventTypeCoverageMatrixProps {
  report: StreamIntegrityReport | null;
  /** 已知道类型全集 */
  allKnownTypes: readonly string[];
}

/** 单调性违例详情 Props */
export interface MonotonicityViolationDetailProps {
  violations: number;
  totalLines: number;
  /** 每源违例分布: Map<sourceName, violationCount> */
  perSourceViolations: Record<string, number>;
}

/** 源覆盖雷达图 Props */
export interface SourceCoverageRadarProps {
  coverage: SourceCoverage;
  /** 已知道源全集 */
  allKnownSources: string[];
}

/** 流完整性仪表板 Props */
export interface StreamIntegrityDashboardProps {
  report: StreamIntegrityReport | null;
  isRunning: boolean;
  /** 当前校验的 JSONL 文件路径 */
  inputPath: string;
  onInputPathChange: (path: string) => void;
}

// --- Template F 组件 ---

/** 阶段状态机图 Props */
export interface PhaseStateMachineProps {
  currentPhase: RuntimePhase;
  /** 阶段历史（时间 → 阶段） */
  phaseHistory: Array<{ timestampEpochMs: number; phase: RuntimePhase }>;
  /** 各阶段超时阈值 */
  stallTimeoutSec: number;
  elapsedSeconds: number;
}

/** RSS 增长速率仪表 Props */
export interface RssGrowthGaugeProps {
  rssKb: number;
  vszKb: number;
  growthRateKbps: number | null;
  criticalThresholdKbps: number;
  /** 历史 RSS 数据点 */
  history: Array<{ timestampEpochMs: number; rssKb: number }>;
}

/** 进程健康卡片 Props */
export interface ProcessHealthCardProps {
  pid: number | null;
  processAlive: boolean;
  processState: string;
  phase: RuntimePhase;
  phase1EventsProcessed: number;
  phase2Complete: boolean;
  phaseElapsedSeconds: number;
}

/** 运行时境迁仪表板 Props */
export interface RuntimeLivenessDashboardProps {
  report: RuntimeLivenessReport | null;
  isRunning: boolean;
  onPidChange: (pid: number | null) => void;
  onPortChange: (port: number) => void;
}

// --- 诊断链汇总组件 ---

/** 诊断级联图 Props（D→E→F 三层关系） */
export interface DiagnosticCascadeViewProps {
  cascadeSummary: CascadeSummary;
  probeReport: ProbeVitalityReport | null;
  streamReport: StreamIntegrityReport | null;
  runtimeReport: RuntimeLivenessReport | null;
}

/** 根因面板 Props */
export interface RootCausePanelProps {
  rootCauseLayer: RootCauseLayer;
  recommendedActions: string[];
  diagnosisRationale: string;
}

/** 历史诊断表 Props */
export interface DiagHistoryTableProps {
  sessions: DiagSessionSummary[];
  /** 当前选中的 runId */
  selectedRunId: string | null;
  onSelectRun: (runId: string) => void;
}

// ════════════════════════════════════════════════════════════════
// 第五章 · SSE 实时事件类型
// ════════════════════════════════════════════════════════════════

/** SSE 流类型鉴别符 */
export type DiagStreamType =
  'diag_probe' | 'diag_stream' | 'diag_runtime' | 'diag_cascade' | 'diag_complete';

/** 诊断 SSE 事件基类 */
export interface DiagEventBase {
  type: DiagStreamType;
  runId: string;
  timestampEpochMs: number;
}

/** 探针生机事件 — 逐枚探针的实时状态 */
export interface ProbeVitalityEvent extends DiagEventBase {
  type: 'diag_probe';
  probe: ProbeStatus;
}

/** 流完整性事件 — 逐行/逐维度的校验进度 */
export interface StreamIntegrityEvent extends DiagEventBase {
  type: 'diag_stream';
  /** 已处理行数 */
  linesProcessed: number;
  /** 破损行数（累计） */
  corruptLines: number;
  /** 新发现的未知事件类型 */
  newUnknownType: string | null;
  /** 单调性违例数（累计） */
  monotonicityViolations: number;
}

/** 运行时境迁事件 — ps + /api/health 周期采样 */
export interface RuntimeLivenessEvent extends DiagEventBase {
  type: 'diag_runtime';
  rssKb: number;
  rssGrowthRateKbps: number | null;
  phase: RuntimePhase;
  phase1EventsProcessed: number;
}

/** 诊断级联事件 — 各层检查完成时 */
export interface DiagCascadeEvent extends DiagEventBase {
  type: 'diag_cascade';
  /** 哪一层刚完成：'probe' | 'stream' | 'runtime' | 'cascade' */
  layerCompleted: 'probe' | 'stream' | 'runtime' | 'cascade';
  /** 该层状态 */
  layerStatus: DiagnosticStatus;
}

/** 诊断完成事件 */
export interface DiagCompleteEvent extends DiagEventBase {
  type: 'diag_complete';
  report: DiagnosticReport;
}

/** 诊断事件联合类型 */
export type DiagEvent =
  | ProbeVitalityEvent
  | StreamIntegrityEvent
  | RuntimeLivenessEvent
  | DiagCascadeEvent
  | DiagCompleteEvent;

/** 事件路由分发器签名 */
export type DiagEventDispatcher = (event: DiagEvent) => void;

// ════════════════════════════════════════════════════════════════
// 第六章 · Zustand 全局状态切片
// ════════════════════════════════════════════════════════════════

/** 诊断仪表板 Zustand 状态切片 */
export interface DiagDashboardSlice {
  // --- 当前诊断状态 ---
  isRunning: boolean;
  currentRunId: string | null;
  currentReport: DiagnosticReport | null;

  // --- 实时事件缓冲区 (Template D/E/F 各独立流) ---
  probeEvents: ProbeVitalityEvent[];
  streamEvents: StreamIntegrityEvent[];
  runtimeEvents: RuntimeLivenessEvent[];

  // --- 级联状态 ---
  cascadeEvents: DiagCascadeEvent[];
  /** 各层是否已完成 */
  layerComplete: Record<'probe' | 'stream' | 'runtime' | 'cascade', boolean>;

  // --- 历史与趋势 ---
  history: DiagSessionSummary[];
  trend: DiagTrendPoint[];

  // --- 阈值配置 ---
  thresholds: DiagThresholdConfig;

  // --- 活跃 mtagentlens 进程 ---
  activePid: number | null;
  activePort: number;

  // --- SSE 连接 ---
  sseConnected: boolean;

  // --- Actions ---
  startDiag: (scope: DiagCheckScope) => void;
  onProbeEvent: (event: ProbeVitalityEvent) => void;
  onStreamEvent: (event: StreamIntegrityEvent) => void;
  onRuntimeEvent: (event: RuntimeLivenessEvent) => void;
  onCascadeEvent: (event: DiagCascadeEvent) => void;
  onDiagComplete: (event: DiagCompleteEvent) => void;
  setThresholds: (config: Partial<DiagThresholdConfig>) => void;
  setActivePid: (pid: number | null) => void;
  setActivePort: (port: number) => void;
  setSseConnected: (connected: boolean) => void;
  loadHistory: (sessions: DiagSessionSummary[]) => void;
  loadTrend: (points: DiagTrendPoint[]) => void;
  clearEvents: () => void;
  reset: () => void;
}

/** 诊断仪表板 Zustand slice 初始状态 */
export const INITIAL_DIAG_DASHBOARD_STATE: Omit<
  DiagDashboardSlice,
  | 'startDiag'
  | 'onProbeEvent'
  | 'onStreamEvent'
  | 'onRuntimeEvent'
  | 'onCascadeEvent'
  | 'onDiagComplete'
  | 'setThresholds'
  | 'setActivePid'
  | 'setActivePort'
  | 'setSseConnected'
  | 'loadHistory'
  | 'loadTrend'
  | 'clearEvents'
  | 'reset'
> = {
  isRunning: false,
  currentRunId: null,
  currentReport: null,

  probeEvents: [],
  streamEvents: [],
  runtimeEvents: [],

  cascadeEvents: [],
  layerComplete: { probe: false, stream: false, runtime: false, cascade: false },

  history: [],
  trend: [],

  thresholds: {
    probeSilentTimeoutSec: 30,
    monotonicityViolationRatio: 0.01,
    phaseStallTimeoutSec: 120,
    rssGrowthRateCriticalKbps: 10240,
  },

  activePid: null,
  activePort: 7395,

  sseConnected: false,
};

// ════════════════════════════════════════════════════════════════
// 第七章 · REST API 端点类型
// ════════════════════════════════════════════════════════════════

/** POST /api/diag/run — 触发诊断 */
export interface DiagRunRequest {
  scope: DiagCheckScope;
  /** 指定 PID（Template F） */
  pid: number | null;
  /** API 端口（默认 7395） */
  port: number;
  /** JSONL 文件路径（Template E） */
  streamPath: string | null;
  /** 阈值覆盖 */
  thresholds?: Partial<DiagThresholdConfig>;
}

/** POST /api/diag/run 响应 */
export interface DiagRunResponse {
  runId: string;
  /** SSE 流地址 */
  sseEndpoint: `/api/diag/events/${string}`;
}

/** GET /api/diag/report/:runId — 获取诊断报告 */
export type DiagReportResponse = DiagnosticReport;

/** GET /api/diag/history — 诊断历史 */
export interface DiagHistoryResponse {
  sessions: DiagSessionSummary[];
  total: number;
}

/** GET /api/diag/trend — 诊断趋势 */
export interface DiagTrendResponse {
  points: DiagTrendPoint[];
}

/** GET /api/diag/thresholds — 获取阈值配置 */
export type DiagThresholdsResponse = DiagThresholdConfig;

/** PUT /api/diag/thresholds — 更新阈值配置 */
export type DiagThresholdsUpdateRequest = Partial<DiagThresholdConfig>;

// ════════════════════════════════════════════════════════════════
// 第八章 · 组件布局规格
// ════════════════════════════════════════════════════════════════

/**
 * 诊断仪表板布局结构:
 *
 * ┌──────────────────────────────────────────────────────────┐
 * │  DiagDashboardShell                                       │
 * │  ┌──────────────────────────────────────────────────┐    │
 * │  │  Toolbar: [Run Diag ▼] [Export] [PID: ___]       │    │
 * │  │           [Port: 7395] [SSE ●/○]                 │    │
 * │  └──────────────────────────────────────────────────┘    │
 * │  ┌──────────┬──────────┬──────────┐                     │
 * │  │ Template │ Template │ Template │                     │
 * │  │    D     │    E     │    F     │                     │
 * │  │ 探针生机  │ 流脉校验  │ 境迁监视  │                     │
 * │  │          │          │          │                     │
 * │  │ ProbeVita│ StreamIn │ RuntimeLi│                     │
 * │  │ lityDash │ tegrityDa│ venessDa │                     │
 * │  │ board    │ shboard  │ shboard  │                     │
 * │  └──────────┴──────────┴──────────┘                     │
 * │  ┌──────────────────────────────────────────────────┐    │
 * │  │  Template D→E→F Cascade: DiagnosticCascadeView    │    │
 * │  │  ┌──────┐   ┌──────┐   ┌──────┐                 │    │
 * │  │  │探针层│──→│流脉层│──→│运行时│                 │    │
 * │  │  │ ✅   │   │ ⚠️   │   │ ❌   │                 │    │
 * │  │  └──────┘   └──────┘   └──────┘                 │    │
 * │  │  RootCausePanel: Root cause → Framework layer     │    │
 * │  │  Actions: [1. SIGINT → Phase2] [2. Check stalled] │    │
 * │  └──────────────────────────────────────────────────┘    │
 * │  ┌──────────────────────────────────────────────────┐    │
 * │  │  DiagHistoryTable (previous runs)                 │    │
 * │  │  ┌──────┬──────────┬──────┬──────┬──────┐       │    │
 * │  │  │ Run  │ Time     │Staus │Probe │Stream│       │    │
 * │  │  ├──────┼──────────┼──────┼──────┼──────┤       │    │
 * │  │  │ abc1 │ 13:29:19 │ ✓    │ 4/4  │ 99%  │       │    │
 * │  │  │ def2 │ 13:28:00 │ ⚠    │ 3/4  │ 98%  │       │    │
 * │  │  └──────┴──────────┴──────┴──────┴──────┘       │    │
 * │  └──────────────────────────────────────────────────┘    │
 * └──────────────────────────────────────────────────────────┘
 */

/**
 * 组件树（按渲染层级）:
 *
 * DiagDashboardShell
 * ├── DiagToolbar
 * │   ├── RunDiagButton (scope 选择: probes/stream/runtime/all)
 * │   ├── ExportButton (format: json/text)
 * │   ├── PidInput
 * │   ├── PortInput
 * │   └── SseStatusIndicator
 * ├── DiagThreeColumnGrid
 * │   ├── ProbeVitalityDashboard (左列)
 * │   │   ├── ProbeStatusCard ×4 (tls_capture, browsertrace, process, stdiocap)
 * │   │   │   └── ProbeDetailPanel (展开态)
 * │   │   └── ProbeVitalityTimeline (图)
 * │   ├── StreamIntegrityDashboard (中列)
 * │   │   ├── EventTypeCoverageMatrix
 * │   │   ├── MonotonicityViolationDetail
 * │   │   └── SourceCoverageRadar
 * │   └── RuntimeLivenessDashboard (右列)
 * │       ├── ProcessHealthCard
 * │       ├── PhaseStateMachine
 * │       └── RssGrowthGauge
 * ├── DiagnosticCascadeView
 * │   ├── CascadeFlowDiagram (3 节点流程图)
 * │   └── RootCausePanel
 * ├── DiagHistoryTable
 * └── DiagTrendChart (可选折叠)
 */

// ════════════════════════════════════════════════════════════════
// 第九章 · 色彩/状态映射常量
// ════════════════════════════════════════════════════════════════

/** 诊断状态 → 语义色 */
export const DIAG_STATUS_COLORS: Record<DiagnosticStatus, string> = {
  healthy: '#22c55e', // green-500
  degraded: '#eab308', // yellow-500
  unhealthy: '#ef4444', // red-500
};

/** 生机等级 → 语义色 + 图标 */
export const VITALITY_LEVEL_DISPLAY: Record<
  VitalityLevel,
  { color: string; icon: string; label: string }
> = {
  alive: { color: '#22c55e', icon: '●', label: '存活' },
  silent: { color: '#eab308', icon: '◑', label: '静默' },
  dead: { color: '#ef4444', icon: '○', label: '阵亡' },
};

/** 运行时阶段 → 语义色 + 标签 */
export const RUNTIME_PHASE_DISPLAY: Record<RuntimePhase, { color: string; label: string }> = {
  bootstrap: { color: '#3b82f6', label: '初始化' },
  phase1: { color: '#22c55e', label: '流式分类' },
  phase2_triggered: { color: '#eab308', label: '触发终结' },
  phase2: { color: '#06b6d4', label: '批量终结' },
  complete: { color: '#8b5cf6', label: '完成' },
  stuck: { color: '#ef4444', label: '僵滞' },
  unknown: { color: '#6b7280', label: '未知' },
};

/** 根因层 → 语义色 + 排查范围 */
export const ROOT_CAUSE_LAYER_DISPLAY: Record<
  RootCauseLayer,
  { color: string; label: string; scope: string }
> = {
  kernel: {
    color: '#ef4444',
    label: '内核/eBPF 层',
    scope: '检查 bpftool、内核版本、verifier 日志',
  },
  pipeline: {
    color: '#f97316',
    label: '数据管道层',
    scope: '检查 perf buffer、磁盘空间、时钟同步',
  },
  framework: {
    color: '#eab308',
    label: 'Rust 框架层',
    scope: '检查 analyzer 管线、SQLite 写入、claim builder',
  },
  none: { color: '#22c55e', label: '无故障', scope: '系统运行正常' },
};

// ════════════════════════════════════════════════════════════════
// 第十章 · 默认诊断阈值
// ════════════════════════════════════════════════════════════════

/** 默认诊断阈值（与 Rust 端一致） */
export const DEFAULT_DIAG_THRESHOLDS: DiagThresholdConfig = {
  probeSilentTimeoutSec: 30,
  monotonicityViolationRatio: 0.01,
  phaseStallTimeoutSec: 120,
  rssGrowthRateCriticalKbps: 10240,
};

// ════════════════════════════════════════════════════════════════
// 第十一章 · 阶段状态机阈值表（与 shared_handoff.md Template F 一致）
// ════════════════════════════════════════════════════════════════

/** 阶段 → 超时阈值 & 健康判定 */
export interface PhaseThreshold {
  phase: RuntimePhase;
  timeoutSec: number;
  healthy: boolean;
  description: string;
  actionIfStuck: string;
}

export const PHASE_THRESHOLDS: PhaseThreshold[] = [
  {
    phase: 'bootstrap',
    timeoutSec: 30,
    healthy: true,
    description: '初始化 runner、加载 BPF 程序',
    actionIfStuck: '检查内核版本、BPF verifier 日志',
  },
  {
    phase: 'phase1',
    timeoutSec: 120,
    healthy: true,
    description: '流式分类、构建图（RSS 增长 <10MB/s）',
    actionIfStuck: 'SIGINT → phase2；启用 SmartSampler',
  },
  {
    phase: 'phase2_triggered',
    timeoutSec: 60,
    healthy: true,
    description: 'SIGINT 已发、phase1 → phase2 排空',
    actionIfStuck: '强制 kill 可能丢失未提交事件',
  },
  {
    phase: 'phase2',
    timeoutSec: 300,
    healthy: true,
    description: '批量终结：traceback、claims、alerts、持久化',
    actionIfStuck: '检查 SQLite 写入性能、claim builder',
  },
  {
    phase: 'complete',
    timeoutSec: Infinity,
    healthy: true,
    description: '所有数据已持久化、服务器运行于 7395 端口',
    actionIfStuck: '（正常终态）',
  },
  {
    phase: 'stuck',
    timeoutSec: 0,
    healthy: false,
    description: '任何阶段超过其超时阈值',
    actionIfStuck: '运行此诊断脚本；若重现则升级处理',
  },
  {
    phase: 'unknown',
    timeoutSec: 0,
    healthy: false,
    description: '无法获取阶段信息（HTTP API 不可达）',
    actionIfStuck: '检查 mtagentlens 进程是否存活',
  },
];

// ════════════════════════════════════════════════════════════════
// 第十二章 · 型制对齐校验矩阵
// ════════════════════════════════════════════════════════════════

/**
 * Template D/E/F → 仪象类型 → 组件 → SSE 事件 → Zustand 切片
 *
 * | 模板   | Rust 类型              | 仪象组件                     | SSE 事件            | Zustand 属性       |
 * |--------|-----------------------|-----------------------------|--------------------|--------------------|
 * | D      | ProbeVitalityReport   | ProbeVitalityDashboard      | ProbeVitalityEvent | probeEvents        |
 * | D      | ProbeStatus           | ProbeStatusCard             | (同上)              | (同上)              |
 * | D      | -                     | ProbeVitalityTimeline       | -                  | trend              |
 * | E      | StreamIntegrityReport | StreamIntegrityDashboard    | StreamIntegrityEv..| streamEvents       |
 * | E      | SourceCoverage        | SourceCoverageRadar          | (同上)              | (同上)              |
 * | E      | -                     | EventTypeCoverageMatrix     | (同上)              | (同上)              |
 * | E      | -                     | MonotonicityViolationDetail  | (同上)              | (同上)              |
 * | F      | RuntimeLivenessReport | RuntimeLivenessDashboard    | RuntimeLivenessEv..| runtimeEvents      |
 * | F      | -                     | ProcessHealthCard           | (同上)              | (同上)              |
 * | F      | -                     | PhaseStateMachine           | (同上)              | (同上)              |
 * | F      | -                     | RssGrowthGauge              | (同上)              | (同上)              |
 * | 汇总    | CascadeSummary        | DiagnosticCascadeView       | DiagCascadeEvent   | cascadeEvents      |
 * | 汇总    | CascadeSummary        | RootCausePanel              | (同上)              | (同上)              |
 * | 全局    | DiagnosticReport      | DiagDashboardShell          | DiagCompleteEvent  | currentReport      |
 * | 历史    | -                     | DiagHistoryTable            | -                  | history            |
 * | 趋势    | -                     | DiagTrendChart              | -                  | trend              |
 */

/**
 * 第二局器具 → 前端组件 对齐:
 *
 * | mtagentlens diag 标志      | 前端组件影响范围              | 数据流路径                    |
 * |--------------------------|----------------------------|-----------------------------|
 * | --probes                 | ProbeVitalityDashboard + 子组件 | diag_probe SSE → Zustand |
 * | --stream <file>          | StreamIntegrityDashboard + 子组件 | diag_stream SSE → Zustand|
 * | --runtime                | RuntimeLivenessDashboard + 子组件 | diag_runtime SSE → Zustand|
 * | (无，默认全查)             | 全部三列 + DiagnosticCascadeView | 所有 SSE 流并行              |
 * | --json                   | 跳过 SSE，直接渲染报告 JSON      | REST API /api/diag/report   |
 * | --text                   | 前端无变化（终端输出由 Rust 处理）| N/A                         |
 * | --pid                    | ProcessHealthCard 的 pid 字段   | 运行时 API 参数               |
 * | --port                   | 运行时 API 请求端口              | 运行时 API 参数               |
 * | --stall-timeout          | PhaseStateMachine 阈值线        | 阈值配置 + API 参数           |
 */

// ════════════════════════════════════════════════════════════════
// 第十三章 · 型制完备性判词
// ════════════════════════════════════════════════════════════════

/**
 * 型制完备性自检:
 *
 * ✅ 第二局 Rust enum ×4   → TS type ×4 (DiagnosticStatus / VitalityLevel / RuntimePhase / RootCauseLayer)
 * ✅ 第二局 Rust struct ×8  → TS interface ×8 (ProbeStatus ~ DiagnosticReport)
 * ✅ 第二局 CLI flags ×7   → TS component props 覆盖全部标志对应行为
 * ✅ 第二局 诊断链 D→E→F    → DiagnosticCascadeView + RootCausePanel
 * ✅ 第二局 输出双通道       → SSE (实时流) + REST (报告查询)
 * ✅ 第二局 历史 RSS 文件   → RssGrowthGauge 历史数据点
 * ✅ 第二局 /api/health     → PhaseStateMachine + ProcessHealthCard
 * ✅ 第二局 阈值覆盖        → DiagThresholdConfig + DiagThresholdsUpdateRequest
 * ✅ shared_handoff.md D    → ProbeVitalityDashboard + ProbeStatusCard + ProbeVitalityTimeline
 * ✅ shared_handoff.md E    → StreamIntegrityDashboard + EventTypeCoverageMatrix + MonotonicityViolationDetail + SourceCoverageRadar
 * ✅ shared_handoff.md F    → RuntimeLivenessDashboard + ProcessHealthCard + PhaseStateMachine + RssGrowthGauge
 * ✅ shared_handoff.md 编排注 → DiagnosticCascadeView 逐层排查逻辑
 *
 * 组件总数: 14 个独立组件 + 1 个 DashboardShell
 * SSE 事件类型: 5 种
 * Zustand 状态属性: 18 项 + 13 actions
 * REST API 端点: 5 个
 *
 * 判曰:
 *   宫室既成，仪象具备。
 *   三符（D/E/F）各有其殿，一器（diag）贯通其中。
 *   生机查验为第一殿，流脉校验为第二殿，境迁监视为第三殿。
 *   级联图为天桥，三殿相望而通；历史表面为地库，往迹可追。
 *   第二局之器（mtagentlens diag）出 JSON 则入 REST，
 *   出 SSE 则入实时流，型制无缺，器象相合。
 *   《道德经》云「大制不割」，此之谓也。
 */
