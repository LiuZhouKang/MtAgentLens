# FuxiBench Human Annotation Workbench Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local FuxiBench scene annotation workbench that lets three raters independently annotate all 93 scenarios, compare disagreements, record adjudications, and export auditable data.

**Architecture:** Add an isolated scene-level package beside the run-level annotator: a read-only scenario catalog, append-only SQLite records, scene-specific HTTP routes and browser UI, and CLI commands to launch and export. Existing run-level schemas and stores remain untouched. Scenario materials come from `scenarios/`, role files, fixtures, and bench scripts; GT and category-bearing prompt bundles are never returned to the independent annotation view.

**Tech Stack:** Python 3 standard library (`sqlite3`, `http.server`, `argparse`, `hashlib`, `json`), static HTML/CSS/JavaScript, pytest.

## Global Constraints

- Keep scene-level records in a new SQLite schema separate from run-level annotation records.
- Read scenario source files without modifying them; never load GT labels into the independent annotation view.
- Preserve individual submissions append-only, and make edits create revisions with a reason.
- Keep changes within `tools/annotator/`, its tests, and this plan/spec documentation.
- Every behavior label must remain backed by scenario design evidence, not runtime traces or detector output.

---

## File Structure

- Create `tools/annotator/scene_catalog.py`: load scenario inputs and return safe, hashed source materials without GT leakage.
- Create `tools/annotator/scene_records.py`: validate scene annotations and persist immutable submissions/revisions/adjudications in a dedicated SQLite database.
- Create `tools/annotator/scene_server.py`: serve the scene queue, annotation forms, comparison, adjudication, and export APIs.
- Create `tools/annotator/static/scenes.html`: single-page scene annotation UI.
- Modify `tools/annotator/__main__.py`: add `scene-serve` and `scene-export` CLI commands.
- Create `tools/annotator/tests/test_scene_catalog.py`, `test_scene_records.py`, and `test_scene_server.py`.
- Modify `tools/annotator/README.md`: document launch, independent workflow, export, and the distinction between scenario design materials and runtime traces.

## Task 1: Load and redact scenario materials

**Files:**
- Create: `tools/annotator/scene_catalog.py`
- Test: `tools/annotator/tests/test_scene_catalog.py`

**Interfaces:**
- `load_scenarios(dataset_root: Path) -> list[dict]`
- Each record is `{scenario_id: str, sources: [{path: str, sha256: str, text: str, kind: str}]}`.
- Allowed source kinds: `scenario`, `role`, `fixture`, `bench`.

- [ ] Write tests with a temporary mini dataset for source discovery, stable SHA-256, UTF-8 text loading, missing source handling, and path traversal/symlink rejection.
- [ ] Add a fixture case where scenario JSON contains `category` and `attack_type`; assert these fields and all GT labels are absent from the returned payload.
- [ ] Run `python3 -m pytest tools/annotator/tests/test_scene_catalog.py -q` and confirm the new tests fail because the module/API is absent.
- [ ] Implement only the catalog API above; gather role references from scenario agent entries, fixture files below the declared fixture directory, and the matching bench directory.
- [ ] Re-run the focused pytest command and confirm all tests pass.

## Task 2: Validate and persist independent records

**Files:**
- Create: `tools/annotator/scene_records.py`
- Test: `tools/annotator/tests/test_scene_records.py`

**Interfaces:**
- `SceneRecordStore(path: Path, scenario_catalog: list[dict], raters: list[str], codebook_version: str)`
- `save_draft(scenario_id: str, rater: str, annotation: dict, expected_revision: str | None = None) -> dict`
- `submit(scenario_id: str, rater: str, annotation: dict, expected_revision: str | None = None) -> dict`
- `rater_view(rater: str) -> list[dict]`
- `comparison(scenario_id: str) -> dict`
- `adjudicate(scenario_id: str, adjudicator: str, annotation: dict, rationale: str) -> dict`
- `agreement_report() -> dict`
- `export() -> dict`

