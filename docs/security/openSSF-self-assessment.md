# OpenSSF Best Practices —— 自评

> 对照 [OpenSSF Best Practices Badge](https://www.bestpractices.dev/) 的
> **Passing** 级别逐项自评。状态只有三种：**满足 / 部分 / 未满足**，
> 每条给证据；不给「大概满足」。
> 生成日期：2026-10-18。

## 基础（Basics）

| # | 准则 | 状态 | 证据 / 缺口 |
|---|---|---|---|
| 1 | 项目有描述性名称与简介 | 满足 | `README.md` 首段 |
| 2 | 描述项目用途 | 满足 | `README.md`、`docs/architecture.md` |
| 3 | 提供获取方式（源码/构建） | 满足 | `README.md` 构建章节；`make build` |
| 4 | 提供反馈渠道 | 部分 | 托管平台 issue；无邮件列表 |
| 5 | 提供贡献指南 | 满足 | `CONTRIBUTING.md` |
| 6 | 声明许可证 | 满足 | `LICENSE-APACHE` / `LICENSE-MIT`；`Cargo.toml` = `Apache-2.0 OR MIT` |
| 7 | 许可证为 OSI 认可 | 满足 | Apache-2.0 / MIT |
| 8 | 支持 HTTPS 获取源码 | 部分 | 托管平台提供；镜像可达性取决于平台 |
| 9 | 每个源文件带许可证标识 | 满足（有豁免） | `scripts/verify.sh` 门 7 覆盖 Rust/scripts/eBPF；`bpftools/ecapture` 与 `tests/bpf` 为上游 vendored；frontend 由 `package.json` 覆盖 |

## 变更控制（Change Control）

| # | 准则 | 状态 | 证据 / 缺口 |
|---|---|---|---|
| 10 | 使用公开版本控制 | 满足 | git；托管于 GitLink / GitLab |
| 11 | 每次发布有唯一版本号 | 部分 | `Cargo.toml` 0.1.0；尚无 release 流程 |
| 12 | 发布说明 | 部分 | 无 `CHANGELOG.md`；提交历史可读 |
| 13 | 源码变更可追溯 | 满足 | git 历史；ADR 记录架构决策（`docs/adr/`） |

## 报告（Reporting）

| # | 准则 | 状态 | 证据 / 缺口 |
|---|---|---|---|
| 14 | 缺陷报告流程 | 满足 | 托管平台 issue 模板由平台提供 |
| 15 | 漏洞报告流程 | 满足 | `SECURITY.md`：私密 issue、7 天确认、协调披露 |
| 16 | 漏洞报告流程公开 | 满足 | `SECURITY.md` |

## 质量（Quality）

| # | 准则 | 状态 | 证据 / 缺口 |
|---|---|---|---|
| 17 | 可工作的构建系统 | 满足 | `make build`；**clean clone 实测 14/14**（`./scripts/check_fresh_clone.sh`） |
| 18 | 自动化测试套件 | 满足 | `cargo test`（1000+ 测试）、`make -C bpftools test`、wire oracle |
| 19 | 测试可被第三方运行 | 满足 | 全部测试在仓库内；fixtures 入库（含 schema v1 DB） |
| 20 | 测试覆盖主要功能 | 部分 | 责任管线/持久化/协议解码覆盖充分；前端 e2e 依赖本机浏览器 |
| 21 | 编译警告处理 | 满足 | clippy 预算门（只降不升）；`cargo fmt --check` |
| 22 | 回归测试 | 满足 | 每个修复带门（ADR 0004/0005/0007 均有说明） |
| 23 | 静态分析 | 满足 | clippy `--all-targets`；eBPF verifier 真实加载检查 |
| 24 | 动态分析 | 部分 | `ASAN=1` 可构建 bpftools；无持续 fuzz |

## 安全（Security）

| # | 准则 | 状态 | 证据 / 缺口 |
|---|---|---|---|
| 25 | 安全设计（威胁模型） | 满足 | `docs/security/threat-model.md`（STRIDE + 坐标） |
| 26 | 密码学使用正确 | 满足（有限使用） | 仅用于 TLS 捕获（内核/libssl 原语）；无自研密码学 |
| 27 | 无已知未修复漏洞 | 部分 | 依赖审计门已实现（独立命令 `scripts/audit_deps.py all` + 离线预算门）；Rust 依赖当前 0 漏洞、npm 生产依赖 0 漏洞。dev-only 工具链告警未纳入门（不进入产物，见 `docs/capabilities.json`） |
| 28 | 安全默认值 | 满足 | 仅回环绑定、无通配 CORS、DB 0600、导出无内容 |
| 29 | 漏洞缓解 | 满足 | 原始头脱敏、schema 零写入拒绝、ring buffer 丢弃计数 |
| 30 | 无硬编码凭据 | 满足 | `/home/<user>` 预算 = 0；测试用合成密钥 |

## CI / 发布（Passing 未强制，但影响可信度）

| 项 | 状态 | 缺口 |
|---|---|---|
| 持续集成 | **不适用（已移除）** | 仓库不携带 CI 配置或 runner 脚本（ADR-0014）；唯一门禁 `scripts/verify.sh` 在本地/定时器上运行 |
| 签名发布 | 未满足 | 无发布产物签名 |
| 依赖审计 | 满足（可执行） | `python3 scripts/audit_deps.py all`：`cargo audit` 全依赖 + `npm audit --omit=dev` 生产依赖；预算文件 `scripts/audit-budget.json` 由 `verify.sh` 离线校验。首次运行即删除未使用的 `anthropic` 依赖并修复 6 个 npm 生产依赖，当前预算为空 |

## 结论

Passing 级别的主要缺口不是 CI（已明确不设 CI），而是**依赖审计需要在发布前手动运行**；该门已实现并首次运行即修复了真实漏洞。
下一步优先级：

1. 在构建主机运行 `./scripts/verify.sh` 与 `python3 scripts/audit_deps.py all`，两者都绿；
2. 建立 release 流程（版本号、发布说明、产物签名）；
3. 保持审计预算为空：出现漏洞时先升级依赖，只有无法升级时才新增带理由的预算。
