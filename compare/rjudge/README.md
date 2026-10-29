> **Status 2026-10-29**：R-Judge 已在 93 条 FuxiBench 上跑完 A2 完整 trace（93 records / 186 calls，0 parse error）；结果入口 `compare/results/external/baselines/README.md`。为适配 deepseek-flash，增加 no-tool system turn 与 `thinking: disabled`（prompt 原文未改），详见 `baselines/plan.json`。

# `compare/rjudge/` — 应用层基线（R-Judge 协议）实现骨架

> 回应审稿意见 3 / R1.3：为「文本 + 工具调用日志」这一**应用层**基线提供可复现、可审计、可扩展的实现。
>
> **状态：adapter 与 contract 已实现；正式比较仍需明确运行级参考标签后逐个执行。** 本目录**没有**产生任何 TPR/FPR/准确率，也**不读取 ground truth**——它只负责"把同一次 run 的应用层证据变成 R-Judge 输入，并按协议问模型"。指标必须由评估层把 judge 输出与冻结数据集配对后计算。

---

## 1. 它回答什么问题

| 臂 | 输入 | 用途 | 本包入口 |
|---|---|---|---|
| **A1** | 各根调用的首轮用户任务文本 | 「信息量 = 最少」端 | `trace_to_record(t, include_agent_turns=False, include_environment=False)` |
| **A2** | 完整应用层轨迹（任务 + assistant 文本 + `tool_use` 名称/参数 + `tool_result`） | **主基线**：与 Observer 同模型、同轨迹层 | `trace_to_record(t)` |
| **A3** | A2 去掉 `tool_result` | 「工具输出」的边际信息 | `trace_to_record(t, include_environment=False)` |

三者来自**同一份** `app_trace.v1`，不重采、不重跑 agent，因此 A1→A2 的差干净地对应"信息量"。

**信息边界（硬约束）**：输入只含应用层证据。

- ✅ 用户任务、assistant 文本/thinking、`tool_use` 名称与参数、`tool_result` 内容
- ❌ 不含任何 OS 事件（进程/文件/网络/内核）
- ❌ 不含 GT 标签、`final_decision`、collector 告警/分数、`.scenario_id`
- ❌ 不读 `collector.responsibility.db`、`summary.json`、`datasets/FuxiBench/ground_truth/**`（旧仓库内数据集路径已退役）

这不是"约定"，是**结构保证**：导出器根本不打开这些文件（不 import、不 stat），写盘前还有 `_assert_boundary()` 对 `FORBIDDEN_TRACE_FIELDS` 做递归拦截，并有契约测试守着。

---

## 2. 流水线

```
env/runs/<run>/.claude_benchmark/projects/**/*.jsonl   (S1, 主源)
env/runs/<run>/collector/collector.log  (S2, 仅回退)
                    │
                    ▼  trace_export.py
        mtagentlens.app_trace.v1  （中性 JSON，每 run 一份）
                    │
                    ▼  record.py
        R-Judge record {profile, contents}  （无标签、无 GT）
                    │
                    ▼  judge.py（两步式）
        原始响应 + 严格解析 → 逐 run JSONL（带 executed 诚实标记）
                    │
                    ▼  【不在本包】评估层
        与冻结数据集配对 → TPR/FPR/Wilson/McNemar
```

模块职责：

| 文件 | 职责 |
|---|---|
| `protocol.py` | 协议 pin：prompt 原文、消息拼装、**严格解析**、record 形状校验、`protocol_pin()` |
| `trace_export.py` | S1/S2 → `app_trace.v1`；截断策略、多 agent、缺失标记、失败原因 |
| `record.py` | `app_trace.v1` → R-Judge record；A1/A2/A3；多 agent 归因 |
| `judge.py` | 两步式 judge、可注入 client、严格失败、JSONL、resume、`executed` 门 |
| `cache.py` | 内容寻址缓存（trace hash + prompt hash + model + 解码参数） |
| `REFERENCE.md` | **逐字 provenance pin**（repo/commit/prompt/论文/UNVERIFIED 清单） |

---

## 3. 最小用法

在仓库根目录、用项目 venv：

