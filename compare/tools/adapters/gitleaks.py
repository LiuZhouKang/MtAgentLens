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
        command, raw_output_path = _build_command(case, output_dir)
        stdout_path = output_dir / f"{case.scenario_id}.gitleaks.stdout.txt"
        stderr_path = output_dir / f"{case.scenario_id}.gitleaks.stderr.txt"

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
            if not raw_output_path.exists():
                raw_output_path.write_text("", encoding="utf-8")
            stdout_path.write_text(_text(exc.stdout), encoding="utf-8")
            stderr_path.write_text(_text(exc.stderr), encoding="utf-8")
            return _result(
                self.metadata,
                case,
                RunStatus.FAILED,
                (),
                raw_output_path,
                stdout_path,
                stderr_path,
                None,
                start,
                f"gitleaks timed out after {_TIMEOUT_SECONDS} seconds",
            )

        stdout_path.write_text(getattr(completed, "stdout", ""), encoding="utf-8")
        stderr_path.write_text(getattr(completed, "stderr", ""), encoding="utf-8")

        findings: tuple[FindingRecord, ...] = ()
        failure_reason = None
        if completed.returncode in {0, 1}:
            if not raw_output_path.exists():
                failure_reason = "gitleaks did not write a JSON report"
            else:
                try:
                    findings = _parse_findings(self.metadata, case, raw_output_path)
                except ValueError as exc:
                    failure_reason = str(exc)
        else:
            failure_reason = f"gitleaks exited with code {completed.returncode}"

        status = RunStatus.FAILED if failure_reason else RunStatus.SUCCESS if findings else RunStatus.NO_FINDINGS
        return _result(
            self.metadata,
            case,
            status,
            findings,
            raw_output_path,
            stdout_path,
            stderr_path,
            completed.returncode,
            start,
            failure_reason,
        )


def _build_command(case: AdapterCase, output_dir: Path) -> tuple[list[str], Path]:
    raw_output_path = output_dir / f"{case.scenario_id}.gitleaks.json"
    return (
        [
            _gitleaks_binary(),
            "dir",
            str(_require_fixture_dir(case)),
            "--no-banner",
            "--redact=0",
            "--report-format",
            "json",
            "--report-path",
            str(raw_output_path),
        ],
        raw_output_path,
    )


def _require_fixture_dir(case: AdapterCase) -> Path:
    if case.fixture_dir is None:
        raise ValueError("fixture_dir is required for gitleaks scans")
    return case.fixture_dir


def _gitleaks_binary() -> str:
    go_path = os.environ.get("GOPATH")
    candidates = []
    if go_path:
        candidates.append(Path(go_path) / "bin" / "gitleaks")
    candidates.append(Path.home() / "go" / "bin" / "gitleaks")
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    binary = shutil.which("gitleaks")
    if binary is not None:
        return binary
    return "gitleaks"


def _result(
    metadata: ToolMetadata,
    case: AdapterCase,
    status: RunStatus,
    findings: tuple[FindingRecord, ...],
    raw_output_path: Path,
    stdout_path: Path,
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
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        exit_code=exit_code,
        duration_seconds=time.monotonic() - start,
        failure_reason=failure_reason,
    )


def _parse_findings(metadata: ToolMetadata, case: AdapterCase, raw_output_path: Path) -> tuple[FindingRecord, ...]:
    try:
        data = json.loads(raw_output_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid gitleaks JSON report: {exc.msg}") from exc
    if not isinstance(data, list):
        raise ValueError("gitleaks report must be a JSON list")

    findings: list[FindingRecord] = []
    for index, item in enumerate(data, start=1):
        if not isinstance(item, dict):
            continue
        rule_id = _string(item.get("RuleID")) or _string(item.get("Rule")) or "gitleaks"
        path = _string(item.get("File")) or _string(item.get("file"))
        match = _string(item.get("Match")) or _string(item.get("Secret"))
        description = _string(item.get("Description")) or _string(item.get("description"))
        line = _string(item.get("StartLine")) or _string(item.get("Line"))
        evidence_parts = [f"rule={rule_id}"]
        if description:
            evidence_parts.append(f"description={description}")
        if path:
            evidence_parts.append(f"path={path}")
        if line:
            evidence_parts.append(f"line={line}")
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
                text_span=match,
                evidence="; ".join(evidence_parts),
                confidence=0.9,
                raw_output_path=raw_output_path,
            )
        )
    return tuple(findings)


def _string(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, int):
        return str(value)
    return None


def _text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return _string(value) or ""
