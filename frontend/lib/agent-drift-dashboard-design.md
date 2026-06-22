# Agent Drift Dashboard — 组件设计文档

> 目标：为 `AgentDriftAnalyzer` 的 `agent_drift` 事件提供一个完整的前端可视化仪表板
> 模式：遵循现有 SSE Content Scanner Dashboard 的架构模式（contract + component）
> 集成点：嵌入 `IntegratedAnalyzerDashboard` 的 `"agent_drift"` 视图模式

---

## 1. 组件树

```
AgentDriftDashboard (主容器 / layout shell)
├─ ViewTabBar              — 顶栏 Tab 切换（概览/维度雷达/Agent/趋势）
├─ FilterBar               — 筛选栏（agent/评分/方向），仅 signals/agents 视图
│
├─ [view_mode=overview]  OverviewTab
│   ├─ StatsGrid            — 4-6 统计卡片（监控agent数/基线建立/恶化数/告警率…）
│   ├─ TopDriftingAgents    — 漂移最严重的 agent 排行列表
│   ├─ DimensionSparklineRow — 6 维最近评分 mini-sparkline 行
│   └─ RecentSnapshotPreview — 最近 5 条漂移快照预览
│
├─ [view_mode=dimensions] DimensionsTab
│   ├─ AgentSelector        — 选择当前对比的 agent（单选或多选）
│   ├─ DriftRadarChart      — 六维雷达图，叠加 2–3 个 agent 的覆盖层
│   ├─ DimensionBarGrid     — 6 维评分柱状图（当前 vs 基线）
│   └─ DimensionScoreHistory — 聚焦维度的历史变化曲线
│
├─ [view_mode=agents]    AgentsTab
│   ├─ AgentDriftCardList   — 每 agent 一张卡片：综合评分、方向、最漂移维度
│   ├─ [selected] AgentDetailPanel
│   │   ├─ AgentDriftTrend  — 该 agent 的综合评分时间线
│   │   ├─ AgentDimensionBreakdown — 6 维条状图（最新快照）
│   │   ├─ TimelineMarker   — 首次漂移 → 最近漂移的时间跨度标注
│   │   └─ SnapshotHistoryList — 按时间倒序的历史快照列表
│
├─ [view_mode=trend]     TrendTab
│   ├─ DimensionSelector     — 选择聚焦维度（默认全部）
│   ├─ DriftTrendChart       — 时间 × 评分折线图，含阈值线标注
│   ├─ AgentOverlayLegend    — 图例（全局 + 各 agent 颜色）
│   └─ AnomalyAnnotations    — 图表上标注超过阈值的点
│
└─ BottomStatusBar        — 最后更新时间 + 快照总数 + 筛选计数
```

---

## 2. 视图布局设计

### 2.1 Overview → `"概览"` (默认视图)

```
┌──────────────────────────────────────────────────────┐
│ [📊 概览] [🔄 维度雷达] [🤖 Agent] [📈 趋势]          │
├──────────────────────────────────────────────────────┤
│ ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐         │
│ │监控中   │ │已基线   │ │恶化态   │ │告警率   │         │
│ │   5     │ │   4     │ │   2 🚨  │ │ 12.3%  │         │
│ └────────┘ └────────┘ └────────┘ └────────┘         │
│                                                       │
│ ┌─── 漂移最严重 Agent ────────────────────────────┐  │
│ │ #1 Claude Code (pid:12345)  ████████░░ 0.63 ⚠️  │  │
│ │ #2 Copilot (pid:23456)      ██████░░░░ 0.41     │  │
│ │ #3 GPT Shell (pid:34567)    ████░░░░░░ 0.22     │  │
│ └──────────────────────────────────────────────────┘  │
│                                                       │
│ ┌─── 六维最近活动 ────────────────────────────────┐  │
│ │ 🔧工具偏好 ████░░ 0.45  ⏱️延迟 ██░░░░ 0.12      │  │
│ │ ❌错误率   ███░░░ 0.25  📦体积 █░░░░░ 0.08      │  │
│ │ 📊频率     ██░░░░ 0.22  🌐方法 ░░░░░░ 0.05      │  │
│ └──────────────────────────────────────────────────┘  │
│                                                       │
│ ┌─── 最近快照 ──────────────────────────────────┐    │
│ │ 🟠 2026-06-04 10:23 — [Worsening] 0.63        │    │
│ │ 🟡 2026-06-04 10:21 — [Plateaued]  0.58       │    │
│ │ 🟡 2026-06-04 10:19 — [Plateaued]  0.55       │    │
│ └──────────────────────────────────────────────────┘  │
├──────────────────────────────────────────────────────┤
│ 5 agents · 42 snapshots · 18 breaches · 10:23       │
└──────────────────────────────────────────────────────┘
```

