#!/usr/bin/env python3
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXPECTED_DIR = ROOT / "docs" / "evaluation" / "expected_alerts"
OUT_PATH = ROOT / "frontend" / "src" / "generated" / "expectedAlertsSummary.json"


def main() -> None:
    category_counts = Counter()
    severity_counts = Counter()
    confidence_counts = Counter()
    total_alerts = 0
    scenarios = 0

    for path in sorted(EXPECTED_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        scenarios += 1
        for alert in data.get("alerts", []):
            total_alerts += 1
            category_counts[alert.get("category", "unknown")] += 1
            severity_counts[str(alert.get("severity", "UNKNOWN")).upper()] += 1
            confidence_counts[str(alert.get("confidence", "unknown")).lower()] += 1

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(
        json.dumps(
            {
                "scenarios": scenarios,
                "expected_total": total_alerts,
                "expected_by_category": dict(category_counts),
                "expected_by_severity": dict(severity_counts),
                "expected_by_confidence": dict(confidence_counts),
            },
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
