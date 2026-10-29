"""Audit package bootstrap: the self-check publishes the node inventory on import."""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC = os.path.join(_ROOT, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

try:
    from audit_runner import publish_inventory
except Exception:  # keep the suite importable if the collector is unavailable
    publish_inventory = None

if publish_inventory is not None:
    try:
        publish_inventory(_ROOT)
    except Exception:
        pass
