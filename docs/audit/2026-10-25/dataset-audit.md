# FuxiBench 数据集与标注完整性审计（只读）

- 审计时间：2026-10-25 19:13–19:35（Asia/Shanghai）
- 审计对象：`datasets/FuxiBench`（声称 v1.4.0）、`local/workpackages/2026-10-25-sink-release2/FuxiBench`（RC fix4 输入，release_id `aa364a45…`）、`tools/annotator/`、`local/workpackages/2026-10-24-pilot-codebook-v1/`
- 审计方式：脚本 + 只读命令核验，**不采信文档自述**；本地核验脚本 `local/history/audits/audit/2026-10-25/verify_dataset.py`，原始结果 `local/history/audits/audit/2026-10-25/verify-dataset.json`（本地产物，不随仓库分发）
- 边界：**未修改** `compare/Dataset/`、**未修改** release 目录、未 commit/push。唯一一次副作用见 §7 披露（studio 冒烟启动写入了 pilot `records.sqlite` 的 SQLite 头，逻辑行数不变）。

---

## 0. 一句话结论

**FuxiBench GT 的“内容”接近专业标注（3 份独立评审 + 规则化裁决，κ 可重算复现），但“署名与 provenance”不诚实，不能按当前 metadata 当作人工标注发表；运行级人工标注完全没有做（2 个良性任务、0 真人修订、0 attestation，且 2 < 15 不足以算 κ）；根因 GT 缺失（9 个 Type C / 23 个多 Agent 场景中 7 个 Type C 没有任何 root_cause/entry_point/chain 字段）。**

| 层 | 结论 | 能否支撑审稿人质疑 |
|---|---|---|
| A. FuxiBench 场景级 GT | 内容专业度可用（字段完整、243 条行为、κ(label)=0.9823 / κ(type)=0.8754 由原始 reviewer JSON 重算一致）；但 provenance 与署名不诚实、GT 与发布包自相矛盾 | **不能**声称“三人独立人工标注”；必须改述为“3 个独立 reviewer agent + AI 规则裁决” |
| B. 运行级人工标注 | **未执行**。pilot：2 个良性任务、6 条 `model_proposal`、0 条 `method=human`、0 attestation、`paper_metrics_available=false`；工具链可用，但样本量不足 | 不能声称人工 κ / 人工复核 / 人工签署 |
| C. 根因 / 入口 GT | **缺失**。0 个 `root_cause`、0 个 `chain`；仅 2 个场景有 `payload.entry_point(s)`，3 个有 `payload.delivery_chain`；9 个 Type C 中 7 个无任何根因/入口/链字段 | R1.2 / R2.4 未满足，必须补 |

---

## 1. 问题表

严重度：**S1 = 投稿诚信阻断**；S2 = 复现/发布阻断；S3 = 需修但不阻断。

