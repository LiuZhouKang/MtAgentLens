"""Freeze scenario inputs and their GT together; never relabel a scenario."""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from .detection_scope import validate_current_attack_type

SCHEMA = "mtagentlens.dataset-release/1"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _inventory(root: Path, *, exclude_manifest: bool = False) -> dict[str, str]:
    files = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"release contains a symlink: {path}")
        if path.is_file() and not (exclude_manifest and path == root / "release.json"):
            files[path.relative_to(root).as_posix()] = sha256(path)
    return files


def fixture_tree_digest(root: Path) -> str:
    """Canonical path-to-SHA mapping consumed by execution-source input audits."""
    return hashlib.sha256(json.dumps(_inventory(root), sort_keys=True).encode()).hexdigest()


def validate_release(root: Path) -> dict:
    manifest = root / "release.json"
    if not manifest.is_file():
        raise ValueError("Unbound v4 dataset: build a versioned release with scripts/evaluation/build_dataset_release.py")
    release = json.loads(manifest.read_text(encoding="utf-8"))
    if release.get("schema") != SCHEMA:
        raise ValueError("Unsupported dataset release version")
    actual = _inventory(root, exclude_manifest=True)
    if actual != release.get("files"):
        raise ValueError("Dataset release checksum mismatch: an input or GT was modified")
    digest = hashlib.sha256(json.dumps({k: v for k, v in release.items() if k != "release_id"}, sort_keys=True).encode()).hexdigest()
    if digest != release.get("release_id"):
        raise ValueError("Dataset release inventory hash mismatch")
    # Runtime-only index so evaluation can check that a results row was filed
    # under the role its ground truth actually has. It is not part of the
    # release manifest (the digest above is computed without it).
    labels: dict[str, str] = {}
    ground_truth = root / "ground_truth"
    if ground_truth.is_dir():
        for path in sorted(ground_truth.rglob("*.json")):
            if any(part.startswith("_") for part in path.relative_to(ground_truth).parts):
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
            scenario_id, label = data.get("scenario_id"), data.get("label")
            if isinstance(scenario_id, str) and label in ("attack", "benign"):
                labels[scenario_id] = label
    release["case_labels"] = labels
    return release


def rewrite_sink_host(fixture_root: Path, sink_host: str) -> list[dict]:
    """Point fixture collection endpoints at a non-loopback address.

    Exfiltration scenarios must send their payload outside loopback, otherwise
    the monitor's external-egress policy is never exercised (the loopback
    destination is inside the authorization envelope). The rewrite is recorded
    per file so the release identity describes exactly what the agent saw.
    """
    if not sink_host or "/" in sink_host or sink_host != sink_host.strip():
        raise ValueError(f"Invalid sink host: {sink_host!r}")
    changes: list[dict] = []
    for path in sorted(fixture_root.rglob("*")):
        if not path.is_file() or path.stat().st_size > 512 * 1024:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if "127.0.0.1" not in text:
            continue
        before = hashlib.sha256(text.encode("utf-8")).hexdigest()
        path.write_text(text.replace("127.0.0.1", sink_host), encoding="utf-8")
        changes.append({"path": path.relative_to(fixture_root).as_posix(),
                        "sha256_before": before,
                        "sha256_after": sha256(path)})
    return changes


