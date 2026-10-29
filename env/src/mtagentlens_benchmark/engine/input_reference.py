"""Bind a release's exact inputs to the materialised per-run fixture tree."""

from __future__ import annotations

import base64
import dataclasses
import hashlib
import json
import os
import stat
from pathlib import Path

from mtagentlens_benchmark.engine.fixtures import MAX_REWRITE_BYTES, _rewrite_text
from mtagentlens_benchmark.engine.process_audit import _capture_tree, _read_file
from mtagentlens_benchmark.loader.scenario_loader import load_scenario

SCHEMA = "mtagentlens.execution-source/v1"


def _tree_digest(files: dict[str, str]) -> str:
    """Digest a relative-path/SHA map the same way fixture case bindings do."""
    return hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()


def fixture_digest(snapshot: dict | None) -> str | None:
    """Content digest of a ``process_audit`` fixture snapshot."""
    if snapshot is None:
        return None
    return _tree_digest({
        relative: entry["sha256"]
        for relative, entry in snapshot["files"].items()
    })


def _file_shas(snapshot: dict) -> dict[str, str]:
    return {
        relative: entry["sha256"]
        for relative, entry in snapshot["files"].items()
    }


def _snapshot_tree(root: Path, label: str, *, optional: bool = False) -> dict:
    try:
        st = root.lstat()
    except FileNotFoundError:
        if optional:
            return {"files": {}, "directories": {}}
        raise ValueError(f"{label} is missing: {root}") from None
    except OSError as error:
        raise ValueError(f"cannot stat {label}: {root}: {error}") from error
    if stat.S_ISLNK(st.st_mode):
        raise ValueError(f"{label} is a symlink: {root}")
    if not stat.S_ISDIR(st.st_mode):
        raise ValueError(f"{label} is not a directory: {root}")
    try:
        return _capture_tree(root)
    except ValueError:
        raise
    except OSError as error:
        raise ValueError(f"cannot capture {label}: {root}: {error}") from error


def _read_regular(path: Path, label: str) -> bytes:
    try:
        st = path.lstat()
    except FileNotFoundError:
        raise ValueError(f"{label} is missing: {path}") from None
    except OSError as error:
        raise ValueError(f"cannot stat {label}: {path}: {error}") from error
    if stat.S_ISLNK(st.st_mode):
        raise ValueError(f"{label} is a symlink: {path}")
    if not stat.S_ISREG(st.st_mode):
        raise ValueError(f"{label} is not a regular file: {path}")
    try:
        return _read_file(path)
    except ValueError:
        raise
    except OSError as error:
        raise ValueError(f"cannot read {label}: {path}: {error}") from error


def _reject_symlink_path(path: Path, stop: Path) -> None:
    """Reject a symlink in the path components below ``stop``."""
    current = path
    while True:
        try:
            st = current.lstat()
        except FileNotFoundError:
            break
        except OSError as error:
            raise ValueError(f"cannot stat release path: {current}: {error}") from error
        if stat.S_ISLNK(st.st_mode):
            raise ValueError(f"symlink not allowed in release inputs: {current}")
        if current == stop or current.parent == current:
            break
        current = current.parent


def _within(path: Path, root: Path, label: str) -> None:
    try:
        resolved, resolved_root = path.resolve(), root.resolve()
    except OSError as error:
        raise ValueError(f"cannot resolve {label}: {path}: {error}") from error
    if not resolved.is_relative_to(resolved_root):
        raise ValueError(f"{label} escapes {root}: {path}")


def _materialized_sha(entry: dict, source: Path, destination: Path) -> str:
    """Predict the bytes produced by ``fixtures._rewrite_tree``."""
    data = base64.b64decode(entry["content_base64"])
    if len(data) > MAX_REWRITE_BYTES:
        return entry["sha256"]
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return entry["sha256"]

    # ``Path.read_text`` translates universal newlines before the rewrite.
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    rewritten = _rewrite_text(normalized, source, destination)
    if rewritten == normalized:
        return entry["sha256"]
    # ``Path.write_text`` translates newlines back on platforms that need it.
    if os.linesep != "\n":
        rewritten = rewritten.replace("\n", os.linesep)
    return hashlib.sha256(rewritten.encode("utf-8")).hexdigest()


def _config_payload(config, setup_path: str | None) -> dict:
    payload = dataclasses.asdict(config)
    payload.pop("run_marker", None)
    payload["pre_run_script"] = setup_path
    return payload


