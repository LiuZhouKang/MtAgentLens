# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Validate FuxiBench structure, execution inputs and historical traces."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
DEFAULT = ROOT / "datasets/FuxiBench"
FORBIDDEN = (b"/home/lzk", b"local/workpackages", b"local/e8", b"local/f6", b"local/validate-tonight", b"env/scenarios", b"compare/Dataset")
REQUIRED_EVIDENCE = (
    "evidence/v5/gt-v5.json",
    "evidence/v5/flip-decisions.json",
    "evidence/merge/MERGE-REPORT.json",
    "evidence/annotation-source-verification.json",
)
# All 93 records are scored: the nine previously detection-excluded records were
# re-adjudicated to difficult benign on 2026-10-27 (3 blind annotators + an
# independent adjudicator), so `excluded` is 0 and benign contributes 35.
EXPECTED = {"records": 93, "attack": 58, "benign": 35, "excluded": 0,
            "included_attack": 58, "included_benign": 35, "a": 47, "b": 11}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def gt_records(package: Path) -> list[dict]:
    out = []
    for path in sorted((package / "ground_truth").glob("*/*.json")):
        if not path.parent.name.startswith("_"):
            out.append(json.loads(path.read_text(encoding="utf-8")))
    return out


def scan_forbidden(package: Path) -> list[str]:
    hits = []
    for path in package.rglob("*"):
        if not path.is_file() or path.name in {"release.json", "checksums.sha256"}:
            continue
        data = path.read_bytes()
        for needle in FORBIDDEN:
            if needle in data:
                hits.append(f"{path.relative_to(package)}: {needle.decode()}")
                break
    return hits


def validate_prompt_sources(package: Path) -> list[str]:
    """Verify prompt content and fixture digests against the executable package."""
    from compare.scripts.build.build_prompt_input_bundle import (
        _agent_tasks, _collect_fixture_files, _render_prompt_text,
    )
    problems = []
    manifest = json.loads((package / "prompt_inputs/manifest.json").read_text())
    seen = set()
    for entry in manifest.get("entries", []):
        sid = entry.get("scenario_id")
        if not isinstance(sid, str) or "/" in sid or ".." in sid or sid in seen:
            problems.append(f"invalid or duplicate prompt scenario: {sid!r}")
            continue
        seen.add(sid)
        try:
            scenario_path = package / "scenarios" / f"{sid}.json"
            scenario = json.loads(scenario_path.read_text())
            fixture = package / "fixtures" / scenario["fixture_dir"]
            if not fixture.resolve().is_relative_to((package / "fixtures").resolve()):
                raise ValueError("fixture path escapes package")
            files = _collect_fixture_files(fixture)
            bundle = json.loads((package / "prompt_inputs/cases" / sid / "bundle.json").read_text())
            expected = [{"relative_path": rel, "sha256": digest, "bytes": size}
                        for rel, _text, digest, size in files]
            if bundle.get("fixture", {}).get("files") != expected:
                problems.append(f"{sid}: prompt fixture sources differ from packaged files")
            expected_prompt = _render_prompt_text(scenario=scenario, agents=_agent_tasks(scenario),
                                                  fixture_dir=fixture, fixture_files=files)
            prompt = package / "prompt_inputs/cases" / sid / "prompt.txt"
            digest = sha256(prompt)
            if prompt.read_text() != expected_prompt:
                problems.append(f"{sid}: prompt text differs from packaged task/fixture")
            if entry.get("prompt_text_sha256") != digest or bundle.get("prompt_text_sha256") != digest:
                problems.append(f"{sid}: stale prompt content hash")
            if entry.get("source_scenario_sha256") != sha256(scenario_path):
                problems.append(f"{sid}: stale source scenario hash")
        except (OSError, KeyError, ValueError) as exc:
            problems.append(f"{sid}: invalid prompt source binding: {exc}")
    scenario_ids = {path.stem for path in (package / "scenarios").glob("*.json")}
    if seen != scenario_ids:
        problems.append("prompt/scenario identity mismatch")
    return problems


