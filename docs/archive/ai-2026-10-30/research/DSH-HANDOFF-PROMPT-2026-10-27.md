# DSH 接手执行 prompt（2026-10-27）

> 直接复制下面整段给新的 DSH 会话。它的任务是**数据修复 + 代码 P1 + 前置验收**；不是跑 C0，不是付费实验，不是 commit。
> 权威上下文：`docs/research/FINAL-CODEX-HANDOFF-2026-10-26.md`、`local/audit-2026-10-26/` 审计、`docs/evaluation/EXPERIMENT-SUITE-7-REVISED-2026-10-26.md`。

---

你是 MtAgentLens 的顶级实现工程师 + 数据治理工程师。先读，再按小包执行；不要凭旧交接/旧文档行动，不要一口气重构。

## 0. 必读
1. `AGENTS.md`、`CLAUDE.md`
2. `docs/research/FINAL-CODEX-HANDOFF-2026-10-26.md`（重点 §11–§12）
3. `local/audit-2026-10-26/{core-detection,identity-attribution,framework-persistence-replay,scoring-judgment-graph,experiments-data,design-review-methodology,design-review-paper,design-review-feasibility}.md`
4. `docs/evaluation/EXPERIMENT-SUITE-7-REVISED-2026-10-26.md`、`EXPERIMENT-IDENTITY-ATTRIBUTION-2026-10-26.md`、`DATASET-93-REPAIR-PLAN-2026-10-26.md`
5. `local/readiness-2026-10-26/team/scenario-repair/inventory.json`、`staging/`、`checks/results.json`

## 1. 当前事实（不要改口径）
- 包 `datasets/FuxiBench` release `ec1ba74d…`；93 输入 =58 attack（A47/B11）+35 benign；9 `detection_excluded`；当前 84 检测计分=58+26；multi 30=22 attack+8 benign，current scored 29（handoff excluded）。
- 目标仍是 G0 后 93 全 scored，**但 attack/benign 数由盲重标决定，不预设 58/35 或 65/28**。
- 数据盘点：0 hash drift；38 valid /30 review_only /16 source_change /9 excluded；核心待修约 25 例；3 组 staged patch 未应用；72 role_file 缺失；identity-confusion fail-open；handoff-unicode ASCII gate。
- 代码：Codex 已完成 tmux/IdentityView/AttributionResult/score/AppBench/ONNX 移除；`verify.sh` 在 `PATH=/home/lzk/.cargo/bin:$PATH` 下 18/18；但数据未修、C0 未跑、Overhead/Ablation/trace 未完成。
- 不做：安全产品化、E11、ONNX、付费模型/agent、sudo、commit、破坏性删除；不改论文结论。

## 2. 执行原则
- 先写一个 `local/readiness-2026-10-26/team/dsh-plan.md`（任务/文件/测试/回滚/停止条件），再动手。
- 小包 TDD：失败测试→最小实现→窄测试→回归；每包独立可验收。
- 改数据前先记录文件 sha256；不整包复制 1.7GB，不删除 canonical/traces；staged 修改可反向 patch。
- 不新增 `#[allow]`/`#[ignore]`/预算；不静默改标签/分母；不把 notes 当现状。
- 若需要 release 重打包，先停下报告，不要用 `--force` 覆盖 1.7GB 包；给出命令和备份方案。
- 不要跑 C0、不要启动付费模型、不要 sudo；需要 root/tmux 真实 multi 的验证只生成 plan，不执行。

## 3. 包 A：数据修复（优先）
### A1 应用 3 组 staged patch
- `staging/` 下 10 文件/3 组（resource-disk-fill、c2-dns-dead-drop、network-anomaly）。
- 先 `patch --dry-run -p0`；应用后同时改 `datasets/FuxiBench/...` 和 `env/scenarios/...` 镜像。
- 运行 `local/readiness-2026-10-26/team/scenario-repair/checks/test_repairs.py`，6/6 通过；记录 red/green。
- 若 patch 应用改变包字节：更新 manifest/release_id 之前先停；不要直接让旧 release 失效。

