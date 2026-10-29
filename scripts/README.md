# scripts/

| 路径 | 用途 | 何时运行 |
|------|------|----------|
| `verify.sh` | **唯一离线门禁**，预算只降；具体检查以脚本为准 | 每次改动；提交前 |
| `audit_deps.py` + `audit-budget.json` | 依赖审计（cargo/npm，需网络） | 发布前；预算只降 |
| `check_fresh_clone.sh` | 隔离检出候选工作树，或 `--commit REF` 固定提交及其子模块；重新安装依赖并跑门禁 | 改构建/嵌入/子模块后 |
| `repro.sh` | 按锁文件安装前端依赖，构建并验证候选产物；失败即停止，输出版本与哈希清单 | 本地复现；严格发布隔离检查仍需单独完成 |
| `release.sh` | 二进制 + 文档 tarball + SHA-256（可选 GPG） | 发布 |
| `decrypt_trace.sh` | 用 `tshark` + `run_dir/tls.keys` 解密 `collector/trace.pcap`，输出每个 TLS 流的明文 | 调试 SSL 明文；需 `tshark` |
| `e2e/` | 真实 E2E：`record-smoke`、`snapshots-contract`、`openclaw-outofband` | 需要 sudo |
| `wire/` | 原始 wire 语料、FPR/开销基线、OpenClaw 交叉校验 | 采集/复测基线 |
| `spikes/` | 有复测用途的探针实验（网络 E2E、噪声、P3b 覆盖） | 按需；先检查适用版本 |
| `schema/` | legacy DB 抽样与 schema 采纳 | 迁移审计 |
| `evaluation/` | 二进制裁决 BCa/Wilson 复现、独立标注一致性 | 生成评估产物 |
| `evaluation/freeze_rc.py` | 冻结 release candidate 身份：binary/eBPF helper/dataset/工具链 hash + git 脏状态 + 门禁日志 hash；输入缺失即报错，记录只写一次 | 跑正式实验前 |
| `evaluation/enrich_campaign_results.py` | 为已测量的 campaign 行补上权威 `capture_quality_status`/`evaluation_status`（只写派生账本，读不到则 unknown） | 汇总前 |
| `tests/` | 审计门的契约测试 | 由 `verify.sh` 第 14 门运行 |

所有命令从仓库根运行。真实内核采集需要权限；浏览器检查需要浏览器依赖；联网依赖审计在发布前独立运行，当前没有 CI audit stage。

规则：新增脚本必须有当前调用者或文档入口，并明确输入、输出、适用版本和失败返回码。先扩展已有入口；一次性分析放本地 `local/`，需要持续复测的实验才进入 `spikes/`。与已删除资产绑定的脚本应随消费者一起退役。

`check_fresh_clone.sh` 默认包含一方未跟踪但非忽略的源文件；子模块只检出本地
Git 对象，忽略构建产物不进入副本，有 tracked 修改则拒绝丢弃。固定提交模式
使用该提交的 gitlink，保留主仓库与子模块的 Git 身份，不采纳脏工作树。
源码重建不联网；`npm ci` 和临时 Python venv 的依赖安装可能使用软件源。
Python 依赖与系统工具尚未完全锁定，因此该检查不声称位级可复现。