```python
from pathlib import Path
from compare.rjudge import (
    export_traces, load_trace, trace_to_record,
    JudgeConfig, JudgeRunner, RJudgeJudge, ResponseCache, ExecutionMode,
)

# 1) 导出（只读 run 产物；不碰 GT / DB）
run_names = ["20261021-003126-db36d097a3c7", "..."]   # 例：冻结 run 清单
manifest = export_traces(
    [Path("env/runs") / name for name in run_names],
    out_dir="compare/results/app_traces/FuxiBench",
)
print(manifest["n_complete"], "/", manifest["n_traces"], "traces complete")

# 2) 转 record（A2）
records = [
    trace_to_record(load_trace(Path("compare/results/app_traces/FuxiBench") / entry["trace_file"]))
    for entry in manifest["traces"]
    if entry["trace_complete"]
]

# 3) 判定（离线冒烟：注入 fake client，绝不联网）
class FakeClient:
    def __init__(self, reason, label): self.reason, self.label, self.calls = reason, label, 0
    def complete(self, *, model, messages, temperature, max_tokens, seed=None):
        self.calls += 1
        return self.reason if self.calls % 2 == 1 else self.label

judge = RJudgeJudge(
    config=JudgeConfig(model="deepseek-v4-flash"),
    client=FakeClient("looks fine", "safe"),
    cache=ResponseCache("compare/results/app_traces/.cache-smoke"),
    execution_mode=ExecutionMode.MOCK,          # ← 冒烟：结果 executed=false
)
summary = JudgeRunner(
    judge=judge,
    out_jsonl="compare/results/app_traces/smoke.jsonl",
    summary_path="compare/results/app_traces/smoke.summary.json",
).run(records[:3])
print(summary["executed"], summary["metrics"])   # False, None
```

真实运行（**需 Lead 决策**，见 §7）：

```python
from compare.rjudge import AnthropicMessagesClient

judge = RJudgeJudge(
    config=JudgeConfig(model="deepseek-v4-flash", temperature=0.0),
    client=AnthropicMessagesClient(),           # 复用 env/config/claude.env 的凭据
    cache=ResponseCache("compare/results/app_traces/.cache"),
    execution_mode=ExecutionMode.REAL,          # ← 只有这个值能进论文表
)
summary = JudgeRunner(
    judge=judge,
    out_jsonl="compare/results/app_traces/rjudge_A2.jsonl",
    summary_path="compare/results/app_traces/rjudge_A2.summary.json",
    resume=True,
).run(records)
```

`AnthropicMessagesClient` 走 `ANTHROPIC_BASE_URL` / `ANTHROPIC_AUTH_TOKEN` / `ANTHROPIC_MODEL`（先读显式参数，再读环境变量，最后读 `env/config/claude.env` 与 `env/config/vendors/deepseek/claude.env`），与 `compare/scripts/evaluate_prompt_tools.py` 同一约定。**无 token 时它 `available=False` 并直接拒绝调用**，不会静默打网络。

---

## 4. 契约测试（无网络、确定性）

```bash
cd /home/lzk/MtAgentLens
env/.venv/bin/python -m pytest -q compare/tests/test_rjudge_*.py
# 136 tests（protocol 54 / trace_export 27 / record 25 / judge 30）
```

覆盖：

| 文件 | 契约 |
|---|---|
| `test_rjudge_protocol.py` | prompt 逐字 golden（sha256）+ 两步消息序列 golden + 严格解析（合法/非法/多行/带解释/双标签）+ 官方口径审计函数 + record 形状（含禁止评测字段）+ provenance pin 完整性 |
| `test_rjudge_trace_export.py` | `app_trace.v1` 字段与事件键集、时间序、多 agent 全局排序、截断（head+tail/计数/原文 sha256）、缺失转录标记、S2 回退与**不可用原因**、无 GT/OS 泄漏、无 socket、symlink 逃逸拒绝 |
| `test_rjudge_record.py` | 轮次结构、连续 assistant 合并、`tool_result`→`environment`、A1/A2/A3、多 agent 前缀、profile 回退与披露、确定性哈希、不完整 trace 拒绝 |
| `test_rjudge_judge.py` | 两次调用顺序与 payload、gen 怪癖、严格失败（`label=None`）、缓存命中/失效维度/持久化/损坏当 miss、resume、**mock 不产出数字**、summary 无检测指标 |

回归：`env/.venv/bin/python -m pytest -q compare/tests` → **655 passed**（含本包 136）。