- [ ] Write tests for valid/invalid enum values, missing required fields, duplicate behavior IDs, unknown evidence paths, unknown raters/scenarios, append-only revisions, stale revision conflicts, peer-answer hiding before all three submissions, visible comparison after all three submissions, and adjudication rationale.
- [ ] Run `python3 -m pytest tools/annotator/tests/test_scene_records.py -q` and confirm expected failures before implementation.
- [ ] Implement a new SQLite application ID/schema with immutable scene catalog bindings and append-only per-rater draft/submission revisions; include rater, time, codebook version, input hashes, and change reason.
- [ ] Implement server-side field validation against `datasets/FuxiBench/docs/codebook.md`: `label`, `attack_type`, `task_summary`, `expected_behaviors`, `benign_activity`, `min_decision`, `should_alert`, `payload`, and `execution_check`.
- [ ] Implement peer visibility policy: an annotator sees their own records; peer records and comparisons become visible only after each configured rater has submitted that scenario.
- [ ] Calculate Fleiss’ κ and raw agreement for nominal fields from complete pre-adjudication submissions; return unavailable while any scenario is incomplete and preserve undefined κ when expected agreement is 1.
- [ ] Run the focused pytest command and confirm all cases pass.

## Task 3: Provide HTTP API and one-page UI

**Files:**
- Create: `tools/annotator/scene_server.py`
- Create: `tools/annotator/static/scenes.html`
- Test: `tools/annotator/tests/test_scene_server.py`

**Interfaces:**
- `create_scene_server(store: SceneRecordStore, catalog: list[dict], host: str, port: int) -> HTTPServer`
- `GET /api/scenes?rater=<name>` returns that rater's queue and sanitized materials only.
- `GET /api/scenes/<id>?rater=<name>` returns the selected scene, own saved record, codebook options, and peer records only when visibility policy allows.
- `POST /api/scenes/<id>/draft`, `/submit`, `/adjudicate` call corresponding store operations.
- `GET /api/agreement` returns the all-scenario pre-adjudication agreement report only when complete.
- `GET /api/export` returns the store export.

- [ ] Write API tests proving GT/category fields are never returned, a rater cannot request another rater's saved view, peers stay hidden before all submissions, and validation/conflict errors have actionable responses.
- [ ] Run `python3 -m pytest tools/annotator/tests/test_scene_server.py -q` and confirm expected failures.
- [ ] Implement the scene-specific HTTP handler without changing the existing run-level workbench routes.
- [ ] Build the UI with rater/scene navigation, progress, source-material panel with paths/hashes, codebook-driven fields, expected-behavior row editing, save draft, submit, comparison, adjudication rationale, and JSON export.
- [ ] Show the agreement report only after all raters submit all scenes; label κ as inter-rater agreement, not validity.
- [ ] Add client-side required-field hints while retaining all validation server-side; show explicit save/submit state and preserve unsaved form contents on errors.
- [ ] Run the focused pytest command and check the static page includes all required controls and accessible labels.

## Task 4: Add launch/export commands and documentation

**Files:**
- Modify: `tools/annotator/__main__.py`
- Modify: `tools/annotator/README.md`
- Test: `tools/annotator/tests/test_scene_server.py`

- [ ] Write CLI parser tests for `scene-serve --dataset --store --rater --codebook-version --port` (repeatable `--rater`) and `scene-export --store --out`.
- [ ] Run the focused CLI test and confirm it fails before the commands exist.
- [ ] Implement commands; `scene-serve` loads the catalog and starts the local UI, and `scene-export` writes a new export without overwriting an existing path.
- [ ] Document the one-command local startup, three-rater workflow, trace/material boundary, revision behavior, and export path.
- [ ] Run `python3 -m pytest tools/annotator/tests/test_scene_catalog.py tools/annotator/tests/test_scene_records.py tools/annotator/tests/test_scene_server.py -q`.

## Task 5: End-to-end acceptance on FuxiBench

- [ ] Start from the repository's FuxiBench dataset and assert the catalog contains exactly 93 unique scenario IDs.
- [ ] Assert no record or serialized independent view contains GT `label`, `attack_type`, `category`, `quality`, or annotation metadata.
- [ ] Use the test client to draft and submit one scenario under all three raters; verify each sees only their own draft before submission and the comparison after all submit.
- [ ] Record an adjudication with rationale and verify the export contains three original rater chains plus the adjudicated record and matching codebook/input hashes.
- [ ] Verify Fleiss’ κ against a hand-calculated rating matrix and that it uses the pre-adjudication submissions.
- [ ] Run `python3 -m pytest tools/annotator/tests/test_scene_catalog.py tools/annotator/tests/test_scene_records.py tools/annotator/tests/test_scene_server.py -q` and `git diff --check`.

## Self-Review

- Catalog, no-label leakage, independent storage, UI, adjudication, export, CLI, and documentation each have an implementation task.
- The independent API contains no runtime trace source; only scenario, role, fixture, and bench inputs are loaded.
- Existing 93 GT files are not used to pre-fill individual records or silently overwritten.
- Existing working-tree changes are outside the implementation scope and must remain untouched.
