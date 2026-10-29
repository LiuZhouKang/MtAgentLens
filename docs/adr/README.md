# Architecture Decision Records

MADR-minimal: YAML front matter + Context / Options / Outcome / **Confirmation**.
`Confirmation` is the part that matters — it names the mechanism that keeps the
decision true, because a decision recorded without a guard is a decision that rots.

Only **irreversible or expensive-to-reverse** decisions are recorded here. Existing
`docs/superpowers/specs` are deliberately *not* migrated: an ADR log does not
retro-fit history, and pretending otherwise produces documentation that was never
true at the time it claims. Accepted debt goes to `docs/capabilities.json`
(`known_limitations`), not here.

## Index

| ADR | Decision |
|---|---|
| [0001](0001-single-verification-entry-point.md) | One verification entry point, with budgets instead of disables |
| [0002](0002-dual-license.md) | Dual `Apache-2.0 OR MIT`, upstream MIT untouched |
| [0003](0003-rust-lints.md) | Enable `dead_code_pub_in_binary` + `unreachable_pub`; reject `unused_crate_dependencies` |
| [0004](0004-ignored-tests-are-tracked-debt.md) | 26 ignored tests are tracked debt, not a solution |
| [0005](0005-single-schema.md) | 单一 schema：版本脊梁、身份信念、退役因果面（ignored 25→3） |
| [0006](0006-scenario-vocabulary-is-data.md) | 场景词表是数据（TOML + 单一加载器 + 运行时覆盖），不是三份代码副本 |
| [0007](0007-file-mutation-producer.md) | 文件变更生产者并入 `process`（unlinkat/truncate），删除未接线 `fileaccess` |
| [0008](0008-data-egress-defaults.md) | 数据出口安全默认值：无内容导出、出口脱敏、仅回环、库文件 0600 |
| [0009](0009-dependency-audit-gates.md) | 依赖审计门：预算只降不升、缺失工具即失败、npm 只门生产依赖 |
| [0010](0010-network-event-producers.md) | 网络事件生产者：syscall tracepoint、AF_INET 解析、AF_INET6 显式未支持不伪造 |
| [0011](0011-one-match-level.md) | 唯一 MatchLevel：审计后删除 legacy 证据宇宙（无持久化、无消费者），单一分类 + 单定义门 |
| [0012](0012-graph-pattern-templates-inert.md) | 图模式模板：14 个模板全部依赖无生产者类型（matcher 从不命中）；显式声明 + 收缩门 |
| [0013](0013-architecture-consolidation.md) | 架构收口：单一类型真源、纯删除死模块、静默失败可观测（diagnostics/计数器/门） |
| [0014](0014-no-ci-local-gate.md) | 不设 CI：移除未接入的 CI 配置，唯一门禁 `scripts/verify.sh`，审计为独立本地命令 |
| [0015](0015-alert-actionability-and-taint-authorization.md) | 只有 `decision=Unsafe` 是可行动告警；TaintedContextUse 需未授权共享 + Observed 置信度；安全分数下的 claim 只作信息信号 |
| [0016](0016-no-event-level-alignment-without-shared-anchor.md) | `event_alignment_available` 保持 false；无共享可核验锚点不得做事件级对齐，时间相近/字符串相似不算证据 |
| [0017](0017-evidence-bounded-attribution.md) | 证据保全、分层身份与授权、偏序归因及独立参考；已授权分阶段实施，验收未全部完成 |
