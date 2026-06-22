# 新增三个分析器的仪表板组件设计
> **作者**: lzkk  
> **日期**: 2026-06-03  
> **状态**: 设计阶段，待团队评审  
> **关联设计**: file_access_audit_analyzer, token_cost_analyzer, session_boundary_analyzer

## 1. 概述

为 FileAccessAuditAnalyzer、TokenCostAnalyzer、SessionBoundaryAnalyzer 三个新分析器设计前端仪表板组件。每个组件遵循现有的子仪表板模式（参考 `agent-loop-dashboard.tsx`、`tool-execution-dashboard.tsx`），嵌入 `IntegratedAnalyzerDashboard` 的标签页系统中。

## 2. TypeScript 契约文件

已创建三个新契约文件，路径及核心内容：

| 契约文件 | 对应分析器 | 输出 source tag | 核心类型 |
|----------|-----------|----------------|---------|
| `lib/file-access-audit-contract.ts` | FileAccessAuditAnalyzer | `file_access` | `FileAccessRecord`, `WorkspaceAccessSnapshot`, `FileAccessDashboardState` |
| `lib/token-cost-contract.ts` | TokenCostAnalyzer | `token_cost_anomaly` | `TokenAnomalyAlert`, `GlobalTokenSnapshot`, `TokenCostDashboardState` |
| `lib/session-boundary-contract.ts` | SessionBoundaryAnalyzer | `session_boundary` | `SessionBoundaryAlert`, `SessionBoundarySummary`, `SessionBoundaryDashboardState` |

同步更新了 `lib/analyzer-dashboard-contract.ts`：
- 新增三个 `dashboard_type`：`file_access_audit`、`token_cost`、`session_boundary`
- 新增三个 `IntegratedViewMode`：对应视图模式
- 流水线 Preprocessing 阶段新增 FileAccessAuditAnalyzer
- 流水线 AnomalyDetection 阶段新增 TokenCostAnalyzer、SessionBoundaryAnalyzer
- AlertEnrichment 阶段 `consumes` 新增 `token_cost_anomaly`、`session_boundary`
- 集成状态和回调完整扩展

## 3. 组件设计

### 3.1 FileAccessAuditDashboard

**文件路径**: `components/ui/file-access-audit-dashboard.tsx`

**视觉设计**:

```
┌──────────────────────────────────────────────────┐
│  📁 文件访问审计    [概览] [时间线] [按Agent] [按类型] │
├──────────────────────────────────────────────────┤
│  ┌─────────┐ ┌──────────┐ ┌──────────┐ ┌───────┐ │
│  │总访问数  │ │工作空间内  │ │跨边界访问 │ │高危   │ │
│  │ 1,234   │ │  1,056   │ │   178    │ │  23   │ │
│  └─────────┘ └──────────┘ └──────────┘ └───────┘ │
│                                                   │
│  风险分布                                          │
│  ████████████████░░░░ low  (89%)                   │
│  ████░░░░░░░░░░░░░░░░ medium (8%)                  │
│  █░░░░░░░░░░░░░░░░░░░ high (3%)                    │
│                                                   │
│  跨边界告警 (最近)                                  │
│  ┌─────────────────────────────────────────────┐  │
│  │ ⚠️ /etc/passwd — Write — claude (pid:1024)  │  │
│  │ ⚠️ /root/.ssh/id_rsa — Read — claude        │  │
│  │ ⚠️ /usr/lib/systemd/system/... — Stat       │  │
│  └─────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────┘
```

**视图模式**:

| 模式 | 描述 |
|------|------|
| `overview` | 工作空间全景快照：KPI 卡片 + 风险分布条 + 跨边界告警列表 |
| `timeline` | 时间线视图：按时间排序的文件访问事件流，高亮跨边界事件 |
| `by_agent` | 按 Agent 分组：每个 Agent 的访问摘要卡片（总访问数、跨边界比例、高危数） |
| `by_type` | 按访问类型分组：Read/Write/Execute/Stat/Delete 的饼图 + 统计表 |

**关键交互**:
- 点击跨边界告警行 → 展开详情面板，显示文件路径、访问类型、Agent 信息
- 按 Agent 视图点击 Agent 卡片 → 过滤该 Agent 的所有文件访问事件
- 风险分布条可点击分段 → 过滤对应风险等级的事件
- 工作空间根路径列表显示在顶部，支持配置

**与现有组件的联动**:
- `agent_root_pid` → 可跳转到 ProcessTreeView 查看进程信息
- `filepath` → 可跳转到 TimelinePanel 查看相关时间段的事件

### 3.2 TokenCostDashboard

**文件路径**: `components/ui/token-cost-dashboard.tsx`

**视觉设计**:

