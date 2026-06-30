from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any

from compare.tools.base import AdapterCase, AdapterResult, BoundaryType, FindingRecord, RunStatus, ToolMetadata

_SAFE_SCENARIO_ID_RE = re.compile(r"^[a-zA-Z0-9_.-]+$")
_TIMEOUT_SECONDS = 120
_DEFAULT_RULES_DIRNAME = ".yara-forge-core-20260524"


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
        stdout_path = output_dir / f"{case.scenario_id}.yara-x.stdout.txt"
        stderr_path = output_dir / f"{case.scenario_id}.yara-x.stderr.txt"
        binary_path = Path(command[0])
        rules_dir = Path(command[-2])

        if not binary_path.exists():
            return _preflight_failure(
                self.metadata,
                case,
                raw_output_path,
                stdout_path,
                stderr_path,
                start,
                f"yara-x binary does not exist: {binary_path}",
            )
        if not os.access(binary_path, os.X_OK):
            return _preflight_failure(
                self.metadata,
                case,
                raw_output_path,
                stdout_path,
                stderr_path,
                start,
                f"yara-x binary is not executable: {binary_path}",
            )
        if not rules_dir.is_dir():
            return _preflight_failure(
                self.metadata,
                case,
                raw_output_path,
                stdout_path,
                stderr_path,
                start,
                f"yara-x rules directory does not exist: {rules_dir}",
            )
        if not _has_rule_files(rules_dir):
            return _preflight_failure(
                self.metadata,
                case,
                raw_output_path,
                stdout_path,
                stderr_path,
                start,
                f"yara-x rules directory does not contain any .yar or .yara files: {rules_dir}",
            )

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
                f"yara-x timed out after {_TIMEOUT_SECONDS} seconds",
            )
        except (FileNotFoundError, PermissionError, OSError) as exc:
            return _preflight_failure(
                self.metadata,
                case,
                raw_output_path,
                stdout_path,
                stderr_path,
                start,
                f"failed to launch yara-x: {exc}",
            )

        stdout_text = getattr(completed, "stdout", "")
        stderr_text = getattr(completed, "stderr", "")
        raw_output_path.write_text(stdout_text, encoding="utf-8")
        stdout_path.write_text(stdout_text, encoding="utf-8")
        stderr_path.write_text(stderr_text, encoding="utf-8")

        findings: tuple[FindingRecord, ...] = ()
        failure_reason = None
        if completed.returncode == 0:
            try:
                findings = _parse_findings(self.metadata, case, raw_output_path)
            except ValueError as exc:
                failure_reason = str(exc)
        else:
            failure_reason = _exit_failure_reason(completed.returncode, binary_path, rules_dir, _require_fixture_dir(case), stderr_text)

        status = _status_for(failure_reason, findings)
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
    raw_output_path = output_dir / f"{case.scenario_id}.yara-x.ndjson"
    return [
        _yara_x_binary(),
        "scan",
        "--recursive",
        "--output-format",
        "ndjson",
        str(_yara_rules_dir()),
        str(_require_fixture_dir(case)),
    ], raw_output_path


def _require_fixture_dir(case: AdapterCase) -> Path:
    if case.fixture_dir is None:
        raise ValueError("fixture_dir is required for yara-x scans")
    return case.fixture_dir


def _yara_x_binary() -> str:
    configured = os.environ.get("MTAGENTLENS_YARA_X_BIN")
    if configured:
        configured_path = Path(configured)
        if not configured_path.exists():
            raise ValueError(f"configured MTAGENTLENS_YARA_X_BIN does not exist: {configured}")
        return configured
    project_binary = Path(__file__).resolve().parents[3] / "compare" / ".yara-x" / "bin" / "yr"
    return str(project_binary)


def _yara_rules_dir() -> Path:
    configured = os.environ.get("MTAGENTLENS_YARA_X_RULES_DIR")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[3] / "compare" / _DEFAULT_RULES_DIRNAME


def _has_rule_files(rules_dir: Path) -> bool:
    return any(path.is_file() for pattern in ("*.yar", "*.yara") for path in rules_dir.rglob(pattern))