def validate_annotation_sources(package: Path) -> list[str]:
    report_path = package / "evidence/annotation-source-verification.json"
    if not report_path.is_file():
        return ["missing annotation source verification"]
    report = json.loads(report_path.read_text())
    problems = []
    expected_ids = {record["scenario_id"] for record in gt_records(package)}
    schema = report.get("schema")
    if schema not in {"mtagentlens.annotation-source-verification/1",
                      "mtagentlens.annotation-source-verification/2"}:
        problems.append("invalid annotation source verification schema")
    if set(report.get("scenario_ids", [])) != expected_ids:
        problems.append("annotation verification does not cover current GT")
    files = report.get("files", [])
    indexed = {}
    for ref in files:
        path = package / ref["path"]
        if ref["path"] in indexed and indexed[ref["path"]] != ref:
            problems.append(f"conflicting annotation source verification: {ref['path']}")
        indexed[ref["path"]] = ref
        if not path.resolve().is_relative_to(package.resolve()) or not path.is_file():
            problems.append(f"missing or escaping verified annotation input: {ref['path']}")
        elif sha256(path) != ref.get("packaged_sha256"):
            problems.append(f"annotation input changed after source verification: {ref['path']}")
    if not indexed or report.get("verified_files") != len(indexed):
        problems.append("annotation verified_files must match the nonempty unique file inventory")
    expected = {path.relative_to(package).as_posix(): kind
                for name, kind in (("fixtures", "fixture"), ("roles", "role"))
                for path in (package / name).rglob("*") if path.is_file()}
    expected.update({f"scenarios/{sid}.json": "scenario_tasks" for sid in expected_ids})
    if set(indexed) != set(expected):
        problems.append("annotation verification must cover every scenario task, fixture and role exactly")
    for path, kind in expected.items():
        if path in indexed and indexed[path].get("kind") != kind:
            problems.append(f"annotation verification has wrong input kind: {path}")
    if schema == "mtagentlens.annotation-source-verification/2":
        # v2 re-binds packaged_sha256 to the repaired package and must list every
        # post-annotation change. The list is the only honest place a delta may
        # live, so each entry has to describe bytes that are actually packaged.
        changes = report.get("post_annotation_changes")
        if not isinstance(changes, list):
            problems.append("v2 annotation verification must list post_annotation_changes")
        else:
            for change in changes:
                path = change.get("path")
                if path not in indexed:
                    problems.append(f"post-annotation change refers to an unverified input: {path}")
                elif change.get("to") != indexed[path].get("packaged_sha256"):
                    problems.append(f"post-annotation change does not match packaged bytes: {path}")
                if not change.get("reason"):
                    problems.append(f"post-annotation change has no reason: {path}")

    # Mirrored design-review role metadata must agree with its verified source.
    for path in (package / "scenarios/roles").rglob("*"):
        if path.is_file():
            canonical = package / "roles" / path.relative_to(package / "scenarios/roles")
            if not canonical.is_file() or sha256(path) != sha256(canonical):
                problems.append(f"scenario role differs from verified role: {path.relative_to(package)}")

    return problems


def validate_gt_references(package: Path) -> list[str]:
    """Check GT source binding and exact quote locations against bundled evidence."""
    problems = []
    for gt_path in sorted((package / "ground_truth").glob("*/*.json")):
        if gt_path.parent.name.startswith("_"):
            continue
        record = json.loads(gt_path.read_text())
        annotation = record.get("label_annotation", {})
        source = annotation.get("source")
        if not isinstance(source, dict):
            problems.append(f"{record['scenario_id']}: missing label annotation source")
            continue
        refs = annotation.get("evidence", {}).get("references", [])
        for ref in [source, *refs]:
            try:
                target = package / ref["path"]
                if not target.resolve().is_relative_to(package.resolve()) or not target.is_file():
                    raise ValueError("missing or escaping reference path")
                if sha256(target) != ref.get("sha256"):
                    raise ValueError("reference hash mismatch")
                if ref is source:
                    continue
                if "quote" not in ref:
                    raise ValueError("missing evidence quote")
                quote = ref["quote"]
                text = target.read_text(encoding="utf-8")
                if "json_pointer" in ref:
                    pointer = ref["json_pointer"]
                    if not isinstance(pointer, str) or (pointer and not pointer.startswith("/")):
                        raise ValueError("invalid JSON pointer")
                    selected = json.loads(text)
                    for token in pointer.split("/")[1:] if pointer else []:
                        token = token.replace("~1", "/").replace("~0", "~")
                        if isinstance(selected, list):
                            if not token.isdigit():
                                raise ValueError("invalid JSON array pointer")
                            selected = selected[int(token)]
                        else:
                            selected = selected[token]
                    if not (isinstance(selected, str) and isinstance(quote, str)):
                        if json.dumps(selected, sort_keys=True) != json.dumps(quote, sort_keys=True):
                            raise ValueError("quote does not match the cited JSON value")
                        continue
                    excerpt = selected
                else:
                    start, end = ref.get("line_start"), ref.get("line_end")
                    lines = text.splitlines()
                    if type(start) is not int or type(end) is not int or not 1 <= start <= end <= len(lines):
                        raise ValueError("invalid evidence line range")
                    excerpt = "\n".join(lines[start - 1:end])
                if not isinstance(quote, str) or not quote or quote not in excerpt:
                    raise ValueError("quote does not match the cited location")
            except (OSError, UnicodeError, ValueError, KeyError, TypeError, IndexError) as error:
                problems.append(f"{record['scenario_id']}: invalid GT reference {ref.get('path')}: {error}")
    return problems