---

## 5. 诚实边界（请勿越线）

1. **没有结果数字。** 本包不读 GT，因此**无法**产出 TPR/FPR/准确率——这是设计，不是遗漏。
2. **`executed` 门。** 每条结果带 `executed`（真实模型产出）与 `execution_mode`；summary 的 `executed` 为 `False` 时**不报告任何比率**（`metrics=None`、`parse_error_rate=None`），并写明原因。mock/缓存混用 → `executed=False`。
3. **解析失败即失败。** 非法标签 → `status="parse_error"`、`label=None`；不做"翻转成错误标签"（上游为算指标会这么做，我们只在 `official_normalize_prediction()` 里保留审计口径）。
4. **上游分歧已披露**：论文说"默认温度"、代码写 `temperature=0`；我们采用代码值并记录（见 `REFERENCE.md` §2.4）。
5. **UNVERIFIED 不猜测**：论文 prompt 图（Appendix D.2 Figure 4）无法抽取文本，`REFERENCE.md` 标注为 UNVERIFIED；我们的逐字依据是**仓库脚本**。
6. **license**：上游无 LICENSE → 只引用协议，不 vendor 代码/数据/结果。论文措辞用「按 R-Judge 协议实现」。

---

## 6. 已知限制 / 后续

| 限制 | 现状 | 建议 |
|---|---|---|
| S1↔S2 合并 | **未实现**：S1 优先，S2 仅整份回退 | 需要补 `tool_result` 时再实现按 `(agent_id, tool_use_id)` 合并，并报 S1/S2 差异率 |
| S2 可用性 | 本机实测多数 `http_parser` body 被截断/为空 → 明确报 `tls_rebuild_unusable`，**不假装有证据** | 重采时确保完整 HTTP 报文；否则只用 S1 |
| 长轨迹分块聚合 | **未实现**：超预算靠 `TruncationPolicy` 截断并计数 | 若 >32k 的极少数 run 影响结论，加 turn-window 分块 + `any(unsafe)` 聚合 |
| 多 agent 时序 | 按毫秒 ISO 时间戳全局排序；同毫秒用 agent 序稳定 | 若出现并发同毫秒竞争，改用 `parentUuid` 拓扑序 |
| `profile` | 新 CLI 的 `api-request-shape` 可取真实 system prompt；旧 run 无此记录 → `profile_source="fallback"` | 论文披露近似串；重采后自动改善 |
| 无 CLI 入口 | 本包只有库 API（`compare/rjudge/` 写域内） | 需要命令行时在 `compare/scripts/` 写薄封装（见 §7） |

**本机只读统计（2026-10-24，`env/runs/2026*` 共 759 个 run；非实验结果）**：

- 可导出完整 trace：**540**；拒绝判定（无转录且 TLS 不可用）：**219**，每个都带显式 `missing` 原因；
- 多 agent run：**10**（2 agents ×2、3 ×5、4 ×1、5 ×1、7 ×1）；
- 截断计数：thinking ×734、text ×257、tool_result ×63、tool_input ×2；
- collector 有 `http_parser` 事件但 **body 不可解析**（被截断/为空）的 run：104 个——不论该 run 是否有转录（若不加区分，会被误当成"无证据"）。
- 结论：**540 个 run 有可判定输入**；覆盖缺口和 TLS 不可用都已被显式计数，不需要（也不允许）猜测。

> 该分布与 `local/baseline-survey/e8-integration-plan.md` 的 S1 统计同量级（那里记 538），差异来自解析口径（该原型把空 transcript 计为缺失、且未分 agent）。

---

## 6.1 为什么没有登记到 `compare/tool-lock.json`

- 本包是**协议实现**，不是可注册的静态工具：`registry.py` 要求 `adapter` 指向 `compare/tools/adapters/<name>.py`，而该目录不在本次写域内；
- `tool-lock.json` 的校验会拒绝含 `llm`/`runtime`/`openai` 字样的 metadata，强行使之通过需要改校验逻辑，收益很小；
- 与 `local/baseline-survey/e8-integration-plan.md` §3.1 的判断一致：应用层基线属于"prompt 基线家族"，应出现在主检测表，而不是静态工具表。

若 Lead 认为必须登记，追加条目时应**只追加**、不改已有条目，并同步新增 `compare/tools/adapters/rjudge_*.py`。

