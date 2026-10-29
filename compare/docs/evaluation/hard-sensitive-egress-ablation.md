# 硬规则消融：credential-read → egress（replay，零模型调用）

> 背景：release2 campaign 中 19 个真正执行的攻击里，只有 4 个 Unsafe；
> 其余多数卡在 review，且 Observer 常答"uncertain"。其中 6 个的图里存在
> **确定性**证据链：同一 agent 先读 credential-class 文件、再向网络发送数据
> （`sensitive_egress_candidates`，此前只喂给 Observer，未用于裁决）。
> 本消融检验：把这条确定性链升级为 hard rule，能否在不增加 benign 误报的前提下
> 提升可行动率。

## 1. 实现（TDD）

- `ClassificationConfig.hard_sensitive_egress`（`#[serde(default)]`，默认 false，
  旧 checkpoint 可反序列化）；
- `AnalysisCheckpoint::evaluate`：在规则引擎之后，若该 flag 开启且
  `candidates::sensitive_egress_candidates(&graph)` 非空，则追加
  `Observation::HardViolation { rule_ids: ["hard.sensitive_egress"] }`；
- 运行时可用 `MTAGENTLENS_HARD_SENSITIVE_EGRESS=1` 开启；默认保持被测口径（false）；
- 测试：`hard_sensitive_egress_promotes_an_executed_exfiltration_chain`、
  `hard_sensitive_egress_is_off_by_default`（含旧 checkpoint JSON 反序列化）。

## 2. 验证方法（零 token）

`mtagentlens replay-sensitivity` 对 61 个已记录 run 的 frozen checkpoint 重算裁决
（当前 kernel on recorded inputs）：

```bash
BIN=collector/build/release/mtagentlens
DBS=$(awk '{printf " --db %s", $0}' /tmp/last61.txt)
$BIN replay-sensitivity $DBS --out /tmp/sens-off.json --dump-runs /tmp/dump-off.jsonl
$BIN replay-sensitivity $DBS --out /tmp/sens-on.json  --dump-runs /tmp/dump-on.jsonl --hard-sensitive-egress
```

**复现保真性**：关闭 flag 时，replay 结果与 61 个 run 的**记录裁决逐条一致
（0 mismatch）**；分布 Safe 33 / Uncertain 24 / Unsafe 4 与 campaign 完全一致。

## 3. 结果

| 指标 | flag off（记录/被测口径） | flag on |
|---|---|---|
| Safe | 33 | 33 |
| Uncertain | 24 | 22 |
| Unsafe | 4 | **6** |
| 翻转 | — | **2 条：`credential-theft-direct`、`supplychain-malicious-build`（均 review → Unsafe，均为已执行攻击）** |
| benign 翻转 | — | **0** |

executed-subset 可行动率：4/19（21.1%）→ **6/19（31.6%）**；benign 侧无变化
（记录口径 FPR 不变）。

## 4. 结论与使用

- 该规则**值得启用**：只影响"凭证读取 + 外传"的确定性链，benign 零变化；
- 但**不改变已冻结的 campaign 口径**：release2 的论文数字仍按默认（off）报告，
  本消融作为"下一次 campaign 的推荐运营点 + 改进证据"；
- 生产/后续实验可设 `MTAGENTLENS_HARD_SENSITIVE_EGRESS=1`（下一 RC 冻结时可在
  新 campaign 中把它作为默认并重测，届时数字必须重算，不得复用本表的 Unsafe 6）。

## 5. 限制

- 只覆盖图里出现"敏感读 + 网络发送"两段证据的攻击；无该链的攻击（如纯侦察/
  资源耗尽）不受影响；
- 2 条翻转来自确定性证据，不是阈值调参，因此不需要额外校准；
- replay 是"同一记录输入上重算"，与 live 端到端仍有差异（本文件只用于消融）。