### 2.2 Dimensions → `"维度雷达"`

```
┌──────────────────────────────────────────────────────┐
│ [📊 概览] [🔄 维度雷达] [🤖 Agent] [📈 趋势]          │
├──────────────────────────────────────────────────────┤
│ Agent: [Claude Code ▼]  对比: [+ 添加对比 Agent]      │
├──────────────────────────────────────────────────────┤
│                                                       │
│                    🔧 工具偏好                        │
│                      ████░░                          │
│          🌐 HTTP方法  \   │   /  ⏱️ 延迟              │
│             ██░░░░    \  │  /    ██░░░░              │
│                        \ │ /                         │
│                         \│/                          │
│                         /│\                          │
│                        / │ \                         │
│            📊 频率     /  │  \    ❌ 错误率            │
│              ████░░░░ /  │  \   ██████░░░              │
│                       📦 体积                          │
│                        ██░░░░                         │
│                                                       │
│         ─── Claude Code  ─ ─ Copilot                  │
│                                                       │
├──────────────────────────────────────────────────────┤
│ ┌─ 工具偏好 ───────────────── [展开历史 ▼] ────────┐  │
│ │ 当前: 0.45  │██████████████░░░░░░░░░░░░░░░░░░░│  │
│ │ 基线: 0.08  │███░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░│  │
│ └──────────────────────────────────────────────────┘  │
│ ┌─ 错误率 ────────────────── [展开历史 ▼] ────────┐  │
│ │ 当前: 0.25  │████████░░░░░░░░░░░░░░░░░░░░░░░░░░░│  │
│ │ 基线: 0.02  │█░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░│  │
│ └──────────────────────────────────────────────────┘  │
│ ... (其余 4 维度)                                      │
└──────────────────────────────────────────────────────┘
```

### 2.3 Agents → `"Agent"`

```
┌──────────────────────────────────────────────────────┐
│ [📊 概览] [🔄 维度雷达] [🤖 Agent] [📈 趋势]          │
├──────────────────────────────────────────────────────┤
│                        ┌── 筛选 ──────────────────┐  │
│                        │ 评分 ≥ [0.15 ≡═≡══━━━━] │  │
│                        │ 方向 [全部 ▼]            │  │
│                        └──────────────────────────┘  │
├──────────────────────────────────────────────────────┤
│ ┌─ Claude Code (pid:12345) ─────── [恶化中 🚨] ──┐  │
│ │ 综合: 0.63  |  快照: 12  |  触阈: 5             │  │
│ │ 最强信号: 🔧工具偏好 (0.45)  ❌错误率 (0.38)     │  │
│ │ 漂移时间线: ·─·─·─●──●──● (恶化趋势)            │  │
│ └──────────────────────────────────────────────────┘  │
│                                                       │
│ ┌─ Copilot (pid:23456) ──────────── [稳定 ➡️] ───┐  │
│ │ 综合: 0.22  |  快照: 8  |  触阈: 1              │  │
│ │ 最强信号: ⏱️延迟 (0.18)  📊频率 (0.15)           │  │
│ │ 漂移时间线: ·─·─·─·─·──· (稳定趋势)            │  │
│ └──────────────────────────────────────────────────┘  │
│                                                       │
│ ┌─ GPT Shell (pid:34567) ───────── [恢复中 📉] ──┐  │
│ │ 综合: 0.15  |  快照: 6  |  触阈: 2              │  │
│ │ 最强信号: 📦体积 (0.12)  ⏱️延迟 (0.10)           │  │
│ │ 漂移时间线: ·─●──●──·─· (先恶化后恢复)          │  │
│ └──────────────────────────────────────────────────┘  │
│                                                       │
│ ═══════════ [详情面板: Claude Code] ═══════════════   │
│ ┌─ 综合评分趋势 ─────────────────────────────────┐   │
│ │ 1.0 ┤                                          │   │
│ │ 0.5 ┤         ·──●──●                          │   │
│ │ 0.2 ┤·····●──●·········  (阈值线 ─ ─)         │   │
│ │ 0.0 ┼──────────────────────────────────────    │   │
│ │     T0   T1   T2   T3   T4   T5   T6          │   │
│ └──────────────────────────────────────────────────┘  │
│ ┌─ 维度分解 (T6) ────────────────────────────────┐   │
│ │ 🔧工具 0.45 ██████████░░░░░░░░░  ⏱️延迟 0.12  │   │
│ │ ❌错误 0.38 ████████░░░░░░░░░░░  📦体积 0.08  │   │
│ │ 📊频率 0.22 █████░░░░░░░░░░░░░░  🌐方法 0.05  │   │
│ └──────────────────────────────────────────────────┘  │
│ ┌─ 历史快照 ─────────────────────────────────────┐   │
│ │ T6  10:23  0.63  Worsening   [详情]            │   │
│ │ T5  10:21  0.58  Plateaued    [详情]           │   │
│ │ T4  10:19  0.55  Plateaued    [详情]           │   │
│ │ T3  10:17  0.41  Recovering   [详情]           │   │
│ │ T2  10:15  0.48  Worsening    [详情]           │   │
│ └──────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────┘
```

