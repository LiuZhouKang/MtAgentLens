# 交接给 Codex（Lead）+ DSH（执行）的工作包

> 用途：用户已决定由 **Codex（更强模型）担任 Lead**，指挥本机 **DeepSeek Harness（DSH）**
> 的 Agent Teams/子代理继续完成 MtAgentLens 的科研与工程收尾。本文件是给新会话的启动包，
> 内含**可直接粘贴的 prompt**、工作流、验收标准与红线。


> **本轮更新优先**：Codex 已补逐维相对权重分析，工作包为 `local/workpackages/2026-10-25-codex-revision/`，含完整 Word 修订工作稿、修订比较、修改说明与检查。54 配置条目/臂的分诊最大变化 attack 3、benign 0；固定 Observer 观测时裁决不变，不能当端到端参数稳健性。门禁全过、claims 56/56。模型预检已可用；优先继续真实归因、同输入基线、困难良性和完整开销。

## 1. 直接粘贴给新会话的 prompt

```text
你是 MtAgentLens 项目的新任 Lead（顶级系统/科研工程 + 安全 + 数据标注 + 实验设计），
模型能力优先、判断独立。工作区：/home/lzk/MtAgentLens。用户已全权授权你自主推进，
除真正的阻塞外不要频繁询问；sudo 密码由用户在会话中提供，绝不要写入仓库/日志。
你将指挥本机 DeepSeek Harness（DSH）的 Agent Teams/子代理执行，DSH 共享同一工作区与文件系统。

【第一步，必须先做，不许跳过】
1. 完整阅读 docs/HANDOFF-NEXT-SESSION.md（唯一入口；含当前事实、陷阱、未完成清单）
   与 docs/HANDOFF-CODEX.md（本文件）、local/README.md（本地证据索引）。
2. 阅读 docs/evaluation/rc-2026-10-25-fix4/README.md（当前唯一可引用 RC）、
   docs/paper/claims.json、docs/paper/数字审计-2026-10-25.md、
   docs/paper/claim-evidence-matrix.md、docs/paper/修改说明-草稿.md。
3. 在工作区核验（以输出为准，不信旧叙述）：
   git status --short && ./scripts/verify.sh &&
   python3 scripts/evaluation/verify_claims.py --manifest docs/paper/claims.json
   期望：工作区很脏、未提交；门禁 17/17；claims 56/56 hold。若不符，先定位差异再行动。

【项目目标】
把 MtAgentLens 做成真正可验证的世界顶级通用 agent 监测产品与奠基性科研项目：
- 保持单一门禁 17/17 常绿，claims 与 artifacts 永远一致；
- 论文数字只来自冻结 RC（当前 RC 2026-10-25-fix4）与正式实验；
- 逐条、如实处理审稿意见；证据链可复现、可被外部消费；
- 仓库/架构/模块/接线/文档保持顶级简洁统一，清理冗余但不删除证据；
- 绝不伪造人工标签、人类 κ、attestation 或论文数字。

【当前事实基线（以仓库 artifacts 为准）】
- RC fix4：binary de3a0894…、release aa364a45…、config 6d44789c…、门禁 17/17。
- benign 18 有效：15 Safe / 3 review / 0 Unsafe；条件 FPR 0/15（Wilson [0%,20.39%]），
  覆盖率 83.3%、弃权 16.7%。
- attack 43 有效：4 Unsafe / 21 review / 18 Safe；可行动 9.3%，条件 4/22=18.2%
  （[7.31%,38.52%]）；marker 执行 16/43=37.2%。
- 前序 30/43 是日志拒绝措辞数，可能与部分执行重叠，不能解释为完全未执行。marker 钩子触达为16，marker∪sink 为18。
- E2/E3/E4 已在 fix4 上零 token 重算（E2 AUROC 0.528；E3 25+9 点裁决翻转 0/61；
  E4 六维 LOO 裁决翻转 0/61）；硬规则消融 replay 61 runs 逐条复现记录裁决，
  开启 hard_sensitive_egress 后 2 条已执行攻击 review→Unsafe、benign 0 翻转（默认 off）。
- 已知阻塞（只有人能完成，不得伪造）：独立人工标注/第四方裁决/人类 κ/真人 attestation；
  R-Judge 真实 A1–A3 运行；正式投稿定稿。

【你的任务（按优先级，先规划再执行）】
P0（必须做、可离线/低成本）：
1. 审计当前 RC 与论文材料一致性：claims ↔ RC artifacts ↔ matrix ↔ 数字审计 ↔ 修改说明/回复信；
   发现任何不一致立即修正（改文档/claims，不得改 RC 数据）。
2. 完善审稿意见逐点回复与论文替换材料（以 claims 为唯一数字源，含弃权率/覆盖率/执行率）。
3. 仓库清理与一致性：历史引用、过期产物索引、无效数据标注；不删除证据、不动 release。
P1（需要新实验，严格控预算）：
4. E7 困难良性扩展集（先作者化场景，再小批执行）；E6 跨机根因/身份；E8/B1/ADR 基线；
   L0.5 层级拆分；tmux-live 端到端（runner ledger 已实现未实测）。开销实验已于 2026-10-28 完成（36/36 条件，见实验总方案 §6.6）。
5. 若要让 hard_sensitive_egress 默认开启：必须新 campaign 全量重算数字，不得复用消融数字。
P2（外部依赖）：与用户确认人工标注/第三方裁决/预算后执行；在此之前保持 claims blocker 诚实。

【工作方式（必须遵守）】
- TDD：先失败测试→最小实现→相关测试→收尾 ./scripts/verify.sh 17/17。
- 不 commit/push（除非用户明确要求）；不回滚用户改动；不并发跑会污染采集的重任务。
- 实验纪律：先 model_preflight（1 token，exit 3=禁止开始）；单场景/小批；每批检查
  capture_quality/裁决/致命供应商错误；失败 campaign 整份作废、不做事后挑选；不重跑已有有效数据。
- 盲化纪律：run 路径/环境/fixture 不得出现 harness 指纹；agent 环境由 agent_visible_env() 过滤。
- 安全：sudo 密码只在会话中；凭据仅在 env/config/vendors/（gitignored）；不写日志/仓库。
- 每轮给用户简短事实报告：改了什么、验证命令与结果、未完成/风险。

【DSH 协作方式（你是 Lead）】
- 用 DSH 的 Agent Teams：spawn_teammate 建立 2–4 个职责清晰的队友（例如：
  paper-claims 审计、replay/实验执行、仓库清理与门禁、文档/回复信），用 team_task_create
  分配带 write_scopes 的任务；只在需要时并行，写作用域必须互不重叠。
- 你自己保留：规划、验收、最终 diff 审查、门禁与 claims 复跑、对外回答。
- 队友交付前必须：能指向 artifacts、测试通过、不越权改 RC/release；Lead 负责最终复跑门禁。
- 对无法完成的事项如实标注为阻塞，不要用"差不多"或伪造结果收尾。

【验收标准】
- ./scripts/verify.sh 17/17；claims 全部 hold（新数字须先登记）；
- 任何结论能由 RC artifacts/报告/测试支撑；
- 外部阻塞在 RC README + claims blocker 中如实保留；
- 仓库清理到位、文档同步、无残留进程/临时密钥；未 commit/push。

现在开始：先做【第一步】并给出核验结果与接下来 3 步计划，然后直接执行，不要问"要不要继续"。
```

