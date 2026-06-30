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
        stdout_path = output_dir / f"{case.scenario_id}.detect-secrets.stdout.txt"
        stderr_path = output_dir / f"{case.scenario_id}.detect-secrets.stderr.txt"

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
                f"detect-secrets timed out after {_TIMEOUT_SECONDS} seconds",
            )

        raw_output_path.write_text(getattr(completed, "stdout", ""), encoding="utf-8")
        stdout_path.write_text(getattr(completed, "stdout", ""), encoding="utf-8")
        stderr_path.write_text(getattr(completed, "stderr", ""), encoding="utf-8")

        findings: tuple[FindingRecord, ...] = ()
        failure_reason = None
        if completed.returncode == 0:
            if not raw_output_path.exists() or not raw_output_path.read_text(encoding="utf-8").strip():
                failure_reason = "detect-secrets did not write a JSON report"
            else:
                try:
                    findings = _parse_findings(self.metadata, case, raw_output_path)
                except ValueError as exc:
                    failure_reason = str(exc)
        else:
            failure_reason = f"detect-secrets exited with code {completed.returncode}"

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
    raw_output_path = output_dir / f"{case.scenario_id}.detect-secrets.json"
    return [
        _detect_secrets_binary(),
        "scan",
        "--all-files",
        "--force-use-all-plugins",
        "--no-verify",
        str(_require_fixture_dir(case)),
    ], raw_output_path


def _require_fixture_dir(case: AdapterCase) -> Path:
    if case.fixture_dir is None:
        raise ValueError("fixture_dir is required for detect-secrets scans")
    return case.fixture_dir


def _detect_secrets_binary() -> str:
    configured = os.environ.get("MTAGENTLENS_DETECT_SECRETS_BIN")
    if configured and Path(configured).exists():
        return configured
    project_binary = Path(__file__).resolve().parents[3] / "compare" / ".venv-detect-secrets" / "bin" / "detect-secrets"
    return str(project_binary)


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
        raise ValueError(f"invalid detect-secrets JSON report: {exc.msg}") from exc
    results = data.get("results") if isinstance(data, dict) else None
    if not isinstance(results, dict):
        raise ValueError("detect-secrets report must contain a results object")

    findings: list[FindingRecord] = []
    index = 0
    for path, entries in results.items():
        if not isinstance(path, str) or not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            index += 1
            secret_type = _string(entry.get("type")) or "detect-secrets"
            line_number = _string(entry.get("line_number"))
            evidence = f"type={secret_type}; path={path}"
            if line_number:
                evidence += f"; line={line_number}"
            # Populate text_span with the matched secret string
            secret_value = _string(entry.get("secret")) or _string(entry.get("hashed_secret")) or secret_type
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
                    text_span=secret_value,
                    evidence=evidence,
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
