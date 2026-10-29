# Source notes（打包版）

本目录是 `docs/evaluation/` 与 `docs/paper/` 的实验相关副本，目的是让 `compare/` 可以独立打包交付。

- 原始实验数据：在各实验目录的 `data/`；不需要 `local/`。
- 历史运行现场、未 promote 的中间 run、旧 build 仍保留在仓库的 `local/final-v1/`，**不放入本包**。
- App 基线原始 `results/external/baselines/app/*/records/` 与 `traces/` 可能含运行时凭据，只保留在本地现场，打包排除；其他 raw 文件仍需交付前审查，不能据排除规则认定全部内容无秘密。
- 文档中出现的 `local/...` 路径是历史命令/现场记录，包内不保证存在；需要复跑时按 `compare/docs/evaluation/REPRODUCE.md` 使用仓库环境。
- `compare/docs/paper/` 保留审稿回复、修改说明和论文工作稿；历史数字以 `compare/docs/evaluation/CURRENT-EVIDENCE.md` 为准。

包入口：[`compare/README.md`](../README.md)。原始数据索引：[`compare/results/README.md`](../results/README.md)。

> 部分历史文档仍会链接到仓库内的 `collector/`、`scripts/`、`docs/adr/`、`docs/audit/`、`tools/`、根目录 `CHANGELOG.md` 或旧 `local/`；这些是仓库依赖或历史审计链接，包内不保证存在。包内只收录顶层 README/MANIFEST、`results/`、`docs/`、`datasets/` 与打包脚本，并排除运行数据库、日志和上述 App 原始记录。根目录的下载或交付归档不在收录范围，结果内部的证据归档（如 `results/overhead/archives/`）保留。查看结果可使用包内数据；代码复现需要完整仓库。

`compare/datasets/` 只随包提供数据契约与三份数据卡／标注说明文本。其链接涉及的 `metadata.json`、`release.json`、`SCENARIOS.md`、`CITATION.cff`、traces 说明和 legacy evidence 均位于完整仓库的数据集目录，不由这几份文本替代。