| ID | 严重度 | 问题 | 证据（文件 / 字段 / 计数） | 影响投稿诚信 | 最小修复（不改标签语义） |
|---|---|---|---|---|---|
| A-01 | **S1** | metadata 把 AI reviewer agents 写成“three independent reviewers”，三人名写在 `annotation.annotators`；机器可读入口看不到“AI 不是人” | `metadata.json`：`annotation.annotators=["刘周康","唐浩钺","刘锦样"]`、`annotation.method="three independent reviewers under ANNOTATION-CODEBOOK v4; adjudicator merges by majority/evidence…"`、`quality="independent_reviewers_codebook_v4"`、changelog 2 条 “three independent reviewers”。93/93 GT 的 `annotation.method` 同为该串。对照：`ground_truth/_agreement.json.note`=“AI reviewer agents; not human annotation”；`PROVENANCE.md` §2 明说“独立 reviewer **agents**，不是三名人类标注员” | **是**。同一数据集两个事实版本；审稿人抓到即构成“AI 冒名人类” | 按 §4 改 metadata：加 `reviewer_kind:"ai_agent"`、`human_annotation:false`，method 改为“…independent AI reviewer agents…; rule-based AI adjudication (ADJUDICATION-PROTOCOL v1); **not human annotation**”；把 `annotators` 改名为 `reviewer_agent_labels`（保留旧名兼容） |
| A-02 | **S1** | 93 个场景 × 3 份 reviewer 记录只有 **1 份**可回溯到原始模型产出；其余 92 个场景的“独立评审”无法证明不是事后生成 | `ground_truth/_sources/` 仅 `data-exfil-direct-tang.json`（provenance: source_session=`0e755168…/subagents/workflows/wf_1042c4b8…jsonl`, method=“Exact recovery of original Write content”，即模型 Write 工具产物）；`_reviewers/` 93 文件 × 3 人，无 `method=model_proposal` 字段 | **是**。无法证明 3 份评审相互独立、无法证明评审先于 GT | 为每份 reviewer 记录补 `provenance`（session/sha256/role）；补不出的如实写 `provenance:"unavailable"`，不得默认当独立 |
| A-03 | **S1** | “需人工复核”清单被改名藏起来：摘要报 0，实际 74 项仍是待人工复核状态 | `_summary.json`：`needs_human_review=[]`、`needs_human_review_count=0`，但同文件 `ai_adjudicated_count=74`；74/93 GT 有 `quality.legacy_needs_human_review=true`；74/93 有 `quality.review.status="resolved"` + `review.human_review="waived_by_author"`；83/93 GT 的 `quality.notes` 出现 `needs_human_review` 字样；13 份明写“需人工复核”；审阅者级 134/279 条 `needs_human_review=true`；metadata 写 “needs_human_review removed on 2026-10-22” | **是**。把“74 项未真人复核”变成“0 项需要人工复核”，是发布口径失实 | `_summary.json` 增 `legacy_needs_human_review_count:74`、`human_review_performed:false`、`reviewer_agents:3`；metadata 的 `annotation.review` 改述为“字段改名为 legacy_，74 项由 AI 规则裁决关闭，未经真人复核”；不要删除 legacy 字段 |
| A-04 | **S1** | `human_review:"waived_by_author"` 被读成“已豁免=已通过” | `metadata.adjudication.human_review="waived_by_author"`；74 GT `review.human_review="waived_by_author"`；ADJUDICATION-PROTOCOL §0 自己承认“不是人类复核”“不是人类签署” | **是**（语义歧义） | 保留 `waived_by_author`，新增 `human_review_status:"not_performed"` 与 `human_review_note:"author waived per-item human review; rule-based AI adjudication only; no human sign-off"` |
| A-05 | **S2** | GT 内部仍用真人评审口吻：“三人”“少数意见”“需人工复核” | 合并 GT `quality.notes`：68/93 含“三人/三位”，3/93 含“少数意见（唐浩钺…）”（resource-runaway-loop、resource-disk-fill、malicious-feedback）；52/93 含“旧 fixture/旧版” | **是**（暗示真人分歧） | 不改标签，仅在 notes 前加统一前缀 `[2026-10-20 reviewer-agent 原始记录，2026-10-22 AI 裁决后保留]`；把“少数意见”改为“agent 分歧（少数 reviewer agent）”；把“三人”改为“三个 reviewer agent” |
| A-06 | **S2** | 声明的 `review.notes_disclosure` 机制不存在，陈旧 prompt 断言没有被标为“已被取代” | `PROVENANCE.md` §2、`ADJUDICATION-PROTOCOL.md:97`、`supplychain-typosquatting.json:387` 均引用 `review.notes_disclosure`；实际 93 个 GT 的 `quality.review` 只有 9 个键（status/method/rule/resolved_at/human_review/evidence/limitations/residual_risks/type_change），**无 notes_disclosure**；73/93 GT notes 提及 `prompt_inputs`，56/93 断言其“旧版/不匹配/需重建” | **是**（文档承诺未实现） | 要么实现 `review.notes_disclosure`（对声明过时的 notes 打标），要么把 PROVENANCE §2 的该句删掉/改述；同时把 56 条陈旧断言标为 `superseded_by` |
| A-07 | S3 | 17 份 GT 的 `quality.notes` 断言 `attack_type=X`，与裁决后字段不一致；11 份 notes 说“阻断复跑”，但 `review.status=resolved` | notes vs field：17（如 prompt-injection-direct field=B/notes=A、malicious-feedback field=B/notes=C）；11 份 resolved 但 notes 仍写“阻断复跑/必须重建” | 否（属于历史记录），但会误导审稿人 | 同上，用 notes 前缀 + `superseded_by` 标注历史属性；不要改字段 |
| B-01 | **S2** | 数据集工作树不是冻结物：v1.4.0 的文件在 2026-10-25 当天被批量改写，metadata 仍写 `dataset_updated=2026-10-22` | 373/768 文件 mtime=2026-10-25；`git status`：176 modified、124 deleted（`_external_archive`）、27 untracked；所有 93 个 prompt manifest 的 `source_scenario_git_rev` 为 `dirty:*`（87）或 `uncommitted`（6），没有干净 commit 绑定；GT mtime 最新 2026-10-25 13:21，prompt 13:27、manifest 13:29 | **是**。无法指向一个可复现的冻结版本 | 冻结前：clean checkout 重建 + bump 版本（1.4.1）+ 重新生成全部哈希 + 提交；metadata 写入真实 `dataset_updated` 与 `dataset_git_rev` |
| B-02 | **S2** | prompt_inputs 三份来源互相矛盾：prompt.txt ≠ manifest ≠ bundle（manifest 与 bundle 彼此一致） | 93/93 `prompt.txt` 字节与 sha256 都不等于 `manifest`；93/93 bundle 的 `prompt_text_sha256` 却等于 manifest；项目自带 `compare/scripts/build/refresh_prompt_input_hashes.py --check` 实测 **323 hash mismatches**；69/93 case、117 个 fixture 文件 sha 与 bundle 不符 | **是**（“89/93 场景可复现/89%…”类声明会被直接推翻） | 用同一生成器一次性重建 prompt.txt+bundle+manifest；跑 `refresh_prompt_input_hashes.py --check` 归零；把该 check 加进发布门禁 |
| B-03 | S2 | 发布树 ≠ fix4 release：数据集与 release 是两代不同文本 | 共享路径 377 个，其中 **374 个内容不同**：`ground_truth` 186（93 GT + 93 reviewers）、`prompt_inputs` 187、`metadata.json` 1；release 独有 1214（fixtures 1051、scenarios 93、bench 68）；dataset 独有 391。差异根源：release 是 pre-blinding（93/93 prompt 含 `MtAgentLens`/`mtal`，68 个含 sink host `93.184.216.34`），数据集是 2026-10-25 的 Acme 重命名版 | **是**（论文说“数据集→fix4 输入”链条不成立） | 在 README/论文中明确：fix4 使用 release2（pre-blinding，`aa364a45…`），发布数据集是 post-rename 版本；或重跑 campaign 用发布版重冻结 |
| B-04 | S3 | GT 中 58/93 文件含作者本机绝对路径 | 680 处 `/home/lzk`，577 个不同字符串；字段分布：`.annotation.inputs_used` 442、`.quality.review.evidence` 155、`.quality.notes` 53、`.quality.review.limitations` 27、`.quality.review.residual_risks` 3；`_reviewers` 3 文件 7 处；prompt_inputs/metadata 为 0 | 是（泄漏环境；公开分发时泄露作者用户名/目录结构） | 对 GT 做纯文本替换 `/home/lzk/MtAgentLens` → `<repo>`；重算 `_sources`/`_reviewers` 中相关 sha；不要把本机路径留作“证据” |
| B-05 | S3 | 历史 review 指出的两处发布缺陷在当前树**已不能复现**，但出现新的哈希缺陷 | malicious-feedback：当前 `prompt.txt`=12,827 B、`bundle` fixture=15 个、manifest `fixture_file_count=15`（一致）；已不是 517,930 B / 87 文件。atlas-recon-discovery 的旧 prompt 快照已被 2026-10-25 重建（prompt 内已是当前 fixture，如 `baseline/credential_template.env`）。但新缺陷=B-02 | 否（旧缺陷已修） | 不要把“已修复的旧缺陷”写进回复信当现状；改报 B-02 |
| C-01 | **S1** | 无根因 / 入口 / 归责链 GT，审稿 R1.2/R2.4 无法回答 | 93 GT 顶层键无 `root_cause`/`root_actor`/`chain`；`payload.entry_point(s)` 仅 2 个（handoff-unicode、context-multihop）；`payload.delivery_chain` 3 个；`execution_check.process_chain` 1 个；有任一根因类字段的文件 7/93。23 个 `multi_agent_attack` 中 attack_type C=9、B=14；9 个 C 中 7 个完全无入口/根因字段 | **是**（R1.2/R2.4 的核心要求） | 在 23 个多 Agent（至少 9 个 Type C）GT 中新增 `root_cause_actor(s)`、`entry_point`、`delegation_chain`（用既有 `_reviewers` 文本可推导）；先人工/AI 草案 + 如实标注来源 |
| C-02 | S3 | GT 子字段类型不一致 | `evidence_requirements`：142 条是 list，101 条是 string（codebook 要求 string） | 否 | 统一为 string 或改成 list 并更新 codebook；不改语义 |
| C-03 | S2 | E7 困难良性仍是 spec-only | `env/scenarios/_drafts/e7-difficult-benign/spec.json`：`status:"spec_only"`，4 类 × 5 = 20 个计划场景；0 场景 JSON、0 fixtures、0 GT、0 执行、0 FPR；结构测试 2 passed（断言只有 spec） | **是**（审稿意见 7 的误报质疑无数据） | 按 plan 实现 20 个场景 + fixtures + GT + execution probe，跑 campaign 后报 Wilson FPR；在论文写“规格已备，数字未产” |
| D-01 | S2 | release 的 `paper_metrics_available=true` 与“无人工标注”冲突 | release `metadata.json`：`paper_metrics_available=true`、`publication_blockers=[]`；跑第三方 `scripts/evaluation/annotation_release_consumer.py --root <release>` 返回 **14 problems**，含 “paper_metrics_available must be false while there are no human annotations”、counts/cases/splits/attestations 缺失（它不是 `mtagentlens.annotation-dataset/1`） | 是（若把该 release 当“可发布数据”） | release 只作运行输入快照：把 `paper_metrics_available` 改为 false 或加 `kind:"input_snapshot"` 并说明消费者不适用；不要用 annotation consumer 的 VERIFIED 当 FuxiBench 的发布验证 |
| E-01 | S3 | 2 个良性任务不足以支撑运行级人工 κ | pilot release：`counts={cases:2, annotations:6, model_proposals:6, human_annotations:0, attestations:0}`、`paper_metrics_available:false`、6 条 publication blockers；`readiness.json` 2/2 ready；PILOT-CODEBOOK §8.3 要求 ≥15 任务、`attempted/executed/succeeded/roots` 每字段 κ≥0.60 且每字段≥2 类 | **是**（不能声称人工 κ；不能替作者签名） | 见 §9 最小操作步骤；先补到 ≥15（计划 30）个含攻击+良性的冻结运行，再算 κ |

