# scripts/

| 路径 | 用途 | 何时运行 |
|------|------|----------|
| `verify.sh` | **唯一门禁**（14 条，离线、确定性、预算只降） | 每次改动；提交前 |
| `audit_deps.py` + `audit-budget.json` | 依赖审计（cargo/npm，需网络） | 发布前；预算只降 |
| `check_fresh_clone.sh` | 用工作树快照建临时 clone 并跑门禁 | 改构建/嵌入/子模块后 |
| `release.sh` | 二进制 + 文档 tarball + SHA-256（可选 GPG） | 发布 |
| `e2e/` | 真实 E2E：`record-smoke`、`snapshots-contract`、`openclaw-outofband` | 需要 sudo |
| `wire/` | 原始 wire 语料、FPR/开销基线、OpenClaw 交叉校验 | 采集/复测基线 |
| `spikes/` | 一次性测量脚本（网络 E2E、噪声、P3b 覆盖） | 按需；结论已记录 |
| `schema/` | legacy DB 抽样与 schema 采纳 | 迁移审计 |
| `evaluation/` | 二进制裁决 BCa/Wilson 复现 | 生成评估产物 |
| `tests/` | 审计门的契约测试 | 由 `verify.sh` 第 14 门运行 |

规则：新增脚本必须有调用者或文档入口；一次性脚本放 `spikes/` 并在结论文档中引用。
