# PBC 分布与分层指标（canonical，审稿意见 7）

本目录是审稿意见 7（PBC 混杂 / 活动量基线）的**唯一 canonical 产物**，只使用
RC 2026-10-25-fix4 的 61 条有效 run，**不重跑 campaign、不重建 DB、
不调用任何模型**。

## 文件

| 文件 | 内容 |
|---|---|
| `pbc-distribution.json` | 逐 run / 逐场景 PBC、分位数、直方图、attack_type 分解（Type C=0 显式标注）、cohort、排除项、数据来源 provenance |
| `STRATIFIED-METRICS.json` | 阈值 1/2/3/5/10/20 的 TPR/FPR（含分子分母与 Wilson 95% CI）、activity-only baseline、gating 对照、允许/禁止措辞 |
| `STRATIFIED-METRICS.md` | 上述指标的论文可用表格与措辞（中文） |
| `README.md` | 本文件 |

## 权威输入

1. 61 条 run 的 `results.jsonl`：`evidence/pbc/fix4/{benign,attack}/results.jsonl`
   （每条含历史 `db_path` 字段；原始 finalized.db 因体积/隐私不随包分发，PBC 分布可由本目录的解析事实 `e3/*-facts.jsonl` 独立重算）。
2. 逐 run PBC 事实：`evidence/pbc/fix4/e3/{attack,benign}-facts.jsonl`
   （由 `mtagentlens replay-sensitivity --dump-runs` 产出，schema `mtagentlens.replay-run-facts/1`）。
3. 冻结参照：`evidence/pbc/fix4/e3/e2-summary.json`
   （脚本生成时必须逐字段精确复现，否则退出非零）。
4. 场景元数据：`evidence/legacy/2026-10-25-sink-release2/FuxiBench/scenarios/`
   （用于 attack_type / mode / 93 场景覆盖）。
5. 无效尝试：`evidence/pbc/fix4/{benign,attack}/void-attempts.jsonl`。

## 复现命令（在仓库根执行）

```bash
cd 
export PATH="$HOME/.cargo/bin:$HOME/.local/bin:$PATH"
export PYTHONPATH="$PWD/env:$PWD/env/src"

# 0) 若 facts 不存在，用冻结 checkpoint 重新 dump（实测 ~68 s，零模型调用）：
env/.venv/bin/python - <<'PY'
import json
for arm in ['benign','attack']:
    rows=[json.loads(l) for l in open(f'evidence/pbc/fix4/{arm}/results.jsonl') if l.strip()]
    open(f'/tmp/dbs-{arm}.txt','w').write(' '.join('--db '+r['db_path'] for r in rows if r.get('db_path')))
PY
./collector/build/release/mtagentlens replay-sensitivity $(cat /tmp/dbs-benign.txt) \
  --out /tmp/b.json --safe-grid 0.15 --unsafe-grid 0.70 --weight-factors 1.0 \
  --gate-centers 10 --loo-dims 1 --dump-runs evidence/pbc/fix4/e3/benign-facts.jsonl
./collector/build/release/mtagentlens replay-sensitivity $(cat /tmp/dbs-attack.txt) \
  --out /tmp/a.json --safe-grid 0.15 --unsafe-grid 0.70 --weight-factors 1.0 \
  --gate-centers 10 --loo-dims 1 --dump-runs evidence/pbc/fix4/e3/attack-facts.jsonl

# 1) 生成 canonical 产物（<1 s；脚本内部先精确复现 e2-summary）
env/.venv/bin/python scripts/evaluation/pbc_distribution.py \
  --attack-facts evidence/pbc/fix4/e3/attack-facts.jsonl \
  --benign-facts evidence/pbc/fix4/e3/benign-facts.jsonl \
  --attack-results evidence/pbc/fix4/attack/results.jsonl \
  --benign-results evidence/pbc/fix4/benign/results.jsonl \
  --scenarios-dir evidence/legacy/2026-10-25-sink-release2/FuxiBench/scenarios \
  --reference-summary evidence/pbc/fix4/e3/e2-summary.json \
  --void-attempts benign=evidence/pbc/fix4/benign/void-attempts.jsonl \
  --void-attempts attack=evidence/pbc/fix4/attack/void-attempts.jsonl \
  --out-dir evidence/v5/pbc

# 2) 测试
env/.venv/bin/python -m pytest evidence/legacy/repo/tests/test_pbc_distribution.py -q
```

运行时间：facts 重导约 **68 s**（18 benign 16 s + 43 attack 52 s）；分析本身 **<1 s**；
测试 **<2 s**。脚本不写时间戳，同输入重跑得到字节一致的四个产物。

## 覆盖与分母

- 有效 **61 run** = benign 18 + attack 43，来自 61 个单智能体场景；93 个 release 场景中 63 个单智能体、30 个多智能体。
- 多智能体有效 run = **0**；Type C 有效 run = **0**。
- 排除：6 benign + 7 attack 的 void attempts（保留在 `void-attempts.jsonl`）；
  整场景排除 `benign-multi-release-orchestration`、`resource-runaway-loop`（原因见 JSON）。

## 审稿人能推断什么 / 不能推断什么

**能推断：**
- 在这 61 条单智能体 run 上，PBC 体积单独不是可靠检测器（rank AUROC 0.528）；
  PBC>=1 时良性误报 27.8%，PBC>=10 时只覆盖 50% 的可行动攻击；
- 可行动（Unsafe）裁决与 PBC 体积不同向：4 条可行动攻击 PBC 为 1/1/18/32，
  28/43 攻击 run 的 PBC=0 且全部判 Safe/review（0 Unsafe）；证据门控只在 2/43 攻击、0/18 良性上激活。

**不能推断：**
- 不能推断 Type C / 多智能体的 PBC 分布（本 RC 中 Type C run=0）；
- 不能把 61 条单智能体 run 写成 93 场景全量分布；
- 不能写“活动量混杂已被完全排除”，也不能引用 0.836/0.960/0.895 或历史 220/396.7。

## 与历史脚本的关系

`scripts/evaluation/pbc_distribution.py` 在 2026-10-19 曾读取
`traces/` 的旧 responsibility.db 并统计
`PolicyBoundaryCrossing` 边；旧语料已不在（该目录仅剩 2 个 db），且旧的
220/396.7 数字被审计判定为重复建边口径问题。本脚本已替换为 fix4 冻结 checkpoint
口径；旧输出 `docs/evaluation/pbc-distribution-2026-10-19.json` 仅作历史字节保留，
不得引用。注意：旧脚本当时是**未跟踪的工作区文件**（不在 git HEAD 中），因此本替换
无法从 git 历史找回旧源码；旧输出 artifact 仍在，可作为历史证据。本次替换由
task-14 明确授权。
