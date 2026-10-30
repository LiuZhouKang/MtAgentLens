# compare/ package manifest

## Scope

- 8 current experiment directories: detection, ablation, sensitivity, identity, overhead, attribution, pbc, external.
- `docs/`: experiment protocol, evidence index, review response, paper revision notes, dataset cards.
- No secrets, no `env/config`, no `local/` working tree, no unpromoted run databases.

## Raw data inventory

| Experiment | Raw data | Count / size |
|---|---|---|
| detection | `results/detection/data/` | metrics + promoted ledger |
| ablation | `results/ablation/data/exports/` | 93 JSON / 65 MB |
| sensitivity | `results/sensitivity/data/raw/` | 93 exports + 3 source files / 66 MB |
| identity | `results/identity/data/identity-runs/` | 30 JSON + 5 CSV / 1.2 GB |
| overhead | `results/overhead/raw/` | 2952 files / 1.1 GB |
| attribution | `results/attribution/data/` | 93 JSON / 58 MB |
| pbc | `results/pbc/data/` | 4 files / 404 KB |
| external | `results/external/data/` | 5 files / ~130 KB |

## Integrity

```bash
cd compare
find results -type f -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS
```

`SHA256SUMS` 是可选的完整性清单；生成后放在 `compare/` 根目录。

## Exclusions

- `local/final-v1/`：完整运行现场/历史中间结果；包内只保留必要 raw 副本。
- `docs/archive/engineering/`：AI 交接归档，不属于实验交付。
- `compare/docs/` 中部分历史文档仍保留 `local/...` 文字路径；这是历史命令，不是包内依赖。

## Entry

- [`README.md`](README.md)
- [`results/README.md`](results/README.md)
- [`docs/evaluation/CURRENT-EVIDENCE.md`](docs/evaluation/CURRENT-EVIDENCE.md)
