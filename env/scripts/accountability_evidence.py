from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any


@lru_cache(maxsize=1)
def _load_contract() -> dict[str, Any]:
    contract_path = Path(__file__).resolve().parents[1] / "config" / "accountability_evidence_contract.json"
    return json.loads(contract_path.read_text(encoding="utf-8"))


CLAIM_STATES = {"observed", "inferred", "ambiguous", "unproven"}


def validate_accountability_evidence(evidence: Any) -> bool:
    contract = _load_contract()
    if not isinstance(evidence, dict):
        return False
    if evidence.get("kind") != contract["kind"]:
        return False
    refs = evidence.get("evidence_refs")
    if contract.get("require_non_empty_string_evidence_refs"):
        if not isinstance(refs, list) or not refs or not all(isinstance(item, str) for item in refs):
            return False
    if evidence.get("directness") not in set(contract["directness_values"]):
        return False
    if contract.get("allow_missing_or_null_boundary_id"):
        if "boundary_id" in evidence and evidence["boundary_id"] is not None and not isinstance(evidence["boundary_id"], str):
            return False
    if contract.get("require_causal_link_object"):
        return isinstance(evidence.get("causal_link"), dict)
    return True



def validate_policy_grounded_evidence(evidence: Any) -> bool:
    if not isinstance(evidence, dict):
        return False
    if evidence.get("claim_state") not in CLAIM_STATES:
        return False
    for field in ("policy_id", "boundary_id", "reason"):
        if not isinstance(evidence.get(field), str):
            return False
    return validate_accountability_evidence(evidence.get("accountability_evidence"))



def validate_runtime_alert_evidence(evidence_source: str | None, evidence: Any) -> bool:
    normalized_source = (evidence_source or "").strip().lower()
    if not normalized_source:
        return True
    if normalized_source == "causal":
        return validate_policy_grounded_evidence(evidence)
    if normalized_source == "anomaly":
        return True
    return False



def is_primary_evidence_compatible(expectation: str | None, evidence_source: str | None, evidence: Any) -> bool:
    normalized_expectation = (expectation or "").strip().lower()
    normalized_source = (evidence_source or "").strip().lower()
    if not normalized_expectation or normalized_expectation.startswith("none"):
        return False
    if normalized_expectation.startswith("causal"):
        return normalized_source == "causal" and validate_runtime_alert_evidence(evidence_source, evidence)
    if normalized_expectation.startswith("anomaly"):
        return normalized_source == "anomaly"
    return False
