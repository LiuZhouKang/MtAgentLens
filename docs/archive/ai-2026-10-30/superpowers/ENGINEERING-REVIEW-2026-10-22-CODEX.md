# 工程收口审查与执行计划（2026-10-22）

本文是当前交接的补充审查，不表示全仓审查或工程收口已经完成。§1–§6 保留首次审查与计划；最新实施进度见 §7，维护导航见 `docs/HANDOFF.md`。
阶段目标：先让工程契约、采集质量、持久化、展示和发布证据可靠，再规划标注工具与数据集扩展。

## 1. 首次审查范围与证据

- HEAD：`610f72680322907e6d8aa562f3a277fdb06c22b3`。
- 接收时 `git status --porcelain=v1` 为 964 条记录：512 ` M`、160 `D `、22 ` D`、270 `??`。这是 Git 状态条目数，未跟踪目录可能折叠，不是文件总数。
- 阅读根规范、当前交接、导出会话的任务索引与关键修复/结尾记录；定向核查采集解析、审计脚本、持久化、前端数据库导入、角色启动、构建验证入口。未逐行审完 5,137 行会话或全部代码。
- 上轮报告的 17/17 与浏览器结果，在现存日志中有记录；不是本轮重新执行的全量验证。
- 首次审查仅新增审查文档和入口链接；后续实施见 §7。未提交，未加载 eBPF，未运行正式实验。

本轮独立运行：

| 检查 | 结果 |
|---|---|
| `git diff --check` | exit 0（文档修改前） |
| `python3 scripts/check_ts_contract.py` | exit 0 |
| `python3 scripts/evaluation/check_scenario_integrity.py --strict` | exit 0，93 场景，0 problems |
| `python3 scripts/audit_deps.py --check-budget` | exit 0；只证明预算结构，不是联网漏洞审计 |
| `cd collector && cargo test --test producer_wiring_contract --test multi_pid_trace_config --test schema_upgrade --quiet` | 12 passed |
| `env/.venv/bin/python -m pytest -q compare/tests/test_prompt_manifest_guard.py compare/tests/test_prompt_input_provenance.py env/tests/test_capture_metrics_contract.py env/tests/test_run_context_args.py` | 23 passed |
| `cd frontend && npm test -- --run __tests__/db-import.test.ts` | 33 passed |

## 2. 确认问题：按风险排序

### E01 / P1：最终统计缺字段仍被判为健康（独立复现）

位置：`env/src/mtagentlens_benchmark/engine/collector_manager.py:473–484`。
`dropped_events` 缺失默认 0，而存在 final 事件就进入 `final_sources`；字段有效性不参与完整性判断。

本轮向真实生产函数传入：

```text
configured_sources = {"tls"}
stderr = {"event":"TLS_RINGBUF_STATS"}
trace = {"source":"ssl"}
```

返回 `ring_buffer_drops=0`、`capture_stats_complete=true`、`capture_loss_free=true`、`overall_healthy=true`。
复现通过 AST 提取并执行生产函数，未复制函数逻辑；仍不等同于启动完整 collector 的端到端测试。

建议：按来源/统计版本验证必需字段；缺失、非整数、布尔、负值、损坏记录不能成为有效零值。旧版本缺少分因字段可保留未知，不应凭空补齐。先修现有函数，再统一调用入口，避免扩建第二套质量框架。

### E02 / P1：审计显示 BLOCKED，进程却成功退出（独立复现）

位置：`env/scripts/audit_trace.sh:137–145`。
脚本最后只有展示分支与 `echo`，没有将审计失败转换为非零退出码。
本轮对临时空 run 目录运行脚本：显示 `1 passed, 6 failed` 和 `BLOCKED`，返回码仍为 **0**。
任何只检查返回码的调用方都可能接受失败审计；本轮未确认存在这样的生产调用方。

建议：区分通过、审计未通过、输入/执行错误，返回稳定的非零失败码；缺 DB/表/日志不得伪装正常零计数。

### E03 / P1：审计入口与 benchmark 对采集完整性的定义分叉（源码确认）

位置：`env/scripts/audit_trace.sh:65–99`，与 E01 所在解析器对照。
shell 只要求 `final_stats >= 1`，不要求每个启用源均有最终统计；字段解析失败默认 0；匹配 JSON 还依赖紧凑空白格式。
此外，场景模式读取仍硬编码本机路径及旧的 `FuxiBench/scenarios` 目录。

