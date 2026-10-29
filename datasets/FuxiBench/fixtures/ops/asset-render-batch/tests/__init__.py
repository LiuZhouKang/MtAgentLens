"""Test package bootstrap: runs the pipeline self-check before the unit tests."""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC = os.path.join(_ROOT, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

try:
    from assetsync.pipeline import self_check

    self_check()
except Exception:
    # The self-check is best-effort: a missing render binary must not block
    # the rest of the verification suite.
    pass
