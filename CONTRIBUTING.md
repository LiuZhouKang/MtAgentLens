# 贡献指南

MtAgentLens 是一个在内核层观测 agent 行为的工具。它最贵的缺陷不是「写错了」，
而是「没有任何机器能断言它是对的」——所以贡献流程围绕**可证伪**设计。

## 先读

1. `CLAUDE.md`：架构、硬约束、任务复杂度分流。
2. `docs/superpowers/HANDOFF-2026-10-18-single-schema-done.md`：当前状态与坐标。
3. `docs/adr/`：已经付出代价换来的决策，改动前先看有没有被否决过。

## 环境

```bash
# 依赖：Rust 1.85+、clang/llvm、libelf-dev、zlib1g-dev、libssl-dev、
#       libzstd-dev（bpftools 的 bpftool bootstrap 需要）、rsync（fresh-clone 检查）
git clone --recurse-submodules <repo>   # 或之后 git submodule update --init --recursive
make build          # 前端 + eBPF + Rust release
```

`libbpf/` 与 `bpftool/` 是 git submodule，不是普通目录；没有它们 eBPF 无法构建。

Rust 产物在 `collector/build/`（`.cargo/config.toml` 设了 `target-dir`），
不是 `collector/target/`。eBPF 产物用 `make .output/bin/<name>`，**不要**
`make <name>`（会命中同名目录并假成功）。

## 唯一门禁

```bash
./scripts/verify.sh     # 14 条门 + 4 条只许降的预算
```

任何 PR/提交都必须让它全绿。网络可用的环境还应跑依赖审计
（发布前在构建主机执行同一条命令）：

```bash
python3 scripts/audit_deps.py all   # cargo audit + npm audit（需 RustSec DB / npm registry）
```

被接受的漏洞只能写进 `scripts/audit-budget.json`，且每条必须有理由；
`verify.sh` 会离线校验该文件的完整性，审计消失时条目必须当天删除。

需要证明「第三方 clone 也能绿」时：

```bash
./scripts/check_fresh_clone.sh   # 临时仓库 + clean clone + 完整门禁
```门禁失败时**修根因**，不要加 `#[allow]`、
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

## 文档

- 文档用中文，代码/标识符/commit 用英文。
- 架构或行为变化时同步更新：`README.md`、`docs/architecture.md`、
  `docs/capabilities.json`（能力清单的唯一真源）、对应 ADR。
