# Source notes（打包版）

本目录是 `docs/evaluation/` 与 `docs/paper/` 的实验相关副本，目的是让 `compare/` 可以独立打包交付。

- 原始实验数据：在各实验目录的 `data/`；不需要 `local/`。
- 历史运行现场、未 promote 的中间 run、旧 build 仍保留在仓库的 `local/final-v1/`，**不放入本包**。
- 文档中出现的 `local/...` 路径是历史命令/现场记录，包内不保证存在；需要复跑时按 `compare/docs/evaluation/REPRODUCE.md` 使用仓库环境。
- `compare/docs/paper/` 保留审稿回复、修改说明和论文工作稿；历史数字以 `compare/docs/evaluation/CURRENT-EVIDENCE.md` 为准。

包入口：[`compare/README.md`](../README.md)。原始数据索引：[`compare/results/README.md`](../results/README.md)。

> 部分历史文档仍会链接到仓库内的 `collector/`、`scripts/`、`docs/adr/`、`tools/` 或旧 `local/`；这些都是历史复现命令或审计链接，不是包内相对依赖。实验交付只需 `results/` 下的 README/MANIFEST/data。