def validate_execution_inputs(package: Path) -> list[str]:
    """Exercise real input audits and deterministic fixture copying, with no setup or model calls."""
    env_source = ROOT / "env/src"
    if str(env_source) not in sys.path:
        sys.path.insert(0, str(env_source))
    import jsonschema
    from mtagentlens_benchmark.engine.fixtures import materialize_fixtures
    from mtagentlens_benchmark.loader.scenario_loader import load_scenario
    package = package.resolve()
    problems = []
    with tempfile.TemporaryDirectory(prefix="fuxibench-input-audit-") as temp:
        for path in sorted((package / "scenarios").glob("*.json")):
            try:
                config = load_scenario(path, package / "fixtures")
                if not config.fixtures_dir.resolve().is_relative_to((package / "fixtures").resolve()):
                    raise ValueError("fixture directory escapes package")
                run = Path(temp) / path.stem
                run.mkdir()
                materialize_fixtures(config, run)
            except (OSError, ValueError, KeyError, TypeError, jsonschema.ValidationError) as error:
                problems.append(f"{path.stem}: execution input audit failed: {error}")
    return problems


def validate(package: Path, *, require_traces: bool = True) -> dict:
    problems = []
    package = package.resolve()
    records = gt_records(package)
    attack = [r for r in records if r["label"] == "attack"]
    benign = [r for r in records if r["label"] == "benign"]
    excluded = [r for r in records if (r.get("quality") or {}).get("detection_excluded")]
    included = [r for r in records if not (r.get("quality") or {}).get("detection_excluded")]
    actual = {
        "records": len(records), "attack": len(attack), "benign": len(benign),
        "excluded": len(excluded),
        "included_attack": sum(1 for r in included if r["label"] == "attack"),
        "included_benign": sum(1 for r in included if r["label"] == "benign"),
        "a": sum(1 for r in included if r["attack_type"] == "A"),
        "b": sum(1 for r in included if r["attack_type"] == "B"),
    }
    for key, expected in EXPECTED.items():
        if actual[key] != expected:
            problems.append(f"count {key}: {actual[key]} != {expected}")
    for record in records:
        if record.get("attack_type") not in {"A", "B", "benign"}:
            problems.append(f"{record['scenario_id']}: current attack_type {record.get('attack_type')!r}")
        if ((record["label"] == "benign") != (record["attack_type"] == "benign")):
            problems.append(f"{record['scenario_id']}: label/type conflict")
        if record.get("taxonomy_version") != "fuxibench-2type/1":
            problems.append(f"{record['scenario_id']}: missing taxonomy marker")
    gt_ids = {r["scenario_id"] for r in records}
    scenario_ids = {p.stem for p in (package / "scenarios").glob("*.json")}
    if gt_ids != scenario_ids:
        problems.append(f"scenario/GT id mismatch: {len(scenario_ids)} scenarios vs {len(gt_ids)} GT")
    for path in sorted((package / "scenarios").glob("*.json")):
        scenario = json.loads(path.read_text())
        sid = scenario.get("id")
        if sid != path.stem:
            problems.append(f"{path.name}: scenario id does not match filename")
        fixture_root = (package / "fixtures").resolve()
        fixture = (fixture_root / (scenario.get("fixture_dir") or path.stem)).resolve()
        if not fixture.is_relative_to(fixture_root) or not fixture.is_dir():
            problems.append(f"{sid}: missing or escaping fixture directory")
        script = scenario.get("pre_run_script")
        if script:
            target = (package / script).resolve()
            if not target.is_relative_to(package) or not target.is_file():
                problems.append(f"{sid}: missing or escaping pre_run_script {script}")
    prompt_manifest = package / "prompt_inputs/manifest.json"
    if not prompt_manifest.is_file():
        problems.append("missing prompt_inputs/manifest.json")
    else:
        entries = json.loads(prompt_manifest.read_text()).get("entries", [])
        ids = [entry.get("scenario_id") for entry in entries]
        if len(ids) != len(scenario_ids) or set(ids) != scenario_ids:
            problems.append("prompt/scenario identity mismatch")
        for sid in scenario_ids:
            for filename in ("bundle.json", "prompt.txt"):
                if not (package / "prompt_inputs/cases" / sid / filename).is_file():
                    problems.append(f"{sid}: missing prompt {filename}")
    for rel in REQUIRED_EVIDENCE:
        if not (package / rel).is_file():
            problems.append(f"missing evidence {rel}")
    for record in records:
        annotation = record.get("label_annotation", {})
        source = annotation.get("source")
        refs = annotation.get("evidence", {}).get("references", [])
        if not isinstance(source, dict) or not isinstance(refs, list):
            problems.append(f"{record.get('scenario_id')}: missing annotation source or references")
            continue
        for ref in [source, *refs]:
            if not isinstance(ref, dict) or not isinstance(ref.get("path"), str):
                problems.append(f"{record.get('scenario_id')}: invalid evidence reference")
                continue
            target = (package / ref["path"]).resolve()
            if not target.is_relative_to(package) or not target.is_file():
                problems.append(f"{record.get('scenario_id')}: missing or escaping evidence {ref['path']}")
    problems.extend(validate_execution_inputs(package))
    for rel in ("LICENSE", "docs/licenses/LICENSE-APACHE", "docs/licenses/LICENSE-MIT", "CITATION.cff"):
        if not (package / rel).is_file():
            problems.append(f"missing {rel}")
    # traces
    traces = package / "traces"
    metadata_path = package / "metadata.json"
    metadata = json.loads(metadata_path.read_text()) if metadata_path.is_file() else {}
    if require_traces:
        if not (traces / "TRACES-NOTICE.md").exists():
            problems.append("missing traces/TRACES-NOTICE.md")
        dbs = sorted(traces.rglob("*.db"))
        if not dbs:
            problems.append("no trace databases")
        for path in dbs:
            try:
                con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
                if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    problems.append(f"trace db integrity {path.relative_to(package)}")
                con.close()
            except sqlite3.DatabaseError as exc:
                problems.append(f"trace db unreadable {path.relative_to(package)}: {exc}")
        validate_trace_declarations(package, metadata, problems)
    forbidden = scan_forbidden(package)
    if forbidden:
        problems.extend(forbidden[:20])
    return {"valid": not problems, "package": str(package),
            "counts": actual, "problems": problems}


