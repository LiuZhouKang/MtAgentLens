# 实验文档包

## 先看

1. [`evaluation/CURRENT-EVIDENCE.md`](evaluation/CURRENT-EVIDENCE.md)：当前实验状态与权威路径；
2. [`../results/README.md`](../results/README.md)：各实验 README/MANIFEST/原始数据；
3. [`paper/REVIEW-RESPONSE-EVIDENCE-2026-10-26.md`](paper/REVIEW-RESPONSE-EVIDENCE-2026-10-26.md)：审稿意见回复；
4. [`evaluation/REPRODUCE.md`](evaluation/REPRODUCE.md)：复现命令。

## 内容

- `evaluation/`：实验协议、证据索引、复现手册、T4 最终结果、当前证据页、PBC/身份/开销等专题；
- `paper/`：审稿意见、回复证据、修改说明、claim-evidence-matrix、论文工作稿与历史数字审计；
- `../datasets/`：数据集契约与数据卡副本；
- `SOURCE-NOTES.md`：包内路径与 `local/` 现场的关系。

## 口径

- 当前实验状态与权威结果路径见 `evaluation/CURRENT-EVIDENCE.md`；
- 历史实验/旧 RC 文档保留在 `evaluation/` 中，但不得作为当前结论；
- `claims.json` 是机器可校验数字层，修改论文数字前先改它并跑 `verify_claims.py`。