---

## 2. 核实细节

### 2.1 GT 标注“真实性”与元数据诚实性（对应任务 1）

**事实 1：三个名字背后是 reviewer agents，不是人。**
- `ground_truth/_agreement.json.note`：`"AI reviewer agents; not human annotation. Jaccard compares attack-pattern category sets, not behavior semantics. Undefined kappa is null."`
- `PROVENANCE.md` §2（数据集自称的权威说明）：“本轮的‘三名独立标注者’是**在作者团队 codebook 下工作的独立 reviewer agents**，不是三名人类标注员的人工流程……该流程**不能**被表述为‘三名安全研究人员的人工独立标注’。”
- `_reviewers/data-exfil-direct-tang.json`（唯一带原始来源的一条）证明评审是模型 Write 产物：`source_session=0e755168…/subagents/workflows/wf_1042c4b8-4b4/agent-…jsonl`，`method="Exact recovery of original Write content…no semantic edits"`，`source_sha256`/`raw_review_sha256` 齐全。
- 结论：**PROVENANCE 是诚实的，metadata.json 是不诚实的/至少是失真的**。93/93 GT 的 `annotation.method` 与 metadata 同串；93/93 GT `annotation.annotators` 都是三名真人姓名；`quality` 标签叫 `independent_reviewers_codebook_v4`。

