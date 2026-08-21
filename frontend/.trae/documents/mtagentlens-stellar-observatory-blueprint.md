# MtAgentLens 星空主舞台设计蓝图

## 1. 文档目的

本蓝图用于正式锁定 MtAgentLens 前端后续三维叙事与主舞台方向，避免继续围绕“地球是否足够炫酷”反复摇摆。

本次结论如下：

- 保留旧三维地球，但只作为加载序章使用。
- 首页正式主舞台改为“责任星空”而不是“地理地球”。
- 三维主舞台不再承担全部详情展示，只承担总览、定位和钻取入口职责。
- 详细判断、证据解释和成熟监测数据，放在分层面板与下一级页面中完成。

## 2. 产品总隐喻

MtAgentLens 的总隐喻正式定义为：

> 一座把多 Agent 混沌行为分解为可判定责任结构的责任天文台。

它由三部分共同组成：

- 地球序章：建立宏观、庄重、观测启动的仪式感。
- 责任星图：承担主舞台、态势入口与关系总览。
- 责任棱镜：承担深入调查时的分解、显影与判定语义。

换句话说：

- 地球负责“启动观测”。
- 星空负责“观察责任如何流动”。
- 棱镜负责“把复杂关系分解成可判定现场”。

## 3. 为什么不再让地球承担主舞台

地球不是错误的视觉对象，但它不是 MtAgentLens 最准确的核心隐喻。

地球方案存在四个长期问题：

- 它天然让用户优先联想到地理分布，而不是责任归因。
- 它会诱导经纬线、全球弧线、热区地图等偏地理化语法。
- 它会把多 Agent 关系错误翻译成“全球网络球”。
- 它很容易滑向泛安全大屏或泛 AI 官网视觉。

MtAgentLens 真正要表达的是：

- authority 如何声明
- delegation 如何传播
- boundary 如何被越过
- evidence 如何沉积
- verdict 如何收束

因此主舞台应从“地球对象”切换到“责任关系宇宙”。

## 4. 三维叙事总结构

首页和后续主要页面采用四幕叙事：

### 第一幕：地球序章

- 页面启动时短暂出现旧三维地球。
- 保留用户喜欢的粒子垂落开场效果。
- 时间控制在 1.5 到 3 秒之间。
- 地球只承担“观测系统已启动”的情绪价值，不承担长期信息职责。

### 第二幕：地球解构

- 粒子从地球表面或外层逐渐脱离。
- 地球不再继续作为主对象停留。
- 点云、轨迹和光尘重组为更抽象的责任场。

### 第三幕：责任星图成形

- 首页正式进入责任星空主舞台。
- Agent 以星体锚点或星群形式出现。
- delegation 以航迹或轨道流出现。
- evidence 以星云和尘带出现。
- boundary crossing 以裂变区或禁区扰动出现。

### 第四幕：判定收束

- 选中的异常路径被高亮。
- 风险重心向单个 cluster 或单个 agent 收束。
- 右侧或下级面板给出成熟监测摘要和 verdict framing。

## 5. 主舞台对象词典

首页主舞台只允许出现以下六类核心三维对象。

### 5.1 Star Anchor

表示单个关键 agent、runtime identity、role pivot 或重要资源锚点。

语义：

- 谁在行动
- 谁是责任主语
- 哪个对象值得继续钻取

### 5.2 Constellation Cluster

表示一个 agent cluster、sub-agent team、delegation family 或围绕同一任务形成的协作群。

语义：

- 哪个群体正在成为风险中心
- 一个异常不是单点，而是成团扩散

### 5.3 Delegation Orbit

表示 authority、delegation、context handoff 和执行责任的传播路径。

语义：

- 责任如何流动
- 上下游关系如何形成
- 风险是如何扇出、回收或偏离

### 5.4 Evidence Nebula

表示日志、文件、网络、进程、规则命中、observer 结论等证据沉积层。

语义：

- 为什么系统认为这里可疑
- 证据密度在哪里聚集
- 哪些区域值得深入

### 5.5 Fracture Zone

表示 boundary crossing、taint propagation、policy breach、identity drift 等越界行为。

语义：

- 不是普通的委派，而是发生了越界
- 不是普通热点，而是结构性破损

### 5.6 Gravity Lock

表示系统最终将责任判断收束到某个对象、路径或 cluster 的中心。

语义：

- verdict 的落点
- confidence 的集中区域
- 调查应该优先进入哪里

## 6. 首页只允许回答的问题

首页不是详情页，首页只回答四个问题：

- 现在最值得关注的风险中心在哪里
- 哪些 cluster 或 agent 正在形成异常扩散
- 风险是如何传播、越界并留下证据的
- 用户下一步应该进入哪个 cluster 或哪个调查入口

首页不负责完整解释所有证据，不负责展开全部节点，也不负责承载法证级表格。

## 7. L0 到 L3 的分层观测模型

MtAgentLens 前端采用四层钻取结构。

### L0：Stellar Overview

定位：

