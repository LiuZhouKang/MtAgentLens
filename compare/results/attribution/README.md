# AttributionBench（T6）：根因候选定位

> 当前根因参考经三位标注者人工核查、修订并确认：刘周康、唐浩钺、刘锦样。此处报告 root-only 评价；Entry 和 Chain 尚无本实验结果。

## 结果概览

预测从 93 个冻结运行的 22,460 条 traceback 中提取，在 30 个多智能体场景上评价，其中 22 个攻击场景有恶意根因，8 个良性场景的参考为 null。

| 指标 | 结果 |
|---|---:|
| Root@1 | 22.7%（5/22） |
| Root@2 | 54.5%（12/22） |
| Root@3／候选覆盖 | 100%（22/22） |
| MRR | 0.538 |
| RootSet F1（3 候选） | 0.500 |

该结果表示参考根因落入三个候选的覆盖情况；Root@1 仍为 22.7%，不支持“根因定位已解决”的结论。Root@3 与固定三候选覆盖相同，不表示排序质量。8 个良性场景不进入攻击根因准确率分母。

## 标注与评价口径

三位标注者对盲化场景材料中的 root 判断进行人工复核与修订，证据不足或良性场景保留 null。当前根因参考列在 `datasets/FuxiBench/annotations/attribution-bench/root-reference.json` 的 `human_annotators` 中。人工最终标签用于计算上述指标；未保留逐人原始投票时，不报告逐人一致率或 κ。

本实验只评价 root 候选定位。Entry、Chain、有序责任传播以及完整 T6 均未由本结果验证。预测是对已记录 traceback 的离线映射，不是 live root inference，也未新增 Agent 运行。

## 数据与复现

- `data/`：93 个运行的归因 JSON。
- `predictions/`：30 个场景的 root 候选。
- `packets/`：盲化场景证据材料。
- `datasets/FuxiBench/annotations/attribution-bench/root-reference.json`：人工复核并冻结的根因标签。
- `metrics/t6-root.json`、`tables/`、`figures/`：指标、论文表格和图形。

```bash
PYTHONPATH="." python compare/results/attribution/reproduce/build_root_predictions.py
PYTHONPATH="." python compare/results/attribution/reproduce/evaluate_root_full.py
PYTHONPATH="env:env/src:." python compare/results/attribution/reproduce/render_root.py \
  --metrics compare/results/attribution/metrics/t6-root.json --out compare/results/attribution
```

- 交接清单：[MANIFEST.md](MANIFEST.md)
- 相关实验：[IdentityBench](../identity/README.md)