class ReleaseInputAudit:
    """Freeze the source release, then verify the copied and prepared fixtures.

    The class only reads files.  Script execution and output writing remain the
    executor's responsibility; ``finish_setup`` merely observes the bytes that
    the successful setup left behind.
    """

    def __init__(self, release_root, manifest, config, run_dir):
        self.release_root = Path(release_root).resolve()
        if not self.release_root.is_dir():
            raise ValueError(f"release root is not a directory: {self.release_root}")
        self.run_dir = Path(run_dir).resolve()
        if self.run_dir.is_relative_to(self.release_root):
            raise ValueError("run output must be outside the immutable release")
        self.materialized_fixture_dir = self.run_dir / "fixtures"

        self.manifest = manifest
        self._manifest_files = self._validate_manifest(manifest)

        scenario_id = getattr(config, "id", None)
        if (
            not isinstance(scenario_id, str)
            or not scenario_id
            or scenario_id in (".", "..")
            or "/" in scenario_id
            or "\\" in scenario_id
            or "\x00" in scenario_id
        ):
            raise ValueError("scenario id must be a single release-relative name")
        self.scenario_id = scenario_id
        self._scenario_relative = f"scenarios/{scenario_id}.json"
        self._scenario_path = self.release_root / self._scenario_relative

        try:
            self._expected = load_scenario(
                self._scenario_path, self.release_root / "fixtures",
            )
        except Exception as error:
            raise ValueError(f"cannot load expected release scenario: {error}") from error

        fixture_root = self.release_root / "fixtures"
        fixture_source = Path(self._expected.fixtures_dir)
        _within(fixture_source, fixture_root, "scenario fixture directory")
        try:
            fixture_relative = fixture_source.relative_to(fixture_root)
        except ValueError as error:
            raise ValueError(
                f"scenario fixture directory is not under {fixture_root}: {fixture_source}"
            ) from error
        if ".." in fixture_relative.parts:
            raise ValueError(f"scenario fixture directory escapes {fixture_root}")
        self.source_fixture_dir = fixture_source
        fixture_prefix = Path("fixtures")
        if fixture_relative != Path("."):
            fixture_prefix /= fixture_relative
        self._fixture_prefix = fixture_prefix.as_posix()
        self._bench_root = self.release_root / "bench"

        if self._expected.pre_run_script and Path(self._expected.pre_run_script).is_absolute():
            raise ValueError("source setup declaration must be release-relative")
        self._setup_relative = self._resolve_setup(self._expected.pre_run_script)
        self._require_config(
            config, _config_payload(self._expected, self._setup_relative),
        )

        self.source_files, scenario_sha, fixture_shas, fixture_snapshot = (
            self._collect_sources()
        )
        self._validate_manifest_scope(self.source_files)
        self._validate_bindings(scenario_sha, fixture_shas)

        self._expected_materialized = {
            relative: _materialized_sha(
                entry, self.source_fixture_dir, self.materialized_fixture_dir,
            )
            for relative, entry in fixture_snapshot["files"].items()
        }
        self.pre_setup_fixture_sha256 = _tree_digest(self._expected_materialized)

        self._expected_rewritten = _config_payload(
            self._expected, self._setup_relative,
        )
        self._expected_rewritten["agents"] = [
            {
                "role_file": agent["role_file"],
                "task": _rewrite_text(
                    agent["task"], self.source_fixture_dir,
                    self.materialized_fixture_dir,
                ),
            }
            for agent in self._expected_rewritten["agents"]
        ]
        self._materialized_verified = False

    @staticmethod
    def _validate_manifest(manifest) -> dict:
        if not isinstance(manifest, dict):
            raise ValueError("release manifest must be an object")
        if manifest.get("schema") != "mtagentlens.dataset-release/1":
            raise ValueError("unsupported input release schema")
        files = manifest.get("files")
        if not isinstance(files, dict) or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in files.items()
        ):
            raise ValueError("release manifest files must be a relative-path/SHA map")
        release_id = manifest.get("release_id")
        if not isinstance(release_id, str) or not release_id:
            raise ValueError("release manifest has no release_id")
        try:
            payload = {
                key: value for key, value in manifest.items()
                if key not in ("release_id", "case_labels")
            }
            calculated = hashlib.sha256(
                json.dumps(payload, sort_keys=True).encode()
            ).hexdigest()
        except (TypeError, ValueError) as error:
            raise ValueError(f"release manifest is not canonical JSON: {error}") from error
        if calculated != release_id:
            raise ValueError("release manifest identity does not match release_id")
        return files

    def _resolve_setup(self, declared) -> str | None:
        if declared is None or declared == "":
            return None
        if isinstance(declared, os.PathLike):
            declared = str(declared)
        if not isinstance(declared, str) or not declared.strip():
            raise ValueError("setup script must be a non-empty path")
        raw = Path(declared)
        if raw.is_absolute():
            candidate = raw
        else:
            parts = raw.parts
            if parts[:3] == ("env", "scenarios", "bench"):
                candidate = self.release_root.joinpath("bench", *parts[3:])
            elif parts and parts[0] == "bench":
                candidate = self.release_root.joinpath(*parts)
            else:
                raise ValueError(
                    "setup script must be under env/scenarios/bench or bench"
                )
        bench = self.release_root / "bench"
        _within(candidate, bench, "setup script")
        _reject_symlink_path(candidate, self.release_root)
        if not candidate.is_file():
            raise ValueError(f"setup script is not a file: {candidate}")
        return candidate.resolve().relative_to(self.release_root).as_posix()

    def _collect_sources(self) -> tuple[dict[str, str], str, dict[str, str], dict]:
        _reject_symlink_path(self._scenario_path, self.release_root)
        scenario_sha = hashlib.sha256(
            _read_regular(self._scenario_path, "scenario JSON")
        ).hexdigest()
        _reject_symlink_path(self.source_fixture_dir, self.release_root)
        fixture_snapshot = _snapshot_tree(
            self.source_fixture_dir, "scenario fixture directory",
        )
        _reject_symlink_path(self._bench_root, self.release_root)
        bench_snapshot = _snapshot_tree(
            self._bench_root, "bench support tree", optional=True,
        )

        fixture_shas = _file_shas(fixture_snapshot)
        selected = {self._scenario_relative: scenario_sha}
        for relative, digest in fixture_shas.items():
            selected[f"{self._fixture_prefix}/{relative}"] = digest
        for relative, digest in _file_shas(bench_snapshot).items():
            selected[f"bench/{relative}"] = digest
        return selected, scenario_sha, fixture_shas, fixture_snapshot

    def _in_scope(self, relative: str) -> bool:
        return (
            relative == self._scenario_relative
            or relative.startswith(self._fixture_prefix + "/")
            or relative.startswith("bench/")
        )

    def _validate_manifest_scope(self, selected: dict[str, str]) -> None:
        expected = {
            relative for relative in self._manifest_files
            if self._in_scope(relative)
        }
        actual = set(selected)
        if expected != actual:
            missing = sorted(expected - actual)
            extra = sorted(actual - expected)
            raise ValueError(
                "release source inputs differ from the manifest: "
                f"missing={missing}, extra={extra}"
            )
        for relative, digest in selected.items():
            if self._manifest_files[relative] != digest:
                raise ValueError(f"release source input changed: {relative}")

    def _validate_bindings(
        self, scenario_sha: str, fixture_shas: dict[str, str],
    ) -> None:
        bindings = self.manifest.get("case_bindings")
        if not isinstance(bindings, dict):
            raise ValueError("release manifest has no case bindings")
        binding = bindings.get(self.scenario_id)
        if not isinstance(binding, dict):
            raise ValueError(f"release manifest has no case binding for {self.scenario_id}")
        if binding.get("scenario_sha256") != scenario_sha:
            raise ValueError("scenario JSON does not match its case binding")
        if binding.get("fixture_sha256") != _tree_digest(fixture_shas):
            raise ValueError("scenario fixtures do not match their case binding")

    def _require_config(self, config, expected: dict) -> None:
        actual = _config_payload(config, self._resolve_setup(config.pre_run_script))
        if actual != expected:
            raise ValueError("scenario config does not match the bound release input")

    def verify_materialized(self, config) -> None:
        """Compare the copied fixture bytes and effective config to the source."""
        self._materialized_verified = False
        actual_tree = _snapshot_tree(
            self.materialized_fixture_dir, "materialised fixture directory",
        )
        if _file_shas(actual_tree) != self._expected_materialized:
            raise ValueError(
                "materialised fixture content does not match the declared rewrite"
            )
        self._require_config(config, self._expected_rewritten)
        self._materialized_verified = True

    def finish_setup(self, config) -> dict:
        """Observe the successful setup result and return the execution source."""
        if not self._materialized_verified:
            raise ValueError("verify_materialized must succeed before finish_setup")

        fresh, scenario_sha, fixture_shas, _ = self._collect_sources()
        self._validate_manifest_scope(fresh)
        self._validate_bindings(scenario_sha, fixture_shas)
        if fresh != self.source_files:
            raise ValueError("release source inputs changed after materialisation")
        self._require_config(config, self._expected_rewritten)

        prepared_tree = _snapshot_tree(
            self.materialized_fixture_dir, "materialised fixture directory",
        )
        prepared_sha = _tree_digest(_file_shas(prepared_tree))
        setup = None
        if self._setup_relative is None and prepared_sha != self.pre_setup_fixture_sha256:
            raise ValueError("fixture changed without a declared setup")
        if self._setup_relative is not None:
            setup = {
                "path": self._setup_relative,
                "sha256": self._manifest_files[self._setup_relative],
                "exit_code": 0,
            }
        return {
            "schema": SCHEMA,
            "release_id": self.manifest["release_id"],
            "release_root": str(self.release_root),
            "scenario_id": self.scenario_id,
            "source_files": dict(self.source_files),
            "source_fixture_dir": str(self.source_fixture_dir),
            "materialized_fixture_dir": str(self.materialized_fixture_dir),
            "pre_setup_fixture_sha256": self.pre_setup_fixture_sha256,
            "prepared_fixture_sha256": prepared_sha,
            "setup": setup,
        }
