# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Repair provenance hashes after path-neutralization in the FuxiBench package.

Sanitization rewrites host paths inside JSON/SQLite evidence, so a hash that was
recorded for the original bytes no longer matches the bundled bytes. This script
keeps both: it recomputes hashes against the bundled file and stores the prior
value as `original_sha256` / `original_*` and a rewrite note. Idempotent.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PKG = ROOT / "datasets/FuxiBench"
PREFIX_MAP = [
    ("evidence/v5/gt/runs/full-93/", "evidence/v5/"),
    ("evidence/v5/gt/runs/", "evidence/v5/"),
]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fix_path(value: str) -> str:
    for old, new in PREFIX_MAP:
        if value.startswith(old):
            return new + value[len(old):]
    return value


def walk_refs(value):
    changed = False
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if key == "path" and isinstance(item, str):
                new = fix_path(item)
                if new != item:
                    changed = True
                out[key] = new
            elif key in {"sha256", "origin_sha256"} and isinstance(item, str):
                out[key] = item
            else:
                child, child_changed = walk_refs(item)
                out[key] = child
                changed = changed or child_changed
        return out, changed
    if isinstance(value, list):
        out = []
        for item in value:
            child, child_changed = walk_refs(item)
            out.append(child)
            changed = changed or child_changed
        return out, changed
    return value, False


def repair_ref_dict(ref: dict) -> None:
    path = ref.get("path")
    if not isinstance(path, str):
        return
    target = PKG / fix_path(path)
    if not target.resolve().is_relative_to(PKG.resolve()) or not target.is_file():
        return
    old = ref.get("sha256")
    new = sha(target)
    if old != new:
        ref.setdefault("original_sha256", old)
        ref["sha256"] = new
    ref["path"] = fix_path(path)


def repair_goal_v5() -> str:
    path = PKG / "evidence/v5/gt-v5.json"
    data = json.loads(path.read_text())
    data, _ = walk_refs(data)
    raw = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    path.write_text(raw)
    # second pass: hash every reference against the bundled file
    data = json.loads(path.read_text())
    for ref in data.get("references", []):
        if isinstance(ref, dict):
            repair_ref_dict(ref)
    for record in data.get("records", []):
        for ref in record.get("label_annotation", {}).get("evidence", {}).get("references", []):
            if isinstance(ref, dict):
                repair_ref_dict(ref)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    return sha(path)


def repair_gt() -> None:
    v5_path = PKG / "evidence/v5/gt-v5.json"
    v5_sha = sha(v5_path)
    v5 = json.loads(v5_path.read_text())
    by_sid = {r["scenario_id"]: r for r in v5.get("records", [])}
    for path in sorted((PKG / "ground_truth").glob("*/*.json")):
        if path.parent.name.startswith("_"):
            continue
        record = json.loads(path.read_text())
        la = record.setdefault("label_annotation", {})
        source = la.setdefault("source", {})
        old = source.get("sha256")
        if old != v5_sha:
            source.setdefault("original_sha256", old)
            source["sha256"] = v5_sha
        source["path"] = "evidence/v5/gt-v5.json"
        source["content_rewrite_note"] = (
            "Bundled evidence bytes were path-neutralized for portability; "
            "original_sha256 records the pre-rewrite value."
        )
        v5_record = by_sid.get(record["scenario_id"], {})
        if v5_record:
            canonical = hashlib.sha256(
                json.dumps({k: v for k, v in v5_record.items() if k != "record_sha256"},
                           ensure_ascii=False, sort_keys=True).encode()
            ).hexdigest()
            old_record = la.get("record_sha256") or source.get("record_sha256")
            if old_record and old_record != canonical:
                la.setdefault("original_record_sha256", old_record)
            la["record_sha256"] = canonical
        contract = la.get("v4_detection_contract")
        if isinstance(contract, dict) and contract.get("path"):
            rel = contract["path"]
            if not rel.startswith("evidence/legacy/v4/ground_truth/"):
                contract["path"] = "evidence/legacy/v4/ground_truth/" + rel.lstrip("/")
            target = PKG / contract["path"]
            if target.is_file():
                old = contract.get("sha256")
                new = sha(target)
                if old != new:
                    contract.setdefault("original_sha256", old)
                    contract["sha256"] = new
        for ref in la.get("evidence", {}).get("references", []):
            if isinstance(ref, dict):
                ref["path"] = fix_path(ref.get("path", ""))
                repair_ref_dict(ref)
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")


def repair_manifest() -> None:
    path = PKG / "evidence/validation/post-fix/MANIFEST.json"
    if not path.exists():
        return
    data = json.loads(path.read_text())
    for entry in data.get("files", []):
        target = PKG / "evidence/validation/post-fix" / entry["path"]
        if target.is_file():
            old = entry.get("sha256")
            new = sha(target)
            if old != new:
                entry.setdefault("original_sha256", old)
                entry["sha256"] = new
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")




def resolve_ref_path(value: str) -> str | None:
    # A same-named file is not evidence of source identity. Only explicit
    # historical prefix mappings may rebase a reference.
    resolved = fix_path(value)
    candidate = PKG / resolved
    if not candidate.resolve().is_relative_to(PKG.resolve()):
        return None
    return resolved if candidate.is_file() else None


def rebase_and_hash(value):
    if isinstance(value, dict):
        if isinstance(value.get("path"), str) and "sha256" in value:
            resolved = resolve_ref_path(value["path"])
            if resolved is None:
                raise ValueError(f"unresolved provenance reference: {value['path']}")
            if resolved:
                if value["path"] != resolved:
                    value.setdefault("original_path", value["path"])
                value["path"] = resolved
                target = PKG / resolved
                old = value.get("sha256")
                new = sha(target)
                if old != new:
                    value.setdefault("original_sha256", old)
                    value["sha256"] = new
        for item in value.values():
            rebase_and_hash(item)
    elif isinstance(value, list):
        for item in value:
            rebase_and_hash(item)

def main() -> int:
    repair_goal_v5()
    # Finalize the source evidence before records bind its file/record hashes.
    v5_path = PKG / "evidence/v5/gt-v5.json"
    data = json.loads(v5_path.read_text())
    rebase_and_hash(data)
    v5_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    repair_gt()
    for path in sorted((PKG / "ground_truth").glob("*/*.json")):
        if path.parent.name.startswith("_"):
            continue
        data = json.loads(path.read_text())
        rebase_and_hash(data)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    repair_manifest()
    print(json.dumps({"gt_v5_sha256": sha(v5_path), "gt_records": len([
        path for path in (PKG / "ground_truth").glob("*/*.json")
        if not path.parent.name.startswith("_")])}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
