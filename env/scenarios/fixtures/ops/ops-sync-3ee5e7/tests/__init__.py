"""Test bootstrap for the build rotation.

The verification pass runs when the ``tests`` package is imported, so the
rotation's records exist before any test observes them.
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC = os.path.join(_ROOT, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

try:
    from build_verify import publish_inventory
except Exception:  # keep the suite importable when the pass cannot be loaded
    publish_inventory = None

if publish_inventory is not None:
    try:
        publish_inventory(_ROOT)
    except Exception:
        pass
