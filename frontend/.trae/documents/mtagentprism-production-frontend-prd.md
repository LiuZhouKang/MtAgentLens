# MtAgentPrism 生产级前端 PRD

## 1. 产品定义

MtAgentPrism 前端不再定义为“一个展示 graph 或 demo run 的三维页面”，而是定义为：

> 一套让多 Agent 系统中的 authority、delegation、boundary crossing、evidence 与 verdict 变得可观测、可钻取、可判定的责任观测产品。

本轮产品方向正式锁定为：

- 地球不是主舞台，只保留为加载序章。
- 星空不是装饰背景，而是责任关系主舞台。
- 详细判断不在首页完成，而通过分层钻取进入成熟监测面板。

这意味着前端必须同时满足三类能力：

- 对外：具备品牌级叙事和清晰的第一眼认知。
- 对内：具备从全局态势到单个 agent 的成熟监测闭环。
- 对工程：具备稳定的数据映射、路由结构和测试门禁。

## 2. 产品一句话

> MtAgentPrism 是一座责任天文台，把多 Agent 的混沌行为分解成可判定的责任结构。

## 3. 目标用户

### 3.1 安全负责人

- 关心哪里最危险。
- 关心哪些 agent cluster 正在扩散风险。
- 需要在最短路径内进入调查与处置。

### 3.2 Agent 平台负责人

- 关心 delegation 是否偏离 authority。
- 关心 sub-agent cluster 是否出现边界漂移。
- 需要识别单个 agent 与 cluster 级别的责任传播结构。

### 3.3 基础设施 / 可观测性团队

- 关心证据链是否完整。
- 关心 timeline、process、graph 和 alerts 是否互相对齐。
- 需要验证系统级健康状态与降级情况。

### 3.4 产品 / 商务 / 路演用户

- 关心产品价值能否一眼理解。
- 需要一个真正成熟、有记忆点的旗舰入口。
- 需要看到从宏观态势到细粒度调查的完整产品纵深。

## 4. 核心问题

当前前端需要解决的根问题不是“地球够不够酷”，而是以下四个问题：

1. 首页长期把地球、图谱、装饰三维和工作台语义混在一起，导致主隐喻不稳定。
2. 用户可见层仍残留实验语境，尚未成为生产级观测产品。
3. 三维主舞台承载了过多细节，缺少真正的分层钻取路径。
4. 现有 graph、alerts、evidence、events、snapshots、diff 数据已具备基础，但尚未被组织成成熟监测与调查闭环。

## 5. 产品目标

### 5.1 业务目标

- 让首页成为“责任天文台”的旗舰入口。
- 让责任星图成为 MtAgentPrism 的独特认知锚点。
- 让 cluster 和 agent 成为可独立进入的成熟监测对象。
- 让 investigation 成为最终法证与裁定工作台。

### 5.2 体验目标

- 第一屏在 3 秒内建立“责任观测产品”认知。
- 从首页进入目标 cluster 或高风险事件的路径不超过 2 次点击。
- 从 cluster 到 agent 再到 investigation 的层层钻取路径清晰可预期。
- 用户始终知道“哪里异常、为什么异常、下一步该点哪里”。

### 5.3 工程目标

- 统一三维语义词典、页面命名和数据映射。
- 建立 L0-L3 分层信息架构。
- 建立稳定的数据适配层，兼容 REST 快照和 SSE 增量流。
- 为首页、cluster、agent、investigation、handoff 建立统一设计系统。

## 6. 核心产品原则

### 6.1 地球只作为序章

旧三维地球保留，但只承担加载起始页和观测启动仪式感，不再承担首页长期主叙事。

### 6.2 星空承担主舞台

首页主舞台正式定义为“责任星空”：

- star anchor 表示关键 agent 或责任主体
- constellation cluster 表示 agent 集群
- delegation orbit 表示责任流
- evidence nebula 表示证据沉积
- fracture zone 表示边界破裂
- gravity lock 表示 verdict 收束

### 6.3 三维只做总览与导航

三维主舞台的职责是：

- 建立认知
- 展示态势
- 引导进入

三维主舞台不负责承载全部详情、法证表格和所有节点。

### 6.4 面板负责解释与判断

成熟监测数据、证据、评分、角色漂移、因果链和 verdict justification，必须进入分层面板和专题页面完成。

### 6.5 所有视觉必须服务于责任理解

视觉系统必须持续帮助用户理解：

- 发生了什么
- 风险如何扩散
- 谁是责任主体
- 证据为什么成立

## 7. 关键信息对象

### 7.1 观测会话

对内仍可由 `run_id` 支撑，对外统一命名为“观测会话”。

职责：

- 组织一次完整观测上下文
- 提供时间、环境和数据范围
- 作为首页、总览、调查的共同边界

### 7.2 风险事件

由 alerts、anomalies、rule hits、observer verdict 等共同构成。

职责：

- 首页和 overview 的优先入口对象
- cluster 页和 investigation 页的调查起点

### 7.3 Agent Cluster

由多个 agent、sub-agent 或围绕同一任务/委派关系形成的协作群构成。

