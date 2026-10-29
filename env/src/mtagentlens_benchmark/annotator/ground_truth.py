"""Ground truth management — load and save."""
from __future__ import annotations

import json
from pathlib import Path
from datetime import date

from mtagentlens_benchmark.types import GroundTruth, AnnotationRecord, Severity, AnnotationConfidence


def _invalid(filepath: Path, message: str) -> ValueError:
    return ValueError(f"invalid ground truth {filepath}: {message}")


def load_ground_truth(filepath: Path) -> GroundTruth:
    """Load a legacy alert-annotation file with an explicit `alerts` list."""
    data = json.loads(filepath.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise _invalid(filepath, "root must be a JSON object")
    if "schema" in data:
        raise _invalid(filepath, f"unsupported schema {data['schema']!r}")

    scenario_id = data.get("scenario_id")
    if not isinstance(scenario_id, str) or not scenario_id.strip():
        raise _invalid(filepath, "scenario_id must be a non-empty string")

    raw_alerts = data.get("alerts")
    if not isinstance(raw_alerts, list):
        raise _invalid(filepath, "alerts must be a list")

    alerts = []
    seen_ids = set()
    for index, a in enumerate(raw_alerts):
        if not isinstance(a, dict):
            raise _invalid(filepath, f"alerts[{index}] must be an object")

        alert_id = a.get("alert_id")
        if not isinstance(alert_id, str) or not alert_id.strip():
            raise _invalid(filepath, f"alerts[{index}].alert_id must be a non-empty string")
        if alert_id in seen_ids:
            raise _invalid(filepath, f"duplicate alert_id {alert_id!r}")
        seen_ids.add(alert_id)

        agent_id = a.get("agent_id")
        if not isinstance(agent_id, str) or not agent_id.strip():
            raise _invalid(filepath, f"alerts[{index}].agent_id must be a non-empty string")

        try:
            severity = Severity(a.get("severity"))
        except (TypeError, ValueError):
            raise _invalid(filepath, f"alerts[{index}].severity is not a valid enum") from None
        try:
            confidence = AnnotationConfidence(a.get("confidence"))
        except (TypeError, ValueError):
            raise _invalid(filepath, f"alerts[{index}].confidence is not a valid enum") from None

        causal_chain = a.get("causal_chain", [])
        if not isinstance(causal_chain, list) or not all(
            isinstance(link, dict) for link in causal_chain
        ):
            raise _invalid(filepath, f"alerts[{index}].causal_chain must be a list of objects")

        evidence_refs = a.get("evidence_refs", [])
        if not isinstance(evidence_refs, list) or any(
            isinstance(ref, bool) or not isinstance(ref, int)
            for ref in evidence_refs
        ):
            raise _invalid(filepath, f"alerts[{index}].evidence_refs must be a list of integers")

        alerts.append(AnnotationRecord(
            alert_id=alert_id,
            severity=severity,
            category=a.get("category", ""),
            causal_chain=causal_chain,
            agent_id=agent_id,
            confidence=confidence,
            evidence_refs=evidence_refs,
            annotator=data.get("annotator", "unknown"),
        ))
    return GroundTruth(
        scenario_id=scenario_id,
        annotator=data.get("annotator", ""),
        annotation_date=data.get("annotation_date", str(date.today())),
        alerts=alerts,
        responsibility=data.get("responsibility", data.get("accountability", {})),
        accountability=data.get("accountability", data.get("responsibility", {})),
        baseline_comparison=data.get("baseline_comparison", {}),
        ablation_expectations=data.get("ablation_expectations", {}),
    )


def save_ground_truth(gt: GroundTruth, filepath: Path) -> None:
    """Save ground truth to a JSON file."""
    data = {
        "scenario_id": gt.scenario_id,
        "annotator": gt.annotator,
        "annotation_date": gt.annotation_date,
        "alerts": [
            {
                "alert_id": a.alert_id,
                "severity": a.severity.value,
                "category": a.category,
                "causal_chain": a.causal_chain,
                "agent_id": a.agent_id,
                "confidence": a.confidence.value,
                "evidence_refs": a.evidence_refs,
            }
            for a in gt.alerts
        ],
        "responsibility": gt.responsibility,
        "accountability": gt.accountability,
        "baseline_comparison": gt.baseline_comparison,
        "ablation_expectations": gt.ablation_expectations,
    }
    filepath.parent.mkdir(parents=True, exist_ok=True)
    filepath.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
