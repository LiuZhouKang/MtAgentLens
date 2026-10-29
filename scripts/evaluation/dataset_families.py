#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Group FuxiBench scenarios into conservative variant families.

Why this exists: an experimental split must not put two variants of the same
task template, fixture tree or explicit attack mechanism on opposite sides.
Grouping is deliberately conservative: scenarios are unioned only by an exact
normalized task signature, an exact normalized fixture signature, or an exact
non-empty mechanism fingerprint. Labels, categories and attack types are never
inputs, and a scenario without a match is reported as an unestablished
singleton rather than as a semantically independent case.

Usage:
    python3 scripts/evaluation/dataset_families.py \
        --dataset datasets/FuxiBench \
        --scenarios env/scenarios \
        --out local/workpackages/2026-10-25-ai-dataset/families/families.json
    python3 scripts/evaluation/dataset_families.py ... --verify
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import unicodedata
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable

SCHEMA = "mtagentlens.dataset-families/1"
SPLIT_SALT = SCHEMA
REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_REL = Path("prompt_inputs") / "manifest.json"
PATH_KEYS = ("fixture_dir", "fixtures_dir", "_original_fixture_dir")


class FamilyError(ValueError):
    """Inputs cannot be grouped reproducibly."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def display_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return resolved.as_posix()


# Documented normalization: scrub only machine-generated values. Operators,
# case, punctuation and the meaningful tail of a path are preserved.
_TEMP_ID_RE = re.compile(
    r"(?<![0-9a-f])([A-Za-z][A-Za-z0-9_.-]{0,40}-)([0-9a-f]{6,8})(?![0-9a-f])"
)
_ABS_PATH_RE = re.compile(
    r"(?<![\w.])/(?:home|root|Users|tmp|var|opt|etc|usr|srv|mnt|proc|dev|sys|bin|sbin|lib|lib64|run|boot|media)"
    r"(?:/[A-Za-z0-9_.@%+-]+)+"
)
_LOOPBACK_RE = re.compile(
    r"\b(?:127(?:\.\d{1,3}){3}|0\.0\.0\.0|localhost|\[::1\])(?::\d{1,5})?\b",
    re.IGNORECASE,
)
_UUID_RE = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)
_TOKEN_RE = re.compile(r"\b(?:[0-9a-f]{16,}|[A-Za-z0-9+/=_-]{40,})\b")


def normalize_text(text: str, *, scenario_id: str, fixture_dir: str | None = None) -> str:
    """Return the documented canonical form used by every signature."""

    def _abs_path_marker(match: re.Match[str]) -> str:
        parts = [part for part in match.group(0).split("/") if part]
        tail = "/".join(parts[-2:])
        tail = tail.replace(scenario_id, "<SCENARIO_ID>")
        tail = tail.replace(scenario_id.replace("-", "_"), "<SCENARIO_ID>")
        tail = _TEMP_ID_RE.sub(r"\1<ID>", tail)
        if tail == "<SCENARIO_ID>":
            tail = "generated"
        elif tail.startswith("<SCENARIO_ID>/"):
            tail = tail.split("/", 1)[1]
        return f"<ABS_PATH:{tail}>"

    text = unicodedata.normalize("NFKC", text).replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("{{FIXTURE_DIR}}", "<FIXTURE_DIR>")
    if fixture_dir:
        text = text.replace(fixture_dir, "<FIXTURE_DIR>")
    text = _ABS_PATH_RE.sub(_abs_path_marker, text)
    text = text.replace(scenario_id, "<SCENARIO_ID>")
    text = text.replace(scenario_id.replace("-", "_"), "<SCENARIO_ID>")
    text = _TEMP_ID_RE.sub(r"\1<ID>", text)
    text = _UUID_RE.sub("<UUID>", text)
    text = _LOOPBACK_RE.sub("<LOOPBACK>", text)
    text = _TOKEN_RE.sub("<TOKEN>", text)
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def canonical_scenario_sha256(path: Path) -> str:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise FamilyError(f"scenario is not a JSON object: {path}")
    for key in PATH_KEYS:
        if key in data:
            data[key] = "{{FIXTURE_DIR}}"
    blob = json.dumps(
        data, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return sha256_bytes(blob)


def resolve_fixture_dir(scenario: dict[str, Any], scenarios_root: Path) -> Path | None:
    name = scenario.get("fixture_dir")
    if not isinstance(name, str) or not name:
        return None
    configured = scenario.get("fixtures_dir")
    root = Path(configured) if isinstance(configured, str) and configured else scenarios_root / "fixtures"
    if not root.is_absolute():
        root = scenarios_root / root
    root = root.resolve()
    candidate = (root / name).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise FamilyError(f"fixture_dir escapes fixtures root: {name}") from exc
    if candidate.is_dir():
        return candidate
    fallback = (scenarios_root / "fixtures").resolve()
    fallback_candidate = (fallback / name).resolve()
    try:
        fallback_candidate.relative_to(fallback)
    except ValueError as exc:
        raise FamilyError(f"fixture_dir escapes fixtures root: {name}") from exc
    return fallback_candidate if fallback_candidate.is_dir() else None


def collect_fixture_files(fixture_root: Path) -> list[Path]:
    files = []
    for path in sorted(fixture_root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(fixture_root)
        if "__pycache__" in relative.parts or path.suffix in {".pyc", ".pyo"}:
            continue
        files.append(path)
    return files


@dataclass(frozen=True)
class MechanismRule:
    atom: str
    description: str
    predicate: Callable[["ScenarioRecord"], bool]


def _is_direct_runbook(record: "ScenarioRecord") -> bool:
    task = " ".join(record.task_text.lower().split())
    if "code block" not in task:
        return False
    if not re.search(r"\b(?:execute|run)\s+(?:every|all)\b", task):
        return False
    if not any(path == "docs.md" for path, _digest in record.fixture_files):
        return False
    return "```" in record.fixture_text


MECHANISM_RULES: tuple[MechanismRule, ...] = (
    MechanismRule(
        atom="runbook_codeblock_exec",
        description=(
            "task tells the agent to execute all code blocks of the fixture "
            "docs.md runbook, and the fixture ships fenced code blocks"
        ),
        predicate=_is_direct_runbook,
    ),
)


@dataclass(frozen=True)
class ScenarioRecord:
    scenario_id: str
    path: Path
    data: dict[str, Any]
    scenario_sha256: str
    canonical_sha256: str
    manifest_sha256: str
    fixture_dir: str | None
    fixture_path: Path | None
    fixture_files: tuple[tuple[str, str], ...]
    fixture_signature: str
    task_signature: str
    task_text: str
    fixture_text: str
    mechanism: tuple[str, ...]


def _read_scenario_records(
    dataset: Path, scenarios_root: Path
) -> tuple[list[ScenarioRecord], dict[str, Any], str, Path]:
    manifest_path = dataset / MANIFEST_REL
    if not manifest_path.is_file():
        raise FamilyError(f"missing prompt-input manifest: {manifest_path}")
    manifest_raw = manifest_path.read_bytes()
    manifest = json.loads(manifest_raw.decode("utf-8"))
    entries = manifest.get("entries")
    if not isinstance(entries, list) or not entries:
        raise FamilyError("prompt-input manifest has no entries")
    total_cases = manifest.get("total_cases")
    if total_cases is not None and total_cases != len(entries):
        raise FamilyError(
            f"manifest total_cases={total_cases} but carries {len(entries)} entries"
        )

    records: list[ScenarioRecord] = []
    seen: set[str] = set()
    for entry in sorted(entries, key=lambda item: str(item.get("scenario_id", ""))):
        if not isinstance(entry, dict):
            raise FamilyError("manifest entry is not an object")
        scenario_id = entry.get("scenario_id")
        if not isinstance(scenario_id, str) or not scenario_id:
            raise FamilyError("manifest entry has no scenario_id")
        if scenario_id in seen:
            raise FamilyError(f"duplicate scenario_id in manifest: {scenario_id}")
        seen.add(scenario_id)

        raw_path = Path(str(entry.get("source_scenario_path", "")))
        if not raw_path.is_absolute():
            # Current packages are self-contained; legacy manifests used repo-relative paths.
            packaged = dataset / raw_path
            raw_path = packaged if packaged.is_file() else REPO_ROOT / raw_path
        if not raw_path.is_file():
            raise FamilyError(f"missing scenario file for {scenario_id}: {raw_path}")
        raw = raw_path.read_bytes()
        scenario_sha = sha256_bytes(raw)
        recorded_sha = entry.get("source_scenario_sha256")
        if recorded_sha and recorded_sha != scenario_sha:
            raise FamilyError(
                f"source hash mismatch for {scenario_id}: manifest {recorded_sha} != current {scenario_sha}"
            )
        canonical_sha = canonical_scenario_sha256(raw_path)
        recorded_canonical = entry.get("source_scenario_canonical_sha256")
        if recorded_canonical and recorded_canonical != canonical_sha:
            raise FamilyError(
                f"canonical source hash mismatch for {scenario_id}: "
                f"manifest {recorded_canonical} != current {canonical_sha}"
            )
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict) or data.get("id") != scenario_id:
            raise FamilyError(f"scenario id mismatch for {scenario_id} in {raw_path}")

        fixture_name = data.get("fixture_dir") if isinstance(data.get("fixture_dir"), str) else None
        fixture_path = resolve_fixture_dir(data, scenarios_root)
        fixture_files: list[tuple[str, str]] = []
        signature_files: list[tuple[str, str]] = []
        fixture_texts: list[str] = []
        if fixture_path is not None:
            for path in collect_fixture_files(fixture_path):
                relative = path.relative_to(fixture_path).as_posix()
                signature_path = normalize_text(
                    relative, scenario_id=scenario_id, fixture_dir=fixture_name
                )
                raw_file = path.read_bytes()
                if b"\x00" in raw_file:
                    digest = sha256_bytes(raw_file)
                else:
                    normalized = normalize_text(
                        raw_file.decode("utf-8", errors="replace"),
                        scenario_id=scenario_id,
                        fixture_dir=fixture_name,
                    )
                    digest = sha256_bytes(normalized.encode("utf-8"))
                    fixture_texts.append(normalized)
                fixture_files.append((relative, digest))
                signature_files.append((signature_path, digest))
            fixture_files.sort()
            signature_files.sort()
        fixture_signature = sha256_bytes(
            json.dumps(signature_files, separators=(",", ":")).encode("utf-8")
        )

        agents = data.get("agents")
        if not isinstance(agents, list):
            raise FamilyError(f"scenario {scenario_id} has no agents list")
        task_text = "\n\x1e\n".join(str(agent.get("task", "")) for agent in agents if isinstance(agent, dict))
        task_signature = sha256_bytes(
            normalize_text(
                task_text, scenario_id=scenario_id, fixture_dir=fixture_name
            ).encode("utf-8")
        )

        record = ScenarioRecord(
            scenario_id=scenario_id,
            path=raw_path,
            data=data,
            scenario_sha256=scenario_sha,
            canonical_sha256=canonical_sha,
            manifest_sha256=str(recorded_sha or ""),
            fixture_dir=fixture_name,
            fixture_path=fixture_path,
            fixture_files=tuple(fixture_files),
            fixture_signature=fixture_signature,
            task_signature=task_signature,
            task_text=task_text,
            fixture_text="\n".join(fixture_texts),
            mechanism=(),
        )
        atoms = tuple(rule.atom for rule in MECHANISM_RULES if rule.predicate(record))
        record = replace(record, mechanism=atoms)
        records.append(record)

    records.sort(key=lambda item: item.scenario_id)
    return records, manifest, sha256_bytes(manifest_raw), manifest_path


class _UnionFind:
    def __init__(self, values: list[str]) -> None:
        self.parent = {value: value for value in values}

    def find(self, value: str) -> str:
        while self.parent[value] != value:
            self.parent[value] = self.parent[self.parent[value]]
            value = self.parent[value]
        return value

    def union(self, left: str, right: str) -> None:
        left_root, right_root = self.find(left), self.find(right)
        if left_root != right_root:
            self.parent[max(left_root, right_root)] = min(left_root, right_root)


def _bucket_reasons(records: list[ScenarioRecord]) -> list[dict[str, Any]]:
    reasons: list[dict[str, Any]] = []
    channels = (
        ("task_signature_exact", lambda record: record.task_signature),
        ("fixture_signature_exact", lambda record: record.fixture_signature),
        ("mechanism_fingerprint_exact", lambda record: record.mechanism),
    )
    for rule, key_of in channels:
        buckets: dict[Any, list[str]] = {}
        for record in records:
            key = key_of(record)
            if rule == "mechanism_fingerprint_exact" and not key:
                continue
            buckets.setdefault(key, []).append(record.scenario_id)
        for key, members in sorted(buckets.items(), key=lambda item: sorted(item[1])):
            if len(members) < 2:
                continue
            reason: dict[str, Any] = {
                "rule": rule,
                "members": sorted(members),
            }
            if rule == "mechanism_fingerprint_exact":
                reason["atoms"] = list(key)
                reason["signature_sha256"] = sha256_bytes(
                    json.dumps(list(key), separators=(",", ":")).encode("utf-8")
                )
                reason["evidence"] = "; ".join(
                    item.description for item in MECHANISM_RULES if item.atom in key
                )
            else:
                reason["signature_sha256"] = key
                reason["evidence"] = (
                    "normalized agent task text identical"
                    if rule == "task_signature_exact"
                    else "normalized fixture tree bytes and relative paths identical"
                )
            reasons.append(reason)
    return reasons


def _family_anchor(records: list[ScenarioRecord]) -> str:
    task_signatures = {record.task_signature for record in records}
    if len(task_signatures) == 1:
        return f"task:{task_signatures.pop()}"
    fixture_signatures = {record.fixture_signature for record in records}
    if len(fixture_signatures) == 1:
        return f"fixture:{fixture_signatures.pop()}"
    mechanisms = {record.mechanism for record in records}
    if len(mechanisms) == 1 and next(iter(mechanisms)):
        return f"mechanism:{','.join(next(iter(mechanisms)))}"
    member_ids = sorted(record.scenario_id for record in records)
    return "mixed:" + sha256_bytes(",".join(member_ids).encode("utf-8"))


def split_for(family_id: str) -> str:
    digest = sha256_bytes(f"{family_id}:{SPLIT_SALT}".encode("utf-8"))
    return "dev" if int(digest[:8], 16) % 2 == 0 else "test"


def build_payload(dataset_dir: Path, scenarios_root: Path) -> dict[str, Any]:
    dataset = Path(dataset_dir).resolve()
    scenarios = Path(scenarios_root).resolve()
    records, manifest, manifest_sha, manifest_path = _read_scenario_records(
        dataset, scenarios
    )
    records_by_id = {record.scenario_id: record for record in records}

    union_find = _UnionFind([record.scenario_id for record in records])
    reasons = _bucket_reasons(records)
    for reason in reasons:
        members = reason["members"]
        for member in members[1:]:
            union_find.union(members[0], member)

    components: dict[str, list[ScenarioRecord]] = {}
    for record in records:
        components.setdefault(union_find.find(record.scenario_id), []).append(record)

    families: list[dict[str, Any]] = []
    assignments: dict[str, Any] = {}
    for members in sorted(components.values(), key=lambda group: sorted(r.scenario_id for r in group)[0]):
        member_ids = sorted(record.scenario_id for record in members)
        member_set = set(member_ids)
        family_reasons = [
            reason for reason in reasons if set(reason["members"]) <= member_set
        ]
        anchor = _family_anchor(members)
        family_id = "fam-" + sha256_bytes(anchor.encode("utf-8"))[:16]
        split = split_for(family_id)
        exact_rules = {"task_signature_exact", "fixture_signature_exact"}
        if any(reason["rule"] in exact_rules for reason in family_reasons):
            status = "exact_signature"
            established = True
        elif family_reasons:
            status = "mechanism_fingerprint"
            established = False
        else:
            status = "singleton_unestablished"
            established = False
            family_reasons = [
                {
                    "rule": "no_exact_match",
                    "members": member_ids,
                    "evidence": (
                        "no normalized task/fixture signature or explicit mechanism "
                        "fingerprint matched another scenario"
                    ),
                }
            ]
        model_assisted = any(
            reason["rule"] == "mechanism_fingerprint_exact"
            for reason in family_reasons
        )
        family = {
            "family_id": family_id,
            "family_key": anchor,
            "member_scenario_ids": member_ids,
            "size": len(member_ids),
            "status": status,
            "established": established,
            "model_assisted": model_assisted,
            "human_review": False,
            "split": split,
            "mechanism_fingerprint": sorted(
                {atom for record in members for atom in record.mechanism}
            ),
            "reasons": family_reasons,
        }
        families.append(family)
        for scenario_id in member_ids:
            assignments[scenario_id] = {
                "family_id": family_id,
                "family_key": anchor,
                "split": split,
                "status": status,
                "match_basis": sorted(
                    {
                        reason["rule"]
                        for reason in family_reasons
                        if reason["rule"] != "no_exact_match"
                    }
                ),
                "task_signature_sha256": records_by_id[scenario_id].task_signature,
                "fixture_signature_sha256": records_by_id[scenario_id].fixture_signature,
                "mechanism_fingerprint": list(records_by_id[scenario_id].mechanism),
            }

    family_counts = {
        "family_count": len(families),
        "multi_member_family_count": sum(1 for family in families if family["size"] > 1),
        "singleton_family_count": sum(1 for family in families if family["size"] == 1),
        "max_family_size": max((family["size"] for family in families), default=0),
        "established_family_count": sum(1 for family in families if family["established"]),
        "provisional_family_count": sum(
            1 for family in families if family["status"] == "mechanism_fingerprint"
        ),
    }
    split_counts = {
        name: sum(1 for family in families if family["split"] == name)
        for name in ("dev", "test")
    }
    atom_counts: dict[str, int] = {}
    for record in records:
        for atom in record.mechanism:
            atom_counts[atom] = atom_counts.get(atom, 0) + 1

    corpus_sha = sha256_bytes(
        json.dumps(
            [
                [record.scenario_id, record.scenario_sha256, record.fixture_signature]
                for record in records
            ],
            separators=(",", ":"),
        ).encode("utf-8")
    )

    sources = {
        "manifest": {
            "path": display_path(manifest_path),
            "sha256": manifest_sha,
            "total_cases": manifest.get("total_cases"),
        },
        "scenarios": [
            {
                "scenario_id": record.scenario_id,
                "path": display_path(record.path),
                "sha256": record.scenario_sha256,
                "canonical_sha256": record.canonical_sha256,
                "manifest_sha256": record.manifest_sha256,
                "fixture_dir": record.fixture_dir,
                "fixture_tree_sha256": record.fixture_signature,
                "fixture_files": [
                    {"path": path, "sha256": digest}
                    for path, digest in record.fixture_files
                ],
            }
            for record in records
        ],
    }

    return {
        "schema": SCHEMA,
        "dataset": str(manifest.get("dataset") or "FuxiBench"),
        "generated_by": "scripts/evaluation/dataset_families.py",
        "scope": {
            "dataset": display_path(dataset),
            "scenarios_root": display_path(scenarios),
            "manifest": display_path(manifest_path),
            "scenario_count": len(records),
            "corpus_sha256": corpus_sha,
            "corpus_scope_note": (
                "family ids are deterministic fingerprints of this corpus snapshot; "
                "record corpus_sha256 when comparing two runs"
            ),
        },
        "policy": {
            "normalization": {
                "version": "1",
                "steps": [
                    "Unicode NFKC, CRLF/CR to LF",
                    "{{FIXTURE_DIR}} and the scenario fixture_dir literal become <FIXTURE_DIR>",
                    "absolute system paths under /home /root /Users /tmp /var /opt /etc /usr /srv /mnt /proc /dev /sys /bin /sbin /lib /lib64 /run /boot /media keep their last two segments as <ABS_PATH:tail>",
                    "the scenario id and its underscore form become <SCENARIO_ID>",
                    "6-8 hex temp suffixes on dashed identifiers become <ID>",
                    "UUIDs become <UUID>; loopback host:port becomes <LOOPBACK>; 16+ hex or 40+ token-like runs become <TOKEN>",
                    "spaces/tabs collapse; line breaks and shell operators are preserved",
                ],
                "preserved": (
                    "case, punctuation, shell operators (|, >, >>, &&, ;, $()), "
                    "command names, remote hostnames, and the last two path segments"
                ),
                "not_used": (
                    "category, attack_type, label, risk_level, variant_of, detector "
                    "results, model performance, runtime stores"
                ),
            },
            "union_rules": [
                {
                    "rule": "task_signature_exact",
                    "requires": "identical normalized agent task text",
                },
                {
                    "rule": "fixture_signature_exact",
                    "requires": (
                        "identical normalized fixture relative paths and file bytes; "
                        "binary files are hashed as-is"
                    ),
                },
                {
                    "rule": "mechanism_fingerprint_exact",
                    "requires": (
                        "identical non-empty set of explicit structural mechanism rules "
                        "(see mechanism_rules); this is AI silver judgement"
                    ),
                },
            ],
            "mechanism_rules": [
                {"atom": rule.atom, "description": rule.description}
                for rule in MECHANISM_RULES
            ],
            "consistency": (
                "connected components over the union rules; labels/categories alone "
                "never create an edge"
            ),
            "family_id": (
                "fam-<16 hex of sha256(family_key)>; family_key is the shared task "
                "or fixture signature, the mechanism atom set, or a hash of sorted "
                "member ids for mixed components"
            ),
            "source_scope": (
                "source hashes cover the prompt-input manifest, every scenario JSON "
                "and every fixture file; prompt_inputs prompt.txt files are derived "
                "artifacts and are not grouping inputs"
            ),
            "split": (
                f"deterministic parity of sha256(family_id + ':' + '{SPLIT_SALT}'); "
                "every member of a family shares the family split"
            ),
        },
        "human_review": False,
        "model_assisted": any(family["model_assisted"] for family in families),
        "sources": sources,
        "families": families,
        "assignments": assignments,
        "stats": {
            "scenario_count": len(records),
            "family_count": family_counts["family_count"],
            "multi_member_family_count": family_counts["multi_member_family_count"],
            "singleton_family_count": family_counts["singleton_family_count"],
            "max_family_size": family_counts["max_family_size"],
            "established_family_count": family_counts["established_family_count"],
            "provisional_family_count": family_counts["provisional_family_count"],
            "split_family_counts": split_counts,
            "split_scenario_counts": {
                name: sum(
                    1 for assignment in assignments.values() if assignment["split"] == name
                )
                for name in ("dev", "test")
            },
            "mechanism_atom_counts": atom_counts,
        },
        "limitations": [
            (
                "Absence of normalized task/fixture matches is not proof of semantic "
                "independence; a singleton may still share an unmodelled mechanism "
                "with another scenario."
            ),
            (
                "Mechanism-fingerprint families are AI silver labels derived from "
                "structural rules, not human adjudication; they are marked "
                "model_assisted and not established."
            ),
            (
                "Group identity is scoped to the recorded corpus snapshot "
                "(scope.corpus_sha256); adding or editing scenarios can change a "
                "singleton's family id."
            ),
            (
                "Fixture signatures cover checked-in fixture bytes only; runtime "
                "state, generated containers and network peers are out of scope."
            ),
            (
                "The dev/test split is deterministic for reproducibility, not "
                "balanced by construction."
            ),
            (
                "This artifact must not be presented as human review, ground truth "
                "or evidence of semantic independence."
            ),
        ],
    }


def write_payload(payload: dict[str, Any], out_path: Path) -> None:
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    temporary = out.with_name(out.name + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, out)


def verify_payload(dataset_dir: Path, scenarios_root: Path, out_path: Path) -> list[str]:
    out = Path(out_path)
    if not out.is_file():
        return [f"missing families file: {out}"]
    errors: list[str] = []
    try:
        payload = json.loads(out.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [f"invalid JSON in {out}: {exc}"]
    if not isinstance(payload, dict):
        return [f"{out} is not a JSON object"]
    if payload.get("schema") != SCHEMA:
        errors.append(f"schema mismatch: expected {SCHEMA}, got {payload.get('schema')!r}")

    try:
        expected = build_payload(dataset_dir, scenarios_root)
    except FamilyError as exc:
        return errors + [f"source hash/rebuild failed: {exc}"]

    expected_ids = set(expected["assignments"])
    got_assignments = payload.get("assignments")
    if not isinstance(got_assignments, dict):
        return errors + ["assignments is not an object"]
    got_ids = set(got_assignments)
    if got_ids != expected_ids:
        errors.append(
            "id coverage mismatch: missing "
            f"{sorted(expected_ids - got_ids)}; unexpected {sorted(got_ids - expected_ids)}"
        )
    if len(got_ids) != expected["scope"]["scenario_count"]:
        errors.append(
            f"scenario count mismatch: stored {len(got_ids)}, "
            f"expected {expected['scope']['scenario_count']}"
        )

    if payload.get("scope", {}).get("corpus_sha256") != expected["scope"]["corpus_sha256"]:
        errors.append("corpus scope hash mismatch: inputs changed since the file was built")
    if payload.get("stats") != expected["stats"]:
        errors.append("stats mismatch: stored stats do not describe this corpus")
    if payload.get("human_review") is not False:
        errors.append("human_review must be false")
    if payload.get("model_assisted") != expected["model_assisted"]:
        errors.append("model_assisted flag does not match the documented judgement")

    got_families: dict[str, dict[str, Any]] = {}
    for family in payload.get("families", []):
        if not isinstance(family, dict):
            errors.append("family entry is not an object")
            continue
        family_id = family.get("family_id")
        if family_id in got_families:
            errors.append(f"duplicate family id: {family_id}")
        got_families[family_id] = family
    expected_families = {family["family_id"]: family for family in expected["families"]}
    if set(got_families) != set(expected_families):
        errors.append(
            "family id set mismatch: missing "
            f"{sorted(set(expected_families) - set(got_families))}; unexpected "
            f"{sorted(set(got_families) - set(expected_families))}"
        )

    for family_id in sorted(set(got_families) & set(expected_families)):
        stored, wanted = got_families[family_id], expected_families[family_id]
        for key in ("family_key", "member_scenario_ids", "status", "split", "established"):
            if stored.get(key) != wanted.get(key):
                errors.append(
                    f"family {family_id} {key} mismatch: stored {stored.get(key)!r}, "
                    f"expected {wanted.get(key)!r}"
                )
        members = stored.get("member_scenario_ids")
        if not isinstance(members, list) or not members:
            errors.append(f"family {family_id} has no members")
            continue
        splits = {
            (got_assignments.get(member) if isinstance(got_assignments.get(member), dict) else {}).get("split")
            for member in members
        }
        if len(splits) != 1:
            errors.append(f"family {family_id} members do not share one split: {splits}")
        if stored.get("split") not in splits:
            errors.append(f"family {family_id} split disagrees with member assignments")

    for scenario_id in sorted(expected_ids):
        stored = got_assignments.get(scenario_id)
        if not isinstance(stored, dict):
            errors.append(f"assignment {scenario_id} is not an object")
            continue
        wanted = expected["assignments"][scenario_id]
        for key in ("family_id", "split", "status"):
            if stored.get(key) != wanted.get(key):
                errors.append(
                    f"assignment {scenario_id} {key} mismatch: stored "
                    f"{stored.get(key)!r}, expected {wanted.get(key)!r}"
                )

    stored_sources = payload.get("sources") or {}
    manifest_source = stored_sources.get("manifest") or {}
    if manifest_source.get("sha256") != expected["sources"]["manifest"]["sha256"]:
        errors.append("manifest source hash mismatch")
    stored_scenarios = {
        item.get("scenario_id"): item
        for item in stored_sources.get("scenarios", [])
        if isinstance(item, dict)
    }
    for wanted in expected["sources"]["scenarios"]:
        stored = stored_scenarios.get(wanted["scenario_id"])
        if stored is None:
            errors.append(f"missing source record for {wanted['scenario_id']}")
            continue
        for key in ("sha256", "canonical_sha256", "fixture_tree_sha256", "manifest_sha256"):
            if stored.get(key) != wanted[key]:
                errors.append(
                    f"source hash mismatch for {wanted['scenario_id']} {key}: "
                    f"stored {stored.get(key)!r}, expected {wanted[key]!r}"
                )
        if stored.get("fixture_files") != wanted["fixture_files"]:
            errors.append(f"source hash mismatch for {wanted['scenario_id']} fixture files")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, help="FuxiBench dataset root")
    parser.add_argument("--scenarios", required=True, help="scenario root")
    parser.add_argument("--out", required=True, help="families.json path")
    parser.add_argument(
        "--verify", action="store_true", help="verify an existing families.json"
    )
    args = parser.parse_args(argv)
    dataset, scenarios, out = Path(args.dataset), Path(args.scenarios), Path(args.out)

    if args.verify:
        errors = verify_payload(dataset, scenarios, out)
        report = {
            "verify": "failed" if errors else "ok",
            "families_file": str(out),
            "errors": errors,
        }
        if not errors:
            payload = json.loads(out.read_text(encoding="utf-8"))
            report["stats"] = payload.get("stats")
            report["scope"] = payload.get("scope")
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 1 if errors else 0

    try:
        payload = build_payload(dataset, scenarios)
    except FamilyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    write_payload(payload, out)
    print(
        json.dumps(
            {
                "families_file": str(out),
                "schema": payload["schema"],
                "scope": payload["scope"],
                "stats": payload["stats"],
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
