# MtAgentLens 生产级前端设计总纲

本文档锁定 MtAgentLens 前端的产品化方向、信息架构、三维联动模型和重构路线。
后续前端改动应优先遵守本文档；若与历史 `.trae/` 设计草案冲突，以本文为准。

## 1. 产品定位

MtAgentLens 前端不是展示型安全大屏，也不是 benchmark 汇报页。它的生产定位是：

> 面向真实 Agent 运行环境的实时安全监测、责任归因与证据处置控制台。

核心用户不是来“浏览项目能力”，而是在生产环境中回答：

- 当前 Agent 系统是否正在发生危险执行。
- 哪个 Agent、进程、资源、边界或委托链是风险中心。
- 这个判断由哪些 OS 级事实支撑。
- 责任如何从全局图谱收束到具体主体、路径和证据。
- 下一步应该处置、复核、导出还是继续下钻。

## 2. 双模式原则

前端必须区分两个模式，避免互相污染。

### Production Console

默认模式。服务真实实时监测。

- 快速进入，不阻塞。
- 信息密度高，但层级清晰。
- 视觉服务判断和行动。
- Demo、benchmark、叙事动画不能伪装成 live 事实。
- 所有关键数字必须可追溯到真实数据或明确标注为示例。

### Presentation Mode

演示、答辩、官网级展示时使用。

- 可以保留地球序章、责任星图、叙事章节和更强动效。
- 可以展示 FuxiBench、场景故事和架构解释。
- 必须与生产导航、生产状态和真实告警队列分离。

## 3. 顶层信息架构

生产版一级导航应围绕实时处置闭环组织：

| 页面           | 职责         | 核心问题                       |
| -------------- | ------------ | ------------------------------ |
| Command Center | 实时态势入口 | 现在最需要看什么               |
| Incidents      | 风险事件队列 | 哪些事件需要处置               |
| Live Graph     | 责任图主舞台 | 风险在责任空间中在哪里         |
| Trace          | 时间线回放   | 危险行为如何发生               |
| Agents         | 主体画像     | 哪个 Agent 应承担关键责任      |
| Handoff        | 委托扩散     | 风险如何跨 Agent 传播          |
| Evidence       | 证据库       | 判断凭什么成立                 |
| System         | 采集可信度   | Collector、probe、SSE 是否可靠 |

`run` 不作为生产主导航。它应成为顶部上下文控件：当前 live session、历史回放、
时间窗口和环境选择。`Benchmarks` 不进入生产主导航，只属于 Presentation/Research Mode。

当前代码中的 `/overview`、`/clusters`、`/investigations` 可分阶段迁移：

- `/overview` 逐步收敛到 Command Center 的态势区。
- `/clusters` 逐步并入 Live Graph 的 cluster drilldown。
- `/investigations` 升级为 Incident Detail / Case Workbench。

## 4. 三维立体联动模型

3D 不应是页面背景装饰，而应成为全站共享的空间索引。用户体验目标是：

> 用户始终感觉自己在同一个运行现场里调查，只是从不同维度切换视角。

### 4.1 Drilldown 路径

```text
Command Center
  -> Live Graph
  -> Incident Detail
  -> Trace
  -> Evidence
  -> Agent / Handoff
  -> System
```

空间收束方式：

```text
全局责任场 -> 风险区域 -> 单个事件 -> 单条责任链 -> 单个证据点
```

### 4.2 各页面中的 3D 角色

| 页面            | 3D 使用方式                                                        |
| --------------- | ------------------------------------------------------------------ |
| Command Center  | 全局 live responsibility field，显示风险热点和最高优先级事件       |
| Live Graph      | 主 3D/2D 图谱，可探索、筛选、聚焦和下钻                            |
| Incident Detail | 局部图谱，只显示事件相关主体、路径、资源和边界                     |
| Trace           | 3D 退为 side minimap，时间线播放时同步点亮节点和边                 |
| Evidence        | 证据定位器，点击证据时锁定对应 file/network/process/tool 节点      |
| Agents          | 以单 Agent 为中心展示一跳/二跳邻域、委托和资源触达                 |
| Handoff         | 委托树或 2.5D propagation map，强调方向、fanout 和 orphan artifact |
| System          | 不使用沉浸 3D，使用 probe matrix、health timeline 和降级状态       |

