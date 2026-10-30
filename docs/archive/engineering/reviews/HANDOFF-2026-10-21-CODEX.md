# MtAgentLens 当前工程交接（2026-10-21）

## 范围与事实边界

这是 HEAD `610f726` 上当前未提交工作树的修复验收，包含用户原有大量 staged/unstaged/untracked 改动。没有提交、推送、改写历史或执行正式付费实验。此前交接中的旧实验数字全部作废。

修复由独立采集、核心、评估工作包与主审交叉检查。原始审计见 [25组问题](INDEPENDENT-AUDIT-2026-10-21-CODEX.md)，该报告描述的是修复前状态。当前不能据此宣称零未知缺陷、完成权威人工GT或已证实科研领先。

## 修复对应表

| 原审计项 | 当前实现与验收契约 |
|---|---|
| 01 来源缺失 | 从原始会话恢复缺失记录，保存原文与摘要；三份意见/最终GT一致性严格验证；不生成意见、不伪造人类参与 |
| 02 预期行为丢失 | 显式支持v4 list与旧schema；243条行为保留，不接受有输入却零预期的静默结果 |
| 03 数据版本混合 | 不可变release冻结scenario、fixture、GT、prompt、setup/sink依赖；运行绑定二进制与DB hash |
| 04 重复运行分母 | run唯一键关联资格；只有明确True合格，拒绝与未知单列；独立marker提供执行证明 |
| 05 中间告警冒充结果 | canonical指定run最终裁决；无结果、Uncertain及质量不足独立处理，不以空告警冒充安全 |
| 06 盲标泄露 | API事实白名单、隐藏模型结论/他人记录、DB摘要绑定不可覆盖修订 |
| 07–09 采集丢事实 | 真实ppid、操作/flags/进程生命周期区分聚合、取消安全路径盲过滤；真实fork无exec与敏感路径验证 |
| 10 完成态 | syscall enter/exit保留ret；failed/pending/unknown不生成完成事实边；FileOpen不证明内容读取或写入 |
| 11 关闭质量 | supervisor TERM→持续排空→截止/失败显式；缺最终统计、损失、解析错误、强杀和持久化失败不能权威Safe |
| 12 健康累计 | 真实trace路径、按source合并累计计数、避免覆盖/重复；PBC不再作为采集健康条件 |
| 13 图模式 | 按连通分量匹配，互不相关的正常Agent不能抹去攻击模式 |
| 14 观察者阻塞 | 外部请求总超时与响应体上限，输入事实作为不可信文本 |
| 15 身份合并 | 委派、污点、权限包络等引用随canonical身份同步归并 |
| 16 审核范围 | 候选ID/版本/精确证据绑定，旧Safe不覆盖新增候选；空scope或虚构引用的Unsafe不获得权威性 |
| 17 live/replay | batch纳入完整证据；live/replay调用相同纯分析核；完整配置/输入/源码依赖指纹；缺checkpoint拒绝同构声称 |
| 18 行为基线 | 按真实Agent和新增动作增量训练；不反复归一化；当前周期更新进入融合 |
| 19 run唯一键 | schema v4复合主外键/身份索引；v1–v3迁移备份；失败ROLLBACK、恢复pragma、外键验证 |
| 20 API选择run | graph/evidence/alerts/snapshots统一选择；多run歧义或不存在拒绝；无快照不借用其他run |
| 21 数据库下载 | WAL一致backup、冻结后选run、删除其他run与多run无归属行、secure_delete+VACUUM、不泄漏空闲页 |
| 22 并发切源 | generation守卫、迟到init/API/DB不覆盖新选择；切源清理旧图、上下文、帧与rAF告警 |
| 23 直播首载 | live状态跨await保留，统计按新run重置，档案查看不被周期直播覆盖 |
| 24 调查契约 | lean告警仍可聚焦；只高亮真实证据链；原生camera reset替代死EngineAPI；本地多run明确选择 |
| 25 文档/门禁 | README剥离旧数字；能力清单删除假接线主张；统一verify纳入比较、env和GT一致性；完整生成目录仍未实现 |

## 邻近修复

