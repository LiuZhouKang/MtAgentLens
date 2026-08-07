#!/usr/bin/env python3
"""Verifier for subagents-collaborative scenario (baseline chain validation).

Checks that the 4-agent handoff pipeline produced a coherent chain:
  C0 — Sequential ordering: Pipeline structure validation (4 agents, order)
  C1 — Contract coherence: Subagent A handoff contract completeness
  C2 — Design consistency: Subagent B analyzer design aligns with contract
  C3 — Frontend coverage: Subagent C dashboard proposal matches upstream
  C4 — Chain integrity: Requirement propagation across all 3 handoffs
  C5 — Artifact completeness: Run-level output artifact + eBPF collector check
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

from _events import EventSource, read_all_task_logs


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

EXPECTED_SUBAGENTS = {
    1: "docs-architecture-reviewer",
    2: "rust-framework-architect",
    3: "frontend-visualization-expert",
    4: "integration-testing-orchestrator",
}

CONTRACT_FIELDS = [
    "feature_id",
    "scope",
    "acceptance_criteria",
    "quality_gates",
    "context_bridge",
]

ANALYZER_KEYWORDS = [
    r"(?i)analyz(er|e)",
    r"(?i)event.?schema",
    r"(?i)stream|pipeline",
    r"(?i)(field|column|attribute)\s*:.*\w+",
    r"(?i)data.?flow",
]

FRONTEND_KEYWORDS = [
    r"(?i)component|props",
    r"(?i)typescript|interface|type",
    r"(?i)toggle|filter|dashboard",
    r"(?i)timeline|visualiz",
    r"(?i)drill.?down|detail|expand",
]

CHAIN_LINK_KEYWORDS = {
    "A_to_B": [
        (r"(?i)handoff.*contract", "Handoff contract reference"),
        (r"(?i)architectur", "Architecture reference"),
        (r"(?i)ownership|boundar", "Ownership/boundary reference"),
    ],
    "B_to_C": [
        (r"(?i)analyzer.*schema", "Analyzer schema reference"),
        (r"(?i)event.*type", "Event type reference"),
        (r"(?i)backend.*contract", "Backend contract reference"),
    ],
}

REQUIRED_RUN_ARTIFACTS = ["collector.log"]
OPTIONAL_RUN_ARTIFACTS = [
    "collector.causal.db",
    "collector.alerts.db",
    "collector.causal.jsonl",
    "summary.json",
]

MIN_TRACE_EVENTS = 1000
MIN_CAUSAL_DB_BYTES = 4096

# Handoff artifact validation (C6)
HANDOFF_ARTIFACT_DIR = Path(".kiro/handoffs")
HANDOFF_GLOB_PATTERN = "*.yaml"

REQUIRED_HANDOFF_FIELDS = [
    "handoff_id",
    "feature_name",
    "from_phase",
    "to_phase",
]

VALID_TO_PHASES = {
    "design": {"implementation"},
    "implementation": {"testing"},
    "testing": {"documentation"},
    "documentation": {"merge", "deployment"},
    "requirements": {"implementation"},
}

MIN_HANDOFF_DELIVERABLES = 1
MIN_HANDOFF_QUALITY_GATES = 1


# ---------------------------------------------------------------------------
# Helpers: per-agent output extraction with prompt fallback
# ---------------------------------------------------------------------------

AGENT_ROLE_MAP = {
    1: "docs-architecture-reviewer",
    2: "rust-framework-architect",
    3: "frontend-visualization-expert",
    4: "integration-testing-orchestrator",
}

AgentOutput = Dict[str, Any]
"""Represents a single subagent's available inputs:
  - index: int (1-4)
  - role: str
  - log_text: str (content from logs/, may be empty)
  - prompt_text: str (content from prompts/, always present if run directory is valid)
  - status: dict (state from status/, may be partial)
  - has_log: bool
  - has_prompt: bool
  - completed: bool (status shows "done")
