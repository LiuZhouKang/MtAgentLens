# Agent annotation pilot（2026-10-24，codebook `pilot/1`）

> 状态：**agent 标注试点，不是人工标注**。只有 2 个良性受控运行的冻结证据包；
> 没有攻击场景、没有真人 κ、没有最终 RC。`paper_metrics_available=false`。

## 流程

1. 从冻结证据包（runner 账本、task brief、assistant trajectory、collector 动作证据）
   生成盲化证据包，约 6–8 KB/任务的可读摘要 + 完整事件集。
2. 三个独立 agent（`lzk-agent`、`thy-agent`、`ljy-agent`）分别读取证据包，按 codebook
   标注 attempted/executed/succeeded/roots；identities 统一保留 unknown，不虚构 actor。
3. 第一轮 agent 把"完成良性任务"误读为 `attempted=yes`；加入
   `CODEBOOK-CLARIFICATION.md` 明确"attempted 指恶意目标动作"后，第二轮三个 agent
   全部改为 `no/no/no`，roots 空且引用 task brief 与轨迹。第一轮记录保留在源 store
   的审计链中。
4. 三条第二轮修订以 `method=model_proposal` 写入 store；导出
   `mtagentlens.annotation-export/v2`，用 `scripts/evaluation/agent_agreement.py`
   计算 agent-only agreement。

## codebook 升级与迁移（2026-10-24）

`pilot/1-draft` 已定稿为 `pilot/1`（[PILOT-CODEBOOK.md](../../../tools/annotator/PILOT-CODEBOOK.md)），
固定了 `pilot/1-draft` 遗留的 5 项未决事项：授权包络判定程序、按目标类别的
`succeeded` 判据、空根因集合与 `unknown` 的分工、裁决协议与分字段 κ 门槛、可见范围与
盲化边界。同时引入**单调链**一致性规则 `attempted ≥ executed ≥ succeeded`
（`yes > unknown > no`），由 `records.py` 强制。

codebook 版本属于 run binding，因此证据包 hash 随版本改变。迁移只重绑元数据：

```bash
python3 -m tools.annotator migrate-codebook \
  --store local/workpackages/2026-10-24-pilot-human/records.sqlite \
  --out-store local/workpackages/2026-10-24-pilot-codebook-v1/records.sqlite \
  --bundles-out local/workpackages/2026-10-24-pilot-codebook-v1/bundles \
  --release local/workpackages/2026-10-24-controlled-run/release \
  --codebook-version pilot/1 \
  --manifest-out local/workpackages/2026-10-24-pilot-codebook-v1/migration.json
```

- 重新冻结时完整重验 release、来源 SHA-256 与事件字节范围；迁移后断言来源 hash、
  事件、actor、标注 payload、annotator 与 method 均未改变；
- 修订按原顺序重放，父链与裁决引用改写为新 revision id，映射写入 `migration.json`；
- 默认拒绝迁移真人修订与复核声明（标签属于旧 codebook 下的阅读），本次迁移
  `human_revisions_carried=0`、`attestations_migrated=0`。

## 结果

- 任务：2 个良性文档审阅运行；每任务 4,860 / 5,623 个证据事件。
- 三个 agent 第二轮在 attempted/executed/succeeded/roots 上 **100% 一致**；
  `identities` 全部 unknown（指标平凡地一致）。
- Fleiss κ：attempted/executed/succeeded/roots 为 **undefined / not_evaluable**
  （所有评分同一类别，无方差），`identities` 为 1.0。**这不是通过，也不是人类 κ。**
- 复核者：刘周康、唐浩钺、刘锦样，状态为 **assigned_pending_review**；
  没有记录任何复核声明，不能写成"三人已复核"。

## 交付物

权威包：`local/workpackages/2026-10-24-pilot-codebook-v1/`

- `records.sqlite`：2 任务、6 条 `model_proposal`、0 真人修订、0 声明；
- `bundles/`：冻结证据包（`mtagentlens.evidence-bundle/v1`，已重绑 `pilot/1`，字节未变）；
- `migration.json`：codebook 迁移清单（旧/新 task 与 revision id 映射）；
- `readiness.json`：证据充分性审计（2/2 就绪，`ready_for_independent_annotation=true`）；
- `annotations.json`：v2 导出；`agent-agreement.json`：agent-only agreement；
- `reviewers.json`：复核责任 sidecar（`assigned_pending_review`，无声明）；
- `REPORT.md`：逐任务三 agent 对照与限制；
- `release/`：可复现 annotation dataset release（见
  [annotation-dataset-release.md](annotation-dataset-release.md)）。

历史包（保留、不删）：`local/workpackages/2026-10-24-pilot-human/`（迁移前 store）、
`local/workpackages/2026-10-24-agent-annotations/`（`pilot/1-draft` 时代的导出与包）。

## 边界

- 这是 **agent 预标注**，不能进入论文人工 κ；正式论文必须使用真人独立标注，
  或如实报告 agent 预标注 + 真人复核。
- 只有 2 个良性任务，没有攻击样本，不能用于 TPR/FPR。
- identities 全部 unknown，归因任务未完成。
- codebook 已定稿为 `pilot/1`，但**没有真人标注与复核声明**；κ 不可评估。
