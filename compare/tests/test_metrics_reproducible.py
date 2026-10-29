# SPDX-License-Identifier: Apache-2.0 OR MIT
"""The recorded fix2 metrics must be reproducible from the archived inputs.

`local/workpackages/2026-10-25-rc-fix2/metrics.json` is the artifact that the
paper claims are verified against (`scripts/evaluation/verify_claims.py`). This
test closes the loop: re-run `final_verdict_metrics.py` over the archived derived
results plus the frozen release and require byte-equal numbers. A clean clone
without the gitignored artifacts skips.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WORKPACKAGE = ROOT / "local" / "workpackages" / "2026-10-25-rc-fix2"
METRICS = WORKPACKAGE / "metrics.json"
RC = WORKPACKAGE / "rc.json"
SCRIPT = ROOT / "scripts" / "evaluation" / "final_verdict_metrics.py"


def _first_differences(left, right, path="", limit=10):
    differences = []
    if isinstance(left, dict) and isinstance(right, dict):
        for key in sorted(set(left) | set(right)):
            if key not in left:
                differences.append(f"{path}/{key}: missing in stored")
            elif key not in right:
                differences.append(f"{path}/{key}: missing in recomputed")
            else:
                differences.extend(_first_differences(left[key], right[key], f"{path}/{key}"))
            if len(differences) >= limit:
                break
    elif isinstance(left, list) and isinstance(right, list):
        if len(left) != len(right):
            differences.append(f"{path}: length {len(left)} != {len(right)}")
        else:
            for index, (a, b) in enumerate(zip(left, right)):
                differences.extend(_first_differences(a, b, f"{path}[{index}]"))
                if len(differences) >= limit:
                    break
    elif isinstance(left, float) or isinstance(right, float):
        if left is None or right is None or abs(float(left) - float(right)) > 1e-12:
            differences.append(f"{path}: {left} != {right}")
    elif left != right:
        differences.append(f"{path}: {left} != {right}")
    return differences[:limit]


def test_recorded_metrics_are_reproducible(tmp_path):
    if not METRICS.is_file() or not RC.is_file():
        pytest.skip("fix2 workpackage is not part of this checkout")
    release = json.loads(RC.read_text(encoding="utf-8"))["dataset"]["path"]
    release_path = Path(release)
    if not release_path.is_dir():
        pytest.skip("frozen release for fix2 is not part of this checkout")
    benign = WORKPACKAGE / "benign" / "derived.results.jsonl"
    attack = WORKPACKAGE / "attack" / "derived.results.jsonl"
    if not benign.is_file() or not attack.is_file():
        pytest.skip("derived results for fix2 are not part of this checkout")
    # Never write into the frozen workpackage: the artifact is immutable evidence.
    out = tmp_path / "metrics.reproduced.json"
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(
        [str(ROOT / "env"), str(ROOT / "env" / "src"), environment.get("PYTHONPATH", "")])
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--benign", str(benign), "--attack", str(attack),
         "--release", str(release_path), "--out", str(out)],
        cwd=ROOT, env=environment, capture_output=True, text=True,
    )
    assert completed.returncode == 0, completed.stderr[-2000:]
    stored = json.loads(METRICS.read_text(encoding="utf-8"))
    recomputed = json.loads(out.read_text(encoding="utf-8"))
    differences = _first_differences(stored, recomputed)
    assert not differences, "recorded metrics are not reproducible:\n" + "\n".join(differences)
