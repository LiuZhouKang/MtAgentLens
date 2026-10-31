# 开发与维护

先阅读[系统架构](../docs/architecture.md)和[架构决策](../docs/adr/README.md)。
当前文档导航见 [docs/README.md](../docs/README.md)，实验状态见[当前证据](../docs/evaluation/CURRENT-EVIDENCE.md)。

## 环境

需要 Rust 1.85+、clang/LLVM、libelf、zlib、OpenSSL、zstd 开发库、
Node.js ≥22.13.0、npm 和 Python 3.10+（含 venv）。在仓库根目录执行：

```bash
git submodule update --init --recursive
python3 -m venv env/.venv
env/.venv/bin/python -m pip install -e './env[dev]' -r compare/requirements-test.txt
make build
```

`libbpf/`、`bpftool/` 是子模块。Rust 产物位于 `collector/build/`；
eBPF 单程序构建使用 `make .output/bin/<name>`。
更多操作见[维护脚本](#维护脚本)与各模块 README。

## 修改与验证

1. 按现有设计做最小完整修改；功能修复先复现问题并补聚焦回归测试。
2. 先运行受影响模块的检查；提交前运行完整离线门禁：

   ```bash
   ./scripts/verify.sh
   ```

3. 修复失败根因，不通过忽略测试、放宽 lint 或提高预算换取通过。
4. 修改 eBPF 时真实加载验证 verifier，并记录内核与权限条件。
   数据库改动先读[单一 schema 决策](../docs/adr/0005-single-schema.md)，
   对外来数据库先只读分类，不能直接执行 DDL。

完整门禁需要本地实验资料；仅源码检出不包含所有大体积数据库。
缺少数据时按失败项与[复现手册](../docs/evaluation/REPRODUCE.md)补齐相应输入，
不要伪造记录或重新生成历史证据。普通 FuxiBench 目录无需发布标识或全包校验清单。

发布前另运行网络依赖审计：

```bash
python3 scripts/audit_deps.py all
```

审计例外集中在 `scripts/audit-budget.json`，必须有理由并随修复移除。
修改构建、嵌入资源或子模块后，使用 `./scripts/check_fresh_clone.sh` 检查隔离副本；
该检查需要软件源或缓存，不等同于位级可复现。

## 文档与数据

- 优先更新已有主题文档，不新增一轮一份的计划、交接或“最终报告”。
- 用户入门看根 README，模块操作看模块 README，设计取舍记 ADR。
  架构或行为变化同步相关 README、架构文档、能力清单与 ADR。
- [场景贡献](../env/README.md#场景贡献)说明新场景开发；FuxiBench 当前标签、
  标注和来源规则在[数据集目录](../datasets/FuxiBench/README.md)维护。
- 正式结果放在 `compare/results/<实验>/`；运行现场和本机备份按 `local/README.md` 组织。
  不提交数据库、运行日志、模型缓存、构建产物或真实凭据。
- 场景 fixture、盲标源文件和冻结实验记录属于实验输入或证据，不能作为重复文档改写。
- `compare/docs/` 是交付文档副本，按其 `SOURCE-NOTES.md` 同步；迁移文件时修复引用和调用。
- 手写文档优先中文，代码标识符和简短代码注释使用英文。

## 提交与安全

提交保持单一目的，说明修改原因与验证结果；常规提交使用 Conventional Commits。
安全问题按 [威胁模型与安全报告](../docs/security/threat-model.md) 报告；测试凭据必须使用合成值。
新增捕获数据外发路径时，同步[威胁模型](../docs/security/threat-model.md)中的字段和外发边界。

项目代码采用 [Apache-2.0](../LICENSE-APACHE) 或 [MIT](../LICENSE-MIT) 双许可；
数据、文档与第三方代码的适用范围见 [README 许可证说明](../README.md#许可证)。
保留原有版权、SPDX 和第三方许可文件。

## 维护脚本

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
