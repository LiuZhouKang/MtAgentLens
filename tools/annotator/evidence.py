"""Freeze raw evidence bytes into a portable, integrity-checked bundle.

Source kind/producer are producer declarations, not authenticated independence:
a bundle records declared lineage and byte integrity, not human provenance, and
generates no detector-derived identities or labels.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import uuid
from pathlib import Path

from compare.tools.dataset_release import validate_release

SCHEMA = "mtagentlens.evidence-bundle/v1"
_MANIFEST_KEYS = {"schema", "binding", "sources", "actors", "events", "evidence_bundle_hash"}
_BINDING_KEYS = {"data_release_id", "scenario_id", "run_id", "source_run_uuid", "codebook_version"}
_BUILD_SOURCE_KEYS = {"path", "kind", "producer"}
_SOURCE_KEYS = {"path", "sha256", "kind", "producer"}
_EVENT_TEXT_KEYS = {"event_id", "source_id", "record_id", "timestamp", "clock_domain", "derivation"}
_EVENT_KEYS = _EVENT_TEXT_KEYS | {"byte_offset", "byte_length", "record_sha256"}
_KINDS = {"controlled_runner", "raw_capture", "human_record"}
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")


def _require(condition: object, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_binding(binding: object) -> None:
    _require(isinstance(binding, dict) and set(binding) == _BINDING_KEYS,
             "binding must have exactly the required keys")
    _require(all(_text(binding[key]) for key in _BINDING_KEYS), "binding values must be non-empty strings")
    _require(_HEX64.fullmatch(binding["data_release_id"]) is not None, "data_release_id must be a lowercase SHA-256")
    try:
        parsed = uuid.UUID(binding["source_run_uuid"])
    except (ValueError, AttributeError):
        raise ValueError("source_run_uuid must be a canonical UUID") from None
    _require(str(parsed) == binding["source_run_uuid"], "source_run_uuid must be a canonical UUID")


def _validate_actors(actors: object) -> None:
    _require(isinstance(actors, list), "actors must be a list")
    seen: set[str] = set()
    for actor in actors:
        _require(_text(actor), "actors must be non-empty strings")
        _require(actor not in seen, "actors must be unique")
        seen.add(actor)


def _validate_events(events: object, source_ids: set[str]) -> None:
    _require(isinstance(events, list), "events must be a list")
    seen: set[str] = set()
    for event in events:
        _require(isinstance(event, dict) and set(event) == _EVENT_KEYS,
                 "event must have exactly the required keys")
        _require(all(_text(event[key]) for key in _EVENT_TEXT_KEYS),
                 "event values must be non-empty strings")
        _require(type(event["byte_offset"]) is int and event["byte_offset"] >= 0,
                 "byte_offset must be a non-negative integer")
        _require(type(event["byte_length"]) is int and event["byte_length"] > 0,
                 "byte_length must be a positive integer")
        _require(isinstance(event["record_sha256"], str)
                 and _HEX64.fullmatch(event["record_sha256"]) is not None,
                 "record_sha256 must be a lowercase SHA-256")
        _require(event["source_id"] in source_ids, "event references an unknown source")
        _require(event["event_id"] not in seen, "event_id must be unique")
        seen.add(event["event_id"])


def _validate_build_sources(sources: object) -> dict:
    _require(isinstance(sources, dict) and sources, "sources must be a non-empty mapping")
    for source_id, entry in sources.items():
        _require(_text(source_id), "source ids must be non-empty strings")
        _require(isinstance(entry, dict) and set(entry) == _BUILD_SOURCE_KEYS,
                 "source entries must have exactly path, kind, and producer")
        path = entry["path"]
        _require(isinstance(path, Path), "source path must be a Path")
        _require(not path.is_symlink() and path.is_file(), "sources must be regular non-symlink files")
        _require(isinstance(entry["kind"], str) and entry["kind"] in _KINDS, "unsupported source kind")
        _require(_text(entry["producer"]), "source producer must be a non-empty string")
    return sources


def _validate_record_anchors(events: list, source_files: dict[str, Path]) -> None:
    for event in events:
        path = source_files[event["source_id"]]
        offset, remaining = event["byte_offset"], event["byte_length"]
        _require(offset + remaining <= path.stat().st_size, "record range exceeds source bytes")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            stream.seek(offset)
            while remaining:
                chunk = stream.read(min(remaining, 1024 * 1024))
                _require(chunk, "record source was truncated")
                digest.update(chunk)
                remaining -= len(chunk)
        _require(digest.hexdigest() == event["record_sha256"], "record checksum mismatch")


def _manifest_hash(manifest: dict) -> str:
    body = {key: value for key, value in manifest.items() if key != "evidence_bundle_hash"}
    encoded = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                    encoding="utf-8")


def build_bundle(output: Path, *, release_root: Path, binding: dict, sources: dict,
                 actors: list, events: list) -> dict:
    output = Path(output)
    if output.exists():
        raise ValueError("Evidence bundle destination already exists; bundles are immutable")
    release = validate_release(Path(release_root))
    _validate_binding(binding)
    _require(binding["data_release_id"] == release.get("release_id"), "binding does not match the validated release")
    case_bindings = release.get("case_bindings")
    _require(isinstance(case_bindings, dict) and binding["scenario_id"] in case_bindings, "binding scenario is absent")
    sources = _validate_build_sources(sources)
    _validate_actors(actors)
    _validate_events(events, set(sources))
    _validate_record_anchors(events, {key: value["path"] for key, value in sources.items()})

    created = False
    try:
        try:
            output.mkdir(parents=True, mode=0o700)
        except FileExistsError:
            raise ValueError("Evidence bundle destination already exists; bundles are immutable") from None
        created = True
        (output / "sources").mkdir()
        frozen = {}
        for index, source_id in enumerate(sorted(sources)):
            source = sources[source_id]
            relative = f"sources/{index:04d}"
            destination = output / relative
            shutil.copyfile(source["path"], destination)
            destination.chmod(0o600)
            frozen[source_id] = {
                "path": relative,
                "sha256": _sha256_file(destination),
                "kind": source["kind"],
                "producer": source["producer"],
            }
        manifest = {
            "schema": SCHEMA,
            "binding": dict(binding),
            "sources": frozen,
            "actors": list(actors),
            "events": [dict(event) for event in events],
        }
        manifest["evidence_bundle_hash"] = _manifest_hash(manifest)
        _write_json(output / "bundle.json", manifest)
        (output / "bundle.json").chmod(0o600)
        return load_bundle(output)
    except BaseException:
        if created:
            shutil.rmtree(output, ignore_errors=True)
        raise


def _loads_strict(text: str) -> object:
    def no_duplicates(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key!r}")
            result[key] = value
        return result

    def reject_constant(value: str):
        raise ValueError(f"invalid JSON constant: {value}")

    return json.loads(text, object_pairs_hook=no_duplicates, parse_constant=reject_constant)


def _contained_file(root: Path, relative: Path) -> Path:
    current = root
    for part in relative.parts:
        current = current / part
        _require(not current.is_symlink(), "bundle contains a symlink")
    target = (root / relative).resolve()
    _require(target.is_relative_to(root), "bundle path escapes its root")
    _require(target.is_file(), "bundle source is not a regular file")
    return target


def _validate_bundle_sources(sources: object, root: Path) -> set[str]:
    _require(isinstance(sources, dict) and sources, "sources must be a non-empty mapping")
    paths: set[str] = set()
    for source_id, entry in sources.items():
        _require(_text(source_id), "source ids must be non-empty strings")
        _require(isinstance(entry, dict) and set(entry) == _SOURCE_KEYS,
                 "source entries must have exactly path, sha256, kind, and producer")
        _require(_text(entry["path"]) and _text(entry["producer"]), "source path and producer must be text")
        digest = entry["sha256"]
        _require(isinstance(digest, str) and _HEX64.fullmatch(digest) is not None, "source sha256 must be lowercase")
        _require(isinstance(entry["kind"], str) and entry["kind"] in _KINDS, "unsupported source kind")
        relative = Path(entry["path"])
        _require(not relative.is_absolute() and ".." not in relative.parts, "source path must be contained")
        _require(entry["path"] not in paths, "source paths must be unique")
        paths.add(entry["path"])
        target = _contained_file(root, relative)
        _require(_sha256_file(target) == digest, "bundle source checksum mismatch")
    return paths


def _inventory(root: Path) -> set[str]:
    found: set[str] = set()
    for path in root.rglob("*"):
        _require(not path.is_symlink(), "bundle contains a symlink")
        if path.is_file():
            found.add(path.relative_to(root).as_posix())
        else:
            _require(path.is_dir(), "bundle contains an unsupported entry")
    return found


def load_bundle(root: Path) -> dict:
    root = Path(root)
    _require(not root.is_symlink() and root.is_dir(), "bundle root must be a directory")
    root = root.resolve()
    payload = root / "bundle.json"
    _require(not payload.is_symlink() and payload.is_file(), "bundle.json is missing")
    manifest = _loads_strict(payload.read_text(encoding="utf-8"))
    _require(isinstance(manifest, dict) and set(manifest) == _MANIFEST_KEYS, "manifest keys are not exact")
    _require(manifest["schema"] == SCHEMA, "unsupported evidence bundle schema")
    _validate_binding(manifest["binding"])
    _validate_actors(manifest["actors"])
    source_paths = _validate_bundle_sources(manifest["sources"], root)
    _validate_events(manifest["events"], set(manifest["sources"]))
    _validate_record_anchors(manifest["events"], {
        key: root / value["path"] for key, value in manifest["sources"].items()
    })
    digest = manifest["evidence_bundle_hash"]
    _require(isinstance(digest, str) and _HEX64.fullmatch(digest) is not None,
             "evidence_bundle_hash must be a lowercase SHA-256")
    _require(_inventory(root) == {"bundle.json"} | source_paths, "bundle content does not match its manifest")
    _require(_manifest_hash(manifest) == digest, "evidence bundle hash mismatch")
    return manifest
