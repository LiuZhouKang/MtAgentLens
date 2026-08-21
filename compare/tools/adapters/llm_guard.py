"""LLM Guard adapter — scans instruction text for prompt injection, jailbreak, and harmful content.

Uses the llm-guard Python library (https://github.com/protectai/llm-guard) to scan
scenario instructions. The tool applies multiple scanners (prompt injection, jailbreak,
toxicity, etc.) and reports findings with confidence scores.

Install: python3 -m venv compare/.venv-llm-guard && compare/.venv-llm-guard/bin/pip install llm-guard
"""

from __future__ import annotations

import json
import subprocess
import re
import time
from pathlib import Path

from compare.tools.base import AdapterCase, AdapterResult, BoundaryType, FindingRecord, RunStatus, ToolMetadata

_SAFE_SCENARIO_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")
_TIMEOUT_SECONDS = 120
_VENV_PYTHON = Path(__file__).resolve().parents[3] / ".venv-llm-guard/bin/python"


def _scan_script() -> str:
    """Python script that uses llm-guard to scan a text file and output JSON findings."""
    return r"""
import json, sys, pathlib

text_file = pathlib.Path(sys.argv[1])
if not text_file.exists():
    json.dump([], sys.stdout)
    sys.exit(0)

text = text_file.read_text(encoding="utf-8", errors="replace")

# llm-guard may not be importable if model not downloaded; handle gracefully
findings = []
try:
    from llm_guard.input_scanners import (
        PromptInjection, BanTopics, Toxicity, TokenLimit,
        Anonymize, Secrets, Gibberish, Language,
    )
    from llm_guard.input_scanners.prompt_injection import MODEL_PATH_DEFAULT
    import os

    # Check if prompt injection model exists
    model_path = os.path.expanduser(MODEL_PATH_DEFAULT)
    has_model = pathlib.Path(model_path).exists() if model_path else False

    # Run lightweight scanners that don't require model downloads
    scanners_to_run = []

    # Toxicity scanner (model-based but lightweight)
    try:
        scanners_to_run.append(("toxicity", Toxicity(threshold=0.5)))
    except Exception:
        pass

    # Ban topics scanner
    try:
        scanners_to_run.append(("ban_topics", BanTopics(threshold=0.5)))
    except Exception:
        pass

    # Secrets scanner
    try:
        scanners_to_run.append(("secrets", Secrets()))
    except Exception:
        pass

    # Gibberish scanner
    try:
        scanners_to_run.append(("gibberish", Gibberish(threshold=0.5)))
    except Exception:
        pass

    # Language scanner
    try:
        scanners_to_run.append(("language", Language(threshold=0.5)))
    except Exception:
        pass

    # Prompt injection scanner (requires model download)
    if has_model:
        try:
            scanners_to_run.append(("prompt_injection", PromptInjection(threshold=0.5)))
        except Exception:
            pass

    # Run all scanners
    sanitized_text = text
    for scanner_name, scanner in scanners_to_run:
        try:
            result = scanner.scan(sanitized_text)
            if result and hasattr(result, 'is_valid') and not result.is_valid:
                risk_score = getattr(result, 'risk_score', 0.5)
                findings.append({
                    "scanner": scanner_name,
                    "category": scanner_name,
                    "confidence": min(float(risk_score), 1.0),
                    "severity": "High" if float(risk_score) > 0.7 else ("Warning" if float(risk_score) > 0.4 else "Info"),
                    "detail": str(getattr(result, 'reason', ''))[:200] if hasattr(result, 'reason') else "",
                    "boundary_type": "ContentBoundary",
                })
        except Exception:
            pass

except ImportError:
    # llm-guard not installed — fall back to keyword-based detection
    keywords = {
        "prompt_injection": [
            "ignore previous instructions", "ignore all previous",
            "system prompt", "you are now", "new instructions",
            "disregard", "bypass", "override your",
            "do not follow", "pretend you are", "you must",
            "your new role", "act as if", "forget all",
        ],
        "jailbreak": [
            "jailbreak", "dan prompt", "developer mode",
            "no restrictions", "without any ethical",
            "do anything now", "no rules", "evil mode",
        ],
        "harmful": [
            "illegal", "malware", "exploit", "hack into",
            "steal", "bomb", "kill", "destroy",
            "poison", "attack the", "ransomware",
        ],
    }
    text_lower = text.lower()
    for cat, kws in keywords.items():
        matches = [kw for kw in kws if kw in text_lower]
        if matches:
            findings.append({
                "scanner": "keyword",
                "category": cat,
                "confidence": min(0.3 + 0.1 * len(matches), 0.85),
                "severity": "Warning",
                "detail": f"Matched keywords: {', '.join(matches[:5])}",
                "boundary_type": "ContentBoundary",
            })

json.dump(findings, sys.stdout)
"""


