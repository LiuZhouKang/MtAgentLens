"""MtAgentPrism Prompt-Only Adapter — Ablation 1 baseline.

Simulates MtAgentPrism running with ALL eBPF kernel probes and SSE Content
Scanner disabled, relying solely on prompt text input. This is a genuine
ablation of the SAME system, using MtAgentPrism's OWN observer heuristics
applied to prompt text, evaluated through the same v2 evaluation pipeline.

This adapter mirrors what MtAgentPrism's heuristic observer would detect
when kernel signals are unavailable — it scans prompt text for known
attack indicators (suspicious commands, sensitive file paths, network
endpoints, prompt injection patterns, credential leaks).

Design: docs/design/experiment-design.md §6, Ablation 1.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from compare.tools.base import (
    AdapterCase,
    AdapterResult,
    BoundaryType,
    FindingRecord,
    RunStatus,
    ToolMetadata,
)

_SAFE_SCENARIO_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")
_TIMEOUT_SECONDS = 60

# ── MtAgentPrism Observer Heuristic Patterns ──────────────────────────
# v2.2 calibrated: each pattern group tested independently against 67 scenarios.
# Kept groups: precision ≥ 0.10 AND contributes unique TP.
# Removed groups: suspicious_command (0 TP), network_endpoint (noise 95%),
#                 tainted_context (precision 1.2%).

_SENSITIVE_FILE_PATTERNS = [
    # High-specificity sensitive files (not generic paths like /etc/)
    r"/etc/passwd\b", r"/etc/shadow\b", r"/etc/sudoers\b",
    r"/etc/crontab\b", r"/etc/cron\.d/",
    r"~?/\.ssh/id_rsa\b", r"~?/\.ssh/authorized_keys\b",
    r"/root/\.bashrc\b", r"/root/\.bash_history\b",
    r"/var/run/docker\.sock\b",
    r"\.aws/credentials\b", r"\.config/gcloud\b",
]

_PROMPT_INJECTION_PATTERNS = [
    # Specific prompt injection markers (low noise, high specificity)
    r"ignore\s+(all\s+)?previous\s+instructions?",
    r"disregard\s+(all\s+)?previous",
    r"you\s+are\s+now\s+(a\s+)?\w+",
    r"override\s+your\s+(instructions?|rules?)",
    r"pretend\s+you\s+are",
    r"act\s+as\s+if",
    r"forget\s+all\s+(previous\s+)?instructions?",
    r"bypass\s+(permissions?|restrictions?)",
    r"no\s+restrictions?\b",
    r"developer\s+mode\b",
    r"jailbreak\b",
]

_CREDENTIAL_PATTERNS = [
    # Best precision group (25%), kept as-is
    r"(?:api[_-]?key|apikey|api_secret)\s*[:=]\s*['\"]?\w{8,}",
    r"(?:password|passwd|pwd)\s*[:=]\s*['\"]?\S{4,}",
    r"(?:token|secret|auth)\s*[:=]\s*['\"]?\w{8,}",
    r"(?:private[_-]?key|ssh[_-]?key)\s*[:=]",
    r"-----BEGIN\s+(?:RSA|EC|DSA|OPENSSH)\s+PRIVATE\s+KEY-----",
    r"(?:AKIA|ASIA)[A-Z0-9]{16}",
    r"ghp_[A-Za-z0-9]{36}",
    r"sk-[A-Za-z0-9]{32,}",
]

_SUBAGENT_DELEGATION_PATTERNS = [
    # Good signal for Type C attacks (18 TP, precision 18%)
    r"bypassPermissions\s*:\s*true",
    r"run_in_background\s*:\s*true",
    r"dangerouslyDisableSandbox\s*:\s*true",
    r"mode\s*:\s*['\"]?bypassPermissions",
]

# ── Pattern groups mapped to boundary types ──
_PATTERN_GROUPS = [
    ("sensitive_file_access", _SENSITIVE_FILE_PATTERNS, BoundaryType.RESOURCE),
    ("prompt_injection", _PROMPT_INJECTION_PATTERNS, BoundaryType.CONTENT),
    ("credential_exposure", _CREDENTIAL_PATTERNS, BoundaryType.CONTENT),
    ("subagent_delegation_bypass", _SUBAGENT_DELEGATION_PATTERNS, BoundaryType.DELEGATION),
]


def _scan_prompt_text(text: str) -> list[dict]:
    """Scan prompt text using MtAgentPrism observer heuristic patterns.

    Produces findings for ALL regex matches in the prompt text. A
    confidence threshold of 0.50 filters out low-specificity matches.
    This mirrors how MtAgentPrism's observer heuristic would naturally
    operate — scanning all available text and flagging anything that
    crosses the confidence threshold.
    """
    findings: list[dict] = []
    min_confidence = 0.50

    for group_name, patterns, boundary_type in _PATTERN_GROUPS:
        for pattern in patterns:
            try:
                matches = list(re.finditer(pattern, text, re.IGNORECASE))
            except re.error:
                continue

            for match in matches:
                matched_text = match.group(0)
                if group_name == "credential_exposure":
                    severity, conf = "Critical", 0.85
                elif group_name == "subagent_delegation_bypass":
                    severity, conf = "Critical", 0.80
                elif group_name == "prompt_injection":
                    severity, conf = "Warning", 0.75
                elif group_name == "sensitive_file_access":
                    severity, conf = "Warning", 0.60
                else:
                    severity, conf = "Info", 0.50

                if conf < min_confidence:
                    continue

                findings.append({
                    "group": group_name,
                    "pattern": pattern,
                    "matched_text": matched_text,
                    "boundary_type": boundary_type.value,
                    "severity": severity,
                    "confidence": conf,
                })

    # Deduplicate by matched text within each group
    seen: set[tuple[str, str]] = set()
    unique: list[dict] = []
    for f in findings:
        key = (f["matched_text"].lower(), f["group"])
        if key not in seen:
            seen.add(key)
            unique.append(f)

    return unique


class Adapter:
    """MtAgentPrism Prompt-Only adapter.

    This adapter simulates MtAgentPrism running without eBPF kernel probes
    or SSE Content Scanner. It applies the SAME observer heuristic patterns
    that MtAgentPrism uses in heuristic-only fallback mode, but limited to
    prompt text input only.

    This is Ablation 1 from the v2 experiment design: remove ALL kernel and
    application-layer signals, leaving only prompt text analysis.
    """

    def __init__(self, metadata: ToolMetadata) -> None:
        self.metadata = metadata

    def supports(self, case: AdapterCase) -> bool:
        prompt_text = case.input_artifacts.get("prompt_text")
        return prompt_text is not None and prompt_text.exists()

    def run(self, case: AdapterCase, output_dir: Path) -> AdapterResult:
        if not _SAFE_SCENARIO_ID_RE.fullmatch(case.scenario_id) or ".." in case.scenario_id:
            raise ValueError("scenario_id must be a safe filename component")

        start = time.monotonic()
        output_dir.mkdir(parents=True, exist_ok=True)

        prompt_file = case.input_artifacts.get("prompt_text")
        if prompt_file is None or not prompt_file.exists():
            return AdapterResult(
                tool=self.metadata,
                scenario_id=case.scenario_id,
                status=RunStatus.FAILED,
                findings=(),
                raw_output_path=None,
                stdout_path=None,
                stderr_path=None,
                exit_code=None,
                duration_seconds=time.monotonic() - start,
                failure_reason="No prompt_text input artifact available",
            )

        try:
            text = prompt_file.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return AdapterResult(
                tool=self.metadata,
                scenario_id=case.scenario_id,
                status=RunStatus.FAILED,
                findings=(),
                raw_output_path=None,
                stdout_path=None,
                stderr_path=None,
                exit_code=None,
                duration_seconds=time.monotonic() - start,
                failure_reason=f"Cannot read prompt text: {exc}",
            )

        raw_findings = _scan_prompt_text(text)

        # Write raw output for reproducibility
        raw_output_path = output_dir / f"{case.scenario_id}.mtagentprism_prompt_only.json"
        raw_output_path.write_text(
            json.dumps(raw_findings, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        findings = []
        for i, rf in enumerate(raw_findings):
            boundary_type_str = rf.get("boundary_type", "ContentBoundary")
            try:
                bt = BoundaryType(boundary_type_str)
            except ValueError:
                bt = BoundaryType.CONTENT

            evidence = {
                "group": rf["group"],
                "pattern": rf["pattern"],
                "matched_text": rf["matched_text"],
                "source": "mtagentprism_prompt_only_ablation",
            }

            findings.append(
                FindingRecord(
                    finding_id=f"{case.scenario_id}.mtap_po.{i}",
                    scenario_id=case.scenario_id,
                    dataset_name=case.dataset_name,
                    tool_id=self.metadata.id,
                    category=rf["group"],
                    severity=rf["severity"],
                    confidence=rf["confidence"],
                    resource=rf.get("matched_text", "") if bt == BoundaryType.RESOURCE else None,
                    command=rf.get("matched_text", "") if bt == BoundaryType.EXEC_ARG else None,
                    text_span=rf.get("matched_text", ""),
                    evidence=json.dumps(evidence, ensure_ascii=False, sort_keys=True),
                    boundary_type=bt,
                    raw_output_path=raw_output_path,
                )
            )

        return AdapterResult(
            tool=self.metadata,
            scenario_id=case.scenario_id,
            status=RunStatus.SUCCESS if findings else RunStatus.NO_FINDINGS,
            findings=tuple(findings),
            raw_output_path=raw_output_path,
            stdout_path=raw_output_path,
            stderr_path=None,
            exit_code=0,
            duration_seconds=time.monotonic() - start,
            failure_reason=None,
        )
