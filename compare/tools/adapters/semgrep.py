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
from compare.tools.normalize import normalize_severity

_SAFE_SCENARIO_ID_RE = re.compile(r"^[a-zA-Z0-9_.-]+$")
_TIMEOUT_SECONDS = 120
_RULES = """rules:
  - id: mtagentlens.dangerous-os-system
    patterns:
      - pattern: os.system(...)
    message: Dangerous command execution
    severity: ERROR
    languages: [python]
  - id: mtagentlens.subprocess-shell-true
    patterns:
      - pattern: subprocess.$FUNC(..., shell=True, ...)
    message: Shell execution through subprocess
    severity: ERROR
    languages: [python]
  - id: mtagentlens.secret-assignment
    patterns:
      - pattern-regex: '(?i)(api[_-]?key|secret|token|password)\\s*[:=]'
    message: Sensitive assignment pattern
    severity: WARNING
    languages: [generic]
"""

# Community rule packs from Semgrep Registry (CE-compatible, no login required).
# These supplement the 3 custom rules above with battle-tested community rules.
_COMMUNITY_RULE_PACKS = [
    "p/secrets",    # Secret detection (API keys, tokens, passwords, certificates)
    "p/generic",    # Generic pattern matching across all text files
    "p/python",     # Python security (command injection, dangerous functions)
    "p/bash",       # Shell script security (command injection, unsafe patterns)
]


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
        command, raw_output_path, _rules_path = _build_command(case, output_dir)
        stdout_path = output_dir / f"{case.scenario_id}.semgrep.stdout.txt"
        stderr_path = output_dir / f"{case.scenario_id}.semgrep.stderr.txt"

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
                f"semgrep timed out after {_TIMEOUT_SECONDS} seconds",
            )

        stdout_path.write_text(getattr(completed, "stdout", ""), encoding="utf-8")
        stderr_path.write_text(getattr(completed, "stderr", ""), encoding="utf-8")

        findings: tuple[FindingRecord, ...] = ()
        failure_reason = None
        if completed.returncode in {0, 1}:
            if not raw_output_path.exists():
                failure_reason = "semgrep did not write a JSON report"
            else:
                try:
                    findings = _parse_findings(self.metadata, case, raw_output_path)
                except ValueError as exc:
                    failure_reason = str(exc)
        else:
            failure_reason = f"semgrep exited with code {completed.returncode}"

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


def _build_command(case: AdapterCase, output_dir: Path) -> tuple[list[str], Path, Path]:
    raw_output_path = output_dir / f"{case.scenario_id}.semgrep.json"
    rules_path = output_dir / f"{case.scenario_id}.semgrep-rules.yaml"
    rules_path.parent.mkdir(parents=True, exist_ok=True)
    rules_path.write_text(_RULES, encoding="utf-8")
    cmd = [
        _semgrep_binary(),
        "--config",
        str(rules_path),
    ]
    # Append community rule packs from Semgrep Registry (CE-compatible, no login).
    for pack in _COMMUNITY_RULE_PACKS:
        cmd.extend(["--config", pack])
    cmd.extend([
        "--json",
        "--output",
        str(raw_output_path),
        "--metrics",
        "off",
        "--error",
        str(_require_fixture_dir(case)),
    ])
    return (cmd, raw_output_path, rules_path)


def _require_fixture_dir(case: AdapterCase) -> Path:
    if case.fixture_dir is None:
        raise ValueError("fixture_dir is required for semgrep scans")
    return case.fixture_dir


def _semgrep_binary() -> str:
    configured = os.environ.get("MTAGENTLENS_SEMGREP_BIN")
    if configured and Path(configured).exists():
        return configured
    project_binary = Path(__file__).resolve().parents[3] / "compare" / ".venv-semgrep" / "bin" / "semgrep"
    if project_binary.exists():
        return str(project_binary)
    binary = shutil.which("semgrep")
    if binary is not None:
        return binary
    return "semgrep"


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
        raise ValueError(f"invalid semgrep JSON report: {exc.msg}") from exc
    results = data.get("results") if isinstance(data, dict) else None
    if not isinstance(results, list):
        raise ValueError("semgrep report must contain a results list")

    findings: list[FindingRecord] = []
    for index, item in enumerate(results, start=1):
        if not isinstance(item, dict):
            continue
        check_id = _string(item.get("check_id")) or "semgrep"
        path = _string(item.get("path"))
        extra = item.get("extra") if isinstance(item.get("extra"), dict) else {}
        severity = normalize_severity(_string(extra.get("severity")))
        lines = _string(extra.get("lines"))
        message = _string(extra.get("message"))
        line = _line_number(item)
        boundary_type = _boundary_for(check_id)
        findings.append(
            FindingRecord(
                tool_id=metadata.id,
                dataset_name=case.dataset_name,
                scenario_id=case.scenario_id,
                finding_id=f"{metadata.id}:{case.scenario_id}:{index}",
                severity=severity,
                category=_category_for(check_id),
                boundary_type=boundary_type,
                resource=path,
                command=lines if boundary_type == BoundaryType.EXEC_ARG else None,
                text_span=lines,
                evidence=_evidence(check_id, message, path, line),
                confidence=0.8,
                raw_output_path=raw_output_path,
            )
        )
    return tuple(findings)


def _boundary_for(check_id: str) -> BoundaryType:
    if "os-system" in check_id or "subprocess" in check_id:
        return BoundaryType.EXEC_ARG
    return BoundaryType.RESOURCE


def _category_for(check_id: str) -> str:
    if "secret" in check_id:
        return "secret_or_sensitive_resource"
    return "dangerous_command"


def _line_number(item: dict[str, Any]) -> str | None:
    start = item.get("start")
    if isinstance(start, dict):
        return _string(start.get("line"))
    return None


def _evidence(check_id: str, message: str | None, path: str | None, line: str | None) -> str:
    parts = [f"check_id={check_id}"]
    if message:
        parts.append(f"message={message}")
    if path:
        parts.append(f"path={path}")
    if line:
        parts.append(f"line={line}")
    return "; ".join(parts)


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