## 5. 全局调查上下文

所有页面必须共享同一个 Investigation Context。页面跳转不能丢失调查现场。

必须共享的状态：

- `mode`: `live | replay | presentation`
- `timeWindow`
- `activeIncidentId`
- `activeAgentId`
- `activeClusterId`
- `activeNodeId`
- `activeEdgeId`
- `activeResourceId`
- `activeEvidenceId`
- `highlightedPathEdgeIds`
- `severityFilter`
- `confidenceThreshold`
- `graphFocus`: camera、zoom、selected object、focus reason

核心交互规则：

- 在 3D 图中点击 boundary crossing，Incident、Trace、Evidence 页面都应继承该路径。
- 从 Incidents 打开事件，Live Graph 应飞向相关 cluster/path。
- Trace 时间线播放时，图中的责任路径同步点亮。
- Evidence 卡片点击后，图中对应节点、边和原始事件应被锁定。
- Agent 页面打开后，Handoff 页面应自动展开与该 Agent 相关的委托分支。
- 返回 Command Center 时保留 breadcrumb 和 last focus。

## 6. 页面蓝图

### Command Center

首屏只回答：

- Collector 是否在线。
- 当前最高风险事件是什么。
- 风险集中在哪个 Agent / Boundary / Cluster。
- 证据覆盖是否足够。
- 下一步进入哪里。

布局：

- 顶部：环境、时间窗口、live/replay、collector 状态、全局检索。
- 中央：3D live responsibility field。
- 左侧：最高风险事件、风险趋势、处置入口。
- 右侧：实时事件队列、证据摘要、系统健康。
- 底部：probe health、SSE stream、latency、dropped events。

### Incidents

生产队列页面，优先使用表格和列表，不使用大 hero。

字段：

- Severity、Status、Decision、Confidence。
- Primary Agent、Boundary、Resource、Evidence Coverage。
- First Seen、Last Updated、Owner、Next Action。

### Live Graph

责任图主舞台。

必须支持：

- 3D/2D 切换。
- 按 Agent、Resource、Boundary、Evidence、Severity 着色。
- 按 incident、agent、cluster、resource 搜索和聚焦。
- 路径高亮、邻域展开、focus breadcrumb。
- 大图降级策略：聚合、抽样、虚拟化列表。

### Incident Detail

生产级调查工作台。

结构：

- 左栏：事件摘要、状态、主责任主体、受影响资源。
- 中栏：责任链、时间线、局部图谱。
- 右栏：证据、规则命中、observer verdict、处置动作。

### Trace

时间维度。

- Timeline 是主角。
- 图谱是同步定位器。
- 支持事件类型过滤：Agent、Tool、File、Network、Boundary、Verdict。
- 支持播放、暂停、跳转到证据。

### Agents

主体维度。

- 身份声明与 runtime identity 对齐。
- 最近触达资源。
- 委托上下游。
- 风险趋势和事件关联。
- 直接跳转 Incident、Trace、Handoff、Evidence。

### Handoff

多 Agent 传播维度。

- 委托树。
- context drift。
- fanout pressure。
- orphaned artifact。
- 跨 Agent 污点传播路径。

### Evidence

证据维度。

- 文件、网络、进程、stdio/TLS、工具调用、规则命中。
- 支持按 incident、agent、resource、confidence 过滤。
- 每条证据必须能定位回图谱和时间线。

### System

采集可信度。

- Probe matrix。
- Collector health。
- SSE 状态。
- 延迟、吞吐、丢失、降级。
- 权限与内核兼容提示。

## 7. 视觉系统

视觉方向：Agent Runtime Forensics。