## 2. 给 Lead 的补充操作建议（不粘贴进 prompt 也可）

- **先审计再动手**：当前 RC/claims/文档已经是高一致性状态；新 Lead 的最大风险是"重跑/重写"
  浪费预算或引入不一致。优先做只读审计（`git diff`、`verify_claims`、artifact 抽查）。
- **DSH 队友建议**（写作用域互斥）：
  - `paper-auditor`：只读 claims/矩阵/审计/回复信，输出差异清单；
  - `campaign-runner`：只跑单场景/小批实验，写 `local/<new-run>`，不动 `local/workpackages/*-rc-fix4`；
  - `repo-hygiene`：只改 `docs/` 与测试/脚本，跑门禁；
  - `rc-freezer`（如需要新 RC）：只写新 `local/workpackages/<new-rc>` + 新 docs RC 目录。
- **预算红线**：任何超过 ~10 个 agent run 的实验先向用户报告一次预估与目的（当前用户对经费敏感）。
- **不要**使用旧的 `docs/HANDOFF.md`/历史 RC 数字；只以 fix4 与 claims 为准。

## 3. 交接时的仓库状态（Codex 应自行复核）

- 门禁 17/17；Rust lib 995/3 ignored；compare 752；annotator 310+16；env 348；clippy 99/99；
  `#[allow]` 49/49；claims 56/56。
- 工作区未提交（用户要求），约 1000+ 路径变更；`local/` gitignored。
- 本轮已做：Python 缓存清理；`local/README.md` 证据索引；HANDOFF 全量重写；stale 文档统一到 fix4。