def _status_for(failure_reason: str | None, findings: tuple[FindingRecord, ...]) -> RunStatus:
    if failure_reason:
        return RunStatus.FAILED
    if findings:
        return RunStatus.SUCCESS
    return RunStatus.NO_FINDINGS


def _preflight_failure(
    metadata: ToolMetadata,
    case: AdapterCase,
    raw_output_path: Path,
    stdout_path: Path,
    stderr_path: Path,
    start: float,
    failure_reason: str,
) -> AdapterResult:
    raw_output_path.write_text("", encoding="utf-8")
    stdout_path.write_text("", encoding="utf-8")
    stderr_path.write_text(failure_reason, encoding="utf-8")
    return _result(
        metadata,
        case,
        RunStatus.FAILED,
        (),
        raw_output_path,
        stdout_path,
        stderr_path,
        None,
        start,
        failure_reason,
    )


def _exit_failure_reason(
    exit_code: int,
    binary_path: Path,
    rules_dir: Path,
    fixture_dir: Path,
    stderr_text: str,
) -> str:
    parts = [
        f"yara-x failed (code {exit_code})",
        f"binary={binary_path}",
        f"rules={rules_dir}",
        f"fixture_dir={fixture_dir}",
    ]
    stderr_summary = stderr_text.strip()
    if stderr_summary:
        parts.append(f"stderr={stderr_summary[:400]}")
    return "; ".join(parts)


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
    raw_text = raw_output_path.read_text(encoding="utf-8")
    if not raw_text.strip():
        return ()

    findings: list[FindingRecord] = []
    index = 0
    for line in raw_text.splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid yara-x ndjson output: {exc.msg}") from exc
        if not isinstance(payload, dict):
            raise ValueError("invalid yara-x ndjson output: each line must be an object")
        path = _string(payload.get("path"))
        rules = payload.get("rules")
        if not isinstance(rules, list):
            raise ValueError("invalid yara-x ndjson output: rules must be a list")
        for rule in rules:
            if not isinstance(rule, dict):
                continue
            index += 1
            identifier = _string(rule.get("identifier")) or "yara-rule"
            namespace = _string(rule.get("namespace"))
            tags = tuple(tag for tag in (_string_list(rule.get("tags"))) if tag)
            # Populate text_span with matched content from rule
            matched_content = _string(rule.get("data")) or ""
            if not matched_content:
                strings = rule.get("strings")
                if isinstance(strings, list) and strings:
                    first_match = strings[0]
                    if isinstance(first_match, dict):
                        matched_content = _string(first_match.get("data")) or ""
            if not matched_content:
                matched_content = identifier
            findings.append(
                FindingRecord(
                    tool_id=metadata.id,
                    dataset_name=case.dataset_name,
                    scenario_id=case.scenario_id,
                    finding_id=f"{metadata.id}:{case.scenario_id}:{index}",
                    severity="Critical",
                    category=_category_for(identifier, tags),
                    boundary_type=BoundaryType.RESOURCE,
                    resource=path,
                    command=None,
                    text_span=matched_content,
                    evidence=_evidence(identifier, namespace, tags, path),
                    confidence=0.8,
                    raw_output_path=raw_output_path,
                )
            )
    return tuple(findings)


def _category_for(identifier: str, tags: tuple[str, ...]) -> str:
    normalized = f"{identifier} {' '.join(tags)}".lower()
    if "secret" in normalized or "credential" in normalized or "token" in normalized:
        return "secret_or_sensitive_resource"
    if "prompt" in normalized or "inject" in normalized:
        return "prompt_injection"
    return "suspicious_pattern"


def _evidence(identifier: str, namespace: str | None, tags: tuple[str, ...], path: str | None) -> str:
    parts = [f"identifier={identifier}"]
    if namespace:
        parts.append(f"namespace={namespace}")
    if tags:
        parts.append(f"tags={','.join(tags)}")
    if path:
        parts.append(f"path={path}")
    return "; ".join(parts)


def _string(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, int):
        return str(value)
    return None


def _string_list(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in (_string(item) for item in value) if item is not None)


def _text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return _string(value) or ""
