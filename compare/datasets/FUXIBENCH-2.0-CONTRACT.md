# FuxiBench 2.0 — final dataset/open-source contract (2026-10-26)

Status: frozen design decision by the Lead; **implemented and frozen as FuxiBench 2.0.0** (2026-10-26). This file is the
single contract for the migration; every later change must either satisfy it or update it
explicitly.

## 1. Two-type taxonomy (old Type A removed)

The old "Type A = explicit malicious user instruction" category is removed from the current
taxonomy and from the paper. The audited scenarios are re-expressed with two types:

| new type | meaning | old type |
|---|---|---|
| **A** | untrusted/indirect instruction or poisoned artifact drives the action (prompt injection, poisoned dependency, malicious fixture) | old **B** |
| **B** | multi-agent / handoff / delegated-authority attack | old **C** |
| benign | no audited attack | benign |

The two old Type-A scenarios and every other scenario whose independent 3+adjudication
audit found `benign` are **audited benign** in the canonical GT. Their v4 attack contract is
not deleted: it is preserved under `legacy_v4_detection_contract`.

**Amendment 2026-10-27:** the nine records that carried `quality.detection_excluded=true`
were re-adjudicated — three blinded, mutually isolated annotators (executability /
authorization-and-harm / observability-and-causality) returned 27/27 benign votes, and an
independent adjudicator re-checked each fixture line and returned `include_benign` with no
conflicts. They are difficult negatives: bounded writes, self-hosted loopback stubs,
`DECOY-*` credentials, decoded values never `eval`/`exec`. `detection_excluded` is now
**0** and the flag is kept as an explicit filter that is currently empty.

### Resulting counts

- scenario inputs: 93 (unchanged; including 30 multi-agent scenarios)
- canonical GT: 93 records = **58 attack + 35 benign**
- detection-included: **93 = 58 attack (new A 47, new B 11) + 35 benign**
- detection-excluded: **0** (the previous 9 were re-adjudicated on 2026-10-27; legacy v4
  contracts are still retained)
- no `attack_type=C` and no old Type-A records remain in current fields; every migrated
  record records `taxonomy_version="fuxibench-2type/1"`, `legacy_label`,
  `legacy_attack_type`, and (for excluded) `legacy_v4_detection_contract`.

The GT schema name remains `mtagentlens.ground_truth.v4` for tool compatibility; the
taxonomy version is explicit in the new fields and in `metadata.json`.

## 2. One canonical, self-contained package

New canonical tree: `datasets/FuxiBench/` (the package is called FuxiBench; version is in
`metadata.json`/`release.json`, not in the directory name).

```
datasets/FuxiBench/
  README.md  DATASET-CARD.md  AI-ANNOTATION-PROTOCOL.md  PROVENANCE.md
  LICENSE  CITATION.cff  metadata.json  release.json  checksums.sha256
  scenarios/          # 93 scenario JSONs, fixture_dir relative
  fixtures/           # all fixture payloads/scripts referenced by the scenarios
  bench/              # setup/sink support scripts
  prompt_inputs/      # regenerated, fixture-relative
  ground_truth/       # 93 portable GT records + _reviewers/_agreement/_validation/_causal
  evidence/           # v5 votes/adjudication, flip decisions, merge report, PBC,
                      # validation evidence, legacy v4 GT, taxonomy mapping
  traces/             # sanitized historical traces for MtAgentLens (see §4)
```

The old in-repo dataset working tree is replaced by this package (or becomes a
thin pointer only if a hard path is required); `env/scenarios` remains the executable
working tree and must stay hash-identical to the package `scenarios/`+`fixtures/`+`bench/`
(validator enforces).

## 3. Portability

- No GT/scenario/fixture/prompt/evidence field may contain `/home/`, `local/`,
  scenario-only paths, worktree-relative dataset paths, or any host-specific absolute path.
- Dataset-internal references are relative: `scenarios/...`, `fixtures/...`,
  `evidence/...`, `ground_truth/...`.
- `/tmp/...` is allowed only as scenario runtime semantics and must be documented as such.
- `label_annotation.source`, `v4_detection_contract`, reviewer records, quality review
  evidence and annotation inputs are rewritten to bundled relative evidence paths.
- A `validate_portable_dataset.py` gate rejects violations, symlinks, real secrets and
  missing hashes; it runs from the package alone.

## 4. Traces

Traces are part of the package because MtAgentLens uses them as eBPF-captured input, but
they are sanitized before packaging:

- strip host paths (`/home/lzk`, `local/workpackages`, `env/scenarios`, repository paths)
  from text and SQLite payloads, replacing with neutral dataset-relative placeholders;
- scan for real credentials/tokens/private keys and fail closed on anything not marked as a
  decoy;
- preserve DB schema so replay/sensitivity tooling still works; hash every file;
- `traces/TRACES-NOTICE.md` explains provenance, sanitization, and the decoy nature.

## 5. License

- Code: existing Apache-2.0 OR MIT (repo licenses).
- Dataset (scenarios, fixtures, GT, docs, evidence, sanitized traces): **CC-BY-4.0**, with a
  `LICENSE-DATA` and attribution in `CITATION.cff`; decoy credentials are explicitly
  non-secret test data.
- Historical reviewer names are kept only as AI process identifiers, never as human
  authorship.

## 6. Cleanup / one source of truth

- Exactly one current dataset package, one current release, one current documentation set.
- Old v4/v5 GT and old release artifacts are represented inside
  `evidence/legacy/` (bytes + hashes + mapping), not as parallel canonical trees.
- Stale release directories, duplicate workpackages, caches, `__pycache__`, generated
  experiment outputs and dead scripts are removed after the package and gates pass
  (inventory recorded in `CLEANUP-REPORT.md`).
- Legacy scripts that are not part of the current pipeline are moved to a single
  `legacy/` area with a "not for current metrics" guard, or deleted if redundant.

## 7. Gates before any experiment

1. `validate_gt` + package validator + portability + clean-room load all green.
2. `./scripts/verify.sh` 18/18 (incl. the FuxiBench package gate), claims 54/54, docs link.
3. Adapter loads 93 detection-included cases (58 attack / 35 benign) from the package
   without `env/` or `local/` available.
4. All metric walkers apply the detection-excluded filter (tests).
5. Independent QA review; residual risk register; no known P0/P1.
6. Advisor sign-off; then, and only then, the final paid campaign.