设计关键词：

- 克制、精密、可信、可操作。
- 用空间表达责任，用表格和时间线表达证据。
- 首页可以惊艳，工作台必须高效。

### 色彩语义

| 语义     | 建议色  |
| -------- | ------- |
| Agent    | cyan    |
| Resource | violet  |
| Boundary | rose    |
| Evidence | amber   |
| Healthy  | emerald |
| Unknown  | slate   |

Severity 继续复用现有 `severity-palette`，但所有颜色编码必须有文字、图标或形状冗余。

### 形态

- 工作台卡片圆角：8-12px。
- Hero 或沉浸区域圆角：最多 16px。
- `999px` 只用于 chip、toggle、status pill。
- 生产页面避免过度 glass、orb、scanline、shimmer。
- 中文标题不使用负字距。
- 命令、路径、PID、host、evidence id 使用 mono 字体。

## 8. 组件与库策略

现有技术栈保留，不做一次性推倒。

建议新增或借鉴：

- Radix UI：Dialog、Popover、Tabs、Dropdown、Tooltip 等可访问 primitive。
- shadcn/ui 方法：组件源码进入本仓库，按产品语义改造，不黑盒套皮。
- TanStack Table：Incidents、Evidence、Agents、System Events。
- TanStack Virtual：大事件流、大证据列表、长 timeline。
- React Flow：生产态可操作 2D 图谱和 handoff graph。
- Apache ECharts：实时趋势、probe health、事件密度、系统指标。
- Three.js / 3D Force Graph：首页、Live Graph、Presentation Mode，不滥用到每个页面。

## 9. 加载策略

Production Console 默认不播放长动画。

加载层规则：

- 品牌启动 800-1200ms。
- 显示真实启动语义：Collector、probe、SSE、live graph、evidence stream。
- 超过预算直接进入 shell，显示 skeleton 和连接状态。
- Collector 不在线时显示明确空态，不能假装 demo 是 live。
- 完整地球序章只用于 Presentation Mode 或首次演示。
- 必须支持显式跳过。
- 必须尊重 `prefers-reduced-motion`。

## 10. 重构阶段

### Phase 1：设计锁定与基础交互

- 本文档落地。
- 顶部全局检索改为真实 button。
- 开场序章支持跳过、超时和 reduced motion。
- 建立 Investigation Context 设计与测试。

### Phase 2：全局联动骨架

- 新增全局 Investigation Context store。
- 将 Command Center、Trace、Investigation、Agent、Handoff 接入同一 selection。
- URL hash/query 与 store 双向同步。
- 增加 breadcrumb 和 focus reason。

### Phase 3：生产 IA 重排

- `/overview` 收敛为 Command Center。
- `/investigations` 升级为 Incident Detail。
- 新增或迁移 Incidents / Evidence / System 页面。
- 将 run 变成顶部上下文控件。

### Phase 4：Live Graph 与 Drilldown

- Live Graph 支持 3D/2D 切换。
- Trace 播放同步图谱高亮。
- Evidence 点击定位图谱与 timeline。
- Handoff 从专题页升级为事件传播视角。

### Phase 5：视觉收敛与质量门禁

- 拆分 `globals.css`。
- 收敛圆角、字距、颜色和组件状态。
- 补 Playwright 桌面/移动/浅色截图。
- 补键盘路径测试。
- 跑 lint、type-check、unit、build、responsive/a11y/interaction audits。

## 11. 验收标准

任一阶段完成前必须满足：

- 页面不再依赖不可验证的假指标伪装 live 状态。
- 用户从一个事件进入 Trace、Evidence、Agent、Handoff 时上下文不断裂。
- 3D 图谱的每次高亮都有可解释的对象、路径和证据。
- 移动端首屏仍能看到最高风险、状态和下一步动作。
- 键盘可完成主要路径：打开命令、选择事件、切换 tab、打开证据。
- `npm run type-check` 通过。
- 相关 Vitest/Playwright 测试通过。
