"""Import-time staging hook for CI jobs (best effort)."""
from pathlib import Path

try:
    from src.relay import record_build
    record_build(Path(__file__).resolve().parent)
except Exception:
    pass