### 2.4 Trend → `"趋势"`

```
┌──────────────────────────────────────────────────────┐
│ [📊 概览] [🔄 维度雷达] [🤖 Agent] [📈 趋势]          │
├──────────────────────────────────────────────────────┤
│ 聚焦维度: [全部 ▼]  Agent: [全部 ▼]  Drift阈值: 0.25 │
├──────────────────────────────────────────────────────┤
│                                                       │
│ 综合漂移评分趋势（全部 agent 叠加）                     │
│ 1.0 ┤                                                │
│     │          ●(Claude Code)                         │
│ 0.5 ┤        ·─●──●────                              │
│     │    ·──●          ●──·─·─· (Copilot)            │
│ 0.2 ┤·──·─────────·──●────                           │
│ 0.0 ┼────────────────────────────────────────        │
│     10:00  10:05  10:10  10:15  10:20  10:25        │
│                                                       │
│ ─── Claude Code  ─ ─ Copilot  ─ · ─ GPT Shell       │
│ ─ ─ ─ threshold (0.25)                               │
│                                                       │
│ · 标注：超过阈值的时间点用 ● 高亮，hover 显示详情     │
└──────────────────────────────────────────────────────┘
```

---

## 3. 关键交互设计

### 3.1 跨仪表板联动

| 触发操作 | 目标 | 参数 |
|----------|------|------|
| 点击 agent 卡片 | ProcessTree 定位 | `agent_root_pid` |
| 点击快照时间 | TimelinePanel 定位 | `window_start_ms`, `window_end_ms` |
| 点击维度分析条 | 聚焦该维度 → 切换到趋势视图 | `DriftDimension` |
| 点击工具名 | ToolExecutionDashboard | `tool_name` |
| 选中 agent 行 | 下方展开 AgentDetailPanel | `agent_root_pid` |

### 3.2 筛选行为

- **min_score_filter**: 滑块 (0→100%) 控制可见快照的最低评分
- **direction_filter**: 下拉选择 Worsening / Plateaued / Recovering / Uncertain
- **agent_filter**: 多选 agent PID（从 `agent_summaries` 中提取）
- 筛选后各 Tab 视图同步更新，底部状态栏显示 "X/Y 匹配"

### 3.3 空状态处理

- **无 agent 数据**: "AgentDriftAnalyzer 等待事件流…"
- **无基线 agent**: "N 个 agent 收集中，最早基线预计在 M 事件后建立"
- **无漂移告警**: "所有 agent 行为稳定，与基线无显著偏差 ✓"
- **无聚焦维度数据**: 该维度条显示灰色虚线 "无数据"

---

## 4. 数据流