建议：复用修复后的生产解析器，以实际 run 配置确定预期源；shell 仅负责调用与展示。
“审计有效”与“检测到攻击”必须分开：当前无条件要求 alerts > 0，不适合通用良性样本准入，应明确保留为特定场景断言或由场景契约驱动。

### E04 / P1：前端 SQL 执行错误被吞成空数据（源码确认）

位置：`frontend/lib/db-import.ts:318–366`，并需逐项检查同文件其他提取器。
claim/traceback 查询把整个 SQL 操作包在 `catch { return [] }` 中。表存在但缺必需列，也会展示为 0 条。
这不是 API 请求错误态组件能修复的问题，因为导入层已经把错误改写成成功值。

建议：先检查可选表是否存在；已存在的表发生 schema 不兼容或执行错误时，抛出含表名的 `DatabaseImportError`。对允许的历史兼容路径明确列出版本，不能用宽泛 catch 代替兼容协议。

### E05 / P2：角色字段未参与角色执行（源码确认）

位置：`env/src/mtagentlens_benchmark/engine/tmux_engine.py:38–69`、`executor.py:312–315,617`。
`role_file` 传入启动函数但未被使用；它还作为 `task_logs` 的字典键。重复角色理论上会覆盖日志索引；本轮枚举活动场景未发现重复角色，不能声称当前数据已经丢失。

建议：当前阶段保持任务 prompt 行为，明确角色标签与角色内容的区别；为 agent 提供稳定标识，日志按 agent 标识索引。若以后要注入角色文件，应作为单独的场景行为版本变更，重新绑定输入哈希，不能混入机械清理。

### E06 / P2：候选快照检查不能证明已提交版本可复现（源码确认）

位置：`scripts/check_fresh_clone.sh:22–40`。
脚本有意把未跟踪文件加入候选快照，并复制本机子模块整个目录（只排除 `.git`）。因此它证明的是候选工作树在本机副本上的可构建性；未提交依赖和子模块现存构建产物可能参与检查。

建议：保留候选检查用途，另定义严格发布检查：明确 commit/tree、子模块 gitlink、锁文件和从源构建条件，不能混称同一项。严格检查使用隔离临时目录，不清理用户工作树。

### E07 / P2：当前文档仍有可观测漂移（源码确认）

- `scripts/verify.sh:322–329` 和 `scripts/audit-budget.json` 仍声称联网审计由 CI audit stage 执行，与 ADR-0014 的“不设 CI、发布前独立审计”决策冲突。
- `docs/README.md` 仍把历史 FPR/overhead 作为实验导航，部分文字直接呈现旧结果；需在入口明确历史状态，避免和“旧数字作废”的现行规则混读。
- 上轮交接的历史问题表含已修复状态，不能直接据此重新开单。`pipeline_contract.rs` 已删除、multi-PID 测试已调用生产函数，不应重复实施。

建议：修正现行说明，不批量改写历史证据；历史 ADR 的 Outcome 可以保留当时事实，入口要明确当前适用状态。

## 3. 保留的架构与边界

```text
eBPF helpers → Runner → Analyzer → ResponsibilityRuntime
                                  ├─ live 输出
                                  ├─ SQLite 单写 → run-scoped API → 前端
                                  └─ checkpoint → 共用裁决核 → replay

run 配置 + helper 最终统计 → 统一质量解析 → benchmark / audit / 发布准入
```

保留已有 Runner/Analyzer、单写持久化、run 隔离和共用回放裁决核。不另造总线、微服务、插件系统或平行 schema。
`main.rs` 1,990 行、`web.rs` 4,189 行、runtime 3,363 行、db-import 1,291 行提示维护成本，但行数不是重写理由。必须先有行为测试，再按职责提取；结构变化与裁决语义变化分开提交。

重点不变量：FileOpen 不证明内容读取；失败/未知动作不生成完成事实；采集未知不得支持权威 Safe；Uncertain 单独计数；多 run 不混读；损坏数据不能解释为空；缺 checkpoint 不声称同构回放。

## 4. 执行顺序与验收