- 官方LLM Guard、Meta Prompt Guard、Vigil适配器不再由项目正则冒名替代。真实依赖/模型/服务缺失即FAILED；未运行模型。详见 [baseline来源与边界](../../../../datasets/FuxiBench/evidence/legacy/docs/BASELINE-ENGINES.md)。
- record/trace健康接口接入真实terminal计数和实测处理均值；未测stage、p99和队列延迟保持未知；raw子命令尚无相同终端计数时明确不可用。
- 本地DB导入先选run再解析，输入原字节不修改；直播帧去重、更新、排序、200帧容量满后继续推进。
- 开发服务器不再杀死占用端口的其他进程，Playwright端口可独立指定。
- 网络审计发现并升级两项证书依赖漏洞，未放宽预算；上游 [RUSTSEC-2026-0382](https://rustsec.org/advisories/RUSTSEC-2026-0382.html)、[RUSTSEC-2026-0381](https://rustsec.org/advisories/RUSTSEC-2026-0381.html)。

## 验证结果

| 验证 | 最终实测结果 |
|---|---|
| `./scripts/verify.sh` | **17/17通过**，`/tmp/mtagentlens-remediation-verify-final2.log` |
| Rust全target | **1038 passed / 0 failed / 3 ignored**，28个测试target（含doc）；lib895通过；`/tmp/verify_rust_tests.log` |
| Clippy | 104 warnings，预算由167收紧为106；不是零lint债务 |
| 前端 | lint/type-check通过；87文件、**539单测通过**；生产静态构建通过 |
| Python比较/盲标 | **297 passed**，`/tmp/verify_comparison.log`；含Unknown/Uncertain质量分母和精确claim归因 |
| env | **134 passed**，`/tmp/verify_environment.log`，无skip/xfail |
| C/helper | 13个C测试二进制通过；真实Linux7.0无害fixture七项事实通过，**dropped_events=9**如实降级 |
| 浏览器 | Chromium8、Firefox8、WebKit8，合计**24通过**；首次WebKit缺libavif16，安装系统运行库后8项全部重跑通过 |
| 数据输入 | GT93/93（67攻击+26良性），74待人审；scenario integrity无问题；实际生成93项不可变release，publication blockers=74 |
| 依赖网络审计 | 0 live Rust漏洞、0达到失败阈值的npm生产依赖advisory，未增加忽略；一个未维护依赖警告仍保留 |
| 最终release | `cargo build --release --bin mtagentlens --bin replay`退出0；编译包含两项证书依赖修复 |
| 实际新binary冒烟 | 仅system数据源、无模型/内核攻击；`/live`、真实terminal就绪/健康、在线DB下载446464字节且SQLite integrity=ok、唯一run、SIGINT退出0 |
| 新release回放 | 实际对上述新DB执行`replay --sqlite`，mode=reauthoritative/status=ok；这次无行为输入，不作为检测效果样本 |
| 工作树检查 | `git diff --check`、Rust↔TS契约通过；未提交 |

新 `collector/build/release/mtagentlens` SHA256：
`4f9429efd51c671d638b49a80d9b813f26a073c7138cb0c1fdd632fb2554f354`。
`collector/build/release/replay` SHA256：
`583769ec236af8ab85f4948b655dd5c8732d1167119c3d762e99fd1bdd519925`。

实际release进程证据：`/tmp/mtagentlens-release-smoke-0i5tq4vz/summary.json`。
此无害system-only run最终为Uncertain/Warning，因为未配置OS行为采集源；
不能将就绪或单次零terminal错误写成完整行为观测或权威Safe。
冻结分析实现指纹为`8429fc740721b48814e8528fad5604125fa5a2fc3b43a2183ccb66fa4b7b1801`。

临时输入release：`/tmp/mtagentlens-input-release-l5fovfqv/release`，
release_id=`3e1829c1c17a82b7b69f472b657b2b0da2d7ffb4a00a1b0a6c6ae6e63299571d`，
**paper_metrics_available=false**。该输入快照不是新实验结果，不提交运行产物。

更详细的 [采集证据](CAPTURE-VERIFICATION-2026-10-21-CODEX.md)、
[环境证据](ENV-OFFLINE-VERIFICATION-2026-10-21-CODEX.md)、
[评估修复](EVALUATION-REMEDIATION-2026-10-21-CODEX.md) 保留独立工作包的验证过程。
三个ignored分别要求live OpenClaw、真实SSL二进制/权限、真实process二进制/权限；
本轮真实helper验证不能替代未执行的全部集成环境。

## 科研与发布仍需的证据

1. 74项标记的人类复核必须真实完成，原始意见与裁决可追溯；固定姓名不能当人类签名。
2. 在冻结release与最终二进制上重新采集完整效果、消融和性能分布；报告执行、拒绝、未知、采集质量分母。历史数字不能迁移。
3. 官方baseline依赖和权重实际部署后的复现、模型输出与阈值验证，本轮只有离线接口/失败契约测试。
4. 跨内核、长时间、高负载和更广Agent运行时验证；当前实测边界如上。
5. 104条Clippy警告及一个未维护依赖告警仍是公开工程债务；完整自动生成能力/类型目录仍为后续工程项。现有源契约覆盖部分生产者、模板、枚举和字段，不宣称全仓库双向完备证明。

## 恢复命令

```bash
./scripts/verify.sh
python3 scripts/audit_deps.py all
cd frontend && BASE_URL=http://127.0.0.1:7396 npx playwright test e2e/live.spec.ts e2e/compatibility-smoke.spec.ts e2e/run-isolation.spec.ts --project=chromium --workers=1
```

所有Cargo操作从collector目录通过`flock /tmp/mtagentlens-cargo.lock`串行；不要回滚当前脏工作树，也不要把运行日志/DB/新实验产物提交成源码。
