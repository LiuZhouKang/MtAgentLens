# MtAgentLens 演示视频说明

本文用于配合 `MtAgentLens初赛演示视频.mp4` 阅读。视频时长约 20 分钟，内容按照赛题背景、系统设计、真实运行、评估结果和性能开销依次展开。

## 内容索引

- [背景与赛题对应](#背景与赛题对应)
- [视频结构](#视频结构)
- [演示场景](#演示场景)
- [系统实现与输出](#系统实现与输出)
- [评估结果与性能](#评估结果与性能)
- [阅读顺序](#阅读顺序)

## 背景与赛题对应

赛题要求面向多智能体场景构建系统级异常监测机制，重点是三件事：

1. 捕获多层级运行事实。
2. 建立异常归因链路。
3. 输出可回溯、可复核的告警。

MtAgentLens 的实现路径与这一要求一致：在 Linux 内核边界通过 eBPF 采集进程、文件、网络、stdio 和支持环境下的 TLS 明文片段；在用户态用 Rust collector 将事件整理为责任图和证据链；在前端和数据库中保留可审计的调查结果。

公开视频中加入了两个现实案例，用于说明这类监测的必要性：

- Amazon Q Developer for VS Code 扩展 1.84.0 的供应链污染事件。
- Replit Agent 删除生产数据库事件。

这两类事件共同说明，风险已经不只存在于提示词文本中，还会进入文件、进程、网络和工具调用链路。

## 视频结构

视频内容按以下顺序组织：

| 片段 | 内容 | 说明 |
|------|------|------|
| 开头 | 赛题背景、现实案例、问题定义 | 系统级观测需求 |
| 中段 1 | 系统架构、eBPF 采集、责任运行时 | 运行事实获取与责任链构建 |
| 中段 2 | `shadow-spawning` 真实运行 | 短命 worker、临时证据、父 Agent 归因 |
| 后段 1 | 数据库、前端调查视图 | 告警回溯与责任主体定位 |
| 后段 2 | FuxiBench、指标、消融、性能 | 覆盖面、误报控制、端侧开销 |

## 演示场景

视频的演示场景是 `shadow-spawning`。该场景展示父 Agent 派生多个短命 worker，worker 在临时目录写入证据文件后退出，父进程再汇总 summary。

该场景关注的是归因过程，而不是单个进程是否可疑。对应关注点如下：

- 父 Agent 与 worker 的关系是否被保留。
- 临时证据文件是否被记录。
- 短命 worker 退出后，责任链是否仍然完整。
- summary 是否可以回溯到前面的执行步骤。
- 告警是否能指向明确的责任主体。

视频中对应展示了三类结果：

| 结果 | 说明 |
|------|------|
| 终端输出 | benchmark 启动、collector 采集、场景执行、结果生成 |
| `trace.responsibility.db` | `actions`、`agent_identities`、`graph_edges`、`graph_snapshots` 等结构化结果 |
| 前端页面 | 总览、trace workbench、investigation、handoff evidence 等调查视图 |

## 系统实现与输出

### 采集层

系统在内核边界采集运行事实，覆盖：

- 进程：`fork`、`exec`、`exit`
- 文件：`open`、`unlink`
- 网络：连接地址和端口
- 本地通信：stdio
- 支持环境下的 TLS 明文片段

### 分析层

Rust collector 负责：

- 事件标准化
- 身份解析
- 污点追踪
- 责任图构建
- 异常分类
- 告警生成

### 展示层

前端按 run 组织调查结果，主要用于：

- 浏览责任图
- 查看告警详情
- 追踪证据链
- 回看委派链与跨 Agent 传播路径

## 评估结果与性能

为避免仅用单个场景说明系统有效，项目构建了 FuxiBench 主评估集。

| 维度 | 数据 |
|------|------|
| 总场景 | 86 |
| Attack | 67 |
| Benign | 19 |
| 风险类别 | 14 |
| Type A 直接注入 | 10 |
| Type B 间接注入 | 34 |
| Type C 多 Agent 攻击 | 23 |

Benign 场景包括构建、迁移、测试、发布和日志聚合等复杂工作流，用于验证误报控制。

主评估结果如下：

| 指标 | 结果 |
|------|------|
| TPR | 0.896 |
| FPR | 0 |
| F1 | 0.945 |
| MCC | 0.809 |
| Attack 检出 | 60 / 67 |
| Benign 误报 | 0 / 19 |
| Type C 多 Agent 攻击 TPR | 1.000 |
| Observer 调用率 | 39.5% |

消融结果显示，移除 PBC、Identity、Taint 后，覆盖率从 1.000 降到 0.534，说明核心增益来自 OS 责任链特征。

性能方面，视频给出的基准结果为：

| 指标 | 结果 |
|------|------|
| SSL enter probe | 101.3 ns/op |
| SSL read exit probe | 57.6 ns/op |
| SSL write exit probe | 59.4 ns/op |
| 单次 SSL read/write 总 probe 成本 | 218.3 ns/op |
| collector 空闲 RSS | 36.8 MB |
| 合成 SSL 事件管线吞吐量开销 | <3% |

## 阅读顺序

若需要按重点阅读，可按以下顺序查看：

1. 先看视频开头，了解赛题背景和现实问题。
2. 再看系统架构和 eBPF 采集部分，确认数据从哪里来。
3. 查看 `shadow-spawning`，确认如何从短命 worker 还原责任链。
4. 再看数据库和前端部分，确认告警如何回到证据。
5. 最后看 FuxiBench 和性能部分，确认覆盖面和端侧开销。

## 对应材料

- 演示视频：[../MtAgentLens初赛演示视频.mp4](../MtAgentLens初赛演示视频.mp4)
- 项目 README：[../README.md](../README.md)
- 系统架构：[architecture.md](architecture.md)
- 实验设计：[design/experiment-design.md](design/experiment-design.md)
- 评分引擎：[design/scoring-engine.md](design/scoring-engine.md)
