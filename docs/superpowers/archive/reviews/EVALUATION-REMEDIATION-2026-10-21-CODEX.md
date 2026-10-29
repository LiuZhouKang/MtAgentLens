# GT 与评估链路修复验收（2026-10-21）

## 已落实的契约

- GT 原始记录、固定名称与真实来源保留。`data-exfil-direct` 的缺失原文已从保留的原始记录精确恢复，来源 SHA256/消息 UUID/原文摘要哈希均保留；没有生成新的语义标注。
- v4 list 型 `expected_behaviors` 的 243 条行为全部进入 ExpectedClaims，并保留正则与 NetworkBoundary。
- 新输入发布冻结 scenario、fixture、prompt、GT、reviewer 原文和 bench setup/sink。拒绝混版、路径逃逸、symlink 和内容变更。新发布使用下游盲标工具可读取的统一 prompt manifest。
- Pilot 执行冻结 collector 副本（其嵌入的 helper 一同受 binary SHA 绑定），复制 benchmark runner、模型与私有运行配置；记录独立 run 身份。SQLite 使用 backup 包含已提交 WAL，再保存自包含最终快照。
- `final_verdict_metrics --release` 对实际 DB、collector、运行模型/配置及发布输入验 hash，并将 JSON 中的 verdict 与 DB canonical verdict 对比；重复 run、缺 DB、改写 verdict 均拒绝。
- conformant / execution-qualified 子集按唯一 run 关联；执行分母须独立于检测器。marker 必须与 run nonce 的实际日志匹配，不能等同于已完成外传。
- `Uncertain`、未知最终裁决和采集质量不足分别统计。采集质量读取真实 checkpoint 和 `decision_provenance_json`，缺少已确认的零损失、完整排空、正常退出和持久化诊断时，不能成为 TN。二分类分母只纳入 healthy Safe/Unsafe，并报告覆盖率；有缺口时正式论文 gate 阻塞。
- 二分类与归因分开。归因通过 `runs.final_alert_id` → 同 run 的最终 alert → `engine.claim_ids` → 同 run 的精确 claims。每条 claim 保留真实类型和主体；原始参数只通过对应 evidence IDs 联 actions/intents。旧库缺少这些 ID 时仅保留 `run_verdict`，归因标记不可用，不填默认 PolicyViolation/Resource。二分类 verdict 不能拿来匹配 GT claim coverage。
- 盲标 API 只发送允许字段，隐藏系统答案与他人记录；历史提交保留。输入盲标包拒绝未绑定的当前数据、内容 hash 不符与覆盖已有标注。
- Legacy `normalize_gt_agreement.py` 现只输出严格统计，不再用描述词重合度声称语义一致，也不改写 GT 或 reviewer 原文。
- 正式 baseline 已切至真实 SDK/model/pipeline。旧项目正则不能冒充官方工具；LLM Guard tuple API、完整 token 窗口、Meta 模型摘要与 Vigil checkout/config 均纳入明确契约。缺引擎即 FAILED。官方出处和配置见 `datasets/FuxiBench/BASELINE-ENGINES.md`。
- 报告、图表与投稿 gate 不再无条件写旧规模、满召回或固定 ablation 数字；缺材料明确 blocked。诊断报告不能以 completed paper report 进入下游。

## 实测证据

```bash
env/.venv/bin/python -m pytest -q compare/tests tools/annotator/tests --tb=short
```

最终 **297 passed，8.76s**。包含审查补充的 Uncertain/TN、真实 capture 质量和精确 final-alert/claim 归因回归。未执行模型、收费 API 或攻击实验。

```bash
python3 scripts/evaluation/validate_gt.py --dataset datasets/FuxiBench
python3 scripts/evaluation/gt_agreement.py --reviewers datasets/FuxiBench/ground_truth/_reviewers
```

当前结构校验 **93/93**，攻击 **67**、良性 **26**、`needs_human_review=74`，`problems=[]`。Label Fleiss κ **0.9823417721518986**；attack-type κ **0.8753503107103693**；pattern 类别集合 Jaccard 中位数 **0.6666666666666666**。三个机器派生报告已按当前原始记录刷新。

实际使用 `build_release` 生成临时发布，随后 `validate_release`、`FrozenDatasetAdapter` 及逐例真实 `load_scenario`：**93 cases、67 attacks、243 expected claims、93/93 schema valid**，**74 publication blockers，paper_metrics_available=false**。

## 仍需真实工作完成的边界

74 项人工复核、独立盲标/签署、场景语义与执行效果、实际模型配置及正式比较实验尚未完成，不能用程序替代或声明已经完成。结构校验、κ 和单元测试都不能证明这些结果。

纯历史投稿包的文档/旧数字快照验收保留在 `compare/historical_tests/`，显式命令 `env/.venv/bin/python -m pytest -q compare/historical_tests`。旧真实投稿包缺失时该命令应失败；未使用 skip，也未为通过它生成旧实验数字。当前离线门禁使用独立合成输入验证生成器及缺材料阻塞行为。

私有 runtime 快照可能含 observer/agent 凭据，只保存于限制权限的本地运行目录；公开发布不能包含该目录。原始 trace 公开仍需真实权限与脱敏验收。
