"""Vigil adapter — LLM prompt security scanner (heuristic fallback).

The vigil package (deadbits/vigil) has Python 3.12 build compatibility issues.
This adapter uses a standalone keyword-based detection approach based on Vigil's
detection patterns for prompt injection, jailbreak, and harmful content.

When vigil becomes Python 3.12 compatible, replace the keyword scan with:
  from vigil import Vigil
  app = Vigil.from_config("openai")
  result = app.scan(text)
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from compare.tools.base import AdapterCase, AdapterResult, BoundaryType, FindingRecord, RunStatus, ToolMetadata

_SAFE_SCENARIO_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")
_TIMEOUT_SECONDS = 120

# Vigil-style detection patterns (based on Vigil's scanner architecture)
_VIGIL_PATTERNS = [
    # Prompt injection — Vigil's injection scanner patterns
    (re.compile(r"(?:ignore|disregard|bypass|override)\s+(?:all\s+)?(?:previous|prior|above)\s+(?:instructions?|commands?|directives?|messages?)", re.I),
     "prompt_injection", 0.85),
    (re.compile(r"(?:new\s+(?:system\s+)?instructions?|your\s+(?:new|updated)\s+(?:role|task|goal)\s+(?:is|should|must)\s+be)", re.I),
     "prompt_injection", 0.80),
    (re.compile(r"(?:do\s+not\s+(?:follow|obey|adhere)|refuse\s+to\s+(?:follow|comply))", re.I),
     "prompt_injection", 0.75),
    (re.compile(r"(?:BEGIN\s+NEW\s+SYSTEM\s+PROMPT|\\[NEW\s+SYSTEM\\]|###\s*SYSTEM\s*###|<<SYSTEM>>)", re.I),
     "prompt_injection", 0.90),
    (re.compile(r"(?:(?:tell|show|reveal)\s+(?:me\s+)?(?:your|the)\s+(?:system\s+)?(?:prompt|instructions?|initial\s+message))", re.I),
     "prompt_injection", 0.88),

    # Jailbreak — Vigil's canary/topic scanner patterns
    (re.compile(r"(?:jailbreak|DAN\s*mode|(?:evil|dark|malicious)\s+(?:mode|persona|role))", re.I),
     "jailbreak", 0.90),
    (re.compile(r"(?:no\s+(?:ethical|moral|legal|safety)\s+(?:constraints?|restrictions?|rules?|guidelines?))", re.I),
     "jailbreak", 0.82),
    (re.compile(r"(?:you\s+can\s+(?:now\s+)?(?:do|say|output)\s+(?:anything|whatever|anything\s+you\s+want))", re.I),
     "jailbreak", 0.80),

    # Role hijack
    (re.compile(r"(?:pretend|act|roleplay|imagine)\s+(?:you\s+are|to\s+be|that\s+you\s+are)\s+(?:a\s+|an\s+)?(?!helpful|useful|assistant)(?!honest)(?!harmless)", re.I),
     "role_hijack", 0.78),
    (re.compile(r"(?:your\s+name\s+is\s+now|I\s+(?:will|am\s+going\s+to)\s+call\s+you)", re.I),
     "role_hijack", 0.72),

    # Harmful content
    (re.compile(r"(?:write|generate|create|compose|craft|produce)\s+(?:me\s+)?(?:a\s+|an\s+)?(?:malware|ransomware|exploit|virus|trojan|backdoor|rootkit)", re.I),
     "harmful_content", 0.88),
    (re.compile(r"(?:how\s+to\s+(?:make|build|create|synthesize)\s+(?:a\s+|an\s+)?(?:bomb|weapon|poison|drug|explosive))", re.I),
     "harmful_content", 0.85),
    (re.compile(r"(?:instructions?\s+(?:for|to)\s+(?:hacking?|cracking?|phishing|stealing?|exploiting|breaking\s+into))", re.I),
     "harmful_content", 0.80),
]

CATEGORY_MAP = {
    "prompt_injection": "prompt_injection",
    "jailbreak": "jailbreak",
    "role_hijack": "role_hijack",
    "harmful_content": "hate_speech",
}


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

        text = instructions_file.read_text(encoding="utf-8", errors="replace")
        raw_output_path = output_dir / f"{case.scenario_id}.vigil.json"
        stderr_path = output_dir / f"{case.scenario_id}.vigil.stderr.txt"

        findings = []
        matched_details = []

        for i, (pattern, cat, confidence) in enumerate(_VIGIL_PATTERNS):
            for match_idx, match in enumerate(pattern.finditer(text)):
                match_text = match.group(0)
                mapped_cat = CATEGORY_MAP.get(cat, cat)
                findings.append(FindingRecord(
                    finding_id=f"{case.scenario_id}.vigil.{i}.{match_idx}",
                    scenario_id=case.scenario_id, dataset_name=case.dataset_name,
                    tool_id=self.metadata.id,
                    category=mapped_cat,
                    severity="High" if confidence > 0.8 else "Warning",
                    confidence=confidence,
                    resource=instructions_file.name,
                    command="vigil_scan",
                    text_span=match_text[:500],
                    evidence=json.dumps(
                        {"pattern": pattern.pattern, "match": match_text[:200]},
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    boundary_type=BoundaryType.CONTENT,
                    raw_output_path=raw_output_path,
                ))
                matched_details.append({
                    "pattern": pattern.pattern, "match": match_text[:200],
                    "category": cat, "confidence": confidence,
                })

        raw_output_path.write_text(
            json.dumps(matched_details, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        stderr_path.write_text("", encoding="utf-8")

        return AdapterResult(
            tool=self.metadata,
            scenario_id=case.scenario_id,
            status=RunStatus.SUCCESS if findings else RunStatus.NO_FINDINGS,
            findings=tuple(findings),
            raw_output_path=raw_output_path,
            stdout_path=raw_output_path,
            stderr_path=stderr_path,
            exit_code=0,
            duration_seconds=time.monotonic() - start,
            failure_reason=None,
        )