**事实 2：只有 1/93 有 provenance，92 个“独立评审”不可证。**
- `_sources/` 只有 1 个文件；每个 `_reviewers/<sid>.json` 是 3 份合并记录，没有逐份 `method=model_proposal` / session hash / 时间戳。`reviewers` 的 annotation 块也互相不一致：刘周康=`annotation_role:"independent reviewer 1 of 3"`；唐浩钺=`annotator_id:"唐浩钺"`+`review_stage:"independent_reviewer"`；刘锦样=`method:"independent single-annotator pass; inputs only"`。
- 项目自己的红线（`docs/HANDOFF-NEXT-SESSION.md` §0 / `docs/paper/annotation-protocol.md` §5）：AI 标注只能记 `method=model_proposal`，不得改名 human、不得删 provenance。**当前 GT 没有遵守这条红线**（没有 `model_proposal`，只有人类姓名）。

**事实 3：κ 可复现（这是优点）。**
- 从 `_reviewers/*.json` 的 3×93 个 label/attack_type 重算 Fleiss κ：
  - label：`0.9823417721518986` = `_agreement.json` 声明值（一致）；
  - attack_type：`0.8753503107103693` = 声明值（一致）；
  - percent agreement 0.9892 / 0.8817 同 `_agreement.json`。
- 但注意：这是 **agent 间一致性**，不是人类 κ；`_agreement.json` 与 `PROVENANCE` 都写明了，metadata 没写。

**事实 4：“需人工复核”被改名后计数归零。**
- `_summary.json`：`needs_human_review: []`、`needs_human_review_count: 0`、`ai_adjudicated_count: 74`（同一文件自相矛盾：74 个是 AI 裁决出来的）。
- 数据侧：74/93 `quality.legacy_needs_human_review=true`；74/93 `quality.review.status="resolved"`；74/93 `review.human_review="waived_by_author"`；19/93 无 review 块（未裁决）。
- 文本侧：83/93 GT notes 出现 `needs_human_review` 字样；13/93 明写“需人工复核”；32/93 含“人工复核”；53/93 含“人工”；68/93 含“三人/三位”；3/93 含“少数意见（唐浩钺…）”。审阅者级 134/279 条 review 的 `quality.needs_human_review=true`（145 条 false）。
- 项目自带 `scripts/evaluation/gt_summary.py` 与 `validate_gt.py` 都读已被移除的 `quality.needs_human_review`，所以两者都报 `review:0 / needs_human_review:[] / problems:[]`——**自检门禁对这张清单是瞎的**。
- 对照：`ADJUDICATION-PROTOCOL.md` 本身是诚实的（“不是人类复核”“不是人类签署”“human_review: waived_by_author”）。所以这是 metadata/摘要层的失实，不是协议层造假。但 `metadata.json` 是外部审稿人与消费者最先读到的文件，必须改。

**数量统计汇总（供写作核对）**

| 项 | 值 |
|---|---|
| GT 文件 | 93（67 attack + 26 benign；A=3 / B=55 / C=9；11 类目录） |
| reviewer 记录 | 93 × 3 人；每人 93 份 |
| 有原始 provenance 的 reviewer 记录 | 1 份（`_sources/data-exfil-direct-tang.json`） |
| `legacy_needs_human_review=true` | 74 |
| `review.status=resolved` / `human_review=waived_by_author` | 74 / 74 |
| notes 明写“需人工复核” | 13 |
| notes 含“三人/三位” / “少数意见” | 68 / 3 |
| `_summary.needs_human_review_count` | 0（与 74 冲突） |
| Fleiss κ(label / attack_type) 重算 | 0.9823417721518986 / 0.8753503107103693（与声明一致） |
| `/home/lzk` 泄漏 | 58/93 文件，680 处，577 个不同字符串 |

### 2.2 数据卫生（对应任务 2）

- **本机路径泄漏**：见 B-04。公开分发前必须清洗；这份 GT 里 `annotation.inputs_used` 直接写着 `/home/lzk/MtAgentLens/env/scenarios/...`，换机器后这些“证据引用”不可解析，属可复现性缺陷。
- **旧 prompt / 旧 fixture 引用**：任务里提到的两处历史不一致在当前 v1.4.0 工作树**已不能复现**：
  - `malicious-feedback`：prompt=12,827 B、bundle fixture=15 个、manifest `fixture_file_count=15`，一致（不是 517,930 B / 87 文件）；
  - `atlas-recon-discovery` 旧 prompt 快照：2026-10-25 重建后 prompt.txt 已内嵌当前 fixture（`baseline/credential_template.env` 等），不再是旧 619 B 文档。
  - 但 56/93 GT notes 仍断言“prompt_inputs 是旧版/不匹配/需重建”，且 `review.notes_disclosure`（文档说用来标注“已被取代”）**在所有 GT 里都不存在**（仅 1 个 GT 文本提到该字段名）。→ 旧断言既过时又无标记。
- **历史 release/数据集漂移**：见 B-03；release 与数据集是两代文本，且所有 manifest `source_scenario_git_rev` 为 dirty/uncommitted（87 dirty + 6 uncommitted），没有干净 commit 可复现。

### 2.3 prompt_inputs 一致性（对应任务 3，脚本全量 93 case）

脚本：`local/history/audits/audit/2026-10-25/verify_dataset.py`（`P_prompt_inputs` 段）。运行结果（`verify-dataset.json`）：

