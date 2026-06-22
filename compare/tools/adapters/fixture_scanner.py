from __future__ import annotations

import json
import re
import time
from pathlib import Path

from compare.tools.base import AdapterCase, AdapterResult, BoundaryType, FindingRecord, RunStatus, ToolMetadata

_PATTERNS = (
    re.compile(r"(?i)[\"']?(api[_-]?key|secret|token|password)[\"']?\s*[:=]"),
    re.compile(r"/etc/shadow"),
    re.compile(r"\.ssh"),
)
_SAFE_SCENARIO_ID_RE = re.compile(r"^[a-zA-Z0-9_.-]+$")
_MAX_FILE_BYTES = 1_048_576


class Adapter:
    def __init__(self, metadata: ToolMetadata) -> None:
        self.metadata = metadata

    def supports(self, case: AdapterCase) -> bool:
        return case.fixture_dir is not None or case.scenario_json.exists()

    def run(self, case: AdapterCase, output_dir: Path) -> AdapterResult:
        if not _SAFE_SCENARIO_ID_RE.fullmatch(case.scenario_id) or ".." in case.scenario_id:
            raise ValueError("scenario_id must be a safe filename component")

        start = time.monotonic()
        output_dir.mkdir(parents=True, exist_ok=True)
        raw_output_path = output_dir / f"{case.scenario_id}.fixture-scanner.json"

        matches = self._scan_case(case)
        findings = tuple(
            FindingRecord(
                tool_id=self.metadata.id,
                dataset_name=case.dataset_name,
                scenario_id=case.scenario_id,
                finding_id=f"{self.metadata.id}:{case.scenario_id}:{index}",
                severity="Warning",
                category="secret_or_sensitive_resource",
                boundary_type=BoundaryType.RESOURCE,
                resource=path.name,
                command=None,
                text_span=match.group(0),
                evidence=f"pattern={pattern.pattern}; path={path}",
                confidence=0.7,
                raw_output_path=raw_output_path,
            )
            for index, (path, pattern, match) in enumerate(matches, start=1)
        )

        raw_payload = {
            "tool_id": self.metadata.id,
            "dataset_name": case.dataset_name,
            "scenario_id": case.scenario_id,
            "findings": [finding.to_json_dict() for finding in findings],
        }
        raw_output_path.write_text(json.dumps(raw_payload, indent=2, sort_keys=True), encoding="utf-8")

        return AdapterResult(
            tool=self.metadata,
            scenario_id=case.scenario_id,
            status=RunStatus.SUCCESS if findings else RunStatus.NO_FINDINGS,
            findings=findings,
            raw_output_path=raw_output_path,
            stdout_path=None,
            stderr_path=None,
            exit_code=0,
            duration_seconds=time.monotonic() - start,
            failure_reason=None,
        )

    def _scan_case(self, case: AdapterCase) -> list[tuple[Path, re.Pattern[str], re.Match[str]]]:
        matches: list[tuple[Path, re.Pattern[str], re.Match[str]]] = []
        paths: list[Path] = []
        if case.scenario_json.exists():
            paths.append(case.scenario_json)
        if case.fixture_dir is not None and case.fixture_dir.exists():
            paths.extend(path for path in sorted(case.fixture_dir.rglob("*")) if path.is_file() and not path.is_symlink())

        for path in paths:
            match = self._first_match(path)
            if match is not None:
                pattern, text_match = match
                matches.append((path, pattern, text_match))
        return matches

    def _first_match(self, path: Path) -> tuple[re.Pattern[str], re.Match[str]] | None:
        try:
            if path.stat().st_size > _MAX_FILE_BYTES:
                return None
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return None

        for pattern in _PATTERNS:
            match = pattern.search(text)
            if match is not None:
                return pattern, match
        return None
