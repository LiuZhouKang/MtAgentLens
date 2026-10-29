# FuxiBench traces

These are historical eBPF/collector traces kept for provenance review only. They are **not
replayable**: every bundled database predates the current collector schema (no
`final_decision`, no `analysis_checkpoints`), so it cannot produce a canonical verdict or
feed the current analysis kernel.

Host paths were replaced with neutral dataset-relative placeholders; fixture credentials are
decoys, not real secrets. `metadata.json` declares `traces_replayable: false` and
`traces_collected: 87`, and `validate_fuxibench_package.py` checks both against the files.
Run `sha256sum -c ../checksums.sha256` before use.