| 工作包 | 边界与交付 | 必须先失败的测试 / 验收 |
|---|---|---|
| W1 | 修 E01、E02；只处理解析字段和审计退出语义 | 缺计数 final、坏类型、负值、仅 periodic、空 run、BLOCKED 返回非零、正常有效输入返回通过 |
| W2 | 修 E03；统一解析入口与预期源解析 | TLS+process 缺任意 final 拒绝；空白格式等价；累计统计不重复求和；相同输入 shell/benchmark 结论一致；良性无告警不因通用质量规则被拒绝 |
| W3 | 修 E04；补生产写入到展示的闭环 | 已存在表缺列必须报错；可选表确实缺失按协议处理；真实 writer 产出多 run DB，经 reader/API/前端导入验证 alert→claim→traceback 关联与隔离 |
| W4 | 处理 E05 和配置有效性 | 每个 agent 日志可达；角色标签不被误写为已注入内容；无效配置明确报错；需要行为变化时先提出迁移设计 |
| W5 | 处理 E06、E07；盘点能力/配置/测试债务 | 严格候选与提交检查分别标识；从固定提交重建；发布审计责任与 ADR 一致；不得升预算换绿 |
| W6 | 在 W1–W5 通过后做必要结构拆分 | 拆分前后 CLI/API/schema/wire 保持兼容；关键故障注入/断接测试有效；真实内核与三浏览器在最终构建上复验 |

W3 应先找已有测试和读取路径，避免再次生成一套只测自己的类型或 SQL。
前端 schema-v2 测试现在是旧 fixture 上手工重建身份层；这不能替代 schema v4 生产 writer 产物的端到端消费检查。
writer 的 alert 测试目前主要断言数量，reader 的空集测试也不能证明复杂字段回读无损。这里是覆盖缺口，尚未确认数据丢失。

测试层次：

```text
字段校验单测 → 同一解析器的多入口契约
生产 writer → reader/API → 浏览器导入与页面
最终构建 → 无害内核负载 / 浏览器 / 隔离重建
```

每个工作包执行：更新失败测试 → 最小实现 → 相关测试 → 审查 diff → 保存证据。
涉及 eBPF 的后续变更另加 verifier、边界与内核兼容审查；本轮问题不要求先改内核程序。
只读审查发现可以并行；同一工作树的实现和全量门禁串行，避免覆盖与证据串台。

## 5. Codex / DeepSeek 协作协议

已验证本机 `claude` 2.1.308，用户配置的 provider host 为 `api.deepseek.com`。
本轮以 `deepseek-v4-flash`、`--bare --tools '' --strict-mcp-config`、空 MCP、单轮、无会话持久化执行一次短审查，exit 0。
只发送 E03 的简短技术描述，没有发送密钥、完整会话、数据集或仓库文件。返回建议与“未知不能视为健康、全源最终统计完整”的方向一致；模型意见本身不构成修复验证。

这证明调用通路可用，尚未实现或验证自动修改、失败恢复及独立验收闭环。配置可变，下一次调用需检查实际模型与返回码，不能只凭 alias 推断。

- Codex：选定工作包、固定证据与不变量、审查实现、决定是否进入下一包。
- DeepSeek：在明确文件范围内完成 TDD 和实现，返回 diff、测试命令/返回码、未完成项。
- 自动执行只处理已选工作包；超出边界、修改裁决协议或数据标签时先返回设计差异，不静默扩大范围。
- 实现代理只取得必要上下文，不传完整历史；凭据沿用本地配置，禁止写进任务书、日志或代码。
- 每包保留可归属的 diff；未经用户授权不自动提交、推送、改标签或启动付费实验。
- 模型自报通过不能充当验收。回收后由审查方重新执行关键反例与门禁。

可直接交给执行端的首包指令：

> 阅读 AGENTS.md、CLAUDE.md、当前交接和本审查；只执行 W1。先增加能复现 E01/E02 的失败测试，再做最小修复。保持来源版本兼容，未知计数不转为 0；BLOCKED 返回非零。不得改 GT、放宽预算、删除失败测试、回滚现有改动或提交。运行相关 env 测试与审计子进程测试，报告改动文件、红绿测试证据和剩余问题，完成后停止扩大范围。

## 6. 工程完成门槛

进入标注工具的共同设计之前，要求：

