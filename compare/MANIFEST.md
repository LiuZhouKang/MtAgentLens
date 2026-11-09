# compare/ package manifest

## Scope

- 8 current experiment directories: detection, ablation, sensitivity, identity, overhead, attribution, pbc, external.
- `docs/`: experiment protocol, evidence index, review response, paper revision notes, dataset cards.
- `datasets/`: selected data contracts, dataset card, annotation/provenance documents.
- `scripts/`, `tools/`, `rjudge/`, `tests/`: repository code and contracts; kept in Git, not in the handoff tarball.
- No secrets, no `env/config`, no `local/` working tree, no unpromoted run databases.

## Handoff tarball

`package.sh` includes only:

- `README.md`, `MANIFEST.md`, `package.sh`
- `results/`
- `docs/`
- `datasets/`

Everything else stays in the repository.

## Raw data inventory

| Experiment | Raw data | Count / size |
|---|---|---|
| detection | `results/detection/data/` | metrics + promoted ledger |
| ablation | `results/ablation/data/exports/` | 93 JSON / 65 MB |
| sensitivity | `results/sensitivity/data/raw/` | 93 exports + 3 source files / 66 MB |
| identity | `results/identity/data/identity-runs/` | 30 JSON + 5 CSV / 1.2 GB |
| overhead | `results/overhead/raw/` | 2952 files / 1.1 GB |
| attribution | `results/attribution/data/` + `predictions/` + `packets/` + `reference/` | 93 JSON / 58 MB + 30 packets/predictions + 人工复核参考 |
| pbc | `results/pbc/data/` | 4 files / 404 KB |
| external | `results/external/data/` | 5 files / ~130 KB |

## Repository code

| Path | Role |
|---|---|
| `scripts/run/` | pilot observer, app baseline, app judge, scenario runners |
| `scripts/external/` | InjecAgent external-baseline reproduction |
| `scripts/build/`, `scripts/audit/`, `scripts/dataset/` | result builders, audits, dataset contracts |
| `scripts/eval/`, `scripts/ablation/`, `scripts/perf/`, `scripts/vis/` | contract-maintained or historical entrypoints |
| `tools/` | offline tool-comparison framework and adapters |
| `rjudge/` | pinned R-Judge protocol, judge, cache and trace export |
| `tests/` | compare/ contract and regression tests |

## Workspace dependencies excluded from the tarball

The following are kept in the working tree but excluded from the handoff tarball:

- `.venv-llm-guard/`, `.venv-prompt-guard/`, `.venv-nemo-guardrails/`
- `models/` (local baseline weights)
- `Dataset/external/InjecAgent/` (tracked InjecAgent inputs for external-baseline reproduction)

Frozen InjecAgent outputs remain in `results/external/InjecAgent/`.

## Integrity

```bash
cd compare
find results -type f -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS
```

`SHA256SUMS` 是可选的完整性清单；生成后放在 `compare/` 根目录。

## Identity/Attribution annotation status

`results/identity/` and `results/attribution/` contain the final references reviewed and revised by 刘周康、唐浩钺、刘锦样. IdentityBench reports unit-level partition consistency; AttributionBench reports root-only candidate localization. Entry/Chain remain outside the evaluated scope.

## Exclusions

- `local/final-v1/`：完整运行现场/历史中间结果；包内只保留必要 raw 副本。
- `compare/docs/` 中部分历史文档仍保留 `local/...` 文字路径；这是历史命令，不是包内依赖。
- `.venv-*`、`models/`、`Dataset/`、Python 缓存、App 基线 records/traces、数据库与日志不进入 tarball。

## Entry

- [`README.md`](README.md)
- [`results/README.md`](results/README.md)
- [`docs/evaluation/CURRENT-EVIDENCE.md`](docs/evaluation/CURRENT-EVIDENCE.md)