```
Rust Backend (agent_drift event)
  │
  │  SSE /api/events 推送
  │  event.source === "agent_drift"
  │
  ▼
lib/store.ts
  │  onAgentDriftReceived(snapshot)
  │  ─ 累积 agent_summaries[]
  │  ─ 累积 recent_snapshots[]（最近 100 条）
  │  ─ 重新计算 stats
  │
  ▼
AgentDriftDashboard
  │  从 store 读取 state
  │  通过 props 分发到子组件
  │
  ├─ OverviewTab        ← stats, agent_summaries, recent_snapshots
  ├─ DimensionsTab      ← agent_summaries, selected_agent_history
  ├─ AgentsTab          ← agent_summaries, selected_agent_history
  └─ TrendTab           ← recent_snapshots (filtered)
```

### 状态更新策略

- `agent_summaries`: 每个 `agent_drift` 事件到达时，对对应 agent 的摘要做 **增量更新**（O(1)）。
- `recent_snapshots`: 环形缓冲区，最多保留 100 条（`unshift + pop`），避免内存无限增长。
- `stats`: 从 `agent_summaries` 和 `recent_snapshots` **useMemo 派生**，不是独立存储。
- 前端不做 KL 散度/均值偏移计算——所有统计由 Rust 侧 `AgentDriftAnalyzer` 完成，前端只负责渲染。

### Store 扩展 (schema)

```typescript
// lib/store.ts 新增字段
agentDriftState: AgentDriftDashboardState;

// 新增 action
onAgentDriftReceived: (snapshot: DriftSnapshot) => void;
```

---

## 5. 与现有仪表板的集成

### 5.1 IntegratedAnalyzerDashboard

在 `analyzer-dashboard-contract.ts` 中新增：

```typescript
export type IntegratedViewMode =
  | "pipeline_overview"
  | ...
  | "agent_drift";  // ← 新增

// StageAnalyzerInfo.dashboard_type 新增
dashboard_type: "agent_loop" | "tool_execution" | ... | "agent_drift" | "generic";

// TAB_DEFS 新增
{ mode: "agent_drift", label: "漂移检测", icon: "📉" },
```

### 5.2 Store 集成

```typescript
// lib/store.ts
import { createDefaultDriftState } from "./agent-drift-contract";

// 初始状态
agentDriftState: createDefaultDriftState(),

// SSE 事件处理
if (event.source === "agent_drift") {
  const snapshot = parseDriftEvent(event);
  set((s) => ({
    agentDriftState: updateDriftState(s.agentDriftState, snapshot),
  }));
}
```

---

## 6. 组件实现优先级

| 优先级 | 组件 | 理由 |
|--------|------|------|
| **P0** | `AgentDriftDashboard` 主壳 + `OverviewTab` | 最小可用版本，展示核心统计和 agent 列表 |
| **P0** | `AgentDriftCard` + `StatsGrid` | Agent 级别的基本信息展示 |
| **P1** | `AgentsTab` + `AgentDetailPanel` | Agent 详情和历史趋势 |
| **P1** | `FilterBar` | 筛选功能 |
| **P2** | `DimensionsTab` + `DriftRadarChart` | 六维对比视图（可复用纯 SVG/Canvas 方案） |
| **P2** | `TrendTab` + `DriftTrendChart` | 全局时间线 + agent 叠加对比 |
| **P3** | `DriftSnapshotDetail` 弹出面板 | 快照详情展开 |
| **P3** | `AgentOverlayLegend` 图例组件 | 多 agent 对比时的彩色图例 |

---

## 7. 视觉规格速查

| 元素 | 值 |
|------|-----|
| 主色调 | 白底 + slate 边框（与现有仪表板一致） |
| 恶化态 | Red-600（`text-red-600`, `bg-red-50`） |
| 告警态 | Orange-600（`text-orange-600`, `bg-orange-50`） |
| 稳定态 | Emerald-600（`text-emerald-600`, `bg-emerald-50`） |
| 不确定态 | Slate-500（`text-slate-500`, `bg-slate-50`） |
| 维度颜色 | 6 色循环: indigo, amber, rose, emerald, violet, teal |
| 阈值线 | 虚线 `border-dashed border-red-300` |
| 趋势线(sparkline) | SVG `<polyline>`，无填充，1.5px 描边 |
| 字体 | 等宽数字 `font-mono` 用于评分/百分数；`text-xs`/`text-[10px]` 用于标签 |
| 动画 | 恶化态 pulsing dot `animate-pulse`；Tab 切换 `transition-all` |