```
┌──────────────────────────────────────────────────┐
│  💰 Token 用量与成本  [概览] [告警] [按Agent] [趋势]│
├──────────────────────────────────────────────────┤
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────┐│
│  │输入Token  │ │输出Token  │ │累计成本   │ │速率  ││
│  │ 2.3M     │ │  450K    │ │ $12.35   │ │3.2K/s││
│  └──────────┘ └──────────┘ └──────────┘ └──────┘│
│                                                   │
│  成本按 Provider 分布                              │
│  ┌─────────────────────────────────────────────┐  │
│  │ Anthropic ████████████████ $9.20 (74%)      │  │
│  │ OpenAI    ██████░░░░░░░░░░ $3.15 (26%)      │  │
│  └─────────────────────────────────────────────┘  │
│                                                   │
│  Token 消耗趋势 (最近 5 分钟)                      │
│  │                                          ╭─   │
│  │                                   ╭──────╯     │
│  │                            ╭──────╯            │
│  │                    ╭──────╯                    │
│  │             ╭─────╯                            │
│  │────────────╯                                   │
│  └──────────────────────────────────────────────  │
│                                                   │
│  告警列表                                          │
│  ┌─────────────────────────────────────────────┐  │
│  │ 🐘 LargeRequest — warning — 85K input tokens│  │
│  │ 💥 OutputBloat — warning — ratio 5.2x       │  │
│  └─────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────┘
```

**视图模式**:

| 模式 | 描述 |
|------|------|
| `overview` | 全局快照：KPI 卡片（输入/输出/成本/速率）+ Provider 成本分布 + 最近告警 |
| `alerts` | 告警列表：所有 TokenAnomalyAlert，支持按类型/严重度/Agent/Provider 过滤 |
| `by_agent` | 按 Agent 分组：每个 Agent 的累计 token 用量和成本，可排序 |
| `by_provider` | 按 Provider 分组：各 LLM API provider 的用量占比和成本 |
| `trends` | 趋势图：累计 token 消耗折线图 + token 速率折线图（基于 TokenTrendDataPoint[]） |

**关键交互**:
- 趋势图支持时间范围拖拽选择
- Provider 分布条可点击 → 过滤该 provider 的告警和用量
- 告警行可展开 → 显示详细信息（input/output tokens、阈值、模型名、成本）
- 成本数据受 `token_source` 影响：`BodyEstimate` 来源的数据旁显示 `~` 前缀表示近似值
- 定价表可展开查看

**与现有组件的联动**:
- `agent_root_pid` → 跳转到 ProcessTreeView
- `endpoint` → 与 LLMApiAnomalyDashboard 的端点过滤联动
- `provider` + `model` → 可在交叉分析视图中与其他维度关联

### 3.3 SessionBoundaryDashboard

**文件路径**: `components/ui/session-boundary-dashboard.tsx`

**视觉设计**:

```
┌──────────────────────────────────────────────────┐
│  🔐 会话边界安全  [概览] [告警] [会话详情]         │
├──────────────────────────────────────────────────┤
│  ┌──────────┐ ┌──────────┐ ┌──────────────────┐  │
│  │活跃会话数  │ │总告警数   │ │最高异常类型       │  │
│  │    8     │ │    3     │ │ CrossUser (2次)  │  │
│  └──────────┘ └──────────┘ └──────────────────┘  │
│                                                   │
│  会话拓扑图                                        │
│  ┌─────────────────────────────────────────────┐  │
│  │                                             │  │
│  │  [Agent A:100]────[tls_ffff]──●──[user_x]   │  │
│  │       │                    ╲                 │  │
│  │       │ handoff         ⚠️ [user_z] ← 混入! │  │
│  │       ▼                                      │  │
│  │  [Agent B:200]────[tls_aaaa]──[user_y]      │  │
│  │                                             │  │
│  └─────────────────────────────────────────────┘  │
│                                                   │
│  异常详情                                          │
│  ┌─────────────────────────────────────────────┐  │
│  │ 👥 CrossUser — HIGH — tls_0xffff9999        │  │
│  │   user_z 混入了原本只有 user_x 的会话         │  │
│  │   Agent: claude (pid: 12345)                │  │
│  └─────────────────────────────────────────────┘  │
│  ┌─────────────────────────────────────────────┐  │
│  │ 🔓 UnauthorizedHandoff — HIGH               │  │
│  │   Agent 300 接收了来自 Agent 100 的 handoff │  │
│  │   目标不在预期接收方列表中                     │  │
│  └─────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────┘
```

**视图模式**:

| 模式 | 描述 |
|------|------|
| `overview` | 会话安全全景：KPI 卡片 + 会话拓扑图 + 异常类型分布 + 最近告警 |
| `alerts` | 告警列表：所有 SessionBoundaryAlert，支持按类型/严重度/Agent/Session 过滤 |
| `sessions` | 会话详情列表：各活跃 SessionBoundarySummary 卡片，显示 user_ids、conversation_id、突变次数 |

**会话拓扑图设计**:

