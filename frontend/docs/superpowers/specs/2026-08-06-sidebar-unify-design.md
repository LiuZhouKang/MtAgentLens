# 左侧侧边栏统一升级 · 设计规格

> 日期：2026-08-06 · 状态：已批准（方向 A：上下文事件检查器）
> 约束（硬性）：**不得破坏现有视觉效果**（运动 / 毛玻璃 / 流体头 / CountUp 等全部保留）。

## 背景与诊断

页面已精简为 4 个：`/`(总览)、`/overview`(仪表盘)、`/investigations`(告警)、`/traces`(追踪)。
但左侧侧边栏 `IncidentRail` 仍是旧设计的"5 工具事件检查器"，存在：

1. **死链（bug）**：`/agents`、`/handoff`、`/clusters` 指向已删除页面，点击 404。
2. **工具集残留**：dock 5 个工具（事件/证据/链路/主体/委派），其中"主体""委派"对应页面已不存在。
3. **与 4 页面结构脱节**：顶部栏是 4 页面导航，侧边栏是另一套 5 工具导航，两套概念并存。
4. **死组件残留**：`observatory-header.tsx`（含 `/clusters` 死链）、`resizable-rail-layout.tsx` 均无页面使用。

## 设计决策

### 1. 侧边栏工具集：5 → 3（事件 / 证据 / 链路）

| 原工具 | 去向 |
|--------|------|
| 事件 (incident) | **保留**——当前告警总览（主体/判定/权限在事实快照中已有） |
| 证据 (evidence) | **保留**——证据链工作台 |
| 链路 (trace) | **保留**——责任链路工作台，**并入委派**（委派边本就是路径边，增加"委派与边界转移"卡片） |
| 主体 (agent) | **并入事件**——主体信息已内聚在事件视图；增加"主体画像"二级链接 → `/overview#agentId=` |
| 委派 (handoff) | **并入链路**——委派/边界转移 SignalList 移入链路视图 |

### 2. 路由重定向（一处改、全局生效）

| 构建器 | 新目标 | 理由 |
|--------|--------|------|
| `buildAgentHref(id)` | `/overview#agentId=<id>` | 仪表盘已有 agent 风险/图形焦点机制 |
| `buildClusterHref(id)` | `/overview#clusterId=<id>` | 仪表盘已有责任集群摘要 |
| fuxibench "打开拓扑" | `/overview` | 拓扑/集群语义收敛到仪表盘 |

自动修复全部消费者：`incident-rail`、`node-detail-panel`、`investigation-context-bar`、`command-linking`。

### 3. overview 支持 `#agentId` / `#clusterId` 焦点参数

overview 焦点链已由 `selectedAlertId` 驱动。新增 hash 解析：
- `#agentId=<id>` → 选中 `primary_agent.agent_id === id` 的首条告警
- `#clusterId=<id>` → 选中该集群的 leadAlertId
- 复用现有 `selectedAlertId → selectedCluster → focusCluster → graphFocus` 链条，**零新增状态**。
- 用 `useHashRouteState`（现有 hook）+ 新 `parseOverviewFocus` / `buildOverviewFocusHref`。

### 4. 死组件清理

- 删除 `components/system/observatory-header.tsx` + `__tests__/observatory-header.test.tsx`（仅测试引用）。
- 删除 `components/command-center/resizable-rail-layout.tsx`（完全无引用）。

## 不做的事（防范围蔓延）

- 不改变侧边栏"有数据时显示"的现有行为。
- 不改顶部 4 页面导航。
- 不引入新 UI 组件库；视觉语言沿用现有 `incident-rail` + Linear×Tremor 风格。
- 不动 `use-alert-models`、LOD、worker 等性能架构。

## 测试策略

- 更新 `command-linking.test.ts`（`surfaceHref` 期望改为 `/overview#...`）。
- 新增 `agent-route`/`cluster-route` 构建器目标断言（`buildAgentHref`/`buildClusterHref`）。
- 新增 `overview-focus` hash 解析单元测试。
- 回归：全量 vitest + eslint 0 警告 + tsc。
