# 复现手册：每个数字对应哪条命令（离线部分）

> 用途：审稿人/下一位 Lead 可以**只按本页**重算仓库里所有"可离线复现"的数字。
> 所有命令在仓库根目录执行；`local/` 是 gitignored 的本地产物目录。
> 需要网络的只有 campaign（模型额度阻塞），本页**不需要网络**。
> 无 artifact 的数字不要引用；清单见
> [`../paper/数字修正-2026-10-25.md`](../paper/数字修正-2026-10-25.md)。

前置：

```bash
cd <repository-root>
export PATH="$HOME/.cargo/bin:$HOME/.local/bin:$PATH"
export PYTHONPATH="$PWD/env:$PWD/env/src"
```

当前实验入口与前置条件以[最终实验清单](final-rc-checklist.md)为准。
当前数据包的fixture/任务来自哈希核验的标注源，IP保持原始语义；角色文件仅为设计审计背景，
executor不会将role_file作为额外system prompt注入。

## 0. 单一门禁

```bash
./scripts/verify.sh     # 期望：18/18，最后一行 "verify: all gates passed"
```

**当前实测（2026-10-26）**：18/18（含第 18 门 FuxiBench 包 portability/clean-room）；
compare **1266 passed + 16 subtests**、env **365 passed**、scripts **60 + 7 subtests**、
claims **54/54**。对外材料冻结前仍以当日最后一次重跑为准。

## 1. 论文数字层（claims）

```bash
env/.venv/bin/python scripts/evaluation/verify_claims.py \
  --manifest docs/paper/claims.json
# 期望：54/54 claims hold
```

历史指标重放命令（需要对应历史release仍在本机；不能改用当前包冒充旧结果）：

```bash
env/.venv/bin/python scripts/evaluation/final_verdict_metrics.py \
  --benign datasets/FuxiBench/evidence/pbc/fix4/benign/results.jsonl \
  --attack datasets/FuxiBench/evidence/pbc/fix4/attack/results.jsonl \
  --release local/workpackages/2026-10-24-final-rc/release \
  --out /tmp/metrics.json
# 仅在原始release和run binding齐全时执行；缺失时标为不可重算，禁止补造身份。
```

## 2. FuxiBench 2.0 数据集包（canonical）

```bash
# 2.1 整包哈希（在包根执行；期望全部 OK）
(cd datasets/FuxiBench && sha256sum -c checksums.sha256)

# 2.2 GT 结构/计数（期望 93 = 58 attack + 35 benign，problems=[]）
env/.venv/bin/python scripts/evaluation/validate_gt.py --dataset datasets/FuxiBench

# 2.3 包 portability/clean-room + 计数/轨迹（verify.sh 第 18 门）
env/.venv/bin/python scripts/evaluation/validate_fuxibench_package.py --clean-room
# 期望：valid=true；records 93 / attack 58 / benign 35 / excluded 9 /
#       included 58+26 / a 47 / b 11；problems=[]
```

GT：`datasets/FuxiBench/ground_truth/<category>/<scenario_id>.json`；当前
`taxonomy_version=fuxibench-2type/1`，`label`/`attack_type` 为两类型（A=旧 B，
B=旧 C；旧 Type A 已移除）。检测指标必须先过滤
`quality.detection_excluded=true`（9 个场景）。

## 3. E5 身份微基准（合成，零模型）

```bash
cd collector && cargo run -q --example identity_microbenchmark > /tmp/e5.jsonl && cd ..
env/.venv/bin/python scripts/evaluation/e5_identity_microbenchmark.py \
  --traces /tmp/e5.jsonl --out /tmp/e5-summary.json --details
# 期望：T1–T6 pairwise F1 全部 1.000、unattributed 0%
```

## 4. PBC（fix4 历史单智能体 run；非检测分母）

canonical 产物：`datasets/FuxiBench/evidence/pbc/`（`pbc-distribution.json`、
`STRATIFIED-METRICS.json`、README）。只读校验（零模型、不改包）：

```bash
python3 - <<'PY'
import json
p = json.load(open('datasets/FuxiBench/evidence/pbc/pbc-distribution.json'))
att = p['cohorts']['attack']; ben = p['cohorts']['benign']
runs = [r for r in p['per_run'] if r['arm'] == 'attack']
print('benign', ben['runs'], 'attack', att['runs'],
      'attack PBC=0', sum(1 for r in runs if r['pbc_edges'] == 0))
baseline = json.load(open('datasets/FuxiBench/evidence/pbc/STRATIFIED-METRICS.json'))
print('auroc', baseline['pbc_rank_auroc'])
PY
# 期望：benign 18 / attack 43 / attack PBC=0 28 / auroc 0.5277777777777778
```