用简单的 SVG/Canvas 绘制 Agent ↔ Session ↔ User 的关系图：
- 圆形节点 = Agent（显示 root_pid）
- 方形节点 = TLS Session（显示 session_id 前缀）
- 菱形节点 = User（显示 user_id）
- 实线边 = 正常关联
- 红色虚线边 = 异常关联（跨用户混入、未授权 handoff）
- 节点按状态着色：正常=绿色、异常=红色、不活跃=灰色

**关键交互**:
- 拓扑图中点击异常边 → 跳转到对应告警详情
- 会话卡片展开 → 显示该会话的完整 user_ids、conversation 历史、关联 Agent
- 告警行点击 → 展开详情面板，显示异常类型特有的字段

**与现有组件的联动**:
- `agent_root_pid` + `target_agent_root_pid` → 跳转到 ProcessTreeView
- `tls_session_id` → 可跳转到 TimelinePanel 查看该 session 的所有 TLS 事件
- `handoff_injection` 相关告警 → 可跳转到 HandoffInjectionDashboard（如果有）

## 4. 集成仪表板的标签页布局

更新 `analyzer-dashboard.tsx` 的 TAB_DEFS：

```tsx
const TAB_DEFS: { mode: IntegratedViewMode; label: string; icon: string }[] = [
  { mode: "pipeline_overview",  label: "流水线",     icon: "🔗" },
  { mode: "agent_loop",         label: "循环检测",   icon: "🔄" },
  { mode: "tool_execution",     label: "工具调用",   icon: "🔧" },
  { mode: "file_access_audit",  label: "文件审计",   icon: "📁" },  // 新增
  { mode: "token_cost",         label: "Token成本",  icon: "💰" },  // 新增
  { mode: "session_boundary",   label: "会话边界",   icon: "🔐" },  // 新增
  { mode: "dedup_diagnostics",  label: "去重诊断",   icon: "📊" },
  { mode: "cross_analysis",     label: "交叉分析",   icon: "🔀" },
];
```

新增交叉分析联动 key：
- `filepath` → 连接 FileAccessAuditDashboard ↔ TimelinePanel
- `session_key` → 连接 SessionBoundaryDashboard ↔ TimelinePanel
- `endpoint` → 连接 TokenCostDashboard ↔ LLMApiAnomalyDashboard

## 5. 实现优先级与分步计划

| 步骤 | 内容 | 预估工时 | 依赖 |
|------|------|---------|------|
| 1 | TypeScript 契约文件（已完成） | ✅ 完成 | 无 |
| 2 | `analyzer-dashboard-contract.ts` 集成更新（已完成） | ✅ 完成 | 步骤 1 |
| 3 | `FileAccessAuditDashboard` 组件实现 | 4h | 步骤 2 |
| 4 | `TokenCostDashboard` 组件实现 | 5h | 步骤 2 |
| 5 | `SessionBoundaryDashboard` 组件实现 | 4h | 步骤 2 |
| 6 | `analyzer-dashboard.tsx` 集成（新增 3 个标签页） | 2h | 步骤 3-5 |
| 7 | SSE 事件推送适配（后端 → 前端数据管道） | 3h | 步骤 2 |
| 8 | 端到端验证 | 2h | 步骤 3-7 |

**合计**: ~20h

## 6. 待团队评审

1. **会话拓扑图**是否需要使用 3d-force-graph（参考 `stage1-visual-quality-bar` 记忆）？还是先用简化版 SVG？
2. **Token 成本趋势图**：需要时间窗口聚合逻辑在前端还是后端？
3. **文件访问审计**的 `workspace_roots` 配置：前端是否需要一个设置面板让用户添加/删除工作空间路径？
4. 三个新仪表板的**紧凑模式**（`compact` prop）是否都需要支持？当前优先级？
5. 是否需要为这三个新分析器增加**交叉分析维度**（如 token 消耗 vs 文件访问量的关联分析）？

## 7. 文件清单

| 文件 | 操作 | 状态 |
|------|------|------|
| `frontend/lib/file-access-audit-contract.ts` | 新建 | ✅ 已创建 |
| `frontend/lib/token-cost-contract.ts` | 新建 | ✅ 已创建 |
| `frontend/lib/session-boundary-contract.ts` | 新建 | ✅ 已创建 |
| `frontend/lib/analyzer-dashboard-contract.ts` | 修改 | ✅ 已更新 |
| `frontend/components/ui/file-access-audit-dashboard.tsx` | 新建 | 🔲 待实现 |
| `frontend/components/ui/token-cost-dashboard.tsx` | 新建 | 🔲 待实现 |
| `frontend/components/ui/session-boundary-dashboard.tsx` | 新建 | 🔲 待实现 |
| `frontend/components/ui/analyzer-dashboard.tsx` | 修改（新增3标签页） | 🔲 待实现 |
