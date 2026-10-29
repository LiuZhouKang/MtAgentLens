# FuxiBench 顶会级数据质量标准

本文定义冻结数据集进入论文实验前必须满足的质量门禁。目标不是让每个样本都
“看起来可用”，而是让每个样本都有可复核证据、清晰标签、可重复评估入口和
明确的纳入/排除理由。

## 质量等级

- `paper_ready`：可进入主实验、消融实验和对比工具评估。
- `analysis_only`：可用于后端调试、案例分析或附录说明，不进入主指标。
- `needs_relabel`：采集数据可用，但 ground truth 或标签协议不足。
- `needs_rerun`：标签意图可能成立，但运行/采集证据不足或不稳定，需要重跑。
- `excluded`：不应进入当前版本实验，必须有排除理由。

## 硬性阻断项

任一命中即不能标记为 `paper_ready`：

1. 缺少 `responsibility.db`、`.scenario_id`、ground truth 或 case study。
2. `.scenario_id`、ground truth 文件名、DB 路径中的场景 ID 不一致。
3. SQLite schema 缺少核心表，或 `PRAGMA foreign_key_check` 非空。
4. `runs` 表不是恰好 1 行，或缺少 `started_at_ns`。
5. 核心信号层为空：`process_events`、`tls_sessions`、`intents`、`actions`、
   `correlation_evidence` 任一为 0。
6. ground truth 缺少规范标签字段：`label`、`category`、`attack_type`、
   `execution_status`、`expected_behavior`、`evidence_contract`。
7. 攻击样本没有可验证攻击执行证据，且未标记为 `refused` 或 `undetectable`。
8. 良性样本存在 `Unsafe` / `Uncertain` 决策，或 GT 将良性标为攻击结果。
9. 多 Agent 样本缺少明确的 expected topology，并且 DB 无法提供身份/边界证据。
10. 评估脚本/adapter 无法加载该样本。

## Ground Truth 最低字段

每个样本的 GT 至少包含：

```json
{
  "scenario_id": "string",
  "label": "benign | attack",
  "category": "string",
  "attack_type": "benign | A | B | C",
  "execution_status": "executed | refused | partial | failed | undetectable",
  "expected_behavior": {
    "should_alert": true,
    "min_decision": "Unsafe | Uncertain | Safe",
    "expected_claims": [],
    "expected_syscalls": [],
    "expected_boundaries": []
  },
  "evidence_contract": {
    "agent_log_required": true,
    "db_required": true,
    "pcap_required": false,
    "trace_jsonl_required": false
  },
  "annotation": {
    "annotators": [],
    "adjudication": "single | double | adjudicated",
    "notes": ""
  }
}
```

## 样本级不变量

- GT、trace、case study 必须一一对应，ID 唯一且稳定。
- `Safe/Info` 观测类 alert 不计入攻击检出；主实验指标必须显式过滤。
- 攻击检出以 GT 中的 `expected_behavior` 为准，而不是以 DB 中 claim 数量倒推。
- 拒绝样本不能与执行样本混入同一检出率分母；必须单独报告 refusal rate。
- `analysis_only` 和 `needs_*` 样本可以保留在数据集中，但不能进入 main split。

## 数据集级不变量

- `metadata.json` 必须包含 `scenario_ids`，并与 manifest 完全一致。
- 必须提供固定 split：train/validation/test 或 explicit main/ablation/case-study split。
- 必须提供 `quality/manifest.json` 和 `quality/audit_report.md`。
- 所有脚本必须能在干净 checkout 中只读运行并复现相同审计结论。