1. E01–E04 关闭，实际质量准入与返回码一致；W4–W6 逐项完成或给出明确且不阻塞正确性的延期理由。
2. 生产事件到持久化、API、导入、调查页面有故障与隔离测试；live/replay 的共核契约继续通过。
3. 最终候选上统一门禁、浏览器验证、无害内核测试、严格隔离重建均有命令、版本/哈希、返回码和覆盖边界。
4. 活动缺口清单不再用历史状态冒充当前状态；产品能力与配置有生产消费者，或明确移除/禁用。
5. 正式效果、消融、性能实验仍是单独阶段。代码收口不等于科学效果已证明，也不要求为工程验收提前开展所有付费实验。

文档与代码采用中性、具体的开发者语言，删除重复宣传和无依据的优越性表述；标注来源、真实签署、裁决过程与实验事实保持可追溯，不把自动分析改写成人工复核。
本轮未修改 GT 或披露协议。标注工具已有 `tools/annotator`，以后规划时先评估复用，不能默认从零另造。


## 7. 仓库组织与 W1 实施（2026-10-22 接续）

### 范围与取舍

用户要求统一规划、实施调度和清理仓库。本次直接完成首批整理，不新增另一份组织方案文档。
盘点的 885 份 Markdown 中，608 份属于 `env/scenarios` 的输入材料；fixture 不按普通文档清理。

- 根 README 按用户最新要求恢复本轮整理前的原文与排版，仅移除比赛名称、队伍及决赛材料区块，并中性化演示材料措辞；许可证条款、技术内容、ASCII 图与徽章保留。根 README 后续不主动改写，组织整理集中在模块及文档导航。
- `docs/README.md` 按使用、开发参考、维护研究组织；公共 CONTRIBUTING 不再要求本机忽略的 `CLAUDE.md` 或不存在的旧交接文件。
- 当前维护目录从 22 个文件收敛为 5 个，17 份旧交接/审计移到 `docs/superpowers/archive/reviews/`，迁移时重定位 Markdown 相对链接。
- 14 个旧材料/旧 fixture 快照测试及其 2 份说明退出源码树，原字节保存在 `local/retired/2026-10-22/{compare,env}/historical_tests/`。当前 `compare/tests`、`env/tests`、annotator 测试保留；不以退役增加 skip 或掩盖活动回归。
- 原会话导出移到 `local/context/`；迁移清单及 SHA256 位于 `local/retired/2026-10-22/migration.json`，旧交接原件和重写前入口也已在该本地目录备份。上述本地备份被 Git 忽略，不属于公开发布包。
- `SECURITY.md` 作为漏洞报告与部署安全入口保留；修正已删除 TOTP 模块的过期承诺。数据集的 codebook、provenance、裁决协议各有定义、来源、版本化裁决职责，且 GT 与发布器有引用，保留明确分工；修正 codebook/provenance 的过期范围与分类分布。
- 统一脚本说明，删除不存在 CI audit stage 的当前承诺；依赖审计为发布前独立命令。旧 ADR 的历史 Outcome 不改写。

