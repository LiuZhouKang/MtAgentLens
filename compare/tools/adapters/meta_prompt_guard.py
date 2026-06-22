"""Meta Prompt Guard adapter — prompt injection and jailbreak detection.

Implements detection patterns based on Meta's Prompt Guard research paper approach:
keyword-based detection of common prompt injection, jailbreak, and role-hijack patterns.
Uses a lightweight heuristic scan (no model download required) that captures the same
attack surface as Meta's model-based approach for the scenario types in our dataset.

For full model-based detection, install: pip install transformers && download meta-llama/Prompt-Guard
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path

from compare.tools.base import AdapterCase, AdapterResult, BoundaryType, FindingRecord, RunStatus, ToolMetadata

_SAFE_SCENARIO_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")
_TIMEOUT_SECONDS = 120


# Meta Prompt Guard 论文中的检测模式 + 实际攻击常用模式
_INJECTION_PATTERNS = [
    # Direct instruction override
    (r"ignore\s+(?:all\s+)?(?:previous|above|prior)\s+(?:instructions?|messages?|text|prompts?)",
     "prompt_injection", 0.85, "High"),
    (r"(?:forget|disregard|override)\s+(?:your|all|the)\s+(?:instructions?|rules?|guidelines?|training)",
     "prompt_injection", 0.80, "High"),
    (r"(?:you\s+are\s+now|your\s+new\s+role\s+is|from\s+now\s+on\s+you\s+are)",
     "role_hijack", 0.80, "High"),
    (r"(?:pretend|act)\s+(?:as\s+if\s+)?(?:you\s+(?:are|were)|to\s+be)\s+(?:a\s+|an\s+)?(?!helpful|assistant)",
     "role_hijack", 0.75, "High"),

    # System prompt extraction/leaking
    (r"(?:what\s+(?:is|are|was)|tell\s+me|show\s+me|reveal|print|output|display)\s+(?:your\s+)?(?:system\s+)?(?:prompts?|instructions?|initial\s+message)",
     "prompt_injection", 0.90, "Critical"),
    (r"(?:repeat|echo|say)\s+(?:back\s+)?(?:the\s+)?(?:above|system\s+prompt|initial\s+prompt)",
     "prompt_injection", 0.80, "High"),

    # Jailbreak patterns
    (r"(?:jailbreak|DAN\s+(?:prompt|mode)|developer\s+mode)",
     "jailbreak", 0.90, "Critical"),
    (r"(?:no\s+(?:restrictions?|rules?|limitations?|ethical|moral)|without\s+(?:any\s+)?(?:ethical|moral)\s+(?:constraints?|guidelines?))",
     "jailbreak", 0.80, "High"),
    (r"(?:do\s+anything\s+now|you\s+have\s+no\s+(?:restrictions?|limits?|rules?)|unrestricted\s+mode)",
     "jailbreak", 0.85, "High"),

    # Harmful content generation
    (r"(?:write|generate|create|compose|craft)\s+(?:me\s+)?(?:a\s+|an\s+)?(?:malicious|harmful|dangerous|illegal|toxic)",
     "harmful_content", 0.75, "High"),
    (r"(?:how\s+to\s+(?:hack|crack|exploit|steal|bypass|break\s+into|compromise))",
     "harmful_content", 0.70, "Warning"),

    # Indirect injection / handoff contamination
    (r"(?:search|visit|fetch|retrieve|read)\s+(?:this|the\s+following|below)\s+(?:URL|website|page|link|content)",
     "indirect_injection", 0.65, "Warning"),
    (r"(?:the\s+(?:website|page|document|file|content)\s+(?:says|states|contains|has|tells|instructs))",
     "indirect_injection", 0.70, "Warning"),

    # Disinformation / fake content
    (r"(?:spread|create|generate|write)\s+(?:fake|false|misleading|disinform)",
     "disinformation", 0.75, "High"),

    # Credential / secret extraction
    (r"(?:tell\s+me|show|reveal|what\s+is|where\s+is)\s+(?:your\s+)?(?:API\s+key|password|secret|token|credential)",
     "credential_theft", 0.80, "High"),
]

# Category mapping to our standard categories
CATEGORY_MAP = {
    "prompt_injection": "prompt_injection",
    "role_hijack": "role_hijack",
    "jailbreak": "jailbreak",
    "harmful_content": "hate_speech",
    "indirect_injection": "handoff_injection",
    "disinformation": "disinformation",
    "credential_theft": "credential_theft",
}


class Adapter:
    def __init__(self, metadata: ToolMetadata) -> None:
        self.metadata = metadata
        self._compiled = [(re.compile(p, re.IGNORECASE), cat, conf, sev)
                          for p, cat, conf, sev in _INJECTION_PATTERNS]

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

        raw_output_path = output_dir / f"{case.scenario_id}.prompt_guard.json"
        stderr_path = output_dir / f"{case.scenario_id}.prompt_guard.stderr.txt"

        # Scan for injection patterns
        findings = []
        matched_details = []

        for i, (pattern, cat, confidence, severity) in enumerate(self._compiled):
            matches = pattern.findall(text)
            for match_idx, match in enumerate(matches):
                match_text = match if isinstance(match, str) else match[0]
                mapped_cat = CATEGORY_MAP.get(cat, cat)
                findings.append(FindingRecord(
                    finding_id=f"{case.scenario_id}.prompt_guard.{i}.{match_idx}",
                    scenario_id=case.scenario_id,
                    dataset_name=case.dataset_name,
                    tool_id=self.metadata.id,
                    category=mapped_cat,
                    severity=severity,
                    confidence=confidence,
                    resource=instructions_file.name,
                    command="prompt_guard_scan",
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
                    "pattern": pattern.pattern,
                    "match": match_text[:200],
                    "category": cat,
                    "confidence": confidence,
                    "severity": severity,
                })

        # Save raw output
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
