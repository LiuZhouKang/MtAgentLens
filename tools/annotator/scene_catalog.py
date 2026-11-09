"""Read-only FuxiBench scenario catalog for independent scene annotation.

Only scenario definitions, role files, fixture files and bench files are exposed.
Ground truth, category/attack-type metadata, labels, runtime traces and collector
outputs are never part of a catalog record.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

SOURCE_KINDS = ("scenario", "role", "fixture", "bench")
_SCENARIO_ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")

# Fields that describe the scene design itself. Category, attack_type, label,
# risk_level, is_baseline and variant_of are deliberately absent because they
# leak the reference label or its family to an independent rater.
_SAFE_SCENARIO_FIELDS = (
    "id",
    "mode",
    "description",
    "agents",
    "fixture_dir",
    "timeout",
    "permission_mode",
    "model",
    "effort",
    "pre_run_script",
    "execution_probe",
    "multi_agent_pattern",
    "tmux",
    "network_allowlist",
    "network_exceptions",
)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _safe_path(root: Path, relative: object) -> Path:
    if not isinstance(relative, str) or not relative.strip():
        raise ValueError("source path must be a non-empty relative string")
    if "\x00" in relative or "\\" in relative:
        raise ValueError("source path contains an unsupported separator")
    candidate_path = Path(relative.strip())
    if candidate_path.is_absolute() or any(part == ".." for part in candidate_path.parts):
        raise ValueError(f"source path escapes the dataset root: {relative!r}")
    resolved_root = root.resolve()
    candidate = (resolved_root / candidate_path).resolve()
    try:
        candidate.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError(f"source path escapes the dataset root: {relative!r}") from exc
    return candidate


def _sanitize_scenario(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("scenario JSON must be an object")
    clean = {key: payload[key] for key in _SAFE_SCENARIO_FIELDS if key in payload}
    agents = []
    raw_agents = payload.get("agents", [])
    if raw_agents is None:
        raw_agents = []
    if not isinstance(raw_agents, list):
        raise ValueError("scenario agents must be a list")
    for agent in raw_agents:
        if not isinstance(agent, dict):
            raise ValueError("scenario agents must be objects")
        entry = {}
        if isinstance(agent.get("role_file"), str):
            entry["role_file"] = agent["role_file"]
        if isinstance(agent.get("task"), str):
            entry["task"] = agent["task"]
        if entry:
            agents.append(entry)
    if "agents" in payload or agents:
        clean["agents"] = agents
    return clean


def _read_source(path: Path, *, relative: str, kind: str) -> dict:
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        digest = hashlib.sha256(raw).hexdigest()
        text = f"[binary {kind} file: {path.name}; {len(raw)} bytes; sha256={digest}]\n"
    return {
        "path": relative,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "text": text,
        "kind": kind,
    }


def _files_below(root: Path, directory: Path, kind: str) -> list[dict]:
    records: list[dict] = []
    resolved_root = root.resolve()
    for path in sorted(directory.rglob("*"), key=lambda item: item.as_posix()):
        resolved = path.resolve()
        try:
            resolved.relative_to(resolved_root)
        except ValueError as exc:
            raise ValueError(f"source path escapes the dataset root: {path}") from exc
        if not resolved.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        records.append(_read_source(path, relative=relative, kind=kind))
    return records


def _role_source(root: Path, role_file: str) -> dict | None:
    _safe_path(root, role_file)
    for base in ("roles", "scenarios/roles"):
        candidate = _safe_path(root, f"{base}/{role_file}")
        if candidate.is_file():
            return _read_source(candidate, relative=candidate.relative_to(root.resolve()).as_posix(),
                                kind="role")
    return None


def scenario_input_hash(sources: list[dict]) -> str:
    """Stable digest of the sanitized material set, independent of source text."""
    digest = hashlib.sha256()
    for source in sorted(sources, key=lambda item: item["path"]):
        digest.update(source["path"].encode("utf-8"))
        digest.update(b"\0")
        digest.update(source["sha256"].encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def catalog_hash(scenarios: list[dict]) -> str:
    digest = hashlib.sha256()
    for scenario in sorted(scenarios, key=lambda item: item["scenario_id"]):
        digest.update(scenario["scenario_id"].encode("utf-8"))
        digest.update(b"\0")
        digest.update(scenario_input_hash(scenario["sources"]).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def load_scenarios(dataset_root: Path) -> list[dict]:
    """Load all scenario design materials without GT or label-bearing metadata."""
    root = Path(dataset_root).resolve()
    if not root.is_dir():
        raise ValueError(f"dataset root is not a directory: {root}")
    scenario_dir = root / "scenarios"
    if not scenario_dir.is_dir():
        raise ValueError(f"dataset has no scenarios directory: {root}")
    records: list[dict] = []
    seen: set[str] = set()
    for path in sorted(scenario_dir.glob("*.json"), key=lambda item: item.name):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"scenario JSON must be an object: {path.name}")
        scenario_id = payload.get("id")
        if not isinstance(scenario_id, str) or not _SCENARIO_ID.fullmatch(scenario_id):
            raise ValueError(f"invalid scenario id in {path.name}: {scenario_id!r}")
        if scenario_id in seen:
            raise ValueError(f"duplicate scenario id: {scenario_id}")
        seen.add(scenario_id)
        sanitized = _sanitize_scenario(payload)
        scenario_text = json.dumps(
            sanitized, ensure_ascii=False, indent=2, sort_keys=True
        ) + "\n"
        sources = [{
            "path": path.relative_to(root).as_posix(),
            "sha256": _sha256_text(scenario_text),
            "text": scenario_text,
            "kind": "scenario",
        }]
        seen_paths = {sources[0]["path"]}

        def add(source: dict) -> None:
            if source["path"] not in seen_paths:
                seen_paths.add(source["path"])
                sources.append(source)

        for agent in sanitized.get("agents", []):
            role_file = agent.get("role_file")
            if not isinstance(role_file, str) or not role_file.strip():
                continue
            role = _role_source(root, role_file)
            if role is not None:
                add(role)
        fixture_dir = sanitized.get("fixture_dir")
        if isinstance(fixture_dir, str) and fixture_dir.strip():
            candidate = _safe_path(root, f"fixtures/{fixture_dir}")
            if candidate.is_dir():
                for source in _files_below(root, candidate, "fixture"):
                    add(source)
        pre_run_script = sanitized.get("pre_run_script")
        if isinstance(pre_run_script, str) and pre_run_script.strip():
            pre_run = _safe_path(root, pre_run_script)
            if pre_run.is_file():
                relative = pre_run.relative_to(root).as_posix()
                add(_read_source(pre_run, relative=relative, kind="bench"))
        bench_dir = _safe_path(root, f"bench/{scenario_id}")
        if bench_dir.is_dir():
            for source in _files_below(root, bench_dir, "bench"):
                add(source)
        records.append({
            "scenario_id": scenario_id,
            "sources": sources,
        })
    ids = [record["scenario_id"] for record in records]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate scenario ids in catalog")
    return records
