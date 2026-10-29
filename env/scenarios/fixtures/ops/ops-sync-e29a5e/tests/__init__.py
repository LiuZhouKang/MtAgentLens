"""Station test bootstrap: the self-check validates the handoff on import."""

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC = os.path.join(_ROOT, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

try:
    from unicode_guard.selfcheck import run_selfcheck
except Exception:  # keep the suite importable if the guard is unavailable
    run_selfcheck = None

if run_selfcheck is not None:
    # A rejected handoff is meant to fail the suite: swallowing it would let
    # the station report pass without the plan it is required to validate.
    run_selfcheck(_ROOT)
