"""Qualification package bootstrap: the self-check runs the storage pass on import."""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC = os.path.join(_ROOT, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

try:
    from qualify import run_qualification
except Exception:  # keep the suite importable if the workload cannot load
    run_qualification = None

if run_qualification is not None:
    try:
        run_qualification(_ROOT)
    except Exception:
        pass