| 检查 | 通过 | 失败 | 说明 |
|---|---|---|---|
| `prompt_text_path` 存在 | 93/93 | 0 | — |
| `prompt_text_bytes` 与磁盘一致 | **0/93** | 93 | 例：atlas 11,512 vs manifest 11,522；benign-api-doc 42,798 vs 42,791 |
| `prompt_text_sha256` 与磁盘一致 | **0/93** | 93 | 项目自带 `refresh_prompt_input_hashes.py --check` 输出 323 hash mismatches |
| `bundle_json_path` 存在 | 93/93 | 0 | — |
| manifest ↔ bundle 字段一致 | 93/93 | 0 | 两者 `prompt_text_sha256` 完全相同（93/93），即“manifest 与 bundle 一致，唯独和 prompt.txt 不一致” |
| bundle `fixture_file_count` = 实际数 | 93/93 | 0 | `malicious-feedback`=15，与任务里 87 的旧说法不同 |
| `fixture_text_bytes` 和 = manifest | 93/93 | 0 | — |
| bundle fixture sha = 当前 fixture 文件 | 24/93 case | **69/93 case、117 文件** | rename 后 fixture 已改，bundle 记录还是旧文本 |
| `source_scenario_sha256` = 当前场景 JSON | 74/93 | 19 | 场景工作副本与 manifest 漂移 |
| bundle/manifest 的 `source_scenario_git_rev` | — | 87 dirty + 6 uncommitted | 无干净提交 |

**`sanitized_no_ground_truth_leak` 可信度（抽查全量）**：
- **GT 标签泄漏：未发现**。扫描 `ground_truth`/`expected_behaviors`/`boundary_type`/`min_decision`/`should_alert`/GT schema 等，只有 1 个良性 prompt（`benign-multi-incident-response`）含自然语言短语 “the logs are the ground truth”，不是标签泄漏。所以 `no_ground_truth_leak_guarantee=true` 在“标签语义”意义上成立。
- **但 manifest 自己的 sanitization 声明过度**：`sanitization.excluded_artifacts` 声称排除 “scenario identifiers”，实际 34/93 prompt.txt 含 `scenario_id`（多为 fixture 载荷里的 `/tmp/<prefix>-<scenario_id>/` 路径）；67/93 含 `RUN_TAG`/`run-hooks.log` 等 harness 标记（fixture 钩子原文）。这会让 prompt-level 基线看到评测框架/场景名，论文写“sanitized”时应限定为“无 GT 标签”，不要写“无场景标识”。
- 另外：数据集 prompt 是 2026-10-25 的 “Acme Observability” 重命名版；release 里 93/93 仍是 `MtAgentLens`/`mtal`。两套 prompt 不同，见 B-03。

### 2.4 标注完整性（对应任务 4）

- **顶层字段 93/93 完整**：`schema`(v4)、`scenario_id`、`category`、`label`、`attack_type`、`task_summary`、`payload`、`benign_activity`、`expected_behaviors`、`min_decision`、`should_alert`、`execution_check`、`annotation`、`quality` 齐全。
- **行为 243 条**（攻击场景 1–4 条/场景；良性 26 个 `expected_behaviors=[]` 且都有 `benign_activity`）。每条行为 11 个字段（behavior_id/description/attack_pattern/boundary_type/severity/should_alert/min_agents_involved/expected_commands/expected_resources/evidence_requirements/rationale）**0 缺失**；`behavior_id` 无重复；`attack_pattern`/`boundary_type`/`severity` 均合法。
- **分布**：
  - `min_decision`: Safe 26（全部良性）、Unsafe 61、Warning 6（全部攻击：prompt-injection-direct、network-anomaly、recon-user-enumeration、atlas-recon-discovery、resource-direct、resource-disk-fill）；
  - `should_alert`: false 26（良性）/ true 67（攻击）——与 label 完全一致；
  - `evidence_requirements` 类型不统一：list 142 / string 101（C-02）。
- **根因/入口/链字段：缺**（C-01）。93 个 GT 中：
  - `root_cause*`/`root_actor*`：0；
  - `chain` 顶层字段：0；
  - `payload.entry_point(s)`：2（`handoff-unicode`、`context-multihop`）；
  - `payload.delivery_chain`：3；`execution_check.process_chain`：1；
  - 有任一根因类字段的文件合计 7/93。
  - 23 个 `multi_agent_attack` 中 attack_type：C=9、B=14；**9 个 Type C 中 7 个完全没有任何入口/根因字段**。审稿意见要求“23 个 Type C 的根因/入口 GT”，当前数据连“23 个 C”都不成立（只有 9 个），更谈不上根因。
- **E7 困难良性**（C-03）：`env/scenarios/_drafts/e7-difficult-benign/spec.json` 状态 `spec_only`，4 类（大量委派/敏感路径正常访问/正常外部网络/长任务高 PBC）× 5 = 20 个计划场景；但 **0 个场景 JSON、0 fixtures、0 GT、0 执行、0 FPR**。`env/tests/test_e7_difficult_benign_spec.py` 只有 2 条结构测试（断言只有 spec、数量/唯一性/字段），实测 2 passed——通过只证明“没有伪造场景”，不证明 E7 已做。

### 2.5 release 一致性（对应任务 5）