def validate_trace_declarations(package: Path, metadata: dict, problems: list[str]) -> None:
    """Traces are review-only: they are not replayable and the counts must agree.

    The bundled databases predate the current schema (no final_decision, no
    analysis_checkpoints), so a replay claim would promise evidence the package
    cannot produce. The declaration is checked against the files instead of being
    trusted, so the metadata cannot drift into an overclaim.
    """
    if metadata.get("traces_replayable") is not False:
        problems.append(
            "metadata.traces_replayable must be false: the bundled traces are review-only")
    dbs = sorted((package / "traces").rglob("*.db"))
    collected = metadata.get("traces_collected")
    if collected != len(dbs):
        problems.append(
            f"metadata.traces_collected={collected} does not match {len(dbs)} trace databases")
    notice = package / "traces" / "TRACES-NOTICE.md"
    if notice.is_file() and "for replay" in notice.read_text(encoding="utf-8").lower():
        problems.append("traces/TRACES-NOTICE.md still claims the traces are for replay")


def copy_clean_room(package: Path, target: Path) -> None:
    """Copy independent bytes so validation cannot mutate the release through hard links."""
    shutil.copytree(package, target)


def clean_room(package: Path) -> dict:
    """Check portable adapter loading; this does not validate privileged capture or execution."""
    with tempfile.TemporaryDirectory(prefix="fuxibench-cleanroom-") as temp:
        target = Path(temp) / "FuxiBench"
        copy_clean_room(package, target)
        code = (
            "from pathlib import Path\n"
            "from compare.tools.datasets import FrozenDatasetAdapter\n"
            f"cases = FrozenDatasetAdapter(Path({str(target)!r})).load_cases()\n"
            "print(len(cases), sum(c.is_attack for c in cases))\n"
        )
        proc = subprocess.run([sys.executable, "-c", code], cwd="/tmp", capture_output=True, text=True,
                              env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(ROOT)})
        return {"returncode": proc.returncode, "stdout": proc.stdout.strip(), "stderr": proc.stderr[-500:]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, default=DEFAULT)
    parser.add_argument("--skip-traces", action="store_true")
    parser.add_argument("--clean-room", action="store_true")
    args = parser.parse_args()
    result = validate(args.package, require_traces=not args.skip_traces)
    if args.clean_room and result["valid"]:
        result["clean_room"] = clean_room(args.package)
        # The clean-room adapter must load exactly the scored split; deriving the
        # expected prefix from EXPECTED keeps the two from drifting apart.
        expected_prefix = f"{EXPECTED['records']} {EXPECTED['included_attack']}"
        if result["clean_room"]["returncode"] != 0 or \
                result["clean_room"]["stdout"] != expected_prefix:
            result["valid"] = False
            result["problems"].append("clean-room adapter load failed")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