重算（需 fix4 facts，在本地产物中）：`scripts/evaluation/e2_pbc_activity.py`
（脚本 + facts 路径见[EVIDENCE-INDEX](EVIDENCE-INDEX.md) 意见 7 行）。该分析
使用旧 fix4 类型记录、早于 2.0 两类型迁移，**不是 detection-included 分母**；
旧 Type C 有效 run=0，不作 Type C 声明。

## 5. E3 阈值 / E4 逐维消融

```bash
./collector/build/release/mtagentlens replay-sensitivity --db <db1> --db <db2> ... \
  --out /tmp/sens.json \
  --safe-grid 0.10,0.15,0.20,0.30 --unsafe-grid 0.50,0.70,0.90 \
  --weight-factors 0.8,1.0,1.2 --gate-centers 5,10,20 --loo-dims 1,2,3,4,5,6
env/.venv/bin/python scripts/evaluation/e3_threshold_sensitivity.py \
  --report attack=/tmp/sens.json --out /tmp/sens-summary.json
# 期望：verdict flips = 0（阈值/权重/门控/逐维 LOO 都不改变裁决）
```

## 6. Observer 调用与告警延迟（fix4 离线，canonical 落盘待 W5）

```bash
env/.venv/bin/python scripts/evaluation/observer_cost_report.py \
  --results benign=datasets/FuxiBench/evidence/pbc/fix4/benign/results.jsonl \
  --results attack=datasets/FuxiBench/evidence/pbc/fix4/attack/results.jsonl \
  --out /tmp/observer-cost.json
env/.venv/bin/python scripts/evaluation/alert_latency_report.py \
  --results benign=datasets/FuxiBench/evidence/pbc/fix4/benign/results.jsonl \
  --results attack=datasets/FuxiBench/evidence/pbc/fix4/attack/results.jsonl \
  --out /tmp/alert-latency.json
```

> 旧 fix2 报告只作历史；fix4 canonical 报告尚未落盘（见数字修正 M12）。

## 6b. OverheadBench：统一结果包

唯一入口为 [compare/results/overhead/README.md](../../results/overhead/README.md)，包含图、表、逐运行与逐事件 CSV、原始证据和历史归档。旧 `local/` 目录已迁移，勿按旧命令重建分散目录。冻结证据内部旧路径保持原样，迁移索引位于 `data/migration.csv`。

```bash
# 只从已有证据复核与导出，不运行实验或调用 API
env/.venv/bin/python compare/results/overhead/reproduce/audit_existing.py
env/.venv/bin/python compare/results/overhead/reproduce/make_compact.py
env/.venv/bin/python scripts/evaluation/verify_claims.py \
  --manifest docs/paper/claims-overhead-2026-10-28.json
# 数字登记预期：16/16 claims hold
```

## 7. 标注 release（历史 pilot）

```bash
env/.venv/bin/python scripts/evaluation/annotation_release_consumer.py \
  --root datasets/FuxiBench
# 期望：VERIFIED for external consumption（exit 0；历史 pilot，不是 2.0 包）
```

## 8. 单页 studio（独立标注不读草稿）

```bash
cp datasets/FuxiBench/evidence/legacy/pilot/records.sqlite /tmp/store.sqlite
python3 -m tools.annotator serve --store /tmp/store.sqlite --rater 刘周康 \
  --method human --mode studio --codebook-version pilot/1 --port 7433
# 另开终端：token 在启动日志里
curl -s -H "Authorization: Bearer <token>" http://127.0.0.1:7433/api/tasks
curl -s -H "Authorization: Bearer <token>" http://127.0.0.1:7433/api/tasks/task-0001 | grep -c proposal  # 期望 0
rm -f /tmp/store.sqlite
```

## 9. 冻结 RC 路径（不产生新 RC）

见 [`final-rc-checklist.md`](final-rc-checklist.md) §7：`freeze_rc.freeze()` 对 fix2 构件
复算出的 binary/dataset/helpers 与 `rc.json` 完全一致，二次写入被拒。

## 10. 不能离线复现的部分（必须等额度/真人）

- campaign（TPR/FPR/开销/执行链）→ `docs/evaluation/CURRENT-EVIDENCE.md` + `docs/archive/ai-2026-10-30/handoffs/HANDOFF-NEXT-SESSION.md`；
- E6 跨机根因、E7（已取消新场景）、E8 应用层基线、E11 同级方法复现（E9 完整开销已于 2026-10-28 完成，见 §6b）；
- Type C/多智能体检测与归因（需最终付费 campaign）；
- 真人独立标注、第四方裁决、分字段人类 κ、attestation（**本轮明确不做**）。