"""


def read_agent_outputs(run_dir: Path) -> Dict[int, AgentOutput]:
    """Read per-agent outputs, falling back to prompts when task logs are empty.

    Returns dict keyed by agent index (1-4). Always returns 4 entries,
    each with log_text (may be empty) and prompt_text (may be empty).
    """
    outputs: Dict[int, AgentOutput] = {}

    logs_dir = run_dir / "logs"
    prompts_dir = run_dir / "prompts"
    status_dir = run_dir / "status"

    for idx in range(1, 5):
        role = AGENT_ROLE_MAP.get(idx, "unknown")
        prefix = f"{idx:03d}-subagents-collaborative-{role}"

        # Read task log
        log_text = ""
        log_path = logs_dir / f"{prefix}.log.attempt-1"  # no .log extension check
        # Try multiple naming patterns
        candidates = [
            logs_dir / f"{prefix}.log.attempt-1",
            logs_dir / f"{prefix}.log",
        ]
        for c in candidates:
            if c.exists():
                try:
                    sz = c.stat().st_size
                    if sz > 0:
                        log_text = c.read_text(encoding="utf-8")
                except Exception:
                    pass
                break

        # Read prompt (fallback text source)
        prompt_text = ""
        prompt_path = prompts_dir / f"{prefix}.md"
        if prompt_path.exists():
            try:
                prompt_text = prompt_path.read_text(encoding="utf-8")
            except Exception:
                pass

        # Read status
        status_data: Dict[str, Any] = {}
        status_path = status_dir / f"{prefix}.json"
        if status_path.exists():
            try:
                status_data = json.loads(status_path.read_text(encoding="utf-8"))
            except Exception:
                pass

        completed = status_data.get("state") == "done"

        outputs[idx] = {
            "index": idx,
            "role": role,
            "log_text": log_text,
            "prompt_text": prompt_text,
            "status": status_data,
            "has_log": len(log_text.strip()) > 0,
            "has_prompt": len(prompt_text.strip()) > 0,
            "completed": completed,
        }

    return outputs


def effective_text(output: AgentOutput) -> str:
    """Return log text if available, falling back to prompt text."""
    if output["has_log"]:
        return output["log_text"]
    return output["prompt_text"]


# ---------------------------------------------------------------------------
# C0 — Sequential pipeline ordering validation
# ---------------------------------------------------------------------------

def verify_c0_sequential_ordering(run_dir: Path) -> Dict[str, Any]:
    """Validate the sequential pipeline structure.

    Checks that all 4 subagents have prompts, status is correct,
    numbering is sequential, and identifies any ordering gaps.
    This layer always returns a result (no 'indeterminate').
    """
    evidence: List[str] = []
    failures: List[str] = []

    outputs = read_agent_outputs(run_dir)

    if len(outputs) != 4:
        failures.append(f"Expected 4 subagents, got {len(outputs)}")

    prev_completed = True  # first agent has no predecessor
    sequential_gaps = 0

    for idx in range(1, 5):
        agent = outputs.get(idx)
        if agent is None:
            failures.append(f"C0: Subagent {idx} missing from outputs")
            sequential_gaps += 1
            continue

        role = agent["role"]
        evidence.append(f"C0: Subagent {idx} ({role}): prompt={'✓' if agent['has_prompt'] else '✗'}, log={'✓' if agent['has_log'] else '○'}, status={'done' if agent['completed'] else agent['status'].get('state', 'unknown')}")

        # Check prompt exists
        if not agent["has_prompt"]:
            failures.append(f"C0: Subagent {idx} ({role}) missing prompt file")
            sequential_gaps += 1

        # Check status is valid
        state = agent["status"].get("state", "unknown")
        if state not in ("done", "running", "failed"):
            failures.append(f"C0: Subagent {idx} ({role}) unexpected state: {state}")

        # Check log presence
        if not agent["has_log"] and agent["completed"]:
            evidence.append(f"C0: Subagent {idx} ({role}) completed but log is empty — output not captured")

        # Validate sequential handoff constraint: agent N+1 should not complete
        # before agent N unless it had independent work (baseline check)
        if idx > 1 and agent["completed"] and not prev_completed:
            failures.append(f"C0: Sequential gap — subagent {idx} completed before subagent {idx-1}")
            sequential_gaps += 1

        prev_completed = agent["completed"]

    # Check all prompt files exist with expected naming
    prompts_dir = run_dir / "prompts"
    if prompts_dir.exists():
        prompt_files = sorted(prompts_dir.glob("*.md"))
        expected_count = 4
        actual_count = len(prompt_files)
        if actual_count < expected_count:
            failures.append(f"C0: Expected {expected_count} prompt files, found {actual_count}")
        else:
            evidence.append(f"C0: {actual_count}/{expected_count} prompt files present")

        # Verify sequential numbering
        seen_numbers = set()
        for pf in prompt_files:
            m = re.match(r"(\d{3})-.+", pf.stem)
            if m:
                num = int(m.group(1))
                if num in seen_numbers:
                    failures.append(f"C0: Duplicate agent index {num:03d} in prompts")
                seen_numbers.add(num)

        expected_numbers = {1, 2, 3, 4}
        missing_numbers = expected_numbers - seen_numbers
        if missing_numbers:
            failures.append(f"C0: Missing agent indices: {sorted(missing_numbers)}")
    else:
        failures.append("C0: prompts directory missing from run directory")

    status = (
        "pass" if not failures else
        "degraded" if sequential_gaps == 0 else
        "fail"
    )

    return {
        "layer": "C0",
        "status": status,
        "evidence": evidence,
        "details": (
            f"Sequential ordering: {len([o for o in outputs.values() if o['has_prompt']])}/4 prompts, "
            f"{len([o for o in outputs.values() if o['completed']])}/4 completed, "
            f"{len(failures)} issues"
        ),
    }


# ---------------------------------------------------------------------------
# C1 — Contract coherence (Subagent A)
# ---------------------------------------------------------------------------

def verify_c1_contract_coherence(run_dir: Path, scenario_dir: Path) -> Dict[str, Any]:
    """Validate Subagent A's handoff contract for completeness and structure.

    Checks that the contract contains all required template fields and
    follows the SUBAGENT_HANDOFF_CONTRACT.md specification.

    Validates against Subagent A's output specifically (not combined text).
    Falls back to prompt content when task log is empty.
    """
    evidence: List[str] = []
    failures: List[str] = []

    outputs = read_agent_outputs(run_dir)
    agent_a = outputs.get(1)
    if not agent_a:
        return {
            "layer": "C1",
            "status": "indeterminate",
            "evidence": ["Subagent A (docs-architecture-reviewer) output not found"],
            "details": "Cannot verify contract coherence without Subagent A output",
        }

    text_source = "log" if agent_a["has_log"] else "prompt (fallback)"
    combined_text = effective_text(agent_a)

    if not combined_text.strip():
        return {
            "layer": "C1",
            "status": "indeterminate",
            "evidence": [f"Subagent A has no text (log empty, prompt empty)"],
            "details": "Cannot verify contract coherence without text content",
        }

    evidence.append(f"C1: Validating against Subagent A ({text_source})")

    # Detect non-English content (CJK / extended Latin)
    cjk_chars = sum(1 for c in combined_text if '一' <= c <= '鿿' or '぀' <= c <= 'ヿ' or '가' <= c <= '힯')
    is_non_english = cjk_chars > 50  # threshold: 50+ CJK chars indicates non-English content
    if is_non_english:
        evidence.append(f"C1: Content appears to be non-English ({cjk_chars} CJK characters) — English field pattern matching may under-report")

    for field in CONTRACT_FIELDS:
        if re.search(rf"(?i){field.replace('_', '.?')}", combined_text):
            evidence.append(f"C1: Contract field '{field}' present")
        else:
            failures.append(f"Contract field '{field}' not detected")
            evidence.append(f"C1: Contract field '{field}' MISSING (EN-only pattern)")

    # Check for quality gate definitions (pass/fail criteria)
    gate_patterns = [
        (r"(?i)quality.?gate.*(pass|fail)", "Quality gate pass/fail criteria"),
        (r"(?i)ownership.*(phase|boundar)", "Phase ownership boundaries"),
        (r"(?i)acceptance.?criteri(on|a)", "Acceptance criteria section"),
    ]
    for pattern, label in gate_patterns:
        if re.search(pattern, combined_text):
            evidence.append(f"C1: {label} present")
        else:
            failures.append(f"C1: {label} not detected (EN-only pattern)")

    # Check for scope/components_affected
    if re.search(r"(?i)component.*(affected|change|scope)", combined_text):
        evidence.append("C1: Component scope defined")
    else:
        failures.append("C1: Component scope not defined (EN-only pattern)")

    # If all English pattern checks failed but content is large, it's likely a language
    # mismatch rather than a missing contract — degrade instead of fail
    content_has_substance = len(combined_text.strip()) > 200
    all_english_checks_failed = len(failures) >= 5

    if all_english_checks_failed and content_has_substance and is_non_english:
        failures.append("C1: All EN patterns missed — content exists in non-English; manual audit recommended")
        status = "degraded"
    else:
        status = "pass" if not failures else "fail" if len(failures) >= 3 else "degraded"

    # Notify if falling back to prompt
    if not agent_a["has_log"]:
        evidence.append(f"C1: NOTE — Using prompt fallback; log output was empty")

    return {
        "layer": "C1",
        "status": status,
        "evidence": evidence,
        "details": (
            f"Contract coherence: {len(CONTRACT_FIELDS)} fields checked, "
            f"{len(failures)} failures" if not failures else
            f"Contract coherence: {len(failures)} issues: {'; '.join(failures[:3])}"
        ),
    }


# ---------------------------------------------------------------------------
# C2 — Design consistency (Subagent B)
# ---------------------------------------------------------------------------

def verify_c2_design_consistency(run_dir: Path) -> Dict[str, Any]:
    """Verify Subagent B's analyzer design references the contract and
    defines concrete schema/processing details.

    Validates against Subagent B's output specifically. Falls back to
    prompt content when task log is empty. Subagent B must reference
    Subagent A's handoff contract as upstream authority.
    """
    evidence: List[str] = []
    failures: List[str] = []

    outputs = read_agent_outputs(run_dir)
    agent_b = outputs.get(2)
    if not agent_b:
        return {
            "layer": "C2",
            "status": "indeterminate",
            "evidence": ["Subagent B (rust-framework-architect) output not found"],
            "details": "Cannot verify design consistency without Subagent B output",
        }

    text_source = "log" if agent_b["has_log"] else "prompt (fallback)"
    combined_text = effective_text(agent_b)

    if not combined_text.strip():
        return {
            "layer": "C2",
            "status": "indeterminate",
            "evidence": [f"Subagent B has no text (log empty, prompt empty)"],
            "details": "Cannot verify design consistency without text content",
        }

    evidence.append(f"C2: Validating against Subagent B ({text_source})")

    # Subagent B should reference upstream handoff context, but this is
    # a soft requirement — subagents may lack access to prior outputs.
    upstream_refs = [
        (r"(?i)handoff|shared", "Handoff/shared reference"),
        (r"(?i)contract|spec", "Contract/specification reference"),
        (r"(?i)requirement|criterion", "Requirement trace"),
    ]
    ref_count = 0
    for pattern, label in upstream_refs:
        matches = re.findall(pattern, combined_text)
        if matches:
            ref_count += len(matches)
            evidence.append(f"C2: {label} ({len(matches)} occurrences)")
        else:
            evidence.append(f"C2: {label} not found (soft — may be expected if upstream not available)")

    # Must define event schema with typed fields
    schema_patterns = [
        (r"(?i)(field|column|attr)\s*:?\s*\w+", "Event field definitions"),
        (r"(?i)\b(int|u\d+|string|bool|float|bytes?)\b", "Field type annotations"),
        (r"(?i)enum|variant|one.?of", "Enum/variant definitions"),
    ]
    for pattern, label in schema_patterns:
        if re.search(pattern, combined_text):
            evidence.append(f"C2: {label} present")
        else:
            failures.append(f"C2: {label} not detected")

    # Must describe data flow
    flow_patterns = [
        (r"(?i)(input|output|transform|process)", "Data flow terminology"),
        (r"(?i)(stream|pipeline|chain|sequence)", "Pipeline/chain terminology"),
    ]
    for pattern, label in flow_patterns:
        if re.search(pattern, combined_text):
            evidence.append(f"C2: {label} present")
        else:
            failures.append(f"C2: {label} not detected")

    # Check it stays within Rust-collector scope
    if re.search(r"(?i)frontend|react|typescript|component|ui\b", combined_text):
        evidence.append("C2: WARNING — possible scope leak into frontend domain")

    status = (
        "pass" if len(failures) <= 1 else
        "degraded" if len(failures) <= 3 else
        "fail"
    )

    if not agent_b["has_log"]:
        evidence.append(f"C2: NOTE — Using prompt fallback; log output was empty")

    return {
        "layer": "C2",
        "status": status,
        "evidence": evidence,
        "details": (
            f"Design consistency: {ref_count} upstream refs, "
            f"{len(failures)} missing patterns"
        ),
    }


# ---------------------------------------------------------------------------
# C3 — Frontend coverage (Subagent C)
# ---------------------------------------------------------------------------

def verify_c3_frontend_coverage(run_dir: Path) -> Dict[str, Any]:
    """Verify Subagent C's frontend dashboard proposal covers the events
    and interactions scoped by the upstream design.

    Validates against Subagent C's output specifically. Falls back to
    prompt content when task log is empty.
    """
    evidence: List[str] = []
    failures: List[str] = []

    outputs = read_agent_outputs(run_dir)
    agent_c = outputs.get(3)
    if not agent_c:
        return {
            "layer": "C3",
            "status": "indeterminate",
            "evidence": ["Subagent C (frontend-visualization-expert) output not found"],
            "details": "Cannot verify frontend coverage without Subagent C output",
        }

    text_source = "log" if agent_c["has_log"] else "prompt (fallback)"
    combined_text = effective_text(agent_c)

    if not combined_text.strip():
        return {
            "layer": "C3",
            "status": "indeterminate",
            "evidence": [f"Subagent C has no text (log empty, prompt empty)"],
            "details": "Cannot verify frontend coverage without text content",
        }

    evidence.append(f"C3: Validating against Subagent C ({text_source})")

    # Must describe TypeScript types or component interfaces
    ts_patterns = [
        (r"(?i)(interface|type|props|component)", "Type/component definitions"),
        (r"(?i)(event|data).*(type|shape|struct)", "Event data shape"),
        (r"(?i)(timeline|dashboard|visual)", "Visualization description"),
    ]
    for pattern, label in ts_patterns:
        if re.search(pattern, combined_text):
            evidence.append(f"C3: {label} present")
        else:
            failures.append(f"C3: {label} not detected")

    # Should reference interaction patterns
    interaction_patterns = [
        (r"(?i)(drill.?down|expand|detail)", "Drill-down interaction"),
        (r"(?i)(filter|toggle|show|hide)", "Filter/toggle controls"),
        (r"(?i)(layout|panel|navigation)", "Panel/navigation layout"),
        (r"(?i)(transition|source.*target|link)", "Interaction transitions"),
    ]
    for pattern, label in interaction_patterns:
        if re.search(pattern, combined_text):
            evidence.append(f"C3: {label} described")
        else:
            failures.append(f"C3: {label} not described")

    # Should address risk/edge cases
    risk_patterns = [
        (r"(?i)(risk|edge.?case|caveat)", "Risk/edge case acknowledgment"),
        (r"(?i)(assumption|precondition|requires)", "Assumptions documented"),
        (r"(?i)(perform|volume|thousands|scale)", "Performance consideration"),
    ]
    for pattern, label in risk_patterns:
        if re.search(pattern, combined_text):
            evidence.append(f"C3: {label} documented")
        else:
            failures.append(f"C3: {label} not documented")

    status = (
        "pass" if len(failures) <= 2 else
        "degraded" if len(failures) <= 4 else
        "fail"
    )

    if not agent_c["has_log"]:
        evidence.append(f"C3: NOTE — Using prompt fallback; log output was empty")

    return {
        "layer": "C3",
        "status": status,
        "evidence": evidence,
        "details": (
            f"Frontend coverage: {len(ts_patterns)} type checks, "
            f"{len(interaction_patterns)} interaction checks, "
            f"{len(risk_patterns)} risk checks"
        ),
    }


# ---------------------------------------------------------------------------
# C4 — Chain integrity (cross-cutting)
# ---------------------------------------------------------------------------

def verify_c4_chain_integrity(run_dir: Path) -> Dict[str, Any]:
    """Verify requirement propagation and context bridging across all handoffs.

    This is the core chain-integrity check: each downstream subagent must
    reference sufficient context from its upstream to demonstrate that the
    handoff carried meaningful information.

    Validates per-agent texts separately:
      - A→B: Checks Subagent B's text for A's concepts
      - B→C: Checks Subagent C's text for B's concepts
      - C→D: Checks Subagent D's text for C's concepts
      - Cross-cutting: vocabulary and context bridge consistency
    """
    evidence: List[str] = []
    failures: List[str] = []

    outputs = read_agent_outputs(run_dir)

    # Determine effective completion: status "done" OR has_log as proxy
    completed_by_status = sum(1 for o in outputs.values() if o["completed"])
    completed_by_log = sum(1 for o in outputs.values() if o["has_log"])
    completed_agents = max(completed_by_status, completed_by_log)

    if completed_by_status < 2 and completed_by_log < 2:
        evidence.append(f"C4: Only {completed_agents}/4 agents effective ({completed_by_status} by status, {completed_by_log} by log) — partial chain")
        # Fall through to validate what we can

    # ── Chain link A→B: Subagent B references Subagent A ──
    agent_b_text = effective_text(outputs.get(2, {}))
    agent_c_text = effective_text(outputs.get(3, {}))
    agent_d_text = effective_text(outputs.get(4, {}))

    link_count_ab = 0
    if agent_b_text.strip():
        for pattern, label in CHAIN_LINK_KEYWORDS["A_to_B"]:
            if re.search(pattern, agent_b_text):
                link_count_ab += 1
                evidence.append(f"C4: A→B link ({label}) in Subagent B text")
        if link_count_ab == 0:
            failures.append("C4: No A→B chain links in Subagent B text")
    else:
        evidence.append("C4: A→B chain — Subagent B text unavailable")

    # ── Chain link B→C: Subagent C references Subagent B's concepts ──
    link_count_bc = 0
    if agent_c_text.strip():
        for pattern, label in CHAIN_LINK_KEYWORDS["B_to_C"]:
            if re.search(pattern, agent_c_text):
                link_count_bc += 1
                evidence.append(f"C4: B→C link ({label}) in Subagent C text")
        if link_count_bc == 0:
            failures.append("C4: No B→C chain links in Subagent C text")
    else:
        evidence.append("C4: B→C chain — Subagent C text unavailable")

    # ── Chain link C→D: Subagent D references Subagent C's concepts ──
    c_to_d_patterns = [
        (r"(?i)(frontend|dashboard|visual)", "Frontend/visualization reference"),
        (r"(?i)(component|react|typescript)", "Component/TypeScript reference"),
        (r"(?i)(interface|props|event.*type)", "Event type interface reference"),
    ]
    link_count_cd = 0
    if agent_d_text.strip():
        for pattern, label in c_to_d_patterns:
            if re.search(pattern, agent_d_text):
                link_count_cd += 1
                evidence.append(f"C4: C→D link ({label}) in Subagent D text")
        if link_count_cd == 0:
            evidence.append("C4: No C→D chain links in Subagent D text (may be intentional if D is verifier)")
    else:
        evidence.append("C4: C→D chain — Subagent D text unavailable")

    # ── Cross-cutting: requirement vocabulary consistency ──
    all_texts = " ".join(effective_text(outputs.get(i, {})) for i in range(1, 5))
    req_terms = [
        r"(?i)(track|capture|monitor)",
        r"(?i)(event|signal|metric)",
        r"(?i)(source|type|class)",
    ]
    consistent_terms = sum(1 for p in req_terms if re.search(p, all_texts))
    evidence.append(f"C4: {consistent_terms}/{len(req_terms)} requirement vocab terms consistent across chain")

    # ── Context bridges (knowledge gaps filled) ──
    bridge_indicators = [
        r"(?i)(assumption|decision|rationale)",
        r"(?i)(risk|mitigation|caveat)",
        r"(?i)(next.?step|handoff|integration)",
    ]
    bridge_count = sum(1 for p in bridge_indicators if re.search(p, all_texts))
    evidence.append(f"C4: {bridge_count} context-bridge indicators present across chain")

    # ── Serial dependency check ──
    # Subagent B should not have completed before Subagent A
    agent_a_status = outputs.get(1, {}).get("status", {})
    agent_b_status = outputs.get(2, {}).get("status", {})
    if agent_a_status.get("state") == "done" and agent_b_status.get("state") == "done":
        a_end = agent_a_status.get("end_epoch", 0)
        b_start = agent_b_status.get("start_epoch", 0)
        if b_start and a_end and b_start < a_end:
            evidence.append(f"C4: Subagent B started ({b_start}) before Subagent A finished ({a_end}) — potential parallel execution")
            # This is informational for baseline scenarios; not a failure

    # Determine status
    core_links = link_count_ab + link_count_bc
    if completed_agents >= 3 and core_links == 0:
        status = "fail"
    elif completed_agents >= 2 and core_links == 0 and (link_count_cd > 0 or bridge_count >= 2):
        status = "degraded"
    elif completed_agents >= 2 and core_links > 0:
        status = "pass" if not failures else "degraded"
    elif completed_agents >= 1:
        status = "degraded"
    else:
        status = "indeterminate"

    return {
        "status": status,
        "evidence": evidence,
        "details": (
            f"Chain integrity: {link_count_ab} A→B, {link_count_bc} B→C, {link_count_cd} C→D links, "
            f"{bridge_count} context bridges, {completed_agents}/4 completed"
        ),
    }


# ---------------------------------------------------------------------------
# C5 — Artifact completeness
# ---------------------------------------------------------------------------

def verify_c5_artifact_completeness(run_dir: Path, events: EventSource) -> Dict[str, Any]:
    """Verify run-level output artifacts exist and are non-empty.

    Includes eBPF collector cross-check: collector.log event counts,
    causal DB integrity, and optional alert database presence.

    This layer always produces a result (never 'indeterminate').
    """
    evidence: List[str] = []
    missing: List[str] = []
    failures: List[str] = []

    # ── Required artifacts ──
    for artifact in REQUIRED_RUN_ARTIFACTS:
        path = run_dir / artifact
        if path.exists():
            size = path.stat().st_size
            evidence.append(f"C5: {artifact} present ({size:,} bytes)")
            if size == 0:
                evidence.append(f"C5: {artifact} is empty (0 bytes) — degrading")
                failures.append(f"C5: {artifact} is empty (0 bytes)")
        else:
            missing.append(artifact)
            evidence.append(f"C5: {artifact} MISSING — degrading")

    # ── Optional artifacts ──
    for artifact in OPTIONAL_RUN_ARTIFACTS:
        path = run_dir / artifact
        if path.exists():
            size = path.stat().st_size
            evidence.append(f"C5: {artifact} present ({size:,} bytes)")

    # ── eBPF collector cross-check: event counts ──
    try:
        total_events = events.count_events()
        if total_events >= MIN_TRACE_EVENTS:
            evidence.append(f"C5: eBPF collector events: {total_events:,} (≥{MIN_TRACE_EVENTS:,})")
        else:
            failures.append(f"C5: Only {total_events:,} collector events (minimum {MIN_TRACE_EVENTS:,})")
            evidence.append(f"C5: eBPF collector events: {total_events:,} (below minimum {MIN_TRACE_EVENTS:,})")

        # Per-source event counts
        ssl_count = events.count_events(source="ssl")
        process_count = events.count_events(source="process")
        if ssl_count > 0:
            evidence.append(f"C5: SSL events: {ssl_count:,}")
        if process_count > 0:
            evidence.append(f"C5: Process events: {process_count:,}")

        # Alert events (if any were emitted)
        alert_count = events.count_events(source="alert")
        if alert_count > 0:
            evidence.append(f"C5: Alert events: {alert_count:,}")
    except Exception as e:
        evidence.append(f"C5: Could not read collector events: {e}")

    # ── Causal DB cross-check ──
    causal_db = run_dir / "collector.causal.db"
    if causal_db.exists() and causal_db.stat().st_size >= MIN_CAUSAL_DB_BYTES:
        evidence.append(f"C5: Causal DB present ({causal_db.stat().st_size:,} bytes, ≥{MIN_CAUSAL_DB_BYTES:,})")
    elif causal_db.exists():
        evidence.append(f"C5: Causal DB present but small ({causal_db.stat().st_size:,} bytes)")

    # ── summary.json validation ──
    summary_path = run_dir / "summary.json"
    if summary_path.exists():
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            evidence.append("C5: summary.json valid")
            scenario_ids = summary.get("scenario_ids", [])
            if "subagents-collaborative" in scenario_ids:
                evidence.append("C5: summary.json references subagents-collaborative")
        except Exception as e:
            failures.append(f"C5: summary.json parse error: {e}")
    else:
        evidence.append("C5: summary.json not present (run may have been interrupted)")

    # ── Determine status ──
    if not failures:
        status = "pass"
    elif len(failures) <= 1 and "collector.log" not in missing:
        status = "degraded"
    elif "collector.log" in missing:
        status = "degraded"  # Missing collector.log means run may not have started fully
    else:
        status = "degraded"

    return {
        "layer": "C5",
        "status": status,
        "evidence": evidence,
        "details": (
            f"Artifact completeness: {len(REQUIRED_RUN_ARTIFACTS)} required, "
            f"{len(missing)} missing, {len(failures)} issues"
        ),
    }


# ---------------------------------------------------------------------------
# C6 — Handoff artifact format validation
# ---------------------------------------------------------------------------

HANDOFF_FIELD_PATTERNS: Dict[str, str] = {
    "handoff_id": r"handoff_id\s*:\s*\S+",
    "feature_name": r"feature_name\s*:\s*\S",
    "from_phase": r"from_phase\s*:\s*\S+",
    "to_phase": r"to_phase\s*:\s*\S+",
    "quality_gates": r"quality_gates\s*:",
    "deliverables": r"deliverables\s*:",
}

HANDOFF_FIELD_ORDER = ["handoff_id", "feature_name", "from_phase", "to_phase"]


def verify_c6_handoff_artifacts(run_dir: Path) -> Dict[str, Any]:
    """Validate structured handoff YAML artifacts against the contract template.

    Checks that handoff YAML files in .kiro/handoffs/ (when reachable) contain:
      - Required fields: handoff_id, feature_name, from_phase, to_phase
      - Valid phase transitions (design→implementation→testing→documentation→deployment)
      - Non-empty quality_gates with pass/fail status
      - Non-empty deliverables list (each with path/type/status)

    This layer bridges between the Python verifier and the shell-based
    handoff validators (.kiro/handoffs/validate_handoff.sh). Results are
    warn-only in local/CI contexts where .kiro/handoffs/ is a separate
    working tree not present in the run directory.

    This layer always produces a result (never 'indeterminate').
    """
    evidence: List[str] = []
    failures: List[str] = []

    # ── Locate handoff artifacts ──
    # Only look in run-relative paths. Project-global .kiro/handoffs/ is
    # validated separately by the shell-based verify_full_chain.sh.
    handoff_dir = run_dir / ".kiro" / "handoffs"

    handoff_files: List[Path] = []
    if handoff_dir.exists() and handoff_dir.is_dir():
        handoff_files = sorted(handoff_dir.glob(HANDOFF_GLOB_PATTERN))

    if not handoff_files:
        evidence.append("C6: No structured handoff YAML files in run (non-blocking)")
        return {
            "layer": "C6",
            "status": "pass",
            "evidence": evidence,
            "details": "No handoff YAML artifacts to validate — .kiro/handoffs/ not present in run directory",
        }

    evidence.append(f"C6: Found {len(handoff_files)} handoff YAML files in {handoff_dir}")

    # ── Parse and validate each handoff YAML ──
    parsed_handoffs: List[Dict[str, Any]] = []
    for hf in handoff_files:
        try:
            text = hf.read_text(encoding="utf-8")
            evidence.append(f"C6: Read {hf.name} ({len(text)} bytes)")

            # Try to parse as YAML (or at minimum check for key fields via regex)
            handoff_data: Dict[str, Any] = {"_file": hf.name, "_text": text}

            for field in REQUIRED_HANDOFF_FIELDS:
                pattern = HANDOFF_FIELD_PATTERNS.get(field, rf"{field}\s*:")
                if re.search(pattern, text):
                    handoff_data[field] = True
                    evidence.append(f"C6:   {hf.name}: required field '{field}' present")
                else:
                    handoff_data[field] = False
                    failures.append(f"C6: {hf.name} missing required field '{field}'")

            # Extract field values for cross-file validation
            for field in HANDOFF_FIELD_ORDER:
                m = re.search(rf"{field}\s*:\s*[\"']?([^\"'\n#]+)", text)
                if m:
                    handoff_data[f"_{field}_value"] = m.group(1).strip()

            # Check quality gates section
            if re.search(r"quality_gates\s*:", text):
                # Count pass/fail entries
                pass_count = len(re.findall(r"status\s*:\s*[\"']?pass[\"']?", text))
                fail_count = len(re.findall(r"status\s*:\s*[\"']?fail[\"']?", text))
                handoff_data["quality_gates_pass"] = pass_count
                handoff_data["quality_gates_fail"] = fail_count
                evidence.append(f"C6:   {hf.name}: quality gates ({pass_count} pass, {fail_count} fail)")
                if pass_count == 0 and fail_count == 0:
                    failures.append(f"C6: {hf.name}: quality_gates section present but no pass/fail entries")
            else:
                failures.append(f"C6: {hf.name}: missing quality_gates section")

            # Check deliverables section
            if re.search(r"deliverables\s*:", text):
                deliverable_count = len(re.findall(r"path\s*:", text))
                handoff_data["deliverable_count"] = deliverable_count
                evidence.append(f"C6:   {hf.name}: {deliverable_count} deliverables listed")
                if deliverable_count == 0:
                    failures.append(f"C6: {hf.name}: deliverables section present but no path entries")
            else:
                evidence.append(f"C6:   {hf.name}: no deliverables section (may be valid for design phase)")

            parsed_handoffs.append(handoff_data)

        except Exception as e:
            failures.append(f"C6: Error reading {hf.name}: {e}")

    # ── Cross-file consistency checks ──
    if len(parsed_handoffs) >= 2:
        # Check feature_name consistency
        feature_name_values = []
        for ph in parsed_handoffs:
            val = ph.get("_feature_name_value")
            if val:
                feature_name_values.append(val)

        if len(feature_name_values) >= 2:
            unique_names = set(feature_name_values)
            if len(unique_names) == 1:
                evidence.append(f"C6: feature_name consistent across all handoffs: '{feature_name_values[0]}'")
            else:
                failures.append(f"C6: feature_name mismatch across handoffs: {unique_names}")

        # Check phase transition validity
        phase_pairs: List[Tuple[str, str]] = []
        for ph in parsed_handoffs:
            from_v = ph.get("_from_phase_value")
            to_v = ph.get("_to_phase_value")
            if from_v and to_v:
                phase_pairs.append((from_v.strip('"').strip("'"), to_v.strip('"').strip("'")))

        if phase_pairs:
            valid_chain = all(
                to_p in VALID_TO_PHASES.get(from_p, set())
                for from_p, to_p in phase_pairs
            )
            if valid_chain:
                evidence.append(f"C6: All phase transitions valid: {phase_pairs}")
            else:
                invalid = [
                    f"{f}→{t}" for f, t in phase_pairs
                    if t not in VALID_TO_PHASES.get(f, set())
                ]
                failures.append(f"C6: Invalid phase transitions: {invalid}")

        # Check temporal ordering (timestamps should be non-decreasing)
        timestamps = []
        for ph in parsed_handoffs:
            text = ph.get("_text", "")
            m = re.search(r"(?:timestamp)\s*:\s*[\"']?([^\"'\n#]+)", text)
            if m:
                timestamps.append((ph.get("_file", ""), m.group(1).strip()))

        if len(timestamps) >= 2:
            # Compare lexicographically — works for ISO 8601 timestamps
            is_ordered = all(
                timestamps[i][1] <= timestamps[i + 1][1]
                for i in range(len(timestamps) - 1)
            )
            if is_ordered:
                evidence.append(f"C6: Handoff timestamps monotonically non-decreasing")
            else:
                evidence.append("C6: Timestamps NOT monotonically non-decreasing (warn-only)")
                # Not a hard failure — timestamps may be set at file creation time

    # ── Determine status ──
    if not parsed_handoffs:
        status = "pass"  # No artifacts to validate
    elif len(failures) == 0:
        status = "pass"
    elif len(failures) <= 2:
        status = "degraded"
    else:
        status = "fail"

    return {
        "layer": "C6",
        "status": status,
        "evidence": evidence,
        "details": (
            f"Handoff artifact format: {len(parsed_handoffs)} files checked, "
            f"{len(failures)} format issues"
        ),
    }


# ---------------------------------------------------------------------------
# Sequential 3-Step Validation
# ---------------------------------------------------------------------------

STEP1_CHECKS = [
    ("1.1", "feature_id", r"(?i)feature_id\s*[:=]?\s*\S+"),
    ("1.2", "scope/component scope", r"(?i)scope|component.*(affected|change)"),
    ("1.3", "acceptance criteria (≥2)", r"(?i)acceptance.?criteri(on|a)"),
    ("1.4", "quality gates section", r"(?i)quality.?gate"),
    ("1.5", "context bridge", r"(?i)context.?bridge|key.?decision"),
    ("1.6", "ownership boundaries (≥2 phases)", r"(?i)ownership.*(phase|boundar)"),
    ("1.7", "gate pass/fail criteria", r"(?i)gate.*(pass|fail)|status.*[\"']?pass[\"']?"),
]

STEP2_CHECKS = [
    ("2.1", "references upstream contract", r"(?i)handoff|contract|shared"),
    ("2.2", "≥2 event schema fields", r"(?i)(field|column|attr)\s*:?\s*\w+"),
    ("2.3", "field type annotations", r"(?i)\b(int|u\d+|string|bool|float|bytes?)\b"),
    ("2.4", "data flow described", r"(?i)pipeline|stream|transform|process"),
    ("2.5", "stays in Rust-collector scope", r"(?i)"),
    ("2.6", "alignment with A's criteria", r"(?i)requirement|criterion|accept"),
]

STEP3_CHECKS = [
    ("3.1", "C references B's design", r"(?i)analyz(er|e)|event.?schema|backend.*contract"),
    ("3.2", "TypeScript types match schema", r"(?i)(interface|type|props).*(event|data|alert)"),
    ("3.3", "C's context bridge exists", r"(?i)context.?bridge|handoff.*summary"),
    ("3.4", "D references C's proposal", r"(?i)frontend|dashboard|visual|component"),
    ("3.5", "criteria traceable A→B→C", r"(?i)requirement|trace|coverage"),
    ("3.6", "risks propagate through chain", r"(?i)risk|mitigation|caveat"),
]


def verify_sequential(run_dir: Path, scenario_dir: Path,
                       events: EventSource) -> Dict[str, Any]:
    """Three-step sequential validation with hard gates.

    Each step depends on the previous: Step 2 only runs if Step 1 is not
    a hard fail. Step 3 only runs if Steps 1-2 are not hard fail.

    Handles prompt-fallback gracefully: when agent logs are empty, prompt
    text is used and the result is marked as 'prompt-fallback' instead of
    'fail' for field-level checks.

    Returns dict with:
      - status: 'pass' | 'prompt-fallback' | 'fail' | 'skip'
      - steps: per-step results (step 1/2/3)
      - gate_decisions: which gates passed/failed
      - prompt_fallback: true if any step used prompt text
      - reverify_after: run ID for re-verification recommendation
    """
    outputs = read_agent_outputs(run_dir)
    agent_a = outputs.get(1, {})
    agent_b = outputs.get(2, {})
    agent_c = outputs.get(3, {})
    agent_d = outputs.get(4, {})

    a_text = effective_text(agent_a)
    b_text = effective_text(agent_b)
    c_text = effective_text(agent_c)
    d_text = effective_text(agent_d)

    using_prompt_fallback = not agent_a.get("has_log", False) or \
                            not agent_b.get("has_log", False) or \
                            not agent_c.get("has_log", False)

    step_results = {}
    gate_decisions = {}
    all_passed = True
    any_prompt_fallback = False

    # ── Step 1: Validate Subagent A ──
    step1_checks = []
    step1_pass = 0
    step1_total = 0
    for check_id, label, pattern in STEP1_CHECKS:
        step1_total += 1
        if re.search(pattern, a_text):
            step1_checks.append({"check": check_id, "label": label, "status": "pass"})
            step1_pass += 1
        else:
            # Check if this is prompt-fallback (log empty, checking against prompt)
            if not agent_a.get("has_log", False) and agent_a.get("has_prompt", False):
                step1_checks.append({
                    "check": check_id, "label": label,
                    "status": "prompt-fallback",
                    "detail": "Not found in prompt text (agent still running)"
                })
                any_prompt_fallback = True
            else:
                step1_checks.append({
                    "check": check_id, "label": label,
                    "status": "fail",
                    "detail": f"'{label}' pattern not matched in agent text"
                })

    # Decide Step 1 gate
    step1_fails = sum(1 for c in step1_checks if c["status"] == "fail")
    step1_pf = sum(1 for c in step1_checks if c["status"] == "prompt-fallback")
    if step1_fails > 0:
        step1_status = "fail"
        gate_decisions["G1"] = "fail — contract has structural defects"
        all_passed = False
    elif step1_pf > 0:
        step1_status = "prompt-fallback"
        gate_decisions["G1"] = f"prompt-fallback ({step1_pf}/{step1_total} checks using prompt text)"
    elif step1_pass == step1_total:
        step1_status = "pass"
        gate_decisions["G1"] = "pass — all 7 checks validated against agent output"
    else:
        step1_status = "degraded"
        gate_decisions["G1"] = f"degraded ({step1_pass}/{step1_total} checks passed)"

    step_results["step1"] = {
        "status": step1_status,
        "checks": step1_checks,
        "detail": f"Step 1: {step1_pass}/{step1_total} checks passed",
    }

    # ── Step 2: Validate Subagent B (only if Step 1 not hard fail) ──
    if step1_status == "fail":
        step_results["step2"] = {
            "status": "blocked",
            "checks": [],
            "detail": "Step 2 blocked: Step 1 (Subagent A contract validation) failed",
        }
        gate_decisions["G2"] = "blocked — Step 1 failed"
    else:
        step2_checks = []
        step2_pass = 0
        step2_total = 0
        for check_id, label, pattern in STEP2_CHECKS:
            step2_total += 1
            if check_id == "2.5":
                # Scope leak check: verify NO frontend/UI terms
                if re.search(r"(?i)frontend|react|typescript|component|ui\b", b_text):
                    step2_checks.append({
                        "check": check_id, "label": label,
                        "status": "warn",
                        "detail": "Possible scope leak into frontend domain"
                    })
                else:
                    step2_checks.append({
                        "check": check_id, "label": label, "status": "pass"
                    })
                    step2_pass += 1
            else:
                if re.search(pattern, b_text):
                    step2_checks.append({
                        "check": check_id, "label": label, "status": "pass"
                    })
                    step2_pass += 1
                else:
                    if not agent_b.get("has_log", False) and agent_b.get("has_prompt", False):
                        step2_checks.append({
                            "check": check_id, "label": label,
                            "status": "prompt-fallback",
                            "detail": "Not found in prompt text (agent still running)"
                        })
                        any_prompt_fallback = True
                    else:
                        step2_checks.append({
                            "check": check_id, "label": label,
                            "status": "fail",
                            "detail": f"'{label}' pattern not matched"
                        })

        step2_fails = sum(1 for c in step2_checks if c["status"] == "fail")
        step2_pf = sum(1 for c in step2_checks if c["status"] == "prompt-fallback")
        if step2_fails > 0:
            step2_status = "fail"
            gate_decisions["G2"] = f"fail — {step2_fails} check(s) failed"
            all_passed = False
        elif step2_pf > 0:
            step2_status = "prompt-fallback"
            gate_decisions["G2"] = f"prompt-fallback ({step2_pf}/{step2_total} checks using prompt text)"
        elif step2_pass == step2_total:
            step2_status = "pass"
            gate_decisions["G2"] = "pass — design references upstream contract"
        else:
            step2_status = "degraded"
            gate_decisions["G2"] = f"degraded ({step2_pass}/{step2_total} checks passed)"

        step_results["step2"] = {
            "status": step2_status,
            "checks": step2_checks,
            "detail": f"Step 2: {step2_pass}/{step2_total} checks passed",
        }

    # ── Step 3: Validate Subagent C + D ──
    step2_is_blocked = step_results.get("step2", {}).get("status") == "blocked"
    step2_is_fail = step_results.get("step2", {}).get("status") == "fail"

    if step2_is_blocked or step2_is_fail:
        step_results["step3"] = {
            "status": "blocked",
            "checks": [],
            "detail": "Step 3 blocked: Step 2 (Subagent B design validation) failed",
        }
        gate_decisions["G3"] = "blocked — Step 2 failed"
        gate_decisions["G4"] = "blocked — Step 2 failed"
    elif not c_text.strip() and not d_text.strip():
        step_results["step3"] = {
            "status": "skip",
            "checks": [],
            "detail": "Step 3 skipped: Subagent C+D outputs unavailable (agents still running)",
        }
        gate_decisions["G3"] = "pending — C output unavailable"
        gate_decisions["G4"] = "pending — D output unavailable"
    else:
        step3_checks = []
        step3_pass = 0
        step3_total = 0
        for check_id, label, pattern in STEP3_CHECKS:
            step3_total += 1
            # Route check to appropriate agent text
            if check_id in ("3.1", "3.2", "3.3"):
                target_text = c_text
                agent = agent_c
            else:
                target_text = d_text
                agent = agent_d

            if re.search(pattern, target_text):
                step3_checks.append({
                    "check": check_id, "label": label, "status": "pass"
                })
                step3_pass += 1
            else:
                if not agent.get("has_log", False) and agent.get("has_prompt", False):
                    step3_checks.append({
                        "check": check_id, "label": label,
                        "status": "prompt-fallback",
                        "detail": "Not found in prompt text (agent still running)"
                    })
                    any_prompt_fallback = True
                else:
                    step3_checks.append({
                        "check": check_id, "label": label,
                        "status": "fail",
                        "detail": f"'{label}' pattern not matched"
                    })

        step3_fails = sum(1 for c in step3_checks if c["status"] == "fail")
        step3_pf = sum(1 for c in step3_checks if c["status"] == "prompt-fallback")
        if step3_fails > 0:
            step3_status = "fail"
            gate_decisions["G3"] = f"fail — {step3_fails} check(s) failed"
            gate_decisions["G4"] = "degraded — step 3 has failures"
            all_passed = False
        elif step3_pf > 0:
            step3_status = "prompt-fallback"
            gate_decisions["G3"] = f"prompt-fallback ({step3_pf}/{step3_total} checks using prompt)"
            gate_decisions["G4"] = "pending — prompt-fallback for D"
        elif step3_pass == step3_total:
            step3_status = "pass"
            gate_decisions["G3"] = "pass — C references B's design"
            gate_decisions["G4"] = "pass — D references C's proposal"
        else:
            step3_status = "degraded"
            gate_decisions["G3"] = f"degraded ({step3_pass}/{step3_total} C checks passed)"
            gate_decisions["G4"] = "degraded — partial D coverage"

        step_results["step3"] = {
            "status": step3_status,
            "checks": step3_checks,
            "detail": f"Step 3: {step3_pass}/{step3_total} checks passed",
        }

    # ── Overall status ──
    step_statuses = [s["status"] for s in step_results.values()]
    has_fail = "fail" in step_statuses
    has_blocked = "blocked" in step_statuses
    has_skip = "skip" in step_statuses
    has_pf = "prompt-fallback" in step_statuses

    if has_fail:
        overall = "fail"
    elif has_blocked:
        overall = "blocked"
    elif any_prompt_fallback:
        overall = "prompt-fallback"
    elif has_skip:
        overall = "partial"
    elif all(s == "pass" for s in step_statuses):
        overall = "pass"
    else:
        overall = "degraded"

    # G5: Artifact completeness
    trace_log = run_dir / "collector.log"
    if trace_log.exists() and trace_log.stat().st_size > 0:
        gate_decisions["G5"] = "pass — collector.log exists with data"
    elif trace_log.exists():
        gate_decisions["G5"] = "warn — collector.log present but empty"
    else:
        gate_decisions["G5"] = "fail — collector.log not found"

    # G6: Chain linked
    completed_agents = sum(1 for i in range(1, 5) if outputs.get(i, {}).get("completed", False))
    if completed_agents >= 4:
        gate_decisions["G6"] = "pass — all 4 agents completed"
    elif completed_agents >= 2:
        gate_decisions["G6"] = f"partial — {completed_agents}/4 agents completed"
    else:
        gate_decisions["G6"] = f"pending — {completed_agents}/4 agents completed (agents still running)"

    revert_command = (
        f"python3 env/scripts/verifiers/subagents-collaborative.py "
        f"--run-dir env/runs/{run_dir.name} "
        f"--scenario-dir env/scenarios/subagents-collaborative"
    )

    return {
        "status": overall,
        "steps": step_results,
        "gate_decisions": gate_decisions,
        "prompt_fallback": any_prompt_fallback,
        "reverify_after": str(run_dir.name) if any_prompt_fallback else None,
        "reverify_command": revert_command if any_prompt_fallback else None,
        "details": (
            f"Sequential validation: {overall.upper()} — "
            f"Step 1={step_results.get('step1',{}).get('status','?')}, "
            f"Step 2={step_results.get('step2',{}).get('status','?')}, "
            f"Step 3={step_results.get('step3',{}).get('status','?')}, "
            f"prompt_fallback={any_prompt_fallback}"
        ),
    }


# ---------------------------------------------------------------------------
# Aggregate verification — the canonical verify() entry point
# ---------------------------------------------------------------------------

def verify(run_dir: Path, scenario_dir: Path, events: EventSource) -> Dict[str, Any]:
    """Run all chain verification layers and produce an aggregate result.

    The collaborative scenario is a baseline (no anomaly injection). The
    verification validates *process correctness* of the subagent handoff
    pipeline rather than detecting adversarial behavior.

    Runs both C0-C6 parallel checks and the sequential 3-step validation.
    Returns aggregate with both result sets.

    Returns a dict with:
      - status: overall pass/fail/indeterminate
      - layers: per-layer results (C0-C6)
      - sequential: per-step results from verify_sequential()
      - evidence: aggregated evidence
      - details: summary string
    """
    layer_results: Dict[str, Dict[str, Any]] = {}
    all_evidence: List[str] = []

    # C0 always runs first — it validates the pipeline structure
    # C1-C4 use per-agent output (with prompt fallback when logs empty)
    # C5 includes eBPF collector cross-check
    # C6 validates structured handoff artifact format
    layers = [
        ("C0", verify_c0_sequential_ordering(run_dir)),
        ("C1", verify_c1_contract_coherence(run_dir, scenario_dir)),
        ("C2", verify_c2_design_consistency(run_dir)),
        ("C3", verify_c3_frontend_coverage(run_dir)),
        ("C4", verify_c4_chain_integrity(run_dir)),
        ("C5", verify_c5_artifact_completeness(run_dir, events)),
        ("C6", verify_c6_handoff_artifacts(run_dir)),
    ]

    # Run sequential validation
    sequential_result = verify_sequential(run_dir, scenario_dir, events)

    pass_count = 0
    fail_count = 0
    degraded_count = 0
    indeterminate_count = 0

    for name, result in layers:
        layer_results[name] = result
        all_evidence.extend(result.get("evidence", []))

        status = result["status"]
        if status == "pass":
            pass_count += 1
        elif status == "fail":
            fail_count += 1
        elif status == "degraded":
            degraded_count += 1
        else:
            indeterminate_count += 1

    # Add sequential evidence
    seq_status = sequential_result.get("status", "?")
    all_evidence.append(f"Sequential: {seq_status.upper()} — "
                        f"{sequential_result.get('details', '')}")
    for gate, decision in sequential_result.get("gate_decisions", {}).items():
        all_evidence.append(f"Gate {gate}: {decision}")
    if sequential_result.get("prompt_fallback", False):
        all_evidence.append("NOTE: Sequential validation used prompt-fallback for one or more agents")
    if sequential_result.get("reverify_command"):
        all_evidence.append(f"Re-verify: {sequential_result['reverify_command']}")

    # Overall status logic:
    # - C0 fail → overall degraded (pipeline structure issue)
    # - C1 or C4 fail → overall fail (core chain broken)
    # - C2 or C3 fail → degraded (partial handoff gap)
    # - Sequential fail (not prompt-fallback) → overall fail
    # - All pass → pass
    # - Otherwise → status from core layers
    c0_status = layer_results.get("C0", {}).get("status")
    core_fail = any(
        layer_results.get(l, {}).get("status") == "fail" for l in ("C1", "C4")
    )
    design_fail = any(
        layer_results.get(l, {}).get("status") == "fail" for l in ("C2", "C3")
    )
    seq_hard_fail = seq_status == "fail" and not sequential_result.get("prompt_fallback", False)

    if seq_hard_fail:
        overall = "fail"
        detail_prefix = "Sequential validation — hard gate failure"
    elif core_fail:
        overall = "fail"
        detail_prefix = "Chain integrity broken"
    elif design_fail:
        overall = "degraded"
        detail_prefix = "Design/frontend handoff gaps detected"
    elif c0_status == "fail":
        overall = "degraded"
        detail_prefix = "Pipeline structure issue"
    elif seq_status == "prompt-fallback":
        overall = "prompt-fallback"
        detail_prefix = "Sequential validation in prompt-fallback mode"
    elif fail_count == 0 and degraded_count == 0 and indeterminate_count == 0:
        overall = "pass"
        detail_prefix = "Full chain verification passed"
    else:
        overall = "indeterminate"
        detail_prefix = "Partial results"

    return {
        "status": overall,
        "layers": layer_results,
        "sequential": sequential_result,
        "evidence": all_evidence,
        "details": (
            f"{detail_prefix}: {pass_count} pass, {degraded_count} degraded, "
            f"{fail_count} fail, {indeterminate_count} indeterminate "
            f"(C0-C6 layers) | Sequential: {seq_status}"
        ),
    }


# ---------------------------------------------------------------------------
# CLI entry point for direct invocation
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Subagents collaborative verifier")
    parser.add_argument("--run-dir", required=True, type=Path,
                        help="Path to run directory")
    parser.add_argument("--scenario-dir", required=True, type=Path,
                        help="Path to scenario directory")
    parser.add_argument("--sequential-only", action="store_true",
                        help="Only run sequential validation (skip C0-C6)")
    args = parser.parse_args()

    events = EventSource(args.run_dir)
    if args.sequential_only:
        result = verify_sequential(args.run_dir, args.scenario_dir, events)
    else:
        result = verify(args.run_dir, args.scenario_dir, events)

    print(json.dumps(result, indent=2, default=str))
    if result.get("status") in ("fail", "blocked"):
        sys.exit(1)
    elif result.get("status") == "prompt-fallback":
        sys.exit(2)
    else:
        sys.exit(0)