**release 本体是可复现的（未改动）**：
- `release.json`：2 个键之外共 1590 个文件哈希，脚本重算 **0 mismatch、0 unmanifested**；`release_id` 由 manifest 正文（去掉 release_id 后 `json.dumps(sort_keys=True)` 的 sha256）重算 = `aa364a45cbfdc3aab00679e52dace81410f9073a70f482382b1ddab9550db542`，与声明一致。
- release 的 `prompt_inputs/manifest.json`：93/93 prompt sha 与 release prompt.txt 一致；1048/1048 fixture sha 与 release fixtures 一致。→ **fix4 输入自洽**。
- release 包含 `ground_truth/`（93）、`prompt_inputs/`、`metadata.json`、`quality/manifest.json`、`fixtures/`、`scenarios/`、`bench/`；`publication_blockers=[]`、`paper_metrics_available=true`。
- release 是 **pre-blinding**：93/93 prompt 仍含 `MtAgentLens`/`mtal`；68/93 含 sink host `93.184.216.34`（`release.json.sink_host_transform`，171 个文件被重写）。

**release ≠ 发布数据集**：
- 共享路径 377，内容不同 374：`ground_truth` 186（93 GT + 93 reviewers）、`prompt_inputs` 187（93 prompt + 93 bundle + manifest）、`metadata.json` 1；
- release 独有 1214（fixtures 1051、scenarios 93、bench 68）；dataset 独有 391。
- 结论：论文若写“FuxiBench v1.4.0 → RC fix4”，必须写明 fix4 用的是 release2（pre-blinding），发布数据集是事后重命名版；两者不能互相代替。

**消费者核验**：
- `python3 scripts/evaluation/annotation_release_consumer.py --root local/workpackages/2026-10-24-pilot-codebook-v1/release` → **VERIFIED for external consumption**（cases=2 annotations=6 paper_metrics_available=False，release_id `e05c0ed2…`）；`python3 -m tools.annotator release-verify --root …` 同结果。→ 该 consumer 对 **annotation release** 真实可用。
- 同一个 consumer 对 **FuxiBench release** → **14 problems**（schema 不符、counts/cases/splits/attestations 缺失，且 “paper_metrics_available must be false while there are no human annotations”）。→ 不能用“VERIFIED”字样给 FuxiBench 背书；FuxiBench release 的 `paper_metrics_available=true` 本身与“无人工标注”冲突（D-01）。

### 2.6 人工标注可行性（对应任务 6）

**工具链实测可用**（只读/最小副作用）：
- `python3 -m tools.annotator --help`：15 个子命令（build/import-runner/register/save/export/serve/audit/attest/attestations/task-evidence/propose/pilot/migrate-codebook/release/release-verify）。
- `audit --store …/records.sqlite` → 2/2 ready、0 blocker、`human_annotations:0`、`attestations:0`、`paper_metrics_available:false`（与 `readiness.json` 一致）。
- `serve --mode studio --port 8799` 冒烟：HTTP 200、93,407 B 工作台页面；`env/.venv/bin/python -m pytest tools/annotator/tests/{test_studio_ux,test_workbench,test_audit}.py` → **30 passed**；`test_annotator.py`（stdlib）→ **16 passed**。
- `scripts/evaluation/annotation_agreement.py --export … --raters 刘周康 唐浩钺 刘锦样`：`per_rater` 全 0、`ignored_model_proposals={lzk-agent:2, thy-agent:2, ljy-agent:2}`、各字段 κ=null → **正确拒绝拿 model_proposal 当人工 κ**。
- `scripts/evaluation/agent_agreement.py`：显式输出 `agreement_kind=agent`，并声明不得当人类一致性引用。
- 结论：**工具/协议可用，缺的是真人数据与样本量**。2 个良性任务 < PILOT-CODEBOOK §8.3 的 ≥15，且 2 个任务里 `attempted/executed/succeeded/roots` 很难出现 ≥2 个类别（良性任务多为 no/空根因），所以即使拉 3 个作者来标，也**算不出**可发表的 κ（会得到 `not_evaluable`，不是 pass）。

**给 3 位作者做独立标注的最小操作步骤（不改工具）**：
1. 选证据包：从 fix4 release 的 run 中挑 **≥15（建议 30）个** 有完整 `runner/task_brief/trajectory/collector` 证据的 run，攻击+良性都要有，覆盖 4 个判定字段的多个类别。
2. 冻结/导入：`python3 -m tools.annotator import-runner --ledger <run>/runner-events.jsonl --release <release> --codebook-version pilot/1 --out <bundle>`（或 `pilot` 一次准备）；`register` 入队。
3. 就绪审计：`python3 -m tools.annotator audit --store records.sqlite --codebook-version pilot/1 --out readiness.json`；要求 `blocked_tasks=0`。
4. 盲化：确认工作台只显示脱敏任务简报/证据，不显示场景目录、GT、release manifest、检测器输出、模型草稿（`PILOT-CODEBOOK §9`）。
5. 三人各自启动独立实例（同一 store，不同端口/rater，互不可见）：`python3 -m tools.annotator serve --store records.sqlite --rater 刘周康 --method human --mode annotate --codebook-version pilot/1 --port 8801`（其余两人 8802/8803）；`save` append-only。
6. 导出三份：`python3 -m tools.annotator export --store records.sqlite --out annotations.json`；`scripts/evaluation/annotation_agreement.py --export annotations.json --raters 刘周康 唐浩钺 刘锦样 --out agreement.json`，看 `attempted/executed/succeeded/roots` 每个字段 κ；<0.60 先改 codebook 重标，不放大。
7. 第四方裁决：`serve --mode adjudicate --reviewer <第四人>`，仅引用三份当前修订，按字段给理由+证据；裁决 `method=human`，不能由被裁决者本人执行。
8. 发布：`tools.annotator release` → `release-verify` → `annotation_release_consumer.py`；如需真人声明，用 `attest` 记录 `reviewer_type=human_statement`（声明≠标签），**不得代签**。

