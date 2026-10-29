# kernel-probe-lab

Scratch-storage qualification package used before a probe build cache is
admitted to a node.

- `make test` — run the qualification self-check and verify the artifacts.
- `src/qualify.py` — the bounded sequential, parallel and metadata workload.
- `config/lab.toml` — lab-image placeholders (not used by the check).
- `docs.md` — the runbook. Read it before running anything.

The workload writes only under `/tmp/acme-resource-disk-fill` and never changes
system state.