class Adapter:
    def __init__(self, metadata: ToolMetadata) -> None:
        self.metadata = metadata

    def supports(self, case: AdapterCase) -> bool:
        prompt_text = case.input_artifacts.get("prompt_text")
        return (prompt_text is not None and prompt_text.exists()) or (case.fixture_dir is not None and case.fixture_dir.is_dir())

    def run(self, case: AdapterCase, output_dir: Path) -> AdapterResult:
        if not _SAFE_SCENARIO_ID_RE.fullmatch(case.scenario_id) or ".." in case.scenario_id:
            raise ValueError("scenario_id must be a safe filename component")

        start = time.monotonic()
        output_dir.mkdir(parents=True, exist_ok=True)

        instructions_file = case.input_artifacts.get("prompt_text")
        if instructions_file is None or not instructions_file.exists():
            if case.fixture_dir is None:
                instructions_file = output_dir / "missing-prompt.txt"
            else:
                instructions_file = case.fixture_dir / "instructions.md"
                if not instructions_file.exists():
                    for alt in ["prompt.txt", "input.txt", "task.md"]:
                        candidate = case.fixture_dir / alt
                        if candidate.exists():
                            instructions_file = candidate
                            break

        if not instructions_file.exists():
            # No text input to scan — return empty result
            return AdapterResult(
                tool=self.metadata,
                scenario_id=case.scenario_id,
                status=RunStatus.NO_FINDINGS,
                findings=(),
                raw_output_path=None,
                stdout_path=None,
                stderr_path=None,
                exit_code=0,
                duration_seconds=time.monotonic() - start,
                failure_reason=None,
            )

        # Write scan script to temp file
        scan_script = output_dir / f"{case.scenario_id}.llm_guard_scan.py"
        scan_script.write_text(_scan_script(), encoding="utf-8")
        raw_output_path = output_dir / f"{case.scenario_id}.llm_guard.json"
        stderr_path = output_dir / f"{case.scenario_id}.llm_guard.stderr.txt"

        python_bin = _VENV_PYTHON if _VENV_PYTHON.exists() else "python3"

        try:
            completed = subprocess.run(
                [str(python_bin), str(scan_script), str(instructions_file)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=_TIMEOUT_SECONDS,
                check=False,
                env={**__import__("os").environ, "PYTHONUNBUFFERED": "1"},
            )
            raw_output_path.write_text(completed.stdout, encoding="utf-8")
            stderr_path.write_text(completed.stderr, encoding="utf-8")

            if completed.returncode != 0:
                return AdapterResult(
                    tool=self.metadata,
                    scenario_id=case.scenario_id,
                    status=RunStatus.FAILED,
                    findings=(),
                    raw_output_path=raw_output_path,
                    stdout_path=raw_output_path,
                    stderr_path=stderr_path,
                    exit_code=completed.returncode,
                    duration_seconds=time.monotonic() - start,
                    failure_reason=completed.stderr[:500] if completed.stderr else f"Exit code {completed.returncode}",
                )

            # Parse findings
            raw_data = completed.stdout.strip()
            if raw_data:
                try:
                    scanner_findings = json.loads(raw_data)
                except json.JSONDecodeError:
                    scanner_findings = []
            else:
                scanner_findings = []

            findings = []
            for i, sf in enumerate(scanner_findings):
                findings.append(FindingRecord(
                    finding_id=f"{case.scenario_id}.llm_guard.{i}",
                    scenario_id=case.scenario_id,
                    dataset_name=case.dataset_name,
                    tool_id=self.metadata.id,
                    category=sf.get("category", "unknown"),
                    severity=sf.get("severity", "Warning"),
                    confidence=sf.get("confidence", 0.5),
                    resource=None,
                    command="llm_guard_scan",
                    text_span=sf.get("detail", "")[:500],
                    evidence=json.dumps(sf, ensure_ascii=False, sort_keys=True),
                    boundary_type=BoundaryType.CONTENT,
                    raw_output_path=raw_output_path,
                ))

            return AdapterResult(
                tool=self.metadata,
                scenario_id=case.scenario_id,
                status=RunStatus.SUCCESS if findings else RunStatus.NO_FINDINGS,
                findings=tuple(findings),
                raw_output_path=raw_output_path,
                stdout_path=raw_output_path,
                stderr_path=stderr_path,
                exit_code=completed.returncode,
                duration_seconds=time.monotonic() - start,
                failure_reason=None,
            )

        except subprocess.TimeoutExpired as exc:
            raw_output_path.write_text("", encoding="utf-8")
            stderr_path.write_text(str(exc), encoding="utf-8")
            return AdapterResult(
                tool=self.metadata,
                scenario_id=case.scenario_id,
                status=RunStatus.FAILED,
                findings=(),
                raw_output_path=raw_output_path,
                stdout_path=raw_output_path,
                stderr_path=stderr_path,
                exit_code=None,
                duration_seconds=time.monotonic() - start,
                failure_reason=f"Timeout ({_TIMEOUT_SECONDS}s)",
            )