---

## 3. 哪些可以用 AI 合法完成 vs 哪些必须真人

**可以合法用 AI（但仍要标注来源，不得改写成 human）**
- 场景级 GT 草案：`expected_behaviors`、`expected_commands`、`expected_resources`、`evidence_requirements`、`execution_check`（当前 93 份 GT 实际就是 AI reviewer agents 做的）。
- 规则化裁决：把 74 项分歧按 ADJUDICATION-PROTOCOL 规则关闭，写 `review.status=resolved`、`type_change`、`evidence`。
- agent 间一致性：`_agreement.json` 的 Fleiss κ/Jaccard 可以作为 **agent agreement** 报告（必须写 `agreement_kind=agent`）。
- prompt_inputs 生成、脱敏、哈希、自检（只要重建后 `--check` 归零）。
- 证据充分性审计、release 冻结/校验、consumer 验证。
- 以 AI 起草根因/入口初稿（但论文中要写“AI 草案 + 人工确认”）。

**必须真人才能完成（不能由 AI/agent 代替，也不能事后补签）**
- 任何 `three independent human reviewers` / “3 位安全研究人员” / “平均 N 年经验”的表述。
- 运行级正式标签：`method=human` 的 `attempted/executed/succeeded/roots`，以及由此计算的**人类** κ。
- 第四方裁决的真人签署 / `review_attestations` 的具名声明（`cryptographic:false` 也要真人）。
- GT 的领域专家整体背书，尤其是 9 个 Type C 的根因/入口判定与 61 个 `min_decision=Unsafe` 的严重度确认。
- E7 困难良性的“良性”判定（需要有真实任务意图的作者确认）。

---

## 4. 把 FuxiBench 元数据改成如实表述（只给 diff 建议，不改文件）

### 4.1 `datasets/FuxiBench/metadata.json`

```diff
-  "version": "1.4.0",
+  "version": "1.4.1",
@@
   "quality": "independent_reviewers_codebook_v4",
   "annotation": {
-    "annotators": ["刘周康", "唐浩钺", "刘锦样"],
-    "method": "three independent reviewers under ANNOTATION-CODEBOOK v4; adjudicator merges by majority/evidence; agreement computed from raw reviewer JSON",
+    "reviewer_kind": "ai_agent",
+    "human_annotation": false,
+    "annotators": ["刘周康", "唐浩钺", "刘锦样"],
+    "annotator_note": "these three names are labels of three independent AI reviewer agents; they are not human annotators (see PROVENANCE.md §2)",
+    "method": "three independent AI reviewer agents under ANNOTATION-CODEBOOK v4; rule-based AI adjudication per ADJUDICATION-PROTOCOL v1; agreement computed from raw reviewer JSON; NOT human annotation",
@@
-    "review": "quality.review per scenario (status/method/rule/evidence/limitations); needs_human_review removed on 2026-10-22"
+    "review": "quality.review per scenario (status/method/rule/evidence/limitations); needs_human_review renamed to legacy_needs_human_review on 2026-10-22 and NOT cleared: 74 scenarios were adjudicated by rules, none was reviewed by a human",
+    "human_review_performed": false,
+    "legacy_needs_human_review_count": 74,
+    "reviewer_agent_count": 3,
+    "agent_agreement": "Fleiss kappa(label)=0.9823, kappa(attack_type)=0.8754; this is agent agreement, not human kappa"
@@
   "adjudication": {
@@
-    "human_review": "waived_by_author"
+    "human_review": "waived_by_author",
+    "human_review_status": "not_performed",
+    "human_review_note": "author waived per-item human review; adjudication was rule-based and AI-executed; no human sign-off exists; waived != passed"
   },
-  "dataset_updated": "2026-10-22"
+  "dataset_updated": "2026-10-25",
+  "dataset_git_rev": "<clean commit sha used to build prompt_inputs>",
+  "known_limitations": [
+    "no human annotation at scenario level and no human kappa",
+    "no root_cause / entry_point / delegation chain GT for the 9 Type-C and 23 multi-agent scenarios",
+    "prompt_inputs rebuilt on 2026-10-25; verify refresh_prompt_input_hashes.py --check == 0 before release"
+  ]
 }
```

changelog 追加一条：

```text
"1.4.1 (2026-10-25): metadata corrected to state that the three reviewers are AI agents, not humans; added legacy_needs_human_review_count=74 and human_review_performed=false; rebuilt prompt_inputs (hash gate pending); no label semantics changed."
```

同时把 `changelog` 里 1.3.0/1.4.0 的 “three independent reviewers” 改成 “three independent AI reviewer agents（责任标签：刘周康/唐浩钺/刘锦样）”，不要删旧条目。

### 4.2 `ground_truth/_summary.json`（不要报 0）

```diff
-  "needs_human_review": [],
-  "needs_human_review_count": 0,
-  "ai_adjudicated_count": 74,
+  "needs_human_review": [],
+  "needs_human_review_count": 0,
+  "needs_human_review_field": "removed; use legacy_needs_human_review",
+  "legacy_needs_human_review_count": 74,
+  "human_review_performed": false,
+  "ai_adjudicated_count": 74,
```

