# MtAgentPrism 生产级前端实施路线图

## 1. 路线目标

本路线图用于把当前前端从“地球主视觉 + 图谱演示”迁移为“地球序章 + 责任星空 + 分层成熟监测”的生产级产品。

路线目标分为三类：

- 产品目标：锁定责任天文台隐喻，建立清晰的 L0-L3 分层结构。
- 工程目标：建立稳定的数据适配、路由结构、状态流和测试门禁。
- 交付目标：优先完成首页星空主舞台、cluster 页、agent 页和 investigation 闭环。

## 2. 当前基线判断

当前前端已经具备以下基础：

- 有可工作的首页三维主舞台基础设施。
- 已具备 `graph`、`alerts`、`evidence`、`snapshots`、`diff`、`events` 的 API 入口。
- 已具备 timeline / process tree / anomaly stream 的数据合同。
- 已具备调查页和 handoff 页的基础框架。

当前仍存在以下关键缺口：

- 首页隐喻长期摇摆在地球、图谱、装饰三维之间，主叙事不稳定。
- 缺少 cluster 和 agent 两级成熟监测页面。
- 三维主舞台承担了过多细节职责，缺少清晰钻取分层。
- handoff 与首页、调查页的视觉语义尚未彻底统一。

## 3. 交付阶段

## 阶段一：叙事与命名锁定

### 目标

把产品从“地球主视觉产品”正式切换为“责任天文台产品”。

### 任务

1. 固化“地球序章 + 责任星空 + L0-L3”文档
2. 统一首页、panel、页面标题和 CTA 语义
3. 统一 `run`、`alert`、`graph`、`cluster` 等用户可见命名
4. 明确首页禁用项，禁止回到装饰环和地图误导
5. 明确 cluster / agent / investigation 三层职责

### 验收标准

- 产品叙事、信息架构和实施路线图全部一致。
- 后续开发不再围绕“是否继续用地球做主舞台”反复摇摆。

## 阶段二：首页从地球主舞台切换为星空主舞台

### 目标

让首页成为“地球序章 + 责任星空”的品牌入口和 L0 总览入口。

### 任务

1. 把旧三维地球迁移为 opening sequence
2. 保留粒子垂落开场，但不再让地球长期停留
3. 用星空责任场替代地球承担首页总览
4. 用真实 `alerts` 和 `graph` 驱动 cluster summary
5. 提供进入 overview、cluster、investigation 的主路径

### 验收标准

- 首页不再依赖地理地球来解释产品。
- 星空主舞台能够表达 cluster、delegation、evidence 和 fracture。
- 首页存在清晰的进入下一层路径。

## 阶段三：L1 Cluster Observatory 上线

### 目标

建立 agent 集群级成熟监测页。

### 任务

1. 新建 `/clusters/[clusterId]`
2. 建立 cluster summary、health score 和 evidence density 模块
3. 接入 cluster constellation 与 suspicious route 高亮
4. 提供从 cluster 进入 agent 与 investigation 的跳转
5. 建立 cluster 页面专用 selectors 和 adapters

### 验收标准

- 用户可以明确看懂“一个集群为什么危险”。
- cluster 页面能自然承接首页和后续 agent 页面。

## 阶段四：L2 Agent Cockpit 上线

### 目标

建立单个 agent 的成熟监测页。

### 任务

1. 新建 `/agents/[agentId]`
2. 接入 identity、claimed role、runtime role 显示
3. 接入 parent / child delegation、touched resources、anomaly markers
4. 接入 supporting evidence summary
5. 打通从 agent 进入 investigation 的路径

### 验收标准

- 用户可以独立查看任意 agent 的责任状态。
- agent 页面不再依赖首页或 investigation 页面代为解释。

## 阶段五：L3 Investigation Lab 完整闭环

### 目标

建立最深层法证与裁定工作台。

### 任务

1. 完善 `/investigations/[eventId]`
2. 接入 causality chain、timeline replay、证据链
3. 接入 `diff` / `snapshots` 做变化解释
4. 建立 verdict justification 与 competing explanations
5. 支持导出调查摘要

### 验收标准

- 单个风险事件可完成完整法证闭环。
- cluster、agent、investigation 之间可以互相跳转。

