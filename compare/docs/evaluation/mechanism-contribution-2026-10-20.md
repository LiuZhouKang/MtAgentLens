# 机制贡献裁决与代码收口（2026-10-20）

> 问题：每个机制/模块/创新点是否**科学有效、真正改变判定**，能否写进论文。
> 方法：6 个只读 agent 的机制审计 + 当前 RC 的 DB 反算 + pilot 复测。
> 结论：**没有一项机制达到“核心且已证”**；本轮按证据把设计重排为
> “OS 证据平面 → 类型化证据 → Observer 仲裁 → 归因”，并删除/降级了
> 无法辩护的机制。论文只能按第 4 节的可写命题组织。

## 1. 裁决表（证据 → 动作）

| 机制 | 当前证据 | 裁决 | 本轮动作 |
|---|---|---|---|
| eBPF TLS uprobe / BoringSSL offset / process exec | 实测抓到明文与进程事件；875 SSL 分片里 1 条完整请求可解析 | 核心且已证（采集能力） | 保留 |
| PayloadNormalizer / SSE / HTTP1 | P1.2 有 42 条 SSE intents；但 sampled run 875 分片全部 partial、ok=0 | 核心但未证（“得到消息”未证） | 新增 `decode_status`/`partial` 直方图入 diagnostics |
| 身份解析（TLS+进程树+委派） | 单测；无真实多 Agent handoff；论文“三信号”实际只有两信号+委派 | 核心但未证 | 多跳 traceback；委派子进程独立；文档纠正“三信号” |
| PBC 计数（D1） | 去重后攻击/良性 distinct pair 均 0–3；旧 220/396.7 是重复计数 | 可疑 → 不是检测信号 | 去重；保留为图事实/证据 |
| 六维评分 + OWA/GeoMean + Platt | 无拟合数据；A=7/B=-3 硬编码；旧 DB 47% 可复现；D4/D6 可疑 | 可疑 → 降为路由启发式 | 新增 raw/calibrated/gate/版本/参数 provenance；不再称“校准” |
| 最小证据门（PBC 计数） | PBC≤6 时把分数压到 <0.15，Observer 根本不被调用 | 有害 | 有边界/模式/rule 时强制 Observer；门只影响引擎路径 |
| 图模式模板 | 6/12 依赖不存在的边；confidence 恒 1.0；曾把良性判 Unsafe | 可疑 | 删除两个无语义模板；pattern-only 不再直接 Unsafe；强制 Observer |
| CUSUM / Progressive | D4 从未 update；L0 AUROC≈0.5 | 可疑 | 从 Observer 触发中移除；保留为诊断 |
| 校准模块 / L2 先验 / 图简化器 | 无调用者/自反馈/EdgeRemoved=0 | 死代码 | 删除（calibration.rs、L2、simplifier.rs） |
| Observer authority | 由 `cache_ttl` 自报，可无证据抬升 Authoritative | 不安全 | 改为按 evidence_cited 派生（Supporting/Unavailable） |
| Observer 输入 | 只给 intent 的 Content 文本；看不到 Path/网络/凭据/rule ID | 核心缺陷 | 新增结构化 Paths/Network/Credentials/Rules/Patterns/Delegation |
| Traceback | 只 1-hop，把所有 outgoing action 串成星 | 实现与论文不符 | 多跳委派回溯 + evidence 匹配 + 上限 |
| TCU / taint / 委派 | 真实语料 0 边/0 claim；只有一个正例 | 辅助/归因，未证 | 保留代码；列为限制/待实验 |
| 带外 OpenClaw | 只作 asserted intent | 辅助/归因 | 不得参与 actionable |

## 2. 本轮代码收口（已验证）

- 删除零接线 TOTP/2FA（2,680 行）+ auth 测试/依赖；删除死 pipeline traits、
  no-op GraphConfig、calibration/L2/simplifier。
- 修复 `simple_glob_match`（`**.ssh/id_*`、`**/target/**` 之前从不匹配）。
- Unsafe 规则收窄：`/etc/passwd`、syslog、/proc、/usr/bin、ps aux 不再 Unsafe。
- 有边界/模式/unsafe rule → 强制 Observer（不再被 PBC 计数门压成 Safe）。
- Observer 收到结构化 OS 证据；authority 由证据派生；缓存只在会话内做 exact
  去重，无 L2 自反馈。
- `score_breakdown` 记录 raw/calibrated/gate/版本/Platt 参数/Observer 调整；
  `engine_section` 增加 `score_model_version`。
- 多跳 traceback：委派链 A→B→C 的根因为 A（测试覆盖）。
- `EvaluationSummary` 修复 phase1 计数被 drain 后归零的 bug。
- benchmark capture-health 改为解析真实事件与 `[diagnostics:json]`。
- 消融开关：`MTAGENTLENS_DISABLE_PATTERNS`、`MTAGENTLENS_DISABLE_DELEGATION`。

## 3. 仍不可写进论文（必须先有实验）

1. 攻击 actionable 召回：严格审计 68 个可检查场景仅 29 个有执行证据。
2. PBC 数量作为检测信号：已否证（去重后攻击/良性重叠）。
3. 六维评分作为“检测理论”或“Platt 校准”：无开发集/ECE；只能写路由启发式。
4. Observer 的独立贡献：无 on/off 配对；同输入有 safe/uncertain 震荡；
   需 temperature/seed、重复试验、token/延迟记录。
5. TCU/taint/委派检测能力：真实 handoff 未测。
6. 归因准确率/chain recall：无人工 GT/κ。
7. “三信号身份”：代码只有 TLS+进程树+委派，conversation_id 未入并查集。
8. 多跳 traceback 的真实效果：合成测试通过，真实 handoff 未测。

## 4. 论文可写的命题（按证据强度）

- **P1（可写为方法/实现）**：OS 证据平面在“必须产生 OS 副作用”的攻击类上
  提供零插桩观测；明确列出不可见类（HTTP/2、QUIC、kTLS、mmap、AF_UNIX 等）。
  需补 leave-one-source-out 覆盖率实验。
- **P2（可写为可证伪机制，待实验）**：类型化边界事件（敏感资源 × 外部端点 ×
  委派授权状态）+ Observer 语义仲裁；报告配对 on/off、成本、翻转率。
- **P3（可写为归因，待 GT）**：责任图/traceback 作为因果可达性证据；用
  Agent/Action/Chain 三层人工标注 + κ 评测。
- **P4（可写为负结果）**：PBC 计数、六维评分、CUSUM、过宽图模板在现有语料上
  不构成独立检测贡献；这本身是有价值的测量结论。
- **限制**：单进程多逻辑 Agent、加密协议盲区、语料执行率、Observer 依赖外部
  LLM。

## 5. 下一步实验（小样本，不扩规模）

| 格 | 机制 | 例组 | 指标 |
|---|---|---|---|
| A20 | syscall attempt vs success | NC2 失败 syscall + 成功对照 | Action 是否产生、TPR 分母 |
| A16 | ring buffer 丢包可见 | 高频 open / 大 payload | RINGBUF_STATS、dropped |
| A04 | 响应完成率 | benign-api-doc、c2、exfil | decode_status 直方图、SSE intents |
| A21 | intent-derived PBC 负控 | mention-only / 执行对照 | PBC、actionable（负控必须 0） |
| A22 | Observer on/off + 重复 | 3 执行攻击 + 3 困难良性 | ACT/FPR/OBC/翻转 κ |
| B01/B04/B13 | 逐维/门控离线 LOO | v2 可复现行 | route 翻转 |
| C03/C05 | taint/委派正负控 | H1–H5 | taint/委派/TCU |
