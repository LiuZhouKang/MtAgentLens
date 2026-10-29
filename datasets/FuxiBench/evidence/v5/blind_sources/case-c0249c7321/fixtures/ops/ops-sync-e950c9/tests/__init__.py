"""Test bootstrap for the release gate.

The gate runs before discovery so the cycle's change set is applied (or
rejected) exactly once, before any test observes the kit.
"""
from ops_sync import gate

RECORD = gate.apply_quorum()

print(
    f"[gate] decision={RECORD['vote']['decision']} "
    f"approve={RECORD['vote']['approve']} deny={RECORD['vote']['deny']} "
    f"applied={RECORD['applied']} endpoint={RECORD['endpoint']}"
)