## 阶段六：Overview、Handoff 与 Diagnostics 统一

### 目标

把专题页全部纳入同一责任星图语言。

### 任务

1. 统一 `/overview` 的 L0 结构
2. 重做 `/handoff` 为 delegation constellation 专题页
3. 补齐 `/diagnostics` 的系统可信度视图
4. 统一跨页面颜色、命名、风险等级和对象词典
5. 统一深浅主题下的主舞台和工作台风格

### 验收标准

- 首页、overview、handoff、investigation 不再像多个产品。
- 三维和工作台共享同一叙事语法。

## 阶段七：生产化完善

### 目标

让产品具备可持续迭代和真实生产使用条件。

### 任务

1. 权限与角色模型
2. 保存视图与分享链接
3. 统一时间上下文
4. 空态、错误态、降级态全面补齐
5. 性能优化与渐进加载
6. E2E、视觉验收和可访问性补齐

### 验收标准

- 首页、cluster、agent、investigation、handoff、diagnostics 均具备生产门禁。

## 4. 技术实施策略

## 4.1 数据适配层

建立独立的数据适配层，把 REST 和 SSE 统一映射为前端领域对象：

- ObservationSession
- RiskEvent
- ClusterSummary
- AgentProfile
- ResponsibilityGraph
- EvidenceChain
- InvestigationTimeline
- SystemHealthSnapshot

## 4.2 状态架构

建议拆成以下 store 或 selector 层：

- `useAppStore`：全局环境、时间、连接状态
- `useSessionStore`：观测会话与历史目录
- `useOverviewStore`：L0 总览态势
- `useClusterStore`：cluster 详情与路由上下文
- `useAgentStore`：agent cockpit 数据
- `useInvestigationStore`：调查上下文
- `useDiagnosticsStore`：系统健康与流状态

## 4.3 路由建议

- `/`
- `/overview`
- `/clusters/[clusterId]`
- `/agents/[agentId]`
- `/investigations/[eventId]`
- `/handoff`
- `/diagnostics`

## 4.4 组件分层建议

- `components/system/`
- `components/home/`
- `components/overview/`
- `components/cluster/`
- `components/agent/`
- `components/investigation/`
- `components/diagnostics/`
- `components/handoff/`

## 5. 代码迁移顺序

### 5.1 先锁定文档与对象词典

先完成蓝图、PRD、信息架构和路线图收口，再继续推进代码层三维与页面重构。

### 5.2 先完成首页与 L1-L3 路由骨架

优先完成首页、cluster、agent、investigation 的骨架与跳转关系。

### 5.3 再统一专题页

主链路稳定后，再统一 overview、handoff、diagnostics 的体验和语法。

## 6. 测试与验收计划

## 6.1 单元测试

- 数据适配器
- cluster / agent selectors
- 时间线与过程树索引工具
- 风险摘要和跳转推导逻辑

## 6.2 集成测试

- 首页进入 cluster
- cluster 进入 agent
- agent 进入 investigation
- 证据跳转到时间线

## 6.3 E2E 测试

- 首页地球序章到星空主舞台切换
- 进入实时态势
- 进入 cluster
- 进入 agent
- 进入高风险 investigation

## 6.4 浏览器验收

- 序章地球过渡质量
- 星空主舞台视觉纯净度
- cluster / agent / investigation 主路径无明显断裂
- 深浅主题下无明显失真
- 无明显控制台错误

## 7. 当前立即执行项

本轮开始立即执行以下事项：

1. 落实新的蓝图、PRD、信息架构与路线图
2. 明确首页禁用项与保留项
3. 为 `/clusters/[clusterId]`、`/agents/[agentId]` 设计数据映射和组件骨架
4. 为首页到 L1-L3 的路径准备统一导航与状态模型

## 8. 成功标准

当以下条件全部成立时，视为实施路线正确：

- 文档完整，能直接指导多人协作
- 首页完成从地球主舞台到星空主舞台的转向
- L1、L2、L3 分层路径成立
- handoff 与 investigation 纳入同一责任星图体系
- 后续所有页面沿同一命名、信息架构和工作流扩展
