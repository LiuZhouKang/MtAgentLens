"""Ground truth management — load and save."""
from __future__ import annotations

import json
from pathlib import Path
from datetime import date

from mtagentprism_benchmark.types import GroundTruth, AnnotationRecord, Severity, AnnotationConfidence


def load_ground_truth(filepath: Path) -> GroundTruth:
    """Load ground truth from a JSON file."""
    data = json.loads(filepath.read_text(encoding="utf-8"))
    alerts = []
    for a in data.get("alerts", []):
        alerts.append(AnnotationRecord(
            alert_id=a["alert_id"],
            severity=Severity(a.get("severity", "Warning")),
            category=a.get("category", ""),
            causal_chain=a.get("causal_chain", []),
            agent_id=a.get("agent_id", "unknown"),
            confidence=AnnotationConfidence(a.get("confidence", "certain")),
            evidence_refs=a.get("evidence_refs", []),
            annotator=data.get("annotator", "unknown"),
        ))
    return GroundTruth(
        scenario_id=data.get("scenario_id", filepath.stem),
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
