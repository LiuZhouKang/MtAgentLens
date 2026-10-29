# 贡献指南

MtAgentLens 是一个在内核层观测 agent 行为的工具。它最贵的缺陷不是「写错了」，
而是「没有任何机器能断言它是对的」——所以贡献流程围绕**可证伪**设计。

## 先读

1. [系统架构](docs/architecture.md)：模块职责、数据流与边界。
2. [架构决策](docs/adr/README.md)：已有约束与取舍。
3. [当前实验与论文证据](docs/evaluation/CURRENT-EVIDENCE.md)与[工程维护](docs/superpowers/README.md)：分别查看实验状态与开发记录。

公共贡献流程不依赖本机专用的助手配置或未纳入版本控制的文件。

## 环境

```bash
# 依赖：Rust 1.85+、clang/llvm、libelf-dev、zlib1g-dev、libssl-dev、
#       libzstd-dev、Node.js ≥22.13.0 / npm、Python 3.10+（含 venv）
git clone --recurse-submodules <repo>   # 或之后 git submodule update --init --recursive
python3 -m venv env/.venv
env/.venv/bin/python -m pip install -e './env[dev]' -r compare/requirements-test.txt
make build          # 前端 + eBPF + Rust release
```

`libbpf/` 与 `bpftool/` 是 git submodule，不是普通目录；没有它们 eBPF 无法构建。

Rust 产物在 `collector/build/`（`.cargo/config.toml` 设了 `target-dir`），
不是 `collector/target/`。eBPF 产物用 `make .output/bin/<name>`，**不要**
`make <name>`（会命中同名目录并假成功）。

## 唯一门禁

```bash
./scripts/verify.sh     # 完整离线门禁；检查项与预算以脚本为准
```

完整门禁也核对冻结 FuxiBench 发布清单与历史 trace DB。Git 不保存大体积数据库；仅克隆源码时，先取得匹配 `release_id` 的完整数据集交付包，保留原清单与校验值，不用重新生成清单代替缺失原件。

任何 PR/提交都必须让它全绿。网络可用的环境还应跑依赖审计
（发布前在构建主机执行同一条命令）：

```bash
python3 scripts/audit_deps.py all   # cargo audit + npm audit（需 RustSec DB / npm registry）
```

被接受的漏洞只能写进 `scripts/audit-budget.json`，且每条必须有理由；
`verify.sh` 会离线校验该文件的完整性，审计消失时条目必须当天删除。

检查候选工作树在隔离副本中的构建情况：

```bash
./scripts/check_fresh_clone.sh                  # 候选源码，排除子模块构建产物
./scripts/check_fresh_clone.sh --commit <ref>   # 固定提交及其 gitlink
```

脚本在临时目录安装依赖、构建前端并运行门禁；需要可用的软件源或缓存。
Python 依赖与系统工具尚未完全锁定，固定源码检查不等于位级可复现。
门禁失败时**修根因**，不要加 `#[allow]`、
不要提高预算、不要 `#[ignore]`——预算只允许下调，理由写在脚本注释里。

## 开发方式

1. **先证据后代码**：结论要有一手材料（DB 行 SQL、原始 wire、内核/verifier、
   构建产物），不接受「我觉得」。
2. **TDD**：先写会失败的测试，再写最小实现；修复必须有回归测试。
3. **每加一条能力，同时加一条能证明它存在的门；每删一条能力，同时把预算降下来。**
4. 数据库/协议/schema 改动先读 `docs/adr/0005-single-schema.md`；
   任何 PRAGMA 之前必须只读分类，禁止对外来库跑 DDL。
5. eBPF 改动必须在 verifier 下真实加载（`sudo`），并说明内核版本影响。

## 提交

- 使用 Conventional Commits：`feat:` / `fix:` / `refactor:` / `docs:` /
  `test:` / `chore:`。
- 一个提交只做一件事；删除与替代分开，便于 `git bisect`。
- 提交信息说明**为什么**，以及用什么证据证明（测试名、门禁、命令输出摘要）。
- 不提交构建产物、运行库、抓包数据；`env/runs/` 与 `*.db` 默认忽略
  （schema fixture 有显式例外）。

## 安全

- 报告漏洞走 `SECURITY.md` 的私密渠道，不要开公开 issue。
- 新增任何把捕获数据送出本机的路径，必须同步更新
  `docs/security/threat-model.md` 的资产/边界表和 `SECURITY.md` 的默认值列表。
- 测试中的凭据必须是合成值；禁止把真实 token 写入 fixture。

## 文档与仓库维护

- 文档用中文，代码/标识符/commit 用英文。
- 架构或行为变化时同步更新：`README.md`、`docs/architecture.md`、
  `docs/capabilities.json`（能力清单的唯一真源）、对应 ADR。
- 先更新已有主题文档。README 负责首次使用，模块 README 负责操作，ADR 记录决策；不为每轮工作新增一份“最终报告”或交接入口。
- 当前导航集中在 `docs/README.md`；实验状态更新到 `docs/evaluation/CURRENT-EVIDENCE.md`，工程记录从 `docs/superpowers/README.md` 进入。被替代的交接退出当前列表，必要的历史证据保留于已有归档。
- `SECURITY.md` 保留漏洞报告和部署安全边界；数据集标注规则与来源记录保留在数据集目录，避免复制到用户入门页面。
- 活动回归测试随代码维护。固定旧实验数字、已撤销投稿包或已删除 fixture 的断言不属于当前产品测试；退役前确认当前机制仍有测试覆盖，不以退役掩盖真实回归。
- 正式实验交付统一放在 `compare/results/<实验>/`；完整运行现场保留在 `local/final-v1/` 与 `env/runs/`。整理前核对引用和原始证据覆盖，不能按文件名、日期或“AI 生成”标签直接删除。
- `compare/docs/` 是实验交付包的文档副本（见 `compare/docs/SOURCE-NOTES.md`），不因内容重复而直接删除；更新源文档时核对交付副本。
- 迁移文件时同步修复调用和链接。可运行脚本必须有当前消费者或操作入口；一次性脚本、会话导出、日志与迁移备份放 `local/`，不进入发布包。
- `env/scenarios/fixtures/` 内的文档是场景输入，不按普通文档改写；变更会影响输入哈希和实验语义。