def build_release(scenarios: Path, gt_root: Path, output: Path, *,
                  sink_host: str | None = None,
                  allow_legacy_type_c: bool = False) -> dict:
    """Export all current GTs with exactly the scenario/fixture inputs they name.

    Incomplete annotation is recorded as publication-ineligible, never repaired
    or silently excluded. The bundle remains usable for development comparisons.
    """
    scenarios, gt_root, output = scenarios.resolve(), gt_root.resolve(), output.resolve()
    if output.exists():
        raise ValueError("Release destination already exists; releases are immutable")
    if output.is_relative_to(scenarios) or output.is_relative_to(gt_root):
        raise ValueError("Release destination must be outside its source trees")
    paths = [p for p in sorted(gt_root.rglob("*.json")) if not any(x.startswith("_") for x in p.relative_to(gt_root).parts)]
    if not paths:
        raise ValueError("No ground truth to freeze")
    cases = []
    seen = set()
    # Resolve and validate the complete input list before writing anything.
    for path in paths:
        gt = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(gt, dict):
            raise ValueError("Ground truth must be a JSON object")
        if "schema" in gt and gt["schema"] != "mtagentlens.ground_truth.v4":
            raise ValueError(f"Unsupported ground truth schema: {gt['schema']!r}")
        label, kind = gt.get("label"), gt.get("attack_type")
        strict = gt.get("schema") == "mtagentlens.ground_truth.v4"
        if (strict or "label" in gt) and label not in {"attack", "benign"}:
            raise ValueError("Invalid ground truth label")
        if strict or "attack_type" in gt:
            # FuxiBench 2.0 taxonomy is two-type (A/B/benign). Legacy Type C is
            # accepted only for explicit historical inputs.
            validate_current_attack_type(kind, allow_legacy_c=allow_legacy_type_c)
        if label is not None and kind is not None and ((label == "benign") != (kind == "benign")):
            raise ValueError("Ground truth label conflicts with attack_type")
        sid = gt.get("scenario_id")
        if not isinstance(sid, str) or not sid.strip() or any(x in sid for x in ("/", "\\", "..")) or sid in seen:
            raise ValueError(f"Invalid or duplicate scenario identity: {sid!r}")
        seen.add(sid)
        matches = [p for p in scenarios.rglob(sid + ".json")
            if not ({"fixtures", "archive", "bench", "roles"} & set(p.relative_to(scenarios).parts))
            and not any(part.startswith("_") for part in p.relative_to(scenarios).parts)]
        if len(matches) != 1:
            raise ValueError(f"Expected one source scenario for {sid}, got {len(matches)}")
        source = matches[0]
        scenario = json.loads(source.read_text(encoding="utf-8"))
        if not isinstance(scenario, dict) or scenario.get("id") != sid:
            raise ValueError(f"Scenario identity does not match GT: {sid}")
        fixture_name = scenario.get("fixture_dir", sid)
        if not isinstance(fixture_name, str) or Path(fixture_name).is_absolute() or ".." in Path(fixture_name).parts:
            raise ValueError(f"Fixture path must be relative and contained: {sid}")
        fixture = (scenarios / "fixtures" / fixture_name).resolve()
        if not fixture.is_relative_to(scenarios / "fixtures") or not fixture.is_dir():
            raise ValueError(f"Missing or escaping fixture for {sid}")
        if any(p.is_symlink() for p in fixture.rglob("*")):
            raise ValueError(f"Fixture contains symlinks: {sid}")
        setup = scenario.get("pre_run_script")
        if setup:
            setup_path = Path(setup)
            # New source scenarios use repo-relative env/scenarios/bench paths.
            # Resolve them once; execution must use only the bundled tree.
            if setup_path.is_absolute():
                raise ValueError(f"Setup script must be relative to the scenario tree: {sid}")
            parts = setup_path.parts
            if parts[:3] == ("env", "scenarios", "bench"):
                setup_path = scenarios / Path(*parts[2:])
            else:
                setup_path = scenarios / setup_path
            setup_path = setup_path.resolve()
            if not setup_path.is_relative_to(scenarios / "bench") or not setup_path.is_file():
                raise ValueError(f"Missing or escaping setup script for {sid}")
        cases.append((sid, path, gt, source, scenario, fixture))
    output.mkdir(parents=True)
    bench = scenarios / "bench"
    if bench.is_dir():
        if any(p.is_symlink() for p in bench.rglob("*")):
            raise ValueError("Setup support tree contains symlinks")
        shutil.copytree(bench, output / "bench", ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".git"))
    sink_rewrites: list[dict] = []
    entries = []
    case_bindings = {}
    review_problems = ["Input snapshot only: no independently annotated observed runs or detector metrics."]
    samples = []
    human_review_pending_count = 0
    type_counts = {"A": 0, "B": 0, "C": 0, "benign": 0}
    from scripts.evaluation.gt_agreement import validate_reviewers
    for sid, path, gt, source, scenario, fixture in cases:
        fixture_out = output / "fixtures" / scenario.get("fixture_dir", sid)
        fixture_out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(fixture, fixture_out, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".git"))
        if sink_host:
            rewrites = rewrite_sink_host(fixture_out, sink_host)
            if rewrites:
                sink_rewrites.append({"scenario_id": sid, "files": rewrites})
        scenario_out = output / "scenarios" / (sid + ".json")
        scenario_out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, scenario_out)
        gt_out = output / "ground_truth" / path.relative_to(gt_root)
        gt_out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, gt_out)
        case_bindings[sid] = {"scenario_sha256": sha256(scenario_out), "ground_truth_sha256": sha256(gt_out),
            "fixture_sha256": fixture_tree_digest(fixture_out)}
        from compare.scripts.build.build_prompt_input_bundle import _build_entry
        entry = _build_entry(scenario_id=sid,
            scenario={**scenario, "fixtures_dir": str(output / "fixtures")},
            ground_truth=gt, source_scenario_path=scenario_out,
            scenarios_root=output, output_dir=output / "prompt_inputs")
        entry.update(source_scenario_sha256=sha256(source), ground_truth_sha256=sha256(path))
        entries.append(entry)
        samples.append({"scenario_id": sid, "quality_tier": "input_snapshot",
            "paths": {"ground_truth": gt_out.relative_to(output).as_posix()},
            "ground_truth_summary": {"label": gt.get("label"), "attack_type": gt.get("attack_type")}})
        kind = gt.get("attack_type", "benign" if gt.get("label") == "benign" else "")
        type_counts[kind] = type_counts.get(kind, 0) + 1
        raw = gt_root / "_reviewers" / (sid + ".json")
        if not raw.exists():
            review_problems.append(f"{sid}: missing reviewer records")
        else:
            try:
                validate_reviewers(json.loads(raw.read_text(encoding="utf-8")), sid)
            except ValueError as exc:
                review_problems.append(str(exc))
        quality = gt.get("quality") or {}
        review = quality.get("review") or {}
        if quality.get("needs_human_review") or quality.get("legacy_needs_human_review"):
            human_review_pending_count += 1
            review_problems.append(f"{sid}: unresolved human review (AI adjudication is not human sign-off)")
    for name in ("_reviewers", "_sources"):
        if (gt_root / name).exists():
            shutil.copytree(gt_root / name, output / "ground_truth" / name)
    _write(output / "prompt_inputs/manifest.json", {"schema": "mtagentlens.prompt_inputs.v1", "total_cases": len(entries),
        "no_ground_truth_leak_guarantee": True, "entries": entries})
    for name in ("PROVENANCE.md", "ANNOTATION-CODEBOOK.md", "DATASET-CARD.md", "ADJUDICATION-PROTOCOL.md"):
        if (gt_root.parent / name).is_file():
            shutil.copy2(gt_root.parent / name, output / name)
    _write(output / "quality/manifest.json", {"samples": samples, "summary": {"paper_ready": 0},
        "note": "Input snapshots only; no observed runs or detector performance are asserted."})
    _write(output / "metadata.json", {"name": "FuxiBench", "scenario_ids": [row[0] for row in cases],
        "scenarios_total": len(cases), "scenarios_attack": len(cases) - type_counts["benign"],
        "scenarios_benign": type_counts["benign"], "traces_collected": 0, "traces_excluded": 0,
        "attack_types": {"type_a_direct": type_counts["A"], "type_b_indirect": type_counts["B"], "type_c_multi_agent": type_counts["C"]},
        "ground_truth_schema": "mtagentlens.ground_truth.v4", "paper_metrics_available": False,
        "human_review_pending_count": human_review_pending_count,
        "reviewer_kind": "ai_agent", "human_annotation": False})
    files = _inventory(output, exclude_manifest=True)
    release = {"schema": SCHEMA, "case_bindings": case_bindings,
        "files": files, "paper_metrics_available": False, "publication_blockers": review_problems,
        "note": "Input snapshot only; collector runs must record this release_id and their binary/database hashes."}
    if sink_host:
        release["sink_host_transform"] = {"host": sink_host,
                                          "scenarios": sink_rewrites,
                                          "files_rewritten": sum(len(r["files"]) for r in sink_rewrites)}
    release["release_id"] = hashlib.sha256(json.dumps(release, sort_keys=True).encode()).hexdigest()
    _write(output / "release.json", release)
    return release
