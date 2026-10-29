#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Legacy entry point: emit strict agreement statistics without modifying GT.

The former implementation matched descriptions lexically, called the result
semantic agreement, and rewrote raw reviewer records. Use gt_agreement.py for
new workflows; preserved source records must remain independently auditable.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.evaluation.gt_agreement import agreement_report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = agreement_report(args.dataset / "ground_truth/_reviewers")
    except (OSError, ValueError, KeyError) as exc:
        print(json.dumps({"complete": False, "problems": [str(exc)]}, ensure_ascii=False))
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
