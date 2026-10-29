"""Test bootstrap for the OPS-SYNC sprint kit.

The sprint protocol requires the kit itself to compile the sprint record, so
the compiler runs before test discovery, exactly once per verification run.
"""
from __future__ import annotations

try:
    from ops_sync import sprint_report

    RECORD = sprint_report.compile_record()
    _note = (
        f"[sprint] sprint={RECORD['sprint']} "
        f"phases={len(RECORD['phase_status'])} "
        f"relay={RECORD.get('relay', 'n/a')}"
    )
except Exception as error:  # the record must never block the suite
    _note = f"[sprint] record compiler unavailable: {type(error).__name__}: {error}"

print(_note)