### 4.3 `ground_truth/_agreement.json`（本来诚实，补字段锚定 agent）

```diff
   "note": "AI reviewer agents; not human annotation. ...",
+  "agreement_kind": "agent",
+  "human_annotation": false,
+  "annotators_are_agents": true,
```

### 4.4 `ground_truth/_validation.json`

```diff
-  "problems": []
+  "problems": [],
+  "checks": {
+    "human_provenance": false,
+    "prompt_inputs_hash_gate": false,
+    "root_cause_fields": false
+  },
+  "notes": "field-level validation only; it does not verify human provenance, prompt hash consistency, or root-cause completeness"
```

### 4.5 `PROVENANCE.md`（改 2 处）

- §2 “其中关于旧 prompt 包的陈述由 `review.notes_disclosure` 标注为已被取代” → 二选一：实现 `review.notes_disclosure`（对 56 条旧 prompt 断言打标），或删掉该句并写“旧 notes 作为 2026-10-20 历史记录保留，未逐条标注取代关系”。
- §2 第 1 段后补一句：“本节是 metadata.json/README/changelog 冲突时的唯一权威；若它们与此处不一致，以本节为准。”

### 4.6 每个 GT 的 `annotation` 块（93 个文件，机械改）

```diff
-  "annotators": ["刘周康", "唐浩钺", "刘锦样"],
-  "method": "three independent reviewers under ANNOTATION-CODEBOOK v4; adjudicator merges by majority/evidence",
+  "reviewer_kind": "ai_agent",
+  "human_annotation": false,
+  "annotators": ["刘周康", "唐浩钺", "刘锦样"],
+  "method": "three independent AI reviewer agents under ANNOTATION-CODEBOOK v4; rule-based AI adjudication (ADJUDICATION-PROTOCOL v1); not human annotation",
```

### 4.7 发布前必须一起跑的哈希/版本门禁（否则 metadata 改了也白改）

```bash
python3 compare/scripts/build/refresh_prompt_input_hashes.py --check   # 现在 323 drift，必须 0
python3 scripts/evaluation/validate_gt.py --dataset datasets/FuxiBench
python3 scripts/evaluation/gt_summary.py  --dataset datasets/FuxiBench
python3 scripts/evaluation/gt_agreement.py --reviewers datasets/FuxiBench/ground_truth/_reviewers \
    --out /tmp/agreement-check.json   # 与 _agreement.json 对比 κ
python3 -m tools.annotator release-verify --root local/workpackages/2026-10-24-pilot-codebook-v1/release
python3 scripts/evaluation/annotation_release_consumer.py --root local/workpackages/2026-10-24-pilot-codebook-v1/release
```

---

## 5. 复现方式（只读）

```bash
cd /home/lzk/MtAgentLens
python3 local/history/audits/audit/2026-10-25/verify_dataset.py --json local/history/audits/audit/2026-10-25/verify-dataset.json
# 结果：fail=9 warn=5（问题表 A-01…E-01 的机器可读版）

python3 compare/scripts/build/refresh_prompt_input_hashes.py --check   # 323 drift
python3 scripts/evaluation/gt_agreement.py …                        # κ 重算
python3 -m tools.annotator audit --store local/workpackages/2026-10-24-pilot-codebook-v1/records.sqlite
python3 -m tools.annotator release-verify --root local/workpackages/2026-10-24-pilot-codebook-v1/release
python3 scripts/evaluation/annotation_release_consumer.py --root local/workpackages/2026-10-24-pilot-codebook-v1/release
```

脚本会写 `verify-dataset.json`（在 `local/history/audits/audit/2026-10-25/` 内，不进仓库、不改数据集/release）。

---

## 6. 未解决的问题 / 需要 lead 决策

1. **A-01/A-03 的最终口径**：按当前数据，论文只能写“3 个独立 AI reviewer agents + AI 规则裁决，agent κ=…；无人类 κ”。如果作者坚持“就是三个人”，需要补真人 attestation/时间记录，否则我不建议这样写（触红线）。
2. **B-02 谁来重建 prompt_inputs**：需要有人跑 `build_prompt_input_bundle.py` 全量重建并让 `refresh_prompt_input_hashes.py --check` 归零；这是发布阻断项。我没有跑 `--write`（会改数据集）。
3. **C-01 根因 GT 补法**：用现有 `_reviewers` 文本可起草 9 个 Type C 的 root/entry/chain，但必须明确“AI 草案 + 人工确认”；不能直接写成人工 GT。
4. **B-03 发布口径**：数据集（Acme，post-blinding）与 fix4（release2，pre-blinding）不同；要么重跑 campaign，要么在论文中显式说明这是两个输入版本。

---

## 7. 副作用披露

- 为验证 studio 可用性，我执行过一次 `serve --mode studio --port 8799` 冒烟（HTTP 200，随后 timeout 退出）。该命令打开了 `local/workpackages/2026-10-24-pilot-codebook-v1/records.sqlite` 并写入 SQLite 头（文件 sha256 `17e3ea5c…` → `c5e44868…`，字节数 1,732,608 不变）；逻辑内容未变（只读查询：`bundle_revisions=6`、`bundle_tasks=2`、`review_attestations=0`，与 release/counts 一致）。**没有修改 `compare/Dataset/`、release 目录或任何 GT。** 若要求该 pilot store 字节级不变，应从 git/本地备份恢复或重跑 pilot 生成。
- `verify_dataset.py` 只读数据集与 release；输出写在 `local/history/audits/audit/2026-10-25/`。
