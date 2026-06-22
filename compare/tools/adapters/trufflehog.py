from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from compare.tools.base import AdapterCase, AdapterResult, BoundaryType, FindingRecord, RunStatus, ToolMetadata

_SAFE_SCENARIO_ID_RE = re.compile(r"^[a-zA-Z0-9_.-]+$")
_TIMEOUT_SECONDS = 120


class Adapter:
    def __init__(self, metadata: ToolMetadata) -> None:
        self.metadata = metadata

    def supports(self, case: AdapterCase) -> bool:
        return case.fixture_dir is not None and case.fixture_dir.is_dir()

    def run(self, case: AdapterCase, output_dir: Path) -> AdapterResult:
        if not _SAFE_SCENARIO_ID_RE.fullmatch(case.scenario_id) or ".." in case.scenario_id:
            raise ValueError("scenario_id must be a safe filename component")

        start = time.monotonic()
        output_dir.mkdir(parents=True, exist_ok=True)
        command = _build_command(case)
        raw_output_path = output_dir / f"{case.scenario_id}.trufflehog.jsonl"
        stderr_path = output_dir / f"{case.scenario_id}.trufflehog.stderr.txt"

        try:
            completed = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raw_output_path.write_text(_text(exc.stdout), encoding="utf-8")
            stderr_path.write_text(_text(exc.stderr), encoding="utf-8")
            return _result(
                self.metadata,
                case,
                RunStatus.FAILED,
                (),
                raw_output_path,
                stderr_path,
                None,
                start,
                f"trufflehog timed out after {_TIMEOUT_SECONDS} seconds",
            )

        raw_output_path.write_text(getattr(completed, "stdout", ""), encoding="utf-8")
        stderr_path.write_text(getattr(completed, "stderr", ""), encoding="utf-8")

        findings: tuple[FindingRecord, ...] = ()
        failure_reason = None
        if completed.returncode in {0, 1}:
            try:
                findings = _parse_findings(self.metadata, case, raw_output_path)
            except ValueError as exc:
                failure_reason = str(exc)
        else:
            failure_reason = f"trufflehog exited with code {completed.returncode}"

        status = RunStatus.FAILED if failure_reason else RunStatus.SUCCESS if findings else RunStatus.NO_FINDINGS
        return _result(
            self.metadata,
            case,
            status,
            findings,
            raw_output_path,
            stderr_path,
            completed.returncode,
            start,
            failure_reason,
        )


def _build_command(case: AdapterCase) -> list[str]:
    return [_trufflehog_binary(), "filesystem", str(_require_fixture_dir(case)), "--json", "--no-verification"]


def _require_fixture_dir(case: AdapterCase) -> Path:
    if case.fixture_dir is None:
        raise ValueError("fixture_dir is required for trufflehog scans")
    return case.fixture_dir


def _trufflehog_binary() -> str:
    go_path = os.environ.get("GOPATH")
    candidates = []
    if go_path:
        candidates.append(Path(go_path) / "bin" / "trufflehog")
    candidates.append(Path.home() / "go" / "bin" / "trufflehog")
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    binary = shutil.which("trufflehog")
    if binary is not None:
        return binary
    return "trufflehog"


def _result(
    metadata: ToolMetadata,
    case: AdapterCase,
    status: RunStatus,
    findings: tuple[FindingRecord, ...],
    raw_output_path: Path,
    stderr_path: Path,
    exit_code: int | None,
    start: float,
    failure_reason: str | None,
) -> AdapterResult:
    return AdapterResult(
        tool=metadata,
        scenario_id=case.scenario_id,
        status=status,
        findings=findings,
        raw_output_path=raw_output_path,
        stdout_path=raw_output_path,
        stderr_path=stderr_path,
        exit_code=exit_code,
        duration_seconds=time.monotonic() - start,
        failure_reason=failure_reason,
    )


def _parse_findings(metadata: ToolMetadata, case: AdapterCase, raw_output_path: Path) -> tuple[FindingRecord, ...]:
    findings: list[FindingRecord] = []
    for index, line in enumerate(raw_output_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid trufflehog JSONL report on line {index}: {exc.msg}") from exc
        if not isinstance(item, dict):
            continue
        detector = _string(item.get("DetectorName")) or _string(item.get("DetectorType")) or "trufflehog"
        path = _extract_path(item)
        line_number = _extract_line(item)
        raw = _string(item.get("Raw")) or _string(item.get("RawV2")) or _string(item.get("Redacted"))
        evidence_parts = [f"detector={detector}"]
        if path:
            evidence_parts.append(f"path={path}")
        if line_number:
            evidence_parts.append(f"line={line_number}")
        findings.append(
            FindingRecord(
                tool_id=metadata.id,
                dataset_name=case.dataset_name,
                scenario_id=case.scenario_id,
                finding_id=f"{metadata.id}:{case.scenario_id}:{index}",
                severity="Critical",
                category="secret_or_sensitive_resource",
                boundary_type=BoundaryType.RESOURCE,
                resource=path,
                command=None,
                text_span=raw,
                evidence="; ".join(evidence_parts),
                confidence=0.9,
                raw_output_path=raw_output_path,
            )
        )
    return tuple(findings)


def _extract_path(item: dict[str, Any]) -> str | None:
    source_metadata = item.get("SourceMetadata")
    if not isinstance(source_metadata, dict):
        return None
    data = source_metadata.get("Data")
    if not isinstance(data, dict):
        return None
    filesystem = data.get("Filesystem")
    if isinstance(filesystem, dict):
        return _string(filesystem.get("file")) or _string(filesystem.get("path"))
    return None


def _extract_line(item: dict[str, Any]) -> str | None:
    source_metadata = item.get("SourceMetadata")
    if not isinstance(source_metadata, dict):
        return None
    data = source_metadata.get("Data")
    if not isinstance(data, dict):
        return None
    filesystem = data.get("Filesystem")
    if isinstance(filesystem, dict):
        return _string(filesystem.get("line"))
    return None


def _string(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, (int, bool)):
        return str(value)
    return None


def _text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return _string(value) or ""