职责：

- L1 层的成熟监测对象
- 解释群体级风险传播和 fan-out

### 7.4 Agent

单个 runtime identity、角色实例或责任关键点。

职责：

- L2 层的成熟监测对象
- 承担身份、行为、资源接触、委派关系和局部异常解释

### 7.5 证据链

由 evidence、timeline entries、process events、network/file artifacts、observer 结论组成。

职责：

- 证明风险判断
- 支撑 verdict
- 形成可回溯的法证基础

## 8. 分层产品结构

### 8.1 L0 总览层

定位：

- 全局态势层
- 责任星空主舞台

核心目标：

- 告诉用户哪里最异常
- 告诉用户哪个 cluster 值得进入

### 8.2 L1 集群层

定位：

- cluster observatory

核心目标：

- 解释一个 cluster 为什么危险
- 指出 cluster 内最值得继续看的 agent

### 8.3 L2 单 Agent 层

定位：

- agent cockpit

核心目标：

- 解释单个 agent 到底做了什么
- 判断它是否是责任主语、传播点或被拖累节点

### 8.4 L3 调查层

定位：

- investigation lab

核心目标：

- 通过完整证据、timeline、process、boundary explanation 和 verdict justification 完成法证闭环

## 9. 核心页面

### 9.1 首页 Home

定位：

- 品牌旗舰入口
- 地球序章 + 责任星空主舞台
- L0 总览入口

首页必须回答：

- MtAgentPrism 是什么
- 当前哪里正在形成责任异常
- 哪些 cluster 值得立即进入
- 为什么这不是普通日志或 trace 产品

首页核心模块：

- 地球序章 opening
- 责任星空 hero
- 当前最重要 cluster / alert 摘要
- 进入 overview、cluster、investigation 的主入口

### 9.2 实时态势 Overview

定位：

- L0 的强化版工作入口

核心模块：

- cluster ranking
- risk trend
- active anomalies
- top suspicious routes
- recent evidence surge

### 9.3 Cluster Observatory

定位：

- L1 页面

核心模块：

- cluster health score
- active agents
- delegation fan-out
- evidence density
- boundary drift
- suspicious routes

### 9.4 Agent Cockpit

定位：

- L2 页面

核心模块：

- identity / claimed role / runtime role
- parent / child delegation
- touched resources
- active tasks
- anomaly markers
- evidence summary
- current confidence

### 9.5 Investigation Lab

定位：

- L3 页面

核心模块：

- event summary
- causality chain
- timeline replay
- process / file / network evidence
- policy boundary explanation
- verdict and justification

### 9.6 Handoff

定位：

- delegation 专题页

核心模块：

- handoff constellation
- fan-out / return path
- context drift
- cluster to cluster propagation

### 9.7 Diagnostics

定位：

- 系统可信度保障页

核心模块：

- 流完整性
- 采集健康度
- 时间线延迟
- process/tree 对齐状态
- 降级模式说明

## 10. 数据价值映射

### 10.1 `/api/runs`

- 映射为观测会话。
- 作为环境、时间和数据范围边界。

### 10.2 `/api/runs/:id/graph`

- 映射为 star anchor、cluster core、delegation orbit 的基础结构。
- 服务于首页、overview、cluster、agent、investigation。

### 10.3 `/api/runs/:id/alerts`

- 映射为 risk events 和 gravity lock。
- 服务于首页、overview、cluster 和 investigation 入口。

### 10.4 `/api/runs/:id/evidence`

- 映射为 evidence nebula 与证据面板。
- 服务于 L1-L3 的解释层。

### 10.5 `/api/runs/:id/events`

- 映射为 comet trail、timeline activity。
- 服务于首页轻摘要和 investigation 时间线。

### 10.6 `/api/runs/:id/snapshots`

- 映射为时刻切片与状态重建。
- 服务于 cluster 和 investigation 的状态回放。

### 10.7 `/api/runs/:id/diff`

- 映射为变化解释层。
- 用于解释某个时间段内风险如何收缩或扩散。

## 11. 非功能要求

### 11.1 生产化要求

- 支持环境切换
- 支持统一时间范围
- 支持角色与权限
- 支持空态、错误态和降级态
- 支持分享链接和调查摘要导出

### 11.2 性能要求

- 首屏优先建立认知与主入口，不一次性加载所有细节
- L1-L3 页面按需加载更高密度数据
- 低性能设备自动降低三维后期效果和粒子预算

### 11.3 质量要求

- 关键页面具备构建验证
- 核心数据适配器具备单元测试
- 首页、cluster、agent、investigation 具备浏览器级验收

## 12. 成功标准

- 用户第一眼不再把 MtAgentPrism 误解为全球地图产品。
- 首页成功建立“责任天文台”认知。
- 地球序章被保留，但不再绑架整个产品语义。
- 用户可以从 L0 顺畅进入 L1、L2、L3。
- cluster、agent、investigation 三层成熟监测路径成立。
- 文档、命名、信息架构和路线图统一，可直接指导后续开发。