---

## 7. 给 Lead 的接入建议（最终 RC 之后）

**前置条件**（缺一不可，否则不要出数）：

1. `datasets/FuxiBench/release.json` 存在（FuxiBench 2.0 自包含冻结包）；当前缺失会让冻结评估链抛 `Unbound dataset`；
2. 重采已完成并把 `.claude_benchmark` 转录归档（旧 Type C 0 覆盖；2.0 的 B 类型/多智能体真实对照尚未跑）；
3. run 与 GT 的配对清单冻结（按 `trace_id → scenario_id`，**只存在于评估层**）。

**建议步骤**：

```bash
# 0) 只读导出（无网络；可反复跑）
cd /home/lzk/MtAgentLens
env/.venv/bin/python - <<'PY'
from pathlib import Path
from compare.rjudge import export_traces
runs = sorted(Path("env/runs").glob("2026*"))
print(export_traces(runs, "compare/results/app_traces/FuxiBench")["n_complete"])
PY

# 1) 离线冒烟：确认 wiring 与缓存，不产生任何数字
env/.venv/bin/python -m pytest -q compare/tests/test_rjudge_*.py

# 2) 真实判定（一次有成本的调用；A2 完整 trace；resume 安全）
#    建议写成 compare/scripts/evaluate_app_log_baseline.py（本包写域不含 CLI）
#    内部：load_trace -> trace_to_record(arm) -> RJudgeJudge(REAL) -> JudgeRunner

# 3) 指标（复用现有评估层，不要在本包实现）
#    compare/scripts/... 读取 rjudge_A2.jsonl 的 label + 冻结 GT -> Wilson/McNemar
```

**成本估算**（量级，非报价）：

- 单 run：输入 ≈ 轨迹 token × 2（两步各一次）+ ~1k；输出 ≤ 500 + 500（或 1）；
- 540 个可用 run、p50 ≈ 2.4k 轨迹 token → 输入量级 **3–4M tokens**，输出 < 0.6M；
- 按 DeepSeek flash 档位，A2 全量约 $1–5；
- **重复运行 ≈ $0**：缓存按 `trace_sha256 + prompt_sha256 + model + 解码参数` 命中（只要模型/prompt/输入不变）。

**注意**：把 `execution_mode` 设为 `REAL` 且全部结果 `executed=true` 之前，任何表格都不得出现本基线数字；`JudgeRunner` 的 summary 会在 `executed=false` 时把指标置 `None`，请保留该字段作为审计证据。


## 逐个实验前的输入检查

应用层 runner 现在保留导出轨迹中的实际 agent profile；缺失时由适配器显式记录 fallback，不能用 `fuxibench` 这个名字替代角色说明。A1 仅保留 agent 元数据标识的根调用的首轮 user 任务，排除后续 user 轮次和子任务；A2/A3 的完整轨迹语义保持不变。导出的具体 judge 输入和哈希保存在 `records/`。

付费命令要求 `--client anthropic --execution-mode real --release <release>`，且本次选中恰好一条有效运行；可以对预先确定的顺序使用 `--limit 1`，或者提供单条输入文件。运行前核验 release/run 绑定、冻结数据库哈希、完成状态、健康采集及 canonical verdict，检查失败则不调用模型。可用 `--arm A1_prompt_only|A2_full_trace|A3_no_tool_result` 指定信息臂。每个运行/臂用独立输出目录，检查结果后才执行下一个。

runner 的 R-Judge 两步提示、二元输出与产品 Observer 的候选版本、提示和三态聚合并不相同；不能把该 runner 自动称为“只改变输入的产品消融”。本对比只保留 A2 完整 trace。场景 attack/benign 标签的一致率也不等于有独立动作真值的检测准确率。


两步协议还要求首步输出非空的审阅说明。首步为空时输出 error 并跳过标签请求；旧的 status=ok 但首步为空的记录不满足续跑条件。真实客户端保留每步 stop_reason 与允许的 token usage 字段，避免只看到 HTTP 200 就误判协议完成。2026-10-25 单条 A1 冒烟即发现过空首步、后续标签为 safe 的情况；该记录保留但未接纳为比较结果。随后一次仅首步的诊断返回 end_turn 与非空说明，说明不能凭原空文本直接归因于 token 预算耗尽。
