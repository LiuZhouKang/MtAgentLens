---
status: accepted
date: 2026-10-19
---

# 0014 — 不设 CI：唯一本地门禁

## Context

项目曾在 `.gitlab-ci.yml` 中定义 `verify` 与 `audit` 两个 stage，并准备了
`docs/ci-runner.md` 与注册脚本。实际情况（2026-10-19 实测）：

- 比赛 GitLab 为 **16.11.10**；项目命名空间 `T202690002999777` 的
  `kind = user`，不是 group；
- 实例没有 instance runner，也没有可用的 group runner；
- Owner token 调 `POST /api/v4/user/runners`（`project_type`）返回 500；
- 实例 API 文档写明 project/group runner 注册要求管理员未启用
  “restrict runner registration”。

结果是仓库长期带着 “配置就绪、等待 runner” 的死配置：文档承诺一个永远无法
运行的 pipeline，OpenSSF 自评也因此留着一个永久缺口。

## Decision

移除全部 CI/CD 资产：`.gitlab-ci.yml`、`docs/ci-runner.md`、`scripts/ci/`、
本机 systemd timer、以及任何 runner token 文件。**唯一门禁**是
`./scripts/verify.sh`（14 条、离线、确定性）；依赖审计保留为独立命令
`python3 scripts/audit_deps.py all`，在发布前于构建主机运行。

## Options considered

- **保留配置等待管理员**：保留一个无法执行的承诺，读者无法区分“未接入”与
  “已损坏”；ADR-0001 明确反对这种状态。
- **改用外部 GitLab/GitHub 镜像**：可用，但会把门禁事实分散到两个平台；
  需要时再按 ADR-0001 的方式调用 `verify.sh`，不需要现在保留死配置。
- **本机定时器**：曾是临时替代；用户要求不再关注 CI/CD，故一并移除，
  避免“看不见的后台任务”成为新的隐性状态。

## Consequences

- 没有 MR 门禁；贡献者与发布者在本地运行唯一入口。`scripts/README.md` 与
  OpenSSF 自评已同步。
- 未来若接入任何 CI，只需让它逐字调用 `scripts/verify.sh`（ADR-0001 仍然
  成立），不改变门禁语义。
- 依赖审计的诚实状态改为“发布前在构建主机运行后满足”，不再依赖 runner。

## Confirmation

- 仓库中不存在 `.gitlab-ci.yml`、`docs/ci-runner.md` 或 `scripts/ci/`；
  `rg` 不再命中这些路径。
- `./scripts/verify.sh` 14 门全绿；`scripts/audit_deps.py all` 是有文档的
  独立命令。
