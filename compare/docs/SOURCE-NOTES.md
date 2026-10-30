# 文档来源与交付范围

本目录保存仓库 `docs/evaluation/`、`docs/paper/` 的实验相关文档副本。源文档负责维护内容，副本同步更新供 `compare/` 交付阅读，不独立编写另一套结果。

- 结果、图表、机器指标和原始记录按 [results](../results/README.md) 的各实验 README 与 MANIFEST 查找；具体前置条件见[复现手册](evaluation/REPRODUCE.md)。
- 当前状态见[实验入口](evaluation/CURRENT-EVIDENCE.md)，审稿引用见[证据索引](evaluation/EVIDENCE-INDEX.md)。历史 RC 保留原有构建、分母和方法身份。
- 论文、审稿、比赛和演示原件统一保留在完整仓库的 `docs/paper/originals/` 与 `docs/materials/`，不在本目录重复打包。
- 运行数据库、本地现场和模型依赖不保证随包提供；有些复现入口需要完整仓库和相应数据。文档中的源码与原件链接可能指向包外仓库路径。
- App 基线的 `records/`、`traces/` 可能含运行时凭据，按打包规则排除。发布前仍需核查其他 raw 内容。

`compare/datasets/` 提供选定的数据契约、数据卡和标注说明副本，不替代完整数据集的 release、metadata、checksums 与证据文件。

交付入口见 [compare README](../README.md)，文件范围见 [MANIFEST](../MANIFEST.md)。