### A2 修两个高风险场景
- `identity-confusion`：`datasets/FuxiBench/fixtures/ops/ops-sync-dd26e6/tests/__init__.py:49-60` 删除缺 plan 静默回退 analyst，改 fail-closed；统一 actor 命名空间（`role-analyst-01` vs `analyst`）；重生成 GT notes/paper_ready；加纯函数/隔离测试，不执行危险载荷。
- `handoff-unicode`：`fixtures/ops/ops-sync-e29a5e/src/unicode_guard/normalize.py` 的 ASCII 复制可通过问题；按“困难 benign”或“真实 attack”二选一（先给证据，不预设），修 oracle/description，重标。

### A3 9 excluded + 30 review + 16 source
- 对 9 excluded 做**盲重标**（3 fresh AI+裁决；不看 detector/目标 58/35/22/8）；输出逐例处置表；仍 attack 则回滚，不翻 flag。
- 30 review_only：核对 `paper_ready`/notes 与当前字节，只改 flag 或说明；不改标签来凑 93。
- 16 source_change：优先 `recon-user-enumeration` v5 label/type、14 个 description 旧 Type A/C；修 description/oracle，不改已证标签。
- 72 role_file：运行仅日志键；补真实 role file 或去引用；不能伪造内容。
- 用 `scripts/evaluation/gt_rebuild.py` v3 做 full prepare；**先移除/gate `legacy_audit_rows()` 的当前 GT 读取**；输出 case-map/manifest/盲源；不调用模型。
- 目标：93 全 scored；release id 重算；`validate_fuxibench_package --clean-room`；不做 campaign。

## 4. 包 B：代码 P1
1. `collector/src/framework/analyzers/security_signoff.rs:259-267`：删除 `scenario_id` 门；改通用 sign-off 语义；补测试；若无法通用化，移除 analyzer 注册并更新测试/文档。
2. `scripts/evaluation/run_artifacts.py:48-65,232-252`：`read_canonical_verdict`/`run_observation_state` 必须校验 checkpoint `implementation`；stale 拒绝；补合成 DB 正/负测试。
3. `scripts/evaluation/gt_rebuild.py:1524-1535,1717`：`legacy_audit_rows()` 在 zero-GT prepare 中移除或显式 gate；补测试证明 blind prepare 不读当前 GT。
4. `datasets/FuxiBench/metadata.json` + `traces/TRACES-NOTICE.md`：旧 v1 降级 review-only：`traces_replayable=false`、`traces_collected=87`、删除 replay 声称；更新 validator 断言；不改旧 DB。
5. `collector/src/responsibility/claims/attribution.rs:615`：`ranked_agents` 只在单 actor 时给值；要么实现多候选确定性排序（证据/时序，不称概率），要么在输出/文档明确只报 @1+RootSet，禁止 @3/MRR；补测试。
6. `scripts/verify.sh`：cargo 不在 PATH 时给出明确错误或自动定位 `$HOME/.cargo/bin`；补 shell 测试/文档。
7. 清理文档：ONNX/`--disable-classifier`/`--lite` 已部分清理；全仓只保留历史文档标注，不把旧 1.3GiB 当当前开销。

## 5. 包 C：前置验收
- `PATH=/home/lzk/.cargo/bin:$PATH ./scripts/verify.sh` 必须 18/18。
- 目标 pytest：`test_run_artifacts.py`、`test_gt_rebuild_sources.py`、`test_gt_merge.py`、`test_app_baseline.py`、`test_run_binding.py`；新增测试必须红→绿。
- 包：`validate_fuxibench_package.py --clean-room`；`gt_rebuild.py verify`；不跑真实 campaign。
- 30 multi：只做 2-pane fake tmux smoke / 文档化真实 multi 待执行；不 sudo、不付费。
- 若 P1 修复需要 schema/API 变更：先写 ADR，不跨包顺手重构。

## 6. 输出
- 改了什么、为什么、文件:line；staged patch 状态；数据逐例表；测试 red/green 日志路径；剩余 blocker；是否触发 release 重打包。
- ≤10 条关键结论；不写“已完成/效果理想”除非有测试/artifact；不写实验结果。
- 最后列出**下一步给 Codex 的最小任务**（只列未完成，不重复已完成）。

## 7. 停止条件
- 数据源证据冲突、需要改标签但无法盲标、需要付费/ sudo、会破坏 1.7GB 包、测试出现无法解释的失败：停止并报告，不猜。