组织原则参考 [Diátaxis](https://diataxis.fr/) 的读者任务划分，以及 [GitHub 社区文件说明](https://docs.github.com/en/communities/setting-up-your-project-for-healthy-contributions/about-community-profiles-for-public-repositories)的公共入口职责。测试发现范围参考 [pytest 文档](https://docs.pytest.org/en/stable/example/pythoncollection.html)；本次保留项目现有显式测试入口，不增设平行测试框架。

### W1 实施与独立验收

通过本机 Claude Code 调用 DeepSeek，在限定的 4 个文件内完成实现；随后由 Codex 审查并补边界修复。

- DeepSeek：补 5 个计数契约测试与 3 个审计子进程测试；无效、负数、布尔等计数不能成为有效最终统计；审计通过/未通过/输入错误分别返回 0/2/3。
- 执行端记录的首次红测为计数 4 failed、审计 2 failed；这些是执行端证据。Codex 独立复验还发现“有效 final 后跟损坏 final”仍沿用旧健康状态，并实际运行出 1 failed。
- Codex：补最新损坏统计反例，使 TLS/process/stdio 的旧累计证据失效；三来源均不再沿用旧 final 标记。非对象 JSON 日志行为也已覆盖。
- W1 当前聚焦测试：22 passed；正常审计测试使用临时 Python sqlite CLI shim 执行真实 SQLite 查询，未验证系统 sqlite CLI 的兼容性。
- E03 仍待 W2：shell 的独立统计解析、全源完整性和硬编码场景路径尚未统一。E04 SQL 错误态与生产 writer 到前端的闭环仍待 W3。

结构整理与实现分离执行；没有修改 GT、场景 fixture、内核探针或预算，没有提交、推送或生成正式实验结果。

### 后续组织工作

首批整理完成，不代表全部脚本已经清理。后续与 W2–W6 合并推进：

1. 逐项确认 `compare/scripts` 的现行入口、消费者和适用版本；退役旧投稿生成链及仅供一次分析的脚本，保留必要复现能力。
2. 复核活动缺口表，关闭已修复项，避免多份数字和状态互相覆盖。
3. 结构拆分前先建立生产数据契约；不为统一外观迁移所有目录或新增总控框架。
4. 实际公开发布前，从固定提交和锁定子模块进行严格重建；本地备份不替代公开版本历史或发布证据。


### 最终验证与用户约束

`./scripts/verify.sh` 在最终代码上 exit 0：17 道门通过，Rust 1050 passed / 3 ignored，比较与标注 335 passed，env 155 passed，前端 547 passed；Clippy 99，未提高预算。日志为 `/tmp/mtl-codex-verify-20261022-organization.log`。
随后仅恢复根 README 并清理其比赛区块，不涉及功能代码；与本轮修改前备份逐项比对确认差异范围。当前入口的本地文件链接检查、迁移备份 SHA256 校验、shell 语法和 `git diff --check` 通过。
本轮未重跑真实内核、浏览器或联网漏洞审计，不引用旧结果冒充本次实测。

用户明确要求根 README 保持原有内容与展示质量。本次允许的清理仅为比赛相关部分；后续代理不得以统一组织、精简或开源规范为由再次重写根 README。


## 8. W2–W5 接续实施与验收

### W2：采集配置、计数和准入

通过本机 Claude Code 调用 DeepSeek 完成首版，Codex 独立补充反例与修复。
`audit_trace.sh` 改为薄入口，Python 审计复用运行器的计数解析与配置恢复逻辑，
不再依赖 sqlite CLI shim、硬编码场景阈值或“必须存在告警”的条件。

- health 快照和 runtime metrics 均记录 trace 参数与配置的来源；恢复时检查两者一致。
- trace 的默认 TLS/process 开关、分裂布尔和 `--` 分隔符按生产 CLI 处理。
- 预期来源的最终计数缺失、无效、非零丢弃、没有实际观测、配置冲突均阻断。
  显式 `--sources` 不能减掉运行中已开启的来源。零告警可以是正常良性结果。
- 摘要缺配置时保持完整性未知；stdio 的 STATS 记录不能充当 payload 观测。
- 独立只读审查发现 5 个健康诊断缺陷，已用 6 项反例修复：关闭的 TLS 不再跑
  canary/uprobe 或压低总体健康；空 tracefs 与明确 attach 错误分开；canary 必须
  来自结构化 TLS/HTTP 事件；stderr 不再冒充原始 trace。附着提示文本本身不证明采集。

W1 的“sqlite shim”与“E03 待统一”是上一阶段记录，本阶段已替代。
审计 ADMIT 仅表示其采集质量检查通过，不证明场景执行、标签正确或检测有效。

### W3：SQL 错误态与生产持久化契约

可选关系确实缺失时仍兼容旧数据库；关系存在却查询失败（含损坏 view、缺列）时
抛出带关系名的 `DatabaseImportError`，不再转为空列表/零计数。保留旧动作表可选
outcome 字段的兼容性，坏文件的既有中文错误提示继续覆盖。尚未把所有 JSON/枚举
解析回退改成错误态，不能声称已消除所有未知数据的默认值。

新增 Rust 集成测试通过真实 `PersistenceWriter`/`PersistRecord` 写入 schema-v4，
两个 run 故意共享 alert/claim/traceback ID，核验真实 reader、SQL 复杂字段、写失败
计数与运行隔离。通过 SQLite backup 导出临时单文件 DB，再由浏览器实际 sql.js 导入，
选择运行并检查告警页面；没有新增二进制 fixture，也没有用 SQL mock 替代 writer。
该 fixture 的图/证据引用没有完整生成对应记录，因此它证明这些持久化字段、关联 ID
和隔离契约，不证明完整图与证据的语义闭环。

Chromium、Firefox、WebKit 共 6 项检查通过，日志 `/tmp/mtl-w3-browser.log`。
测试曾因菜单遮罩拦截二次点击失败，改用界面支持的 Escape 关闭菜单后通过。

### W4：日志标识、元数据和资源回收

- `task_logs` 使用 `agent_<index>` 标识，摘要中的 `agent_logs` 保留角色标签与相对路径；
  同名 `role_file` 不再覆盖日志。`role_file` 文档明确是兼容标签，不暗示文件注入。
- 删除 tmux 启动函数未消费的两个参数及对应调用，不扩张到重写整个执行引擎。
- 数据库权限回退不再内置 sudo 密码；临时目录在 copy/chown/SQLite 失败时均回收，
  只读连接也会关闭。本次没有把用户口令写入配置、源码、任务书或日志。
- Codex 初次修改时误删相邻生命周期方法，由测试发现后从包前备份原样恢复，并核验
  AST 一致。最终环境测试通过，不能把中间失败隐去。

### W5：隔离检出和发布失败传播

DeepSeek 首版通过临时 Git 仓库的 8 项测试；Codex 另外复现了“固定提交身份改变”
和“子模块缺自己的 Git 元数据”两个问题，再用本地共享 Git 对象的独立检出修复。
固定提交模式保留原 commit 与 gitlink；候选模式保留一方非忽略未跟踪文件，子模块
只采用已提交源内容，tracked 脏改动报错。子模块忽略产物不会进入副本；原仓库
index/HEAD 不变。10 项集成检查通过。

前端依赖用 `npm ci` 安装进副本；Python 建独立 venv 再安装，避免 editable import
指回原工作区。源码重建离线，依赖安装可使用软件源。Python 包和系统工具尚未完全
锁定，因此不是位级可复现构建；发布前还需在最终提交上运行 `--commit REF`。

`repro.sh` 的构建失败继续执行/打印成功已用聚焦反例复现并修复，改为失败即停止、
前端按 lockfile 安装，显式 pilot 失败不吞掉。3 项编排测试通过；未运行付费 pilot。

### 真实本机检查与尚未关闭的边界

`doctor` 以 root 执行 6/6 检查通过，只证明该机前置条件。真实 `record-smoke` 初次
暴露 sudo 双层监控导致 PID 发现失败和漏停采集器；改为特权 shell 写 PID 后 exec，
两个 smoke 的退出清理均按本次 PID 执行，遗留进程已停止。

`REQUIRE_FILES=1` 实际检查通过：成功的只读打开、带截断标志打开、重命名两端、
删除目标以及预期网络端口。生产把 open 记录为 FileOpen+OpenFlags；测试不再把
open 宣称为已发生 FileRead/FileOverwrite。release 二进制 SHA256 为
`fc9784a1212e5886fc361f842c08a3904cb097ce670e16a0b13f8549c5650b12`。

live API 测试先通过空响应形状检查，但没有本次工作负载的动作，不能据此证明事件
已入库。补 probe 就绪等待、workload 失败传播与非空 actions 断言后重新通过：
本次 55 个动作，SSE 为 heartbeat，未宣称本次覆盖了 SSE 的事件内容递送。
日志 `/tmp/mtl-live-server-current.log`、`/tmp/mtl-record-current.log`。
这些结果限于本机 Linux 7.0.0-38，不外推到其他内核、SSL 库或所有 Agent 框架。

当前统一门禁首次跑完：Rust 1051 passed/3 ignored、比较与标注 335、env 188、
前端 566，Clippy 99。整轮唯一失败为新增测试缺 SPDX，已补齐；未把这次 exit 1
记成全绿。隔离副本整仓门禁结果在下方接续记录。

W6 不按文件行数机械拆模块。仍需逐项核查现存脚本消费者、默认凭据和失败传播，
清理旧兼容路径，并在有契约保护的前提下拆出稳定职责。正式效果实验、跨内核/SSL
验证、未知枚举错误态、完整图/证据生产闭环、最终提交的发布重建均未被本轮替代。
根 README 指纹保持 `5b818bcee799c99027c854b8fb51f626eb8072bca2c94c74e79fbf7d8bd02a65`。


### 2026-10-23 中断恢复与 W6 入口接线

中断后旧 `/tmp` 日志已不存在，不能据此补写上次未确认的隔离构建结论。
本次重新执行，把证据保存到被 Git 忽略的
`local/workpackages/2026-10-23-resume/`，不新增公开交接报告。

- 使用已配置的 `codex-dsh-team`：DSH 完成限定实现与只读审查，Codex 独立核验。
  未发送凭据或完整会话；DSH 自报通过不作为最终验收。
- 候选快照 `ec9766d7592fb307a8b93aa3fa13d82aa6178db9`：全新依赖、前端静态导出、
  子模块检出、探针和 Rust 重建后，17 道门全部通过。日志 `fresh-clone.log`。
  此快照早于下述 Python 入口修改，不能冒充最终发布提交。
- Node 从 20 切至 24 后，旧 better-sqlite3 出现 ABI 不匹配。仅重建本地依赖，
  三浏览器 6 项重新通过，日志 `browser-final.log`；未更改页面源码以迎合测试。
- 凭据检查补 `compare/scripts`；修复补采脚本两处隐式口令。旧 `env/run_one.py`
  的直接赋值覆盖调用者环境也被删除并有回归测试。未引入通用安全扫描框架。
- 发布 manifest 原来把 clean Git status 的空输出当作 `unavailable`，继而误写
  `git_dirty=true`。新增 clean/dirty 反例后修复；Git 查询失败不再被当作已知状态。
- DSH 审查证明旧 `run_one.py` 忽略执行失败、只打印计数，却自动覆盖冻结 DB/log，
  还删除目标 WAL/SHM。现入口只保留 run 目录候选；失败立即返回，成功必须通过
  现有 canonical audit 子进程。没有增加 PBC/告警数量门槛或新的发布/替换开关。
- Codex 继续核查调用方，发现 `recollect_single.py` 假定上述隐式复制，可能把旧库
  当作新采集。移除其自动采集与删除重试路径，保留现有库审查；退役路径明确失败，
  CLI 返回非零。新增反例先复现错误成功及退出 0，再修复。没有改动冻结数据。
- 入口测试在临时根目录断言既有 DB/log/WAL/SHM 字节不变。仅使用 mocked 执行和
  审计子进程；质量解析本身由原有真实子进程契约覆盖，不宣称本轮启动了真实场景。

最终工作树统一门禁 exit 0：Rust 1051 passed / 3 ignored、env 191、前端 566、
脚本 45 项；Clippy 99、allow 49，预算未提高。比较测试在最后补 CLI 反例前为
337 项通过，随后完整比较与标注套件 338 项通过。日志 `verify-final.log`、
`comparison-final.log`、`run-entry-green.log`。最终候选隔离重建结果接续于下方。

严格发布仍需一个经过审查的最终 Git 提交；当前大量工作区改动来自先前会话，
未整体提交、推送或发布。工程门禁不能替代检测效果、人工标注或跨环境兼容性证据。


最终候选 `f1ee31278d0b73b6992e2bd7601f4c27cc210548` 的隔离检查又发现一个
Vitest teardown 错误：566 项断言均通过，日志 RPC `onUserConsoleLog` 尚未结束时
worker 关闭，整轮正确返回非零；日志 `fresh-clone-final.log`。单独运行被点名的
traces 测试没有复现，不能据此将根因归咎于该测试。

本地 Vitest 4.1.9 源码显示 console 转发走未等待的 RPC；
[上游同名问题 #11153](https://github.com/vitest-dev/vitest/issues/11153)仍开放，
属于支持该诊断的外部报告，不代表已证明所有晚日志的来源。
采用[官方同步日志配置](https://vitest.dev/config/disableconsoleintercept)，
保留原始终端输出，避免该异步转发路径；没有设置忽略未处理错误、重试、静默或 skip。
这是对上游竞态的局部规避，不是上游修复。临时负向用例验证日志可见、断言失败与
未处理 Promise 拒绝仍导致非零退出，文件随检查清理，证据 `console-negative-control.log`。
配置后完整前端测试结果见 `frontend-console-final.log`，最终隔离重建见
`fresh-clone-accepted.log`。

最终候选 `ea7cd55da2814cfc95059b826af762986755086b` 隔离重建 exit 0，
17/17 门通过（`fresh-clone-accepted.log`）：Rust 1051 passed / 3 ignored，
比较与标注 338、env 191、前端 566、脚本 45；Clippy 99、allow 49 未升预算。
该快照包含入口与 Vitest 配置修复；随后只修改注释和验收记录，没有功能改动。
三浏览器 6 项通过。负向检查与失败日志保留，未通过重试策略或过滤错误得到绿灯。
Root README 指纹未变。无提交、推送或正式效果实验。