- 全局态势层
- 首页和总览层的核心视图

展示：

- 主要 cluster
- 全局风险热区
- delegation 流向
- evidence 密度
- boundary fracture
- 当前值得进入的对象

回答：

- 哪里最危险
- 哪个集群正在失控

### L1：Cluster Observatory

定位：

- 集群级成熟监测面板

展示：

- cluster health score
- active agents
- recent delegation fan-out
- boundary drift
- evidence concentration
- top suspicious routes
- recent verdict trend

回答：

- 这个 cluster 为什么危险
- 这个 cluster 里最值得继续看哪个 agent

### L2：Agent Cockpit

定位：

- 单个 agent 的成熟监测面板

展示：

- identity / claimed role / runtime role
- parent and child delegation
- touched resources
- active tasks
- anomaly markers
- confidence / risk score
- evidence summary

回答：

- 这个 agent 到底做了什么
- 它为什么成为责任主语或关键传播点

### L3：Investigation Lab

定位：

- 最深层的法证与裁定工作台

展示：

- causality chain
- timeline replay
- process / file / network evidence
- policy boundary explanation
- competing explanations
- verdict justification

回答：

- 我们凭什么得出这个判断
- 这个判断在证据上是否站得住

## 8. 页面级映射

页面结构按以下方式统一：

- `/`
  - 首页，包含地球序章和星空主舞台
  - 默认承载 L0
- `/overview`
  - 强化版总览页
  - 仍以 L0 为主，但提供更多过滤和时间切片
- `/clusters/[clusterId]`
  - cluster 级成熟监测页
  - 承载 L1
- `/agents/[agentId]`
  - 单 agent 监测页
  - 承载 L2
- `/investigations/[eventId]`
  - 调查与法证页
  - 承载 L3
- `/handoff`
  - 强调 delegation、fan-out、return path 和 drift
  - 作为星图体系的专题页而不是独立视觉物种

## 9. 数据语义映射

前端三维对象必须与现有后端数据合同建立直接映射。

| 数据来源 | 三维语义 | 页面落点 |
|---------|----------|----------|
| `graph.nodes` | Star Anchor / Cluster Core | L0 / L1 / L2 |
| `graph.edges` | Delegation Orbit / Propagation Route | L0 / L1 / L3 |
| `alerts` | Gravity Lock / Priority Beacon | L0 / L1 |
| `evidence` | Evidence Nebula | L1 / L2 / L3 |
| `events` | Comet Trail / Timeline Activity | L0 / L3 |
| `snapshots` / `diff` | Evolution Slice / State Change | L1 / L3 |

## 10. 首页视觉禁用项

从本蓝图生效起，首页主舞台禁止重新引入以下语法：

- 把地球长期作为首页唯一主角
- 明显经纬线或地理轮廓依赖
- 无实际语义的双环、靶心环、装饰大环
- 把整张图数据库直接摊进首页三维球
- 大量无区分几何体噪音
- 没有责任语义的泛 AI 能量核视觉
- 用厚重卡片堆满首页首屏

## 11. 首页视觉保留项

以下元素可以保留，但必须被纳入新叙事：

- 地球序章中的粒子垂落开场
- 克制的空间尘埃和远景星海
- 少量高价值高亮路径
- 局部 cluster 的收束高亮
- 与当前最重要异常绑定的右侧摘要面板

## 12. 开发原则

### 12.1 三维负责定位，不负责一切

三维主舞台的职责是：

- 建立认知
- 提供态势
- 引导进入

三维主舞台不负责展开所有详情。

### 12.2 面板负责解释与判断

成熟监测数据、表格、证据链、评分和裁定，都应该放在分层面板和专题页面，而不是硬塞进首页主舞台。

### 12.3 首页必须保持“少而准”

首页应是高价值对象的筛选器，而不是所有信息的容器。

### 12.4 所有页面必须共享同一叙事语言

首页、cluster 页、agent 页、investigation 页与 handoff 页，必须共享：

- 相同对象命名
- 相同风险颜色语义
- 相同 delegation / evidence / fracture / verdict 词典

## 13. 实施优先级

第一优先级：

- 固化文档和命名
- 完成首页从地球主舞台到星空主舞台的切换
- 把旧地球迁为加载序章

第二优先级：

- 建立 L1 cluster 页
- 建立 L2 agent 页
- 统一右侧成熟监测摘要卡结构

第三优先级：

- 完成 L3 investigation lab
- 打通 evidence、timeline 和 verdict 的互跳
- 统一 handoff 页到同一星图叙事

## 14. 成功标准

当以下条件同时成立时，可视为新叙事落地成功：

- 用户不再把 MtAgentLens 理解成“全球地图产品”
- 首页能够在短时间内建立“责任天文台”的认知
- 用户能从 L0 顺畅进入 L1、L2、L3
- 三维主舞台与成熟监测面板职责清晰，不再互相抢戏
- 旧地球的粒子垂落记忆点被保留，但不再绑架整个产品语义
